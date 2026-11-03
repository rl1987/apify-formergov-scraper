"""Each test is named for the production defect it would have caught.

The numbers in the names are real: they come from capped runs in this portfolio,
and the fake Actor in conftest.py reproduces the SDK's ordering faithfully enough
that a leak shows up here instead of on someone's invoice.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from my_actor.billing import (
    AUDIT_RECORD_KEY,
    STOP_CAP,
    STOP_CHANNEL,
    BillingChannelError,
    Ledger,
    RowGuard,
    Slice,
)
import my_actor.billing as billing
from tests.conftest import FakeActor


def run(coro):
    return asyncio.run(coro)


def rows(count: int, kind: str = "row", **extra) -> list[dict]:
    return [{"id": index, "kind": kind, **extra} for index in range(count)]


def make(
    monkeypatch,
    *,
    prices: dict[str, float] | None = None,
    cap: float | None = None,
    is_ppe: bool = True,
    events: dict | None = None,
    **configure,
) -> tuple[Ledger, FakeActor]:
    actor = FakeActor(prices=prices, cap=cap, is_ppe=is_ppe)
    monkeypatch.setattr(billing, "Actor", actor)
    ledger = Ledger()
    ledger.configure(events=events or {"row": "row-event"}, **configure)
    run(ledger.start())
    return ledger, actor


# -- rule 1: a capped run must never deliver more than it billed ----------


def test_a_capped_run_delivers_exactly_what_it_billed(monkeypatch):
    """letterboxd at a $0.11 cap: 200 rows delivered, 110 charged, 45% free."""
    ledger, actor = make(monkeypatch, prices={"row-event": 0.001}, cap=0.11)
    delivered = 0
    for _ in range(10):  # ten pages of 20, as the real pager produced them
        result = run(ledger.deliver("row", rows(20)))
        delivered += result.count
        if not ledger.should_enqueue():
            break
    assert delivered == 110
    assert len(actor.dataset) == 110
    assert actor.manager.counts["row-event"] == 110


def test_concurrent_handlers_cannot_outrun_the_cap(monkeypatch):
    """Five pages in flight at maxConcurrency 5 is how the leak got its size."""
    ledger, actor = make(monkeypatch, prices={"row-event": 0.001}, cap=0.05)

    async def drive():
        return await asyncio.gather(*(ledger.deliver("row", rows(20)) for _ in range(5)))

    results = run(drive())
    assert sum(result.count for result in results) == 50
    assert len(actor.dataset) == 50 == actor.manager.counts["row-event"]


def test_a_page_aligned_cap_would_have_looked_clean(monkeypatch):
    """The test that proves the test: at a page-aligned cap even a leak reconciles,
    which is why the cap in the first test is 110 and not 100."""
    ledger, actor = make(monkeypatch, prices={"row-event": 0.001}, cap=0.10)
    for _ in range(10):
        run(ledger.deliver("row", rows(20)))
        if not ledger.should_enqueue():
            break
    assert len(actor.dataset) == 100 == actor.manager.counts["row-event"]


def test_delivery_is_sliced_to_a_partial_charge(monkeypatch):
    """opentable shipped rows carrying 20 reviews with 5 billed."""
    ledger, actor = make(monkeypatch, prices={"row-event": 0.001}, cap=0.005)
    result = run(ledger.deliver("row", rows(20)))
    assert result.count == 5
    assert result.billed == 5
    assert len(actor.dataset) == 5


def test_delivery_is_still_sliced_when_the_budget_cannot_be_planned(monkeypatch):
    """The plan normally cuts the batch so the write never truncates. When the
    plan is unavailable the write truncates instead, and delivery must follow the
    charge rather than the list that was passed in."""
    ledger, actor = make(monkeypatch, prices={"row-event": 0.001}, cap=0.004)

    def no_planning():
        raise RuntimeError("charging manager busy")

    # Only the planner reads this; the write's own truncation does not.
    monkeypatch.setattr(actor.manager, "calculate_total_charged_amount", no_planning)
    result = run(ledger.deliver("row", rows(10)))
    assert result.count == 4 == len(actor.dataset) == actor.manager.counts["row-event"]
    assert "Could not plan the batch" in actor.log.text()


def test_nothing_is_delivered_once_the_ledger_has_stopped(monkeypatch):
    ledger, actor = make(monkeypatch, prices={"row-event": 0.001}, cap=0.002)
    run(ledger.deliver("row", rows(5)))
    assert ledger.stop_reason == STOP_CAP
    second = run(ledger.deliver("row", rows(5)))
    assert second.count == 0 and second.stopped == STOP_CAP
    assert len(actor.dataset) == 2


# -- rule 4/5: charged_count 0 does not mean unpaid ------------------------


def test_a_local_or_free_run_delivers_everything_unbilled(monkeypatch):
    """dubai-rest broke a cap test by reading charged_count 0 as a spent budget."""
    ledger, actor = make(monkeypatch, prices={}, is_ppe=False)
    result = run(ledger.deliver("row", rows(7)))
    assert result.count == 7 and result.billed == 0
    assert len(actor.dataset) == 7
    assert ledger.stop_reason is None


def test_a_failed_pricing_read_delivers_rather_than_withholds(monkeypatch):
    actor = FakeActor(prices={"row-event": 0.001}, cap=0.001)
    actor.pricing_read_raises = True
    monkeypatch.setattr(billing, "Actor", actor)
    ledger = Ledger()
    ledger.configure(events={"row": "row-event"})
    run(ledger.start())
    result = run(ledger.deliver("row", rows(5)))
    assert result.count == 5
    assert "delivering unbilled" in actor.log.text()


def test_an_unpriced_event_is_never_charged_and_the_audit_says_so(monkeypatch):
    """sofascore billed no enrichments for two weeks: the build led the pricing.

    Measured on apify 4.0.2: on-platform, charging an unpriced name returns
    charged_count == count, so the SDK's own numbers cannot reveal this.
    """
    ledger, actor = make(monkeypatch, prices={"legacy-row": 0.001}, events={"row": "new-row"})
    assert ledger.event_for("row") is None
    result = run(ledger.deliver("row", rows(3)))
    assert result.count == 3
    assert actor.manager.counts == {}
    assert "will be delivered unbilled" in actor.log.text()
    run(ledger.finish())
    assert actor.values[AUDIT_RECORD_KEY]["findings"] == [
        "pay-per-event is on but none of the declared events is priced: "
        "every row was delivered unbilled"
    ]


def test_a_fallback_name_bills_until_the_new_one_is_priced(monkeypatch):
    """One build spans the switchover: no redeploy on the effective date."""
    ledger, actor = make(
        monkeypatch,
        prices={"legacy-row": 0.001},
        events={"row": ("new-row", "legacy-row")},
    )
    assert ledger.event_for("row") == "legacy-row"
    run(ledger.deliver("row", rows(2)))
    assert actor.manager.counts == {"legacy-row": 2}
    assert "switches to 'new-row' by itself" in actor.log.text()


def test_two_priced_names_for_one_kind_are_flagged(monkeypatch):
    """A later pricing edit that prices both would bill the row twice."""
    _, actor = make(
        monkeypatch,
        prices={"new-row": 0.001, "legacy-row": 0.001},
        events={"row": ("new-row", "legacy-row")},
    )
    assert "does not bill this row twice" in actor.log.text()


def test_a_synthetic_event_name_is_refused(monkeypatch):
    """shopify: the synthetic event cannot be charged or capped from the Actor."""
    actor = FakeActor()
    monkeypatch.setattr(billing, "Actor", actor)
    with pytest.raises(ValueError, match="synthetic"):
        Ledger().configure(events={"row": "apify-default-dataset-item"})


# -- rules 6/7: surcharges and slices -------------------------------------


def test_a_surcharge_never_withholds_a_row(monkeypatch):
    """99co charged 52 listing-detail for 49 rows; the mirror defect is worse."""
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001, "detail": 0.001},
        cap=0.005,
        events={"row": "row-event", "detail": "detail"},
    )
    result = run(ledger.deliver("row", rows(3), surcharges=["detail"]))
    assert result.count == 3
    assert actor.manager.counts["row-event"] == 3
    assert actor.manager.counts["detail"] == 2  # budget ran out; rows still shipped
    assert "were delivered; the surcharge was not charged" in actor.log.text()


def test_a_surcharge_is_charged_once_per_delivered_row(monkeypatch):
    """dubai-rest 0.0.17 delivered 5 rows and charged the legacy event 10."""
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001, "detail": 0.002},
        events={"row": "row-event", "detail": "detail"},
    )
    run(ledger.deliver("row", rows(5), surcharges=["detail"]))
    assert actor.manager.counts == {"row-event": 5, "detail": 5}


def test_a_surcharge_only_pays_for_rows_that_shipped(monkeypatch):
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001, "detail": 0.001},
        cap=0.004,
        events={"row": "row-event", "detail": "detail"},
    )
    result = run(ledger.deliver("row", rows(10), surcharges=["detail"]))
    assert result.count == actor.manager.counts["row-event"] == 4
    # The base charge spent the last of the budget, so the surcharge billed
    # nothing at all - and the four rows still shipped.
    assert actor.manager.counts.get("detail", 0) == 0


def test_a_refused_slice_is_blanked_and_the_row_still_ships(monkeypatch):
    """ssense: 67 rows carried a detail slice against 66 pdp-row charges.

    $0.005 at $0.001 a row leaves $0.002 for slices once the three rows' own
    money is reserved, so two rows keep their bio and the third loses it - and
    all three ship, because their base event is paid.
    """
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001, "bio": 0.001},
        cap=0.005,
        events={"row": "row-event", "bio": "bio"},
        max_items=None,
    )
    batch = [{"id": index, "bio": f"long text {index}"} for index in range(3)]
    result = run(ledger.deliver("row", batch, slices=[Slice(kind="bio", fields=("bio",))]))
    assert result.count == 3
    assert [row["bio"] for row in actor.dataset] == ["long text 0", "long text 1", None]
    assert actor.manager.counts == {"bio": 2, "row-event": 3}
    assert result.stripped == {"bio": 1}


def test_a_slice_never_spends_the_money_the_write_needs(monkeypatch):
    """The over-bill the planner exists to prevent: the slice is charged before
    the write, so an unreserved slice spends what the write needed and the write
    then truncates - leaving a slice billed for a row nobody received.

    $0.02 at $0.001 a row covers all five rows, so $0.005 is reserved and the
    $0.009 slice gets one of the remaining $0.015. Five rows ship; one has its
    slice.
    """
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001, "bio": 0.009},
        cap=0.02,
        events={"row": "row-event", "bio": "bio"},
    )
    batch = [{"id": index, "bio": "text"} for index in range(5)]
    result = run(ledger.deliver("row", batch, slices=[Slice(kind="bio", fields=("bio",))]))
    assert result.count == 5 == len(actor.dataset)
    assert actor.manager.counts == {"bio": 1, "row-event": 5}
    assert [row["bio"] for row in actor.dataset].count(None) == 4
    run(ledger.finish())
    assert actor.values[AUDIT_RECORD_KEY]["findings"] == []


def test_a_row_whose_base_is_affordable_ships_even_when_its_slice_is_not(monkeypatch):
    """Planning the row as one indivisible cost refuses it outright, which is the
    wrong way to fail: measured while porting the ledger onto social-blade-lookup,
    whose row is a $0.015 lookup plus two $0.006 sections. A whole row is $0.027,
    so at a $0.022 cap the indivisible plan delivers NOTHING; sizing and reserving
    the base first delivers the lookup and one section, which is what the buyer
    can afford.
    """
    ledger, actor = make(
        monkeypatch,
        prices={"lookup": 0.015, "history": 0.006, "growth": 0.006},
        cap=0.022,
        events={"profile": "lookup", "history": "history", "growth": "growth"},
    )
    row = {"id": 1, "history": [1, 2, 3], "growth": {"1d": 1}}
    result = run(ledger.deliver(
        "profile", [row],
        slices=[Slice(kind="history", fields=("history",)),
                Slice(kind="growth", fields=("growth",))],
    ))
    assert result.count == 1
    assert actor.manager.counts == {"lookup": 1, "history": 1}
    assert actor.dataset[0]["history"] == [1, 2, 3]
    assert actor.dataset[0]["growth"] is None
    run(ledger.finish())
    assert actor.values[AUDIT_RECORD_KEY]["findings"] == []


def test_without_planning_a_cheap_slice_would_pay_for_rows_that_never_ship(monkeypatch):
    """The arm that distinguishes the planner: base $0.009, slice $0.001, cap
    $0.02. Charging the slice first for all five rows costs $0.005 and leaves
    room for ONE row - four slices billed for rows nobody receives. With the base
    sized and reserved first, two rows fit and both keep their slice."""
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.009, "bio": 0.001},
        cap=0.02,
        events={"row": "row-event", "bio": "bio"},
    )
    batch = [{"id": index, "bio": "text"} for index in range(5)]
    result = run(ledger.deliver("row", batch, slices=[Slice(kind="bio", fields=("bio",))]))
    assert result.count == 2
    assert actor.manager.counts == {"bio": 2, "row-event": 2}
    run(ledger.finish())
    assert actor.values[AUDIT_RECORD_KEY]["findings"] == []


def test_an_unpriced_slice_does_not_strip_anything(monkeypatch):
    """The price gate is load-bearing: without it every row loses its slice while
    the legacy event bills for it (opentable, 2026-10-27)."""
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001},
        events={"row": "row-event", "bio": "bio"},
    )
    batch = [{"id": 1, "bio": "kept"}]
    run(ledger.deliver("row", batch, slices=[Slice(kind="bio", fields=("bio",))]))
    assert actor.dataset[0]["bio"] == "kept"


def test_a_local_run_keeps_every_slice(monkeypatch):
    """The gate's real work: off the platform every charge reports 0, and an
    ungated trim would blank the slice on every row of every local and free run.
    """
    ledger, actor = make(
        monkeypatch,
        prices={},
        is_ppe=False,
        events={"row": "row-event", "bio": "bio"},
    )
    batch = [{"id": index, "bio": f"kept {index}"} for index in range(3)]
    result = run(ledger.deliver("row", batch, slices=[Slice(kind="bio", fields=("bio",))]))
    assert result.count == 3
    assert [row["bio"] for row in actor.dataset] == ["kept 0", "kept 1", "kept 2"]


# -- rule 8: dedupe and "did anything resolve" ----------------------------


def test_duplicates_are_dropped_before_the_charge(monkeypatch):
    """zara billed 79 rows for 39 distinct products."""
    ledger, actor = make(monkeypatch, prices={"row-event": 0.001}, key_of=lambda kind, row: row["id"])
    first = run(ledger.deliver("row", rows(5)))
    second = run(ledger.deliver("row", rows(5)))
    assert (first.count, second.count) == (5, 0)
    assert second.duplicates == 5
    assert actor.manager.counts["row-event"] == 5


def test_dedupe_scopes_do_not_collide(monkeypatch):
    """booking's flexible-date search shared one scope, so each stay dropped the
    previous one's results."""
    ledger, _ = make(monkeypatch, prices={"row-event": 0.001}, key_of=lambda kind, row: row["id"])
    first = run(ledger.deliver("row", rows(3), scope="2026-11-01"))
    second = run(ledger.deliver("row", rows(3), scope="2026-11-02"))
    assert (first.count, second.count) == (3, 3)


