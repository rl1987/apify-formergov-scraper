"""Main entry point for the Former Gov directory Apify Actor.

Reads the Actor input, resolves advanced-search facet names into the ids the API
expects, and runs the Scrapy spider against the Former Gov public JSON API. The
Apify-Scrapy integration (custom scheduler, dataset item pipeline, proxy handling)
is applied via ``apply_apify_settings``.

For an in-depth description of the Apify-Scrapy integration, see:
https://docs.apify.com/cli/docs/integrating-scrapy
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from apify import Actor
from apify.scrapy import apply_apify_settings
from scrapy.crawler import AsyncCrawlerRunner

from .formergov_api import build_search_params
from .spiders import FormerGovSpider as Spider


def _usernames_from_input(actor_input: dict) -> list[str]:
    """Collect explicit usernames and any profile URLs given as start URLs."""
    usernames: list[str] = []
    for username in actor_input.get('profileUsernames') or []:
        username = str(username).strip().strip('/')
        if username:
            usernames.append(username.rsplit('/', 1)[-1])

    for entry in actor_input.get('startUrls') or []:
        url = entry.get('url') if isinstance(entry, dict) else entry
        if not url:
            continue
        path = urlparse(str(url)).path.strip('/')
        # Expect .../directory/<username>
        if 'directory/' in path:
            slug = path.split('directory/', 1)[1].split('/', 1)[0]
            if slug:
                usernames.append(slug)

    # De-duplicate, preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for username in usernames:
        if username not in seen:
            seen.add(username)
            unique.append(username)
    return unique


async def main() -> None:
    """Apify Actor main coroutine for executing the Former Gov Scrapy spider."""
    async with Actor:
        actor_input = await Actor.get_input() or {}

        usernames = _usernames_from_input(actor_input)
        max_items = int(actor_input.get('maxItems') or 0)
        page_size = int(actor_input.get('pageSize') or 100)
        use_fallback = actor_input.get('useNjsparserFallback', True)
        proxy_config = actor_input.get('proxyConfiguration')

        search_params = None
        if not usernames:
            # No explicit profiles -> run a directory search. Facet name resolution
            # makes blocking HTTP calls to the meta endpoints; run them off the loop.
            search_params = await asyncio.to_thread(build_search_params, actor_input, log=Actor.log)
            Actor.log.info('Directory search params: %s', search_params)
        else:
            Actor.log.info('Direct mode: scraping %d profile(s) by username.', len(usernames))

        settings = apply_apify_settings(proxy_config=proxy_config)
        crawler_runner = AsyncCrawlerRunner(settings)
        await crawler_runner.crawl(
            Spider,
            search_params=search_params,
            usernames=usernames,
            max_items=max_items,
            page_size=page_size,
            use_fallback=use_fallback,
        )
