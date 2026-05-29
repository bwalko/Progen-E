"""Abstract local geography: category weights from biome/terrain and placement graphs."""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from library.ethnic_proto_placewords import (
    EthnicProtoPlacewordLexicon,
    generate_feature_name,
)
from library.geography import Region
from library.placenames_lexicon import PlacenameLexicon
from library.world_map_geometry import (
    MAP_GEOMETRY_VERSION,
    RegionFeature,
    WorldMapGeometry,
    build_world_map_geometry,
    project_world_point_to_region_footprint,
)


def category_weights_for_region(region: Region) -> dict[str, float]:
    """Relative weights for placename ``Category`` rows (Engineering, Topography, …)."""
    biome = (region.biome or "").lower()
    terrain = (region.terrain or "").lower()

    w = {
        "Engineering": 1.0,
        "Fortification": 0.55,
        "Sacred": 0.45,
        "Status": 0.4,
        "Regional": 0.45,
        "Topography": 2.0,
    }

    if "coast" in terrain or "coastal" in biome or terrain == "coast":
        w["Topography"] *= 1.55
        w["Engineering"] *= 1.15
    if "river" in terrain:
        w["Topography"] *= 1.45
        w["Engineering"] *= 1.05
    if "forest" in terrain:
        w["Topography"] *= 1.35
    if "hill" in terrain or "highland" in terrain or "plateau" in terrain:
        w["Topography"] *= 1.25
        w["Fortification"] *= 1.1
    if "plains" in terrain or terrain == "plains":
        w["Engineering"] *= 1.12
        w["Topography"] *= 1.08
    if "arid" in biome:
        w["Topography"] *= 0.92
        w["Engineering"] *= 1.05

    s = sum(max(0.0, v) for v in w.values())
    if s <= 0:
        return {k: 1.0 / len(w) for k in w}
    return {k: max(0.0, v) / s for k, v in w.items()}


_MEANING_HINT_TO_FEATURE = (
    ("forest", "forest"),
    ("wood", "forest"),
    ("grove", "forest"),
    ("oak", "forest"),
    ("river", "river"),
    ("stream", "stream"),
    ("brook", "stream"),
    ("burn", "stream"),
    ("ford", "ford"),
    ("hill", "hill"),
    ("mountain", "mountain"),
    ("coast", "coast"),
    ("shore", "coast"),
    ("harbor", "harbor"),
    ("bay", "bay"),
    ("lake", "lake"),
    ("marsh", "marsh"),
    ("meadow", "meadow"),
    ("valley", "valley"),
    ("spring", "spring"),
    ("castle", "castle"),
    ("fort", "fort"),
    ("church", "church"),
    ("bridge", "bridge"),
)


def feature_kind_for_meaning(meaning: str, category: str) -> str:
    """Map lexicon meaning/category to an abstract feature kind for anchoring."""
    m = (meaning or "").lower()
    for hint, kind in _MEANING_HINT_TO_FEATURE:
        if hint in m:
            return kind
    c = (category or "").lower()
    if c == "engineering":
        return "settlement"
    if c == "fortification":
        return "fort"
    if c == "sacred":
        return "sanctuary"
    return "landmark"


@dataclass
class AbstractFeature:
    feature_id: str
    kind: str
    x: float
    y: float
    source_region_feature_id: str | None = None
    source_world_x: float | None = None
    source_world_y: float | None = None
    display_name: str | None = None
    etymology: str | None = None
    name_ethnic: str | None = None
    name_feature_type: str | None = None
    name_core_concept: str | None = None
    name_normalized_form: str | None = None
    name_placename_category: str | None = None
    name_placename_meaning: str | None = None


@dataclass
class AbstractBorder:
    border_id: str
    kind: str
    points: list[tuple[float, float]]


@dataclass
class SettlementPin:
    settlement_slot: int
    x: float
    y: float
    anchor_feature_id: str | None
    offset_dx: float
    offset_dy: float
    narrative_hint: str
    world_x: float | None = None
    world_y: float | None = None


@dataclass
class LocalEdge:
    from_slot: int
    to_slot: int
    distance: float


