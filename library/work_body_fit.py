"""Era/tool aware body-demand scoring for jobs and force-backed authority."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from library.mind_body import work_trait_values
from library.random_traits import DEFAULT_DB_PATH

ERA_TOOL_LEVELING_01: dict[str, float] = {
    "prehistoric": 0.0,
    "bronze_age": 0.05,
    "iron_age": 0.08,
    "medieval": 0.15,
    "modern": 0.65,
}

MALE_RAW_STRENGTH_PRIOR_01 = 0.08


@dataclass(frozen=True)
class BodyDemandFit:
    body_power_01: float
    effective_physical_demand_01: float
    physical_demand_multiplier: float
    era_tool_leveling_01: float
    magic_leveling_01: float


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _clamp01(value: float) -> float:
    return _clamp(value, 0.0, 1.0)


def era_tool_leveling_01(era: str | None) -> float:
    """Physical-demand leveling from historical tools/infrastructure."""
    key = (era or "").strip().lower()
    return float(ERA_TOOL_LEVELING_01.get(key, 0.0))


def _person_trait_values(person, trait_values: Mapping[str, float] | None) -> Mapping[str, float]:
    if trait_values is not None:
        return trait_values
    try:
        return work_trait_values(person)
    except (AttributeError, TypeError, ValueError):
        return {}


def body_power_01(person, trait_values: Mapping[str, float] | None = None) -> float:
    """Near-ideal physical condition plus a small male raw-strength prior.

    The signed ``physical`` value is centered at the normal/healthy body. Frailty
    below center hurts body-demand work more than above-center excess, because
    hard labor and force roles need baseline power/endurance more than perfect
    overall fitness.
    """
    traits = _person_trait_values(person, trait_values)
    try:
        physical = float(traits.get("physical", 0.0))
    except (TypeError, ValueError):
        physical = 0.0
    frailty = max(0.0, -physical) / 100.0
    excess = max(0.0, physical) / 100.0
    physical_fit = _clamp01(1.0 - frailty * 0.82 - excess * 0.34)
    gender = str(getattr(person, "gender", "") or "").strip().lower()
    sex_prior = MALE_RAW_STRENGTH_PRIOR_01 if gender == "male" else 0.0
    return _clamp01(0.08 + physical_fit * 0.84 + sex_prior)


def effective_physical_demand_01(
    physical_demand_01: float,
    *,
    era: str | None,
    leveling_affinity_01: float = 0.5,
    magic_leveling_01: float = 0.0,
) -> float:
    demand = _clamp01(float(physical_demand_01 or 0.0))
    affinity = _clamp01(float(leveling_affinity_01 or 0.0))
    leveling = max(era_tool_leveling_01(era), _clamp01(float(magic_leveling_01 or 0.0)))
    return _clamp01(demand * (1.0 - leveling * affinity))


def physical_demand_multiplier_from_values(
    *,
    body_power: float,
    effective_demand: float,
    floor: float = 0.12,
) -> float:
    """Soft multiplier for work whose body demands exceed the person's capacity."""
    demand = _clamp01(effective_demand)
    power = _clamp01(body_power)
    if demand <= 0.01:
        return 1.0
    if power >= demand:
        reserve = min(1.0, power - demand)
        return _clamp(1.0 + reserve * demand * 0.18, max(0.0, floor), 1.10)
    gap = demand - power
    severity = 0.75 + 0.55 * demand
    return _clamp(1.0 - gap * severity, max(0.0, floor), 1.10)


def body_demand_fit_for_person(
    person,
    *,
    physical_demand_01: float,
    leveling_affinity_01: float = 0.5,
    era: str | None,
    magic_leveling_01: float = 0.0,
    trait_values: Mapping[str, float] | None = None,
    floor: float = 0.12,
) -> BodyDemandFit:
    power = body_power_01(person, trait_values)
    effective = effective_physical_demand_01(
        physical_demand_01,
        era=era,
        leveling_affinity_01=leveling_affinity_01,
        magic_leveling_01=magic_leveling_01,
    )
    return BodyDemandFit(
        body_power_01=power,
        effective_physical_demand_01=effective,
        physical_demand_multiplier=physical_demand_multiplier_from_values(
            body_power=power,
            effective_demand=effective,
            floor=floor,
        ),
        era_tool_leveling_01=era_tool_leveling_01(era),
        magic_leveling_01=_clamp01(float(magic_leveling_01 or 0.0)),
    )


def authority_force_fit_from_body_power(
    *,
    body_power: float,
    force_authority_01: float,
    era: str | None,
    magic_leveling_01: float = 0.0,
) -> BodyDemandFit:
    effective = effective_physical_demand_01(
        force_authority_01,
        era=era,
        leveling_affinity_01=0.35,
        magic_leveling_01=magic_leveling_01,
    )
    return BodyDemandFit(
        body_power_01=_clamp01(body_power),
        effective_physical_demand_01=effective,
        physical_demand_multiplier=physical_demand_multiplier_from_values(
            body_power=body_power,
            effective_demand=effective,
            floor=0.30,
        ),
        era_tool_leveling_01=era_tool_leveling_01(era),
        magic_leveling_01=_clamp01(float(magic_leveling_01 or 0.0)),
    )


def authority_force_fit_for_person(
    person,
    *,
    force_authority_01: float,
    era: str | None,
    magic_leveling_01: float = 0.0,
    trait_values: Mapping[str, float] | None = None,
) -> BodyDemandFit:
    return authority_force_fit_from_body_power(
        body_power=body_power_01(person, trait_values),
        force_authority_01=force_authority_01,
        era=era,
        magic_leveling_01=magic_leveling_01,
    )


@lru_cache(maxsize=64)
def _magic_physical_leveling_cached(
    world: str,
    db_path_s: str,
    db_mtime_ns: int,
) -> float:
    path = Path(db_path_s)
    if not path.is_file():
        return 0.0
    with closing(sqlite3.connect(str(path))) as conn:
        conn.row_factory = sqlite3.Row
        try:
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(world_start)").fetchall()
            }
        except sqlite3.OperationalError:
            return 0.0
        if "magic_physical_leveling_01" not in columns:
            return 0.0
        row = conn.execute(
            """
            SELECT magic_physical_leveling_01
            FROM world_start
            WHERE world = ?
            LIMIT 1
            """,
            (world,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT magic_physical_leveling_01
                FROM world_start
                WHERE world = '*'
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return 0.0
        try:
            return _clamp01(float(row["magic_physical_leveling_01"] or 0.0))
        except (TypeError, ValueError):
            return 0.0


def magic_physical_leveling_for_world(
    world: str | None = "default", db_path: Path | str | None = None
) -> float:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    try:
        mtime = resolved.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return _magic_physical_leveling_cached(
        (world or "default").strip() or "default",
        str(resolved),
        int(mtime),
    )


def clear_work_body_fit_cache() -> None:
    _magic_physical_leveling_cached.cache_clear()
