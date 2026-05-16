"""Geography graph helpers for world/continent/region simulation."""

from __future__ import annotations

import math
import random
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from library.geography_inference import (
    ContinentPhysics,
    RegionEnvironment,
    build_world_inference,
)
from library.route_inference import (
    RouteEdge,
    augment_routes_with_inference,
    index_routes_by_origin,
)
from library.random_traits import DEFAULT_DB_PATH, _as_int, _connect

# Future: per-region tile/hex overlays (warfare, dense urban areas). No effect while False.
LOCAL_GEOGRAPHY_ENABLED = False

# Simulation: regional soft cap scales from config ``carrying_capacity`` (1.0 = use CSV as-is).
# This is the default when ``world_start.population_scale`` is missing or invalid; the
# canonical knob is :func:`population_scale_for_world` (one row per world).
CARRYING_CAPACITY_FROM_CONFIG_FRACTION = 0.05

# Cache: (world.strip(), resolved_db_path, mtime_ns) -> population_scale (float in (0, 10]).
_population_scale_cache: dict[tuple[str, str, int], float] = {}


def population_scale_for_world(
    world: str = "default",
    db_path: Path | str | None = None,
) -> float:
    """Return ``world_start.population_scale`` for ``world`` (defaults to module constant).

    A single global multiplier that compresses or expands every "real-world" population
    threshold into simulation alive counts:

    * Region carrying capacities (config ``world_geography_regions.carrying_capacity``)
    * Government tier thresholds (``government_polity_types.min_population_to_form`` and
      ``max_population_before_split``)
    * Settlement leadership thresholds (``government_titles`` /
      :func:`library.simulation_government._ensure_settlement_offices`)

    Defaults to :data:`CARRYING_CAPACITY_FROM_CONFIG_FRACTION` when the ``world_start``
    column or row is missing. Cached per ``(world, db_path, mtime)``.
    """
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    resolved = str(path.resolve())
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    key = (world.strip() or "default", resolved, mtime)
    hit = _population_scale_cache.get(key)
    if hit is not None:
        return hit
    scale = float(CARRYING_CAPACITY_FROM_CONFIG_FRACTION)
    try:
        with closing(_connect(path)) as conn:
            cols = {
                str(r[1])
                for r in conn.execute("PRAGMA table_info(world_start)").fetchall()
            }
            if "population_scale" in cols:
                row = conn.execute(
                    "SELECT population_scale FROM world_start WHERE world = ?",
                    (world.strip(),),
                ).fetchone()
                if row is not None and row["population_scale"] is not None:
                    raw = str(row["population_scale"]).strip()
                    if raw:
                        try:
                            v = float(raw)
                            if v > 0.0:
                                scale = v
                        except ValueError:
                            pass
    except sqlite3.OperationalError:
        pass
    _population_scale_cache[key] = scale
    return scale


def scale_population_threshold(real_world_count: int, scale: float) -> int:
    """Convert a real-world count to the equivalent simulation alive count via ``scale``.

    Returns at least ``1`` so a non-zero CSV threshold never collapses to zero. Used by
    government-tier and settlement-leadership comparisons against ``count_alive_*``.
    """
    n = max(0, int(real_world_count))
    if n <= 0:
        return 0
    return max(1, int(round(n * float(scale))))

_REGION_SELECT = (
    "world, region_id, region_name, continent_id, biome, terrain, carrying_capacity, keywords"
)

_CONTINENT_SELECT = "world, continent_id, continent_name, keywords"

_inference_cache: dict[tuple[str, str, int], tuple[dict[str, ContinentPhysics], dict[str, RegionEnvironment]]] = {}

_route_index_cache: dict[tuple[str, str, int], dict[str, list[RouteEdge]]] = {}


def _as_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    s = str(value).strip()
    if not s:
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _region_from_row(r: sqlite3.Row, *, scale: float | None = None) -> Region:
    raw_cap = max(0, _as_int(r["carrying_capacity"], 0))
    eff_scale = (
        float(scale)
        if scale is not None
        else float(CARRYING_CAPACITY_FROM_CONFIG_FRACTION)
    )
    scaled = int(round(float(raw_cap) * eff_scale))
    return Region(
        world=str(r["world"] or "").strip(),
        region_id=str(r["region_id"] or "").strip(),
        region_name=str(r["region_name"] or "").strip(),
        continent_id=str(r["continent_id"] or "").strip(),
        biome=str(r["biome"] or "").strip(),
        terrain=str(r["terrain"] or "").strip(),
        carrying_capacity=max(0, scaled),
        keywords=str(r["keywords"] or "").strip(),
    )


