"""Settlement geography affordances and role inference.

The functions in this module deliberately derive their answers from existing
settlement/local geography state. They do not persist new schema state; callers
can recompute profiles when they need population, economy, or migration bias.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from zlib import crc32
from typing import Any


SETTLEMENT_ROLES: tuple[str, ...] = (
    "major_port_city",
    "market_town",
    "toll_town",
    "fortress_town",
    "extraction_town",
    "caravan_town",
    "farming_village_cluster",
    "pilgrimage_village",
    "monastery",
    "logging_town",
    "shipbuilding_settlement",
    "fishing_reed_village",
    "refuge_settlement",
    "hamlet",
)

_ROLE_BASE_CEILING = {
    "major_port_city": 3.8,
    "market_town": 1.9,
    "toll_town": 1.55,
    "fortress_town": 1.15,
    "extraction_town": 1.25,
    "caravan_town": 1.55,
    "farming_village_cluster": 0.9,
    "pilgrimage_village": 0.85,
    "monastery": 0.48,
    "logging_town": 0.85,
    "shipbuilding_settlement": 1.45,
    "fishing_reed_village": 0.65,
    "refuge_settlement": 0.34,
    "hamlet": 0.26,
}

_FAST_GROWTH_ROLES = {
    "major_port_city",
    "market_town",
    "toll_town",
    "caravan_town",
    "shipbuilding_settlement",
}

_WATER_KINDS = {
    "bay",
    "coast",
    "fishery",
    "ford",
    "harbor",
    "lake",
    "marsh",
    "oasis",
    "river",
    "spring",
    "stream",
    "wadi",
    "well",
}
_TRADE_KINDS = {"bridge", "ford", "harbor", "market", "pass", "road", "coast", "bay"}
_AGRICULTURE_KINDS = {"meadow", "pasture", "orchard", "stream", "river"}
_DEFENSE_KINDS = {"castle", "cliff", "fort", "mountain", "pass", "ridge", "mesa"}
_EXTRACTION_KINDS = {"mine", "quarry", "saltpan", "ore"}
_SACRED_KINDS = {"church", "monastery", "sacred", "sanctuary", "spring"}
_FOREST_KINDS = {"clearing", "forest", "grove", "wood"}
_WETLAND_FISHING_KINDS = {"bog", "fishery", "lake", "marsh", "reed", "stream", "coast"}


@dataclass(frozen=True)
class SettlementAffordanceProfile:
    settlement_id: str
    region_id: str
    site_slot: int
    selected_role: str
    role_candidates: tuple[tuple[str, float], ...]
    water_access: float
    agricultural_hinterland: float
    trade_connectivity: float
    defense: float
    extraction_resources: float
    sacred_civic_importance: float
    forest_industry: float
    fishing_wetland: float
    climate_stress: float
    terrain_constraint: float
    import_capacity: float
    volatility: float
    population_ceiling_multiplier: float
    migration_pull: float
    large_population_enablers: tuple[str, ...]

    def as_trace_dict(self) -> dict[str, object]:
        return {
            "settlement_id": self.settlement_id,
            "region_id": self.region_id,
            "site_slot": self.site_slot,
            "selected_role": self.selected_role,
            "role_candidates": [
                {"role": role, "weight": round(weight, 5)}
                for role, weight in self.role_candidates
            ],
            "water_access": round(self.water_access, 4),
            "agricultural_hinterland": round(self.agricultural_hinterland, 4),
            "trade_connectivity": round(self.trade_connectivity, 4),
            "defense": round(self.defense, 4),
            "extraction_resources": round(self.extraction_resources, 4),
            "sacred_civic_importance": round(self.sacred_civic_importance, 4),
            "forest_industry": round(self.forest_industry, 4),
            "fishing_wetland": round(self.fishing_wetland, 4),
            "climate_stress": round(self.climate_stress, 4),
            "terrain_constraint": round(self.terrain_constraint, 4),
            "import_capacity": round(self.import_capacity, 4),
            "volatility": round(self.volatility, 4),
            "population_ceiling_multiplier": round(self.population_ceiling_multiplier, 4),
            "migration_pull": round(self.migration_pull, 4),
            "large_population_enablers": list(self.large_population_enablers),
        }


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(float(lo), min(float(hi), float(value)))


def _stable_unit(text: str) -> float:
    return crc32(text.encode("utf-8")) / 0xFFFFFFFF


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    cur: list[str] = []
    for ch in (text or "").lower():
        if ch.isalnum():
            cur.append(ch)
        else:
            if cur:
                out.add("".join(cur))
                cur = []
    if cur:
        out.add("".join(cur))
    return out


def _local_geo(settlement: Any) -> dict[str, Any]:
    raw = getattr(settlement, "local_geography_json", None)
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}


def _feature_kinds(data: dict[str, Any]) -> list[str]:
    kinds: list[str] = []
    for feature in data.get("features") or ():
        if isinstance(feature, dict):
            kind = str(feature.get("kind") or "").strip().lower()
            if kind:
                kinds.append(kind)
    return kinds


def _anchor_kind(data: dict[str, Any], site_slot: int) -> str:
    target = max(0, int(site_slot) - 1)
    anchor_id = ""
    for pin in data.get("settlements") or ():
        if not isinstance(pin, dict):
            continue
        try:
            if int(pin.get("settlement_slot") or 0) != target:
                continue
        except (TypeError, ValueError):
            continue
        anchor_id = str(pin.get("anchor_feature_id") or "").strip()
        hint = str(pin.get("narrative_hint") or "").strip().lower()
        if hint.startswith("near_"):
            return hint[5:]
        break
    if not anchor_id:
        return ""
    for feature in data.get("features") or ():
        if not isinstance(feature, dict):
            continue
        if str(feature.get("feature_id") or "").strip() == anchor_id:
            return str(feature.get("kind") or "").strip().lower()
    return ""


def _region_text(ctx: Any, settlement: Any) -> str:
    rid = str(getattr(settlement, "region_id", "") or "").strip()
    parts = [rid, str(getattr(settlement, "region_display_name", "") or "")]
    region = getattr(ctx, "region_by_id", {}).get(rid) if ctx is not None else None
    if region is None and ctx is not None:
        try:
            from library.geography import get_region

            region = get_region(
                rid,
                world=getattr(ctx, "world", "default"),
                db_path=getattr(ctx, "db_path", None),
            )
        except Exception:
            region = None
    if region is not None:
        parts.extend(
            [
                str(getattr(region, "region_name", "") or ""),
                str(getattr(region, "biome", "") or ""),
                str(getattr(region, "terrain", "") or ""),
                str(getattr(region, "keywords", "") or ""),
            ]
        )
    return " ".join(parts).lower()


def _route_signal(ctx: Any, rid: str, year: int | None) -> tuple[float, bool]:
    if ctx is None or not rid:
        return 0.0, False
    override = getattr(ctx, "settlement_affordance_route_counts", None)
    if isinstance(override, dict):
        raw = override.get(rid, 0)
        try:
            return _clamp(float(raw) / 4.0), False
        except (TypeError, ValueError):
            return 0.0, False
    try:
        from library.geography import list_routes_from

        routes = list_routes_from(
            rid,
            world=getattr(ctx, "world", "default"),
            db_path=getattr(ctx, "db_path", None),
            simulation_year=year,
        )
    except Exception:
        return 0.0, False
    if not routes:
        return 0.0, False
    strength = sum(1.0 / (1.0 + max(0.0, float(getattr(r, "friction", 1.0)))) for r in routes)
    has_sea = any(str(getattr(r, "route_type", "") or "").strip().lower() == "sea" for r in routes)
    return _clamp(strength / 3.0), has_sea


def _government_signal(ctx: Any, settlement: Any) -> float:
    if ctx is None:
        return 0.0
    sid = str(getattr(settlement, "settlement_id", "") or "").strip()
    rid = str(getattr(settlement, "region_id", "") or "").strip()
    score = 0.0
    for pol in getattr(ctx, "gov_polities", {}).values():
        if str(getattr(pol, "status", "active") or "active").lower() != "active":
            continue
        if str(getattr(pol, "capital_settlement_id", "") or "").strip() == sid:
            score = max(score, 0.75)
    for seat in getattr(ctx, "gov_office_seats", {}).values():
        if str(getattr(seat, "scope_settlement_id", "") or "").strip() == sid:
            score = max(score, 0.55)
    for row in getattr(ctx, "gov_territory_rows", ()) or ():
        kind = str(getattr(row, "target_kind", "") or "").strip().lower()
        target = str(getattr(row, "target_id", "") or "").strip()
        if (kind == "settlement" and target == sid) or (kind == "region" and target == rid):
            score = max(score, 0.45)
    return _clamp(score)


def _kind_score(kinds: set[str], wanted: set[str], anchor: str, anchor_bonus: float = 0.24) -> float:
    hits = len(kinds & wanted)
    score = min(0.82, hits * 0.18)
    if anchor in wanted:
        score += anchor_bonus
    return _clamp(score)


def _role_weights(
    *,
    water: float,
    agriculture: float,
    trade: float,
    defense: float,
    extraction: float,
    sacred: float,
    forest: float,
    wetland: float,
    climate: float,
    terrain: float,
    import_capacity: float,
    variation: float,
) -> dict[str, float]:
    return {
        "major_port_city": (water * 0.45 + trade * 0.42 + agriculture * 0.18 + import_capacity * 0.28) ** 1.5,
        "market_town": (trade * 0.60 + agriculture * 0.22 + water * 0.12 + (1.0 - terrain) * 0.08) ** 1.25,
        "toll_town": (trade * 0.48 + defense * 0.24 + water * 0.20) ** 1.35,
        "fortress_town": (defense * 0.62 + trade * 0.18 + terrain * 0.18) ** 1.25,
        "extraction_town": (extraction * 0.68 + import_capacity * 0.22 + defense * 0.08 + climate * 0.08) ** 1.18,
        "caravan_town": (trade * 0.46 + climate * 0.28 + water * 0.24 + import_capacity * 0.16) ** 1.25,
        "farming_village_cluster": (agriculture * 0.70 + water * 0.16 + (1.0 - trade) * 0.10) ** 1.12,
        "pilgrimage_village": (sacred * 0.65 + water * 0.16 + terrain * 0.12) ** 1.16,
        "monastery": (sacred * 0.46 + terrain * 0.24 + forest * 0.12 + (1.0 - trade) * 0.10) ** 1.15,
        "logging_town": (forest * 0.62 + water * 0.20 + trade * 0.16) ** 1.14,
        "shipbuilding_settlement": (forest * 0.38 + water * 0.36 + trade * 0.26) ** 1.28,
        "fishing_reed_village": (wetland * 0.62 + water * 0.26 + (1.0 - trade) * 0.08) ** 1.08,
        "refuge_settlement": (terrain * 0.30 + wetland * 0.22 + defense * 0.24 + (1.0 - trade) * 0.16) ** 1.08,
        "hamlet": 0.20 + (1.0 - max(water, agriculture, trade, sacred, import_capacity)) * 0.52,
    } | {"_variation": variation}


def _choose_role(weights: dict[str, float], key: str) -> tuple[str, tuple[tuple[str, float], ...]]:
    varied: list[tuple[str, float]] = []
    for role in SETTLEMENT_ROLES:
        w = max(0.001, float(weights.get(role, 0.0)))
        jitter = 0.82 + _stable_unit(f"{key}|{role}") * 0.42
        varied.append((role, w * jitter))
    varied.sort(key=lambda item: (-item[1], item[0]))
    total = sum(w for _, w in varied)
    pick = _stable_unit(f"{key}|role-pick") * total
    acc = 0.0
    selected = varied[0][0]
    for role, weight in sorted(varied, key=lambda item: item[0]):
        acc += weight
        if pick <= acc:
            selected = role
            break
    return selected, tuple(varied[:5])


def build_settlement_affordance_profile(
    ctx: Any,
    settlement: Any,
    year: int | None = None,
) -> SettlementAffordanceProfile:
    sid = str(getattr(settlement, "settlement_id", "") or "").strip()
    rid = str(getattr(settlement, "region_id", "") or "").strip()
    slot = max(1, int(getattr(settlement, "site_slot", 1) or 1))
    data = _local_geo(settlement)
    kinds_list = _feature_kinds(data)
    kinds = set(kinds_list)
    anchor = _anchor_kind(data, slot)
    if anchor:
        kinds.add(anchor)
    text = _region_text(ctx, settlement)
    words = _tokens(text)
    route_score, has_sea_route = _route_signal(ctx, rid, year)
    gov = _government_signal(ctx, settlement)
    founding_reason = str(getattr(settlement, "founding_reason", "") or "").strip().lower()

    water = _kind_score(kinds, _WATER_KINDS, anchor)
    if (
        (not kinds or water > 0.05 or bool(kinds & _TRADE_KINDS))
        and words & {"coast", "coastal", "delta", "fjord", "harbor", "river", "shore"}
    ):
        water += 0.22
    if has_sea_route:
        water += 0.10
    water = _clamp(water)

    agriculture = _kind_score(kinds, _AGRICULTURE_KINDS, anchor, 0.16)
    if (
        (not kinds or agriculture > 0.05 or water > 0.20)
        and words & {"fertile", "floodplain", "plain", "plains", "pasture", "orchard", "loam"}
    ):
        agriculture += 0.38
    if words & {"arid", "desert", "salt", "tundra"}:
        agriculture -= 0.22
    agriculture = _clamp(agriculture)

    trade = _kind_score(kinds, _TRADE_KINDS, anchor)
    if not kinds or trade > 0.05 or water > 0.25:
        trade += route_score * 0.45
    if (
        (not kinds or trade > 0.05 or water > 0.25)
        and words & {"caravan", "crossing", "market", "port", "road", "trade"}
    ):
        trade += 0.26
    trade = _clamp(trade)

    defense = _kind_score(kinds, _DEFENSE_KINDS, anchor)
    if words & {"border", "frontier", "highland", "mountain", "pass", "ridge"}:
        defense += 0.28
    defense = _clamp(defense)

    extraction = _kind_score(kinds, _EXTRACTION_KINDS, anchor)
    if words & {"mine", "mining", "ore", "quarry", "salt", "copper", "iron"}:
        extraction += 0.42
    extraction = _clamp(extraction)

    sacred = _kind_score(kinds, _SACRED_KINDS, anchor, 0.18)
    if words & {"abbey", "monastery", "pilgrim", "sacred", "shrine", "temple"}:
        sacred += 0.38
    sacred = _clamp(max(sacred, gov * 0.82))

    forest = _kind_score(kinds, _FOREST_KINDS, anchor)
    if words & {"forest", "wood", "woods", "taiga", "timber"}:
        forest += 0.34
    forest = _clamp(forest)

    wetland = _kind_score(kinds, _WETLAND_FISHING_KINDS, anchor)
    if words & {"bog", "fish", "marsh", "muskeg", "reed", "wetland"}:
        wetland += 0.38
    wetland = _clamp(wetland)

    climate = 0.0
    if words & {"arid", "desert", "dry", "salt", "steppe", "tundra"}:
        climate += 0.50
    if words & {"marsh", "muskeg", "bog"}:
        climate += 0.18
    if water > 0.55 and "oasis" not in kinds:
        climate -= 0.10
    climate = _clamp(climate)

    terrain = 0.0
    if words & {"alps", "cliff", "highland", "mountain", "plateau", "ridge"}:
        terrain += 0.44
    if kinds & {"bog", "marsh", "pass", "ridge", "mountain", "cliff"}:
        terrain += 0.28
    terrain = _clamp(terrain)

    import_capacity = _clamp(trade * 0.55 + water * 0.22 + gov * 0.24 + (0.10 if has_sea_route else 0.0))
    volatility = _clamp(extraction * 0.30 + climate * 0.24 + terrain * 0.16 + max(0.0, 0.40 - agriculture) * 0.26)
    if founding_reason in {"commercial_outpost", "birth_spinoff", "regional_service_village"}:
        volatility = _clamp(volatility + 0.08)

    variation = _stable_unit(f"{rid}|{sid}|{slot}|{founding_reason}")
    weights = _role_weights(
        water=water,
        agriculture=agriculture,
        trade=trade,
        defense=defense,
        extraction=extraction,
        sacred=sacred,
        forest=forest,
        wetland=wetland,
        climate=climate,
        terrain=terrain,
        import_capacity=import_capacity,
        variation=variation,
    )
    selected_role, candidates = _choose_role(weights, f"{rid}|{sid}|{slot}|{year or 0}")

    enablers: list[str] = []
    if water >= 0.58:
        enablers.append("strong_water_access")
    if agriculture >= 0.58:
        enablers.append("strong_agricultural_hinterland")
    if trade >= 0.58:
        enablers.append("major_trade_connectivity")
    if sacred >= 0.62:
        enablers.append("administrative_or_religious_importance")
    if import_capacity >= 0.58:
        enablers.append("import_capacity")
    if extraction >= 0.62 and import_capacity >= 0.42:
        enablers.append("resource_extraction_with_imports")

    enabler_strength = max(water, agriculture, trade, sacred, import_capacity, extraction * import_capacity)
    base = _ROLE_BASE_CEILING.get(selected_role, 0.5)
    ceiling = base * (0.45 + enabler_strength * 1.15)
    if not enablers:
        ceiling = min(ceiling, 0.58)
    if selected_role in {"refuge_settlement", "monastery", "hamlet"}:
        ceiling = min(ceiling, 0.78)
    if selected_role == "extraction_town" and import_capacity < 0.35:
        ceiling = min(ceiling, 0.68)
    if founding_reason == "birth_spinoff":
        ceiling *= 0.78
    elif founding_reason == "regional_service_village":
        ceiling *= 0.70
    if str(getattr(settlement, "autonomy_level", "") or "").strip().lower() == "district":
        ceiling *= 0.90
    ceiling *= 0.88 + variation * 0.28
    ceiling = _clamp(ceiling, 0.08, 5.0)

    migration_pull = _clamp(
        ceiling / 4.0
        + trade * 0.24
        + import_capacity * 0.18
        + water * 0.10
        + sacred * 0.06
        - volatility * 0.12
    )

    return SettlementAffordanceProfile(
        settlement_id=sid,
        region_id=rid,
        site_slot=slot,
        selected_role=selected_role,
        role_candidates=candidates,
        water_access=water,
        agricultural_hinterland=agriculture,
        trade_connectivity=trade,
        defense=defense,
        extraction_resources=extraction,
        sacred_civic_importance=sacred,
        forest_industry=forest,
        fishing_wetland=wetland,
        climate_stress=climate,
        terrain_constraint=terrain,
        import_capacity=import_capacity,
        volatility=volatility,
        population_ceiling_multiplier=ceiling,
        migration_pull=migration_pull,
        large_population_enablers=tuple(enablers),
    )


def _active_status(value: Any) -> bool:
    return str(getattr(value, "status", "active") or "active").strip().lower() == "active"


def _government_cache_signature(ctx: Any, sid: str, rid: str) -> tuple[object, ...]:
    if ctx is None:
        return ()
    capital_hits = tuple(
        sorted(
            str(getattr(pol, "capital_settlement_id", "") or "").strip()
            for pol in getattr(ctx, "gov_polities", {}).values()
            if _active_status(pol)
            and str(getattr(pol, "capital_settlement_id", "") or "").strip() == sid
        )
    )
    seat_hits = tuple(
        sorted(
            str(getattr(seat, "scope_settlement_id", "") or "").strip()
            for seat in getattr(ctx, "gov_office_seats", {}).values()
            if str(getattr(seat, "scope_settlement_id", "") or "").strip() == sid
        )
    )
    territory_hits: list[tuple[str, str]] = []
    for row in getattr(ctx, "gov_territory_rows", ()) or ():
        kind = str(getattr(row, "target_kind", "") or "").strip().lower()
        target = str(getattr(row, "target_id", "") or "").strip()
        if (kind == "settlement" and target == sid) or (kind == "region" and target == rid):
            territory_hits.append((kind, target))
    return (capital_hits, seat_hits, tuple(sorted(territory_hits)))


def _route_cache_signature(ctx: Any, rid: str, year: int | None) -> tuple[object, ...]:
    if ctx is None:
        return ()
    override = getattr(ctx, "settlement_affordance_route_counts", None)
    if isinstance(override, dict):
        return ("override", rid, override.get(rid, 0))
    return (
        "db",
        getattr(ctx, "world", "default"),
        str(getattr(ctx, "db_path", "") or ""),
        int(year or 0),
    )


def settlement_affordance_cache_key(
    ctx: Any,
    settlement: Any,
    *,
    year: int | None = None,
) -> tuple[object, ...]:
    sid = str(getattr(settlement, "settlement_id", "") or "").strip()
    rid = str(getattr(settlement, "region_id", "") or "").strip()
    slot = max(1, int(getattr(settlement, "site_slot", 1) or 1))
    return (
        sid,
        rid,
        slot,
        str(getattr(settlement, "local_geography_json", "") or ""),
        str(getattr(settlement, "founding_reason", "") or "").strip().lower(),
        str(getattr(settlement, "autonomy_level", "") or "").strip().lower(),
        int(year or 0),
        _route_cache_signature(ctx, rid, year),
        _government_cache_signature(ctx, sid, rid),
    )


def _store_cached_profile(
    settlement: Any,
    key: tuple[object, ...],
    profile: SettlementAffordanceProfile,
) -> None:
    try:
        setattr(settlement, "_affordance_cache_key", key)
        setattr(settlement, "_affordance_profile", profile)
        setattr(settlement, "affordance_selected_role", profile.selected_role)
        setattr(
            settlement,
            "affordance_secondary_roles",
            tuple(role for role, _weight in profile.role_candidates if role != profile.selected_role),
        )
        setattr(
            settlement,
            "affordance_population_ceiling_multiplier",
            profile.population_ceiling_multiplier,
        )
        setattr(settlement, "affordance_migration_pull", profile.migration_pull)
        setattr(
            settlement,
            "affordance_large_population_enablers",
            tuple(profile.large_population_enablers),
        )
    except Exception:
        pass


def cached_settlement_affordance_profile(
    ctx: Any,
    settlement: Any,
    year: int | None = None,
) -> SettlementAffordanceProfile:
    """Return a per-settlement/year affordance profile without repeated route work."""
    key = settlement_affordance_cache_key(ctx, settlement, year=year)
    cached_key = getattr(settlement, "_affordance_cache_key", None)
    cached_profile = getattr(settlement, "_affordance_profile", None)
    if cached_key == key and isinstance(cached_profile, SettlementAffordanceProfile):
        return cached_profile
    profile = build_settlement_affordance_profile(ctx, settlement, year=year)
    _store_cached_profile(settlement, key, profile)
    return profile


def growth_invariant_cap(profile: SettlementAffordanceProfile, target_count: int) -> int:
    """Cap unexplained large settlement populations."""
    target = max(0, int(target_count))
    if profile.large_population_enablers:
        return target
    core_strength = max(
        profile.water_access,
        profile.agricultural_hinterland,
        profile.trade_connectivity,
        profile.sacred_civic_importance,
        profile.import_capacity,
        profile.extraction_resources,
    )
    if core_strength < 0.35:
        return min(target, 48)
    role_caps = {
        "hamlet": 48,
        "refuge_settlement": 64,
        "fishing_reed_village": 72,
        "farming_village_cluster": 96,
        "monastery": 120,
        "pilgrimage_village": 140,
    }
    if profile.selected_role in role_caps:
        return min(target, role_caps[profile.selected_role])
    if target <= 300:
        return target
    enabler_strength = max(
        profile.water_access,
        profile.agricultural_hinterland,
        profile.trade_connectivity,
        profile.sacred_civic_importance,
        profile.import_capacity,
        profile.extraction_resources * profile.import_capacity,
    )
    soft_cap = int(round(120 + enabler_strength * 260 + profile.population_ceiling_multiplier * 90))
    return min(target, max(40, soft_cap))


def regional_service_settlement_target(region_cap: int, region_population: int) -> int:
    """Approximate how many ordinary civic settlements a region can productively host."""
    cap = max(1, int(region_cap))
    pop = max(0, int(region_population))
    if cap < 300 or pop < 90:
        return 1
    max_by_cap = min(6, max(1, 1 + cap // 5_000))
    service_pop = max(160, cap // max(1, max_by_cap))
    return min(max_by_cap, max(1, 1 + pop // service_pop))


def settlement_role_soft_cap(
    profile: SettlementAffordanceProfile,
    *,
    region_cap: int,
) -> int:
    """Soft mixed-population cap used to keep minor niches village-sized."""
    cap = max(1, int(region_cap))
    base = int(round(cap * 0.18 * profile.population_ceiling_multiplier))
    role_caps = {
        "hamlet": 70,
        "refuge_settlement": 90,
        "fishing_reed_village": 120,
        "farming_village_cluster": 160,
        "monastery": 180,
        "pilgrimage_village": 220,
        "logging_town": 260,
    }
    if profile.selected_role in role_caps:
        base = min(base, role_caps[profile.selected_role])
    if profile.selected_role == "extraction_town" and profile.import_capacity < 0.35:
        base = min(base, 220)
    return max(24, base)


def young_satellite_viability_floor(
    profile: SettlementAffordanceProfile,
    settlement: Any,
    *,
    year: int,
) -> int:
    """Small non-detailed floor so fresh villages exist without becoming towns."""
    slot = max(1, int(getattr(settlement, "site_slot", 1) or 1))
    founded = getattr(settlement, "founded_sim_year", None)
    if slot <= 1 or founded is None:
        return 0
    age = max(0, int(year) - int(founded))
    if age > 6:
        return 0
    reason = str(getattr(settlement, "founding_reason", "") or "").strip().lower()
    if reason == "regional_service_village":
        base = 14 + min(18, age * 3)
    elif reason == "birth_spinoff":
        base = 10 + min(15, age * 3)
    else:
        return 0
    if profile.selected_role in {"farming_village_cluster", "logging_town", "fishing_reed_village"}:
        base += 8
    elif profile.selected_role in {"monastery", "refuge_settlement", "hamlet"}:
        base -= 3
    return max(6, min(42, base))


def new_settlement_backfill_cap(
    profile: SettlementAffordanceProfile,
    settlement: Any,
    *,
    year: int,
    detailed_alive: int = 0,
) -> int | None:
    """Role-aware cap for young non-primary settlements.

    ``None`` means normal allocation is allowed. Primary sites are not capped here
    because they bootstrap the region.
    """
    slot = max(1, int(getattr(settlement, "site_slot", 1) or 1))
    founded = getattr(settlement, "founded_sim_year", None)
    if slot <= 1 or founded is None:
        return None
    age = max(0, int(year) - int(founded))
    if age >= 8:
        return None
    detail = max(0, int(detailed_alive))
    first_year = 8 + detail * 3
    if profile.selected_role in _FAST_GROWTH_ROLES:
        role_cap = 42 + age * 18 + detail * 4
    elif profile.selected_role in {"fortress_town", "extraction_town"}:
        role_cap = 34 + age * 12 + detail * 3
    elif profile.selected_role in {"refuge_settlement", "monastery", "hamlet"}:
        role_cap = 12 + age * 4 + detail * 2
    else:
        role_cap = 22 + age * 8 + detail * 3
    if str(getattr(settlement, "founding_reason", "") or "").strip().lower() == "regional_service_village":
        role_cap = min(role_cap, 26 + age * 5 + detail * 2)
    if age == 0:
        return min(49, max(1, first_year, min(role_cap, 49)))
    return max(1, role_cap)
