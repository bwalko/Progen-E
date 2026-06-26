"""Settlement records: identity, lifecycle, and mutable regional/economic state."""

from __future__ import annotations

import json
import math
import random
import re
from dataclasses import dataclass, field
from zlib import crc32

PRIMARY_SETTLEMENT_SUFFIX = ":primary"
SETTLEMENT_SEQ_PATTERN = re.compile(r":s(\d+)$")


def primary_settlement_id(region_id: str) -> str:
    """Legacy stable id used in older saves (single settlement per region)."""
    rid = (region_id or "").strip()
    return f"{rid}{PRIMARY_SETTLEMENT_SUFFIX}"


def make_settlement_id(region_id: str, seq: int) -> str:
    """Opaque settlement key: ``{region_id}:s{seq}`` with seq >= 1."""
    rid = (region_id or "").strip()
    return f"{rid}:s{max(1, int(seq))}"


# Vacancy before abandon rolls begin (years with zero/low mixed residents while active).
ABANDON_EMPTY_GRACE_YEARS = 3
# After grace, abandon probability is min(1, excess * slope) per year.
ABANDON_PROB_PER_EXCESS_YEAR = 0.2
# Mixed directory population below this counts as demographically collapsing.
ABANDON_MIXED_POP_THRESHOLD = 5
# Consecutive years of sustained economic distress before collapse rolls begin.
ABANDON_DISTRESS_GRACE_YEARS = ABANDON_EMPTY_GRACE_YEARS
# Share of alive non-detailed residents promoted when a viable site stays low-resolution.
ABANDON_LOW_RESOLUTION_PROMOTION_SHARE = 0.01
ABANDON_DISTRESS_MAX_RESIDENTS = 250
ABANDON_DISTRESS_FOOD_PRESSURE = 1.65
ABANDON_DISTRESS_STABILITY = 0.12
ABANDON_DISTRESS_PROSPERITY = 0.18
SETTLEMENT_ABSORPTION_FOUNDING_REASONS = frozenset(
    {
        "absorbed",
        "merged",
        "settlement_merged",
        "settlement_absorbed",
    }
)


@dataclass(frozen=True)
class SettlementAbandonmentDecision:
    """Annual abandonment / low-resolution sampling outcome for one active settlement."""

    should_abandon: bool
    abandon_reason: str
    next_consecutive_risk_years: int
    should_promote_sample: bool
    promotion_count: int


def directory_mixed_alive(*, detailed_alive: int, nondetailed_alive: int) -> int:
    """Viability census: detailed RAM residents plus SQL city-directory residents."""
    return max(0, int(detailed_alive)) + max(0, int(nondetailed_alive))


def settlement_is_explicitly_absorbed(state: "SettlementState") -> bool:
    reason = (getattr(state, "founding_reason", "") or "").strip().lower()
    return reason in SETTLEMENT_ABSORPTION_FOUNDING_REASONS


def low_resolution_promotion_count(nondetailed_alive: int) -> int:
    alive = max(0, int(nondetailed_alive))
    if alive <= 0:
        return 0
    return max(1, int(math.ceil(alive * ABANDON_LOW_RESOLUTION_PROMOTION_SHARE)))


def settlement_economic_distress_this_year(
    state: "SettlementState",
    *,
    mixed_alive: int,
) -> bool:
    """Severe settlement-level distress from food pressure, stability, and prosperity."""
    pop = max(0, int(mixed_alive))
    if pop <= 0:
        return False
    food = _clamp(_metric_value(state, "food_pressure", 0.0), 0.0, 2.0)
    stability = _clamp(_metric_value(state, "stability", 0.5), 0.0, 1.0)
    prosperity = _clamp(_metric_value(state, "prosperity_pool", 1.0), 0.0, 3.0)
    return (
        food >= ABANDON_DISTRESS_FOOD_PRESSURE
        and stability <= ABANDON_DISTRESS_STABILITY
        and prosperity <= ABANDON_DISTRESS_PROSPERITY
    )


def settlement_abandon_risk_this_year(
    *,
    mixed_alive: int,
    economic_distress: bool,
) -> bool:
    """Whether this year extends the consecutive abandon-risk streak."""
    pop = max(0, int(mixed_alive))
    if pop <= 0:
        return True
    if pop < ABANDON_MIXED_POP_THRESHOLD:
        return True
    return bool(economic_distress)