def test_a_row_that_resolved_nothing_is_not_billed(monkeypatch):
    """yahoo-sports answers an unknown id with HTTP 200 and an empty body, and a
    full-shaped row was billed off it."""
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001},
        resolved=lambda kind, row: bool(row.get("name")),
    )
    result = run(ledger.deliver("row", [{"id": 1, "name": "real"}, {"id": 2, "name": None}]))
    assert result.count == 1 and result.unresolved == 1
    assert actor.manager.counts["row-event"] == 1


# -- rule 2: a row the dataset would reject ------------------------------


def test_a_row_the_dataset_would_reject_is_dropped_before_the_charge(monkeypatch):
    """uniqlo billed 5 and delivered 0: a bare "string" rejects null, and one bad
    field rejects the whole item."""
    guard = RowGuard(types={"title": ("string", "null"), "count": ("integer",)}, required=("title",))
    ledger, actor = make(monkeypatch, prices={"row-event": 0.001}, guard=guard)
    batch = [
        {"title": "fine", "count": 1},
        {"title": None, "count": 2},        # required, and null
        {"title": "bad type", "count": "7"},
    ]
    result = run(ledger.deliver("row", batch))
    assert result.count == 1 and result.invalid == 2
    assert actor.manager.counts["row-event"] == 1
    assert "the dataset would reject" in actor.log.text()


