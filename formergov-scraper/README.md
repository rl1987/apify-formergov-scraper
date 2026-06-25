# FormerGov Directory Scraper

**Extract former government and military professionals from the [FormerGov](https://formergov.com) directory — with their contact info (LinkedIn profile and website) and full professional background.** Mirror the site's advanced search with structured filters, or pull specific profiles by username. Built with Scrapy on the Apify platform, so you get scheduling, a versioned API, integrations, proxy rotation, and run monitoring out of the box.

## What does FormerGov Directory Scraper do?

[FormerGov](https://formergov.com) is an apolitical directory that connects former federal, state, local, tribal, and foreign government and military professionals with people who need their expertise. This Actor turns that directory into structured data: for every matching person it returns their name, headline, location, employment history, sectors, practice areas, and — most importantly — their **contact points**: LinkedIn profile URL and personal/employer website.

It talks directly to FormerGov's public JSON API, so it is fast and reliable. The same advanced-search filters available on the website (keyword, practice areas, sectors, functions, jurisdiction, position type, agency, location, and more) are exposed as Actor input, so you can target exactly the people you care about.

## Why use FormerGov Directory Scraper?

- **Lead generation & business development** — build targeted lists of former officials by practice area, sector, or agency.
- **Recruiting & executive search** — find candidates with specific government or military backgrounds.
- **Expert sourcing** — locate expert witnesses, board advisors, speakers, or media commentators.
- **Research & due diligence** — map the post-government careers of professionals in a field.
- **CRM enrichment** — append LinkedIn and website contact points to existing records.

## How to use FormerGov Directory Scraper

1. Open the Actor in Apify Console and go to the **Input** tab.
2. Pick a **Search type** (Combined, Current, or Former roles) and add any filters you want — e.g. set **Practice areas** to `Cybersecurity` and **Jurisdiction** to `Federal`. Leave filters empty to scrape the whole directory.
3. (Optional) Set **Max profiles** to cap the run, and configure **Proxy**.
4. Click **Start**. When the run finishes, open the **Output** tab and export the data as JSON, CSV, Excel, or HTML.

> Tip: to scrape only specific people, skip the filters and put their usernames (or profile URLs) in **Specific usernames** / **Profile URLs**.

## Input

Configure the run on the Actor's **Input** tab or via the API. All search filters are optional and map 1:1 to the site's advanced search.

| Field | Type | Description |
|---|---|---|
| `searchType` | select | `combined`, `current`, or `former` (default `combined`). |
| `scrapeEntireDirectory` | boolean | Scrape every profile, ignoring filters, usernames, and the `maxItems` cap. |
| `text` | string | Free-text keyword search. |
| `practiceAreas` | string list | Practice-area names (e.g. `Cybersecurity`, `Corporate Law`). |
| `sectors` | string list | Sector names (e.g. `Legal`, `Technology`). |
| `functions` | string list | Function/role-category names. |
| `employer` | string | Employer name. |
| `jurisdiction` | select | `FEDERAL`, `STATE`, `LOCAL`, `FOREIGN`. |
| `positionType` | select | `APPOINTED`, `ELECTED`, `CIVIL_SERVICE`, `MILITARY`. |
| `agency` | string | Agency name (set `jurisdiction` too so names resolve) or id. |
| `district` | string | District, where applicable. |
| `isGovernment` | select | Restrict to government roles (Any/Yes/No). |
| `hasNoCurrentRoles` | select | Restrict to people with no current role (Any/Yes/No). |
| `city` / `state` / `country` | string | Location filters. |
| `openTo` | string | What the person is open to (board work, speaking, …). |
| `combinedFilters` | object | Second-leg filters for a Combined search (advanced). |
| `extraSearchParams` | object | Raw query params merged verbatim (escape hatch). |
| `profileUsernames` | string list | Scrape these usernames directly, skipping search. |
| `startUrls` | request list | Profile page URLs to scrape directly. |
| `maxItems` | integer | Max profiles to scrape (0 = no limit). |
| `pageSize` | integer | Results per search page (1–1000). |
| `proxyConfiguration` | object | Proxy settings. Defaults to Apify Proxy (datacenter). If you see persistent `HTTP 403`, switch to **residential** groups. |

Names for `practiceAreas`, `sectors`, `functions`, and `agency` are resolved to the directory's internal ids automatically; unrecognized names are skipped with a warning. You may also pass raw UUIDs.

### Example input

```json
{
  "searchType": "former",
  "jurisdiction": "FEDERAL",
  "practiceAreas": ["Cybersecurity"],
  "text": "privacy",
  "maxItems": 200
}
```

## Output

Each dataset item is one person. You can download the dataset in various formats such as JSON, HTML, CSV, or Excel.

```json
{
  "username": "brianlevine",
  "profileUrl": "https://formergov.com/directory/brianlevine",
  "fullName": "Brian L Levine",
  "firstName": "Brian",
  "lastName": "Levine",
  "headline": "Leading at the intersection of law and technology",
  "city": "Washington",
  "state": "District of Columbia",
  "country": "United States",
  "linkedinUrl": "https://www.linkedin.com/in/brian-levine-cyberlaw",
  "websiteUrl": "https://www.ey.com/en_us/people/brian-levine",
  "websites": [
    { "name": "LinkedIn", "url": "https://www.linkedin.com/in/brian-levine-cyberlaw" },
    { "name": "Employer", "url": "https://www.ey.com/en_us/people/brian-levine" }
  ],
  "clearVerified": true,
  "currentTitle": "Founder and Executive Director",
  "currentEmployer": "FormerGov",
  "sectors": ["Consulting Services", "Public Services", "Legal", "Technology"],
  "practiceAreas": ["Artificial Intelligence", "Cybersecurity", "Privacy / Data Privacy"],
  "functions": ["Cybersecurity", "Data Privacy", "Technology"],
  "roles": [{ "title": "Managing Director", "employer": "EY Parthenon", "isCurrentRole": false, "isGovernmentRole": false }],
  "scrapedAt": "2026-06-25T12:00:00+00:00"
}
```

### Data fields

| Field | Description |
|---|---|
| `username`, `profileUrl` | Directory handle and public profile link. |
| `firstName`, `middleName`, `lastName`, `fullName` | Person's name. |
| `headline` | Professional headline. |
| `city`, `state`, `country` | Location. |
| `linkedinUrl` | LinkedIn profile URL (contact). |
| `websiteUrl` | Primary non-LinkedIn website (contact). |
| `websites` | Full list of `{name, url}` links on the profile. |
| `clearVerified` | Whether the member is identity-verified via CLEAR. |
| `currentTitle`, `currentEmployer` | Current role. |
| `sectors`, `practiceAreas`, `functions` | Expertise tags. |
| `roles` | Structured employment history. |
| `biography`, `education`, `certifications`, `languages`, `memberships`, `honorsAwards`, `publications` | Rich profile detail. |
| `profilePicture` | Profile image URL. |
| `scrapedAt` | ISO timestamp of extraction. |

## How much does it cost to scrape FormerGov?

This Actor is **pay per result**: you are charged **US$1.50 per profile** delivered to the dataset (US$1,500 per 1,000 profiles). You only pay for unique profiles actually returned — duplicates, not-found (404) profiles, and failed requests are not billed. Set **Max profiles** (`maxItems`) to cap how many rows — and therefore how much — a run can produce, and set your run's max-charge limit in the Console to stay within budget.

## Tips and advanced options

- **Whole directory**: enable **Scrape entire directory** (it ignores filters and the Max profiles cap, and uses the maximum page size). `searchType` = `combined` covers every profile.
- **Combined searches**: set `searchType` to `combined` and use `combinedFilters` for the second role leg (e.g. current law-firm role + former federal role).
- **Future-proofing**: any filter not exposed in the form can be passed through `extraSearchParams`.
- **Search result ceiling**: a single search returns at most **10,000** results (the backend's pagination window). The full directory is well under that, but for a filtered search exceeding 10,000, split it into narrower queries (e.g. by jurisdiction, sector, or state) to reach everything. The Actor logs a warning when a search exceeds this ceiling.
- **Missing & blocked profiles**: some directory usernames have no public profile (HTTP 404) and are skipped. Profiles blocked by a temporary IP block (HTTP 403) are automatically retried at the end of the run, so transient blocks don't drop them. The run's final log line reports how many were scraped, not-found, and permanently failed.
- **403 errors**: FormerGov has anti-bot protection that primarily blocks non-browser requests, which the Actor handles by sending browser headers, pacing requests (AutoThrottle), and retrying blocked requests on a fresh IP. Some datacenter IPs can also be rejected by reputation; if you see **persistent** `HTTP 403` warnings, switch the proxy to **residential** groups in the input.

## FAQ, disclaimers, and support

**Is scraping FormerGov legal?** This Actor collects only publicly available profile information that FormerGov members choose to publish in the public directory. You are responsible for using the data in compliance with FormerGov's Terms of Service, applicable laws (including data-protection rules such as GDPR/CCPA), and for respecting individuals' privacy. Do not use the data for spam or any unlawful purpose.

**Found a bug or need a custom field?** Open an issue on the Actor's **Issues** tab. Custom scraping solutions can be arranged on request.