def _continent_from_row(r: sqlite3.Row) -> Continent:
    return Continent(
        world=str(r["world"] or "").strip(),
        continent_id=str(r["continent_id"] or "").strip(),
        continent_name=str(r["continent_name"] or "").strip(),
        keywords=str(r["keywords"] or "").strip(),
    )


def _rowdict(r: sqlite3.Row) -> dict[str, object]:
    return {str(k): r[k] for k in r.keys()}


def _get_inference_maps(
    world: str, db_path: Path | str | None = None
) -> tuple[dict[str, ContinentPhysics], dict[str, RegionEnvironment]]:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    resolved = str(path.resolve())
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    key = (world.strip(), resolved, mtime)
    hit = _inference_cache.get(key)
    if hit is not None:
        return hit

    with closing(_connect(path)) as conn:
        crows = conn.execute(
            f"""
            SELECT {_CONTINENT_SELECT}
            FROM world_geography_continents
            WHERE world = ?
            """,
            (world.strip(),),
        ).fetchall()
        rrows = conn.execute(
            f"""
            SELECT {_REGION_SELECT}
            FROM world_geography_regions
            WHERE world = ?
            """,
            (world.strip(),),
        ).fetchall()

    cphys, envs = build_world_inference(
        world=world.strip(),
        continent_rows=[_rowdict(r) for r in crows],
        region_rows=[_rowdict(r) for r in rrows],
    )
    _inference_cache[key] = (cphys, envs)
    return cphys, envs


def _get_route_edges_by_origin(
    world: str, db_path: Path | str | None = None
) -> dict[str, list[RouteEdge]]:
    """CSV routes plus engine-inferred reciprocals and continental land bridges."""
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    resolved = str(path.resolve())
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    key = (world.strip(), resolved, mtime)
    hit = _route_index_cache.get(key)
    if hit is not None:
        return hit

    with closing(_connect(path)) as conn:
        route_rows = conn.execute(
            """
            SELECT from_region_id, to_region_id, route_type, friction, bidirectional
            FROM world_geography_routes
            WHERE world = ?
            """,
            (world.strip(),),
        ).fetchall()
        rrows = conn.execute(
            f"""
            SELECT {_REGION_SELECT}
            FROM world_geography_regions
            WHERE world = ?
            """,
            (world.strip(),),
        ).fetchall()

    region_map = {
        str(r["region_id"] or "").strip(): str(r["continent_id"] or "").strip()
        for r in rrows
        if str(r["region_id"] or "").strip()
    }
    augmented = augment_routes_with_inference(
        list(route_rows),
        region_continent_by_id=region_map,
    )
    idx = index_routes_by_origin(augmented)
    _route_index_cache[key] = idx
    return idx


def region_environment(
    region_id: str,
    *,
    world: str = "default",
    db_path: Path | str | None = None,
) -> RegionEnvironment:
    """Engine-inferred hydrology, elevation offsets, drainage, and hull for a region."""
    _, envs = _get_inference_maps(world, db_path)
    rid = region_id.strip()
    env = envs.get(rid)
    if env is None:
        raise LookupError(f"No inferred environment for world={world!r}, region_id={rid!r}")
    return env


def continent_physics(
    continent_id: str,
    *,
    world: str = "default",
    db_path: Path | str | None = None,
) -> ContinentPhysics:
    cphys, _ = _get_inference_maps(world, db_path)
    cid = continent_id.strip()
    phys = cphys.get(cid)
    if phys is None:
        raise LookupError(f"No inferred physics for world={world!r}, continent_id={cid!r}")
    return phys


@dataclass(frozen=True)
class Continent:
    world: str
    continent_id: str
    continent_name: str
    keywords: str


@dataclass(frozen=True)
class Region:
    world: str
    region_id: str
    region_name: str
    continent_id: str
    biome: str
    terrain: str
    carrying_capacity: int
    keywords: str


@dataclass(frozen=True)
class Route:
    from_region_id: str
    to_region_id: str
    route_type: str
    friction: float


