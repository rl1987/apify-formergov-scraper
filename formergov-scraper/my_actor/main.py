"""Main entry point for the FormerGov directory Apify Actor.

Reads the Actor input - a list of formergov.com URLs - and runs the Scrapy spider
against the FormerGov public JSON API. Each URL is classified as either an individual
profile page (scraped directly) or a directory search / home page (whose query string
is forwarded to the search API). The Apify-Scrapy integration (custom scheduler,
dataset item pipeline, proxy handling) is applied via ``apply_apify_settings``.

For an in-depth description of the Apify-Scrapy integration, see:
https://docs.apify.com/cli/docs/integrating-scrapy
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from apify import Actor
from apify.scrapy import apply_apify_settings
from scrapy.crawler import AsyncCrawlerRunner

from .formergov_api import is_unfiltered, search_params_from_url
from .spiders import FormerGovSpider as Spider

# Page size for filtered searches; bumped to the API max for whole-directory runs to
# minimise the number of (proxied) requests.
DEFAULT_PAGE_SIZE = 100
UNFILTERED_PAGE_SIZE = 1000


def _start_urls(actor_input: dict) -> list[str]:
    """Normalise the startUrls input (list of strings or {"url": ...} objects)."""
    urls: list[str] = []
    for entry in actor_input.get('startUrls') or []:
        url = entry.get('url') if isinstance(entry, dict) else entry
        if url:
            urls.append(str(url).strip())
    return urls


def _classify_urls(urls: list[str]) -> tuple[list[str], dict[str, Any] | None]:
    """Split the input URLs into direct profile usernames and a single search.

    A ``/directory/<username>`` URL is a profile to scrape directly; anything else
    (``/directory?...``, a bare ``/directory``, or the home page) is a search whose
    query string drives the directory search. Profile URLs take precedence: if any are
    given, only those are scraped. Otherwise the first search URL is used.
    """
    usernames: list[str] = []
    search_params: dict[str, Any] | None = None

    for url in urls:
        path = urlparse(url).path.strip('/')
        if path.startswith('directory/'):
            slug = path.split('directory/', 1)[1].split('/', 1)[0]
            if slug:
                usernames.append(slug)
                continue
        if search_params is None:
            search_params = search_params_from_url(url)

    # De-duplicate usernames, preserving order.
    seen: set[str] = set()
    unique = [u for u in usernames if not (u in seen or seen.add(u))]
    return unique, search_params


async def main() -> None:
    """Apify Actor main coroutine for executing the FormerGov Scrapy spider."""
    async with Actor:
        actor_input = await Actor.get_input() or {}

        urls = _start_urls(actor_input)
        if not urls:
            Actor.log.error('No startUrls provided - give at least one formergov.com URL to scrape.')
            return

        usernames, search_params = _classify_urls(urls)
        max_items = int(actor_input.get('maxItems') or 0)
        page_size = DEFAULT_PAGE_SIZE
        proxy_config = actor_input.get('proxyConfiguration')

        if usernames:
            # Direct mode: any profile URLs given win over a search URL.
            search_params = None
            Actor.log.info('Direct mode: scraping %d profile(s) by URL.', len(usernames))
        elif search_params is not None:
            if is_unfiltered(search_params):
                page_size = UNFILTERED_PAGE_SIZE
                Actor.log.info('Scraping entire directory (type=%s).', search_params['type'])
            else:
                Actor.log.info('Directory search params: %s', search_params)
        else:
            Actor.log.error('Could not derive anything to scrape from the given URLs.')
            return

        settings = apply_apify_settings(proxy_config=proxy_config)
        crawler_runner = AsyncCrawlerRunner(settings)
        await crawler_runner.crawl(
            Spider,
            search_params=search_params,
            usernames=usernames,
            max_items=max_items,
            page_size=page_size,
        )
