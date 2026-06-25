"""Item pipelines for the Former Gov scraper.

For detailed information on creating and utilizing item pipelines, refer to the official documentation:
http://doc.scrapy.org/en/latest/topics/item-pipeline.html
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from scrapy.exceptions import DropItem

if TYPE_CHECKING:
    from scrapy import Spider

    from .items import ProfileItem


class ProfileDedupPipeline:
    """Drop duplicate profiles (the same username can surface across search pages)."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def process_item(self, item: ProfileItem, spider: Spider) -> ProfileItem:
        username = item.get('username')
        if username and username in self._seen:
            raise DropItem(f'Duplicate profile: {username}')
        if username:
            self._seen.add(username)
        return item
