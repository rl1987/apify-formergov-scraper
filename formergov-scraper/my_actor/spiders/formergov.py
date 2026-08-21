# ruff: noqa: RUF012, TID252

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from scrapy import Request, Spider, signals
from scrapy.exceptions import DontCloseSpider

from ..formergov_api import BROWSER_HEADERS, profile_url, search_url
from ..items import ProfileItem
from ..parsers import build_item_from_profile

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

    from scrapy.crawler import Crawler
    from scrapy.http.response import Response


class FormerGovSpider(Spider):
    """Scrape the FormerGov directory via its public JSON API.

    Two modes:
      * Search mode - page through ``/data/profiles`` with the requested filters and
        fetch every matching profile.
      * Direct mode - fetch a specific set of profiles by username (no search).

    Profiles blocked by the anti-bot layer (HTTP 403/429/5xx) after retries are not
    dropped immediately: they are collected and re-tried at the end of the run, once
    the request queue has drained and any temporary IP block has cleared.
    """

    name = 'formergov'

    # How many end-of-run passes to retry profiles that were blocked mid-run.
    MAX_REQUEUE_ROUNDS = 2

    # The search backend (elasticsearch) only returns results within a from+size
    # window; offsets at/after this are empty. A single search cannot exceed it.
    SEARCH_RESULT_WINDOW = 10000

    def __init__(
        self,
        search_params: dict[str, Any] | None = None,
        usernames: list[str] | None = None,
        max_items: int = 0,
        page_size: int = 100,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.search_params = search_params
        self.seed_usernames = usernames or []
        self.max_items = max_items or 0  # 0 == unlimited
        self.page_size = max(1, min(int(page_size or 100), 1000))
        self.enqueued_profiles = 0
        self.scraped_count = 0
        self.not_found_count = 0
        self.blocked_usernames: set[str] = set()
        self.blocked_search_pages: set[int] = set()
        self.requeue_round = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler, *args: Any, **kwargs: Any) -> FormerGovSpider:
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider.on_spider_idle, signal=signals.spider_idle)
        return spider

    # -- request generation ------------------------------------------------------

    async def start(self) -> AsyncGenerator[Request, None]:
        """Yield the initial requests (Scrapy >= 2.13 entry point)."""
        if self.seed_usernames:
            for username in self.seed_usernames:
                if not self._can_enqueue_more():
                    break
                self.enqueued_profiles += 1
                yield self._profile_request(username)
            return

        if self.search_params:
            yield self._search_request(page=1)
            return

        self.logger.error('No search params and no usernames provided - nothing to scrape.')

    def _search_request(self, page: int, *, dont_filter: bool = False) -> Request:
        params = dict(self.search_params or {})
        params['page'] = page
        params['pageSize'] = self.page_size
        return Request(
            search_url(params),
            callback=self.parse_search,
            errback=self.on_search_error,
            headers=BROWSER_HEADERS,
            dont_filter=dont_filter,
            cb_kwargs={'page': page},
        )

    def _profile_request(self, username: str, *, dont_filter: bool = False) -> Request:
        return Request(
            profile_url(username),
            callback=self.parse_profile,
            errback=self.on_profile_error,
            headers=BROWSER_HEADERS,
            dont_filter=dont_filter,
            cb_kwargs={'username': username},
        )

    def _can_enqueue_more(self) -> bool:
        return self.max_items == 0 or self.enqueued_profiles < self.max_items

    # -- search results ----------------------------------------------------------

    def parse_search(self, response: Response, page: int) -> Generator[Request, None, None]:
        try:
            data = json.loads(response.text)
        except ValueError:
            # A 2xx with a non-JSON body is usually a WAF interstitial - retry at end of run.
            self.logger.warning('Search page %s returned non-JSON (HTTP %s); queued for end-of-run retry.', page, response.status)
            self.blocked_search_pages.add(page)
            return

        usernames = [entry.get('username') for entry in data.get('usernames', []) if entry.get('username')]
        total_pages = data.get('totalPages') or 0
        total_hits = data.get('totalHits')

        if page == 1:
            self.logger.info('Search matched %s profiles across %s page(s).', total_hits, total_pages)
            if isinstance(total_hits, int) and total_hits > self.SEARCH_RESULT_WINDOW:
                self.logger.warning(
                    'This search has %s results but the API only returns the first %s. '
                    'Narrow the search with filters (e.g. jurisdiction, sector, state) to '
                    'reach the rest.',
                    total_hits,
                    self.SEARCH_RESULT_WINDOW,
                )

        for username in usernames:
            if not self._can_enqueue_more():
                self.logger.info('Reached maxItems=%s; stopping enqueue.', self.max_items)
                return
            self.enqueued_profiles += 1
            yield self._profile_request(username)

        # Stop before the result window: the next page starts at offset page*pageSize.
        next_offset = page * self.page_size
        if page < total_pages and self._can_enqueue_more() and next_offset < self.SEARCH_RESULT_WINDOW:
            yield self._search_request(page=page + 1)

    # -- individual profiles -----------------------------------------------------

    def parse_profile(self, response: Response, username: str) -> Generator[ProfileItem, None, None]:
        try:
            data = json.loads(response.text)
        except ValueError:
            data = None

        if not isinstance(data, dict) or not data:
            # A 2xx with an unexpected body - treat as transient and retry at end of run.
            self.logger.warning('Profile %s returned empty/invalid JSON (HTTP %s); will retry.', username, response.status)
            self.blocked_usernames.add(username)
            return

        self.blocked_usernames.discard(username)
        self.scraped_count += 1
        row = build_item_from_profile(data, username, self._now())
        yield ProfileItem(**row)

    def on_search_error(self, failure: Any) -> None:
        page = failure.request.cb_kwargs.get('page', '?')
        response = getattr(failure.value, 'response', None)
        status = getattr(response, 'status', None)
        # 403/429/5xx or a transport error - likely a temporary IP block. Retry at end.
        self.blocked_search_pages.add(page)
        self.logger.warning('Search page %s failed (HTTP %s); queued for end-of-run retry.', page, status or 'error')

    def on_profile_error(self, failure: Any) -> None:
        username = failure.request.cb_kwargs.get('username', '?')
        response = getattr(failure.value, 'response', None)
        status = getattr(response, 'status', None)

        if status == 404:
            # The username has no profile document (private/removed). Expected; not a block.
            self.not_found_count += 1
            self.logger.info('Profile %s not found (HTTP 404); skipping.', username)
            return

        # 403/429/5xx or a transport error - likely a temporary IP block. Retry at end.
        self.blocked_usernames.add(username)
        self.logger.warning('Profile %s failed (HTTP %s); queued for end-of-run retry.', username, status or 'error')

    # -- end-of-run retry of blocked profiles ------------------------------------

    def on_spider_idle(self) -> None:
        """When the queue drains, retry search pages/profiles blocked earlier (the block has likely lifted)."""
        if not self.blocked_search_pages and not self.blocked_usernames:
            return

        if self.requeue_round >= self.MAX_REQUEUE_ROUNDS:
            if self.blocked_search_pages:
                self.logger.error(
                    '%d search page(s) still blocked after %d retry rounds; results are likely '
                    'INCOMPLETE. The proxy IPs are being rejected by the site - switch Proxy '
                    'configuration to RESIDENTIAL groups and re-run. Blocked pages: %s',
                    len(self.blocked_search_pages),
                    self.requeue_round,
                    ', '.join(str(p) for p in sorted(self.blocked_search_pages)),
                )
            if self.blocked_usernames:
                self.logger.warning(
                    '%d profile(s) still failing after %d retry rounds; giving up: %s',
                    len(self.blocked_usernames),
                    self.requeue_round,
                    ', '.join(sorted(self.blocked_usernames)),
                )
            return

        self.requeue_round += 1
        pages = sorted(self.blocked_search_pages)
        self.blocked_search_pages.clear()
        usernames = sorted(self.blocked_usernames)
        self.blocked_usernames.clear()
        if pages:
            self.logger.info('Requeue round %d: retrying %d blocked search page(s).', self.requeue_round, len(pages))
            for page in pages:
                self.crawler.engine.crawl(self._search_request(page, dont_filter=True))
        if usernames:
            self.logger.info('Requeue round %d: retrying %d blocked profile(s).', self.requeue_round, len(usernames))
            for username in usernames:
                self.crawler.engine.crawl(self._profile_request(username, dont_filter=True))
        raise DontCloseSpider

    def closed(self, reason: str) -> None:
        self.logger.info(
            'Finished (%s): scraped %d, not-found(404) %d, permanently failed %d, unfetched search pages %d.',
            reason,
            self.scraped_count,
            self.not_found_count,
            len(self.blocked_usernames),
            len(self.blocked_search_pages),
        )
        self.logger.info('Did you find this useful? Rate this actor on Apify!')

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
