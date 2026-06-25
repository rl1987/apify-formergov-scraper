"""Item pipelines for the FormerGov scraper.

For detailed information on creating and utilizing item pipelines, refer to the official documentation:
http://doc.scrapy.org/en/latest/topics/item-pipeline.html
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from scrapy.exceptions import DropItem

if TYPE_CHECKING:
    from scrapy import Spider

    from .items import ProfileItem

# Pay-per-event id; must match the event configured in the Actor's pricing.
PROFILE_EVENT = 'profile-row'


class ProfileDedupPipeline:
    """Drop duplicate profiles (the same username can surface across search pages)."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def process_item(self, item: ProfileItem, spider: Spider) -> ProfileItem:
        username = item.get('username')
        if username and username in self._seen:
            raise DropItem(f'Duplicate profile: {username}')
        if username:
            self._seen.add(username)
        return item


class ProfileChargePipeline:
    """Charge one pay-per-event unit for each unique profile delivered to the dataset.

    Runs after dedup, so duplicates are not billed. Charging is only active when the
    Actor runs on the Apify platform (or under local PPE testing via
    ``ACTOR_TEST_PAY_PER_EVENT``); in plain Scrapy runs it is a no-op.
    """

    def __init__(self) -> None:
        self._actor = None
        self._charging = False
        self._limit_reached = False

    def open_spider(self, spider: Spider) -> None:
        try:
            from apify import Actor

            if Actor.is_at_home() or os.environ.get('ACTOR_TEST_PAY_PER_EVENT'):
                self._actor = Actor
                self._charging = True
        except Exception as exc:  # noqa: BLE001 - charging is best-effort, never block scraping
            spider.logger.debug('Pay-per-event charging disabled: %s', exc)

    async def process_item(self, item: ProfileItem, spider: Spider) -> ProfileItem:
        if self._charging and not self._limit_reached and self._actor is not None:
            result = await self._actor.charge(event_name=PROFILE_EVENT)
            if getattr(result, 'event_charge_limit_reached', False):
                self._limit_reached = True
                spider.logger.warning(
                    'Pay-per-event charge limit reached; stopping the crawl to avoid further work.'
                )
                spider.crawler.engine.close_spider(spider, 'charge_limit_reached')
        return item
