"""Parse Former Gov profile data into output rows.

Primary path: the clean profile JSON returned by ``cdn.formergov.com/api``.
Fallback path: parse the Next.js flight data embedded in the public profile page
HTML using ``njsparser`` (used only when the JSON API is unavailable for a profile).
"""

from __future__ import annotations

import json
import re
from typing import Any

from .formergov_api import profile_page_url

CDN_IMAGE_BASE = 'https://cdn.formergov.com'

_EMAIL_RE = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_LINKEDIN_PROFILE_RE = re.compile(r'linkedin\.com/(?:in|pub)/', re.IGNORECASE)


def rich_text_to_plain(node: Any) -> str:
    """Flatten a Tiptap/ProseMirror-style rich-text document into plain text."""
    parts: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            if item.get('type') == 'text' and isinstance(item.get('text'), str):
                parts.append(item['text'])
            for value in item.values():
                if isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(item, list):
            for value in item:
                walk(value)

    walk(node)
    return ' '.join(p.strip() for p in parts if p and p.strip()).strip()


def classify_websites(websites: list[dict[str, Any]]) -> tuple[str | None, str | None, list[dict[str, str]]]:
    """Split a profile's websites into (linkedin_url, primary_website_url, all_websites).

    A website is treated as LinkedIn if its url points at a LinkedIn profile or it is
    labelled "LinkedIn". The primary website is the first non-LinkedIn url.
    """
    linkedin_url: str | None = None
    website_url: str | None = None
    cleaned: list[dict[str, str]] = []

    for site in websites or []:
        url = (site.get('url') or '').strip()
        name = (site.get('name') or '').strip()
        if not url:
            continue
        cleaned.append({'name': name, 'url': url})
        is_linkedin = bool(_LINKEDIN_PROFILE_RE.search(url)) or name.lower() == 'linkedin'
        if is_linkedin:
            if linkedin_url is None:
                linkedin_url = url
        elif website_url is None:
            website_url = url

    return linkedin_url, website_url, cleaned


def extract_email(*texts: str) -> str | None:
    """Return the first email address found across the given text blobs, if any."""
    for text in texts:
        if not text:
            continue
        match = _EMAIL_RE.search(text)
        if match:
            return match.group(0)
    return None


def _full_name(first: str, middle: str, last: str) -> str:
    return ' '.join(p for p in (first.strip(), middle.strip(), last.strip()) if p).strip()


def _profile_picture_url(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith('http'):
        return value
    return f'{CDN_IMAGE_BASE}{value}'


def build_item_from_profile(data: dict[str, Any], username: str, scraped_at: str) -> dict[str, Any]:
    """Map the profile JSON document to a flat output row."""
    first = data.get('firstName') or ''
    middle = data.get('middleName') or ''
    last = data.get('lastName') or ''
    address = data.get('address') or {}

    linkedin_url, website_url, websites = classify_websites(data.get('websites') or [])

    biography = rich_text_to_plain(data.get('biography'))
    rep_matters = rich_text_to_plain(data.get('representativeMatters'))
    email = extract_email(biography, rep_matters, data.get('headline') or '')

    roles = data.get('roles') or []
    current = next((r for r in roles if r.get('isCurrentRole')), None)
    role_functions = sorted({f for r in roles for f in (r.get('functions') or [])})

    def _clean_role(role: dict[str, Any]) -> dict[str, Any]:
        return {
            'title': (role.get('title') or '').strip(),
            'employer': (role.get('employer') or '').strip(),
            'isCurrentRole': bool(role.get('isCurrentRole')),
            'isGovernmentRole': bool(role.get('isGovernmentRole')),
            'startDate': role.get('startDate'),
            'endDate': role.get('endDate'),
            'functions': role.get('functions') or [],
        }

    return {
        'username': username,
        'profileUrl': profile_page_url(username),
        'firstName': first.strip() or None,
        'middleName': middle.strip() or None,
        'lastName': last.strip() or None,
        'fullName': _full_name(first, middle, last) or None,
        'headline': (data.get('headline') or '').strip() or None,
        'city': (address.get('city') or '').strip() or None,
        'state': (address.get('state') or '').strip() or None,
        'country': (address.get('country') or '').strip() or None,
        'linkedinUrl': linkedin_url,
        'websiteUrl': website_url,
        'email': email,
        'websites': websites,
        'clearVerified': bool(data.get('clearVerified')),
        'currentTitle': (current.get('title') or '').strip() if current else None,
        'currentEmployer': (current.get('employer') or '').strip() if current else None,
        'sectors': data.get('sectors') or [],
        'practiceAreas': data.get('practiceAreas') or [],
        'functions': role_functions,
        'roles': [_clean_role(r) for r in roles],
        'biography': biography or None,
        'education': data.get('education') or [],
        'certifications': data.get('certifications') or [],
        'languages': data.get('languages') or [],
        'memberships': data.get('memberships') or [],
        'honorsAwards': data.get('honorsAwards') or [],
        'publications': data.get('publications') or [],
        'profilePicture': _profile_picture_url(data.get('profilePicture')),
        'scrapedAt': scraped_at,
    }


def build_item_from_page(html: str, username: str, scraped_at: str) -> dict[str, Any] | None:
    """Fallback extractor: pull contact info from a profile page's Next.js flight data.

    Uses njsparser to parse the ``self.__next_f.push`` flight data, then recovers the
    contact hrefs (LinkedIn, website), name and biography text from the rendered tree.
    Returns a partial row, or ``None`` if no flight data could be parsed.
    """
    import njsparser  # imported lazily so the primary path has no hard dependency at import time

    fd = njsparser.BeautifulFD(html)
    if not fd:
        return None

    blob = json.dumps(fd, default=njsparser.default)

    # Recover the LinkedIn profile link - the one external link with an unambiguous
    # pattern. The rendered flight data also contains publication/appearance links
    # mixed with site chrome, which can't be reliably told apart from a personal
    # website, so the fallback does not guess websiteUrl (the JSON API path provides
    # the structured `websites` list).
    hrefs = sorted(set(re.findall(r'https?://[^\s"\\]+', blob)))
    linkedin_url = next((h for h in hrefs if _LINKEDIN_PROFILE_RE.search(h)), None)

    # Name + headline come from the document <title>: "First Last, Headline".
    title_match = re.search(r'<title>([^<]+)</title>', html)
    full_name = None
    headline = None
    if title_match:
        title = re.sub(r'\s*\|\s*Former Gov.*$', '', title_match.group(1)).strip()
        if ',' in title:
            full_name, headline = (part.strip() for part in title.split(',', 1))
        else:
            full_name = title

    # Exclude the site's own footer contact address from the fuzzy fallback scan.
    email = next(
        (m for m in _EMAIL_RE.findall(blob) if not m.lower().endswith('@formergov.com')),
        None,
    )

    return {
        'username': username,
        'profileUrl': profile_page_url(username),
        'fullName': full_name,
        'headline': headline,
        'linkedinUrl': linkedin_url,
        'websiteUrl': None,
        'email': email,
        'websites': [{'name': 'LinkedIn', 'url': linkedin_url}] if linkedin_url else [],
        'scrapedAt': scraped_at,
        '_partial': True,
    }
