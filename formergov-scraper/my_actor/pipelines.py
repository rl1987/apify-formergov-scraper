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
    """Charge one pay-per-event unit for each unique profile before it is delivered.

    Registered at 900 in settings.py, i.e. BELOW the priority 1000 at which the
    Apify SDK injects ``apify.scrapy.pipelines.ActorDatasetPushPipeline``. At the
    original priority of 350 this also ran before the push, and a profile that the
    push pipeline then dropped — or one lost when the spider died between the two
    stages — was billed without ever reaching the user's dataset. The concern was
    real; moving the charge to 1100 was the wrong answer, because past the run's
    ``maxTotalChargeUsd`` ``Actor.charge`` bills nothing and raises nothing, so a
    profile charged after the push had already been delivered for free. Charging
    below 1000 and dropping a profile that could not be billed is neither
    billed-and-not-delivered nor delivered-and-not-billed: an unbillable profile
    never reaches the push.

    Runs after dedup, so duplicates are not billed. Charging is only active when the
    Actor runs on the Apify platform (or under local PPE testing via
    ``ACTOR_TEST_PAY_PER_EVENT``); in plain Scrapy runs it is a no-op.
    """

    def __init__(self) -> None:
        self._actor = None
        self._charging = False
        self._limit_reached = False
        #: Profiles this pipeline let through to the push at 1000, i.e. the ones the user
        #: actually received. Counting what the spider scraped would overstate a capped run.
        self._delivered = 0
        # Only a priced event may withhold a profile: a charge comes back with
        # nothing charged both past the cap and on a run that does not bill at all.
        self._billable = False

    def open_spider(self, spider: Spider) -> None:
        try:
            from apify import Actor

            if Actor.is_at_home() or os.environ.get('ACTOR_TEST_PAY_PER_EVENT'):
                self._actor = Actor
                self._charging = True
                info = Actor.get_charging_manager().get_pricing_info()
                self._billable = bool(info.is_pay_per_event) and PROFILE_EVENT in set(info.per_event_prices or {})
        except Exception as exc:  # noqa: BLE001 - charging is best-effort, never block scraping
            # A failed read must fall back to delivering, never to withholding.
            self._billable = False
            spider.logger.debug('Pay-per-event charging disabled: %s', exc)

    async def process_item(self, item: ProfileItem, spider: Spider) -> ProfileItem:
        if self._charging and self._actor is not None:
            result = await self._actor.charge(event_name=PROFILE_EVENT)
            # An unbillable profile is dropped below and never reaches the push, so a
            # profile counts as delivered only when the charge actually billed for it.
            withheld = self._billable and not getattr(result, 'charged_count', 0)
            if not withheld:
                self._delivered += 1
            if getattr(result, 'event_charge_limit_reached', False) and not self._limit_reached:
                self._limit_reached = True
                spider.logger.warning(
                    'Pay-per-event charge limit reached after %s profile(s); stopping the crawl.',
                    self._delivered,
                )
                # A cap hit still finishes SUCCEEDED, so the truncation has to explain
                # itself, or the short dataset reads as the end of the results.
                await self._actor.set_status_message(
                    f'Stopped after {self._delivered} profile(s): this run hit its maximum cost '
                    "setting, not the end of the results. Raise the run's max total charge, or "
                    'lower maxItems, to collect the rest.'
                )
                # Scrapy finishes the pipelines already in flight before it shuts the
                # crawl down, so a profile that was billed here still reaches the push.
                spider.crawler.engine.close_spider(spider, 'charge_limit_reached')
            if withheld:
                # Nothing was billed for this profile, so it must not be delivered.
                raise DropItem('the run reached its maximum total charge')
        return item
