"""Runtime geographic labels for regions and polities (lazy naming)."""

from __future__ import annotations

import random
import re
from typing import TYPE_CHECKING

from library.geography import population_scale_for_world

if TYPE_CHECKING:
    from library.geography import Region

# Real-world headcount floor before a place "deserves" a generated name.
NAMING_MIN_POPULATION_REAL = 50

# Dominant settlement must hold this fraction of regional alive population to rename the region.
REGION_RENAME_DOMINANT_CITY_RATIO = 0.55

# Second-largest settlement must be at most half the dominant's alive count (tie-break).
REGION_RENAME_DOMINANT_CITY_SECOND_RATIO = 0.5

# Years the dominant-city condition must fail before reverting a city-takeover label.
REGION_RENAME_DOMINANT_CITY_HYSTERESIS_YEARS = 3

# Tier labels for polity_geographic_label (subset; others title-case the type id).
_TIER_LABEL_BY_TYPE: dict[str, str] = {
    "county": "County",
    "duchy": "Duchy",
    "kingdom": "Kingdom",
    "tribe": "Tribe",
    "city_state": "City",
    "band": "Band",
    "republic": "Republic",
}


def naming_threshold_for_world(world: str, db_path) -> int:
    scale = float(population_scale_for_world(world, db_path=db_path))
    return max(1, int(round(NAMING_MIN_POPULATION_REAL * scale)))


def _tokenize_keywords(keywords: str) -> list[str]:
    s = (keywords or "").strip()
    if not s:
        return []
    parts = re.split(r"[;,]", s)
    return [p.strip() for p in parts if p.strip()]


def _terrain_biome_redundant(terrain_raw: str, biome_raw: str) -> bool:
    """True when terrain and biome are essentially the same word.

    Catches both exact matches (``coast``/``coast``) and substring overlap
    (``coast``/``coastal``, ``tundra``/``polar tundra``) so labels don't end up as
    ``"Coast Coastal"``.
    """
    t = (terrain_raw or "").strip().lower()
    b = (biome_raw or "").strip().lower()
    if not t or not b or t == b:
        return t == b
    return t in b or b in t


def _normalize_label(text: str) -> str:
    return " ".join((text or "").split()).strip()


def region_geographic_label(region: "Region", *, rng_seed: int) -> str:
    """Deterministic-ish label from terrain, biome, and optional keyword fragment.

    Always inserts a space between terrain/biome words; collapses near-duplicates
    (e.g. ``coast``/``coastal``) into a single descriptor; injects keyword fragments
    after ``"The "`` so the output reads ``"The Stormy Highlands"`` rather than
    ``"Stormy The Highlands"``.
    """
    r = random.Random(int(rng_seed) & 0xFFFFFFFF)
    biome_raw = (region.biome or "land").strip().lower().replace("_", " ")
    terrain_raw = (region.terrain or "ground").strip().lower().replace("_", " ")
    biome = biome_raw.title()
    terrain = terrain_raw.title()

    kws = _tokenize_keywords(region.keywords)
    candidate_kws: list[str] = []
    for k in kws:
        first = (k.split()[0] if k else "").strip(".,;")
        if not first or len(first) > 18:
            continue
        low = first.lower()
        # Skip fragments already implied by terrain or biome to avoid stutter
        # (e.g. ``"Coast"`` keyword on a coast/coastal region).
        if low == terrain_raw or low == biome_raw:
            continue
        if low in terrain_raw or low in biome_raw:
            continue
        if terrain_raw in low or biome_raw in low:
            continue
        candidate_kws.append(first.title())
    frag = r.choice(candidate_kws) if candidate_kws else ""

    if _terrain_biome_redundant(terrain_raw, biome_raw):
        unified = terrain if len(terrain) >= len(biome) else biome
        templates = [
            f"The {unified}",
            f"{unified} Reach",
            f"{unified} Marches",
            f"{unified} Lands",
        ]
    else:
        templates = [
            f"The {terrain} {biome}",
            f"{terrain} {biome}",
            f"{biome} {terrain}",
            f"{terrain} {biome} Reach",
            f"{biome} Marches",
            f"The {biome} {terrain}",
        ]
    base = r.choice(templates)
    if frag and r.random() < 0.45:
        if base.startswith("The "):
            base = f"The {frag} {base[4:]}"
        else:
            base = f"{frag} {base}"
    return _normalize_label(base)


