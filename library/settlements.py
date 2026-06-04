"""Settlement records: identity, lifecycle, and mutable regional/economic state."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

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


# Vacancy before abandon rolls begin (years with zero residents while still active).
ABANDON_EMPTY_GRACE_YEARS = 3
# After grace, abandon probability is min(1, excess * slope) per year.
ABANDON_PROB_PER_EXCESS_YEAR = 0.2


def roll_abandon_this_year(consecutive_empty_years: int, rng: random.Random) -> bool:
    """Escalating probability of abandonment after consecutive empty years."""
    if consecutive_empty_years <= ABANDON_EMPTY_GRACE_YEARS:
        return False
    excess = consecutive_empty_years - ABANDON_EMPTY_GRACE_YEARS
    p = min(1.0, excess * ABANDON_PROB_PER_EXCESS_YEAR)
    return rng.random() < p


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
    if p >= 20000:
        return "city"
    if p >= 2500:
        return "town"
    return "hamlet"


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

    pressure = pop / cap
    next_pressure = min(2.0, max(0.0, pressure))
    next_market_pull = min(1.0, conn / 2.0) * min(1.0, pop / 10000.0)
    next_stability = min(1.0, max(0.0, 0.72 - max(0.0, pressure - 1.0) * 0.7))

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
    )
