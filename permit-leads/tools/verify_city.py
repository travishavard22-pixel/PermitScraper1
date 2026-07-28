#!/usr/bin/env python3
"""Verify a candidate Socrata permit dataset before adding it to CITY_REGISTRY.

Adding cities is how this actor grows revenue. Adding a BROKEN city is how it
churns customers. Run this first, every time.

Usage:
    python tools/verify_city.py data.austintexas.gov 3syk-w9eu

It reports:
    - whether the endpoint resolves
    - the real column names (so you can write field_map correctly)
    - the freshness of the newest record  <-- the check that matters most
    - a suggested field_map you can paste into base.py

A dataset whose newest row is older than ~7 days is an archive, not a feed.
Do not ship it.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import httpx
from dateutil import parser as dateparser

DATE_CANDIDATES = (
    "issue_date", "issued_date", "date_issued", "issueddate",
    "permit_issue_date", "issue_dt", "applied_date", "application_date",
)

FIELD_HINTS: dict[str, tuple[str, ...]] = {
    "permit_number": ("permit_number", "permit_num", "permitnumber", "record_number", "permit_id"),
    "address": ("original_address1", "street_address", "address", "site_address", "project_address", "location"),
    "zip": ("original_zip", "zip_code", "zipcode", "zip", "postal_code"),
    "work_description": ("description", "work_description", "project_description", "scope_of_work"),
    "permit_type": ("permit_type_desc", "permit_type", "permittype", "type", "work_class"),
    "valuation": ("total_job_valuation", "estimated_cost", "declared_value", "valuation", "value", "job_value", "construction_cost"),
    "contractor_name": ("contractor_company_name", "contractor_name", "contractor", "company_name"),
    "owner_name": ("owner_name", "owner", "applicant_name", "applicant"),
}


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    domain, dataset_id = sys.argv[1], sys.argv[2]
    base = f"https://{domain}/resource/{dataset_id}.json"

    print(f"\n>> {base}\n")

    # Sample MANY rows, not one. Socrata omits null fields per row, so a
    # single sparse row hides columns that actually exist. This bug once made
    # NYC look like it had no issue-date column at all -- it reported 36
    # columns from one row versus 41 when unioned across 200.
    try:
        resp = httpx.get(base, params={"$limit": 200}, timeout=45, follow_redirects=True)
    except Exception as exc:
        print(f"FAIL  cannot reach host: {type(exc).__name__}: {exc}")
        return 1

    if resp.status_code != 200:
        print(f"FAIL  HTTP {resp.status_code} — dataset id is probably wrong.")
        print(f"      Browse https://{domain}/browse?q=building+permits")
        return 1

    try:
        rows = resp.json()
    except Exception:
        print("FAIL  response was not JSON — this host is not a Socrata portal.")
        return 1

    if not rows:
        print("FAIL  dataset is empty.")
        return 1

    columns_set: set[str] = set()
    for row in rows:
        columns_set |= set(row.keys())
    columns = sorted(columns_set)
    print(f"OK    endpoint live, {len(columns)} columns (unioned over {len(rows)} rows)\n")
    print("COLUMNS")
    for col in columns:
        sample = next((r[col] for r in rows if r.get(col) not in (None, "")), "")
        print(f"  {col:34} = {str(sample)[:44]}")

    date_field = next((c for c in DATE_CANDIDATES if c in columns), None)
    if not date_field:
        print("\nWARN  no obvious issue-date column. Pick one manually from above.")
        return 1

    # Exclude NULL dates or they sort first under DESC and fake a bad result.
    fresh = httpx.get(
        base,
        params={
            "$where": f"{date_field} IS NOT NULL",
            "$order": f"{date_field} DESC",
            "$limit": 1,
        },
        timeout=45,
        follow_redirects=True,
    )
    newest_raw = fresh.json()[0].get(date_field)
    try:
        newest = dateparser.parse(str(newest_raw))
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - newest).days
    except Exception:
        print(f"\nWARN  could not parse newest date '{newest_raw}'.")
        print("      Non-ISO dates also break Socrata's $where filter. Likely unusable.")
        return 1

    print(f"\nFRESHNESS  date_field='{date_field}'  newest={newest.date()}  age={age_days}d")
    if age_days > 7:
        print("  VERDICT: ARCHIVE — do NOT add. Customers would pay for zero leads.")
        return 1
    print("  VERDICT: LIVE — safe to add.")

    print("\nSUGGESTED field_map (paste into CITY_REGISTRY, verify by eye):")
    print('        field_map={')
    print(f'            "issued_date": "{date_field}",')
    for out_key, hints in FIELD_HINTS.items():
        match = next((h for h in hints if h in columns), None)
        if match:
            print(f'            "{out_key}": "{match}",')
        else:
            print(f'            # "{out_key}": NOT FOUND — city does not publish it')
    print('        },')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
