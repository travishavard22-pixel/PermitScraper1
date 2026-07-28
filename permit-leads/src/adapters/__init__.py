"""City adapters."""

from .base import CITY_REGISTRY, CitySource, PermitAdapter, ScrapeContext
from .portal import PortalAdapter
from .houston_webfocus import WebFocusAdapter
from .socrata import SocrataAdapter


def build_adapter(source: CitySource, ctx: ScrapeContext) -> PermitAdapter:
    if source.kind == "socrata":
        return SocrataAdapter(source, ctx)
    if source.kind == "webfocus":
        return WebFocusAdapter(source, ctx)
    if source.kind == "portal":
        return PortalAdapter(source, ctx)
    raise ValueError(f"Unknown source kind: {source.kind}")


__all__ = [
    "CITY_REGISTRY",
    "CitySource",
    "PermitAdapter",
    "ScrapeContext",
    "PortalAdapter",
    "WebFocusAdapter",
    "SocrataAdapter",
    "build_adapter",
]
