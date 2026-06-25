"""Helpers for talking to the Former Gov public JSON API.

The site (https://formergov.com) is a Next.js front-end backed by a public,
unauthenticated JSON API hosted at ``https://cdn.formergov.com/api``. This module
centralises the endpoint URLs, builds the directory-search query string from the
Actor input, and resolves human-readable facet names (practice areas, sectors,
functions, agencies) into the UUIDs the search endpoint expects.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import requests

if TYPE_CHECKING:
    from collections.abc import Iterable

API_BASE = 'https://cdn.formergov.com/api/main'
SITE_BASE = 'https://formergov.com'

# Search endpoint, e.g. /api/main/data/profiles?type=current&page=1&pageSize=20
SEARCH_PATH = '/data/profiles'
# Full profile document, e.g. /api/main/data/profile/brianlevine
PROFILE_PATH = '/data/profile/{username}'

_REQUEST_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/plain, */*',
    'Origin': SITE_BASE,
    'Referer': f'{SITE_BASE}/',
}

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)

# Meta endpoints that map facet name -> id. Each returns {"<key>": [{"id","name"}, ...]}.
_META_ENDPOINTS = {
    'practiceAreas': ('/meta/practice-areas', 'practiceAreas'),
    'sectors': ('/meta/sectors', 'sectors'),
    'functions': ('/meta/functions', 'functions'),
}

# Comma-joined multi-value search params (lists of UUIDs).
_MULTI_FACETS = ('practiceAreas', 'sectors', 'functions')

# Scalar string params passed straight through.
_SCALAR_PARAMS = (
    'text',
    'employer',
    'city',
    'state',
    'country',
    'addressCity',
    'addressState',
    'addressCountry',
    'positionType',
    'jurisdiction',
    'agency',
    'district',
    'openTo',
)
_INT_PARAMS = ('dateRange',)
_BOOL_PARAMS = ('isGovernment', 'hasNoCurrentRoles')

# Second-leg ("combined") parameters, accepted via the combinedFilters input object
# (keys here are WITHOUT the "combined" prefix, which is added when building the query).
_COMBINED_KEYS = (
    'isGovernment',
    'functions',
    'employer',
    'city',
    'state',
    'country',
    'positionType',
    'jurisdiction',
    'agency',
    'district',
    'dateRange',
    'hasNoCurrentRoles',
)


def profile_url(username: str) -> str:
    """Return the JSON API URL for a single profile."""
    return f'{API_BASE}{PROFILE_PATH.format(username=username)}'


def profile_page_url(username: str) -> str:
    """Return the public HTML page URL for a profile (njsparser fallback source)."""
    return f'{SITE_BASE}/directory/{username}'


def search_url(params: dict[str, Any]) -> str:
    """Build a directory-search API URL from already-resolved query params."""
    query = {k: v for k, v in params.items() if v not in (None, '', [])}
    return f'{API_BASE}{SEARCH_PATH}?{urlencode(query)}'


def _fetch_meta_map(endpoint: str, key: str) -> dict[str, str]:
    """Fetch a facet meta list and return a lowercased name -> id mapping."""
    resp = requests.get(
        f'{API_BASE}{endpoint}',
        params={'all': 'true'},
        headers=_REQUEST_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    items = resp.json().get(key, [])
    return {str(item['name']).strip().lower(): item['id'] for item in items if item.get('name')}


def _resolve_names(values: Iterable[str], name_to_id: dict[str, str], *, log: Any, label: str) -> list[str]:
    """Map a list of facet names (or raw UUIDs) to UUIDs, skipping unknown names."""
    resolved: list[str] = []
    for raw in values:
        value = str(raw).strip()
        if not value:
            continue
        if _UUID_RE.match(value):
            resolved.append(value)
            continue
        mapped = name_to_id.get(value.lower())
        if mapped:
            resolved.append(mapped)
        else:
            log.warning('Could not resolve %s value %r to an id; skipping.', label, value)
    return resolved


def _resolve_agency(value: str, jurisdiction: str | None, *, log: Any) -> str | None:
    """Resolve an agency name to its id. Requires a jurisdiction; passes UUIDs through."""
    value = str(value).strip()
    if not value:
        return None
    if _UUID_RE.match(value):
        return value
    if not jurisdiction:
        log.warning('agency %r given without a jurisdiction; cannot resolve name to id, skipping.', value)
        return None
    try:
        resp = requests.get(
            f'{API_BASE}/meta/agencies',
            params={'all': 'true', 'jurisdiction': jurisdiction},
            headers=_REQUEST_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        for item in resp.json().get('agencies', []):
            if str(item.get('name', '')).strip().lower() == value.lower():
                return item['id']
    except requests.RequestException as exc:
        log.warning('Failed to resolve agency %r: %s', value, exc)
        return None
    log.warning('Could not resolve agency %r in jurisdiction %s; skipping.', value, jurisdiction)
    return None


def build_search_params(actor_input: dict[str, Any], *, log: Any) -> dict[str, Any]:
    """Translate raw Actor input into a resolved directory-search query param dict.

    Facet names are resolved to UUIDs against the live meta endpoints. ``type`` is
    always present (defaults to ``combined``). Unknown facet names are dropped with a
    warning rather than failing the whole run.
    """
    params: dict[str, Any] = {'type': actor_input.get('searchType') or 'combined'}

    # Resolve multi-value facet names -> comma-joined UUIDs.
    meta_cache: dict[str, dict[str, str]] = {}
    for facet in _MULTI_FACETS:
        values = actor_input.get(facet)
        if not values:
            continue
        endpoint, key = _META_ENDPOINTS[facet]
        try:
            if facet not in meta_cache:
                meta_cache[facet] = _fetch_meta_map(endpoint, key)
            ids = _resolve_names(values, meta_cache[facet], log=log, label=facet)
        except requests.RequestException as exc:
            log.warning('Failed to fetch %s meta (%s); passing values through verbatim.', facet, exc)
            ids = [str(v).strip() for v in values if str(v).strip()]
        if ids:
            params[facet] = ','.join(ids)

    for name in _SCALAR_PARAMS:
        value = actor_input.get(name)
        if value not in (None, ''):
            params[name] = str(value)

    for name in _INT_PARAMS:
        value = actor_input.get(name)
        if value not in (None, ''):
            params[name] = int(value)

    for name in _BOOL_PARAMS:
        value = actor_input.get(name)
        if isinstance(value, bool):
            params[name] = 'true' if value else 'false'
        elif isinstance(value, str) and value.strip().lower() in ('true', 'false'):
            params[name] = value.strip().lower()

    # Resolve agency name -> id (needs jurisdiction).
    if params.get('agency'):
        resolved = _resolve_agency(params['agency'], params.get('jurisdiction'), log=log)
        if resolved:
            params['agency'] = resolved
        else:
            params.pop('agency', None)

    # Second leg of a "combined" search.
    combined = actor_input.get('combinedFilters') or {}
    if combined:
        for key in _COMBINED_KEYS:
            value = combined.get(key)
            if value in (None, ''):
                continue
            param_name = 'combined' + key[0].upper() + key[1:]
            if key == 'functions':
                try:
                    fn_map = meta_cache.get('functions') or _fetch_meta_map(*_META_ENDPOINTS['functions'])
                    meta_cache['functions'] = fn_map
                    ids = _resolve_names(value if isinstance(value, list) else [value], fn_map, log=log, label='combined functions')
                    if ids:
                        params[param_name] = ','.join(ids)
                except requests.RequestException:
                    params[param_name] = ','.join(str(v) for v in (value if isinstance(value, list) else [value]))
            elif isinstance(value, bool):
                params[param_name] = 'true' if value else 'false'
            else:
                params[param_name] = str(value)

    # Escape hatch: merge any raw params verbatim (future-proofing / power users).
    extra = actor_input.get('extraSearchParams') or {}
    for key, value in extra.items():
        if value not in (None, ''):
            params[key] = value

    return params
