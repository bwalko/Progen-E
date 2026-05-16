"""Print five traced offspring examples (RNG matches generate_offspring_genome)."""

from __future__ import annotations

import random
import sys
from pathlib import Path
from typing import Callable

_PROJECT = Path(__file__).resolve().parent.parent
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))

from library.offspring_genome import generate_offspring_genome
from library.person import Person
from library.random_traits import GENOME_MAX_MAGNITUDE

_POS_LABELS = (
    "tens digit of integer part",
    "ones digit of integer part",
    "tenths",
    "hundredths",
)
_WEIGHTS = (55.0, 30.0, 10.0, 5.0)


def _split_magnitude_digits(mag: float) -> list[int]:
    capped = min(float(mag), GENOME_MAX_MAGNITUDE)
    n = int(round(capped * 100.0))
    n = max(0, min(9999, n))
    return [n // 1000, (n // 100) % 10, (n // 10) % 10, n % 10]


def _trace_two_digits(who: str, raw: float | None, rnd: Callable[..., object]) -> tuple[int, int]:
    """Same RNG pattern as offspring_genome._two_digits_from_parent_trait_value."""
    if raw is None:
        print(f"  {who}: (no trait) -> digits 0, 0")
        return 0, 0
    mag = abs(round(float(raw), 2))
    mag = min(mag, GENOME_MAX_MAGNITUDE)
    digits = _split_magnitude_digits(mag)
    print(f"  {who}: raw={raw!r} -> abs, clipped magnitude mag={mag:.2f}")
    slots = "".join(str(d) for d in digits)
    print(f"    Four slots (mantissa x100 -> 0000-9999): {slots!r}")
    for i, d in enumerate(digits):
        print(f"    slot {i} ({_POS_LABELS[i]}): {d}")

    pos1 = rnd("choices_pos1", lambda: random.choices(range(4), weights=list(_WEIGHTS), k=1)[0])
    remaining = [i for i in range(4) if i != pos1]
    w_rem = [_WEIGHTS[i] for i in remaining]
    total = sum(w_rem)
    print(
        f"    1st position draw (55/30/10/5): chosen slot {pos1} -> digit {digits[pos1]}"
    )
    print(
        "    2nd position (renormalized on remaining): "
        + ", ".join(
            f"slot {j} weight {w}/{total:.0f} ({100 * w / total:.2f}%)"
            for j, w in zip(remaining, w_rem)
        )
    )
    pos2 = rnd("choices_pos2", lambda: random.choices(remaining, weights=w_rem, k=1)[0])
    print(f"    2nd position draw: chosen slot {pos2} -> digit {digits[pos2]}")
    return digits[pos1], digits[pos2]


def _dummy_person(name: str, genome: dict[str, float]) -> Person:
    return Person(
        first_name=name,
        last_name="Demo",
        gender="Male",
        ethnic="Middle English",
        species="Human",
        birthyear=2000,
        genome=genome,
        sexual_nature="heterosexual",
        gender_mind="cisgender",
    )


def run_case(
    title: str,
    seed: int,
    trait: str,
    val_a: float | None,
    val_b: float | None,
) -> None:
    print("=" * 72)
    print(f"{title}  (seed={seed}, trait={trait!r})")
    print("=" * 72)

    source_a = float(val_a) if val_a is not None else float(val_b)  # type: ignore[arg-type]
    source_b = float(val_b) if val_b is not None else float(val_a)  # type: ignore[arg-type]

    def rnd(_tag: str, fn: Callable[[], object]) -> object:
        return fn()

    random.seed(seed)
    print("Parent A genome:", {trait: val_a} if val_a is not None else "(missing)")
    print("Parent B genome:", {trait: val_b} if val_b is not None else "(missing)")
    print(
        f"Effective sources for digit pools: A uses {source_a!r}, B uses {source_b!r}"
    )
    print()
    print("Digit extraction (parent A pool):")
    p1_d1, p1_d2 = _trace_two_digits("Parent A", source_a, rnd)
    print()
    print("Digit extraction (parent B pool):")
    p2_d1, p2_d2 = _trace_two_digits("Parent B", source_b, rnd)

    a_first = rnd("a_first", lambda: random.random() < 0.5)
    order = "A digits first (P1d1, P2d1, P1d2, P2d2)" if a_first else "B digits first (P2d1, P1d1, P2d2, P1d2)"
    print()
    print(f"Interleave order (50/50): {order}")

    if a_first:
        a, b, c, d = p1_d1, p2_d1, p1_d2, p2_d2
    else:
        a, b, c, d = p2_d1, p1_d1, p2_d2, p1_d2
    unsigned = (10 * a + b) + (10 * c + d) / 100.0
    sign_pos = rnd("sign", lambda: random.random() < 0.5)
    sign = 1.0 if sign_pos else -1.0
    traced = round(sign * unsigned, 2)
    print()
    print(
        f"Reconstruct: tens/ones = 10*{a}+{b} = {10 * a + b}; "
        f"frac = (10*{c}+{d})/100 = {(10 * c + d) / 100:.2f}; "
        f"unsigned = {unsigned:.2f}"
    )
    print(f"Sign (50/50, + if <0.5): {'+' if sign > 0 else '-'}")
    print(f"Traced offspring trait value: {traced:+.2f}")

    # Library (must match)
    random.seed(seed)
    pa = _dummy_person("A", {trait: val_a} if val_a is not None else {})
    pb = _dummy_person("B", {trait: val_b} if val_b is not None else {})
    child = generate_offspring_genome(pa, pb).get(trait)
    print()
    print(f"Library generate_offspring_genome (same seed): {child:+.2f}")
    assert child == traced, (child, traced)
    print("OK -- trace matches library.")
    print()


def main() -> None:
    run_case(
        "Set 1 - Plan-style pair",
        seed=202601,
        trait="physical",
        val_a=19.54,
        val_b=-61.98,
    )
    run_case(
        "Set 2 - Max magnitude vs near zero",
        seed=202602,
        trait="social",
        val_a=99.99,
        val_b=0.0,
    )
    run_case(
        "Set 3 - Small magnitudes",
        seed=202603,
        trait="mental",
        val_a=-3.21,
        val_b=7.89,
    )
    run_case(
        "Set 4 - Trait only on parent B (A missing)",
        seed=202604,
        trait="only_b",
        val_a=None,
        val_b=-41.41,
    )
    run_case(
        "Set 5 - Two high magnitudes",
        seed=202605,
        trait="temper",
        val_a=87.65,
        val_b=-92.31,
    )


if __name__ == "__main__":
    main()
