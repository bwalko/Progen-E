# Genome design (character traits)

This document captures the **design intent** for per-person genome scores: what the numbers mean, how they are rolled, and how config ties in. The authoritative implementation is `library.random_traits.choose_genome` (used from `library.generator.generate_person_random`); values live on `library.person.Person.genome` as `dict[str, float]` keyed by trait name from `config/genome.csv`.

---

## Core idea

- **Zero is the archetype / “best” numeric anchor** for each trait: a hypothetical person at **0 on every trait** is maximally aligned with the ideal (the “optimal centerpoint” described in the CSV row—not stored as a separate number on the person; it is the narrative label for the middle).

- Each person gets a **signed deviation from that ideal** per trait. The sign does **not** mean morally good or bad; it means **which side** of the ideal the individual sits on (toward the “deficient deviation” pole vs the “excess deviation” pole in the table). **+** and **−** are symmetric in role: both are “normal” at typical magnitudes.

- **Magnitude** (absolute distance from 0) matters for how **extreme** the expression is:
  - **Small |value|** (e.g. around **±10**): unusually **close** to ideal—**strong** for that trait in the “near optimal” sense.
  - **Moderate |value|** (e.g. around **±50**): **ordinary**—most people should land here most of the time.
  - **Large |value|** (e.g. toward **±90**): **far** from ideal—**weak** / dysfunctional expression for that trait in simulation terms.

So: **0 = best**, **|x| small = unusually good**, **|x| ~ 50 = typical**, **|x| large = unusually poor**.

---

## How random generation works (two steps)

For **each row** in the `genome` table (each `trait`):

1. **Magnitude** — Draw from a **normal (bell) distribution** centered on **50** with configurable spread, then **clip to [0, 100]**. That yields a **non-negative** “distance from ideal” before sign. Most draws cluster near ~50; values near 0 or 100 are rarer tails.

2. **Sign** — Independently choose **+** or **−** with baseline probability **0.5 / 0.5**, then adjust using **`gender_skew_high`** and **`gender_skew_low`** (see below). The final stored value is **`round(sign * magnitude, 1)`**.

Default tuning constants (overridable via `choose_genome` kwargs) live next to the implementation: `DEFAULT_GENOME_MAGNITUDE_MEAN`, `DEFAULT_GENOME_MAGNITUDE_STDEV`, `DEFAULT_GENOME_SKEW_STRENGTH`.

---

## Gender skew columns (`gender_skew_high` / `gender_skew_low`)

These only affect **step 2 (sign)**, not magnitude. Values are treated as **`male`**, **`female`**, or **`none`** (case-insensitive; `none` means no skew on that side).

- **`gender_skew_high`** — If it equals the person’s sex (`male` / `female` matching `Male` / `Female`), **P(positive sign)** increases by the skew strength (so that sex is **more likely** to sit on the **positive** side of the trait).

- **`gender_skew_low`** — If it equals the person’s sex, **P(positive sign)** **decreases** by the same strength (so that sex is **more likely** to sit on the **negative** side).

Both can apply to different sexes on the same row (e.g. physical: high `male`, low `female`). The implementation clamps the positive probability to a small band away from 0 and 1 so **either sign always remains possible**.

Interpretation in data authoring: skews encode **population-level** tendencies (e.g. which sex more often lands “high pole” vs “low pole” for that trait), not individual moral judgment.

---

## CSV columns beyond generation

The genome table also carries human-readable poles used for UI, narration, or future systems:

| Column | Role |
|--------|------|
| `trait` | Stable key; must match keys in `Person.genome`. |
| `deficient deviation` | Label for the **negative** side of the axis. |
| `optimal centerpoint` | Label for **0** / ideal. |
| `excess deviation` | Label for the **positive** side of the axis. |
| `gender_skew_high` / `gender_skew_low` | Sign bias tags as above. |
| `deficient description` / `optimal description` / `excess description` | Adjective forms of each pole used by `library.personality_interpreter.interpret_genome_personality` to build phrases that read as **"\<name\> is \<phrase\>"**, **"\<name\> is very \<phrase\>"**, **"\<name\> is incredibly \<phrase\>"**. The interpreter falls back to the matching `*deviation` / `optimal centerpoint` label when a description is blank, so old DB snapshots remain readable. |

