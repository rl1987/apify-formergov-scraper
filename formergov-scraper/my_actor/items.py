"""Scrapy item model for a scraped Former Gov directory profile.

For detailed information on creating and utilizing items, refer to the official documentation:
https://docs.scrapy.org/en/latest/topics/items.html
"""

from __future__ import annotations

from scrapy import Field, Item


class ProfileItem(Item):
    """A single former government / military professional from the directory."""

    # Identity
    username = Field()
    profileUrl = Field()
    firstName = Field()
    middleName = Field()
    lastName = Field()
    fullName = Field()
    headline = Field()

    # Location
    city = Field()
    state = Field()
    country = Field()

    # Contact info (the headline output of this Actor)
    linkedinUrl = Field()
    websiteUrl = Field()
    email = Field()
    websites = Field()  # full list of {name, url}

    # Professional context
    clearVerified = Field()
    currentTitle = Field()
    currentEmployer = Field()
    sectors = Field()
    practiceAreas = Field()
    functions = Field()
    roles = Field()  # structured employment history

    # Rich detail
    biography = Field()
    education = Field()
    certifications = Field()
    languages = Field()
    memberships = Field()
    honorsAwards = Field()
    publications = Field()
    profilePicture = Field()

    # Provenance
    scrapedAt = Field()