def test_the_guard_reads_nullable_unions_from_a_dataset_schema(tmp_path):
    schema = {
        "fields": {
            "type": "object",
            "required": ["id"],
            "properties": {
                "id": {"type": "string"},
                "rating": {"type": ["number", "null"]},
                "tags": {"type": "array"},
            },
        }
    }
    path = tmp_path / "dataset_schema.json"
    path.write_text(json.dumps(schema))
    guard = RowGuard.from_dataset_schema(path)
    assert guard.active
    assert guard.reject_reason({"id": "a", "rating": None, "tags": []}) is None
    assert "required field 'id'" in guard.reject_reason({"rating": 1.5})
    assert "schema declares number|null" in guard.reject_reason({"id": "a", "rating": "1.5"})


def test_a_boolean_is_not_an_integer_even_though_python_says_so():
    guard = RowGuard(types={"count": ("integer",)})
    assert guard.reject_reason({"count": 3}) is None
    assert guard.reject_reason({"count": True}) is not None


def test_a_missing_schema_file_is_a_no_op_guard(tmp_path):
    guard = RowGuard.from_dataset_schema(tmp_path / "nope.json")
    assert not guard.active
    assert guard.reject_reason({"anything": object()}) is None


# -- platform failures between the charge and the write -------------------


def test_a_write_failure_is_retried_once(monkeypatch):
    ledger, actor = make(monkeypatch, prices={"row-event": 0.001})
    actor.fail_write_times = 1
    result = run(ledger.deliver("row", rows(3)))
    assert result.count == 3
    assert actor.manager.counts["row-event"] == 3
    assert len(actor.dataset) == 3  # retried, not double-written