@dataclass(frozen=True)
class TravelEraParams:
    """Per-era scaling of route base friction.

    Land and sea follow separate travel-time gauges (see ``world_geography_travel_eras``):
    ``land_friction_multiplier`` is normalized vs a ~70-day / 1000-mile reference (Medieval
    pack horse), ``sea_friction_multiplier`` vs a ~50-day / 1000-mile reference (early
    coastal maritime baseline).
    """

    land_friction_multiplier: float
    sea_friction_multiplier: float
    cross_continent_weight_multiplier: float | None
    disabled_route_types: frozenset[str]


def _parse_disabled_route_types(cell: object) -> frozenset[str]:
    if cell is None:
        return frozenset()
    s = str(cell).strip()
    if not s:
        return frozenset()
    return frozenset(x.strip().lower() for x in s.split(";") if x.strip())


def _era_route_friction_multiplier(era: TravelEraParams, route_type: str) -> float:
    rt = route_type.strip().lower()
    if rt == "sea":
        scale = era.sea_friction_multiplier
    else:
        scale = era.land_friction_multiplier
    return scale if scale > 0.0 else 1.0


def _historical_year_from_simulation_calendar(
    conn: sqlite3.Connection, world: str, simulation_year: int
) -> int:
    """Map simulation calendar year to historical-year scale via ``world_start``."""
    row = conn.execute(
        """
        SELECT start_year, history_equivalent_start_year
        FROM world_start
        WHERE world = ?
        """,
        (world.strip(),),
    ).fetchone()
    if row is None:
        return int(simulation_year)
    sim_start = _as_int(row["start_year"], int(simulation_year))
    hist_equiv = _as_int(row["history_equivalent_start_year"], sim_start)
    return hist_equiv + int(simulation_year) - sim_start


def resolve_travel_era(
    *,
    world: str,
    simulation_year: int | None,
    db_path: Path | str | None = None,
) -> TravelEraParams:
    """Travel modifiers keyed by historical year (same scale as ``historical_mortality_milestones``).

    ``simulation_year`` is converted with ``world_start`` (``history_equivalent_start_year`` /
    ``start_year``) and matched against ``history_year_from`` / ``history_year_to`` in
    ``world_geography_travel_eras``. When ``simulation_year`` is None, returns neutral era.

    Overlapping rows: the row with the largest ``history_year_from`` that still contains the
    historical year wins.
    """
    if simulation_year is None:
        return TravelEraParams(
            land_friction_multiplier=1.0,
            sea_friction_multiplier=1.0,
            cross_continent_weight_multiplier=None,
            disabled_route_types=frozenset(),
        )
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    sy = int(simulation_year)
    with closing(_connect(path)) as conn:
        hy = _historical_year_from_simulation_calendar(conn, world, sy)
        try:
            rows = conn.execute(
                """
                SELECT history_year_from, history_year_to, land_friction_multiplier,
                       sea_friction_multiplier, cross_continent_weight_multiplier, route_types_disabled
                FROM world_geography_travel_eras
                WHERE world = ?
                """,
                (world.strip(),),
            ).fetchall()
        except sqlite3.OperationalError:
            return TravelEraParams(
                land_friction_multiplier=1.0,
                sea_friction_multiplier=1.0,
                cross_continent_weight_multiplier=None,
                disabled_route_types=frozenset(),
            )

    best_from = -10**18
    best: sqlite3.Row | None = None
    for r in rows:
        y_from = _as_int(r["history_year_from"], -10**18)
        y_to_raw = r["history_year_to"]
        y_to_s = str(y_to_raw or "").strip()
        y_to = _as_int(y_to_s, 10**18) if y_to_s else 10**18
        if y_from <= hy <= y_to and y_from >= best_from:
            best_from = y_from
            best = r
    if best is None:
        return TravelEraParams(
            land_friction_multiplier=1.0,
            sea_friction_multiplier=1.0,
            cross_continent_weight_multiplier=None,
            disabled_route_types=frozenset(),
        )
    land_fm = float(best["land_friction_multiplier"] or 1.0)
    if land_fm <= 0.0:
        land_fm = 1.0
    sea_cell = best["sea_friction_multiplier"]
    sea_s = str(sea_cell if sea_cell is not None else "").strip()
    sea_fm = float(sea_cell) if sea_s else land_fm
    if sea_fm <= 0.0:
        sea_fm = land_fm
    cc_cell = best["cross_continent_weight_multiplier"]
    cc_s = str(cc_cell if cc_cell is not None else "").strip()
    cc_mult = float(cc_cell) if cc_s else None
    disabled = _parse_disabled_route_types(best["route_types_disabled"])
    return TravelEraParams(
        land_friction_multiplier=land_fm,
        sea_friction_multiplier=sea_fm,
        cross_continent_weight_multiplier=cc_mult,
        disabled_route_types=disabled,
    )


