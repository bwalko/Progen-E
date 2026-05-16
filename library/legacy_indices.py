"""Derived 0..1 historical-legacy potential scores from person traits.

These are not narrative composite tags. They are compact indexes for quickly
spotting people whose current mind/body could plausibly make them remembered
centuries later, for good or ill.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class LegacyIndex:
    key: str
    label: str
    score: float
    description: str


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _trait(traits: Mapping[str, float], key: str) -> float:
    try:
        return float(traits.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _ideal(traits: Mapping[str, float], key: str) -> float:
    return _clamp01(1.0 - abs(_trait(traits, key)) / 100.0)


def _positive(traits: Mapping[str, float], key: str) -> float:
    return _clamp01((_trait(traits, key) + 100.0) / 200.0)


def _negative(traits: Mapping[str, float], key: str) -> float:
    return _clamp01((100.0 - _trait(traits, key)) / 200.0)


def _extreme(traits: Mapping[str, float], key: str) -> float:
    return _clamp01(abs(_trait(traits, key)) / 100.0)


def _avg(*values: float) -> float:
    if not values:
        return 0.0
    return _clamp01(sum(float(v) for v in values) / len(values))


def legacy_index_scores(traits: Mapping[str, float] | None) -> tuple[LegacyIndex, ...]:
    """Return broad historical-significance indexes from current trait values.

    Every index combines at least two traits. Scores are intentionally broad:
    they estimate the raw human material for being remembered, not whether the
    person actually received opportunity, office, wealth, survival, or luck.
    """
    t = traits or {}
    rows = (
        LegacyIndex(
            "beauty_legend",
            "Beauty Legend",
            _avg(
                _ideal(t, "symmetry"),
                _ideal(t, "wit"),
                _ideal(t, "persuasion"),
                _positive(t, "mating drive"),
            ),
            "Memorable beauty, charm, wit, and romantic signal.",
        ),
        LegacyIndex(
            "scholar_sage",
            "Scholar-Sage",
            _avg(
                _ideal(t, "intellect"),
                _ideal(t, "curiosity"),
                _ideal(t, "focus"),
                _ideal(t, "discipline"),
            ),
            "Capacity for durable learning, teaching, analysis, or strategy.",
        ),
        LegacyIndex(
            "creative_genius",
            "Creative Genius",
            _avg(
                _ideal(t, "creativity"),
                _ideal(t, "intellect"),
                _ideal(t, "focus"),
                _ideal(t, "perception"),
                0.65 * _ideal(t, "discipline") + 0.35 * _positive(t, "neurochemical"),
            ),
            "Original artistic or inventive power with enough mind to shape it.",
        ),
        LegacyIndex(
            "athletic_hero",
            "Athletic Hero",
            _avg(
                _ideal(t, "physical"),
                _ideal(t, "discipline"),
                _ideal(t, "resilience"),
                _ideal(t, "courage"),
            ),
            "Physical excellence, courage, endurance, and training capacity.",
        ),
        LegacyIndex(
            "conqueror",
            "Conqueror",
            _avg(
                _ideal(t, "physical"),
                _ideal(t, "courage"),
                _positive(t, "ambition"),
                _positive(t, "assertiveness"),
                _ideal(t, "discipline"),
            ),
            "Forceful military or expansionist potential.",
        ),
        LegacyIndex(
            "founder_ruler",
            "Founder-Ruler",
            _avg(
                _ideal(t, "civics"),
                _ideal(t, "justice"),
                _ideal(t, "persuasion"),
                _ideal(t, "discipline"),
                _positive(t, "ambition"),
            ),
            "Institution-building leadership and durable public authority.",
        ),
        LegacyIndex(
            "prophet_mystic",
            "Prophet-Mystic",
            _avg(
                _positive(t, "creativity"),
                _positive(t, "perception"),
                _positive(t, "neurochemical"),
                _ideal(t, "persuasion"),
                _ideal(t, "humility"),
            ),
            "Visionary intensity, symbolic perception, and ability to move others.",
        ),
        LegacyIndex(
            "infamous_predator",
            "Infamous Predator",
            _avg(
                _negative(t, "empathy"),
                _negative(t, "justice"),
                _negative(t, "honesty"),
                _positive(t, "persuasion"),
                _positive(t, "ambition"),
            ),
            "Memorable danger: exploitation, manipulation, crime, or tyranny.",
        ),
        LegacyIndex(
            "scandal_icon",
            "Scandal Icon",
            _avg(
                _negative(t, "modesty"),
                _positive(t, "mating drive"),
                _positive(t, "neurochemical"),
                _negative(t, "temperance"),
                _ideal(t, "symmetry"),
            ),
            "Attention through beauty, appetite, drama, and public impropriety.",
        ),
        LegacyIndex(
            "martyr_reformer",
            "Martyr-Reformer",
            _avg(
                _ideal(t, "justice"),
                _ideal(t, "empathy"),
                _ideal(t, "courage"),
                _positive(t, "civics"),
                _positive(t, "loyalty"),
            ),
            "Remembered for cause, sacrifice, moral conviction, or reform.",
        ),
    )
    return tuple(
        LegacyIndex(r.key, r.label, round(_clamp01(r.score), 4), r.description)
        for r in rows
    )


def top_legacy_index_scores(
    traits: Mapping[str, float] | None, *, limit: int = 5
) -> tuple[LegacyIndex, ...]:
    ranked = sorted(
        legacy_index_scores(traits),
        key=lambda row: (-row.score, row.label),
    )
    return tuple(ranked[: max(0, int(limit))])