def test_a_write_that_fails_twice_stops_the_run(monkeypatch):
    ledger, actor = make(monkeypatch, prices={"row-event": 0.001})
    actor.fail_write_times = 2
    with pytest.raises(BillingChannelError):
        run(ledger.deliver("row", rows(3)))
    assert ledger.stop_reason == STOP_CHANNEL
    assert actor.manager.counts == {}
    assert actor.dataset == []


def test_a_charge_that_fails_after_the_write_does_not_write_again(monkeypatch):
    """expedia measured 3 hotels billed for 2 rows delivered before this was
    handled. Retrying the atomic push here would double the rows AND the charge."""
    ledger, actor = make(monkeypatch, prices={"row-event": 0.001})
    actor.fail_charge_after_write_times = 1
    with pytest.raises(BillingChannelError):
        run(ledger.deliver("row", rows(3)))
    assert len(actor.dataset) == 3
    assert ledger.stop_reason == STOP_CHANNEL
    assert "may be unbilled" in actor.log.text()
    # And the audit says which rows those were, because that is what a refund
    # would have to be worked out from.
    asyncio.run(ledger.finish())
    audit = actor.values[AUDIT_RECORD_KEY]
    assert audit["events"]["row-event"]["writtenUnbilled"] == 3
    assert audit["findings"] == [
        "LEAK: 3 row(s) were written on 'row-event' while the charge failed - "
        "delivered, possibly unbilled"
    ]
    assert audit["counters"]["written_unbilled"] == 3


