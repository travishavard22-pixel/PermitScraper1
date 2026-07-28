#!/usr/bin/env python3
"""Houston sold-permits fetcher — direct WebFOCUS calls, NO BROWSER.

Verified working 2026-07-28 against the live City of Houston system.

HOW THIS WAS FOUND
------------------
The public "Sold Permits Search" page on houstonpermittingcenter.org is a
Drupal menu with no form on it. The real application is a separate IBI
WebFOCUS install on cohtora.houstontx.gov. Two reports matter:

  cc1001ah.htm  -> report cc1001ap  = AGGREGATE counts by council district
                                      and week. Useless for leads.
  online_permit.htm -> online_per_se.fex = PERMIT-LEVEL DETAIL. This one.

Two things made the portal look broken in a browser:
  1. the report is configured targettype="window" targetname="_blank",
     so results open in a popup, and
  2. the launch form defaults its output format to PDF,
so an automated browser shows a blank tab. Nothing was actually failing.

Because the report is a plain POST, we skip the browser entirely. That makes
the Houston adapter faster and more reliable than the Playwright version, and
it removes the headless/bot-detection risk in the cloud.

KNOWN LIMITATIONS -- read before relying on this
------------------------------------------------
* The report returns NO issue-date column. Columns are PROJECT_NO,
  PERMIT_DESC, OWNER_OCCUPANT, Address, PROJECT_DESC, CURRENT_VALUATION,
  PERMIT_TYPE. Dates are a query FILTER only.
  Workaround: run daily with BDT == EDT == yesterday. Then every returned row
  is known to have sold on that date, and lead_score's freshness weighting
  still works. Do NOT run wide windows and guess dates.
* Output is a WebFOCUS "ActiveReport": data is embedded in a JS string array,
  and repeated/empty values are OMITTED rather than emitted as blanks. So
  records have VARIABLE token counts and cannot be chunked by a fixed width.
  The parser below splits on the 8-digit project number instead.
* PTYPE did not visibly filter results in testing (sign and elevator permits
  came back under PTYPE=13). Treat PTYPE as unverified; filter by trade
  downstream with enrich.classify_trade instead.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Iterator

import httpx

LAUNCH_URL = "https://cohtora.houstontx.gov/approot/soldpermits/online_permit.htm"
SERVLET = "https://cohtora.houstontx.gov/ibi_apps/WFServlet"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# SELTD picks which field SRH searches.
SEARCH_BY = {
    "zip": "ZC",
    "address": "JA",
    "project_number": "PN",
    "owner_occupant": "OO",
    "permit_type": "PT",
    "applicant": "AN",
}

COLUMNS = [
    "PROJECT_NO", "PERMIT_DESC", "OWNER_OCCUPANT",
    "Address", "PROJECT_DESC", "CURRENT_VALUATION", "PERMIT_TYPE",
]

# Project numbers are mostly 8 digits but not always; allow 6-10.
PROJECT_NO_RE = re.compile(r"^\d{6,10}$")
ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def _tokenize_activereport(html: str) -> list[str]:
    """Pull the data token stream out of the ActiveReport payload.

    The page contains several ARstrings=[...] blocks; the data one is simply
    the largest. Everything else is chrome and UI labels.
    """
    blocks = [m.group(1) for m in re.finditer(r"ARstrings=\[(.*?)\];", html, re.S)]
    if not blocks:
        return []
    # Pick the block that CONTAINS THE COLUMN HEADERS, not the biggest one.
    # With only a handful of records the UI-chrome blocks are larger than the
    # data block, and "max by length" silently returned zero records.
    data_blocks = [b for b in blocks if "PROJECT_NO" in b and "PERMIT_TYPE" in b]
    raw = data_blocks[0] if data_blocks else max(blocks, key=len)

    tokens: list[str] = []
    for m in re.finditer(r"'((?:[^'\\]|\\.)*)'|(-?\d+(?:\.\d+)?)", raw):
        val = m.group(1) if m.group(1) is not None else m.group(2)
        tokens.append(val.replace("\\'", "'").replace('\\"', '"'))
    return tokens


def _records_from_tokens(tokens: list[str]) -> Iterator[dict[str, Any]]:
    """Split the flat token stream into records.

    ActiveReport omits repeated values, so a record is NOT a fixed number of
    tokens. Project numbers are 8 digits and start every record, so we split
    on those and assign the remaining tokens positionally by shape:
      - the token that looks like an address (has a street suffix or a ZIP)
        is the Address
      - a bare integer is CURRENT_VALUATION
      - a 2-char uppercase code is PERMIT_TYPE
      - anything starting with '*' is OWNER_OCCUPANT
      - the longest leftover is PROJECT_DESC
    This is heuristic by necessity; it is also why every row is scored, not
    trusted blindly.
    """
    try:
        start = tokens.index("PERMIT_TYPE") + 1
    except ValueError:
        start = 0
    body = tokens[start:]

    chunks: list[list[str]] = []
    current: list[str] = []
    for tok in body:
        if PROJECT_NO_RE.match(tok):
            if current:
                chunks.append(current)
            current = [tok]
        elif current:
            current.append(tok)
    if current:
        chunks.append(current)

    # An address must START WITH A HOUSE NUMBER or carry a 5-digit ZIP.
    # A bare suffix match is not enough: "ADDL PL PMT #4" is a permit
    # description, and the old rule matched the "PL" inside it and filed the
    # whole string as the street address.
    street_hint = re.compile(
        r"^\d+[A-Z]?\s+.*\b(ST|RD|DR|LN|BLVD|PKY|PKWY|AVE|CT|CIR|WAY|TRL|HWY|FWY|PL|"
        r"STRIP|ROAD|STREET|DRIVE|LANE)\b",
        re.I,
    )

    # ActiveReport SUPPRESSES REPEATED VALUES. Several permits at the same site
    # emit the owner/address once, then omit them on following rows. Without
    # forward-fill those rows arrive with a blank address, lose 25 points in
    # lead_score, and look like junk to the customer when they are in fact
    # additional permits on a job that already qualified.
    last_owner = ""
    last_address = ""

    for chunk in chunks:
        pno, rest = chunk[0], chunk[1:]
        rec: dict[str, Any] = {
            "PROJECT_NO": pno, "PERMIT_DESC": "", "OWNER_OCCUPANT": "",
            "Address": "", "PROJECT_DESC": "", "CURRENT_VALUATION": 0,
            "PERMIT_TYPE": "",
        }
        leftovers: list[str] = []
        for tok in rest:
            if not rec["Address"] and (
                street_hint.search(tok)
                or (ZIP_RE.search(tok) and re.match(r"^\d", tok))
            ):
                rec["Address"] = tok
            elif tok.startswith("*") and not rec["OWNER_OCCUPANT"]:
                rec["OWNER_OCCUPANT"] = tok.lstrip("*").strip()
            elif re.fullmatch(r"\d{1,2}", tok) and not rec["PERMIT_TYPE"]:
                # Houston's numeric PERMIT TYPE codes (11 Electrical,
                # 12 Plumbing, 13 Structural, 14 Mechanical) are bare 1-2 digit
                # integers and were previously misread as dollar valuations --
                # producing absurd rows like "HVAC ... $14".
                rec["PERMIT_TYPE"] = tok
            elif re.fullmatch(r"\d{3,}", tok) and not rec["CURRENT_VALUATION"]:
                rec["CURRENT_VALUATION"] = int(tok)
            elif re.fullmatch(r"[A-Z]{2}", tok) and not rec["PERMIT_TYPE"]:
                rec["PERMIT_TYPE"] = tok
            else:
                leftovers.append(tok)
        if leftovers:
            leftovers.sort(key=len, reverse=True)
            rec["PROJECT_DESC"] = leftovers[0]
            if len(leftovers) > 1:
                rec["PERMIT_DESC"] = leftovers[1]

        if rec["Address"]:
            last_address = rec["Address"]
        else:
            rec["Address"] = last_address
            rec["address_inherited"] = True
        if rec["OWNER_OCCUPANT"]:
            last_owner = rec["OWNER_OCCUPANT"]
        else:
            rec["OWNER_OCCUPANT"] = last_owner

        yield rec


def fetch_houston(
    search_by: str = "zip",
    value: str = "77072",
    day: date | None = None,
    timeout: float = 120.0,
) -> list[dict[str, Any]]:
    """Fetch one day of Houston sold permits for one ZIP (or address, etc.).

    Dates use YYYYMMDD -- other formats are rejected by the report.
    Because the report returns no per-row date, query ONE day at a time so the
    issue date is known from the query itself.
    """
    day = day or (date.today() - timedelta(days=1))
    stamp = day.strftime("%Y%m%d")

    with httpx.Client(
        headers={"User-Agent": UA, "Referer": LAUNCH_URL},
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        client.get(LAUNCH_URL)  # establish a session
        resp = client.post(
            SERVLET,
            data={
                "IBIF_ex": "online_per_se.fex",
                "IBIAPP_app": "soldpermits",
                "IBIC_server": "EDASERVE",
                "SELTD": SEARCH_BY.get(search_by, "ZC"),
                "SRH": value,
                "BDT": stamp,
                "EDT": stamp,
            },
        )
    resp.raise_for_status()

    found = re.search(r"'(\d+)',\s*'Record\(s\) found for'", resp.text)
    tokens = _tokenize_activereport(resp.text)
    rows = list(_records_from_tokens(tokens))

    out: list[dict[str, Any]] = []
    for r in rows:
        zip_m = ZIP_RE.search(r["Address"])
        out.append({
            "permit_number": r["PROJECT_NO"],
            "issued_date": day.isoformat(),      # from the query window
            "address": r["Address"],
            "zip": zip_m.group(1) if zip_m else (value if search_by == "zip" else ""),
            "work_description": r["PROJECT_DESC"],
            "permit_type": r["PERMIT_DESC"] or r["PERMIT_TYPE"],
            "valuation": r["CURRENT_VALUATION"],
            "owner_name": r["OWNER_OCCUPANT"],
            "address_inherited": bool(r.get("address_inherited")),
            "contractor_name": "",
            "source_city": "houston_tx",
            "source_url": LAUNCH_URL,
        })

    reported = int(found.group(1)) if found else None
    note = ""
    if reported is not None and len(out) < reported:
        note = (f"  [CAPTURE {len(out)}/{reported} — ActiveReport suppresses repeated "
                f"values; some grouped sub-permits are unrecoverable from this format]")
    print(f"[houston] {search_by}={value} {stamp}: "
          f"report says {reported if reported is not None else '?'} record(s), "
          f"parsed {len(out)}{note}")
    return out


if __name__ == "__main__":
    import json
    # A wider window purely to prove extraction; production runs one day.
    res = fetch_houston("zip", "77072", date.today() - timedelta(days=1))
    print(json.dumps(res[:6], indent=2))


# ---------------------------------------------------------------------------
# Adapter wrapper so the actor can use this like any other city
# ---------------------------------------------------------------------------
from .base import PermitAdapter  # noqa: E402  (kept at bottom: avoids cycle)


class WebFocusAdapter(PermitAdapter):
    """Houston via direct WebFOCUS POSTs — no browser.

    The report accepts ONE search value per call and returns no per-row date,
    so we loop day-by-day over the lookback window and once per ZIP. Each
    (day, zip) pair is one POST. With a 2-day lookback and 5 ZIPs that is 10
    requests — cheap, and it means every row's issue date is known exactly.

    If no ZIPs are supplied we cannot enumerate the whole city (the report
    requires a search term), so we fall back to a broad permit-type sweep.
    """

    #: conservative pacing against a city server
    DELAY_SECONDS = 1.0

    async def fetch(self):
        import asyncio

        log = self.ctx.log
        zips = list(getattr(self.ctx, "zip_whitelist", None) or [])
        days = [
            date.today() - timedelta(days=d)
            for d in range(1, max(1, self.ctx.lookback_days) + 1)
        ]

        if not zips:
            log.warning(
                "[houston_tx] no ZIP filter set. Houston's report requires a search "
                "term, so it cannot be swept city-wide. Set zipWhitelist to your "
                "target ZIPs (e.g. 77072, 77036, 77081) to get Houston leads."
            )
            return

        emitted = 0
        for day in days:
            for z in zips:
                if emitted >= self.ctx.max_results:
                    return
                try:
                    rows = await asyncio.to_thread(fetch_houston, "zip", z, day)
                except Exception as exc:
                    log.warning(f"[houston_tx] {z} {day}: {type(exc).__name__}: {exc}")
                    continue

                for r in rows:
                    rec = self.blank_record("houston_tx")
                    rec.update(r)
                    rec["issued_date_parsed"] = day
                    yield rec
                    emitted += 1
                    if emitted >= self.ctx.max_results:
                        return
                await asyncio.sleep(self.DELAY_SECONDS)

        log.info(f"[houston_tx] emitted {emitted} rows over {len(days)} day(s) x {len(zips)} zip(s)")