def evaluate_settlement_abandonment(
    state: "SettlementState",
    *,
    detailed_alive: int,
    nondetailed_alive: int,
    rng: random.Random,
) -> SettlementAbandonmentDecision:
    """Decide abandonment or low-resolution promotion using mixed directory viability."""
    detailed_alive_i = max(0, int(detailed_alive))
    nondetailed_alive_i = max(0, int(nondetailed_alive))
    mixed_alive = directory_mixed_alive(
        detailed_alive=detailed_alive_i,
        nondetailed_alive=nondetailed_alive_i,
    )
    prior_risk = max(0, int(getattr(state, "consecutive_empty_years", 0) or 0))

    if settlement_is_explicitly_absorbed(state):
        return SettlementAbandonmentDecision(
            should_abandon=True,
            abandon_reason="absorbed",
            next_consecutive_risk_years=0,
            should_promote_sample=False,
            promotion_count=0,
        )

    economic_distress = settlement_economic_distress_this_year(
        state,
        mixed_alive=mixed_alive,
    )
    abandon_risk = settlement_abandon_risk_this_year(
        mixed_alive=mixed_alive,
        economic_distress=economic_distress,
    )
    next_risk = prior_risk + 1 if abandon_risk else 0

    demographic_collapse = mixed_alive <= 0 or mixed_alive < ABANDON_MIXED_POP_THRESHOLD
    sustained_distress = (
        mixed_alive >= ABANDON_MIXED_POP_THRESHOLD
        and economic_distress
        and next_risk > ABANDON_DISTRESS_GRACE_YEARS
    )
    eligible_for_roll = demographic_collapse or sustained_distress

    should_abandon = False
    abandon_reason = ""
    if eligible_for_roll and roll_abandon_this_year(next_risk, rng):
        should_abandon = True
        if demographic_collapse:
            abandon_reason = "empty"
        else:
            abandon_reason = "economic"

    should_promote = False
    promotion_count = 0
    if (
        not should_abandon
        and detailed_alive_i == 0
        and nondetailed_alive_i > 0
        and mixed_alive >= ABANDON_MIXED_POP_THRESHOLD
        and not sustained_distress
    ):
        legacy_vacancy = settlement_distress_counts_as_vacancy(
            state,
            resident_count=detailed_alive_i,
        )
        legacy_eligible = legacy_vacancy and prior_risk > ABANDON_EMPTY_GRACE_YEARS
        if legacy_eligible or not economic_distress:
            should_promote = True
            promotion_count = low_resolution_promotion_count(nondetailed_alive_i)

    return SettlementAbandonmentDecision(
        should_abandon=should_abandon,
        abandon_reason=abandon_reason,
        next_consecutive_risk_years=next_risk,
        should_promote_sample=should_promote,
        promotion_count=promotion_count,
    )


def roll_abandon_this_year(consecutive_empty_years: int, rng: random.Random) -> bool:
    """Escalating probability of abandonment after consecutive empty years."""
    if consecutive_empty_years <= ABANDON_EMPTY_GRACE_YEARS:
        return False
    excess = consecutive_empty_years - ABANDON_EMPTY_GRACE_YEARS
    p = min(1.0, excess * ABANDON_PROB_PER_EXCESS_YEAR)
    return rng.random() < p


def settlement_distress_counts_as_vacancy(
    state: "SettlementState",
    *,
    resident_count: int | None = None,
) -> bool:
    """Legacy detailed-only vacancy/distress signal (tests and promotion guardrails)."""
    pop = (
        max(0, int(resident_count))
        if resident_count is not None
        else max(0, int(getattr(state, "resident_count", 0) or 0))
    )
    if pop <= 0:
        return True
    if pop > ABANDON_DISTRESS_MAX_RESIDENTS:
        return False
    food = _clamp(_metric_value(state, "food_pressure", 0.0), 0.0, 2.0)
    stability = _clamp(_metric_value(state, "stability", 0.5), 0.0, 1.0)
    prosperity = _clamp(_metric_value(state, "prosperity_pool", 1.0), 0.0, 3.0)
    return (
        food >= ABANDON_DISTRESS_FOOD_PRESSURE
        and stability <= ABANDON_DISTRESS_STABILITY
        and prosperity <= ABANDON_DISTRESS_PROSPERITY
    )


def next_settlement_sequence(region_id: str, existing_ids: list[str]) -> int:
    """Next sequence integer for ``region_id`` given existing settlement ids."""
    rid_p = (region_id or "").strip()
    max_seq = 0
    for sid in existing_ids:
        s = (sid or "").strip()
        if not s.startswith(rid_p + ":"):
            continue
        if s.endswith(PRIMARY_SETTLEMENT_SUFFIX):
            max_seq = max(max_seq, 1)
            continue
        m = SETTLEMENT_SEQ_PATTERN.search(s)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq + 1 if max_seq > 0 else 1


