# FormerGov Directory Scraper

**Extract former government and military professionals from the [FormerGov](https://formergov.com) directory — with their published contact links (LinkedIn profile and firm/personal website) and full professional background.** There is no email address anywhere in this data: FormerGov does not publish one, so a row gives you a LinkedIn profile and a firm page to resolve from, not an inbox. Mirror the site's advanced search with structured filters, or pull specific profiles by username. Built with Scrapy on the Apify platform, so you get scheduling, a versioned API, integrations, proxy rotation, and run monitoring out of the box.

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

1. On [formergov.com/directory](https://formergov.com/directory), enter a keyword and/or use **Advanced Search** to pick the filters you want (search type, practice areas, sectors, jurisdiction, …), then click **SEARCH**. The address bar now holds your search as a URL (e.g. `…/directory?type=combined&text=OSINT`).
2. Copy that URL from your browser's address bar.
3. Open the Actor in Apify Console, go to the **Input** tab, and paste that URL into **FormerGov URLs**.
4. (Optional) Set **Max profiles** to cap the run, and configure **Proxy**.
5. Click **Start**. When the run finishes, open the **Output** tab and export the data as JSON, CSV, Excel, or HTML.

> Tip: paste `https://formergov.com` (or a bare `https://formergov.com/directory`) to scrape the **entire directory**, or paste individual profile URLs like `https://formergov.com/directory/brianlevine` to scrape just those people.

## Input

The Actor takes a single field: a list of FormerGov URLs copied straight from your browser. No need to look up internal ids — the site already encodes every filter in the URL.

| Field | Type | Description |
|---|---|---|
| `startUrls` | request list | FormerGov URLs to scrape. Each URL is one of: a **directory search URL** (e.g. `https://formergov.com/directory?type=combined&sectors=<uuid>`) whose filters drive the search; the **home page** `https://formergov.com` (or a bare `/directory`) to scrape the whole directory; or an **individual profile URL** `https://formergov.com/directory/<username>`. `page`/`pageSize` in the URL are ignored — the Actor paginates for you. |
| `maxItems` | integer | Max profiles to deliver (0 = no limit). |
| `maxItems` note | — | Counts profiles **delivered**: duplicates and profiles that resolved nothing never count against it. |
| `proxyConfiguration` | object | Proxy settings. **Defaults to Apify Proxy with the RESIDENTIAL group, which this target requires** — see "403 errors" below. |

If any individual profile URLs are present, only those are scraped (the search URL is ignored).

### Example input

```json
{
  "startUrls": [
    { "url": "https://formergov.com/directory?type=combined&practiceAreas=d8ac7065-59f2-4724-889e-b1af7299bd47&sectors=7738e4e4-87f6-4f8d-978b-954959c7d0ff" }
  ],
  "maxItems": 200,
  "proxyConfiguration": { "useApifyProxy": true, "apifyProxyGroups": ["RESIDENTIAL"] }
}
```

That example is practice area *National Security* in the *Government Services* sector —
36 profiles as of 2026-11-03. The site **ANDs** its filters, so an unusual
practice-area + sector pair can legitimately match nobody; the run then finishes with a
status message saying the search matched no one rather than an unexplained empty dataset.

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
| `linkedinUrl` | LinkedIn profile URL (contact). **Conditional**: present on 25 of 40 whole-directory profiles sampled on 2026-11-03 (62.5%) — members choose which links to publish. |
| `websiteUrl` | Primary non-LinkedIn website, usually the person's firm page (contact). Conditional: 32 of 40 (80%). |
| `websites` | Full list of `{name, url}` links on the profile. Conditional: 32 of 40 (80%). |
| `clearVerified` | Whether the member is identity-verified via CLEAR. |
| `currentTitle`, `currentEmployer` | Current role, from the `roles` entry flagged `isCurrentRole`. Conditional: 33 of 40 (82.5%) — a member with no current role listed has both `null`. |
| `sectors`, `practiceAreas`, `functions` | Expertise tags. |
| `roles` | Structured employment history. |
| `biography`, `education`, `certifications`, `languages`, `memberships`, `honorsAwards`, `publications` | Rich profile detail, all conditional on what the member filled in. Coverage on the same 40-profile sample: biography 85%, education 85%, languages 85%, memberships 78%, publications 65%, honoursAwards 45%, certifications 15%. |
| `profilePicture` | Profile image URL. |
| `scrapedAt` | ISO timestamp of extraction. |

## How much does it cost to scrape FormerGov?

The current price of every event is on the **Pricing** tab of this Actor's page in Apify Store. That tab is generated from the Actor's own pricing record, so it is always current, and your plan's rung is applied automatically. A figure repeated here would be a second copy that drifts out of date.

You can cap what any single run may spend with **Max total charge**.

## Tips and advanced options

- **Whole directory**: paste `https://formergov.com` (or a bare `https://formergov.com/directory`) — an unfiltered `type=combined` search that covers every profile (~2,500).
- **Any filter**: anything the site's advanced search can express ends up in the URL (search type, practice areas, sectors, functions, jurisdiction, agency, combined second-leg filters, free text, …), so it works automatically — just build the search on formergov.com and copy the URL.
- **Search result ceiling**: a single search returns at most **10,000** results (the backend's pagination window). The full directory is well under that, but for a filtered search exceeding 10,000, split it into narrower queries (e.g. by jurisdiction, sector, or state) to reach everything. The Actor logs a warning when a search exceeds this ceiling.
- **Missing & blocked profiles**: some directory usernames have no public profile (HTTP 404) and are skipped. Profiles blocked by a temporary IP block (HTTP 403) are automatically retried at the end of the run, so transient blocks don't drop them. The run's final log line reports how many were scraped, not-found, and permanently failed.
- **403 errors — use the RESIDENTIAL proxy group, which is now the default.** FormerGov's
  CDN rejects Apify's datacenter IPs by reputation. Measured 2026-11-03: on datacenter,
  the **profile** endpoint — where every delivered row comes from — answered `HTTP 403`
  on 3 of 3 attempts, while the search endpoint answered 403 on only 1 of 3; a platform
  run the same day took 403 on 27 of 27 requests and delivered 0 rows. The same requests
  on RESIDENTIAL answered `HTTP 200` on 6 of 6. That split is why a datacenter run could
  look like it was working, list usernames, and still deliver nothing. The Actor now
  defaults (and prefills) the RESIDENTIAL group, warns at the start of a run if it is
  given a datacenter configuration anyway, and **fails** a run that collected nothing
  because it was blocked rather than finishing SUCCEEDED with an empty dataset. The
  anti-bot layer also blocks non-browser requests, which the Actor handles with browser
  headers, pacing (AutoThrottle), and retrying on a fresh IP.
- **How a run that returns nothing explains itself.** There are four ways to get an empty
  dataset and they are no longer interchangeable: no usable URL in `startUrls` (SUCCEEDED,
  with the reason), a search that matched nobody (SUCCEEDED, saying nothing was blocked),
  a run blocked by the site (**FAILED**, naming the proxy fix), and a run cut short by
  **Max total charge** (SUCCEEDED, saying how many rows it stopped at). Only profiles
  actually delivered are ever charged.

## Did you find this useful?

Rate this actor on Apify! Your feedback helps other users find it and helps us keep improving it.

## FAQ, disclaimers, and support

**Is scraping FormerGov legal?** This Actor collects only publicly available profile information that FormerGov members choose to publish in the public directory. You are responsible for using the data in compliance with FormerGov's Terms of Service, applicable laws (including data-protection rules such as GDPR/CCPA), and for respecting individuals' privacy. Do not use the data for spam or any unlawful purpose.

**Found a bug or need a custom field?** Open an issue on the Actor's **Issues** tab. Custom scraping solutions can be arranged on request.
