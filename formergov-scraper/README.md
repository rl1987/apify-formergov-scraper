# Former Gov Directory Scraper

**Extract former government and military professionals from the [Former Gov](https://formergov.com) directory — with their contact info (LinkedIn profile, website, and email when public) and full professional background.** Mirror the site's advanced search with structured filters, or pull specific profiles by username. Built with Scrapy on the Apify platform, so you get scheduling, a versioned API, integrations, proxy rotation, and run monitoring out of the box.

## What does Former Gov Directory Scraper do?

[Former Gov](https://formergov.com) is an apolitical directory that connects former federal, state, local, tribal, and foreign government and military professionals with people who need their expertise. This Actor turns that directory into structured data: for every matching person it returns their name, headline, location, employment history, sectors, practice areas, and — most importantly — their **contact points**: LinkedIn profile URL, personal/employer website, and email address when it appears in their public profile.

It talks directly to Former Gov's public JSON API, so it is fast and reliable. The same advanced-search filters available on the website (keyword, practice areas, sectors, functions, jurisdiction, position type, agency, location, and more) are exposed as Actor input, so you can target exactly the people you care about.

## Why use Former Gov Directory Scraper?

- **Lead generation & business development** — build targeted lists of former officials by practice area, sector, or agency.
- **Recruiting & executive search** — find candidates with specific government or military backgrounds.
- **Expert sourcing** — locate expert witnesses, board advisors, speakers, or media commentators.
- **Research & due diligence** — map the post-government careers of professionals in a field.
- **CRM enrichment** — append LinkedIn, website, and email contact points to existing records.

## How to use Former Gov Directory Scraper

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
| `useNjsparserFallback` | boolean | If a profile's JSON API call fails, parse the Next.js page with `njsparser` to recover contact info. |
| `proxyConfiguration` | object | Proxy settings (Apify Proxy datacenter is sufficient). |

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
  "email": null,
  "websites": [
    { "name": "LinkedIn", "url": "https://www.linkedin.com/in/brian-levine-cyberlaw" },
    { "name": "Employer", "url": "https://www.ey.com/en_us/people/brian-levine" }
  ],
  "clearVerified": true,
  "currentTitle": "Founder and Executive Director",
  "currentEmployer": "Former Gov",
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
| `email` | Email address, when present in the public profile text. |
| `websites` | Full list of `{name, url}` links on the profile. |
| `clearVerified` | Whether the member is identity-verified via CLEAR. |
| `currentTitle`, `currentEmployer` | Current role. |
| `sectors`, `practiceAreas`, `functions` | Expertise tags. |
| `roles` | Structured employment history. |
| `biography`, `education`, `certifications`, `languages`, `memberships`, `honorsAwards`, `publications` | Rich profile detail. |
| `profilePicture` | Profile image URL. |
| `scrapedAt` | ISO timestamp of extraction. |

## How much does it cost to scrape Former Gov?

The Actor uses lightweight HTTP requests (no headless browser), so it is inexpensive: roughly two requests per profile (one search page covers many profiles, plus one request per profile). Scraping a few hundred profiles typically costs a small fraction of a compute unit. Use `maxItems` to cap spend on large runs. Costs depend on your Apify plan and proxy usage.

## Tips and advanced options

- **Whole directory**: leave all filters empty and set `searchType` to `current` or `former`.
- **Combined searches**: set `searchType` to `combined` and use `combinedFilters` for the second role leg (e.g. current law-firm role + former federal role).
- **Future-proofing**: any filter not exposed in the form can be passed through `extraSearchParams`.
- **Deep pagination**: the search API windows very large result sets, so for the most complete coverage split big pulls into narrower filtered queries rather than one unbounded run.

## FAQ, disclaimers, and support

**Is scraping Former Gov legal?** This Actor collects only publicly available profile information that Former Gov members choose to publish in the public directory. You are responsible for using the data in compliance with Former Gov's Terms of Service, applicable laws (including data-protection rules such as GDPR/CCPA), and for respecting individuals' privacy. Do not use the data for spam or any unlawful purpose.

**Email availability** — Former Gov has no dedicated email field; an email is only returned when a member includes one in their public biography text, so most rows will have `email: null`.

**Found a bug or need a custom field?** Open an issue on the Actor's **Issues** tab. Custom scraping solutions can be arranged on request.