@dataclass
class SettlementState:
    """Settlement: founding metadata plus evolving metrics.

    ``resident_count`` is the census of alive people with this settlement as
    birthplace (until residence migration exists). It is not a capacity limit.
    The SQLite column remains ``population_cap`` for backward compatibility.
    """

    region_id: str
    region_display_name: str = ""
    settlement_id: str = ""
    level: str = "hamlet"
    resident_count: int = 0
    household_cap: int = 0
    food_pressure: float = 0.0
    # Circulating local prosperity stock for pooled job draws (see ``library/simulation_economy``).
    prosperity_pool: float = 1.0
    stability: float = 0.5
    market_pull: float = 0.0
    display_name: str | None = None
    etymology: str | None = None
    name_category_primary: str | None = None
    name_category_secondary: str | None = None
    name_culture_primary: str | None = None
    name_culture_secondary: str | None = None
    local_geography_json: str | None = None
    founded_sim_year: int | None = None
    abandoned_sim_year: int | None = None
    status: str = "active"
    consecutive_empty_years: int = 0
    site_slot: int = 1
    founding_reason: str = "organic"
    mother_settlement_id: str | None = None
    trade_network_id: str | None = None
    autonomy_level: str = "autonomous"
    affordance_selected_role: str | None = field(default=None, repr=False, compare=False)
    affordance_secondary_roles: tuple[str, ...] = field(default_factory=tuple, repr=False, compare=False)
    affordance_population_ceiling_multiplier: float | None = field(default=None, repr=False, compare=False)
    affordance_migration_pull: float | None = field(default=None, repr=False, compare=False)
    affordance_large_population_enablers: tuple[str, ...] = field(default_factory=tuple, repr=False, compare=False)
    _affordance_cache_key: tuple[object, ...] | None = field(default=None, repr=False, compare=False)
    _affordance_profile: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not str(self.settlement_id).strip():
            self.settlement_id = make_settlement_id(self.region_id, 1)
        st = (self.status or "active").strip().lower()
        self.status = st if st else "active"
        if int(self.site_slot) < 1:
            self.site_slot = 1
        reason = (self.founding_reason or "organic").strip().lower()
        self.founding_reason = reason if reason else "organic"
        mother = str(self.mother_settlement_id or "").strip()
        self.mother_settlement_id = mother or None
        network = str(self.trade_network_id or "").strip()
        self.trade_network_id = network or self.settlement_id
        autonomy = (self.autonomy_level or "autonomous").strip().lower()
        self.autonomy_level = autonomy if autonomy else "autonomous"


def classify_settlement_level(resident_count: int) -> str:
    p = max(0, int(resident_count))
    if p >= 1000:
        return "city"
    if p >= 100:
        return "town"
    if p >= 50:
        return "village"
    return "hamlet"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


def _metric_value(state: object, name: str, default: float) -> float:
    raw = getattr(state, name, None)
    if raw is None or raw == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _stable_unit_interval(text: str) -> float:
    return crc32(text.encode("utf-8")) / 0xFFFFFFFF


def _settlement_signal_text(state: SettlementState) -> str:
    pieces = [
        state.region_id,
        state.settlement_id,
        state.display_name or "",
        state.founding_reason or "",
        state.autonomy_level or "",
        state.local_geography_json or "",
    ]
    try:
        data = json.loads(state.local_geography_json or "{}")
    except (TypeError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict):
        for site in data.get("settlements") or ():
            if isinstance(site, dict):
                pieces.extend(str(v) for v in site.values())
    return " ".join(pieces).lower()


def settlement_site_capacity_factor(
    state: SettlementState,
    *,
    ctx: object | None = None,
    year: int | None = None,
) -> float:
    """Deterministic local carrying-capacity signal for uneven settlement scale."""
    if ctx is not None:
        try:
            from library.settlement_affordances import cached_settlement_affordance_profile

            return cached_settlement_affordance_profile(
                ctx,
                state,
                year=year,
            ).population_ceiling_multiplier
        except Exception:
            pass
    key = (
        f"{state.region_id}|{state.settlement_id}|{state.site_slot}|"
        f"{state.founding_reason}|{state.display_name or ''}"
    )
    unit = _stable_unit_interval(key)
    base = 0.12 + (unit**2.25) * 3.75
    text = _settlement_signal_text(state)
    multiplier = 1.0
    if any(token in text for token in ("delta", "river", "mouth", "ford", "ferry")):
        multiplier += 0.32
    if any(token in text for token in ("port", "harbor", "harbour", "dock", "coast")):
        multiplier += 0.42
    if any(token in text for token in ("market", "trade", "road", "crossing")):
        multiplier += 0.22
    if "birth" in (state.founding_reason or "") or "spinoff" in (state.founding_reason or ""):
        multiplier *= 0.72
    if (state.autonomy_level or "").strip().lower() == "district":
        multiplier *= 0.88
    return _clamp(base * multiplier, 0.08, 5.0)