@dataclass
class LocalRegionGraph:
    region_id: str
    world: str
    features: list[AbstractFeature]
    borders: list[AbstractBorder]
    settlements: list[SettlementPin]
    edges: list[LocalEdge]
    source_geometry_version: str | None = None
    region_cell_polygon: list[tuple[float, float]] | None = None

    def to_json_obj(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "world": self.world,
            "source_geometry_version": self.source_geometry_version,
            "region_cell_polygon": self.region_cell_polygon or [],
            "features": [asdict(f) for f in self.features],
            "borders": [asdict(b) for b in self.borders],
            "settlements": [asdict(s) for s in self.settlements],
            "edges": [asdict(e) for e in self.edges],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_json_obj(), indent=2)


def _rng_seed(
    world: str,
    region_id: str,
    purpose: str,
    slot: int = 0,
    *,
    salt: int = 0,
) -> int:
    """Stable seed from inputs; ``salt`` lets you vary names/geo per campaign/run."""
    import hashlib

    h = hashlib.sha256(
        f"{world}:{region_id}:{purpose}:{slot}:{int(salt)}".encode("utf-8")
    ).hexdigest()
    return int(h[:16], 16)


def _clamp01(value: float, *, pad: float = 0.04) -> float:
    return min(1.0 - pad, max(pad, float(value)))


def _region_text(region: Region) -> str:
    return " ".join(
        str(part or "").lower()
        for part in (region.biome, region.terrain, region.keywords, region.region_name)
    )


def _terrain_family(region: Region) -> str:
    text = _region_text(region)
    if any(token in text for token in ("coast", "shore", "fjord", "harbor", "bay", "delta", "littoral")):
        return "coast"
    if any(token in text for token in ("river", "stream", "creek", "brook", "floodplain", "channel")):
        return "riverland"
    if any(token in text for token in ("highland", "mountain", "range", "ridge", "alps", "plateau", "cordillera")):
        return "highlands"
    if any(token in text for token in ("forest", "taiga", "wood", "boreal")):
        return "forest"
    if any(token in text for token in ("arid", "desert", "steppe", "salt", "oasis", "wadi")):
        return "drylands"
    return "plains"


def _river_y(x: float) -> float:
    return 0.5 + 0.13 * math.sin((float(x) * 2.6 + 0.15) * math.pi)


def _ridge_y(x: float) -> float:
    return 0.28 + 0.42 * float(x) + 0.06 * math.sin(float(x) * math.pi * 2.0)


def _weighted_choice(rng: random.Random, pairs: list[tuple[str, float]]) -> str:
    usable = [(kind, max(0.0, weight)) for kind, weight in pairs if weight > 0]
    if not usable:
        return "landmark"
    kinds, weights = zip(*usable)
    return rng.choices(kinds, weights=weights, k=1)[0]


def _feature_kind_weights(region: Region) -> list[tuple[str, float]]:
    text = _region_text(region)
    family = _terrain_family(region)
    weights: dict[str, float] = {
        "landmark": 0.5,
        "meadow": 0.55,
        "spring": 0.4,
        "hill": 0.45,
        "grove": 0.35,
    }
    if family == "coast":
        weights.update({"coast": 2.2, "bay": 1.4, "harbor": 1.1, "cliff": 0.9, "river": 0.45, "ford": 0.15})
    elif family == "riverland":
        weights.update({"river": 2.0, "stream": 1.2, "ford": 1.25, "bridge": 0.75, "marsh": 0.75, "meadow": 0.8, "mill": 0.55})
    elif family == "highlands":
        weights.update({"ridge": 1.8, "mountain": 1.15, "pass": 1.0, "spring": 0.9, "quarry": 0.65, "hill": 1.1, "lake": 0.35})
    elif family == "forest":
        weights.update({"forest": 2.0, "grove": 1.1, "stream": 0.85, "lake": 0.65, "bog": 0.5, "clearing": 0.75, "hill": 0.45})
    elif family == "drylands":
        weights.update({"wadi": 1.3, "oasis": 0.95, "spring": 0.7, "ridge": 0.8, "saltpan": 0.75, "well": 0.75, "mesa": 0.55})
    else:
        weights.update({"meadow": 1.35, "stream": 0.8, "ford": 0.55, "pasture": 1.0, "orchard": 0.75, "hill": 0.55, "market": 0.35})

    keyword_boosts = {
        "peat": "bog",
        "bog": "bog",
        "muskeg": "bog",
        "fjord": "bay",
        "cliff": "cliff",
        "quarry": "quarry",
        "orchard": "orchard",
        "salt": "saltpan",
        "lake": "lake",
        "tarn": "lake",
        "forest": "forest",
        "creek": "stream",
        "stream": "stream",
        "river": "river",
        "harbor": "harbor",
        "flood": "marsh",
    }
    for token, kind in keyword_boosts.items():
        if token in text:
            weights[kind] = weights.get(kind, 0.0) + 0.9
    return sorted(weights.items())


