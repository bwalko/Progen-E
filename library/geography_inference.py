"""Deterministic geography inference from plain-English keywords (plus biome/terrain cues).

Optional plug-in backend can replace the default lexicon-driven inference for LLM or custom tools.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from library.random_traits import _as_int


def tokenize_geography_text(*parts: str) -> frozenset[str]:
    """Split on comma/semicolon; normalize to lowercase underscore-free tokens (phrase words)."""
    raw = " ".join(p for p in parts if p)
    raw = raw.replace(";", " ").replace(",", " ")
    raw = re.sub(r"[^\w\s-]", " ", raw, flags=re.UNICODE)
    tokens: set[str] = set()
    for w in raw.lower().split():
        w = w.strip("-_")
        if len(w) >= 2:
            tokens.add(w)
    # canonical synonyms expand
    out: set[str] = set()
    for t in tokens:
        out.add(_SYNONYM.get(t, t))
    return frozenset(out)


_SYNONYM: dict[str, str] = {
    "humid": "wet",
    "rainy": "wet",
    "moist": "wet",
    "parched": "arid",
    "desiccated": "arid",
    "seaward": "coastal",
    "littoral": "coastal",
    "midlatitude": "midlatitude",
    "sub-arctic": "subarctic",
    "boreal": "taiga",
}


@dataclass(frozen=True)
class ContinentPhysics:
    base_elev_m: float
    precip_avg_mm: float
    resource_idx: float


@dataclass(frozen=True)
class RegionEnvironment:
    region_id: str
    biome_type: str
    local_elev_m: float
    hydro_idx: float
    fertility: float
    ruggedness: float
    drainage_to: str
    poly_hull: str


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def default_infer_continent(
    *,
    world: str,
    continent_id: str,
    continent_name: str,
    keywords: str,
) -> ContinentPhysics:
    toks = tokenize_geography_text(keywords, continent_name)
    base = 320.0
    precip = 720.0
    res = 0.55

    if "high" in toks and "latitude" in toks:
        base += 120.0
        precip += 40.0
    if "cold" in toks:
        base += 40.0
    if "maritime" in toks or "atlantic" in toks:
        precip += 140.0
        base -= 30.0
    if "subtropical" in toks or "tropical" in toks:
        base -= 60.0
        precip -= 80.0
    if "aridity" in toks or "arid" in toks:
        precip -= 280.0
    if "monsoon" in toks:
        precip += 160.0
    if "shield" in toks or "craton" in toks:
        base += 90.0
        res += 0.12
    if "moderate" in toks and "rainfall" in toks:
        precip += 80.0
    if "humid" in toks or "wet" in toks:
        precip += 100.0

    base = _clamp(base, 50.0, 900.0)
    precip = _clamp(precip, 120.0, 2400.0)
    res = _clamp(res, 0.15, 0.95)
    _ = world
    _ = continent_id
    return ContinentPhysics(
        base_elev_m=base, precip_avg_mm=precip, resource_idx=res
    )


def _terrain_biome_offset(biome: str, terrain: str, toks: frozenset[str]) -> tuple[float, float, float]:
    """Returns (local_elev_offset, ruggedness seed, hydro seed)."""
    b = (biome or "").lower()
    t = (terrain or "").lower()
    elev = 40.0
    rugged = 0.35
    hydro = 0.55

    if "coast" in t or "coastal" in b:
        elev = -45.0
        hydro = 0.82
        rugged = 0.15
    elif "highland" in t or "mountain" in t:
        elev = 280.0
        rugged = 0.88
    elif "hill" in t:
        elev = 160.0
        rugged = 0.55
    elif "plateau" in t:
        elev = 220.0
        rugged = 0.62
        hydro = 0.35
    elif "plain" in t:
        elev = 25.0
        rugged = 0.22
    elif "river" in t or "riverland" in t:
        elev = -15.0
        hydro = 0.88
        rugged = 0.28
    elif "forest" in t:
        elev = 80.0
        rugged = 0.38
        hydro = 0.58

    if "arid" in b:
        hydro -= 0.35
    if "taiga" in b:
        hydro -= 0.08
        elev += 20.0
    if "wet" in toks or "flood" in toks or "alluvial" in toks:
        hydro += 0.15
    if "wind" in toks or "rocky" in toks or "stone" in toks:
        rugged += 0.12

    hydro = _clamp(hydro, 0.05, 0.98)
    rugged = _clamp(rugged, 0.05, 0.98)
    return elev, rugged, hydro


def _koppen_from_biome(biome: str, terrain: str, hydro: float) -> str:
    b = (biome or "").lower()
    t = (terrain or "").lower()
    if "arid" in b:
        return "BWh" if hydro < 0.35 else "BSh"
    if "taiga" in b:
        return "Dfc"
    if "subtropical" in b:
        if "river" in t or hydro > 0.75:
            return "Cwa"
        return "Cfa"
    if "coast" in t or "coastal" in b:
        return "Cfb"
    if "temperate" in b:
        return "Cfb"
    return "Cfb"


def _fertility_from_tokens(toks: frozenset[str], terrain: str, biome: str) -> float:
    f = 0.5
    t = (terrain or "").lower()
    b = (biome or "").lower()
    if "alluvial" in toks or "silt" in toks or "breadbasket" in toks:
        f += 0.25
    if "plain" in t or "river" in t:
        f += 0.15
    if "rocky" in toks or "acidic" in toks or "thin" in toks:
        f -= 0.2
    if "arid" in b:
        f -= 0.25
    if "humid" in toks and "forest" in t:
        f -= 0.05
    return _clamp(f, 0.05, 0.95)


def default_infer_region(
    *,
    world: str,
    continent_id: str,
    region_id: str,
    region_name: str,
    biome: str,
    terrain: str,
    carrying_capacity: int,
    keywords: str,
    continent: ContinentPhysics,
) -> RegionEnvironment:
    toks = tokenize_geography_text(keywords, region_name, biome, terrain)
    elev_off, rugged, hydro = _terrain_biome_offset(biome, terrain, toks)

    # Keyword nudges
    if "harbor" in toks or "bay" in toks or "fjord" in toks:
        hydro += 0.08
    if "sun" in toks and "baked" in toks:
        hydro -= 0.18
        rugged += 0.1
    if "seasonal" in toks and "flood" in toks:
        hydro += 0.12

    hydro = _clamp(hydro, 0.05, 0.98)
    rugged = _clamp(rugged, 0.05, 0.98)
    kop = _koppen_from_biome(biome, terrain, hydro)
    fert = _fertility_from_tokens(toks, terrain, biome)
    cap = max(1, _as_int(carrying_capacity, 1))
    fert = _clamp(fert + min(0.12, cap / 200000.0), 0.05, 0.95)

    hull = synthetic_poly_hull(world, continent_id, region_id)
    _ = world
    return RegionEnvironment(
        region_id=region_id,
        biome_type=kop,
        local_elev_m=elev_off,
        hydro_idx=hydro,
        fertility=fert,
        ruggedness=rugged,
        drainage_to="",  # filled by DAG pass
        poly_hull=hull,
    )


def synthetic_poly_hull(world: str, continent_id: str, region_id: str) -> str:
    """Stable quadrilateral ``lat|lon;…`` for point-in-polygon tests (not real cartography)."""
    h = hashlib.sha256(
        f"{world}:{continent_id}:{region_id}".encode("utf-8")
    ).hexdigest()
    lat0 = 10.0 + (int(h[0:8], 16) % 700000) / 100000.0
    lon0 = -60.0 + (int(h[8:16], 16) % 900000) / 100000.0
    d = 0.35 + (int(h[16:20], 16) % 500) / 5000.0
    pts = [
        (lat0, lon0),
        (lat0 + d, lon0),
        (lat0 + d * 0.7, lon0 + d),
        (lat0, lon0 + d * 0.85),
    ]
    return ";".join(f"{a:.4f}|{b:.4f}" for a, b in pts)


def assign_drainage_edges(
    regions: list[dict[str, Any]],
    continent_physics: dict[str, ContinentPhysics],
    environments: dict[str, RegionEnvironment],
) -> dict[str, RegionEnvironment]:
    """Order regions by inferred surface elevation (high → low); chain drainage_to."""
    by_c: dict[str, list[str]] = {}
    for row in regions:
        cid = str(row["continent_id"] or "").strip()
        rid = str(row["region_id"] or "").strip()
        by_c.setdefault(cid, []).append(rid)

    out = dict(environments)
    for cid, rids in by_c.items():
        cphys = continent_physics.get(cid)
        if cphys is None:
            continue

        def surface_elev(rid: str) -> float:
            env = out[rid]
            return cphys.base_elev_m + env.local_elev_m

        def is_coastal(rid: str) -> bool:
            row = next(r for r in regions if str(r["region_id"]).strip() == rid)
            b = str(row.get("biome") or "").lower()
            t = str(row.get("terrain") or "").lower()
            return "coast" in t or "coastal" in b

        ordered = sorted(
            rids,
            key=lambda r: (-surface_elev(r), is_coastal(r), r),
        )
        # Highest upstream first; drain toward lower neighbors in list order
        for i, rid in enumerate(ordered):
            env = out[rid]
            if i + 1 < len(ordered):
                nxt = ordered[i + 1]
                out[rid] = RegionEnvironment(
                    region_id=env.region_id,
                    biome_type=env.biome_type,
                    local_elev_m=env.local_elev_m,
                    hydro_idx=env.hydro_idx,
                    fertility=env.fertility,
                    ruggedness=env.ruggedness,
                    drainage_to=nxt,
                    poly_hull=env.poly_hull,
                )
            else:
                out[rid] = RegionEnvironment(
                    region_id=env.region_id,
                    biome_type=env.biome_type,
                    local_elev_m=env.local_elev_m,
                    hydro_idx=env.hydro_idx,
                    fertility=env.fertility,
                    ruggedness=env.ruggedness,
                    drainage_to="",
                    poly_hull=env.poly_hull,
                )
    return out


@runtime_checkable
class GeographyInferenceBackend(Protocol):
    def infer_continent(
        self,
        *,
        world: str,
        continent_id: str,
        continent_name: str,
        keywords: str,
    ) -> ContinentPhysics:
        ...

    def infer_region(
        self,
        *,
        world: str,
        continent_id: str,
        region_id: str,
        region_name: str,
        biome: str,
        terrain: str,
        carrying_capacity: int,
        keywords: str,
        continent: ContinentPhysics,
    ) -> RegionEnvironment:
        ...


class DefaultGeographyInference:
    """Lexicon-driven deterministic inference."""

    def infer_continent(
        self,
        *,
        world: str,
        continent_id: str,
        continent_name: str,
        keywords: str,
    ) -> ContinentPhysics:
        return default_infer_continent(
            world=world,
            continent_id=continent_id,
            continent_name=continent_name,
            keywords=keywords,
        )

    def infer_region(
        self,
        *,
        world: str,
        continent_id: str,
        region_id: str,
        region_name: str,
        biome: str,
        terrain: str,
        carrying_capacity: int,
        keywords: str,
        continent: ContinentPhysics,
    ) -> RegionEnvironment:
        return default_infer_region(
            world=world,
            continent_id=continent_id,
            region_id=region_id,
            region_name=region_name,
            biome=biome,
            terrain=terrain,
            carrying_capacity=carrying_capacity,
            keywords=keywords,
            continent=continent,
        )


_default_backend: GeographyInferenceBackend = DefaultGeographyInference()
_custom_backend: GeographyInferenceBackend | None = None


def set_geography_inference_backend(backend: GeographyInferenceBackend | None) -> None:
    """Replace inference (e.g. LLM-backed). ``None`` restores the built-in lexicon backend."""
    global _custom_backend
    _custom_backend = backend


def get_geography_inference_backend() -> GeographyInferenceBackend:
    return _custom_backend if _custom_backend is not None else _default_backend


def build_world_inference(
    *,
    world: str,
    continent_rows: list[dict[str, Any]],
    region_rows: list[dict[str, Any]],
) -> tuple[dict[str, ContinentPhysics], dict[str, RegionEnvironment]]:
    """Run backend over raw SQLite-like row dicts; returns physics + environment maps keyed by id."""
    backend = get_geography_inference_backend()
    cphys: dict[str, ContinentPhysics] = {}
    for row in continent_rows:
        cid = str(row["continent_id"] or "").strip()
        cphys[cid] = backend.infer_continent(
            world=world,
            continent_id=cid,
            continent_name=str(row.get("continent_name") or ""),
            keywords=str(row.get("keywords") or ""),
        )

    envs: dict[str, RegionEnvironment] = {}
    for row in region_rows:
        rid = str(row["region_id"] or "").strip()
        cid = str(row["continent_id"] or "").strip()
        cp = cphys.get(cid)
        if cp is None:
            continue
        envs[rid] = backend.infer_region(
            world=world,
            continent_id=cid,
            region_id=rid,
            region_name=str(row.get("region_name") or ""),
            biome=str(row.get("biome") or ""),
            terrain=str(row.get("terrain") or ""),
            carrying_capacity=int(row.get("carrying_capacity") or 0),
            keywords=str(row.get("keywords") or ""),
            continent=cp,
        )

    envs = assign_drainage_edges(region_rows, cphys, envs)
    return cphys, envs