def _world_exists(conn: sqlite3.Connection, world: str) -> bool:
    row = conn.execute(
        'SELECT 1 FROM world_geography_continents WHERE "world" = ? LIMIT 1',
        (world,),
    ).fetchone()
    return row is not None


def list_continents(
    *,
    world: str = "default",
    db_path: Path | str | None = None,
) -> list[Continent]:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    with closing(_connect(path)) as conn:
        if not _world_exists(conn, world.strip()):
            raise LookupError(f"No geography data for world={world!r}")
        rows = conn.execute(
            f"""
            SELECT {_CONTINENT_SELECT}
            FROM world_geography_continents
            WHERE world = ?
            ORDER BY continent_id
            """,
            (world.strip(),),
        ).fetchall()
    return [_continent_from_row(r) for r in rows]


def get_continent(
    continent_id: str,
    *,
    world: str = "default",
    db_path: Path | str | None = None,
) -> Continent:
    cid = continent_id.strip()
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    with closing(_connect(path)) as conn:
        row = conn.execute(
            f"""
            SELECT {_CONTINENT_SELECT}
            FROM world_geography_continents
            WHERE world = ? AND continent_id = ?
            """,
            (world.strip(), cid),
        ).fetchone()
    if row is None:
        raise LookupError(f"Continent not found for world={world!r}, continent_id={cid!r}")
    return _continent_from_row(row)