Random generation today reads **`trait`**, **`gender_skew_high`**, and **`gender_skew_low`** from SQLite (`genome` table) after importing `config/*.csv` into the world’s `config.sqlite` (via `utils/util_load_config.py` or `library.config_import`).

## Derived per-person summaries (composites and trait phrases)

When `library.simulation_careers.assign_career_if_eligible` first assigns a job to a person, it computes two narrative summaries from the immutable signed genome and stores them on `Person` so reports and downstream systems do not have to recompute:

1. **`genome_composite_names`** — Top `composite_name` tags from `config/genome_composites.csv` whose component traits score above `library.genome_composites.GENOME_COMPOSITE_MIN_SCORE` (deduped, capped at `GENOME_COMPOSITE_MAX_TAGS`). See `library.genome_composites.significant_composite_names`.
2. **`genome_trait_phrases`** — Single-trait callouts from `library.personality_interpreter.interpret_genome_personality` using the prototype defaults (`DEFAULT_PERSONALITY_Z_CUTOFFS = (1.0, 2.0, 2.5, 3.0)`, `sigma = DEFAULT_GENOME_MAGNITUDE_STDEV`, `reference_magnitude = 50.0`). Only **extremes** are recorded: traits inside the ordinary band (`z ≤ c0`) produce no entry. Phrases use the `*description` columns from `config/genome.csv` and read as "\<name\> is \<phrase\>". Adverb intensifiers at higher z bands are: bare label → `very` → `extremely` / `remarkably` (toward dysfunction / toward ideal) → `clinically` / `incredibly` (top tier, "mental-illness-level" extremes vs. uncanny excellence).

Composite tag strings returned by `library.genome_composites.composite_row_name` are **normalized to lowercase** so persisted `Person.genome_composite_names` (and any downstream reports) stay consistent regardless of CSV casing.

Both summaries are also echoed into the `job_assigned` `simulation_events` payload, and survive `simulation_people` checkpoint round-trips via `library.world_save._person_from_dict`.

---

## Related files

- `config/genome.csv` — source data.
- `library/random_traits.py` — `choose_genome`, `_genome_trait_definitions`, `_p_positive_genome_sign`.
- `library/person.py` — `Person.genome`, `Person.genome_composite_names`, `Person.genome_trait_phrases`.
- `library/personality_interpreter.py` — `interpret_genome_personality` (single-trait extreme phrases).
- `library/genome_composites.py` — `significant_composite_names` (composite tag scoring).
- `library/simulation_careers.py` — `assign_career_if_eligible` populates both summaries when a person first gets a job.
- `dev_rules/config_schemas.md` — CSV column reference and project-wide config conventions.
- `dev_rules/session_start.md` — startup/testing workflow.
- `dev_rules/module_map.md` — quick orientation of related modules and tests.

---

## Guardrails for future edits

When changing genome behavior or docs, keep these invariants:

- **Signed, centered representation stays intact**: each trait value remains a signed deviation around ideal `0`.
- **Magnitude generation remains separate from sign generation**: distribution tuning should not silently change sign logic.
- **Sex skew only biases sign probability**: `gender_skew_high` / `gender_skew_low` should not alter magnitude.
- **Trait keys remain data-driven** from `config/genome.csv` -> SQLite `genome` table.
- **Person contract stays stable**: `Person.genome` remains `dict[str, float]`.

If you change any of these, update:

- this file (`dev_rules/genome.md`),
- `dev_rules/config_schemas.md` (if schema semantics changed),
- and tests that rely on generation assumptions.
