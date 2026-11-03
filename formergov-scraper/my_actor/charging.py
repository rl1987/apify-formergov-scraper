"""What this Actor bills, and the three gates that run before any charge.

Kept apart from ``pipelines.py`` so the billing contract can be read, and tested,
without pulling Scrapy in - and so there is exactly one place that names the event.

One billable row is one former government or military professional's directory profile.
Search mode (paginating ``/data/profiles``) and direct-profile mode (fetching one
username) produce the identical row, so there is one row kind and one event.
"""

from __future__ import annotations

import os
from typing import Any

#: The only row kind this Actor emits.
KIND_PROFILE = 'profile'

#: Pay-per-event id. The ledger resolves it against the live pricing config at run start,
#: so a build can never charge a name the platform does not price.
PROFILE_EVENT = 'profile-row'

CHARGE_EVENTS = {KIND_PROFILE: PROFILE_EVENT}

#: Where the row contract lives. The ledger validates every row against it BEFORE the
#: charge, because the platform rejects the whole item on one bad member - so with
#: charging ahead of the write a type mismatch would be a bill with no delivery.
DATASET_SCHEMA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.actor', 'dataset_schema.json',
)


def row_key(kind: str, row: dict[str, Any]) -> Any:
    """What makes a row the same row.

    The same username surfaces across search pages, and the end-of-run requeue of
    blocked profiles can re-fetch one that eventually succeeded, so a duplicate has to be
    dropped BEFORE it can be charged.
    """
    return row.get('username')


def row_resolved(kind: str, row: dict[str, Any]) -> bool:
    """A row that resolved nothing is not a cheap row, it is not a row.

    The spider already refuses an empty or non-dict profile document, but a 2xx with a
    JSON object that happens to carry no name at all would still build a full-shaped
    record off ``or {}`` defaults. Billing that is the yahoo-sports defect.
    """
    return bool(row.get('username')) and bool(row.get('fullName') or row.get('lastName'))
