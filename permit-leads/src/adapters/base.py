"""Adapter contract shared by every city source.

Two families of source exist, and the split is the whole reason this actor has
a moat:

  SOCRATA  — the city publishes a clean, queryable open-data endpoint. Cheap to
             support, ~40 lines per city. Also cheap for a competitor to copy.

  PORTAL   — the city only exposes an interactive Accela/Tyler/CityView search
             form. Requires a real browser, session handling, and pagination
             through rendered results. Expensive to build, annoying to maintain,
             and therefore rarely done well by anyone else. This is where the
             pricing power is.

Houston is a PORTAL city. That is a feature.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any, AsyncIterator


@dataclass
class CitySource:
    """Static configuration for one city."""

    key: str
    label: str
    kind: str  # "socrata" | "portal"
    # Socrata fields
    domain: str = ""
    dataset_id: str = ""
    # value = one column name, or a list of columns joined with spaces
    field_map: dict[str, Any] = field(default_factory=dict)
    date_field: str = ""
    # Portal fields
    portal_url: str = ""


@dataclass
class ScrapeContext:
    """Everything an adapter needs from the run, without touching global state."""

    lookback_days: int
    max_results: int
    proxy_url: str | None
    debug_screenshots: bool
    log: Any
    #: Houston's report requires a search term, so it needs the ZIP list.
    zip_whitelist: list[str] = field(default_factory=list)


class PermitAdapter(ABC):
    """Yields normalized permit dicts for one city."""

    def __init__(self, source: CitySource, ctx: ScrapeContext) -> None:
        self.source = source
        self.ctx = ctx

    @abstractmethod
    async def fetch(self) -> AsyncIterator[dict[str, Any]]:
        """Yield normalized permit records.

        Every adapter must emit at minimum these keys, using None/'' when the
        city does not supply them:

            permit_number, issued_date, issued_date_parsed (date|None),
            address, zip, work_description, permit_type, valuation,
            contractor_name, owner_name, source_city, source_url
        """
        raise NotImplementedError
        yield {}  # pragma: no cover - satisfies AsyncIterator typing

    @staticmethod
    def blank_record(source_key: str) -> dict[str, Any]:
        return {
            "permit_number": "",
            "issued_date": "",
            "issued_date_parsed": None,
            "address": "",
            "zip": "",
            "work_description": "",
            "permit_type": "",
            "valuation": 0,
            "contractor_name": "",
            "owner_name": "",
            "source_city": source_key,
            "source_url": "",
        }


# ---------------------------------------------------------------------------
# City registry — VERIFIED SOURCES ONLY
# ---------------------------------------------------------------------------
# Every entry below was queried live on 2026-07-27 and confirmed to return
# current data. Do not add a city to this registry until `tools/verify_city.py`
# passes for it. Shipping a city whose dataset silently returns nothing is worse
# than not shipping it — the customer pays for a run that finds zero leads and
# then churns.
#
# WHAT VERIFICATION TURNED UP, and why it matters commercially:
#
#   Dallas (e7gq-4sah)  — endpoint is live but the newest record is 2019-12-31.
#                         It is an archive, not a feed. Useless for leads.
#   San Antonio         — dataset id 404s; the city reorganized its portal.
#   Fort Worth          — not a Socrata host at all.
#
# The general lesson: most "city open permit data" is a stale dump. Live,
# lead-grade permit data is rarer than it looks, which is exactly why the
# Houston browser adapter has pricing power. Do not assume a city is easy
# until you have seen a row from this week.
#
# Houston has no Socrata entry by design: the City of Houston Planning &
# Development Department stopped supplying Permit Activity Report links on
# 2025-12-01 and now directs the public to the interactive Sold Permits Search.
# That removal is the moat.

CITY_REGISTRY: dict[str, CitySource] = {
    "houston_tx": CitySource(
        key="houston_tx",
        label="Houston, TX",
        kind="webfocus",
        portal_url="https://www.houstonpermittingcenter.org/sold-permits-search",
    ),
    # Verified live 2026-07-28: newest issue_date 2026-07-27 (1 day old).
    # Austin publishes NO valuation column, so every Austin row gets an
    # estimated valuation. That is why passes_filters never drops a row on an
    # estimated value.
    "austin_tx": CitySource(
        key="austin_tx",
        label="Austin, TX",
        kind="socrata",
        domain="data.austintexas.gov",
        dataset_id="3syk-w9eu",
        date_field="issue_date",
        field_map={
            "permit_number": "permit_number",
            "issued_date": "issue_date",
            "address": "original_address1",
            "zip": "original_zip",
            "work_description": "description",
            "permit_type": "permit_type_desc",
            "contractor_name": "contractor_company_name",
        },
    ),
    # Verified live 2026-07-28: newest issue_date 2026-07-27 (1 day old).
    # Richest source of the set: has valuation AND contractor.
    "chicago_il": CitySource(
        key="chicago_il",
        label="Chicago, IL",
        kind="socrata",
        domain="data.cityofchicago.org",
        dataset_id="ydr8-5enu",
        date_field="issue_date",
        field_map={
            "permit_number": "permit_",
            "issued_date": "issue_date",
            # Chicago splits the site address across three columns.
            "address": ["street_number", "street_direction", "street_name"],
            "work_description": "work_description",
            "permit_type": "permit_type",
            "valuation": "reported_cost",
            "contractor_name": "contact_1_name",
            # NOTE: no site ZIP is published (contact_*_zipcode is the
            # CONTACT's zip, not the job site). Leaving zip blank is correct;
            # mapping the contact zip would silently mis-target ZIP filters.
        },
    ),
    # Verified live 2026-07-28: newest issued_date 2026-07-27 (1 day old).
    "nyc_ny": CitySource(
        key="nyc_ny",
        label="New York City, NY",
        kind="socrata",
        domain="data.cityofnewyork.us",
        dataset_id="rbx6-tga4",
        date_field="issued_date",
        field_map={
            "permit_number": "job_filing_number",
            "issued_date": "issued_date",
            "address": ["house_no", "street_name"],
            "zip": "zip_code",
            "work_description": "job_description",
            "permit_type": "work_type",
            "valuation": "estimated_job_costs",
            "contractor_name": "applicant_business_name",
            "owner_name": "owner_name",
        },
    ),
    # Verified live 2026-07-28: newest issueddate 2026-07-25 (3 days old).
    "seattle_wa": CitySource(
        key="seattle_wa",
        label="Seattle, WA",
        kind="socrata",
        domain="cos-data.seattle.gov",
        dataset_id="76t5-zqzr",
        date_field="issueddate",
        field_map={
            "permit_number": "permitnum",
            "issued_date": "issueddate",
            "address": "originaladdress1",
            "zip": "originalzip",
            "work_description": "description",
            "permit_type": "permittypedesc",
            "valuation": "estprojectcost",
            "contractor_name": "contractorcompanyname",
        },
    ),
    # Verified live 2026-07-28: newest issue_date 2026-07-25 (3 days old).
    "los_angeles_ca": CitySource(
        key="los_angeles_ca",
        label="Los Angeles, CA",
        kind="socrata",
        domain="data.lacity.org",
        dataset_id="pi9x-tg5x",
        date_field="issue_date",
        field_map={
            "permit_number": "permit_nbr",
            "issued_date": "issue_date",
            "address": "primary_address",
            "zip": "zip_code",
            "work_description": "use_desc",
            "permit_type": "permit_type",
            "valuation": "valuation",
        },
    ),
}
