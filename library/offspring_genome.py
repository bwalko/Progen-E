"""Heredity: build a child genome from two parents."""

from __future__ import annotations

import random

from library.person import Person
from library.random_traits import GENOME_MAX_MAGNITUDE

# Per digit position (thousands → hundredths): first pick uses these weights;
# second pick renormalizes onto the three remaining positions.
_DIGIT_POSITION_WEIGHTS: tuple[float, ...] = (55.0, 30.0, 10.0, 5.0)


def _weighted_digit_position() -> int:
    r = random.random() * 100.0
    if r < 55.0:
        return 0
    if r < 85.0:
        return 1
    if r < 95.0:
        return 2
    return 3


def _weighted_remaining_position(exclude: int) -> int:
    total = 100.0 - _DIGIT_POSITION_WEIGHTS[int(exclude)]
    r = random.random() * total
    acc = 0.0
    for i, weight in enumerate(_DIGIT_POSITION_WEIGHTS):
        if i == int(exclude):
            continue
        acc += weight
        if r < acc:
            return i
    return 3 if int(exclude) != 3 else 2


def _split_magnitude_digits(mag: float) -> list[int]:
    """Four base-10 digits for ``mag`` on [0, GENOME_MAX_MAGNITUDE], thousands first."""
    capped = min(float(mag), GENOME_MAX_MAGNITUDE)
    n = int(round(capped * 100.0))
    n = max(0, min(9999, n))
    return [n // 1000, (n // 100) % 10, (n // 10) % 10, n % 10]


def _two_digits_from_parent_trait_value(value: float | None) -> tuple[int, int]:
    """Pick two digit values (0–9) from the parent's four-place magnitude representation."""
    if value is None:
        return 0, 0
    mag = abs(round(float(value), 2))
    mag = min(mag, GENOME_MAX_MAGNITUDE)
    digits = _split_magnitude_digits(mag)
    pos1 = _weighted_digit_position()
    pos2 = _weighted_remaining_position(pos1)
    return digits[pos1], digits[pos2]


def _offspring_value_from_digits(
    parent_a_first: int,
    parent_b_first: int,
    parent_a_second: int,
    parent_b_second: int,
    *,
    parent_a_digits_first: bool,
) -> float:
    if parent_a_digits_first:
        a, b, c, d = (
            parent_a_first,
            parent_b_first,
            parent_a_second,
            parent_b_second,
        )
    else:
        a, b, c, d = (
            parent_b_first,
            parent_a_first,
            parent_b_second,
            parent_a_second,
        )
    unsigned = (10 * a + b) + (10 * c + d) / 100.0
    sign = 1.0 if random.random() < 0.5 else -1.0
    return round(sign * unsigned, 2)


def generate_offspring_genome(
    parent_a: Person,
    parent_b: Person,
) -> dict[str, float]:
    """Build child traits by interleaving digit picks from each parent's magnitude.

    Per trait: each parent's value is taken as a four-digit magnitude (two decimal
    places, ``±99.99`` max). Two digit positions are drawn per parent (55/30/10/5
    for the first, same weights renormalized among the three remaining for the
    second). A random sign applies to the child. Digits from A and B are woven in
    random order (A-first or B-first). If only one parent carries the trait, that
    parent's value is used for both extractions.
    """
    child_genome: dict[str, float] = {}
    traits = sorted(set(parent_a.genome) | set(parent_b.genome))

    for trait in traits:
        val_a = parent_a.genome.get(trait)
        val_b = parent_b.genome.get(trait)
        if val_a is None and val_b is None:
            continue
        source_a = float(val_a) if val_a is not None else float(val_b)
        source_b = float(val_b) if val_b is not None else float(val_a)

        p1_d1, p1_d2 = _two_digits_from_parent_trait_value(source_a)
        p2_d1, p2_d2 = _two_digits_from_parent_trait_value(source_b)
        a_first = random.random() < 0.5
        child_genome[trait] = _offspring_value_from_digits(
            p1_d1,
            p2_d1,
            p1_d2,
            p2_d2,
            parent_a_digits_first=a_first,
        )

    return child_genome
