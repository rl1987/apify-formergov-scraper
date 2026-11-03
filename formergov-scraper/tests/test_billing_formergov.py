"""How this Actor's one event maps onto the ledger, and the three defects now fixed.

`my_actor/billing.py` is vendored from the api-scraper template unchanged, with its own
suite in tests/test_billing.py. What is specific to FormerGov:

* the delivery path is a Scrapy pipeline, so the ledger is reached through
  `ProfileLedgerPipeline` rather than from a route handler - and the SDK's own
  `ActorDatasetPushPipeline` has to be switched OFF, or every row is written twice;
* the default proxy configuration returned 0 rows and the run ended SUCCEEDED in
  silence, which is three different endings wearing the same face;
* the prefilled example query returned HTTP 200 with zero results.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from itemadapter.adapter import ItemAdapter
from scrapy.exceptions import DropItem

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import my_actor.billing as billing  # noqa: E402
from my_actor.billing import AUDIT_RECORD_KEY, Ledger, RowGuard  # noqa: E402
from my_actor.charging import (  # noqa: E402
    CHARGE_EVENTS,
    DATASET_SCHEMA,
    KIND_PROFILE,
    PROFILE_EVENT,
    row_key,
    row_resolved,
)
from my_actor.formergov_api import search_params_from_url  # noqa: E402
from my_actor.items import ProfileItem  # noqa: E402
from my_actor.parsers import build_item_from_profile  # noqa: E402
from my_actor.pipelines import ProfileLedgerPipeline  # noqa: E402
from tests.conftest import FakeActor  # noqa: E402

LIVE_PRICES = {PROFILE_EVENT: 0.006}  # the proposal's BRONZE rung


def run(coro):
    return asyncio.run(coro)


def ledger_for(monkeypatch, cap=None, max_items=None, prices=None, is_ppe=True):
    actor = FakeActor(prices=LIVE_PRICES if prices is None else prices, cap=cap, is_ppe=is_ppe)
    monkeypatch.setattr(billing, 'Actor', actor)
    ledger = Ledger()
    ledger.configure(events=CHARGE_EVENTS, key_of=row_key, resolved=row_resolved,
                     guard=RowGuard.from_dataset_schema(DATASET_SCHEMA), max_items=max_items)
    run(ledger.start())
    monkeypatch.setattr(billing, 'LEDGER', ledger, raising=False)
    import my_actor.pipelines as pipelines
    monkeypatch.setattr(pipelines, 'LEDGER', ledger)
    return ledger, actor


def profile_doc(username='hennabhandal', last='Bhandal'):
    """Shaped like a real cdn.formergov.com profile document (measured 2026-11-03)."""
    return {
        'firstName': 'Henna', 'middleName': None, 'lastName': last,
        'headline': 'Attorney at Baker McKenzie',
        'address': {'city': 'Los Angeles', 'state': 'California', 'country': 'United States'},
        'websites': [{'id': 'w1', 'name': 'Employer Website',
                      'url': 'https://www.bakermckenzie.com/en/people/b/bhandal-henna'},
                     {'id': 'w2', 'name': 'LinkedIn',
                      'url': 'https://www.linkedin.com/in/hennab3/'}],
        'clearVerified': True,
        'sectors': ['Government Services'], 'practiceAreas': ['Civil Rights'],
        'roles': [{'title': 'Associate', 'employer': 'Baker McKenzie LLP',
                   'isCurrentRole': True, 'isGovernmentRole': False,
                   'startDate': 1614556800000, 'functions': ['Civil Litigation']}],
        'biography': {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [
            {'type': 'text', 'text': 'A biography.'}]}]},
        'education': [{'id': 'e1', 'degree': 'JD', 'institution': 'UCLA'}],
        'certifications': [], 'languages': ['en'], 'memberships': [],
        'honorsAwards': [], 'publications': [],
        'profilePicture': '/api/main/media/profile/profile-images/x.jpeg',
    }


def row(username='hennabhandal', **overrides):
    record = build_item_from_profile(profile_doc(username), username,
                                     '2026-11-03T00:00:00+00:00')
    record.update(overrides)
    return record


class FakeEngine:
    def __init__(self):
        self.closed = []

    def close_spider(self, spider, reason):
        self.closed.append(reason)


class MainActor(FakeActor):
    """FakeActor plus `fail`, so the terminal-state ladder can be driven offline.

    `Actor.log` is a read-only property on the real SDK, so main.py's `Actor` is swapped
    whole rather than patched attribute by attribute.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.failures: list[str | None] = []

    async def fail(self, *, status_message=None, **kwargs):
        self.failures.append(status_message)


