# Config file schemas

This project stores configuration as UTF-8 CSV files under `config/`. There are no separate JSON Schema, YAML, or TOML definition files; the **schema is the header row** of each CSV plus conventions described below.

---

## Summary

| File | Row count (approx.) | Primary key / grain |
|------|---------------------|---------------------|
| `config/ethnic.csv` | 7 ethnicities + header | One row per `ethnic` |
| `config/first_name.csv` | ~4.9k + header | One row per (`name`, `gender`, `ethnic`, …) |
| `config/last_name.csv` | ~2.7k + header | One row per (`name`, `ethnic`) |
| `config/species.csv` | 11 species rows + header | One row per (`species`, `ethnic`) |
| `config/genome.csv` | One row per trait + header | Trait keys + narrative poles + gender sign skew |
| `config/world_start.csv` | Worlds + mortality + header + **`population_scale`** | One row per `world`. `population_scale` (default `0.05`) is the **shared global** modifier applied to region `carrying_capacity` (see [`library/geography.py`](../library/geography.py)) **and** to `government_polity_types.min_population_to_form` / `max_population_before_split` and to settlement-leadership thresholds in `government_titles.csv`. |
| `config/world_geography_continents.csv` | Small + header | One row per (`world`, `continent_id`) |
| `config/world_geography_regions.csv` | Small + header | One row per (`world`, `region_id`) |
| `config/world_geography_routes.csv` | Small + header | One directed route per (`world`, `from_region_id`, `to_region_id`) |
| `config/world_geography_travel_eras.csv` | Bands + header | Historical-year bands per `world` (same scale as mortality milestones): friction scaling, cross-continent migration weight multiplier, disabled route types |
| `config/gender_mind.csv` | Small + header | Gendered mind rules (loaded to SQLite; generation hooks TBD) |
| `config/sexual_nature.csv` | Small + header | Loaded to SQLite; generation hooks TBD |
| `config/genome_jobs.csv` | One row per (`trait`, `deviation_band`) | Career/status/leadership tendencies and era-specific job examples derived from genome traits |
| `config/job_economics.csv` | ~450 rows + header | Per-era **base** (`*`) plus **deviation** multiplier rows for non-typical jobs (from `genome_jobs` + tier heuristics) |
| `config/job_market.csv` | Small + header, human-editable | Per-job market semantics: family, essential/luxury/urban demand, saturation, scarcity resilience, and settlement effect deltas |
| `config/government_eras.csv` | Small + header | Historical-year bands per `world` → allowed polity type ids + default succession style |
| `config/government_polity_types.csv` | Small + header | One row per `polity_type_id` (era, jurisdiction grain, head title, **min_population_to_form** = minimum **real-world** count to *bootstrap*/*promote* to that tier — multiplied by `world_start.population_scale` before comparison against alive in region; **max_population_before_split** = vassal split threshold, also scaled) |
| `config/government_titles.csv` | Small + header | One row per `title_id` (selection rule, term, male weight, usurp params, linked `polity_type_id` — `*` means "applies to all polity types" for universal per-settlement seats; **min_population_for_first_holder** + **pop_per_holder** = real-world settlement-population thresholds for population-scaled holder counts, also scaled by `world_start.population_scale`; **merit_takeover_chance** = per death-succession probability that an ambitious local leader replaces the hereditary heir, see `dev_rules/government.md` for the per-tier defaults) |
| `config/government_starting_polities.csv` | Header / optional seed | Optional explicit starter polities per `world` + `region_id` |

Cross-reference: `ethnic` values align across `ethnic.csv`, `first_name.csv`, `last_name.csv`, and `species.csv` (e.g. `Old Norse`, `Middle English`). **Genome semantics and RNG rules** are documented in **`dev_rules/genome.md`** (not repeated here in full). **Government runtime + save tables** are summarized in **`dev_rules/government.md`**.

---

## `config/ethnic.csv`

**Purpose:** Naming and kinship templates per culture/ethnicity, plus surname-generation weights.

| Column | Type / role | Notes |
|--------|-------------|--------|
| `ethnic` | string | Culture label; foreign key for name and species tables. |
| `kin_m` | string (template) | Male kinship suffix/pattern; `$` is a placeholder for the stem (e.g. `$sson` → patronymic style). |
| `kin_f` | string (template) | Female kinship pattern; may contain `$` and commas (CSV-quoted where needed). |
| `hails_from` | string | Prefix for “from” / locative style names (e.g. `of `, `van `, `von `). |
| `sur_kin_rate` | integer | Relative weight for surname style “kin”. |
| `sur_hails_rate` | integer | Relative weight for “hails from” style. |
| `sur_lookup_rate` | integer | Relative weight for lookup-style surname. |
| `sur_none_rate` | integer | Relative weight for no extra surname treatment. |
| `num_first_names` | string | Pattern like `1;1` or `1;3`; likely min/max or buckets for how many first names to combine (project-specific). |
| `num_last_names` | string | Same semicolon-separated pattern for last names. |
| `sep_first_names` | string | Optional separator(s) between multiple first-name parts; empty in current data. |
| `sep_last_names` | string | Optional separator(s) between multiple last-name parts; empty in current data. |
| `suppress_constructed` | integer (0/1) | Flag; `1` in all current rows—meaning likely “suppress constructed compound names” for that ethnicity. |

---

## `config/first_name.csv`

**Purpose:** Given names with gender, ethnicity, and weighting.

| Column | Type / role | Notes |
|--------|-------------|--------|
| `name` | string | Given name; will be read via `library.generator.generate_person_random` / DB (SQLite `first_name` table). |
| `gender` | string | Observed values: `Female`, `Male`. |
| `ethnic` | string | Must match `ethnic` in `ethnic.csv` / `last_name.csv`. |
| `name_part` | integer | Likely part index when building multi-part names (`1` in samples). |
| `rate` | integer | Relative selection weight for random draws. |

---

## `config/last_name.csv`

**Purpose:** Family names by ethnicity with weighting.

| Column | Type / role | Notes |
|--------|-------------|--------|
| `name` | string | Family name; will be read via generation helpers (SQLite `last_name` table). |
| `ethnic` | string | Must match `ethnic` elsewhere. |
| `rate` | integer | Relative selection weight. |

---

## `config/species.csv`

**Purpose:** Species (or ancestry) tied to a default `ethnic`, spawn/select rates, age bands, body metrics, and appearance pools for male (`m*`) and female (`f*`) presentation.

| Column | Type / role | Notes |
|--------|-------------|--------|
| `species` | string | Species or hybrid label (e.g. `Human`, `Half-Orc`, `Dwarf`). |
| `ethnic` | string | Default culture for that row; pairs with `species`. |
| `rate` | integer | Relative weight (e.g. `Human` rows use `10`–`20`; many non-Humans use `0` in sample—interpretation is project-specific). |
| `maturity` | integer | Age threshold (years). |
| `prime` | integer | Age threshold. |
| `middleaged` | integer | Age threshold. |
| `elder` | integer | Age threshold. |
| `lifespan` | integer | Maximum or typical lifespan (years). |
| `mheight` / `fheight` | integer | Height (likely cm); baseline for mature height draws in `library.random_traits`. |
| `mbmi` / `fbmi` | integer | Mean BMI baseline for that sex; used with height to sample weight in `choose_weight_kg`. If zero or missing, code falls back to a generic BMI distribution. |
| `mskin` / `fskin` | string | Skin tone tokens; multiple options separated by `;`. |
| `mhair` / `fhair` | string | Hair color/style tokens; `;`-separated lists. |
| `meyes` / `feyes` | string | Eye color tokens; `;`-separated lists. |

---

## `config/genome.csv`

**Purpose:** Define each simulation **trait** name, the **narrative labels** for the low pole, ideal, and high pole, and optional **gender sign skew** for random genome rolls.

| Column | Type / role | Notes |
|--------|-------------|-------|
| `trait` | string | Key used in `Person.genome` (e.g. `physical`, `intellect`). |
| `deficient deviation` | string | Label for the **negative** side of the signed deviation. |
| `optimal centerpoint` | string | Label for **0** / ideal. |
| `excess deviation` | string | Label for the **positive** side of the signed deviation. |
| `gender_skew_high` | `male` / `female` / `none` | That sex gets higher **P(+)** when rolling sign; `none` = no effect. |
| `gender_skew_low` | `male` / `female` / `none` | That sex gets lower **P(+)** (more **−**); `none` = no effect. |
| `deficient description` | string | Adjective form of the deficient pole, designed to fit "<name> is …" / "<name> is very …" / "<name> is incredibly …". Consumed by `library.personality_interpreter.interpret_genome_personality`; falls back to `deficient deviation` if blank. |
| `optimal description` | string | Adjective form of the optimal centerpoint (same sentence pattern); falls back to `optimal centerpoint`. |
| `excess description` | string | Adjective form of the excess pole (same sentence pattern); falls back to `excess deviation`. |

**Design and RNG:** See **`dev_rules/genome.md`** (0 = ideal, magnitude ~ bell on [0,100], sign + skew).

---

## `config/job_economics.csv`

**Purpose:** Settlement/regional economy weights for assigned jobs (`library.simulation_economy`). Rows are generated from `genome_jobs` via `utils/util_extract_job_economics_skeleton.py` (keyword tiers + premium bump), with **base** lines per era and **deviation** lines only where a job differs from the era baseline.

| Column | Type / role | Notes |
|--------|-------------|-------|
| `job_key` | string | Normalized title; `*` with `row_kind=base` defines the era baseline (absolute numbers). |
| `era` | `prehistoric` / `bronze_age` / `iron_age` / `medieval` / `modern` / `*` | Era for the row; `*` base row is a global fallback. |
| `row_kind` | `base` / `deviation` | **`base`**: `job_key` must be `*`; `pool_draw`, `wage_yield`, `value_add`, `tax_rate` are **absolute** scales for that era. **`deviation`**: same columns are **multipliers** on the era base; **blank cell = 1.0** (inherit). |
| `pool_draw` | float | Base: draw weight on settlement pool. Deviation: multiplier on base `pool_draw`. |
| `wage_yield` | float | Base: wage proxy scale. Deviation: multiplier (may be ≫1 for chiefs, experts; ≪1 for marginal labor). |
| `value_add` | float | Base: value-add to settlement pool. Deviation: multiplier on base `value_add`. |
| `tax_rate` | float | Base: treasury share. Deviation: multiplier on base `tax_rate`. |

**Legacy:** If `job_economics` has no `row_kind` column (older saves), every row is read as **absolute** `JobEconomicsParams` (pre–base/deviation format).

---

## `config/job_market.csv`

**Purpose:** Human-editable market semantics for normalized job titles. `library.job_market.JobMarketCatalog` reads explicit rows when present and falls back to keyword inference for flavor titles or older test fixtures. This table tells the career and economy systems what a job *does* in a settlement, while `genome_jobs.csv` remains the trait-to-job authoring surface.

| Column | Type / role | Notes |
|--------|-------------|-------|
| `job_key` | string | Normalized title from `library.job_economics.normalize_job_catalog_key`; `*` is the default row. |
| `job_family` | controlled-ish string | Suggested families: `food`, `labor`, `craft`, `care`, `security`, `trade`, `admin`, `knowledge`, `ritual`, `prestige`, `entertainment`, `criminal`, `domestic`, `transport`. |
| `essential_need` | float `0.00..1.00` | Baseline functional need, especially in small or stressed settlements. |
| `luxury_need` | float `0.00..1.00` | Surplus/urban/status demand; rises with market pull and falls under scarcity. |
| `urban_scale` | float `0.00..1.00` | How much settlement scale is required before the job has a real local market. |
| `scarcity_resilience` | float `0.00..1.00` | How well demand survives food/resource pressure. Survival jobs are high; luxury specialists are low. |
| `saturation_curve` | `flat` / `medium` / `steep` | How quickly local demand drops when the settlement already has that job/family. |
| `food_delta` | float `-1.00..1.00` | Annual local food-pressure relief or harm from productive workers in this job. |
| `stability_delta` | float `-1.00..1.00` | Annual local stability effect. Guards and good admin trend positive; criminal roles trend negative. |
| `care_delta` | float `-1.00..1.00` | Care/support contribution for future household and social systems. |
| `capacity_delta` | float `-1.00..1.00` | Infrastructure/capacity support from builders, engineers, crafts, etc. |
| `taxability` | float `0.00..1.00` | How visible/taxable the job's income is for treasury intake. |

`job_market.csv` is intentionally semantic rather than exhaustive. Add explicit rows for titles that need hand tuning; otherwise the loader infers reasonable defaults from title keywords.

---

## `config/genome_jobs.csv`

**Purpose:** Assign tendency-based simulation jobs from existing genome traits. Rows describe how one trait band influences status movement, leadership perception/quality, and example jobs by historical era. The simulation treats this as probabilistic guidance: people do not need perfect or extreme values to match a row, but stronger trait expression makes that row more likely.

| Column | Type / role | Notes |
|--------|-------------|-------|
| `trait` | string | Must match a `config/genome.csv` trait key stored in `Person.genome`. |
| `deviation_band` | `deficient` / `optimal` / `excess` | Which side of the signed genome value this row describes. `optimal` scores near `0`; `deficient` scores negative values; `excess` scores positive values. |
| `descriptor` | string | Human-readable label for the trait band. |
| `status_tendency` | string | Tendency for social status to rise/fall/volatilize from this trait row. |
| `leader_quality` | string | How good the person is likely to be if placed in leadership. |
| `leader_tendency` | string | How likely others are to see or push the person as a leader. |
| `prehistoric_jobs` / `bronze_age_jobs` / `iron_age_jobs` / `medieval_jobs` / `modern_jobs` | semicolon-separated strings | Common, broadly employable example jobs for each era; primary pool for assignment. Optional suffix `` [M]`` (male-only) or `` [F]`` (female-only) on a token; the tag is stripped from the assigned job title. See **Sex-restricted job tokens** below. |
| `prehistoric_premium_jobs` / `bronze_age_premium_jobs` / `iron_age_premium_jobs` / `medieval_premium_jobs` / `modern_premium_jobs` | semicolon-separated strings (optional) | Rarer or more specialized titles for that era. When non-empty, `library.simulation_careers.choose_career_assignment` may draw from this pool instead of the common list if the person’s `career_fitness_score` clears a threshold (see `PREMIUM_JOB_FITNESS_THRESHOLD` / `PREMIUM_JOB_MAX_PROB` in that module). Empty cells behave like the legacy single-list model for that era. Same ``[M]`` / ``[F]`` token rules as common job columns. |
| `society_need` | float `0.00..1.00` | Strict settlement-function demand weight for the whole trait-band category. `1.00` means core function (food, health, protection, records, infrastructure, public order); mid values are useful support, specialist, or prestige roles; low values are niche, parasitic, destabilizing, or failure-prone roles even when narratively interesting. Used by `library.simulation_careers.choose_career_assignment` so smaller settlements favor simpler/core roles while large settlements can sustain more specialized markets. |
| `selfish_desperate` | float `0.00..1.00` | Attraction weight for selfish or scarcity-pressed people. High values mean the row offers easy returns, extraction, predation, survival fallback, hoarding, corruption, coercion, fraud, or low-barrier desperate work. This is intentionally **not** the inverse of `society_need`: some low-need roles are not attractive, and some needed low-skill fallback roles become attractive under deprivation. Career scoring increases this weight as resource pressure, unemployment duration, and low household savings raise the worker's desperation score. |
| `strong_pairings` / `role_cluster` / `overlap_notes` / `design_notes` | annotation strings | Authoring notes only; ignored by simulation logic. |

**Era mapping:** `library.simulation_careers.resolve_job_era` maps historical years to the era job columns: prehistoric before `-3300`, bronze age `-3300..-1201`, iron age `-1200..499`, medieval `500..1499`, modern `1500+`.

**Assignment age:** `library.simulation_careers.job_eligibility_age` assigns jobs earlier in older eras: prehistoric around half maturity, bronze age around two-thirds, iron age around three-quarters, medieval around five-sixths, and modern at maturity. Maturity uses `Person.min_fertility_age` when present, then `species.maturity`, then `18`.

**Employment volatility:** `library.simulation_careers` also derives an annual career fitness score from weighted genome deviation magnitudes. Near-ideal traits improve job stability; high-deviation traits reduce it, with `physical`, `intellect`, `symmetry`, `neurochemical`, `ambition`, `persuasion`, and `empathy` carrying extra weight. That same score gates optional draws from the era’s `*_premium_jobs` list when assigning a job. Local resource pressure increases job-loss risk and reduces rehire odds. Job loss, unemployment start/end, rehire, fitness updates, and job-seeker household migration are logged as append-only `simulation_events`.

**Trait-band job scoring:** `library.simulation_careers.score_genome_job_row` uses signed trait-band peaks rather than rewarding extremes: deficient rows peak around `-50`, optimal rows peak around `0`, and excess rows peak around `+50`; values close to `-100` / `+100` are treated as mania-level extremes and taper back down.

**Job event fitness:** Job lifecycle events (`job_assigned`, `job_lost`, `unemployment_started`, `unemployment_ended`) use `fitness_score` for the job-category fit shown in readers: broad `career_fitness_score` blended with the selected / existing job row's trait-band score. The same payload keeps `career_fitness_score` for the general work-fitness value and `job_trait_match_score` for the trait-only component.

**Sex-restricted job tokens:** A job token may end with `` [M]`` (defaults to male assignees only) or `` [F]`` (female only). Other `Person.gender` values only receive untagged jobs. Cross-gender assignment is allowed when all of: `gender_mind` is the cross pole (`masculine` for a female assignee taking an `[M]` job, `feminine` for a male assignee taking an `[F]` job), genome `mating drive` is sufficiently on the deficient side (low sex drive; threshold `CROSS_GENDER_MATING_DRIVE_THRESHOLD`), and `physical` passes a sex-specific band (`CROSS_GENDER_FEMALE_PHYS_MIN` / `CROSS_GENDER_MALE_PHYS_MAX` in `library.simulation_careers`). `job_assigned` events may include `job_sex_restriction` and `cross_gender_job_exception`.

---

## `config/world_geography_continents.csv`

**Purpose:** Immutable continent partition for compartmentalized world simulation.

| Column | Type / role | Notes |
|--------|-------------|--------|
| `world` | string | World key (joins to `world_start.world`). |
| `continent_id` | string | Stable continent identifier. |
| `continent_name` | string | Display label. |
| `keywords` | string | Plain-English hints (comma/semicolon-separated phrases). The simulation derives continent-wide physics (base elevation, precipitation band, resource bias, etc.) via `library.geography_inference` — see also `set_geography_inference_backend` for optional plug-in interpreters. |

---

## `config/world_geography_regions.csv`

**Purpose:** Canonical region layer where settlements and births attach. Links to continents via `continent_id`. Numeric/climate/drainage detail is **not** stored here; it is inferred from `keywords` plus the existing `biome` and `terrain` columns (`library.geography_inference`, surfaced through `library.geography.region_environment` and related helpers).

| Column | Type / role | Notes |
|--------|-------------|--------|
| `world` | string | World key. |
| `region_id` | string | Stable region identifier. |
| `region_name` | string | Display label. |
| `continent_id` | string | Foreign key to `world_geography_continents.continent_id`. |
| `biome` | string | Broad climate/ecology marker (used by placename / local geography hooks and inference). |
| `terrain` | string | Broad terrain marker (same). |
| `carrying_capacity` | integer | Soft population capacity for settlement growth pressure. |
| `keywords` | string | Plain-English hints for soils, hydrology, relief, human land-use cues, etc. Combined with `biome`/`terrain` for deterministic inference. |

---

## `config/world_geography_routes.csv`

**Purpose:** Directed route graph with friction controlling movement/trade.

At runtime, `library.geography.list_routes_from` does **not** query this table alone: `library.route_inference.augment_routes_with_inference` merges CSV rows with (1) reverse legs where `bidirectional` is set but the return row is missing, and (2) minimal same-continent `land` bridges so the land graph on each continent is connected (higher friction than typical authored links). Sea edges are only mirrored when `bidirectional` requests it; they are never auto-bridged across continents.

| Column | Type / role | Notes |
|--------|-------------|--------|
| `world` | string | World key. |
| `from_region_id` | string | Route source region. |
| `to_region_id` | string | Route destination region. |
| `route_type` | string | Route class (e.g. `land`, `sea`). |
| `friction` | number | Cost/difficulty scalar; higher = less likely movement. |
| `bidirectional` | integer (0/1) | Data hint for humans; loader still stores explicit directed rows. |

---

## `config/world_geography_travel_eras.csv`

**Purpose:** Time-varying travel rules keyed by **historical year** — the same signed calendar as `historical_mortality_milestones.year`. Simulation calendar maps via `world_start` (see `library.geography.resolve_travel_era`).

| Column | Type / role | Notes |
|--------|-------------|--------|
| `world` | string | World key. |
| `history_year_from` | integer | First historical year in this band (inclusive). |
| `history_year_to` | integer or empty | Last historical year inclusive; empty = no upper bound. |
| `land_friction_multiplier` | number | Scales base friction for non-``sea`` routes. Calibrated from overland travel-time gauges vs a ~70-day / ~1000-mile reference (Medieval pack horse baseline). Invalid ≤ 0 → treated as ``1``. |
| `sea_friction_multiplier` | number or empty | Scales friction for routes with ``route_type`` = ``sea``. Calibrated vs a ~50-day / ~1000-mile early-maritime baseline. Empty → reuse ``land_friction_multiplier``. Invalid ≤ 0 → reuse land value. |
| `cross_continent_weight_multiplier` | number or empty | Multiplier on migration sampling weight when origin/destination continents differ (**higher = cross-continent moves more likely**; typical ≤ 1). Empty: use ``choose_migration_destination``’s ``cross_continent_weight_fallback``. |
| `route_types_disabled` | string | Semicolon-separated `route_type` values (e.g. `sea`) to exclude from routing for this era. |

**Calibration (order-of-magnitude gauges, ~1000-mile journeys):**

- Land multipliers derive from indicative overland durations (bronze-era foot ~80 d … railway era ~3 d … highway ~¾ day) scaled against a **Medieval (~1200 CE) ~70 day** reference.
- Sea multipliers derive from indicative blue-water/coastal durations (early maritime ~50 d … classical freighter ~20 d … steam ~7 d … modern ~2 d) scaled against an **Early Maritime (~2500 BCE coastal) ~50 day** baseline.
- Rows are chunked into contiguous historical bands; values within a band blends nearby anchors where a single millennium-wide row would otherwise average unrelated modes.

Overlapping bands for the same `world`: the row with the largest `history_year_from` that still contains the mapped historical year wins.

---

## Code reference

CSV files under `config/` are loaded into **`worlds/<world_id>/config.sqlite`** by `utils/util_load_config.py` (one SQLite table per file, e.g. `ethnic`, `first_name`, `last_name`, `species`, `genome`, `world_start`, `gender_mind`, `sexual_nature`, …).

**`generate_person_random`** in `library.generator` reads **`species`** (body, appearance, age bands), builds **`Person.genome`** via `library.random_traits.choose_genome` from the **`genome`** table, and uses **`world_start`** + **`species`** for life-stage / age sampling (`choose_life_stage_and_age`, backed by `utils.util_age_distribution`). **`first_name`** / **`last_name`** / full naming integration are still planned; the **`ethnic`** table remains for kinship / constructed-name logic. **`generate_person_from_birth`** is not implemented yet.

---

## Conventions

- **Encoding:** UTF-8 (`utf-8-sig` in `util_load_config.py` for CSV import).
- **CSV:** Standard comma-separated; fields with commas are quoted (e.g. `kin_f` in `ethnic.csv`).
- **Multi-valued cells:** Semicolon (`;`) separates allowed tokens in appearance columns and in `num_*` patterns.
