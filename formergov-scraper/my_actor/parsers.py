"""Parse Former Gov profile data into output rows.

Profiles are read from the clean profile JSON returned by ``cdn.formergov.com/api``.
"""

from __future__ import annotations

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
