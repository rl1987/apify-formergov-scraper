"""Helpers for talking to the FormerGov public JSON API.

The site (https://formergov.com) is a Next.js front-end backed by a public,
unauthenticated JSON API hosted at ``https://cdn.formergov.com/api``. The site's
``/directory`` search page carries every filter as a URL query parameter, and those
parameters map 1:1 onto the search API. So the Actor takes the URL straight from the
browser and forwards its query string to the API - no facet-name resolution needed.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

API_BASE = 'https://cdn.formergov.com/api/main'
SITE_BASE = 'https://formergov.com'

# Search endpoint, e.g. /api/main/data/profiles?type=combined&page=1&pageSize=20
SEARCH_PATH = '/data/profiles'
# Full profile document, e.g. /api/main/data/profile/brianlevine
PROFILE_PATH = '/data/profile/{username}'

# The FormerGov WAF rejects non-browser User-Agents with HTTP 403, so every request
# must present a browser UA. These are set per-request so they survive the
# Apify-Scrapy integration overriding the project's USER_AGENT setting.
BROWSER_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Origin': SITE_BASE,
    'Referer': f'{SITE_BASE}/',
}

# Query params the spider controls itself; ignored when read off a pasted URL.
_PAGINATION_PARAMS = ('page', 'pageSize')


def profile_url(username: str) -> str:
    """Return the JSON API URL for a single profile."""
    return f'{API_BASE}{PROFILE_PATH.format(username=username)}'


def profile_page_url(username: str) -> str:
    """Return the public profile page URL (used as the item's profileUrl)."""
    return f'{SITE_BASE}/directory/{username}'


def search_url(params: dict[str, Any]) -> str:
    """Build a directory-search API URL from already-resolved query params."""
    query = {k: v for k, v in params.items() if v not in (None, '', [])}
    return f'{API_BASE}{SEARCH_PATH}?{urlencode(query)}'


def search_params_from_url(url: str) -> dict[str, Any]:
    """Extract directory-search query params from a formergov.com ``/directory`` URL.

    The site already stores filters as UUIDs in the URL, so the query string is
    forwarded verbatim to the search API. Pagination params are dropped (the spider
    paginates), and ``type`` defaults to ``combined`` (the site's default) when absent.
    Repeated values (``?sectors=a&sectors=b``) are comma-joined, matching the API.
    A bare ``/directory`` or home-page URL yields just ``{'type': 'combined'}``, i.e.
    an unfiltered search over the whole directory.
    """
    parsed = parse_qs(urlparse(url).query)
    params: dict[str, Any] = {}
    for key, values in parsed.items():
        if key in _PAGINATION_PARAMS:
            continue
        joined = ','.join(v for v in values if v)
        if joined:
            params[key] = joined
    params.setdefault('type', 'combined')
    return params


def is_unfiltered(params: dict[str, Any]) -> bool:
    """True if the search has no filters beyond ``type`` (i.e. the whole directory)."""
    return not any(key != 'type' for key in params)