def test_a_bare_charge_failure_is_treated_as_unbilled_and_stops_the_run(monkeypatch):
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001, "detail": 0.001},
        events={"row": "row-event", "detail": "detail"},
    )
    actor.fail_charge_times = 1
    result = run(ledger.deliver("row", rows(2), surcharges=["detail"]))
    assert result.count == 2  # the rows shipped on their own base charge
    assert ledger.stop_reason == STOP_CHANNEL
    assert ledger.should_enqueue() is False


# -- rules 9/10: stopping, and saying so ---------------------------------


def test_should_enqueue_goes_false_with_the_cap(monkeypatch):
    ledger, _ = make(monkeypatch, prices={"row-event": 0.001}, cap=0.002)
    assert ledger.should_enqueue() is True
    run(ledger.deliver("row", rows(2)))
    assert ledger.should_enqueue() is False


def test_the_cap_message_counts_delivered_rows_not_emitted_ones(monkeypatch):
    """hacker-news reported "Stopped after 150 row(s)" for a run that delivered 100.

    The run below emits 150 rows: 100 delivered, 25 duplicates, 25 that resolved
    nothing. Only the 100 may be named.
    """
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001},
        cap=0.1,
        key_of=lambda kind, row: row["id"],
        resolved=lambda kind, row: row.get("real", True),
    )
    run(ledger.deliver("row", [{"id": index} for index in range(50)]))
    run(ledger.deliver("row", [{"id": index} for index in range(25)]))  # duplicates
    run(ledger.deliver("row", [{"id": 900 + i, "real": False} for i in range(25)]))
    run(ledger.deliver("row", [{"id": 100 + index} for index in range(50)]))
    assert ledger.delivered == 100
    run(ledger.finish())
    assert actor.status_messages == [
        "Stopped after 100 row(s): this run hit its maximum cost setting, not the end of "
        "the results. Raise the run's max total charge to collect the rest."
    ]


def test_max_items_counts_delivered_rows_after_dedupe(monkeypatch):
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001},
        key_of=lambda kind, row: row["id"],
        max_items=7,
    )
    run(ledger.deliver("row", rows(5)))
    run(ledger.deliver("row", rows(5)))  # all duplicates: they must not count
    result = run(ledger.deliver("row", [{"id": index} for index in range(100, 110)]))
    assert ledger.delivered == 7
    assert result.trimmed == 8
    run(ledger.finish())
    assert "Finished at the requested limit of 7 row(s)." in actor.status_messages


def test_an_unconfigured_ledger_refuses_to_deliver(monkeypatch):
    actor = FakeActor()
    monkeypatch.setattr(billing, "Actor", actor)
    ledger = Ledger()
    ledger.configure(events={"row": "row-event"})
    with pytest.raises(RuntimeError, match="start"):
        run(ledger.deliver("row", rows(1)))


def test_the_first_stop_reason_is_the_one_reported(monkeypatch):
    """The cap is what stopped this run; a later failure on a channel nobody was
    going to use again must not rename it in the status message."""
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001, "detail": 0.001},
        cap=0.002,
        events={"row": "row-event", "detail": "detail"},
    )
    run(ledger.deliver("row", rows(2)))
    assert ledger.stop_reason == STOP_CAP
    actor.fail_charge_times = 1
    run(ledger._charge_surcharge("detail", 1))
    assert ledger.stop_reason == STOP_CAP
    run(ledger.finish())
    assert actor.status_messages[0].startswith("Stopped after 2 row(s): this run hit its maximum cost")