def _keyword_feature_kinds(region: Region) -> list[str]:
    text = _region_text(region)
    hints = (
        ("peat", "bog"),
        ("bog", "bog"),
        ("muskeg", "bog"),
        ("fjord", "bay"),
        ("cliff", "cliff"),
        ("rooker", "cliff"),
        ("quarry", "quarry"),
        ("orchard", "orchard"),
        ("pasture", "pasture"),
        ("salt", "saltpan"),
        ("lake", "lake"),
        ("tarn", "lake"),
        ("forest", "forest"),
        ("creek", "stream"),
        ("stream", "stream"),
        ("river", "river"),
        ("harbor", "harbor"),
        ("mill", "mill"),
        ("flood", "marsh"),
        ("delta", "marsh"),
        ("sturgeon", "fishery"),
        ("cod", "fishery"),
        ("fish", "fishery"),
        ("bridge", "bridge"),
        ("breakwater", "harbor"),
        ("caravan", "market"),
        ("market", "market"),
        ("well", "well"),
        ("oasis", "oasis"),
    )
    kinds: list[str] = []
    seen: set[str] = set()
    for token, kind in hints:
        if token in text and kind not in seen:
            kinds.append(kind)
            seen.add(kind)
    return kinds[:5]


def _natural_feature_point(region: Region, kind: str, rng: random.Random) -> tuple[float, float]:
    family = _terrain_family(region)
    if kind in {"river", "stream", "ford", "bridge", "mill", "marsh", "bog", "wadi", "fishery"} or family == "riverland":
        x = rng.uniform(0.08, 0.92)
        return _clamp01(x), _clamp01(_river_y(x) + rng.uniform(-0.09, 0.09))
    if kind in {"coast", "bay", "harbor", "cliff"} or family == "coast":
        return _clamp01(rng.uniform(0.08, 0.92)), _clamp01(rng.uniform(0.72, 0.9))
    if kind in {"ridge", "mountain", "pass", "quarry", "mesa"} or family == "highlands":
        x = rng.uniform(0.08, 0.92)
        return _clamp01(x), _clamp01(_ridge_y(x) + rng.uniform(-0.08, 0.08))
    if kind in {"forest", "grove", "clearing", "lake"} or family == "forest":
        return _clamp01(rng.uniform(0.14, 0.86)), _clamp01(rng.uniform(0.14, 0.86))
    if kind in {"oasis", "well", "saltpan"} or family == "drylands":
        return _clamp01(rng.uniform(0.18, 0.82)), _clamp01(rng.uniform(0.2, 0.78))
    return _clamp01(rng.uniform(0.12, 0.88)), _clamp01(rng.uniform(0.18, 0.82))


def _near_pin(pin: SettlementPin, rng: random.Random, radius: float = 0.09) -> tuple[float, float]:
    angle = rng.uniform(0.0, math.tau)
    dist = rng.uniform(radius * 0.35, radius)
    return _clamp01(pin.x + math.cos(angle) * dist), _clamp01(pin.y + math.sin(angle) * dist)


def _near_feature(feature: AbstractFeature, rng: random.Random, radius: float = 0.1) -> tuple[float, float]:
    angle = rng.uniform(0.0, math.tau)
    dist = rng.uniform(radius * 0.25, radius)
    return _clamp01(feature.x + math.cos(angle) * dist), _clamp01(feature.y + math.sin(angle) * dist)


def _settlement_landmark_kind(region: Region) -> str:
    family = _terrain_family(region)
    if family == "coast":
        return "harbor"
    if family == "riverland":
        return "ford"
    if family == "highlands":
        return "pass"
    if family == "forest":
        return "clearing"
    if family == "drylands":
        return "well"
    return "market"


