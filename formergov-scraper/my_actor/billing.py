"""Charging that cannot leak, cannot over-bill, and cannot silently bill nothing.

Every rule in this module is here because a build in this portfolio shipped
without it. The run always looked healthy; the money was wrong. Keep this file
when re-targeting the template - it is not target-specific - and configure it
once in main.py (see `Ledger.configure`).

WHAT GOES WRONG, AND WHICH RULE ANSWERS IT
------------------------------------------
1. Push, then charge. Past the run's `maxTotalChargeUsd` `Actor.charge` bills
   nothing and raises nothing, so rows already in flight ship free and the run
   ends SUCCEEDED. Measured: letterboxd at a $0.11 cap delivered 200 rows
   against 110 charges - 45% of the run free, because five pages were in flight
   at maxConcurrency 5 and each pushed before charging. (The same cap at $0.10
   looked clean: 100 rows is a page boundary, so a page-aligned cap hides it.)
   -> every delivery goes through `Actor.push_data(rows, charged_event_name=...)`,
      which holds the SDK's charge lock across the write AND the charge, truncates
      the write to the remaining budget, and charges exactly what it wrote.

2. Charge, then push, and the push fails. Billing for a row nobody received is
   worse than the leak above. dynamitejobs reverted a correct reorder over this:
   one listing in ~100 failed the dataset schema, so charge-first billed 100 and
   delivered 99, three runs out of three.
   -> rows are validated against the dataset schema BEFORE they are charged
      (`RowGuard`), and the atomic push above cannot write without charging.

3. A bulk count bills partially. `charged_count < count` when the budget runs
   out mid-batch; opentable shipped rows carrying 20 reviews with 5 billed.
   -> delivery is always sliced to `charged_count`, never gated on a boolean.

4. `charged_count == 0` does not mean "unpaid". It is also 0 on a local run and
   on a free run of a monetised Actor, where every row must still ship. Reading
   it as a spent budget empties the dataset - it broke a cap test on
   dubai-rest before it shipped.
   -> billability is resolved ONCE at run start from the live pricing config,
      and a failed read falls back to delivering unbilled, never to withholding.

5. An event name the pricing config does not know bills USD 0 in silence.
   Measured against apify 4.0.2 on 2026-10-29, on-platform: charging an unpriced
   name returns `charged_count == count` with `event_charge_limit_reached=False`,
   `push_data` does not truncate, and no charge request is sent - only a
   `logger.warning`. So the SDK's own numbers say "fully billed" while the run
   earns nothing; `per_event_prices` is the only thing that tells you otherwise.
   (Off-platform the same name costs $1 each, which is the opposite failure:
   charged_count 0 and a truncated push.) sofascore billed no enrichments at all
   for two weeks this way, because the build led the pricing.
   -> event names are resolved from `per_event_prices` at run start, with a
      legacy fallback, so no build ever charges a name the platform does not
      price. The audit names any event that was charged while unpriced.

6. A surcharge that gates delivery. The base row is already paid for by the time
   a surcharge fires, so withholding the row then bills without delivering - the
   mirror of (1). 99co measured 49 rows against 52 `listing-detail` charges at a
   $0.101 cap, three surcharges with no row, because Crawlee drained DETAIL
   requests concurrently and several handlers cleared the surcharge before any of
   their pushes found the budget gone.
   -> `surcharges` are charged AFTER the base row has shipped, only for rows that
      shipped, and can never withhold. A refused surcharge leaves one row's
      enrichment unbilled, which is the right direction to fail.
   -> for an enrichment that CAN be withheld because it has not shipped yet, use
      `slices`: charged before the write, and a refused slice blanks its own
      fields and lets the row ship (ssense: 67 rows carried a detail slice
      against 66 pdp-row charges).

7. The same row billed twice. dubai-rest 0.0.17 delivered 5 rows and charged the
   legacy event 10, because the base and the surcharge path each charged it.
   -> one base charge per row per delivery, and the audit reconciles charged
      against delivered per event.

8. Billing for rows that resolved nothing, or for duplicates. Yahoo Sports
   answers an unknown-but-well-formed id with HTTP 200 and a zero-byte body, and
   a full-shaped row was billed off it. Zara billed 79 rows for 39 distinct
   products; esimdb would have billed each multi-country plan up to 3.7x.
   -> `resolved` and `key_of` run strictly BEFORE the charge, and both counts are
      in the audit. Dedupe state is scoped, because sharing one scope across
      sibling walks made each stay drop the previous one's results (booking).

9. Concurrency. Everything above is decided per handler while 5-20 handlers run,
   so a decision read outside a lock is a decision taken on stale state.
   -> `deliver()` holds one lock across dedupe -> validate -> slice charges ->
      write+charge -> counters, and the stop flag is set under that same lock.
      `should_enqueue()` exists because the charge that spends the budget happens
      in the same pass that queues the next page, so follow-up work has to
      re-check (the skill's rule; 99co's three free surcharges are the cost).

10. A capped run that does not explain itself. It ends SUCCEEDED with a short
    dataset, which reads as "that is all there was"; and the count in the message
    has to be rows DELIVERED, not rows emitted - hacker-news told users
    "Stopped after 150 row(s)" for a run that delivered and billed 100.
    -> `finish()` sets the status message from the delivered count.

11. Nothing in the run record says what was billed and why. `chargedEventCounts`
    on the platform settles ~20s after the run finishes and is `{}` on the list
    endpoint, so there is nothing to triage with while the run is fresh.
    -> `finish()` logs a per-event audit and writes it to the key-value store,
     including a cross-check of the ledger's own counts against the SDK's, which
     is how a charge made outside the ledger shows up at all.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Hashable, Mapping, Sequence

from apify import Actor

Row = dict[str, Any]

# The platform charges this one per dataset write by itself; the SDK refuses it as
# a `charged_event_name` and it cannot be capped from the Actor at all (measured:
# a $5.00 cap delivered 13,143 rows where the cap permits 5,000, and the run ended
# ABORTED long after the budget was gone). An Actor that must honour a cap has to
# be migrated to a named event, so the ledger refuses the name outright.
SYNTHETIC_PREFIX = "apify-"
SYNTHETIC_DATASET_ITEM = "apify-default-dataset-item"

AUDIT_RECORD_KEY = "BILLING_AUDIT"

STOP_CAP = "charge-cap"
STOP_MAX_ITEMS = "max-items"
STOP_CHANNEL = "charge-channel"

_STOP_MESSAGES = {
    STOP_CAP: (
        "Stopped after {rows} row(s): this run hit its maximum cost setting, not the end "
        "of the results. Raise the run's max total charge to collect the rest."
    ),
    STOP_MAX_ITEMS: "Finished at the requested limit of {rows} row(s).",
    STOP_CHANNEL: (
        "Stopped after {rows} row(s): the Apify charging API would not answer, so the run "
        "stopped rather than carry on scraping without being able to bill it. Nothing new "
        "was charged after that point - please re-run, and contact support if anything was "
        "billed without being delivered."
    ),
}


class BillingChannelError(RuntimeError):
    """The charge/write channel failed twice, so no more billable work is safe.

    Raised out of `deliver()` after the ledger has already stopped itself. Let it
    reach main.py's handler: a charging channel that just failed cannot be trusted
    with the rest of the run, and the run must not crash on it either (a platform
    outage is not this Actor's defect and must not page the crash webhook as one).
    """


@dataclass(frozen=True)
class Slice:
    """An optional part of a row, billed separately, withheld if it is refused.

    `kind` is a key in the ledger's event map, `fields` the row keys the slice
    owns, and `present` decides which rows actually carry it (default: at least
    one of the fields is not None). Use a slice when the enrichment is in hand
    before the row ships and so CAN be withheld; use a surcharge when it cannot.

    **A slice may be billed per ITEM rather than per row.** Several Actors here
    charge one event per review, per sale, per price-history point - a list that
    rides inside the row it belongs to. Give such a slice a `count` (how many
    units this row owes) and a `trim` (cut the row back to the `billed` units),
    and the ledger charges and trims in units instead of all-or-nothing. Without
    `trim`, a refused slice is blanked entirely, which is right for a scalar part
    and wrong for a list the buyer partly paid for.
    """

    kind: str
    fields: tuple[str, ...]
    present: Callable[[Row], bool] | None = None
    count: Callable[[Row], int] | None = None
    trim: Callable[[Row, int], None] | None = None

    def units(self, row: Row) -> int:
        """How many billable units of this slice the row carries."""
        if self.count is None:
            return 1 if self.carries(row) else 0
        return max(0, int(self.count(row) or 0))

    def carries(self, row: Row) -> bool:
        if self.count is not None:
            return self.units(row) > 0
        if self.present is not None:
            return bool(self.present(row))
        return any(row.get(name) is not None for name in self.fields)

    def cut(self, row: Row, billed: int) -> None:
        """Keep only what was billed. `billed == 0` removes the part entirely."""
        if self.trim is not None:
            self.trim(row, billed)
            return
        self.blank(row)

    def blank(self, row: Row) -> None:
        for name in self.fields:
            if name in row:
                row[name] = None


@dataclass(frozen=True)
class Delivery:
    """What one `deliver()` call actually did. Count rows from here, not from the
    list you passed in - that is the bug in rule 10 above."""

    kind: str
    delivered: list[Row]
    billed: int
    duplicates: int = 0
    unresolved: int = 0
    invalid: int = 0
    trimmed: int = 0
    stripped: dict[str, int] = field(default_factory=dict)
    stopped: str | None = None

    @property
    def count(self) -> int:
        return len(self.delivered)


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


@dataclass
class RowGuard:
    """Drops a row the dataset would reject, before it can be charged.

    The platform validates each item against `dataset_schema.json` and rejects the
    WHOLE item on one bad member, so with charging ahead of the write a type
    mismatch becomes a bill with no delivery. uniqlo billed 5 and delivered 0 that
    way: three row shapes shared one schema whose types were written for one of
    them, and review fields are routinely null, which a bare `"string"` rejects.

    Build it from the shipped schema (`from_dataset_schema`) so the types have one
    source of truth. With no schema file this is a no-op by design - the template
    deliberately ships no `dataset_schema.json`, because one describing the example
    would reject a real target's rows.
    """

    types: dict[str, tuple[str, ...]] = field(default_factory=dict)
    required: tuple[str, ...] = ()

    @classmethod
    def from_dataset_schema(cls, path: str | Path) -> RowGuard:
        schema_path = Path(path)
        if not schema_path.is_file():
            return cls()
        try:
            fields = json.loads(schema_path.read_text()).get("fields") or {}
        except (OSError, ValueError):  # a malformed schema must not stop the run
            return cls()
        properties = fields.get("properties") or {}
        types: dict[str, tuple[str, ...]] = {}
        for name, spec in properties.items():
            declared = spec.get("type") if isinstance(spec, dict) else None
            if isinstance(declared, str):
                types[name] = (declared,)
            elif isinstance(declared, list):
                types[name] = tuple(str(entry) for entry in declared)
        return cls(types=types, required=tuple(fields.get("required") or ()))

    @property
    def active(self) -> bool:
        return bool(self.types or self.required)

    def reject_reason(self, row: Row) -> str | None:
        """None when the row is deliverable, else why it would be rejected."""
        for name in self.required:
            if row.get(name) is None:
                return f"required field '{name}' is missing or null"
        for name, declared in self.types.items():
            if name not in row:
                continue
            value = row[name]
            if not self._matches(value, declared):
                return (
                    f"field '{name}' is {type(value).__name__}, "
                    f"schema declares {'|'.join(declared)}"
                )
        return None

    @staticmethod
    def _matches(value: Any, declared: Sequence[str]) -> bool:
        for name in declared:
            allowed = _JSON_TYPES.get(name)
            if allowed is None:
                return True  # a type this guard does not model is not a finding
            # `isinstance(True, int)` is True, so an integer column would accept a
            # boolean and the platform would not. Exclude it explicitly.
            if name in {"integer", "number"} and isinstance(value, bool):
                continue
            if isinstance(value, allowed):
                return True
        return False


class Ledger:
    """One run's charging state: which events it bills, what it has billed, and
    whether it is still allowed to bill anything.

    Configured once in main.py, then used from the route handlers through the
    module-level `LEDGER` singleton - the same shape as `crash_reporting.REPORTER`,
    and for the same reason: the handlers run concurrently and must share one
    piece of state rather than each keeping their own.
    """

    def __init__(self) -> None:
        self._events: dict[str, tuple[str, ...]] = {}
        self._resolved: dict[str, str] = {}
        self._prices: dict[str, Decimal] = {}
        self._is_ppe = False
        self._pricing_read_failed = False
        self._key_of: Callable[[str, Row], Hashable | None] | None = None
        self._resolved_pred: Callable[[str, Row], bool] | None = None
        self._guard = RowGuard()
        self._max_items: int | None = None
        self._lock = asyncio.Lock()
        self._seen: set[tuple[str, str, Hashable]] = set()
        self._charged: Counter[str] = Counter()
        self._unbilled: Counter[str] = Counter()
        self._attempted: Counter[str] = Counter()
        self._delivered_on: Counter[str] = Counter()
        self._delivered_by_kind: Counter[str] = Counter()
        self._roles: dict[str, str] = {}
        self._counters: Counter[str] = Counter()
        self._stop_reason: str | None = None
        self._started = False
        self._finished = False
        self._report: Callable[[dict[str, Any]], Any] | None = None

    # -- configuration ----------------------------------------------------

    def configure(
        self,
        *,
        events: Mapping[str, str | Sequence[str]],
        key_of: Callable[[str, Row], Hashable | None] | None = None,
        resolved: Callable[[str, Row], bool] | None = None,
        guard: RowGuard | None = None,
        max_items: int | None = None,
        report: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        """Declare the event map and the three gates that run before any charge.

        `events` maps a kind (a row kind, a slice, a surcharge - any label the
        handlers use) to the event name to charge, or to several names in
        preference order. Several names is how a pricing switchover crosses its
        own effective date with one build: the Actor charges the first name the
        platform actually prices, so there is never a window where the name in the
        code is not a name in `pricingInfos` (sofascore, 2026-10-21). An event
        absent from the map bills NOTHING once per-class pricing is in force and
        nothing in the run record reveals it, so `start()` names the gap
        (marketmovers: `index-row` was priced for a kind the code could not emit).
        """
        normalised: dict[str, tuple[str, ...]] = {}
        for kind, names in events.items():
            candidates = (names,) if isinstance(names, str) else tuple(names)
            for name in candidates:
                if name.startswith(SYNTHETIC_PREFIX):
                    raise ValueError(
                        f"'{name}' is a synthetic platform event: it cannot be charged "
                        "from the Actor and cannot be capped from the Actor either. "
                        "Price a named event instead."
                    )
            normalised[kind] = candidates
        self._events = normalised
        self._key_of = key_of
        self._resolved_pred = resolved
        self._guard = guard or RowGuard()
        self._max_items = max_items if max_items and max_items > 0 else None
        # Called once at the end of a run whose audit found something, with the whole
        # summary. The point is that nobody reads a user's run log: a run that billed
        # the wrong amount has to reach us the way a crash does. See main.py for the
        # wiring; it may be sync or async, and anything it raises is swallowed.
        self._report = report

    async def start(self) -> None:
        """Resolve which events this run can actually bill. Call once, before the
        first request: every later decision is a lookup, not an API call."""
        self._started = True
        try:
            info = Actor.get_charging_manager().get_pricing_info()
            self._is_ppe = bool(info.is_pay_per_event)
            self._prices = dict(info.per_event_prices or {})
        except Exception as exc:  # noqa: BLE001 - see below
            # A pricing read that failed must fall back to DELIVERING, never to
            # withholding: a reporting failure must not look like a spent budget.
            self._pricing_read_failed = True
            self._is_ppe = False
            self._prices = {}
            Actor.log.warning(f"Could not read pricing info; delivering unbilled: {exc}")
            return

        if not self._is_ppe:
            Actor.log.info(
                "Not a pay-per-event run (local, free, or a different pricing model): "
                "every row is delivered unbilled."
            )
            return

        priced = set(self._prices)
        for kind, candidates in self._events.items():
            for name in candidates:
                if name in priced:
                    self._resolved[kind] = name
                    break
            else:
                Actor.log.warning(
                    f"No priced event for '{kind}' (tried {', '.join(candidates)}); "
                    "those rows will be delivered unbilled."
                )
                continue
            if self._resolved[kind] != candidates[0]:
                Actor.log.info(
                    f"'{kind}' bills the fallback event '{self._resolved[kind]}'; it switches "
                    f"to '{candidates[0]}' by itself once that is priced. No redeploy."
                )
            also_priced = [n for n in candidates[1:] if n in priced and n != self._resolved[kind]]
            if also_priced:
                # Charging two names for one row is safe only while a pricing entry
                # prices one of them. A later pricing edit can break that silently
                # and bill the row twice, so say so now (opentable, 2026-10-27).
                Actor.log.warning(
                    f"'{kind}' resolves to '{self._resolved[kind]}' but "
                    f"{', '.join(also_priced)} {'is' if len(also_priced) == 1 else 'are'} "
                    "also priced - check the pricing entry does not bill this row twice."
                )

    # -- state ------------------------------------------------------------

    def event_for(self, kind: str) -> str | None:
        """The event this run charges for `kind`, or None when nothing is billable."""
        return self._resolved.get(kind)

    def billable(self, kind: str) -> bool:
        return kind in self._resolved

    @property
    def started(self) -> bool:
        """True once `start()` has read the pricing. A caller that finishes the audit from
        a `finally` needs this: a run that never got as far as configuring billing has
        nothing to report, and an audit claiming `payPerEvent: false` would be a lie about
        a run that never asked."""
        return self._started

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    @property
    def delivered(self) -> int:
        return sum(self._delivered_by_kind.values())

    def should_enqueue(self) -> bool:
        """Re-check before queueing follow-up work - a next page, a detail request.

        The charge that spends the budget fires in the same pass that queues the
        next request, so a handler that only checks before pushing queues work the
        user can no longer be billed for.
        """
        return self._stop_reason is None

    # -- delivery ---------------------------------------------------------

    async def deliver(
        self,
        kind: str,
        rows: Sequence[Row],
        *,
        scope: str = "",
        slices: Sequence[Slice] = (),
        surcharges: Sequence[str] = (),
    ) -> Delivery:
        """Dedupe, validate, charge and write `rows` - in that order, under one lock.

        `scope` separates dedupe state between sibling walks that legitimately
        return the same entity (booking's flexible-date search shared one scope and
        each stay dropped the previous one's results). `slices` are charged before
        the write and blanked if refused; `surcharges` are charged after it and can
        never withhold.
        """
        if not self._started:  # a silent unconfigured ledger would bill nothing
            raise RuntimeError("Ledger.start() was not awaited before the first delivery")

        async with self._lock:
            if self._stop_reason is not None:
                return Delivery(kind=kind, delivered=[], billed=0, stopped=self._stop_reason)

            counts = {"unresolved": 0, "duplicates": 0, "invalid": 0, "trimmed": 0}
            candidates: list[Row] = []
            for row in rows:
                if self._resolved_pred is not None and not self._resolved_pred(kind, row):
                    # A row that resolved nothing is not a cheap row, it is not a
                    # row. HTTP 200 with an empty body builds a full-shaped record
                    # off `or {}` and bills it at the full price (yahoo-sports).
                    counts["unresolved"] += 1
                    continue
                if self._key_of is not None:
                    key = self._key_of(kind, row)
                    if key is not None:
                        marker = (scope, kind, key)
                        if marker in self._seen:
                            counts["duplicates"] += 1
                            continue
                        # Marked seen before it is known to ship. A row trimmed
                        # by the budget or by maxItems below is therefore seen and
                        # undelivered - safe only because both trims stop the run,
                        # so there is no later page for it to be dropped from.
                        self._seen.add(marker)
                candidates.append(row)

            if self._max_items is not None:
                room = self._max_items - self.delivered
                if room <= 0:
                    self._stop(STOP_MAX_ITEMS)
                    return Delivery(kind=kind, delivered=[], billed=0, stopped=self._stop_reason)
                if len(candidates) > room:
                    counts["trimmed"] = len(candidates) - room
                    candidates = candidates[:room]

            deliverable: list[Row] = []
            for row in candidates:
                reason = self._guard.reject_reason(row)
                if reason is None:
                    deliverable.append(row)
                    continue
                counts["invalid"] += 1
                # Loud, because the alternative is a row billed and never
                # delivered: the dataset rejects the whole item on one bad member.
                Actor.log.warning(f"Dropping a {kind} row the dataset would reject: {reason}")

            for name, value in counts.items():
                self._counters[name] += value

            if not deliverable:
                return Delivery(
                    kind=kind,
                    delivered=[],
                    billed=0,
                    stopped=self._stop_reason,
                    **counts,
                )

            deliverable, bound_by_budget, slice_budget = self._plan(kind, deliverable, slices)
            if bound_by_budget:
                counts["trimmed"] += bound_by_budget
                self._counters["trimmed"] += bound_by_budget
                self._counters["budget_trimmed"] += bound_by_budget
            if not deliverable:
                self._stop(STOP_CAP)
                return Delivery(kind=kind, delivered=[], billed=0, stopped=self._stop_reason, **counts)

            stripped = await self._charge_slices(deliverable, slices, slice_budget)
            delivered, billed = await self._write(kind, deliverable)
            if bound_by_budget:
                self._stop(STOP_CAP)

            self._delivered_by_kind[kind] += len(delivered)
            event = self.event_for(kind)
            if event is not None:
                self._delivered_on[event] += len(delivered)
            if self._max_items is not None and self.delivered >= self._max_items:
                self._stop(STOP_MAX_ITEMS)

            for surcharge in surcharges:
                await self._charge_surcharge(surcharge, len(delivered))

            return Delivery(
                kind=kind,
                delivered=delivered,
                billed=billed,
                stopped=self._stop_reason,
                stripped=stripped,
                **counts,
            )

    def _plan(
        self, kind: str, rows: list[Row], slices: Sequence[Slice]
    ) -> tuple[list[Row], int, Decimal | None]:
        """Decide how many rows the base event can pay for, and reserve its money.

        `push_data` truncates to what the BASE event's budget covers, which is
        exactly right when the base event is the only charge. It is not enough once
        a slice is charged ahead of the write: the slice spends budget the write was
        going to need, so the write truncates and the slice has paid for a row
        nobody receives - the over-bill 99co measured, reached by another route.

        The order that cannot do that, and does not throw away a row the buyer can
        still afford: size the batch on the base event alone, reserve exactly what
        writing those rows will cost, and hand the slices only what is left over. A
        row whose base is affordable therefore always ships; a slice that does not
        fit blanks itself. Planning the row as one indivisible cost instead would
        refuse the whole row - measured while porting this onto
        social-blade-lookup, whose rows are a $0.015 lookup plus two $0.006
        sections: a whole row is $0.027, so at a $0.022 cap the indivisible plan
        delivers nothing where the buyer can afford the lookup and one section.

        Returns the rows to deliver, how many were cut, and the budget the slices
        may spend (None when the run is not billable, or when the budget could not
        be read - in which case the write's own truncation is still a backstop).
        """
        base_event = self.event_for(kind)
        if base_event is None or not rows:
            return rows, 0, None
        try:
            manager = Actor.get_charging_manager()
            keep = manager.compute_push_data_limit(
                items_count=len(rows), event_name=base_event, is_default_dataset=True
            )
            remaining = manager.get_max_total_charge_usd() - manager.calculate_total_charged_amount()
            # What writing the kept rows will cost - the base event plus the
            # synthetic per-dataset-item event, which the platform charges by itself
            # whenever it is priced and which push_data's own limit accounts for.
            per_row = self._prices.get(base_event, Decimal(0)) + self._prices.get(
                SYNTHETIC_DATASET_ITEM, Decimal(0)
            )
            slice_budget = remaining - keep * per_row
            if not slice_budget.is_finite():
                slice_budget = None
            else:
                slice_budget = max(Decimal(0), slice_budget)
        except Exception as exc:  # noqa: BLE001 - no planning is still safe for the base
            Actor.log.warning(f"Could not plan the batch against the budget: {exc}")
            return rows, 0, None
        return rows[:keep], len(rows) - keep, slice_budget

    async def _charge_slices(
        self, rows: list[Row], slices: Sequence[Slice], budget: Decimal | None = None
    ) -> dict[str, int]:
        """Charge each slice for the rows that carry it; blank what was refused.

        `budget` is what `_plan` left over after reserving the base event's money.
        Charging past it would take the budget the write needs, so a slice is only
        charged for as many rows as that leftover covers; the rest keep the row and
        lose the part.

        Only runs where the slice's event is actually priced. That gate is
        load-bearing, not defensive: an unpriced name reports a count that proves
        nothing, and trimming on it would strip every row's enrichment while the
        legacy event billed for it (opentable, 2026-10-27).
        """
        stripped: dict[str, int] = {}
        for part in slices:
            carrying = [(row, part.units(row)) for row in rows if part.carries(row)]
            owed = sum(units for _, units in carrying)
            if not owed:
                continue
            event = self.event_for(part.kind)
            if event is None:
                continue
            wanted = owed
            price = self._prices.get(event, Decimal(0))
            if budget is not None and price:
                wanted = min(wanted, int(budget // price))
            billed = await self._charge(event, wanted, role="slice") if wanted else 0
            if budget is not None:
                budget = max(Decimal(0), budget - billed * price)
            if billed >= owed:
                continue
            # Hand out what was billed in row order, and cut each row back to its
            # share. A row whose share is 0 loses the part; a row that got some of
            # its units keeps those - the buyer paid for them. The row itself always
            # ships: its own base charge decides that, and a refused slice must not
            # withhold a row the buyer is paying for.
            left = billed
            for row, units in carrying:
                share = min(left, units)
                left -= share
                if share < units:
                    part.cut(row, share)
            stripped[event] = stripped.get(event, 0) + (owed - billed)
            self._counters[f"stripped:{event}"] += owed - billed
            Actor.log.warning(
                f"Charge for '{event}' was refused for {owed - billed} of {owed} unit(s); "
                "the rows ship without that part rather than being given away."
            )
        return stripped

    async def _charge_surcharge(self, kind: str, rows: int) -> None:
        if rows <= 0:
            return
        event = self.event_for(kind)
        if event is None:
            return
        billed = await self._charge(event, rows, role="surcharge")
        if billed < rows:
            # The rows have already shipped and their base charge is paid, so this
            # is the right direction to fail. Say it in the log; do not take the
            # row back (dubai-rest, 99co).
            Actor.log.warning(
                f"'{event}' billed {billed} of {rows} delivered row(s) - the budget ran "
                "out. Those rows were delivered; the surcharge was not charged."
            )

    async def _charge(self, event: str, count: int, *, role: str) -> int:
        """A bare charge, for things that are not a dataset write.

        `count` is keyword-only on SDK 4, so a positional `Actor.charge(EVENT, 1)`
        dies after the first push - a run that works until it doesn't.
        """
        self._roles.setdefault(event, role)
        self._attempted[event] += count
        try:
            result = await Actor.charge(event_name=event, count=count)
        except Exception as exc:  # noqa: BLE001
            # A charge that raised is "nothing confirmed billed": the platform may
            # or may not have recorded it, and that is the only reading that cannot
            # overcharge. The run stops either way - a charging channel that just
            # failed cannot be trusted with more billable work (expedia, #7).
            self._counters["charge_failures"] += 1
            Actor.log.warning(f"Charging '{event}' failed; treating it as unbilled: {exc}")
            self._stop(STOP_CHANNEL)
            return 0
        self._charged[event] += result.charged_count
        if result.event_charge_limit_reached:
            self._stop(STOP_CAP)
        return result.charged_count

    async def _write(self, kind: str, rows: list[Row]) -> tuple[list[Row], int]:
        """Write and charge in one act, and report only what the platform took.

        `Actor.push_data(rows, charged_event_name=...)` holds the SDK's charge lock
        across both, truncates the write to the remaining budget and charges
        exactly what it wrote, so delivery can never outrun billing. The returned
        `charged_count` IS the number of rows written.
        """
        event = self.event_for(kind)
        if event is None:
            # Nothing billable: deliver everything. The platform reports
            # charged_count 0 here and reading that as a spent cap empties the
            # dataset on every local and free run.
            await self._push(rows, None, kind)
            return rows, 0

        self._roles.setdefault(event, "base")
        self._attempted[event] += len(rows)
        result = await self._push(rows, event, kind)
        self._charged[event] += result.charged_count
        if result.charged_count < len(rows):
            Actor.log.warning(
                f"Charge cap: {result.charged_count} of {len(rows)} {kind} row(s) were "
                "billed and written; the rest were neither."
            )
        if result.event_charge_limit_reached:
            self._stop(STOP_CAP)
        return rows[: result.charged_count], result.charged_count

    async def _push(self, rows: list[Row], event: str | None, kind: str) -> Any:
        """One write, retried once, without ever writing the same rows twice.

        A charge is an API call and so is a write, so either can fail transiently -
        and the atomic form does the write first, then the charge. Retrying blindly
        would therefore double-write and double-charge whenever it was the CHARGE
        that failed. The SDK's own counter tells the two apart: it is incremented
        before the charge request is sent, so a count that moved means the write
        already happened.
        """
        for attempt in (1, 2):
            before = self._sdk_charged(event)
            try:
                if event is None:
                    await Actor.push_data(rows)
                    return None
                return await Actor.push_data(rows, charged_event_name=event)
            except Exception as exc:  # noqa: BLE001
                written = self._sdk_charged(event) - before if event is not None else 0
                if written > 0:
                    # Rows are out and the charge may not have landed. That is the
                    # leak direction, not an over-bill, and it must not be made
                    # worse by writing them again.
                    self._counters["charge_failures"] += 1
                    # Recorded as written-and-unbilled rather than quietly dropped:
                    # this is the one path where rows really can reach a buyer
                    # without a charge behind them, and the audit has to say so (it
                    # is what a refund would be worked out from).
                    self._unbilled[event] += written  # type: ignore[index]
                    self._counters["written_unbilled"] += written
                    Actor.log.warning(
                        f"Charging '{event}' failed after {written} row(s) were written; they "
                        f"are delivered and may be unbilled: {exc}"
                    )
                    self._stop(STOP_CHANNEL)
                    raise BillingChannelError(f"charge failed after writing {kind} rows") from exc
                if attempt == 1:
                    self._counters["write_retries"] += 1
                    Actor.log.warning(f"Writing {len(rows)} {kind} row(s) failed; retrying once: {exc}")
                    continue
                self._counters["write_failures"] += 1
                self._stop(STOP_CHANNEL)
                raise BillingChannelError(f"could not write {kind} rows: {exc}") from exc
        return None

    @staticmethod
    def _sdk_charged(event: str | None) -> int:
        if event is None:
            return 0
        try:
            return Actor.get_charging_manager().get_charged_event_count(event)
        except Exception:  # noqa: BLE001 - only used to tell two failures apart
            return 0

    async def charge_once(self, kind: str) -> bool:
        """A flat per-run fee. False means do no work: the fee could not be charged.

        Used by the per-run-fee Actors (regex-helper, pd-katana, osint-jobs), where
        running the whole job and then failing to bill it is the whole failure mode.
        True when the run is not billable at all, so a local or free run still works.
        """
        event = self.event_for(kind)
        if event is None:
            return True
        return await self._charge(event, 1, role="once") > 0

    def _stop(self, reason: str) -> None:
        """First reason wins: the cap being spent is not news once the channel is
        broken, and the status message should name what actually stopped the run."""
        if self._stop_reason is None:
            self._stop_reason = reason

    # -- audit ------------------------------------------------------------

    def _money(self, events: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        """What this run should have billed, what the platform says it billed, and
        whatever is left over when the two are subtracted.

        `expectedUsd` is the ledger's own arithmetic: every charge it made, at the
        price this run was given. `chargedUsd` is the SDK's running total, which also
        counts the synthetic per-dataset-item event (charged by the platform on every
        write, with no charge call of ours) and anything charged outside the ledger.
        Naming the synthetic part explicitly is what makes the remainder meaningful:
        an `unexplainedUsd` that is not zero is money this Actor cannot account for,
        in either direction, and that is the thing worth a webhook.
        """
        money: dict[str, Any] = {
            "expectedUsd": round(sum(data["estimatedUsd"] for data in events.values()), 6),
        }
        budget = self._budget()
        money.update(budget)
        if "chargedUsd" not in money:
            return money
        synthetic_price = self._prices.get(SYNTHETIC_DATASET_ITEM)
        money["syntheticUsd"] = (
            round(float(synthetic_price) * self.delivered, 6) if synthetic_price else 0.0
        )
        money["unexplainedUsd"] = round(
            money["chargedUsd"] - money["expectedUsd"] - money["syntheticUsd"], 6
        )
        return money

    def _budget(self) -> dict[str, Any]:
        """The run's own money, as the platform sees it.

        `estimatedUsd` per event is the ledger's arithmetic at whatever price this
        run was given; this is the SDK's own running total and the cap it is
        charged against, which is what the two should be reconciled to.

        The price itself is NOT something an Actor computes. On a tier-priced
        event the Apify platform resolves the buyer's plan rung before the run
        starts and hands the Actor a flat `eventPriceUsd` in
        APIFY_ACTOR_PRICING_INFO, so `per_event_prices` already carries the
        discounted price for this buyer - measured 2026-10-29 on
        social-blade-lookup, whose live entry prices `profile-lookup` only as a
        tier table (FREE $0.024 ... DIAMOND $0.0127) and whose platform run read
        exactly $0.015, the BRONZE rung. The rung's NAME is not exposed to the
        Actor; the price that applies to this run is.
        """
        try:
            manager = Actor.get_charging_manager()
            cap = manager.get_max_total_charge_usd()
            return {
                # An uncapped run reports infinity, which is not JSON.
                "maxTotalChargeUsd": float(cap) if cap.is_finite() else None,
                "chargedUsd": float(manager.calculate_total_charged_amount()),
            }
        except Exception:  # noqa: BLE001 - the audit is best-effort
            return {}

    def summary(self) -> dict[str, Any]:
        events: dict[str, Any] = {}
        for event in sorted(set(self._attempted) | set(self._charged) | set(self._unbilled)):
            price = self._prices.get(event)
            charged = self._charged[event]
            events[event] = {
                "role": self._roles.get(event, "base"),
                "attempted": self._attempted[event],
                "charged": charged,
                "writtenUnbilled": self._unbilled[event] or None,
                "deliveredRows": self._delivered_on.get(event) or None,
                "priced": price is not None,
                "priceUsd": float(price) if price is not None else None,
                "estimatedUsd": round(float(price) * charged, 6) if price is not None else 0.0,
            }
        money = self._money(events)
        return {
            "payPerEvent": self._is_ppe,
            "pricingReadFailed": self._pricing_read_failed,
            "eventsByKind": {kind: self._resolved.get(kind) for kind in self._events},
            "events": events,
            "rowsDelivered": self.delivered,
            "rowsByKind": dict(self._delivered_by_kind),
            "counters": dict(self._counters),
            "money": money,
            "stopReason": self._stop_reason,
            "findings": self._findings(events, money),
        }

    def _findings(
        self, events: Mapping[str, Mapping[str, Any]], money: Mapping[str, Any] | None = None
    ) -> list[str]:
        """The reconciliation, stated as findings rather than left for arithmetic.

        Rows > charges is a leak; charges > rows is an over-bill. Both directions
        are defects, and on an uncapped run neither is visible - nothing truncates
        - so the numbers go in the record for the capped run that will.
        """
        findings: list[str] = []
        # Half a cent: below that it is Decimal-to-float rounding, above it is money.
        unexplained = (money or {}).get("unexplainedUsd") or 0.0
        if self._is_ppe and abs(unexplained) > 0.005:
            direction = "more than" if unexplained > 0 else "less than"
            findings.append(
                f"MONEY: the platform charged ${money['chargedUsd']:.4f}, which is "
                f"${abs(unexplained):.4f} {direction} this run can account for "
                f"(${money['expectedUsd']:.4f} of events it charged"
                + (f" plus ${money['syntheticUsd']:.4f} of dataset items"
                   if money.get("syntheticUsd") else "")
                + ")"
            )
        if self._is_ppe and not self._resolved and self._events:
            findings.append(
                "pay-per-event is on but none of the declared events is priced: "
                "every row was delivered unbilled"
            )
        for event, data in events.items():
            if data["writtenUnbilled"]:
                findings.append(
                    f"LEAK: {data['writtenUnbilled']} row(s) were written on '{event}' while "
                    "the charge failed - delivered, possibly unbilled"
                )
            if not data["priced"] and data["charged"]:
                findings.append(
                    f"'{event}' was charged {data['charged']}x while absent from the pricing "
                    "config: those charges billed USD 0"
                )
            rows = data["deliveredRows"]
            if data["role"] == "base" and rows is not None:
                if data["charged"] > rows:
                    findings.append(
                        f"OVER-BILL: '{event}' charged {data['charged']} for {rows} delivered row(s)"
                    )
                elif self.billable_event(event) and data["charged"] < rows:
                    findings.append(
                        f"LEAK: {rows} row(s) delivered on '{event}' against {data['charged']} charged"
                    )
            if data["role"] in {"slice", "surcharge"} and data["charged"] > data["attempted"]:
                # Compared against what was ASKED for, not against the row count: a
                # per-item slice bills one event per review or per sale, so charging more
                # events than there are rows is normal and charging more than the rows
                # owed is not.
                findings.append(
                    f"OVER-BILL: '{event}' charged {data['charged']} where {data['attempted']} "
                    "unit(s) were owed"
                )
            sdk = self._sdk_charged(event)
            if sdk and sdk != data["charged"] + self._unbilled[event]:
                # The ledger is not the only thing that can charge. A divergence
                # means a charge happened outside it - a stray Actor.charge, or a
                # second push_data with an event name.
                findings.append(
                    f"'{event}': the SDK counted {sdk} charge(s), the ledger {data['charged']} - "
                    "something charged outside the ledger"
                )
        return findings

    def billable_event(self, event: str) -> bool:
        return event in self._prices

    async def finish(self, *, set_status_message: bool = True) -> None:
        """Log the audit, leave it in the key-value store, and explain a short run.

        Nothing here may raise: it runs at the end of every run, including the ones
        that are already failing.
        """
        if self._finished:
            # Idempotent, so a caller can finish from a `finally` without having to know
            # whether an early return already did it. The audit for a run that returned
            # before its first delivery is the point of that pattern: the instagram-gated
            # path on social-blade-lookup left no record at all until this was here.
            return
        self._finished = True
        try:
            summary = self.summary()
            lines = [
                f"{'event':<24}{'role':<10}{'charged':>8}{'rows':>7}{'price':>11}{'est. USD':>10}"
            ]
            for event, data in summary["events"].items():
                price = "unpriced" if not data["priced"] else f"{data['priceUsd']:.6f}"
                rows = "-" if data["deliveredRows"] is None else str(data["deliveredRows"])
                lines.append(
                    f"{event:<24}{data['role']:<10}{data['charged']:>8}{rows:>7}"
                    f"{price:>11}{data['estimatedUsd']:>10.4f}"
                )
            counters = ", ".join(f"{k}={v}" for k, v in sorted(summary["counters"].items()) if v)
            Actor.log.info(
                "Billing audit for this run:\n"
                + "\n".join(lines)
                + f"\nrows delivered: {summary['rowsDelivered']}"
                + (
                    f" | charged ${summary['money']['chargedUsd']:.4f}"
                    + (
                        f" of a ${summary['money']['maxTotalChargeUsd']:.4f} cap"
                        if summary["money"].get("maxTotalChargeUsd") is not None
                        else " (no cap on this run)"
                    )
                    if "chargedUsd" in summary["money"]
                    else ""
                )
                + (f" ({counters})" if counters else "")
                + (f"\nstopped by: {summary['stopReason']}" if summary["stopReason"] else "")
            )
            for finding in summary["findings"]:
                Actor.log.warning(f"Billing audit: {finding}")
            await Actor.set_value(AUDIT_RECORD_KEY, summary)
            if summary["findings"]:
                await self._send_report(summary)
            if set_status_message and self._stop_reason in _STOP_MESSAGES:
                await Actor.set_status_message(
                    _STOP_MESSAGES[self._stop_reason].format(rows=self.delivered)
                )
        except Exception as exc:  # noqa: BLE001 - an audit must never fail a run
            Actor.log.warning(f"Could not write the billing audit: {exc}")

    async def _send_report(self, summary: dict[str, Any]) -> None:
        """Hand the audit to whatever this Actor reports anomalies with.

        A billing defect is the one failure nobody sees: the run SUCCEEDS, the user
        has their rows, and the only trace is a number in a record nobody opens. So
        it goes out the same way a crash does.
        """
        if self._report is None:
            return
        try:
            result = self._report(summary)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 - reporting must never fail a run
            Actor.log.warning(f"Could not report the billing findings: {exc}")


# One ledger per run, imported by main.py (to configure it) and by the route
# handlers (to deliver through it).
LEDGER = Ledger()