# -- rule 11: the audit --------------------------------------------------


def test_the_audit_records_what_was_billed_and_what_it_cost(monkeypatch):
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.002, "detail": 0.001},
        events={"row": "row-event", "detail": "detail"},
    )
    run(ledger.deliver("row", rows(4), surcharges=["detail"]))
    run(ledger.finish())
    audit = actor.values[AUDIT_RECORD_KEY]
    assert audit["rowsDelivered"] == 4
    assert audit["events"]["row-event"] == {
        "role": "base",
        "attempted": 4,
        "charged": 4,
        "writtenUnbilled": None,
        "deliveredRows": 4,
        "priced": True,
        "priceUsd": 0.002,
        "estimatedUsd": 0.008,
    }
    assert audit["events"]["detail"]["role"] == "surcharge"
    assert audit["findings"] == []
    assert "Billing audit for this run" in actor.log.text()


def test_the_audit_catches_a_charge_made_outside_the_ledger(monkeypatch):
    """A stray Actor.charge or a second push_data with an event name is otherwise
    invisible - opentable's audit had to establish by hand that there was exactly
    one push site."""
    ledger, actor = make(monkeypatch, prices={"row-event": 0.001})
    run(ledger.deliver("row", rows(2)))
    run(actor.charge(event_name="row-event", count=5))  # somebody else's charge
    run(ledger.finish())
    findings = actor.values[AUDIT_RECORD_KEY]["findings"]
    assert any("something charged outside the ledger" in finding for finding in findings)


def test_the_audit_names_an_over_bill(monkeypatch):
    ledger, actor = make(monkeypatch, prices={"row-event": 0.001, "detail": 0.001},
                         events={"row": "row-event", "detail": "detail"})
    run(ledger.deliver("row", rows(1), surcharges=["detail"]))
    # Pretend a second surcharge fired for a row that never shipped.
    # Charge the surcharge again for rows that never shipped, without telling the
    # ledger they were owed - which is what a double-charging code path looks like.
    ledger._attempted["detail"] -= 3
    run(ledger._charge_surcharge("detail", 3))
    run(ledger.finish())
    findings = actor.values[AUDIT_RECORD_KEY]["findings"]
    assert any(finding.startswith("OVER-BILL: 'detail'") for finding in findings)


def test_the_audit_never_fails_the_run(monkeypatch):
    ledger, actor = make(monkeypatch, prices={"row-event": 0.001})
    run(ledger.deliver("row", rows(1)))

    async def explode(*_args, **_kwargs):
        raise RuntimeError("key-value store unavailable")

    monkeypatch.setattr(actor, "set_value", explode)
    run(ledger.finish())  # must not raise
    assert "Could not write the billing audit" in actor.log.text()


# -- the flat per-run fee ------------------------------------------------


def test_a_refused_run_fee_means_do_no_work(monkeypatch):
    """regex-helper/pd-katana: running the whole job and then failing to bill it
    is the entire failure mode."""
    ledger, _ = make(monkeypatch, prices={"run-fee": 0.01}, cap=0.0, events={"fee": "run-fee"})
    assert run(ledger.charge_once("fee")) is False


def test_a_run_fee_charges_once(monkeypatch):
    ledger, actor = make(monkeypatch, prices={"run-fee": 0.01}, events={"fee": "run-fee"})
    assert run(ledger.charge_once("fee")) is True
    assert actor.manager.counts == {"run-fee": 1}


def test_a_local_run_is_not_blocked_by_the_run_fee(monkeypatch):
    ledger, _ = make(monkeypatch, prices={}, is_ppe=False, events={"fee": "run-fee"})
    assert run(ledger.charge_once("fee")) is True


def test_the_audit_carries_the_platform_s_own_total_and_cap(monkeypatch):
    """The per-event estimate is the ledger's arithmetic; this is the SDK's own
    total, which is what it should be reconciled against. The price in it is the
    rung the platform already resolved for this buyer - an Actor cannot and need
    not work out which discount tier applies."""
    ledger, actor = make(monkeypatch, prices={"row-event": 0.002}, cap=0.1)
    run(ledger.deliver("row", rows(3)))
    run(ledger.finish())
    audit = actor.values[AUDIT_RECORD_KEY]
    assert audit["money"] == {
        "expectedUsd": 0.006,
        "maxTotalChargeUsd": 0.1,
        "chargedUsd": 0.006,
        "syntheticUsd": 0.0,
        "unexplainedUsd": 0.0,
    }
    assert audit["events"]["row-event"]["estimatedUsd"] == 0.006
    assert "charged $0.0060 of a $0.1000 cap" in actor.log.text()