def _feature_kind_for_anchor(primary_kind: str | None, region: Region) -> str:
    kind = (primary_kind or "").strip().lower()
    if kind in {"settlement", "landmark"}:
        return _settlement_landmark_kind(region)
    return kind or _settlement_landmark_kind(region)


_SETTLEMENT_SITE_PRIORITY = {
    "harbor": 120,
    "bay": 108,
    "coast": 96,
    "river": 88,
    "ford": 84,
    "stream": 72,
    "lake": 58,
    "spring": 52,
    "marsh": 46,
    "meadow": 34,
    "clearing": 30,
    "well": 28,
}


def _site_priority(feature: AbstractFeature) -> int:
    return _SETTLEMENT_SITE_PRIORITY.get((feature.kind or "").strip().lower(), 8)


def _settlement_site_anchors(features: list[AbstractFeature]) -> list[AbstractFeature]:
    """Physical-map anchors preferred for settlement sites."""
    ranked = [f for f in features if _site_priority(f) >= 28]
    if not ranked:
        ranked = list(features)
    return sorted(
        ranked,
        key=lambda f: (f.source_region_feature_id is None, -_site_priority(f), f.feature_id),
    )


def _distance_to_existing_pins(x: float, y: float, pins: list[SettlementPin]) -> float:
    if not pins:
        return math.inf
    return min(math.hypot(x - p.x, y - p.y) for p in pins)


def _site_point_near_anchor(
    anchor: AbstractFeature,
    rng: random.Random,
    pins: list[SettlementPin],
) -> tuple[float, float]:
    """Nudge a settlement beside a harbor/river while keeping a readable buffer."""
    radius = 0.035 if anchor.kind in {"harbor", "bay", "coast", "river", "ford"} else 0.055
    best = (anchor.x, anchor.y)
    best_d = _distance_to_existing_pins(best[0], best[1], pins)
    for i in range(10):
        angle = rng.uniform(0.0, math.tau)
        dist = radius * (0.35 + 0.65 * (i + 1) / 10.0)
        candidate = (
            _clamp01(anchor.x + math.cos(angle) * dist),
            _clamp01(anchor.y + math.sin(angle) * dist),
        )
        d = _distance_to_existing_pins(candidate[0], candidate[1], pins)
        if d > best_d:
            best = candidate
            best_d = d
    return best


def _local_to_world_from_region_bounds(
    local: tuple[float, float],
    region_cell_polygon: list[tuple[float, float]],
) -> tuple[float, float] | None:
    if not region_cell_polygon:
        return None
    xs = [p[0] for p in region_cell_polygon]
    ys = [p[1] for p in region_cell_polygon]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    return (
        min_x + (max_x - min_x) * _clamp01(local[0], pad=0.0),
        min_y + (max_y - min_y) * _clamp01(local[1], pad=0.0),
    )


