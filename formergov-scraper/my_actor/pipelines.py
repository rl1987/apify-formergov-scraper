"""Item pipelines for the FormerGov scraper.

There is one, and it is the only thing in this Actor that writes or charges: it hands
every item to the billing ledger (``my_actor/billing.py``), which de-duplicates,
validates, charges and writes under one lock, in that order.

What it replaced, and why
-------------------------
Three pipelines used to share the job: a dedupe pipeline at 300, a charge pipeline at
900, and the Apify SDK's own ``ActorDatasetPushPipeline``, injected at 1000 by
``apply_apify_settings``. Charging at 900 and pushing at 1000 is the right ORDER - past
the run's ``maxTotalChargeUsd`` ``Actor.charge`` bills nothing and raises nothing, so a
charge after the push would ship rows free - but two separate steps cannot be atomic,
and the charge had to guess from ``charged_count`` whether the push that followed would
land. ``Actor.push_data(rows, charged_event_name=...)`` does both under the SDK's own
charge lock and charges exactly what it wrote, which is what the ledger uses. The SDK's
push pipeline is therefore switched OFF in ``main.py`` (set to ``None`` after
``apply_apify_settings``), leaving one writer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from itemadapter.adapter import ItemAdapter
from scrapy.exceptions import DropItem

from .billing import LEDGER
from .charging import KIND_PROFILE

if TYPE_CHECKING:
    from scrapy import Spider

    from .items import ProfileItem


class ProfileLedgerPipeline:
    """Deliver one profile through the ledger, or drop it.

    Registered at 300 in settings.py. Priority no longer matters the way it used to -
    this pipeline both charges and writes, so there is nothing left for a later stage to
    get wrong - but it stays below 1000 so that re-enabling the SDK's push pipeline by
    accident would be loud (a double write) rather than silent.
    """

    async def process_item(self, item: ProfileItem, spider: Spider) -> ProfileItem:
        row = ItemAdapter(item).asdict()
        result = await LEDGER.deliver(KIND_PROFILE, [row])
        if result.count:
            return item
        # Nothing was written and nothing was billed. Say which of the four reasons it
        # was: a silent drop here is how a short dataset becomes unexplainable.
        if result.duplicates:
            raise DropItem(f"duplicate profile: {row.get('username')}")
        if result.unresolved:
            raise DropItem(f"profile resolved nothing: {row.get('username')}")
        if result.invalid:
            raise DropItem(f"profile would be rejected by the dataset: {row.get('username')}")
        if LEDGER.stop_reason is not None:
            # The budget, or the user's maxItems, or a broken charging channel. The
            # spider is closed by the ledger's own stop flag via `should_enqueue`; this
            # item simply must not ship unbilled.
            spider.crawler.engine.close_spider(spider, LEDGER.stop_reason)
            raise DropItem(f'stopped: {LEDGER.stop_reason}')
        raise DropItem(f"profile not delivered: {row.get('username')}")