def test_an_uncapped_run_reports_no_cap_rather_than_infinity(monkeypatch):
    ledger, actor = make(monkeypatch, prices={"row-event": 0.002}, cap=None)
    run(ledger.deliver("row", rows(2)))
    run(ledger.finish())
    assert actor.values[AUDIT_RECORD_KEY]["money"]["maxTotalChargeUsd"] is None
    assert "(no cap on this run)" in actor.log.text()


# -- expected against billed, and the webhook when they disagree ----------


def test_money_that_the_run_cannot_account_for_is_a_finding(monkeypatch):
    """The audit's sharpest check: the platform's own total against what this run
    charged, at the price this run was given. A charge from anywhere else - a stray
    Actor.charge, a second push_data with an event name, a pricing entry that bills
    two names for one row - lands here as money nobody can explain."""
    reports: list[dict] = []
    actor = FakeActor(prices={"row-event": 0.002}, cap=None)
    monkeypatch.setattr(billing, "Actor", actor)
    ledger = Ledger()
    ledger.configure(events={"row": "row-event"}, report=reports.append)
    run(ledger.start())
    run(ledger.deliver("row", rows(5)))
    run(actor.charge(event_name="row-event", count=5))  # somebody else's charge
    run(ledger.finish())

    audit = actor.values[AUDIT_RECORD_KEY]
    assert audit["money"]["expectedUsd"] == 0.01
    assert audit["money"]["chargedUsd"] == 0.02
    assert audit["money"]["unexplainedUsd"] == 0.01
    assert audit["findings"][0] == (
        "MONEY: the platform charged $0.0200, which is $0.0100 more than this run can "
        "account for ($0.0100 of events it charged)"
    )
    # And it left the run, because nobody reads a user's log.
    assert len(reports) == 1 and reports[0] is audit


def test_rounding_is_not_a_mismatch(monkeypatch):
    ledger, actor = make(monkeypatch, prices={"row-event": 0.0001}, cap=None)
    run(ledger.deliver("row", rows(7)))
    run(ledger.finish())
    assert actor.values[AUDIT_RECORD_KEY]["findings"] == []


def test_the_synthetic_dataset_item_is_named_before_the_remainder_is_called_unexplained(
    monkeypatch,
):
    """An Actor priced on a named event AND the synthetic one bills both on every row.
    That is a pricing mistake rather than a code one, but the audit has to subtract
    what the platform charges by itself before calling the rest unexplained."""
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.002, "apify-default-dataset-item": 0.001},
        cap=None,
    )
    run(ledger.deliver("row", rows(3)))
    run(ledger.finish())
    money = actor.values[AUDIT_RECORD_KEY]["money"]
    assert money == {
        "expectedUsd": 0.006,
        "maxTotalChargeUsd": None,
        "chargedUsd": 0.009,
        "syntheticUsd": 0.003,
        "unexplainedUsd": 0.0,
    }
    assert actor.values[AUDIT_RECORD_KEY]["findings"] == []


def test_a_clean_run_sends_no_report(monkeypatch):
    reports: list[dict] = []
    actor = FakeActor(prices={"row-event": 0.002}, cap=None)
    monkeypatch.setattr(billing, "Actor", actor)
    ledger = Ledger()
    ledger.configure(events={"row": "row-event"}, report=reports.append)
    run(ledger.start())
    run(ledger.deliver("row", rows(3)))
    run(ledger.finish())
    assert actor.values[AUDIT_RECORD_KEY]["findings"] == []
    assert reports == []


def test_an_async_reporter_is_awaited(monkeypatch):
    seen: list[dict] = []

    async def report(summary):
        seen.append(summary)

    actor = FakeActor(prices={}, is_ppe=True)
    monkeypatch.setattr(billing, "Actor", actor)
    ledger = Ledger()
    ledger.configure(events={"row": "row-event"}, report=report)
    run(ledger.start())  # pay-per-event with nothing priced: a finding by itself
    run(ledger.deliver("row", rows(1)))
    run(ledger.finish())
    assert len(seen) == 1
    assert seen[0]["findings"]


def test_a_reporter_that_raises_does_not_fail_the_run(monkeypatch):
    def report(_summary):
        raise RuntimeError("webhook unreachable")

    actor = FakeActor(prices={}, is_ppe=True)
    monkeypatch.setattr(billing, "Actor", actor)
    ledger = Ledger()
    ledger.configure(events={"row": "row-event"}, report=report)
    run(ledger.start())
    run(ledger.deliver("row", rows(1)))
    run(ledger.finish())  # must not raise
    assert "Could not report the billing findings" in actor.log.text()


