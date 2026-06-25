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

1. On [formergov.com/directory](https://formergov.com/directory), use the advanced search to pick the filters you want (search type, practice areas, sectors, jurisdiction, …). The page updates the URL as you go.
2. Copy the URL from your browser's address bar.
3. Open the Actor in Apify Console, go to the **Input** tab, and paste that URL into **FormerGov URLs**.
4. (Optional) Set **Max profiles** to cap the run, and configure **Proxy**.
5. Click **Start**. When the run finishes, open the **Output** tab and export the data as JSON, CSV, Excel, or HTML.

> Tip: paste `https://formergov.com` (or a bare `https://formergov.com/directory`) to scrape the **entire directory**, or paste individual profile URLs like `https://formergov.com/directory/brianlevine` to scrape just those people.

## Input

The Actor takes a single field: a list of FormerGov URLs copied straight from your browser. No need to look up internal ids — the site already encodes every filter in the URL.

| Field | Type | Description |
|---|---|---|
| `startUrls` | request list | FormerGov URLs to scrape. Each URL is one of: a **directory search URL** (e.g. `https://formergov.com/directory?type=combined&sectors=<uuid>`) whose filters drive the search; the **home page** `https://formergov.com` (or a bare `/directory`) to scrape the whole directory; or an **individual profile URL** `https://formergov.com/directory/<username>`. `page`/`pageSize` in the URL are ignored — the Actor paginates for you. |
| `maxItems` | integer | Max profiles to scrape (0 = no limit). |
| `proxyConfiguration` | object | Proxy settings. Defaults to Apify Proxy (datacenter). If you see persistent `HTTP 403`, switch to **residential** groups. |

If any individual profile URLs are present, only those are scraped (the search URL is ignored).

### Example input

```json
{
  "startUrls": [
    { "url": "https://formergov.com/directory?type=combined&practiceAreas=1e01b018-bd8a-4c1b-857d-3f93d613935a&sectors=e97b9d61-7fd5-4565-8ca5-8f4d5a9dd56e" }
  ],
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

This Actor is **pay per result**: you are charged **US$0.0015 per profile** delivered to the dataset (US$1.50 per 1,000 profiles). You only pay for unique profiles actually returned — duplicates, not-found (404) profiles, and failed requests are not billed. For example, scraping the entire directory (~2,500 profiles) costs about **US$3.75**. Set **Max profiles** (`maxItems`) to cap how many rows a run can produce, and set your run's max-charge limit in the Console to stay within budget.

## Tips and advanced options

- **Whole directory**: paste `https://formergov.com` (or a bare `https://formergov.com/directory`) — an unfiltered `type=combined` search that covers every profile (~2,500).
- **Any filter**: anything the site's advanced search can express ends up in the URL (search type, practice areas, sectors, functions, jurisdiction, agency, combined second-leg filters, free text, …), so it works automatically — just build the search on formergov.com and copy the URL.
- **Search result ceiling**: a single search returns at most **10,000** results (the backend's pagination window). The full directory is well under that, but for a filtered search exceeding 10,000, split it into narrower queries (e.g. by jurisdiction, sector, or state) to reach everything. The Actor logs a warning when a search exceeds this ceiling.
- **Missing & blocked profiles**: some directory usernames have no public profile (HTTP 404) and are skipped. Profiles blocked by a temporary IP block (HTTP 403) are automatically retried at the end of the run, so transient blocks don't drop them. The run's final log line reports how many were scraped, not-found, and permanently failed.
- **403 errors**: FormerGov has anti-bot protection that primarily blocks non-browser requests, which the Actor handles by sending browser headers, pacing requests (AutoThrottle), and retrying blocked requests on a fresh IP. Some datacenter IPs can also be rejected by reputation; if you see **persistent** `HTTP 403` warnings, switch the proxy to **residential** groups in the input.

## FAQ, disclaimers, and support

**Is scraping FormerGov legal?** This Actor collects only publicly available profile information that FormerGov members choose to publish in the public directory. You are responsible for using the data in compliance with FormerGov's Terms of Service, applicable laws (including data-protection rules such as GDPR/CCPA), and for respecting individuals' privacy. Do not use the data for spam or any unlawful purpose.

**Found a bug or need a custom field?** Open an issue on the Actor's **Issues** tab. Custom scraping solutions can be arranged on request.
