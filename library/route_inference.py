"""Augment authored ``world_geography_routes`` with internally inferred edges.

Adds:
- Reverse legs for CSV rows flagged ``bidirectional`` when the reciprocal row was omitted.
- Sparse ``land`` bridges so each continent's land graph is connected (authored land edges only),
  so interior regions are never isolated from the rest of their continent unless intentional
  sea-only access is the only author path.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteEdge:
    from_region_id: str
    to_region_id: str
    route_type: str
    friction: float
    inferred: bool


def _truthy(cell: object) -> bool:
    s = str(cell if cell is not None else "").strip().lower()
    return s in ("1", "true", "yes", "y", "on")


def _normalize_route_type(route_type: str) -> str:
    return route_type.strip().lower()


def _as_friction(cell: object) -> float:
    try:
        return max(0.0, float(cell or 0.0))
    except (TypeError, ValueError):
        return 0.0


def load_route_rows(rows: list) -> list[dict[str, object]]:
    """Normalize sqlite rows/dicts from ``world_geography_routes``."""
    out: list[dict[str, object]] = []
    for r in rows:
        if hasattr(r, "keys"):
            d = dict(r)
        else:
            d = {
                "from_region_id": r[0],
                "to_region_id": r[1],
                "route_type": r[2],
                "friction": r[3],
                "bidirectional": r[4],
            }
        out.append(
            {
                "from_region_id": str(d.get("from_region_id") or "").strip(),
                "to_region_id": str(d.get("to_region_id") or "").strip(),
                "route_type": _normalize_route_type(
                    str(d.get("route_type") or "").strip() or "land"
                ),
                "friction": _as_friction(d.get("friction")),
                "bidirectional": d.get("bidirectional"),
            }
        )
    return out


def _land_pairs_from_maps(
    primary: dict[tuple[str, str, str], float],
    inferred: dict[tuple[str, str, str], float],
) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for (a, b, rt), _ in {**primary, **inferred}.items():
        if rt == "land" and a and b:
            pairs.add(tuple(sorted((a, b))))
    return pairs


def _uf_find(parent: dict[str, str], x: str) -> str:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _uf_union(parent: dict[str, str], a: str, b: str) -> None:
    ra, rb = _uf_find(parent, a), _uf_find(parent, b)
    if ra != rb:
        if ra < rb:
            parent[rb] = ra
        else:
            parent[ra] = rb


def _land_components(
    continent_regions: frozenset[str],
    land_pairs: set[tuple[str, str]],
) -> list[set[str]]:
    parent = {r: r for r in continent_regions}

    def nbr_on_continent(other: str) -> bool:
        return other in continent_regions

    for a, b in land_pairs:
        if not nbr_on_continent(a) or not nbr_on_continent(b):
            continue
        _uf_union(parent, a, b)

    comps: dict[str, set[str]] = defaultdict(set)
    for r in continent_regions:
        comps[_uf_find(parent, r)].add(r)
    # stable order: earliest region id representative
    ordered = sorted(comps.keys())
    return [comps[k] for k in ordered]


def augment_routes_with_inference(
    route_rows: list,
    *,
    region_continent_by_id: dict[str, str],
    bridge_land_friction: float | None = None,
) -> list[RouteEdge]:
    """Return authored + inferred routes. Explicit CSV rows beat inferred duplicates."""
    rows = load_route_rows(route_rows)

    primary: dict[tuple[str, str, str], float] = {}
    inferred: dict[tuple[str, str, str], float] = {}

    for row in rows:
        fro = row["from_region_id"]
        to = row["to_region_id"]
        rt = row["route_type"]
        fr = float(row["friction"])
        if not fro or not to:
            continue
        primary[(fro, to, rt)] = fr

    # 1) Bidirectional symmetry
    for row in rows:
        fro = row["from_region_id"]
        to = row["to_region_id"]
        rt = row["route_type"]
        fr = float(row["friction"])
        if not fro or not to:
            continue
        if _truthy(row.get("bidirectional")):
            rev = (to, fro, rt)
            if rev not in primary:
                inferred[rev] = fr

    max_land = 0.0
    for (_, _, rt), fr in primary.items():
        if rt == "land":
            max_land = max(max_land, fr)
    for (_, _, rt), fr in inferred.items():
        if rt == "land":
            max_land = max(max_land, fr)

    bridge = bridge_land_friction if bridge_land_friction is not None else max(4.0, max_land + 2.0)

    regions_by_continent: dict[str, frozenset[str]] = {}
    continents: dict[str, frozenset[str]] = defaultdict(set)
    for rid, cid in region_continent_by_id.items():
        continents[cid].add(rid)
    for cid, rs in continents.items():
        regions_by_continent[cid] = frozenset(rs)

    # 2) Iteratively bridge land components per continent until connected
    for _, crs in sorted(regions_by_continent.items()):
        if len(crs) < 2:
            continue
        land_pairs_snapshot = _land_pairs_from_maps(primary, inferred).copy()
        while True:
            comps = _land_components(crs, land_pairs_snapshot)
            if len(comps) <= 1:
                break
            c0 = comps[0]
            c1 = comps[1]
            a = min(c0)
            b = min(c1)
            if a == b:
                break
            for kfro, kto in ((a, b), (b, a)):
                key = (kfro, kto, "land")
                if key not in primary and key not in inferred:
                    inferred[key] = bridge
                land_pairs_snapshot.add(tuple(sorted((kfro, kto))))

    edges: list[RouteEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for (fro, to, rt), fr in sorted(primary.items(), key=lambda kv: (kv[0][2], kv[0][0], kv[0][1])):
        edges.append(
            RouteEdge(
                from_region_id=fro,
                to_region_id=to,
                route_type=rt,
                friction=fr,
                inferred=False,
            )
        )
        seen.add((fro, to, rt))
    for (fro, to, rt), fr in sorted(inferred.items(), key=lambda kv: (kv[0][2], kv[0][0], kv[0][1])):
        k = (fro, to, rt)
        if k in seen:
            continue
        edges.append(
            RouteEdge(
                from_region_id=fro,
                to_region_id=to,
                route_type=rt,
                friction=fr,
                inferred=True,
            )
        )
        seen.add(k)

    return edges


def index_routes_by_origin(edges: list[RouteEdge]) -> dict[str, list[RouteEdge]]:
    idx: dict[str, list[RouteEdge]] = defaultdict(list)
    for e in edges:
        idx[e.from_region_id].append(e)
    for k in idx:
        idx[k].sort(key=lambda x: (x.route_type, x.to_region_id))
    return dict(idx)
