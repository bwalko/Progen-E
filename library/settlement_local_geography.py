"""Abstract local geography: category weights from biome/terrain and placement graphs."""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass
from typing import Any

from library.geography import Region


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


@dataclass
class SettlementPin:
    settlement_slot: int
    x: float
    y: float
    anchor_feature_id: str | None
    offset_dx: float
    offset_dy: float
    narrative_hint: str


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
    settlements: list[SettlementPin]
    edges: list[LocalEdge]

    def to_json_obj(self) -> dict[str, Any]:
        return {
            "region_id": self.region_id,
            "world": self.world,
            "features": [asdict(f) for f in self.features],
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


def synthesize_features(
    region: Region,
    rng: random.Random,
    *,
    n_features: int | None = None,
) -> list[AbstractFeature]:
    """Place abstract features in ``[0,1]²`` using jittered grid positions."""
    weights = category_weights_for_region(region)
    topo_bias = weights.get("Topography", 0.5)
    base_n = 5 + int(round(topo_bias * 6))
    n = base_n if n_features is None else max(3, int(n_features))
    feats: list[AbstractFeature] = []
    side = int(math.ceil(math.sqrt(n)))
    idx = 0
    for gy in range(side):
        for gx in range(side):
            if idx >= n:
                break
            bx = (gx + 0.5) / side
            by = (gy + 0.5) / side
            jitter_x = rng.uniform(-0.15, 0.15) / side
            jitter_y = rng.uniform(-0.15, 0.15) / side
            x = min(1.0, max(0.0, bx + jitter_x))
            y = min(1.0, max(0.0, by + jitter_y))
            kinds = [
                "forest",
                "river",
                "hill",
                "meadow",
                "ford",
                "coast",
                "landmark",
            ]
            if "coast" in (region.terrain or "").lower():
                kind = rng.choices(
                    kinds, weights=[1, 1, 1, 1, 1, 3, 1], k=1
                )[0]
            elif "river" in (region.terrain or "").lower():
                kind = rng.choices(
                    kinds, weights=[1, 3, 1, 1, 2, 0.5, 1], k=1
                )[0]
            else:
                kind = rng.choice(kinds)
            feats.append(
                AbstractFeature(
                    feature_id=f"{region.region_id}:f{idx}",
                    kind=kind,
                    x=x,
                    y=y,
                )
            )
            idx += 1
        if idx >= n:
            break
    return feats


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


def place_settlement_pins(
    *,
    region: Region,
    features: list[AbstractFeature],
    rng: random.Random,
    settlement_slots: int,
    primary_kind: str | None,
) -> list[SettlementPin]:
    """Assign each settlement slot a position relative to the nearest matching feature."""
    pins: list[SettlementPin] = []
    for slot in range(max(1, settlement_slots)):
        # Base point in region — deterministic jitter per slot.
        bx = 0.35 + 0.3 * rng.random()
        by = 0.35 + 0.3 * rng.random()
        anchor = _nearest_feature(features, bx, by, primary_kind)
        ox = (bx - anchor.x) * 0.08
        oy = (by - anchor.y) * 0.08
        hint = f"near_{anchor.kind}"
        pins.append(
            SettlementPin(
                settlement_slot=slot,
                x=min(1.0, max(0.0, bx)),
                y=min(1.0, max(0.0, by)),
                anchor_feature_id=anchor.feature_id,
                offset_dx=ox,
                offset_dy=oy,
                narrative_hint=hint,
            )
        )
    return pins


def intra_region_edges(pins: list[SettlementPin]) -> list[LocalEdge]:
    """Complete graph with Euclidean distance as abstract travel cost."""
    edges: list[LocalEdge] = []
    n = len(pins)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = pins[i], pins[j]
            d = math.hypot(a.x - b.x, a.y - b.y)
            edges.append(LocalEdge(from_slot=a.settlement_slot, to_slot=b.settlement_slot, distance=d))
    return edges


def build_local_region_graph(
    *,
    world: str,
    region: Region,
    rng: random.Random,
    settlement_slots: int = 1,
    primary_meaning: str | None = None,
    primary_category: str | None = None,
) -> LocalRegionGraph:
    """Deterministic layout when ``rng`` is seeded consistently for the region."""
    feats = synthesize_features(region, rng)
    kind = feature_kind_for_meaning(primary_meaning or "", primary_category or "")
    pins = place_settlement_pins(
        region=region,
        features=feats,
        rng=rng,
        settlement_slots=settlement_slots,
        primary_kind=kind,
    )
    edges = intra_region_edges(pins)
    return LocalRegionGraph(
        region_id=region.region_id,
        world=world,
        features=feats,
        settlements=pins,
        edges=edges,
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