# City-takeover templates per culture. Use ``{city}`` for substitution. The default
# pool covers cultures we don't have a specific entry for. Variants are intentionally
# overlapping (e.g. ``Greater {city}`` shows up across pools) so labels don't collide
# on every region but still feel culturally coherent.
_CITY_TAKEOVER_TEMPLATES_BY_CULTURE: dict[str, tuple[str, ...]] = {
    "old english": ("{city}shire", "Greater {city}", "the {city}lands", "{city}"),
    "middle english": ("{city}shire", "Greater {city}", "the {city}lands", "{city}"),
    "modern english": ("{city} County", "Greater {city}", "{city}shire", "{city}"),
    "middle scots": ("{city}shire", "Greater {city}", "the {city} Marches", "{city}"),
    "old high german": (
        "{city}land",
        "Greater {city}",
        "the {city} March",
        "{city}",
    ),
    "middle german": ("{city}land", "Greater {city}", "the {city} March", "{city}"),
    "modern german": ("Greater {city}", "{city}land", "{city}"),
    "middle dutch": ("{city}land", "Land of {city}", "Greater {city}", "{city}"),
    "modern dutch": ("Greater {city}", "Land of {city}", "{city}"),
    "old french": ("Pays de {city}", "Greater {city}", "{city}"),
    "modern french": ("Pays de {city}", "Greater {city}", "{city}"),
    "gaulish": ("Land of {city}", "Greater {city}", "{city}"),
    "cisalpine celtic": ("Land of {city}", "Greater {city}", "{city}"),
    "irish gaelic": ("Tír {city}", "Greater {city}", "{city} Lands", "{city}"),
    "modern irish": ("Greater {city}", "{city} Lands", "{city}"),
    "norse": ("{city}land", "Greater {city}", "{city}"),
    "old norse": ("{city}land", "Greater {city}", "{city}"),
}

_CITY_TAKEOVER_TEMPLATES_DEFAULT: tuple[str, ...] = (
    "Greater {city}",
    "{city} Lands",
    "Land of {city}",
    "{city}",
)


def _city_takeover_template_pool(culture: str | None) -> tuple[str, ...]:
    key = (culture or "").strip().lower()
    if key in _CITY_TAKEOVER_TEMPLATES_BY_CULTURE:
        return _CITY_TAKEOVER_TEMPLATES_BY_CULTURE[key]
    return _CITY_TAKEOVER_TEMPLATES_DEFAULT


def region_label_after_dominant_city(
    settlement_display_name: str,
    *,
    culture: str | None = None,
    rng_seed: int = 0,
) -> str:
    """Region label when a single settlement dominates the region.

    Picks a template from a per-``culture`` pool (e.g. Middle English -> ``{city}shire``,
    Old French -> ``Pays de {city}``) deterministically from ``rng_seed`` so a region
    tends to get the same shape on repeated naming events. Falls back to a generic
    pool (``Greater {city}`` / ``{city} Lands`` / ``Land of {city}`` / just the city)
    when ``culture`` is unknown or empty.
    """
    name = (settlement_display_name or "").strip() or "City"
    pool = _city_takeover_template_pool(culture)
    r = random.Random(int(rng_seed) & 0xFFFFFFFF)
    template = r.choice(pool)
    return template.format(city=name).strip()


def polity_geographic_label(
    polity_type_id: str,
    *,
    region_label: str,
    anchor_settlement_display_name: str | None,
    jurisdiction_grain: str = "region",
) -> str:
    tid = (polity_type_id or "").strip().lower()
    grain = (jurisdiction_grain or "region").strip().lower()
    tier = _TIER_LABEL_BY_TYPE.get(tid) or tid.replace("_", " ").title()
    if grain == "settlement" and tid == "county" and anchor_settlement_display_name:
        return f"County of {anchor_settlement_display_name.strip()}"
    if grain == "settlement" and anchor_settlement_display_name:
        return f"{tier} of {anchor_settlement_display_name.strip()}"
    return f"{tier} of {region_label.strip()}"


def placeholder_region_label(region_id: str) -> str:
    rid = (region_id or "").strip() or "?"
    return f"Unnamed region ({rid})"


def placeholder_polity_name(
    polity_type_id: str,
    *,
    region_id: str | None = None,
    settlement_id: str | None = None,
) -> str:
    tid = (polity_type_id or "polity").strip().lower()
    if tid == "county" and settlement_id:
        return f"Unnamed county at settlement {settlement_id.strip()}"
    if region_id:
        return f"Unnamed {tid} at region {region_id.strip()}"
    return f"Unnamed {tid}"


def is_placeholder_polity_name(name: str, polity_type_id: str) -> bool:
    n = (name or "").strip().lower()
    tid = (polity_type_id or "").strip().lower()
    if n.startswith("unnamed county at settlement"):
        return tid == "county"
    if n.startswith(f"unnamed {tid} at region"):
        return True
    if n.startswith("unnamed ") and " at region" in n:
        return True
    return False
