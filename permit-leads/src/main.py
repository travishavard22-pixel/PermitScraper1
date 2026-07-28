"""Construction Permit Leads — Apify actor entry point.

Monetization: pay-per-event. Apify is retiring the rental model (no new rental
listings since 2026-04-01, full retirement 2026-10-01), and PPE actors also get
better placement in Store. Two events are defined:

    actor-start   — small fixed fee, covers the browser spin-up on portal cities
    permit-lead   — charged per NEW, filtered, scored lead actually delivered

Charging per delivered lead rather than per row scraped is the honest structure
and it is also the one that survives a refund dispute: the customer is billed
for leads they had not already received.
"""

from __future__ import annotations

import json
from typing import Any

from apify import Actor

from .adapters import CITY_REGISTRY, ScrapeContext, build_adapter
from .enrich import enrich, passes_filters

EVENT_ACTOR_START = "actor-start"
EVENT_PERMIT_LEAD = "permit-lead"

SEEN_STORE_NAME = "permit-leads-seen"
SEEN_KEY = "seen_ids"
# Cap the dedup ledger so a long-running schedule cannot grow the record
# unboundedly. 200k ids is roughly 5MB of JSON and years of a single metro.
SEEN_MAX = 200_000


def fingerprint(record: dict[str, Any]) -> str:
    """Stable identity for a permit across runs.

    Permit number alone is not safe: numbering collides across municipalities,
    and some portals reuse numbers for revisions. City + number + issue date is
    stable and cheap.
    """
    return "|".join(
        [
            record.get("source_city", ""),
            (record.get("permit_number") or "").strip().upper(),
            record.get("issued_date") or "",
            # Fall back to address when the portal gives no permit number.
            (record.get("address") or "").strip().upper()[:60],
        ]
    )


async def load_seen() -> tuple[set[str], Any]:
    store = await Actor.open_key_value_store(name=SEEN_STORE_NAME)
    raw = await store.get_value(SEEN_KEY)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = []
    return set(raw or []), store


async def save_seen(store: Any, seen: set[str]) -> None:
    trimmed = list(seen)[-SEEN_MAX:]
    await store.set_value(SEEN_KEY, trimmed)


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}

        cities: list[str] = inp.get("cities") or ["houston_tx"]
        lookback_days: int = int(inp.get("lookbackDays", 3))
        trades: list[str] = inp.get("trades") or []
        min_valuation: int = int(inp.get("minValuation", 5000))
        zip_whitelist: list[str] = inp.get("zipWhitelist") or []
        require_contractor: bool = bool(inp.get("requireContractor", False))
        only_new: bool = bool(inp.get("onlyNew", True))
        max_results: int = int(inp.get("maxResults", 500))
        debug_screenshots: bool = bool(inp.get("debugScreenshots", False))

        # ---- proxy -------------------------------------------------------
        # Never let proxy setup kill the run. Locally there is no Apify token,
        # so create_proxy_configuration() raises — but Socrata cities need no
        # proxy at all, and a portal run without one is still worth attempting.
        proxy_url: str | None = None
        try:
            proxy_cfg = await Actor.create_proxy_configuration(
                actor_proxy_input=inp.get("proxyConfiguration")
            )
            if proxy_cfg:
                proxy_url = await proxy_cfg.new_url()
        except Exception as exc:
            Actor.log.warning(
                f"proxy unavailable, continuing without one ({exc.__class__.__name__}). "
                f"This is normal for local runs. Portal cities may be rate-limited."
            )

        # ---- charge for the run start ------------------------------------
        # If the actor is not monetized (local run, or PPE not yet configured),
        # charge() is a no-op-ish call; never let billing break the scrape.
        budget_exhausted = False
        try:
            await Actor.charge(event_name=EVENT_ACTOR_START)
        except Exception as exc:
            Actor.log.debug(f"actor-start charge skipped: {exc}")

        # ---- dedup ledger --------------------------------------------------
        seen, seen_store = (set(), None)
        if only_new:
            seen, seen_store = await load_seen()
            Actor.log.info(f"dedup ledger loaded: {len(seen)} permits previously seen")

        ctx = ScrapeContext(
            lookback_days=lookback_days,
            max_results=max_results,
            proxy_url=proxy_url,
            debug_screenshots=debug_screenshots,
            log=Actor.log,
            zip_whitelist=zip_whitelist,
        )

        stats = {
            "scraped": 0,
            "filtered_out": 0,
            "duplicates": 0,
            "delivered": 0,
        }

        for city_key in cities:
            if budget_exhausted:
                break

            source = CITY_REGISTRY.get(city_key)
            if source is None:
                Actor.log.warning(f"unknown city '{city_key}', skipping")
                continue

            Actor.log.info(f"=== {source.label} ({source.kind}) ===")
            adapter = build_adapter(source, ctx)

            try:
                async for raw in adapter.fetch():
                    stats["scraped"] += 1

                    record = enrich(raw)

                    if not passes_filters(
                        record, trades, min_valuation, zip_whitelist, require_contractor
                    ):
                        stats["filtered_out"] += 1
                        continue

                    fp = fingerprint(record)
                    if only_new and fp in seen:
                        stats["duplicates"] += 1
                        continue

                    # issued_date_parsed is a date object; strip before pushing.
                    record.pop("issued_date_parsed", None)

                    await Actor.push_data(record)
                    stats["delivered"] += 1
                    if only_new:
                        seen.add(fp)

                    # Charge only for leads actually delivered.
                    try:
                        result = await Actor.charge(event_name=EVENT_PERMIT_LEAD)
                        if getattr(result, "event_charge_limit_reached", False):
                            Actor.log.info(
                                "user's max-cost-per-run limit reached; stopping cleanly"
                            )
                            budget_exhausted = True
                            break
                    except Exception as exc:
                        Actor.log.debug(f"lead charge skipped: {exc}")

            except Exception as exc:
                # One broken city must not kill the whole run.
                Actor.log.exception(f"[{city_key}] adapter failed: {exc}")
                continue

        if only_new and seen_store is not None:
            await save_seen(seen_store, seen)

        Actor.log.info(
            f"done — scraped {stats['scraped']}, "
            f"filtered {stats['filtered_out']}, "
            f"duplicates {stats['duplicates']}, "
            f"delivered {stats['delivered']}"
        )
        await Actor.set_status_message(
            f"{stats['delivered']} new leads "
            f"({stats['duplicates']} already seen, {stats['filtered_out']} filtered)"
        )

