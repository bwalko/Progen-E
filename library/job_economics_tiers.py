"""Keyword tiers for job_economics deviation multipliers (used when generating CSV).

Multipliers apply to the era ``base`` row (see ``config/job_economics.csv``).
Values are >= 0; blanks in CSV mean 1.0 (no effect). Premium-only genome slots
get an extra bump on top of keyword tier.
"""

from __future__ import annotations

# Bump applied when the job token came from a *_premium_jobs column.
_PREMIUM_BUMP: dict[str, float] = {
    "pool_draw": 0.88,
    "wage_yield": 1.38,
    "value_add": 1.32,
    "tax_rate": 1.15,
}

# (label, keyword_substrings, pool_draw, wage_yield, value_add, tax_rate) multipliers.
# First matching rule wins (list is high-priority first).
_TIER_RULES: tuple[tuple[str, tuple[str, ...], dict[str, float]], ...] = (
    (
        "apex_institution",
        (
            "vision quest",
            "band champion",
            "temple astronomer",
            "guild peak master",
            "cathedral clock",
            "royal workshop specialist",
            "venture-scale architect",
            "systems architect",
            "master siege engineer",
            "arena choreographer",
            "famous duel referee",
            "elite crisis negotiator",
            "prize-winning specialist",
        ),
        {
            "pool_draw": 0.72,
            "wage_yield": 5.2,
            "value_add": 5.0,
            "tax_rate": 1.55,
        },
    ),
    (
        "high_command",
        (
            "crisis leader",
            "caravan master",
            "military strategist",
            "guild master",
            "village elder",
            "mayor",
            "magistrate",
            "constable",
            "officer",
            "household head",
            "clan representative",
            "estate steward",
            "marriage broker",
            "clan diplomat",
        ),
        {
            "pool_draw": 0.78,
            "wage_yield": 4.1,
            "value_add": 4.0,
            "tax_rate": 1.38,
        },
    ),
    (
        "warrior_elite",
        (
            "band defender",
            "legionary",
            "shock infantry",
            "gladiator",
            "berserker",
            "duelist",
            "knight-equivalent",
            "longbowman",
            "blacksmith striker",
            "siege craft specialist",
            "shock troop",
            "master chariot",
        ),
        {
            "pool_draw": 0.82,
            "wage_yield": 3.6,
            "value_add": 3.4,
            "tax_rate": 1.22,
        },
    ),
    (
        "marginal_unreliable",
        (
            "unreliable",
            "careless",
            "bad ",
            "bankrupt",
            "anxious",
            "bizarre",
            "lazy",
            "muck worker",
            "scullion",
            "gambler",
            "troublemaker",
        ),
        {
            "pool_draw": 1.08,
            "wage_yield": 0.38,
            "value_add": 0.4,
            "tax_rate": 0.82,
        },
    ),
    (
        "expert_knowledge",
        (
            "philosopher",
            "physician",
            "engineer",
            "architect",
            "analyst",
            "strategist",
            "jurist",
            "astronomer-priest",
            "master artisan",
            "scientist",
            "professor",
            "software engineer",
            "inventor",
            "alchemist",
        ),
        {
            "pool_draw": 0.92,
            "wage_yield": 2.75,
            "value_add": 2.9,
            "tax_rate": 1.18,
        },
    ),
    (
        "skilled_trades",
        (
            "healer",
            "toolmaker",
            "seasonal planner",
            "fire specialist",
            "potter",
            "brewery assistant",
            "mill worker",
            "smith",
            "mason",
            "carpenter",
            "teacher",
            "midwife",
            "judge",
            "priest",
            "scribe",
        ),
        {
            "pool_draw": 0.98,
            "wage_yield": 1.85,
            "value_add": 1.95,
            "tax_rate": 1.08,
        },
    ),
    (
        "routine_labor",
        (
            "porter",
            "carrier",
            "sweeper",
            "hauler",
            "helper",
            "assistant",
            "field hand",
            "gatherer",
            "firewood",
            "berry picker",
            "water carrier",
            "brick carrier",
            "dock hand",
            "baggage",
            "laborer",
            "picker",
            "janitorial",
            "stockroom",
            "warehouse picker",
            "groundskeeping",
        ),
        {
            "pool_draw": 1.1,
            "wage_yield": 0.55,
            "value_add": 0.58,
            "tax_rate": 0.92,
        },
    ),
)


def keyword_tier_multipliers(job_key: str) -> dict[str, float]:
    """Return multiplier map (all keys present); defaults 1.0 per axis."""
    jk = (job_key or "").strip().lower()
    out = {"pool_draw": 1.0, "wage_yield": 1.0, "value_add": 1.0, "tax_rate": 1.0}
    if not jk:
        return out
    for _label, keys, mults in _TIER_RULES:
        if any(k in jk for k in keys):
            out.update(mults)
            return out
    return out


def infer_deviation_multipliers(job_key: str, *, is_premium: bool) -> dict[str, float]:
    """Multipliers for one catalog key, including premium-slot bump when applicable."""
    m = keyword_tier_multipliers(job_key)
    if is_premium:
        for axis, bump in _PREMIUM_BUMP.items():
            m[axis] = float(m.get(axis, 1.0)) * float(bump)
    return m


def deviation_row_non_trivial(mults: dict[str, float]) -> bool:
    return any(abs(float(v) - 1.0) > 1e-6 for v in mults.values())


def format_deviation_cells(mults: dict[str, float]) -> dict[str, str]:
    """CSV cells: empty string means multiplier 1.0 (inherit)."""
    keys = ("pool_draw", "wage_yield", "value_add", "tax_rate")
    return {k: ("" if abs(float(mults.get(k, 1.0)) - 1.0) < 1e-9 else f"{float(mults[k]):.4f}") for k in keys}
