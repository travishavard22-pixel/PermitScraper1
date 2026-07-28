"""Socrata (SODA API) adapter.

Covers any city publishing permits to a Socrata open-data portal. Paginates
with $limit/$offset and filters server-side on the issue date so we transfer
only the window we need.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, AsyncIterator

import httpx
from dateutil import parser as dateparser

from .base import PermitAdapter

PAGE_SIZE = 1000
TIMEOUT = httpx.Timeout(30.0, connect=15.0)


class SocrataAdapter(PermitAdapter):
    async def fetch(self) -> AsyncIterator[dict[str, Any]]:
        src = self.source
        log = self.ctx.log
        cutoff = (date.today() - timedelta(days=self.ctx.lookback_days)).isoformat()

        base_url = f"https://{src.domain}/resource/{src.dataset_id}.json"
        # Socrata floating timestamps compare correctly as ISO strings.
        where = (f"{src.date_field} IS NOT NULL AND "
                 f"{src.date_field} >= '{cutoff}T00:00:00.000'")

        emitted = 0
        offset = 0

        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            while emitted < self.ctx.max_results:
                params = {
                    "$where": where,
                    "$order": f"{src.date_field} DESC",
                    "$limit": min(PAGE_SIZE, self.ctx.max_results - emitted),
                    "$offset": offset,
                }
                try:
                    resp = await client.get(base_url, params=params)
                except httpx.HTTPError as exc:
                    log.warning(f"[{src.key}] network error: {exc}")
                    return

                if resp.status_code == 404:
                    log.error(
                        f"[{src.key}] dataset {src.dataset_id} returned 404. "
                        f"The city likely republished it. Update CITY_REGISTRY — "
                        f"browse https://{src.domain}/browse?q=building+permits"
                    )
                    return
                if resp.status_code == 400:
                    log.error(
                        f"[{src.key}] 400 from Socrata — a field name in field_map "
                        f"or date_field is wrong for this dataset. Response: "
                        f"{resp.text[:300]}"
                    )
                    return
                if resp.status_code != 200:
                    log.warning(f"[{src.key}] HTTP {resp.status_code}, stopping.")
                    return

                rows = resp.json()
                if not rows:
                    return

                for row in rows:
                    yield self._normalize(row)
                    emitted += 1
                    if emitted >= self.ctx.max_results:
                        return

                if len(rows) < PAGE_SIZE:
                    return
                offset += len(rows)

    def _normalize(self, row: dict[str, Any]) -> dict[str, Any]:
        src = self.source
        rec = self.blank_record(src.key)

        for out_key, in_key in src.field_map.items():
            # A field_map value may be a single column name, or a LIST of
            # column names to join with spaces. Cities differ on this: Austin
            # publishes one address column, while Chicago splits it into
            # street_number / street_direction / street_name and NYC into
            # house_no / street_name. Joining here keeps the rest of the
            # pipeline working on one clean `address` string.
            if isinstance(in_key, (list, tuple)):
                parts = [str(row.get(k)).strip() for k in in_key if row.get(k) not in (None, "")]
                if parts:
                    rec[out_key] = " ".join(parts)
            else:
                value = row.get(in_key)
                if value is not None:
                    rec[out_key] = value

        raw_date = rec.get("issued_date")
        if raw_date:
            try:
                rec["issued_date_parsed"] = dateparser.parse(str(raw_date)).date()
                rec["issued_date"] = rec["issued_date_parsed"].isoformat()
            except (ValueError, TypeError, OverflowError):
                rec["issued_date_parsed"] = None

        pn_field = src.field_map.get("permit_number", "permit_number")
        if isinstance(pn_field, (list, tuple)):
            pn_field = pn_field[0]
        rec["source_url"] = (
            f"https://{src.domain}/resource/{src.dataset_id}.json"
            f"?{pn_field}={rec.get('permit_number', '')}"
        )
        return rec