def synthesize_features(
    region: Region,
    rng: random.Random,
    *,
    n_features: int | None = None,
    settlement_pins: list[SettlementPin] | None = None,
    primary_kind: str | None = None,
    region_features: list[RegionFeature] | None = None,
    region_cell_polygon: list[tuple[float, float]] | None = None,
) -> list[AbstractFeature]:
    """Place terrain-aware features in ``[0,1]²``.

    Region-scale features follow the configured terrain/keywords, while settlement
    landmarks are generated close to town pins so names like "ford" or "harbor"
    have a visible local anchor.
    """
    weights = category_weights_for_region(region)
    topo_bias = weights.get("Topography", 0.5)
    pins = settlement_pins or []
    base_n = 9 + int(round(topo_bias * 8)) + max(0, len(pins) - 1)
    n = base_n if n_features is None else max(3, int(n_features))
    feats: list[AbstractFeature] = []

    def add(
        kind: str,
        x: float,
        y: float,
        source_region_feature_id: str | None = None,
        source_world_x: float | None = None,
        source_world_y: float | None = None,
    ) -> None:
        feats.append(
            AbstractFeature(
                feature_id=f"{region.region_id}:f{len(feats)}",
                kind=kind,
                x=_clamp01(x),
                y=_clamp01(y),
                source_region_feature_id=source_region_feature_id,
                source_world_x=source_world_x,
                source_world_y=source_world_y,
            )
        )

    if region_features:
        for feature in region_features:
            x, y = _region_feature_to_local_point(feature, region_cell_polygon or [])
            add(feature.kind, x, y, feature.feature_id, feature.x, feature.y)

    family = _terrain_family(region)
    if family == "riverland":
        for x in (0.14, 0.34, 0.56, 0.78):
            add("river", x + rng.uniform(-0.025, 0.025), _river_y(x) + rng.uniform(-0.025, 0.025))
    elif family == "coast":
        for x, kind in ((0.18, "coast"), (0.42, "bay"), (0.66, "harbor"), (0.84, "cliff")):
            add(kind, x + rng.uniform(-0.035, 0.035), rng.uniform(0.76, 0.9))
    elif family == "highlands":
        for x, kind in ((0.18, "ridge"), (0.38, "mountain"), (0.58, "pass"), (0.78, "spring")):
            add(kind, x + rng.uniform(-0.03, 0.03), _ridge_y(x) + rng.uniform(-0.04, 0.04))
    elif family == "forest":
        for kind in ("forest", "stream", "grove", "clearing"):
            x, y = _natural_feature_point(region, kind, rng)
            add(kind, x, y)
    elif family == "drylands":
        for kind in ("wadi", "spring", "well", "ridge"):
            x, y = _natural_feature_point(region, kind, rng)
            add(kind, x, y)
    else:
        for kind in ("meadow", "stream", "pasture", "hill"):
            x, y = _natural_feature_point(region, kind, rng)
            add(kind, x, y)

    for kind in _keyword_feature_kinds(region):
        x, y = _natural_feature_point(region, kind, rng)
        add(kind, x, y)

    anchor_kind = _feature_kind_for_anchor(primary_kind, region)
    for pin in pins:
        x, y = _near_pin(pin, rng, radius=0.065)
        add(anchor_kind, x, y)
        x, y = _near_pin(pin, rng, radius=0.09)
        add(_settlement_landmark_kind(region), x, y)

    kind_weights = _feature_kind_weights(region)
    while len(feats) < n:
        kind = _weighted_choice(rng, kind_weights)
        if pins and rng.random() < 0.58:
            x, y = _near_pin(rng.choice(pins), rng, radius=0.14)
        else:
            x, y = _natural_feature_point(region, kind, rng)
        add(kind, x, y)
    return feats


def assign_feature_names(
    features: list[AbstractFeature],
    *,
    rng: random.Random,
    ethnic_weights: dict[str, float] | None,
    db_path: Path | str | None,
    placename_lexicon: PlacenameLexicon | None = None,
    proto_lexicon: EthnicProtoPlacewordLexicon | None = None,
    anchor_feature_ids: set[str] | None = None,
    used_names: set[str] | None = None,
) -> list[AbstractFeature]:
    """Attach stable proto-derived names to settlement anchor features.

    Physical features can exist without being known landmarks. Names are only
    assigned when a settlement actually anchors to the feature, so maps do not
    accumulate named dots for unused ridges, wadis, or groves.
    """
    weights = ethnic_weights or {}
    if not weights:
        return features
    allowed = {fid for fid in (anchor_feature_ids or set()) if fid}
    if not allowed:
        return features
    try:
        placenames = placename_lexicon or PlacenameLexicon.from_db(db_path=db_path)
        proto = proto_lexicon or EthnicProtoPlacewordLexicon.from_db(db_path=db_path)
    except Exception:
        return features
    if not proto.rows or not placenames.rows:
        return features

    taken = {str(name or "").strip().casefold() for name in (used_names or set()) if str(name or "").strip()}
    taken.update(str(f.display_name or "").strip().casefold() for f in features if (f.display_name or "").strip())

    for feature in features:
        if feature.feature_id not in allowed:
            continue
        if (feature.display_name or "").strip():
            continue
        generated = None
        for _ in range(8):
            candidate = generate_feature_name(
                rng=rng,
                proto=proto,
                placenames=placenames,
                ethnic_weights=weights,
                local_kind=feature.kind,
            )
            if candidate is None:
                break
            if candidate.display_name.strip().casefold() not in taken:
                generated = candidate
                break
            generated = candidate
        if generated is None:
            continue
        display = generated.display_name
        if display.strip().casefold() in taken:
            base = display
            idx = 2
            while f"{base}{idx}".casefold() in taken:
                idx += 1
            display = f"{base}{idx}"
        taken.add(display.strip().casefold())
        feature.display_name = display
        feature.etymology = generated.etymology
        feature.name_ethnic = generated.ethnic
        feature.name_feature_type = generated.feature_type
        feature.name_core_concept = generated.core_concept
        feature.name_normalized_form = generated.normalized_form
        feature.name_placename_category = generated.placename_category
        feature.name_placename_meaning = generated.placename_meaning
    return features


