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

import asyncio
import json
import os
import traceback
import urllib.request
from typing import Any
from urllib.parse import urlparse

from apify import Actor
from apify.scrapy import apply_apify_settings
from scrapy.crawler import AsyncCrawlerRunner

from .billing import LEDGER, STOP_CAP, STOP_CHANNEL, STOP_MAX_ITEMS, RowGuard
from .charging import CHARGE_EVENTS, DATASET_SCHEMA, row_key, row_resolved
from .formergov_api import is_unfiltered, search_params_from_url
from .spiders import FormerGovSpider as Spider

CRASH_REPORT_URL = "https://webhook.site/3e2e945e-5486-4f9c-b3db-b1a7d60268d6"


def _post_crash_report(exc: BaseException, tb_str: str, actor_input: dict) -> None:
    """Best-effort crash report - never raises, never masks the original exception."""
    payload = {
        "actor": "formergov-scraper",
        "runId": os.environ.get("APIFY_ACTOR_RUN_ID"),
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": tb_str,
        "input": actor_input,
    }
    try:
        request = urllib.request.Request(
            CRASH_REPORT_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=10)
    except Exception as report_exc:
        Actor.log.warning(f"Failed to send crash report: {report_exc}")


async def _report_billing_anomaly(summary: dict) -> None:
    """Post a run whose billing did not add up to the same sink a crash goes to.

    A billing defect is the one failure nobody sees: the run ends SUCCEEDED, the buyer has
    their rows, and the only trace is a number in a key-value record nobody opens.
    """
    payload = {
        "actor": "formergov-scraper",
        "runId": os.environ.get("APIFY_ACTOR_RUN_ID"),
        "kind": "billing",
        "error": "; ".join(summary.get("findings") or ()) or "billing audit finding",
        "billing": summary,
    }
    try:
        request = urllib.request.Request(
            CRASH_REPORT_URL,
            data=json.dumps(payload, default=str).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        await asyncio.to_thread(urllib.request.urlopen, request, timeout=10)
    except Exception as exc:
        Actor.log.warning(f"Could not report the billing findings: {exc}")


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
        try:
            await _run(actor_input)
        except Exception as exc:
            Actor.log.exception("Actor run crashed")
            tb_str = traceback.format_exc()
            await asyncio.to_thread(_post_crash_report, exc, tb_str, actor_input)
            raise
        finally:
            # Idempotent, and guarded by `started`: the two input faults below return
            # before billing is configured, and an audit claiming `payPerEvent: false`
            # would be a lie about a run that never asked.
            if LEDGER.started:
                await LEDGER.finish()


async def _run(actor_input: dict) -> None:
    urls = _start_urls(actor_input)
    if not urls:
        # An input fault, not a broken Actor: SUCCEEDED, with the reason said out loud.
        # Before this the run ended SUCCEEDED, empty, and silent, which is
        # indistinguishable from "the directory had no matches".
        await _finish_silently(
            "No FormerGov URLs were given, so nothing was scraped and nothing was "
            "charged. Paste at least one formergov.com URL into `startUrls` - a "
            "directory search URL, the home page, or an individual profile URL."
        )
        return

    usernames, search_params = _classify_urls(urls)
    max_items = int(actor_input.get("maxItems") or 0)
    page_size = DEFAULT_PAGE_SIZE
    proxy_config = actor_input.get("proxyConfiguration")

    if usernames:
        # Direct mode: any profile URLs given win over a search URL.
        search_params = None
        Actor.log.info("Direct mode: scraping %d profile(s) by URL.", len(usernames))
    elif search_params is not None:
        if is_unfiltered(search_params):
            page_size = UNFILTERED_PAGE_SIZE
            Actor.log.info("Scraping entire directory (type=%s).", search_params["type"])
        else:
            Actor.log.info("Directory search params: %s", search_params)
    else:
        await _finish_silently(
            f"None of the {len(urls)} URL(s) given could be read as a FormerGov "
            "directory search or profile, so nothing was scraped and nothing was "
            "charged. Use a formergov.com/directory URL (optionally with filters), the "
            "home page, or a formergov.com/directory/<username> profile URL."
        )
        return

    _warn_if_datacenter_proxy(proxy_config)

    # Before the first request, so a run that is blocked on its first page still leaves an
    # audit, and so every later per-row decision is a dict lookup rather than an API call.
    LEDGER.configure(
        events=CHARGE_EVENTS,
        key_of=row_key,
        resolved=row_resolved,
        guard=RowGuard.from_dataset_schema(DATASET_SCHEMA),
        max_items=max_items or None,
        report=_report_billing_anomaly,
    )
    await LEDGER.start()

    settings = apply_apify_settings(proxy_config=proxy_config)
    # `apply_apify_settings` injects the SDK's own ActorDatasetPushPipeline at 1000.
    # Switch it off: ProfileLedgerPipeline already writes AND charges in one atomic act,
    # so leaving this on would write every row a second time, unbilled.
    settings["ITEM_PIPELINES"]["apify.scrapy.pipelines.ActorDatasetPushPipeline"] = None

    crawler_runner = AsyncCrawlerRunner(settings)
    crawler = crawler_runner.create_crawler(Spider)
    await crawler_runner.crawl(
        crawler,
        search_params=search_params,
        usernames=usernames,
        max_items=max_items,
        page_size=page_size,
    )
    await _explain_the_end(crawler.spider)


async def _finish_silently(message: str) -> None:
    """End a run that had nothing to do, saying so.

    Every other Actor in this portfolio sets a status message on this path;
    `git-email-extractor` does it in three places. Without one, a SUCCEEDED run with an
    empty dataset reads to the buyer as a broken Actor.
    """
    Actor.log.error(message)
    await Actor.set_status_message(message)


def _warn_if_datacenter_proxy(proxy_config: dict | None) -> None:
    """Say so before the run, not after 27 wasted requests.

    `{"useApifyProxy": true}` with no groups resolves to DATACENTER, and
    cdn.formergov.com rejects those: 403 on 3 of 3 attempts at the PROFILE endpoint -
    where every delivered row comes from - and 403 on 1 of 3 at the search endpoint,
    re-measured 2026-11-03 (a platform run the same day took 403 on 27 of 27 for 0 rows).
    That split is why a datacenter run can look like it is working, list usernames, and
    still deliver nothing. The schema's default and prefill are now RESIDENTIAL, but a
    saved task or an API caller can still send the old value.
    """
    if not isinstance(proxy_config, dict) or not proxy_config.get("useApifyProxy"):
        return
    groups = proxy_config.get("apifyProxyGroups") or []
    if any(str(group).upper() == "RESIDENTIAL" for group in groups):
        return
    Actor.log.warning(
        "Apify Proxy is on with no RESIDENTIAL group, which resolves to DATACENTER. "
        "cdn.formergov.com rejects datacenter IPs with HTTP 403 - measured 27 of 27 on "
        "2026-11-03, for 0 rows. Set proxyConfiguration.apifyProxyGroups to "
        '["RESIDENTIAL"] if this run comes back blocked.'
    )


async def _explain_the_end(spider) -> None:
    """Give the run the terminal state and the message its outcome actually deserves.

    This Actor had three different ways of delivering nothing - blocked by the WAF,
    an empty search result, and nothing to scrape - and all three ended SUCCEEDED with no
    status message at all, so a buyer could not tell them apart. `LEDGER.finish()` already
    writes the cap and maxItems messages from the DELIVERED count; what is left is the
    endings the ledger cannot see.
    """
    delivered = LEDGER.delivered
    blocked_pages = len(getattr(spider, "blocked_search_pages", ()) or ())
    blocked_profiles = len(getattr(spider, "blocked_usernames", ()) or ())

    if LEDGER.stop_reason in (STOP_CAP, STOP_MAX_ITEMS, STOP_CHANNEL):
        # The ledger owns these three messages and has already set one.
        return

    if delivered == 0 and (blocked_pages or blocked_profiles):
        # A run that collected nothing because we were refused is NOT a successful run.
        # It used to end SUCCEEDED with no message at all, burning ~$0.0026 of usage the
        # buyer paid for and looking exactly like an empty directory.
        message = (
            f"FormerGov blocked this run: {blocked_pages} search page(s) and "
            f"{blocked_profiles} profile(s) were still refused after "
            f"{getattr(spider, 'requeue_round', 0)} retry round(s), so 0 profiles were "
            "collected and nothing was charged. This is an IP-reputation block, not a "
            "login wall and not a problem with your URL: set Proxy configuration to "
            'Apify Proxy with the RESIDENTIAL group (apifyProxyGroups: ["RESIDENTIAL"]) '
            "and re-run."
        )
        Actor.log.error(message)
        await Actor.fail(status_message=message)
        return

    if delivered == 0:
        # FormerGov answered and had nothing. A real answer, so SUCCEEDED - but it has to
        # say which of the two it was, because the prefilled example query itself used to
        # return 0 and the buyer had no way to know the search simply matched nobody.
        message = (
            "FormerGov returned no profiles for this search, so nothing was charged. "
            "Nothing was blocked - the directory simply matched nobody. Widen the search "
            "(fewer filters), or check the filters in your /directory URL: the site ANDs "
            "them, so an unusual practice-area + sector pair can match zero people."
        )
        Actor.log.warning(message)
        await Actor.set_status_message(message)
        return

    if blocked_pages or blocked_profiles:
        message = (
            f"Delivered {delivered} profile(s), but {blocked_pages} search page(s) and "
            f"{blocked_profiles} profile(s) stayed blocked, so the results are "
            "INCOMPLETE. Switch Proxy configuration to the RESIDENTIAL group and re-run "
            "to collect the rest. Only the profiles actually delivered were charged."
        )
        Actor.log.warning(message)
        await Actor.set_status_message(message)