def settlement_attraction_score(
    state: SettlementState,
    *,
    connectivity_score: float = 0.0,
    resident_count: int | None = None,
    ctx: object | None = None,
    year: int | None = None,
) -> float:
    """Positive destination/allocation score from site, economy, stability, and mass."""
    residents = (
        max(0, int(resident_count))
        if resident_count is not None
        else max(0, int(getattr(state, "resident_count", 0) or 0))
    )
    affordance_pull = 1.0
    headroom = 1.0
    profile = None
    if ctx is not None:
        try:
            from library.settlement_affordances import cached_settlement_affordance_profile

            profile = cached_settlement_affordance_profile(ctx, state, year=year)
            affordance_pull = 0.84 + profile.migration_pull * 0.42
        except Exception:
            profile = None
    site = (
        profile.population_ceiling_multiplier
        if profile is not None
        else settlement_site_capacity_factor(state)
    )
    prosperity = _clamp(float(getattr(state, "prosperity_pool", 1.0) or 1.0), 0.0, 3.0)
    market = _clamp(float(getattr(state, "market_pull", 0.0) or 0.0), 0.0, 1.0)
    stability = _clamp(float(getattr(state, "stability", 0.5) or 0.5), 0.0, 1.0)
    pressure = _clamp(float(getattr(state, "food_pressure", 0.0) or 0.0), 0.0, 2.0)
    conn = _clamp(float(connectivity_score), 0.0, 3.0)
    if ctx is not None and profile is not None:
        try:
            region_cap = max(1, int(ctx.effective_regional_population_cap(profile.region_id)))
            soft_site_cap = max(
                24,
                int(round(region_cap * 0.18 * profile.population_ceiling_multiplier)),
            )
            headroom = _clamp(1.16 - residents / soft_site_cap, 0.22, 1.35)
        except Exception:
            headroom = 1.0
    mass = 1.0 + min(0.82, residents**0.5 / 34.0)
    economy = 0.56 + 0.22 * prosperity + 0.34 * market + 0.24 * stability
    pressure_factor = _clamp(1.28 - pressure * 0.32, 0.35, 1.35)
    connectivity_factor = 0.86 + min(0.42, conn * 0.12)
    return max(
        0.01,
        site * economy * pressure_factor * connectivity_factor * mass * affordance_pull * headroom,
    )


def evolve_settlement(
    state: SettlementState,
    *,
    resident_count: int,
    carrying_capacity: int,
    connectivity_score: float,
) -> SettlementState:
    """Update derived pressure/stability/market metrics from census and regional capacity."""
    cap = max(1, int(carrying_capacity))
    pop = max(0, int(resident_count))
    conn = max(0.0, float(connectivity_score))

    site_factor = settlement_site_capacity_factor(state)
    pressure = pop / cap
    next_pressure = min(2.0, max(0.0, pressure))
    next_market_pull = _clamp(
        (0.15 + min(0.85, conn / 2.4))
        * min(1.0, pop / 900.0)
        * (0.70 + min(0.45, site_factor / 7.0)),
        0.0,
        1.0,
    )
    next_stability = min(1.0, max(0.0, 0.76 - max(0.0, pressure - 1.0) * 0.7))

    hh = max(0 if pop <= 0 else 1, int(round(pop / 4.5)))

    return SettlementState(
        region_id=state.region_id,
        region_display_name=state.region_display_name,
        settlement_id=state.settlement_id,
        level=classify_settlement_level(pop),
        resident_count=pop,
        household_cap=hh,
        food_pressure=next_pressure,
        prosperity_pool=float(getattr(state, "prosperity_pool", 1.0) or 0.0),
        stability=next_stability,
        market_pull=next_market_pull,
        display_name=state.display_name,
        etymology=state.etymology,
        name_category_primary=state.name_category_primary,
        name_category_secondary=state.name_category_secondary,
        name_culture_primary=state.name_culture_primary,
        name_culture_secondary=state.name_culture_secondary,
        local_geography_json=state.local_geography_json,
        founded_sim_year=state.founded_sim_year,
        abandoned_sim_year=state.abandoned_sim_year,
        status=state.status,
        consecutive_empty_years=state.consecutive_empty_years,
        site_slot=state.site_slot,
        founding_reason=state.founding_reason,
        mother_settlement_id=state.mother_settlement_id,
        trade_network_id=state.trade_network_id,
        autonomy_level=state.autonomy_level,
        affordance_selected_role=state.affordance_selected_role,
        affordance_secondary_roles=state.affordance_secondary_roles,
        affordance_population_ceiling_multiplier=state.affordance_population_ceiling_multiplier,
        affordance_migration_pull=state.affordance_migration_pull,
        affordance_large_population_enablers=state.affordance_large_population_enablers,
        _affordance_cache_key=state._affordance_cache_key,
        _affordance_profile=state._affordance_profile,
    )
