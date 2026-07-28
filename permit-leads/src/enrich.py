"""Trade classification and lead scoring.

This module is the product. Anyone can dump raw permit rows; the reason a
contractor pays for this actor is that it tells them *which* rows are worth a
phone call this morning.

Classification is deliberately rule-based rather than LLM-based:
  - Permit work descriptions are short, formulaic, and municipal. Regex wins.
  - It costs $0 per row. An LLM call per permit would eat the margin at volume.
  - It is deterministic, so a customer who reports a misclassification can be
    given a fix in the next release instead of a shrug.

If you later want fuzzy handling of genuinely messy descriptions, run the LLM
ONLY on rows that fall through to `other` — typically <10% of volume.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

# Order matters. The first pattern that matches wins, so the most specific and
# highest-commercial-value trades are tested first.
TRADE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("solar", re.compile(r"\b(solar|photovoltaic|\bpv\b|battery storage)\b", re.I)),
    ("pool", re.compile(r"\b(pool|spa|hot tub|jacuzzi)\b", re.I)),
    (
        # A bare "roof" token is a strong enough signal on its own: municipal
        # descriptions say "REPLACE ROOF", "ROOF OVER", "ROOF ONLY" as often as
        # they say "reroof". Solar and pool are tested BEFORE this, so
        # "roof mounted solar" still classifies as solar rather than roofing.
        "roofing",
        re.compile(
            r"\b(re-?roof\w*|roofs?\b|roofing|shingle|tpo\b|torch down|"
            r"built-?up roof|modified bitumen|standing seam)\b",
            re.I,
        ),
    ),
    (
        "foundation",
        re.compile(
            r"\b(foundation|pier and beam|piers?\b|slab repair|underpinning|"
            r"structural repair|helical)\b",
            re.I,
        ),
    ),
    (
        "hvac",
        re.compile(
            r"\b(hvac|\ba/?c\b|air condition\w*|furnace|heat pump|condenser|"
            r"mini-?split|ductwork|duct work|mechanical|rtu\b)\b",
            re.I,
        ),
    ),
    (
        "plumbing",
        re.compile(
            r"\b(plumb\w*|water heater|tankless|sewer|re-?pipe|repipe|gas line|"
            r"gas test|backflow|grease trap|water line|drain line)\b",
            re.I,
        ),
    ),
    (
        "electrical",
        re.compile(
            r"\b(electric\w*|panel upgrade|service upgrade|rewire|re-?wire|"
            r"meter loop|sub-?panel|generator|ev charger|amp service)\b",
            re.I,
        ),
    ),
    (
        "demolition",
        re.compile(r"\b(demo\b|demolition|demolish|tear ?down|raze)\b", re.I),
    ),
    (
        "new_construction",
        re.compile(
            r"\b(new construction|new single family|new sfr|new residence|"
            r"new commercial|new building|ground up|shell building)\b",
            re.I,
        ),
    ),
    (
        "fence_deck",
        re.compile(r"\b(fence|fencing|deck\b|patio cover|carport|pergola|arbor)\b", re.I),
    ),
    (
        "remodel",
        re.compile(
            r"\b(remodel|renovat\w*|addition|alteration|repair|convert\w*|"
            r"finish out|finish-?out|build-?out|buildout|interior|tenant improvement|"
            r"\bti\b|kitchen|bathroom|bath\b|window replacement|siding)\b",
            re.I,
        ),
    ),
]

# Median job value by trade. Used as a fallback when a municipality reports
# valuation as 0 or blank, which is common for trade permits pulled separately
# from the parent building permit. Calibrate these against your own market.
TRADE_VALUE_FALLBACK: dict[str, int] = {
    "solar": 22000,
    "pool": 55000,
    "roofing": 14000,
    "foundation": 9000,
    "hvac": 8500,
    "plumbing": 4500,
    "electrical": 3800,
    "demolition": 12000,
    "new_construction": 250000,
    "fence_deck": 6500,
    "remodel": 25000,
    "other": 5000,
}

# Signals that a permit is a large commercial job rather than residential.
COMMERCIAL_HINTS = re.compile(
    r"\b(commercial|tenant|retail|restaurant|warehouse|office|multi-?family|"
    r"apartment|hotel|school|church|medical|clinic|strip center)\b",
    re.I,
)

OWNER_PULLED_HINTS = re.compile(r"\b(owner|homeowner|self)\b", re.I)


def classify_trade(work_description: str, permit_type: str = "") -> str:
    """Return the best-guess trade for a permit.

    Tests the permit's own type field first (more reliable when present), then
    falls back to the free-text work description.
    """
    haystack_primary = permit_type or ""
    haystack_secondary = work_description or ""

    for trade, pattern in TRADE_PATTERNS:
        if pattern.search(haystack_primary):
            return trade
    for trade, pattern in TRADE_PATTERNS:
        if pattern.search(haystack_secondary):
            return trade
    return "other"


def parse_valuation(raw: Any) -> int:
    """Coerce a municipal valuation field into an int.

    Municipalities report this as '$12,500.00', '12500', 12500.0, '', or None
    depending on the city and the decade the record was entered.
    """
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return max(0, int(raw))
    cleaned = re.sub(r"[^0-9.]", "", str(raw))
    if not cleaned:
        return 0
    try:
        return max(0, int(float(cleaned)))
    except ValueError:
        return 0


def _days_old(issued: date | None) -> int:
    if issued is None:
        return 999
    return max(0, (date.today() - issued).days)


def score_lead(record: dict[str, Any]) -> int:
    """Score a permit 0-100 on how worth calling it is, today.

    The weighting reflects how contractors actually buy:
      - Freshness dominates. A three-day-old permit is a cold lead because
        four competitors already called. This is the single biggest factor.
      - Job size matters, but with diminishing returns.
      - Owner-pulled permits score higher for anyone selling TO homeowners,
        because no contractor is attached yet.
    """
    score = 0

    age = _days_old(record.get("issued_date_parsed"))
    if age <= 1:
        score += 45
    elif age <= 3:
        score += 32
    elif age <= 7:
        score += 18
    elif age <= 14:
        score += 8

    valuation = record.get("valuation") or 0
    if valuation >= 100_000:
        score += 30
    elif valuation >= 40_000:
        score += 24
    elif valuation >= 15_000:
        score += 18
    elif valuation >= 5_000:
        score += 11
    elif valuation > 0:
        score += 5

    # No contractor on the permit means the homeowner is still shopping.
    contractor = (record.get("contractor_name") or "").strip()
    if not contractor or OWNER_PULLED_HINTS.search(contractor):
        score += 15

    description = record.get("work_description") or ""
    if COMMERCIAL_HINTS.search(description):
        score += 8

    # A permit with no usable address cannot be doorknocked or mailed.
    if not (record.get("address") or "").strip():
        score -= 25

    return max(0, min(100, score))


def enrich(record: dict[str, Any]) -> dict[str, Any]:
    """Attach trade, normalized valuation, and lead score to a raw permit row."""
    record["trade"] = classify_trade(
        record.get("work_description", ""),
        record.get("permit_type", ""),
    )

    valuation = parse_valuation(record.get("valuation"))
    if valuation == 0:
        record["valuation"] = TRADE_VALUE_FALLBACK.get(record["trade"], 5000)
        record["valuation_is_estimated"] = True
    else:
        record["valuation"] = valuation
        record["valuation_is_estimated"] = False

    record["lead_score"] = score_lead(record)
    record["scraped_at"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    return record


def passes_filters(
    record: dict[str, Any],
    trades: list[str],
    min_valuation: int,
    zip_whitelist: list[str],
    require_contractor: bool,
) -> bool:
    """Apply user filters. Runs AFTER enrichment so it can filter on trade."""
    if trades and record.get("trade") not in trades:
        return False

    # Never filter out a permit on an *estimated* valuation — that would drop
    # real leads because the city left the field blank.
    if (
        min_valuation
        and not record.get("valuation_is_estimated")
        and (record.get("valuation") or 0) < min_valuation
    ):
        return False

    if zip_whitelist:
        record_zip = (record.get("zip") or "").strip()[:5]
        if record_zip not in {z.strip()[:5] for z in zip_whitelist}:
            return False

    if require_contractor and not (record.get("contractor_name") or "").strip():
        return False

    return True