class FakeSpider:
    def __init__(self):
        self.crawler = type('C', (), {'engine': FakeEngine()})()
        self.blocked_search_pages = set()
        self.blocked_usernames = set()
        self.requeue_round = 0

    @property
    def logger(self):
        import logging
        return logging.getLogger('fake')


# -- the event ---------------------------------------------------------------


def test_one_profile_charges_one_profile_row(monkeypatch):
    ledger, actor = ledger_for(monkeypatch)
    assert run(ledger.deliver(KIND_PROFILE, [row()])).count == 1
    assert actor.manager.counts == {PROFILE_EVENT: 1}


def test_search_mode_and_direct_mode_produce_the_same_row():
    # The two modes are the same unit, which is why there is one event and not two.
    assert row('a') == row('a')


def test_the_same_username_is_billed_once(monkeypatch):
    # The same username surfaces across search pages, and the end-of-run requeue can
    # re-fetch one that eventually succeeded.
    ledger, actor = ledger_for(monkeypatch)
    first = run(ledger.deliver(KIND_PROFILE, [row()]))
    second = run(ledger.deliver(KIND_PROFILE, [row()]))
    assert (first.count, second.count, second.duplicates) == (1, 0, 1)
    assert actor.manager.counts == {PROFILE_EVENT: 1}


def test_a_nameless_profile_is_not_billed(monkeypatch):
    ledger, actor = ledger_for(monkeypatch)
    result = run(ledger.deliver(KIND_PROFILE, [row(fullName=None, lastName=None)]))
    assert (result.count, result.unresolved) == (0, 1)
    assert actor.manager.counts == {}


# -- the Scrapy pipeline is the only writer ---------------------------------


def test_the_pipeline_delivers_through_the_ledger(monkeypatch):
    ledger, actor = ledger_for(monkeypatch)
    pipeline = ProfileLedgerPipeline()
    spider = FakeSpider()
    item = ProfileItem(**row())
    assert run(pipeline.process_item(item, spider)) is item
    assert actor.manager.counts == {PROFILE_EVENT: 1}
    assert len(actor.dataset) == 1
    assert ItemAdapter(item).asdict()['username'] == actor.dataset[0]['username']


def test_the_pipeline_drops_a_duplicate_without_billing_it(monkeypatch):
    ledger, actor = ledger_for(monkeypatch)
    pipeline = ProfileLedgerPipeline()
    spider = FakeSpider()
    run(pipeline.process_item(ProfileItem(**row()), spider))
    with pytest.raises(DropItem, match='duplicate'):
        run(pipeline.process_item(ProfileItem(**row()), spider))
    assert actor.manager.counts == {PROFILE_EVENT: 1}
    assert len(actor.dataset) == 1


def test_the_pipeline_closes_the_spider_when_the_budget_is_spent(monkeypatch):
    ledger, actor = ledger_for(monkeypatch, cap=0.006)
    pipeline = ProfileLedgerPipeline()
    spider = FakeSpider()
    run(pipeline.process_item(ProfileItem(**row('a')), spider))
    with pytest.raises(DropItem, match='stopped'):
        run(pipeline.process_item(ProfileItem(**row('b')), spider))
    assert actor.manager.counts == {PROFILE_EVENT: 1}
    assert len(actor.dataset) == 1  # the second row is neither written nor billed
    assert spider.crawler.engine.closed == [billing.STOP_CAP]


def test_only_one_pipeline_writes():
    # Two writers would mean every row in the dataset twice, the second copy unbilled.
    from my_actor.settings import ITEM_PIPELINES

    assert ITEM_PIPELINES == {'my_actor.pipelines.ProfileLedgerPipeline': 300}
    assert 'ProfileChargePipeline' not in (ROOT / 'my_actor' / 'pipelines.py').read_text().split('"""')[-1]


def test_main_switches_off_the_sdk_push_pipeline():
    # `apply_apify_settings` injects ActorDatasetPushPipeline at 1000; the ledger's own
    # push already writes AND charges, so leaving it on double-writes.
    source = (ROOT / 'my_actor' / 'main.py').read_text()
    assert ('settings["ITEM_PIPELINES"]["apify.scrapy.pipelines.ActorDatasetPushPipeline"] '
            '= None') in source


