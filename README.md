# apify-formergov-scraper

An Apify Actor (built with Scrapy) that scrapes the [Former Gov](https://formergov.com)
directory of former government and military professionals, extracting person details and
contact info (LinkedIn profile URL, website URL, and email when public). It mirrors the
site's advanced search via structured input filters.

The Actor lives in [`formergov-scraper/`](./formergov-scraper). See its
[README](./formergov-scraper/README.md) for input/output documentation, and
[`AGENTS.md`](./formergov-scraper/AGENTS.md) for development guidance.

## Quick start

```bash
cd formergov-scraper
apify run            # run locally using storage/key_value_stores/default/INPUT.json
apify push           # deploy to the Apify platform
```

## How it works

- **Search** — calls the public Former Gov JSON API (`cdn.formergov.com/api`) directory
  search endpoint with the requested filters and paginates through results.
- **Profiles** — fetches each matching profile's structured JSON and maps it to a flat
  output row (name, location, roles, sectors, practice areas, and contact links).
- **Fallback** — if a profile's JSON API call fails, the Next.js page flight data is
  parsed with [`njsparser`](https://pypi.org/project/njsparser/) to recover contact info.

No login is required; only public directory data is collected.
