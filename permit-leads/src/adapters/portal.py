"""Browser-driven adapter for Accela-family permit portals (Houston).

WHY THIS EXISTS
---------------
The City of Houston Planning & Development Department stopped supplying Permit
Activity Report download links on 2025-12-01 and now directs the public to the
interactive Sold Permits Search. There is no bulk endpoint. The data is public
and free, but only reachable through a rendered search form.

That gap is the business. Anyone can hit a Socrata API; far fewer will maintain
a browser automation against a municipal portal that gets redesigned without
notice. Price accordingly.

LEGAL POSTURE
-------------
This adapter is logged-out only and never authenticates. Post-*hiQ v. LinkedIn*
and *Van Buren*, scraping public pages without bypassing an access control does
not implicate the CFAA; the exposure that remains is contract-based and attaches
mainly to accounts that accepted terms. Do not add a login to this adapter. If a
portal ever requires an account to see permit results, drop that city rather
than authenticate — the whole product's risk profile depends on staying
logged-out.

Rate limiting below is deliberately conservative. Hammering a municipal server
is how you generate a trespass-to-chattels claim and get the IP range banned for
everyone.

CALIBRATION
-----------
Selectors are candidate lists, tried in order, because municipal portals get
reskinned. When a portal changes:
    1. Re-run with debugScreenshots = true
    2. Open the key-value store screenshots
    3. Add the new selector to the front of the relevant candidate list
No other code should need to change.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta
from typing import Any, AsyncIterator

from dateutil import parser as dateparser
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout, async_playwright

from .base import PermitAdapter

# Be a good citizen against a city server.
DELAY_BETWEEN_PAGES_MS = 1500
NAV_TIMEOUT_MS = 45_000

SELECTORS: dict[str, list[str]] = {
    "date_from": [
        "input#dateFrom",
        "input[name='dateFrom']",
        "input[id*='IssuedFrom']",
        "input[placeholder*='From']",
        "#ctl00_PlaceHolderMain_generalSearchForm_txtGSStartDate",
    ],
    "date_to": [
        "input#dateTo",
        "input[name='dateTo']",
        "input[id*='IssuedTo']",
        "input[placeholder*='To']",
        "#ctl00_PlaceHolderMain_generalSearchForm_txtGSEndDate",
    ],
    "submit": [
        "button[type='submit']",
        "input[type='submit'][value*='Search']",
        "a#ctl00_PlaceHolderMain_btnNewSearch",
        "button:has-text('Search')",
    ],
    "results_table": [
        "table#results",
        "table[id*='Result']",
        "div[role='grid']",
        "table.ACA_GridView",
        "table",
    ],
    "next_page": [
        "a:has-text('Next')",
        "a[title='Next page']",
        "li.next > a",
        "a[id*='Next']",
    ],
}

# Maps a normalized field to the header text a portal might use for it.
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "permit_number": ("permit number", "permit #", "record number", "permit no"),
    "issued_date": ("issued date", "date issued", "issue date", "sold date", "date"),
    "address": ("address", "site address", "project address", "location"),
    "work_description": ("description", "work description", "project description", "scope"),
    "permit_type": ("type", "permit type", "record type", "work type"),
    "valuation": ("valuation", "value", "job value", "declared value", "cost"),
    "contractor_name": ("contractor", "contractor name", "licensed professional"),
    "owner_name": ("owner", "owner name", "applicant"),
}

ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


async def _first_matching(page: Page, candidates: list[str], timeout: int = 4000):
    """Return the first selector from `candidates` that resolves, else None."""
    for selector in candidates:
        try:
            locator = page.locator(selector).first
            await locator.wait_for(state="attached", timeout=timeout)
            return locator, selector
        except PlaywrightTimeout:
            continue
    return None, None


class PortalAdapter(PermitAdapter):
    async def fetch(self) -> AsyncIterator[dict[str, Any]]:
        log = self.ctx.log
        src = self.source

        date_to = date.today()
        date_from = date_to - timedelta(days=self.ctx.lookback_days)

        launch_args: dict[str, Any] = {"headless": True}
        if self.ctx.proxy_url:
            launch_args["proxy"] = {"server": self.ctx.proxy_url}

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(**launch_args)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
            )
            page = await context.new_page()
            page.set_default_timeout(NAV_TIMEOUT_MS)

            try:
                log.info(f"[{src.key}] opening {src.portal_url}")
                await page.goto(src.portal_url, wait_until="domcontentloaded")
                await self._shot(page, "01-landing")

                await self._fill_date_range(page, date_from, date_to, log)
                await self._shot(page, "02-filled")

                submit, sel = await _first_matching(page, SELECTORS["submit"])
                if submit is None:
                    log.error(
                        f"[{src.key}] could not find a search button. Re-run with "
                        f"debugScreenshots=true and update SELECTORS['submit']."
                    )
                    return
                log.info(f"[{src.key}] submitting via {sel}")
                await submit.click()
                await page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
                await self._shot(page, "03-results")

                emitted = 0
                page_num = 1
                while emitted < self.ctx.max_results:
                    rows = await self._parse_results_table(page, log)
                    if not rows:
                        log.info(f"[{src.key}] no rows on page {page_num}; done.")
                        break

                    for row in rows:
                        yield row
                        emitted += 1
                        if emitted >= self.ctx.max_results:
                            break

                    if emitted >= self.ctx.max_results:
                        break

                    nxt, _ = await _first_matching(page, SELECTORS["next_page"], timeout=2500)
                    if nxt is None or not await nxt.is_enabled():
                        break

                    page_num += 1
                    await asyncio.sleep(DELAY_BETWEEN_PAGES_MS / 1000)
                    await nxt.click()
                    await page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)

                log.info(f"[{src.key}] emitted {emitted} rows across {page_num} page(s)")

            except PlaywrightTimeout as exc:
                log.error(f"[{src.key}] timed out — portal may be down or redesigned: {exc}")
                await self._shot(page, "99-timeout")
            finally:
                await context.close()
                await browser.close()

    async def _fill_date_range(self, page: Page, dfrom: date, dto: date, log) -> None:
        fmt = "%m/%d/%Y"
        for key, value in (("date_from", dfrom), ("date_to", dto)):
            field, sel = await _first_matching(page, SELECTORS[key])
            if field is None:
                log.warning(
                    f"[{self.source.key}] no {key} input found; falling back to the "
                    f"portal's default window. Results may be wider than requested."
                )
                continue
            await field.fill(value.strftime(fmt))
            log.info(f"[{self.source.key}] {key} = {value.strftime(fmt)} via {sel}")

    async def _parse_results_table(self, page: Page, log) -> list[dict[str, Any]]:
        table, sel = await _first_matching(page, SELECTORS["results_table"], timeout=8000)
        if table is None:
            log.warning(f"[{self.source.key}] no results table found.")
            return []

        headers = [
            (h or "").strip().lower()
            for h in await table.locator("thead th, tr:first-child th").all_inner_texts()
        ]
        if not headers:
            log.warning(
                f"[{self.source.key}] results table at '{sel}' has no headers. "
                f"It may be a layout table — refine SELECTORS['results_table']."
            )
            return []

        col_index: dict[str, int] = {}
        for field_name, aliases in HEADER_ALIASES.items():
            for idx, header in enumerate(headers):
                if any(alias in header for alias in aliases):
                    col_index[field_name] = idx
                    break

        if "permit_number" not in col_index:
            log.warning(
                f"[{self.source.key}] no permit-number column among headers: {headers}. "
                f"Add the portal's header text to HEADER_ALIASES."
            )

        out: list[dict[str, Any]] = []
        row_locator = table.locator("tbody tr")
        for i in range(await row_locator.count()):
            cells = await row_locator.nth(i).locator("td").all_inner_texts()
            if not cells:
                continue

            rec = self.blank_record(self.source.key)
            for field_name, idx in col_index.items():
                if idx < len(cells):
                    rec[field_name] = (cells[idx] or "").strip()

            raw_date = rec.get("issued_date")
            if raw_date:
                try:
                    rec["issued_date_parsed"] = dateparser.parse(raw_date).date()
                    rec["issued_date"] = rec["issued_date_parsed"].isoformat()
                except (ValueError, TypeError, OverflowError):
                    rec["issued_date_parsed"] = None

            zip_match = ZIP_RE.search(rec.get("address", ""))
            if zip_match:
                rec["zip"] = zip_match.group(1)

            rec["source_url"] = self.source.portal_url

            if rec.get("permit_number") or rec.get("address"):
                out.append(rec)

        return out

    async def _shot(self, page: Page, name: str) -> None:
        if not self.ctx.debug_screenshots:
            return
        try:
            from apify import Actor

            png = await page.screenshot(full_page=True)
            store = await Actor.open_key_value_store()
            await store.set_value(
                f"debug-{self.source.key}-{name}.png", png, content_type="image/png"
            )
        except Exception as exc:  # debug aid must never break a run
            self.ctx.log.debug(f"screenshot failed: {exc}")
