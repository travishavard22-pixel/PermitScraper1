# Construction Permit Leads — Trade-Scored Building Permit Monitor

Get **new building permits, classified by trade and scored as sales leads**, delivered every morning.

Built for roofers, HVAC and plumbing contractors, foundation companies, restoration crews, solar installers, and the suppliers who sell to them. A permit is a signed statement that someone is about to spend money on construction. This actor finds those statements the day they are filed.

---

## Why this instead of a raw permit dump

Most permit tools hand you a spreadsheet of every record the city published and leave you to sort it. That is not a lead list, it is homework.

This actor does four things to each permit before you see it:

| | |
|---|---|
| **Classifies the trade** | Reads the work description and tags it `roofing`, `hvac`, `plumbing`, `electrical`, `foundation`, `remodel`, `new_construction`, `solar`, `pool`, `demolition`, `fence_deck`. Handles the messy real strings — `REPLACE ROOF STORM DAMAGE`, `RE-ROOF`, `ROOF OVER EXISTING` all resolve to roofing. |
| **Scores the lead 0–100** | Weighted heavily toward **freshness**, because a three-day-old permit already has four competitors calling it. Job value, whether a contractor is already attached, and address quality also factor in. |
| **Filters to your business** | Your trades, your ZIP codes, your minimum job size. |
| **Remembers what it already sent you** | Run it daily and you get *only today's new permits* — never the same row twice. |

Sort by `lead_score` descending and work top-down. That is the whole workflow.

---

## Coverage

Every city below was queried live on 2026-07-28 and confirmed to be returning **current** data. A city is not listed until it passes that check.

| City | Source | Freshness | Valuation | Contractor | Site ZIP |
|---|---|---|---|---|---|
| **Houston, TX** | Permit portal | Same/next day | Yes | — | Yes |
| **Chicago, IL** | Open data | 1 day | Yes | Yes | **No** |
| **New York City, NY** | Open data | 1 day | Yes | Yes | Yes |
| **Austin, TX** | Open data | 1 day | **Estimated** | Yes | Yes |
| **Seattle, WA** | Open data | 3 days | Yes | Sparse | Yes |
| **Los Angeles, CA** | Open data | 3 days | Yes | — | Yes |

Three caveats worth knowing before you buy:

- **Austin publishes no valuation column at all.** Every Austin row carries a trade-median estimate flagged `valuation_is_estimated: true`. Your minimum-valuation filter never discards a row on an estimated value, so you do not lose real leads to a blank field.
- **Chicago publishes no job-site ZIP.** It publishes contact ZIPs, which are the *contact's* address, not the job. Mapping those would silently mis-target your ZIP filter, so the field is left empty and Chicago rows are excluded when a ZIP filter is set.
- **Houston has no data feed whatsoever.** The City stopped publishing Permit Activity Reports on 2025-12-01 and now exposes permits only through an interactive search. That is exactly why Houston data is hard to find anywhere else.

A note on why this list is short: many well-known "open permit datasets" are frozen archives. Dallas's endpoint returns HTTP 200 and looks healthy — its newest record is from December 2019. San Francisco's building-permits feed is a contacts table with no addresses or descriptions. Neither is listed here, because a run that finds nothing is worse than a city that is missing.

## Output

One row per permit:

```json
{
  "lead_score": 78,
  "trade": "roofing",
  "issued_date": "2026-07-27",
  "valuation": 28000,
  "valuation_is_estimated": false,
  "address": "1234 Beechnut St, Houston TX 77072",
  "zip": "77072",
  "work_description": "REPLACE ROOF STORM DAMAGE",
  "permit_type": "Building Permit",
  "permit_number": "26-0891234",
  "contractor_name": "",
  "owner_name": "SMITH JOHN",
  "source_city": "houston_tx",
  "source_url": "https://www.houstonpermittingcenter.org/sold-permits-search",
  "scraped_at": "2026-07-27T13:04:11Z"
}
```

Export to CSV or Excel, or push straight into your CRM via the Apify API.

---

## Reading `lead_score`

| Score | Meaning |
|---|---|
| **75–100** | Call today. Fresh, real money, often no contractor attached yet. |
| **50–74** | Worth a call or a mailer this week. |
| **25–49** | Marketing list, not a phone list. |
| **0–24** | Old, tiny, or missing an address. |

**`contractor_name` is empty** means the homeowner pulled the permit themselves and has not hired anyone. If you sell to homeowners, these are your best rows and the score reflects that. If you sell *to contractors*, flip on **Only permits with a named contractor** to invert it.

---

## Recommended setup

**Roofing contractor in southwest Houston:**
```
cities:            houston_tx
lookbackDays:      2
trades:            roofing
minValuation:      8000
zipWhitelist:      77072, 77036, 77081, 77074, 77099
requireContractor: false
onlyNew:           true
```

Then **Schedule** it daily at 6:00 AM. You get that morning's new roofing permits in your inbox before you leave the yard.

---

## Pricing

Pay per event. You are charged for **new leads actually delivered** — not for rows scraped, not for duplicates you already received, not for permits your filters excluded.

**Worked example.** A Houston roofer running daily with the setup above typically sees 8–25 qualifying new roofing permits per day. At the per-lead event price shown on this listing, a full month of daily runs bills for roughly 240–750 delivered leads. If your average roof job is $14,000, the arithmetic is not close.

Set a **max cost per run** in the Console and the actor stops cleanly when it reaches your cap — it will never quietly overrun your budget.

---

## FAQ

**Where does this data come from?**
Public municipal permit records. Building permits are public records in Texas by statute.

**Is this legal?**
Yes. The actor reads only public, logged-out pages and never creates an account, never logs in, and never bypasses any access control. It also rate-limits itself so it does not burden city servers.

**Why is Houston slower than Austin?**
Austin is a direct data feed. Houston has no feed at all — the actor has to drive the city's search portal in a real browser. Expect a Houston run to take a few minutes. That is also why Houston data is hard to get anywhere else.

**Some valuations look like round numbers.**
Those are estimates, and they are marked `valuation_is_estimated: true`. Austin publishes no valuation field, and many cities leave it blank on trade permits. Rather than drop those leads or show `$0`, the actor fills in a trade median. Your minimum-valuation filter never discards a row on an estimated value, so you do not lose real leads to a blank field.

**A permit is classified under the wrong trade.**
Send the exact `work_description` and the correct trade. Classification is rule-based and deterministic, so a reported miss becomes a fix in the next release rather than a shrug.

**Can you add my city?**
Ask. Cities with a live open-data feed can be added quickly. Cities with only a search portal take longer but are usually the ones worth the most, because nobody else bothers.