def list_regions(
    *,
    world: str = "default",
    continent_id: str | None = None,
    db_path: Path | str | None = None,
) -> list[Region]:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    with closing(_connect(path)) as conn:
        if not _world_exists(conn, world.strip()):
            raise LookupError(f"No geography data for world={world!r}")
        if continent_id is None:
            rows = conn.execute(
                f"""
                SELECT {_REGION_SELECT}
                FROM world_geography_regions
                WHERE world = ?
                ORDER BY continent_id, region_id
                """,
                (world.strip(),),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""
                SELECT {_REGION_SELECT}
                FROM world_geography_regions
                WHERE world = ? AND continent_id = ?
                ORDER BY region_id
                """,
                (world.strip(), continent_id.strip()),
            ).fetchall()
    scale = population_scale_for_world(world, db_path=path)
    return [_region_from_row(r, scale=scale) for r in rows]


def get_region(
    region_id: str,
    *,
    world: str = "default",
    db_path: Path | str | None = None,
) -> Region:
    rid = region_id.strip()
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    with closing(_connect(path)) as conn:
        row = conn.execute(
            f"""
            SELECT {_REGION_SELECT}
            FROM world_geography_regions
            WHERE world = ? AND region_id = ?
            """,
            (world.strip(), rid),
        ).fetchone()
    if row is None:
        raise LookupError(f"Region not found for world={world!r}, region_id={rid!r}")
    scale = population_scale_for_world(world, db_path=path)
    return _region_from_row(row, scale=scale)


def list_routes_from(
    region_id: str,
    *,
    world: str = "default",
    db_path: Path | str | None = None,
    simulation_year: int | None = None,
) -> list[Route]:
    rid = region_id.strip()
    era = resolve_travel_era(
        world=world, simulation_year=simulation_year, db_path=db_path
    )
    idx = _get_route_edges_by_origin(world, db_path)
    edges = idx.get(rid, [])
    out: list[Route] = []
    for e in edges:
        rt = e.route_type.strip().lower()
        if rt in era.disabled_route_types:
            continue
        base = max(0.0, float(e.friction))
        mult = _era_route_friction_multiplier(era, rt)
        friction = max(0.0, base * mult)
        out.append(
            Route(
                from_region_id=e.from_region_id,
                to_region_id=e.to_region_id,
                route_type=e.route_type,
                friction=friction,
            )
        )
    return out


def travel_friction(
    from_region_id: str,
    to_region_id: str,
    *,
    world: str = "default",
    db_path: Path | str | None = None,
    simulation_year: int | None = None,
) -> float | None:
    fr = from_region_id.strip()
    to = to_region_id.strip()
    for route in list_routes_from(
        fr, world=world, db_path=db_path, simulation_year=simulation_year
    ):
        if route.to_region_id == to:
            return route.friction
    return None


def choose_birth_region_id(
    *,
    world: str = "default",
    db_path: Path | str | None = None,
) -> str:
    regions = list_regions(world=world, db_path=db_path)
    if not regions:
        raise LookupError(f"No geography regions found for world={world!r}")
    weights = [max(1, r.carrying_capacity) for r in regions]
    return random.choices(regions, weights=weights, k=1)[0].region_id


def choose_migration_destination(
    *,
    origin_region_id: str,
    world: str = "default",
    db_path: Path | str | None = None,
    cross_continent_weight_fallback: float = 0.08,
    simulation_year: int | None = None,
) -> str:
    """Pick a neighbor region; cross-continent candidates scale weight by the era multiplier (higher = more mobile)."""
    origin = get_region(origin_region_id, world=world, db_path=db_path)
    era = resolve_travel_era(
        world=world, simulation_year=simulation_year, db_path=db_path
    )
    cc_weight = (
        era.cross_continent_weight_multiplier
        if era.cross_continent_weight_multiplier is not None
        else cross_continent_weight_fallback
    )
    routes = list_routes_from(
        origin.region_id,
        world=world,
        db_path=db_path,
        simulation_year=simulation_year,
    )
    if not routes:
        return origin.region_id

    destinations: list[str] = []
    weights: list[float] = []
    for route in routes:
        dst = get_region(route.to_region_id, world=world, db_path=db_path)
        weight = 1.0 / (1.0 + max(0.0, route.friction))
        if dst.continent_id != origin.continent_id:
            weight *= max(0.0, min(1.0, cc_weight))
        destinations.append(dst.region_id)
        weights.append(max(0.0, weight))

    if sum(weights) <= 0:
        return origin.region_id
    return random.choices(destinations, weights=weights, k=1)[0]


def region_connectivity_score(
    region_id: str,
    *,
    world: str = "default",
    db_path: Path | str | None = None,
    simulation_year: int | None = None,
) -> float:
    routes = list_routes_from(
        region_id, world=world, db_path=db_path, simulation_year=simulation_year
    )
    if not routes:
        return 0.0
    return sum(1.0 / (1.0 + max(0.0, route.friction)) for route in routes)


def parse_poly_hull_vertices(poly_hull: str) -> list[tuple[float, float]]:
    """Parse ``poly_hull`` cells formatted as ``lat|lon;lat|lon;…`` into vertices."""
    out: list[tuple[float, float]] = []
    for chunk in (poly_hull or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split("|")
        if len(parts) != 2:
            continue
        lat = _as_float(parts[0].strip(), float("nan"))
        lon = _as_float(parts[1].strip(), float("nan"))
        if math.isnan(lat) or math.isnan(lon):
            continue
        out.append((lat, lon))
    return out


def region_surface_elevation_m(
    region_id: str,
    *,
    world: str = "default",
    db_path: Path | str | None = None,
) -> float:
    """Absolute mean elevation (m): inferred continent base plus regional offset."""
    reg = get_region(region_id, world=world, db_path=db_path)
    cp = continent_physics(reg.continent_id, world=world, db_path=db_path)
    env = region_environment(region_id, world=world, db_path=db_path)
    return cp.base_elev_m + env.local_elev_m


def iter_drainage_downstream(
    region_id: str,
    *,
    world: str = "default",
    db_path: Path | str | None = None,
    max_hops: int = 64,
) -> list[str]:
    """Follow inferred drainage edges toward a sink (empty downstream id)."""
    chain: list[str] = []
    visited: set[str] = set()
    current = region_id.strip()
    for _ in range(max(1, max_hops)):
        if current in visited:
            raise ValueError(f"Drainage cycle involving region_id={current!r}")
        visited.add(current)
        chain.append(current)
        env = region_environment(current, world=world, db_path=db_path)
        nxt = env.drainage_to.strip()
        if not nxt:
            break
        current = nxt
    return chain