def test_the_spider_stops_enqueueing_when_nothing_more_can_be_billed(monkeypatch):
    from my_actor.spiders.formergov import FormerGovSpider

    ledger, _ = ledger_for(monkeypatch, max_items=1)
    import my_actor.spiders.formergov as spider_module
    monkeypatch.setattr(spider_module, 'LEDGER', ledger)
    spider = FormerGovSpider.__new__(FormerGovSpider)
    spider.max_items = 0  # the spider's own limiter is off; the ledger's is not
    spider.enqueued_profiles = 0
    assert spider._can_enqueue_more() is True
    run(ledger.deliver(KIND_PROFILE, [row()]))
    assert ledger.stop_reason == billing.STOP_MAX_ITEMS
    assert spider._can_enqueue_more() is False


# -- the dataset schema -----------------------------------------------------


def test_the_schema_covers_exactly_the_fields_the_parser_emits():
    schema = json.loads(Path(DATASET_SCHEMA).read_text())
    declared = set(schema['fields']['properties'])
    assert declared == set(row())
    assert len(declared) == 29
    assert 'email' not in declared  # there is no email on this target, anywhere


def test_the_guard_is_live_and_passes_a_real_row():
    guard = RowGuard.from_dataset_schema(DATASET_SCHEMA)
    assert guard.active
    assert guard.reject_reason(row()) is None
    # a profile with no current role, no middle name and no firm page is still valid
    assert guard.reject_reason(row(currentTitle=None, currentEmployer=None,
                                   middleName=None, websiteUrl=None)) is None


def test_a_row_the_dataset_would_reject_is_dropped_before_it_is_charged(monkeypatch):
    ledger, actor = ledger_for(monkeypatch)
    bad = row()
    bad['clearVerified'] = 'yes'  # schema declares boolean
    result = run(ledger.deliver(KIND_PROFILE, [bad]))
    assert (result.count, result.invalid) == (0, 1)
    assert actor.manager.counts == {}
    assert actor.dataset == []


# -- the two input-schema defects -------------------------------------------


def test_the_prefilled_proxy_configuration_asks_for_residential():
    # `{"useApifyProxy": true}` resolves to DATACENTER, and cdn.formergov.com answered
    # 403 on 27 of 27 such requests for 0 rows, on a run that ended SUCCEEDED in silence.
    schema = json.loads((ROOT / '.actor' / 'input_schema.json').read_text())
    proxy = schema['properties']['proxyConfiguration']
    for key in ('default', 'prefill'):
        assert proxy[key]['useApifyProxy'] is True
        assert proxy[key]['apifyProxyGroups'] == ['RESIDENTIAL'], key


def test_the_prefilled_example_query_is_not_the_dead_one():
    schema = json.loads((ROOT / '.actor' / 'input_schema.json').read_text())
    url = schema['properties']['startUrls']['prefill'][0]['url']
    # The old pair - practice area "Park Managment" AND sector "Accommodation" - are both
    # still live ids, but no profile is both, so the worked example returned
    # totalHits 0 on HTTP 200.
    assert '1e01b018-bd8a-4c1b-857d-3f93d613935a' not in url
    assert 'e97b9d61-7fd5-4565-8ca5-8f4d5a9dd56e' not in url
    params = search_params_from_url(url)
    assert params['type'] == 'combined'
    assert params['practiceAreas'] == 'd8ac7065-59f2-4724-889e-b1af7299bd47'  # National Security
    assert params['sectors'] == '7738e4e4-87f6-4f8d-978b-954959c7d0ff'  # Government Services


def test_the_store_description_no_longer_promises_an_email():
    actor_json = json.loads((ROOT / '.actor' / 'actor.json').read_text())
    assert 'email' not in actor_json['description'].lower()


def test_the_sdk_pin_is_4x():
    reqs = (ROOT / 'requirements.txt').read_text()
    assert 'apify[scrapy] >= 4.0.0, < 5.0.0' in reqs
    assert '3.3.0' not in reqs


# -- the terminal-state ladder ----------------------------------------------


def main_actor(monkeypatch, ledger=None, **kwargs):
    actor = MainActor(prices=LIVE_PRICES, cap=None, **kwargs)
    from my_actor import main as main_module

    monkeypatch.setattr(main_module, 'Actor', actor)
    if ledger is not None:
        monkeypatch.setattr(main_module, 'LEDGER', ledger)
    return main_module, actor