def _nearest_feature(
    features: list[AbstractFeature], px: float, py: float, prefer_kind: str | None
) -> AbstractFeature:
    if not features:
        raise ValueError("features empty")
    ranked = list(features)
    if prefer_kind:
        same = [f for f in ranked if f.kind == prefer_kind]
        if same:
            ranked = same
    best = ranked[0]
    best_d = (best.x - px) ** 2 + (best.y - py) ** 2
    for f in ranked[1:]:
        d = (f.x - px) ** 2 + (f.y - py) ** 2
        if d < best_d:
            best_d = d
            best = f
    return best


def _settlement_base_point(
    region: Region,
    rng: random.Random,
    slot: int,
    total_slots: int,
) -> tuple[float, float]:
    family = _terrain_family(region)
    total = max(1, int(total_slots))
    if total == 1:
        t = 0.5 + rng.uniform(-0.08, 0.08)
    else:
        t = (slot + 0.5) / total + rng.uniform(-0.045, 0.045)
    t = _clamp01(t, pad=0.12)

    if family == "coast":
        return _clamp01(0.12 + 0.76 * t), _clamp01(rng.uniform(0.68, 0.82))
    if family == "riverland":
        x = _clamp01(0.1 + 0.8 * t)
        return x, _clamp01(_river_y(x) + rng.uniform(-0.065, 0.065))
    if family == "highlands":
        x = _clamp01(0.12 + 0.76 * t)
        # Settlements sit near passes and valley shoulders, not on the peaks.
        return x, _clamp01(_ridge_y(x) + rng.choice((-1.0, 1.0)) * rng.uniform(0.07, 0.13))
    if family == "forest":
        angle = math.tau * t
        radius = 0.16 + 0.09 * rng.random()
        return _clamp01(0.5 + math.cos(angle) * radius), _clamp01(0.5 + math.sin(angle) * radius)
    if family == "drylands":
        x = _clamp01(0.18 + 0.64 * t)
        return x, _clamp01(0.45 + rng.uniform(-0.18, 0.18))
    x = _clamp01(0.18 + 0.64 * t)
    return x, _clamp01(0.42 + rng.uniform(-0.2, 0.2))


def place_settlement_pins(
    *,
    region: Region,
    features: list[AbstractFeature],
    rng: random.Random,
    settlement_slots: int,
    primary_kind: str | None,
    region_cell_polygon: list[tuple[float, float]] | None = None,
    world_geometry: WorldMapGeometry | None = None,
) -> list[SettlementPin]:
    """Assign settlement slots to strong physical sites with spacing.

    Placename landmarks no longer drive site selection. The first pass prefers
    physical-map harbors/coasts, then river/ford anchors, then softer lowland
    support features. Additional settlements favor unused anchors before sharing
    one already claimed by an existing slot.
    """
    pins: list[SettlementPin] = []
    anchors = _settlement_site_anchors(features)
    used_anchor_ids: set[str] = set()
    for slot in range(max(1, settlement_slots)):
        anchor = None
        if anchors:
            available = [f for f in anchors if f.feature_id not in used_anchor_ids] or anchors
            if all(f.feature_id in used_anchor_ids for f in anchors):
                anchor = max(
                    available,
                    key=lambda f: (
                        min(_distance_to_existing_pins(f.x, f.y, pins), 1.0),
                        _site_priority(f),
                        f.feature_id,
                    ),
                )
            else:
                anchor = max(
                    available,
                    key=lambda f: (
                        _site_priority(f),
                        min(_distance_to_existing_pins(f.x, f.y, pins), 1.0),
                        f.source_region_feature_id is not None,
                        f.feature_id,
                    ),
                )
            used_anchor_ids.add(anchor.feature_id)
            bx, by = _site_point_near_anchor(anchor, rng, pins)
        else:
            bx, by = _settlement_base_point(region, rng, slot, max(1, settlement_slots))
            anchor = _nearest_feature(features, bx, by, primary_kind) if features else None
        ox = (bx - anchor.x) if anchor is not None else 0.0
        oy = (by - anchor.y) if anchor is not None else 0.0
        hint = f"near_{anchor.kind}" if anchor is not None else f"near_{_settlement_landmark_kind(region)}"
        world_xy = None
        if anchor is not None and anchor.source_world_x is not None and anchor.source_world_y is not None:
            bounded = _local_to_world_from_region_bounds(
                (anchor.x + ox, anchor.y + oy),
                region_cell_polygon or [],
            )
            if bounded is not None:
                world_xy = bounded
        if world_xy is None:
            world_xy = _local_to_world_from_region_bounds((bx, by), region_cell_polygon or [])
        if world_xy is not None and world_geometry is not None:
            world_xy = project_world_point_to_region_footprint(
                world_geometry,
                region.region_id,
                world_xy,
            )
        pins.append(
            SettlementPin(
                settlement_slot=slot,
                x=_clamp01(bx),
                y=_clamp01(by),
                anchor_feature_id=anchor.feature_id if anchor is not None else None,
                offset_dx=ox,
                offset_dy=oy,
                narrative_hint=hint,
                world_x=world_xy[0] if world_xy is not None else None,
                world_y=world_xy[1] if world_xy is not None else None,
            )
        )
    return pins


