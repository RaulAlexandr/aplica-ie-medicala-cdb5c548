"""T03 allowed locations of a catalog service (contract: docs/contracts/t03-locations-v1.md).

The rule is checked only when a T04 performed procedure or a T08 plan item is created (and when a
plan item's location is changed). Records already stored are never re-validated.
"""

from typing import Literal

ANY = "any"
NO_TOOTH = "no_tooth"
TOOTH = "tooth"
TOOTH_SURFACE = "tooth_surface"
ALLOWED_LOCATIONS = (ANY, NO_TOOTH, TOOTH, TOOTH_SURFACE)
AllowedLocation = Literal["any", "no_tooth", "tooth", "tooth_surface"]


def location_violation(rule: str, code: str, tooth: str | None, surface: str | None) -> str | None:
    """Return a clear 422 message when ``tooth``/``surface`` break the service rule, else None."""
    has_surface = bool(surface and surface.strip())
    if rule == NO_TOOTH and (tooth or has_surface):
        return f"Service {code} is recorded without a tooth; remove the tooth and surfaces"
    if rule == TOOTH and not tooth:
        return f"Service {code} requires an FDI tooth"
    if rule == TOOTH_SURFACE and (not tooth or not has_surface):
        return f"Service {code} requires an FDI tooth and at least one surface"
    return None