def test_a_blocked_run_that_delivered_nothing_fails(monkeypatch):
    ledger, _ = ledger_for(monkeypatch)
    main_module, actor = main_actor(monkeypatch, ledger)
    spider = FakeSpider()
    spider.blocked_search_pages = {1, 2}
    spider.requeue_round = 2
    run(main_module._explain_the_end(spider))
    assert len(actor.failures) == 1
    message = actor.failures[0]
    assert 'blocked this run' in message
    assert 'RESIDENTIAL' in message
    assert 'nothing was charged' in message
    assert actor.status_messages == []  # FAILED, not a SUCCEEDED run with a note


def test_an_empty_search_succeeds_but_says_so(monkeypatch):
    ledger, _ = ledger_for(monkeypatch)
    main_module, actor = main_actor(monkeypatch, ledger)
    run(main_module._explain_the_end(FakeSpider()))
    # An empty search is a real answer, not a failure.
    assert actor.failures == []
    assert 'matched nobody' in actor.status_messages[0]
    assert 'Nothing was blocked' in actor.status_messages[0]


def test_a_partly_blocked_run_warns_that_the_results_are_incomplete(monkeypatch):
    ledger, _ = ledger_for(monkeypatch)
    main_module, actor = main_actor(monkeypatch, ledger)
    run(ledger.deliver(KIND_PROFILE, [row()]))
    spider = FakeSpider()
    spider.blocked_usernames = {'someone'}
    run(main_module._explain_the_end(spider))
    assert actor.failures == []
    assert 'INCOMPLETE' in actor.status_messages[0]
    assert 'Only the profiles actually delivered were charged' in actor.status_messages[0]


def test_a_capped_run_leaves_its_message_to_the_ledger(monkeypatch):
    ledger, _ = ledger_for(monkeypatch, cap=0.006)
    main_module, actor = main_actor(monkeypatch, ledger)
    run(ledger.deliver(KIND_PROFILE, [row('a'), row('b')]))
    assert ledger.stop_reason == billing.STOP_CAP
    run(main_module._explain_the_end(FakeSpider()))
    # One message for a truncated run, and it is the ledger's.
    assert actor.status_messages == []
    assert actor.failures == []


def test_the_datacenter_warning_fires_only_for_a_datacenter_config(monkeypatch):
    main_module, actor = main_actor(monkeypatch)
    main_module._warn_if_datacenter_proxy({'useApifyProxy': True})
    assert 'DATACENTER' in actor.log.text()
    before = len(actor.log.lines)
    main_module._warn_if_datacenter_proxy({'useApifyProxy': True,
                                           'apifyProxyGroups': ['RESIDENTIAL']})
    main_module._warn_if_datacenter_proxy(None)
    assert len(actor.log.lines) == before


# -- the cap, and the audit --------------------------------------------------


def test_the_cap_truncates_the_write_rather_than_leaking(monkeypatch):
    ledger, actor = ledger_for(monkeypatch, cap=0.018)
    rows = [row(f'user{n}') for n in range(5)]
    result = run(ledger.deliver(KIND_PROFILE, rows))
    assert result.count == 3  # $0.006 each
    assert len(actor.dataset) == 3
    assert actor.manager.counts == {PROFILE_EVENT: 3}


def test_the_audit_reconciles_to_zero(monkeypatch):
    ledger, actor = ledger_for(monkeypatch, cap=0.018)
    run(ledger.deliver(KIND_PROFILE, [row(f'user{n}') for n in range(5)]))
    run(ledger.finish())
    audit = actor.values[AUDIT_RECORD_KEY]
    assert audit['findings'] == []
    assert audit['money']['unexplainedUsd'] == 0.0
    assert audit['rowsByKind'] == {KIND_PROFILE: 3}
    assert 'Stopped after 3 row(s)' in actor.status_messages[-1]


def test_a_free_run_delivers_everything_unbilled(monkeypatch):
    ledger, actor = ledger_for(monkeypatch, is_ppe=False)
    assert run(ledger.deliver(KIND_PROFILE, [row()])).count == 1
    assert actor.manager.counts == {}
    assert len(actor.dataset) == 1


def test_the_only_declared_kind_resolves_to_the_priced_event(monkeypatch):
    ledger, _ = ledger_for(monkeypatch)
    assert ledger.event_for(KIND_PROFILE) == PROFILE_EVENT
    assert list(CHARGE_EVENTS) == [KIND_PROFILE]