def attach_settlement_anchors(
    pins: list[SettlementPin],
    features: list[AbstractFeature],
    *,
    primary_kind: str | None,
) -> None:
    """Mutate pins with their closest visible anchor after features are synthesized."""
    for pin in pins:
        anchor = _nearest_feature(features, pin.x, pin.y, primary_kind)
        pin.anchor_feature_id = anchor.feature_id
        pin.offset_dx = pin.x - anchor.x
        pin.offset_dy = pin.y - anchor.y
        pin.narrative_hint = f"near_{anchor.kind}"


def _wiggled_edge(
    rng: random.Random,
    side: str,
    *,
    steps: int = 7,
    amplitude: float = 0.035,
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for i in range(steps):
        t = i / max(1, steps - 1)
        wiggle = rng.uniform(-amplitude, amplitude)
        if side == "north":
            x, y = t, 0.03 + wiggle
        elif side == "south":
            x, y = 1.0 - t, 0.97 + wiggle
        elif side == "east":
            x, y = 0.97 + wiggle, t
        else:
            x, y = 0.03 + wiggle, 1.0 - t
        points.append((_clamp01(x, pad=0.015), _clamp01(y, pad=0.015)))
    return points


def _region_feature_to_local_point(feature: RegionFeature, region_cell_polygon: list[tuple[float, float]]) -> tuple[float, float]:
    if not region_cell_polygon:
        return _clamp01(feature.x), _clamp01(feature.y)
    xs = [p[0] for p in region_cell_polygon]
    ys = [p[1] for p in region_cell_polygon]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    w = max(1e-9, max_x - min_x)
    h = max(1e-9, max_y - min_y)
    return _clamp01((feature.x - min_x) / w), _clamp01((feature.y - min_y) / h)


def _cell_polygon_to_local(region_cell_polygon: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not region_cell_polygon:
        return []
    xs = [p[0] for p in region_cell_polygon]
    ys = [p[1] for p in region_cell_polygon]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    w = max(1e-9, max_x - min_x)
    h = max(1e-9, max_y - min_y)
    return [(_clamp01((x - min_x) / w, pad=0.015), _clamp01((y - min_y) / h, pad=0.015)) for x, y in region_cell_polygon]


def _world_geometry_for_region(
    *,
    world: str,
    region_id: str,
    db_path: Path | str | None,
    map_seed: object | None = None,
    world_geometry: WorldMapGeometry | None,
) -> tuple[list[RegionFeature], list[tuple[float, float]], str | None]:
    geometry = world_geometry
    if geometry is None:
        try:
            geometry = build_world_map_geometry(world=world, db_path=db_path, map_seed=map_seed)
        except Exception:
            return [], [], None
    features = geometry.features_by_region_id().get(region_id, [])
    cell = geometry.cell_by_region_id().get(region_id)
    return features, list(cell.polygon) if cell is not None else [], geometry.version


def synthesize_borders(region: Region, rng: random.Random) -> list[AbstractBorder]:
    """Create natural-looking soft region boundaries for the local map."""
    family = _terrain_family(region)
    if family == "coast":
        primary_kind = "coastline"
    elif family == "riverland":
        primary_kind = "river_boundary"
    elif family == "highlands":
        primary_kind = "ridge_boundary"
    elif family == "forest":
        primary_kind = "forest_boundary"
    elif family == "drylands":
        primary_kind = "dry_boundary"
    else:
        primary_kind = "soft_boundary"

    borders = [
        AbstractBorder(f"{region.region_id}:b0", primary_kind, _wiggled_edge(rng, "north")),
        AbstractBorder(f"{region.region_id}:b1", "soft_boundary", _wiggled_edge(rng, "east")),
        AbstractBorder(f"{region.region_id}:b2", "soft_boundary", _wiggled_edge(rng, "south")),
        AbstractBorder(f"{region.region_id}:b3", "soft_boundary", _wiggled_edge(rng, "west")),
    ]
    return borders


def intra_region_edges(pins: list[SettlementPin]) -> list[LocalEdge]:
    """No implicit roads.

    Roads should be explicit route geometry between established settlements. The
    old all-pairs Euclidean graph looked like straight roads and overpromised
    pathing the simulator does not yet model.
    """
    return []


def build_local_region_graph(
    *,
    world: str,
    region: Region,
    rng: random.Random,
    settlement_slots: int = 1,
    primary_meaning: str | None = None,
    primary_category: str | None = None,
    db_path: Path | str | None = None,
    map_seed: object | None = None,
    world_geometry: WorldMapGeometry | None = None,
    ethnic_weights: dict[str, float] | None = None,
    placename_lexicon: PlacenameLexicon | None = None,
    proto_placeword_lexicon: EthnicProtoPlacewordLexicon | None = None,
) -> LocalRegionGraph:
    """Deterministic layout when ``rng`` is seeded consistently for the region."""
    kind = feature_kind_for_meaning(primary_meaning or "", primary_category or "")
    anchor_kind = _feature_kind_for_anchor(kind, region)
    source_features, region_cell_polygon, source_version = _world_geometry_for_region(
        world=world,
        region_id=region.region_id,
        db_path=db_path,
        map_seed=map_seed,
        world_geometry=world_geometry,
    )
    feats = synthesize_features(
        region,
        rng,
        settlement_pins=None,
        primary_kind=kind,
        region_features=source_features,
        region_cell_polygon=region_cell_polygon,
    )
    pins = place_settlement_pins(
        region=region,
        features=feats,
        rng=rng,
        settlement_slots=settlement_slots,
        primary_kind=anchor_kind,
        region_cell_polygon=region_cell_polygon,
        world_geometry=world_geometry,
    )
    assign_feature_names(
        feats,
        rng=rng,
        ethnic_weights=ethnic_weights,
        db_path=db_path,
        placename_lexicon=placename_lexicon,
        proto_lexicon=proto_placeword_lexicon,
        anchor_feature_ids={p.anchor_feature_id or "" for p in pins},
    )
    borders = synthesize_borders(region, rng)
    edges = intra_region_edges(pins)
    return LocalRegionGraph(
        region_id=region.region_id,
        world=world,
        features=feats,
        borders=borders,
        settlements=pins,
        edges=edges,
        source_geometry_version=source_version or MAP_GEOMETRY_VERSION,
        region_cell_polygon=_cell_polygon_to_local(region_cell_polygon),
    )


def make_region_geography_rng(
    world: str, region_id: str, *, slot: int = 0, salt: int = 0
) -> random.Random:
    return random.Random(
        _rng_seed(world, region_id, "local_geo", slot, salt=salt)
    )


def make_settlement_name_rng(
    world: str, region_id: str, *, salt: int = 0
) -> random.Random:
    """RNG for settlement naming.

    With ``salt=0`` (default), names depend only on ``world`` and ``region_id``,
    so **aeria_north** always gets the same generated name across runs — this is
    intentional for reproducible worlds. Pass a non-zero ``salt`` (e.g. campaign
    id or ``SimulationContext.placename_rng_salt``) to vary names.
    """
    return random.Random(_rng_seed(world, region_id, "settle_name", 0, salt=salt))