def test_finish_is_idempotent_so_a_finally_block_can_call_it(monkeypatch):
    """An Actor with early returns finishes the audit from a `finally`; it must not matter
    whether the normal path already did. social-blade-lookup's instagram-gated mode
    returned before its first delivery and left no BILLING_AUDIT at all."""
    ledger, actor = make(monkeypatch, prices={"row-event": 0.002}, cap=0.004)
    run(ledger.deliver("row", rows(5)))          # capped, so there IS something to say
    run(ledger.finish())
    run(ledger.finish())
    assert actor.values[AUDIT_RECORD_KEY]["rowsDelivered"] == 2
    # Said once: a second finish must not re-announce a run that already explained itself,
    # and must not re-emit the findings webhook either.
    assert len(actor.status_messages) == 1


def test_started_says_whether_there_is_anything_to_audit(monkeypatch):
    actor = FakeActor(prices={"row-event": 0.002})
    monkeypatch.setattr(billing, "Actor", actor)
    ledger = Ledger()
    assert ledger.started is False
    ledger.configure(events={"row": "row-event"})
    assert ledger.started is False
    run(ledger.start())
    assert ledger.started is True


# -- a slice billed per item, not per row ---------------------------------


def _reviews_slice() -> Slice:
    """One event per review, with the list cut back to what was billed."""

    def trim(row, billed):
        row["reviews"] = (row.get("reviews") or [])[:billed]
        row["reviewsDelivered"] = billed

    return Slice(
        kind="review",
        fields=("reviews",),
        count=lambda row: len(row.get("reviews") or []),
        trim=trim,
    )


def test_a_per_item_slice_charges_one_event_per_item(monkeypatch):
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001, "review": 0.0001},
        events={"row": "row-event", "review": "review"},
    )
    batch = [{"id": 1, "reviews": ["a", "b", "c"]}, {"id": 2, "reviews": ["d"]}]
    result = run(ledger.deliver("row", batch, slices=[_reviews_slice()]))
    assert result.count == 2
    assert actor.manager.counts == {"row-event": 2, "review": 4}  # 3 + 1 reviews
    assert [len(row["reviews"]) for row in actor.dataset] == [3, 1]


def test_a_partly_billed_per_item_slice_is_cut_back_not_blanked(monkeypatch):
    """opentable shipped rows carrying 20 reviews with 5 billed. A bulk count bills
    PARTIALLY, and the buyer paid for the first five - so the row keeps five, not none
    and not twenty."""
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001, "review": 0.001},
        cap=0.005,
        events={"row": "row-event", "review": "review"},
    )
    batch = [{"id": 1, "reviews": list("abcde")}, {"id": 2, "reviews": list("fgh")}]
    result = run(ledger.deliver("row", batch, slices=[_reviews_slice()]))

    assert result.count == 2                       # both rows ship: their base is paid
    assert actor.manager.counts["row-event"] == 2
    billed = actor.manager.counts["review"]
    assert billed == 3                             # $0.005 - 2 rows = 3 reviews' worth
    # Handed out in row order: the first row keeps three, the second loses all of its.
    assert [row["reviews"] for row in actor.dataset] == [list("abc"), []]
    assert [row["reviewsDelivered"] for row in actor.dataset] == [3, 0]
    assert result.stripped == {"review": 5}        # 8 owed, 3 billed
    run(ledger.finish())
    audit = actor.values[AUDIT_RECORD_KEY]
    assert audit["counters"]["stripped:review"] == 5
    assert audit["findings"] == []                 # under-billing is not a leak: unbilled
    assert audit["money"]["unexplainedUsd"] == 0.0 # reviews were never delivered unpaid


def test_a_per_item_slice_with_no_items_is_not_charged(monkeypatch):
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001, "review": 0.001},
        events={"row": "row-event", "review": "review"},
    )
    run(ledger.deliver("row", [{"id": 1, "reviews": []}], slices=[_reviews_slice()]))
    assert actor.manager.counts == {"row-event": 1}


def test_a_scalar_slice_is_still_blanked_whole(monkeypatch):
    """No `trim`, so the part is all-or-nothing - right for a biography, wrong for a list."""
    ledger, actor = make(
        monkeypatch,
        prices={"row-event": 0.001, "bio": 0.004},
        cap=0.003,
        events={"row": "row-event", "bio": "bio"},
    )
    run(ledger.deliver("row", [{"id": 1, "bio": "text"}], slices=[Slice(kind="bio", fields=("bio",))]))
    assert actor.dataset[0]["bio"] is None
