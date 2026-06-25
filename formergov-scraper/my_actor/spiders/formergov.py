# ruff: noqa: RUF012, TID252

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from scrapy import Request, Spider

from ..formergov_api import profile_url, search_url
from ..items import ProfileItem
from ..parsers import build_item_from_profile

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator

    from scrapy.http.response import Response


class FormerGovSpider(Spider):
    """Scrape the Former Gov directory via its public JSON API.

    Two modes:
      * Search mode - page through ``/data/profiles`` with the requested filters and
        fetch every matching profile.
      * Direct mode - fetch a specific set of profiles by username (no search).
    """

    name = 'formergov'

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

        self.logger.error('No searchType/filters and no usernames provided - nothing to scrape.')

    def _search_request(self, page: int) -> Request:
        params = dict(self.search_params or {})
        params['page'] = page
        params['pageSize'] = self.page_size
        return Request(search_url(params), callback=self.parse_search, cb_kwargs={'page': page})

    def _profile_request(self, username: str) -> Request:
        return Request(
            profile_url(username),
            callback=self.parse_profile,
            errback=self.on_profile_error,
            cb_kwargs={'username': username},
        )

    def _can_enqueue_more(self) -> bool:
        return self.max_items == 0 or self.enqueued_profiles < self.max_items

    # -- search results ----------------------------------------------------------

    def parse_search(self, response: Response, page: int) -> Generator[Request, None, None]:
        try:
            data = json.loads(response.text)
        except ValueError:
            self.logger.error('Search page %s returned non-JSON (HTTP %s).', page, response.status)
            return

        usernames = [entry.get('username') for entry in data.get('usernames', []) if entry.get('username')]
        total_pages = data.get('totalPages') or 0
        total_hits = data.get('totalHits')

        if page == 1:
            self.logger.info('Search matched %s profiles across %s page(s).', total_hits, total_pages)

        for username in usernames:
            if not self._can_enqueue_more():
                self.logger.info('Reached maxItems=%s; stopping enqueue.', self.max_items)
                return
            self.enqueued_profiles += 1
            yield self._profile_request(username)

        if page < total_pages and self._can_enqueue_more():
            yield self._search_request(page=page + 1)

    # -- individual profiles -----------------------------------------------------

    def parse_profile(self, response: Response, username: str) -> Generator[ProfileItem, None, None]:
        try:
            data = json.loads(response.text)
        except ValueError:
            data = None

        if not isinstance(data, dict) or not data:
            self.logger.warning('Skipping %s: profile API returned empty/invalid JSON (HTTP %s).', username, response.status)
            return

        row = build_item_from_profile(data, username, self._now())
        yield ProfileItem(**row)

    def on_profile_error(self, failure: Any) -> None:
        username = failure.request.cb_kwargs.get('username', '?')
        self.logger.warning('Profile API request failed for %s: %s', username, failure.value)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
