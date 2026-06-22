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
| `config/genome_save_columns.csv` | One row per genome save slot | Compact checkpoint slot → trait mapping |
| `config/world_start.csv` | Worlds + mortality + header + **`population_scale`** + **`magic_physical_leveling_01`** | One row per `world`. `population_scale` (default `0.05`) is the **shared global** modifier applied to region `carrying_capacity` (see [`library/geography.py`](../library/geography.py)) **and** to `government_polity_types.min_population_to_form` / `max_population_before_split` and to settlement-leadership thresholds in `government_titles.csv`. `magic_physical_leveling_01` defaults to `0.0` and lets fantasy worlds soften body-demand scoring for physical jobs and force-backed authority. |
| `config/incident_rates.csv` | Small + header | Era-tuned detailed-incident knobs per (`world` or `*`, `incident_key`, historical-year band): murder target per 10k/year plus chance/cap multipliers for other incident families. |
| `config/remarkable_archetypes.csv` | Small + header, human-editable | Rare historically visible archetype opportunity weights, trait/composite score recipes, context weights, and target event families. Percentages apportion triggered rare opportunities, not the detailed population. |
| `config/event_catalog.csv` | Small + header | Authored event/incident kinds per (`event_type`, `incident_kind`): family, display label, context tags, consequence profile, default memory record type/visibility, and selection weight. |
| `config/event_ontology.csv` | Small + header | Workstream-level event ontology per `event_key`: required context, trait/precondition/witness signals, consequence hooks, importance range, preservation defaults, and public unknown/rumored/known views. |
| `config/innovation_source_rows.csv` | Generated + header | Trace rows parsed from `Timeline of historic inventions.wiki`; source line, headings, normalized dates, cleaned source title/summary, wiki links, and parse notes. |
| `config/innovations.csv` | Generated/curated + header | Active simulation innovation catalog; local analogue rows drive startup seeding, discovery eligibility, adoption, diffusion, prosperity, and port-network knowledge. |
| `config/innovation_eras.csv` | Small + header | Historical-year era bands and adoption thresholds for local/place/polity effective innovation era state. |
| `config/innovation_category_rules.csv` | Small + header | Per-category rank/log-gap gates, discovery/spread multipliers, same-polity diffusion strength, and prosperity weights. |
| `config/world_geography_continents.csv` | Small + header | One row per (`world`, `continent_id`) |
| `config/world_geography_regions.csv` | Small + header | One row per (`world`, `region_id`) |
| `config/world_geography_routes.csv` | Small + header | One directed route per (`world`, `from_region_id`, `to_region_id`) |
| `config/world_geography_travel_eras.csv` | Bands + header | Historical-year bands per `world` (same scale as mortality milestones): friction scaling, cross-continent migration weight multiplier, disabled route types |
| `config/gender_mind.csv` | Small + header | Gendered mind rules (loaded to SQLite; generation hooks TBD) |
| `config/sexual_nature.csv` | Small + header | Loaded to SQLite; generation hooks TBD |
| `config/genome_jobs.csv` | One row per (`trait`, `deviation_band`) | Career/status/leadership tendencies and era-specific job examples derived from genome traits |
| `config/job_economics.csv` | ~450 rows + header | Per-era **base** (`*`) plus **deviation** multiplier rows for non-typical jobs (from `genome_jobs` + tier heuristics) |
| `config/job_market.csv` | Small + header, human-editable | Per-job market semantics: family, essential/luxury/urban demand, saturation, scarcity resilience, and settlement effect deltas |
| `config/job_archetypes.csv` | Small + header, human-editable | Social/job-market semantics over normalized titles: household care, domestic service, vice/criminal/office pools, class/status, care intensity, body demand, force authority, informal-role status, adult-only gates, and board/cash compensation |
| `config/status_echelons.csv` | Small + header, human-editable | Prestige/status bands derived from standing, household prosperity, class, and job market type; drives elite job access, patronage power, service demand, scandal severity, and elite investment |
| `config/ethnic_proto_placewords.csv` | Proto/toponym stems by ethnicity and feature | One row per ethnic + feature type + concept |
| `config/government_eras.csv` | Small + header | Historical-year bands per `world` → allowed polity type ids + default succession style |
| `config/government_polity_types.csv` | Small + header | One row per `polity_type_id` (era, jurisdiction grain, head title, **min_population_to_form** = minimum **real-world** count to *bootstrap*/*promote* to that tier — multiplied by `world_start.population_scale` before comparison against alive in region; **max_population_before_split** = vassal split threshold, also scaled) |
| `config/government_titles.csv` | Small + header | One row per `title_id` (selection rule, term, male weight, force-authority demand, usurp params, linked `polity_type_id` — `*` means "applies to all polity types" for universal per-settlement seats; **min_population_for_first_holder** + **pop_per_holder** = real-world settlement-population thresholds for population-scaled holder counts, also scaled by `world_start.population_scale`; **merit_takeover_chance** = per death-succession probability that an ambitious local leader replaces the hereditary heir, see `dev_rules/government.md` for the per-tier defaults) |
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

## `config/genome_save_columns.csv`

**Purpose:** Configure the compact trait slot order used when checkpointing `Person.genome` and `Person.mind_body` into `save.sqlite`.

Runtime code still uses normal dictionaries keyed by trait name. The compact save payload stores trait values as arrays (`g` for genome, `mb` for mind/body) and expands them through this table on resume and in the data browser.

| Column | Type / role | Notes |
|--------|-------------|-------|
| `slot` | string | Short readable storage slot (`a`..`z`, then `aa`, `ab`, etc.). |
| `trait` | string | Trait key from `config/genome.csv`. |
| `sort_order` | integer | Array position; must stay stable for saves written with this config. |

When adding or renaming a genome trait, update both `genome.csv` and this mapping. Because this project is pre-alpha, old saves may be deleted/regenerated instead of migrated.

---

## `config/incident_rates.csv`

**Purpose:** Era-specific tuning knobs for detailed incident materialization in
`library.simulation_incidents`. Rows are resolved by `library.incident_rates` by
mapping the simulation year to a historical year through
`SimulationContext.get_historical_year(...)`, then picking the matching row with
the latest `history_year_from`. World-specific rows override `*` rows.

| Column | Type / role | Notes |
|--------|-------------|-------|
| `world` | string | `*` for global defaults, or a specific `world_start.world` value. |
| `incident_key` | string | Current keys: `murder`, `property_crime`, `affair_scandal`, `public_virtue`, `knowledge_culture`, `remarkable_archetype`. Unknown keys are ignored unless code starts asking for them. |
| `history_year_from` | integer | First historical year in the band, inclusive. |
| `history_year_to` | integer or empty | Last historical year in the band, inclusive; empty means open-ended. |
| `target_per_10k_per_year` | float or empty | Direct detailed-population target for incidents with calibrated population-rate logic. Currently used by `murder`; blank falls back to the code default for that family. |
| `chance_multiplier` | float `>=0` | Multiplies the per-settlement chance gate and the settlement chance cap. `0` disables chance generation for that row. |
| `annual_cap_multiplier` | float `>=0` | Multiplies the annual materialized-event cap for that incident family. `0` disables materialization for that row. |
| `notes` | string | Human tuning notes only; ignored by runtime logic. |

The initial medieval rows keep murder at the current target of about **4
murders per 10,000 detailed people per year** while raising property-crime and
scandal visibility because the first review samples showed those slices were
even quieter than the old undercounted murder slice. Re-run
`utils/util_event_history_report.py` after changing this CSV.

---

## `config/remarkable_archetypes.csv`

**Purpose:** Rare archetype-event authoring for
`library.simulation_remarkable_archetypes`. The simulator first opens a rare
mixed-population-scaled opportunity, then uses `share_weight` to choose the
archetype bucket. These weights are **not** demographic percentages and must not
be applied to all detailed people.

| Column | Type / role | Notes |
|--------|-------------|-------|
| `archetype_key` | string | Stable key stored in event payloads. |
| `bucket` / `display_name` | string | Grouping and readable label for reports/browser details. |
| `share_weight` | float `>=0` | Relative share among triggered remarkable opportunities. The shipped rows sum to 100 for human readability. |
| `trait_factors` | semicolon list | `trait|mode|weight`; mode is `ideal_strength`, `positive_extreme`, or `negative_extreme` from `library.event_scoring`. |
| `composite_weights` | semicolon list | `composite name|weight`; names are matched case-insensitively against `Person.genome_composite_names`. |
| `role_weights` / `pressure_weights` / `opportunity_weights` | semicolon list | `tag|weight`; tags come from role inference plus settlement/court/war/trade/archive/temple/workshop context. |
| `event_options` | semicolon list | `event_type|incident_kind|weight|domain`; event types reuse existing catalog/save families such as `knowledge_culture`, `public_virtue`, `political_crime`, `religious_cultural_conflict`, `private_life`, `status_rise`, `patronage_granted`, `elite_household_investment`, `outlaw_case_opened`, and `city_state_*`. |
| `minimum_score` | float | Minimum candidate score in a capped detailed sample. Background promotion uses a lower threshold but is decade-cooldown-limited. |
| `importance_min` / `importance_max` | float | Historical-importance range for the emitted event. |
| `promotion_allowed` | bool | Allows one passive/nondetailed promotion when mixed-population pressure has no detailed candidate and the promotion cooldown is clear. |
| `notes` | string | Human tuning notes only. |

Runtime note: default opportunity count is tiny: expected events per year are
`min(2, 0.02 + mixed_world_population / 100000)` before incident-rate
multipliers. Detailed people supply bounded visible candidates; passive and
nondetailed populations only scale opportunity pressure unless a rare promotion
fallback is used.

---

## `config/event_catalog.csv`

**Purpose:** Authored catalog rows for detailed event/incident kinds used by
`library.event_catalog` and `library.simulation_incidents`. The active simulator
still chooses broad incident families through bounded trait/context logic;
catalog rows provide concrete `incident_kind` variants within those families.
The catalog also contains dormant Workstream 1 rows for future political,
religious/cultural, and private-life generators so every ontology key has a
curated catalog entry before sampling code exists.

| Column | Type / role | Notes |
|--------|-------------|-------|
| `event_type` | string | Active factual event type such as `murder`, `property_crime`, `affair_scandal`, `public_virtue`, or `knowledge_culture`, or a dormant broad family such as `political_crime`, `religious_cultural_conflict`, or `private_life` for future generators. |
| `incident_kind` | string | Concrete subtype stored in event payloads, e.g. `storehouse_robbery`, `river_rescue`, `succession_precedent`. |
| `event_family` | string | Broad authoring family such as `violent_crime`, `property_survival_crime`, `household_scandal`, `political_crime`, `religious_cultural_conflict`, `public_virtue`, `knowledge_culture`, or `private_life`. |
| `display_name` | string | Human-readable label. Prose currently falls back to labelizing `incident_kind`, but this is the authoring surface for future template selection. |
| `context_tags` | semicolon-separated strings | Tags used by generator classifiers to pick variant pools, e.g. `theft;scarcity`, `rescue;danger`, `legal;succession`. Matching is any-tag. |
| `consequence_profile` | string | Current profiles include `death`, `property_loss`, `relationship_fallout`, `public_relief`, and `knowledge_state`. |
| `default_record_type` | string | Expected memory-record type for this kind; the save layer currently derives default records from `event_type`, while explicit public records can override this. |
| `default_visibility` | string | Expected initial memory visibility such as `rumored`, `public_unknown`, or `public_known`; explicit event-record helpers can use these public stages directly. |
| `selection_weight` | float `>=0` | Relative weight when multiple catalog rows match the same event type and context tags. |
| `notes` | string | Human tuning notes only; ignored by runtime logic. |

Rows currently expand the initial vertical slices with more crime, rescue,
legal, invention, and succession-adjacent variants, and now cover every
Workstream 1 ontology key as an authored catalog row. Some alias rows such as
`rescue`, `mercy`, and `arbitration` have zero active selection weight because
the current generator uses concrete variants such as `heroic_rescue`,
`public_mercy`, and `public_arbitration`; keep those weights at zero unless the
runtime selector and tests are intentionally retuned. The `knowledge_culture`
slice includes portable mercantile/maritime variants such as
`shipbuilding_advance`, `navigation_discovery`, `writing_system`,
`accounting_method`, `trade_law_precedent`, `standard_container`, and
`luxury_dye_recipe`; `library.simulation_incidents` maps them to domain-state
keys such as `navigation`, `shipbuilding`, `writing`, `accounting`,
`trade_law`, and `craft`. `library.event_catalog` falls back to legacy
built-in rows if the table is absent in an old or fixture config DB.

Runtime note: `knowledge_state` event payloads may include
`consequences.knowledge_state_diffusion`, a list of bounded sea-route diffusion
deltas. Each diffusion item targets a destination region/domain with a reduced
delta based on route friction. `library.world_save` applies those rows to
`simulation_domain_states` in addition to the primary region delta.

---

## `config/event_ontology.csv`

**Purpose:** Authoring/spec rows for the narrative event ontology described in
`TODO.md` Workstream 1. This table is broader than the runtime incident catalog:
rows can exist before a generator uses them, so future event systems have a
defined context/probability/consequence/public-memory shape before code starts
sampling them.

`library.event_ontology` loads these rows from config SQLite and falls back to
the five current vertical-slice families when an old or fixture config DB lacks
the table.

| Column | Type / role | Notes |
|--------|-------------|-------|
| `event_key` | string | Stable ontology key such as `murder`, `storehouse_robbery`, `heresy_accusation`, or `secret_kindness`. |
| `event_family` | string | Broad family such as `violent_crime`, `property_survival_crime`, `household_scandal`, `political_crime`, `religious_cultural_conflict`, `public_virtue`, `knowledge_culture`, or `private_life`. |
| `minimum_context` | semicolon-separated strings | Required actor/place roles before a generator should materialize the event. |
| `probability_traits` | semicolon-separated strings | Trait or composite signals that should raise/lower probability; these remain propensities, not deterministic labels. |
| `preconditions` | semicolon-separated strings | Non-genome circumstances such as scarcity, grievance, office tension, or existing relationship state. |
| `likely_witnesses` | semicolon-separated strings | Plausible witness/source groups for event-memory records. |
| `consequence_hooks` | semicolon-separated strings | Candidate simulation feedback such as `death`, `property_loss`, `legal_fallout`, `faction_memory`, `knowledge_state`, or `obligation`. |
| `importance_min` / `importance_max` | float | Expected historical-importance range for tuning and prose sampling. |
| `default_record_type` / `default_visibility` | string | Expected event-memory defaults when this ontology key is materialized. |
| `preservation_defaults` | semicolon-separated strings | How records tend to survive, distort, or vanish. |
| `public_unknown_view` | string | Public-facing unresolved view, e.g. a person is missing but the cause is unknown. |
| `public_rumored_view` | string | Public-facing rumor/distortion view, e.g. an uncertain tale blames a monster, faction, curse, or rival. |
| `public_known_view` | string | Public-facing known-history view, ideally matching the factual event when truth is established. |
| `prose_tone_variants` | semicolon-separated strings | Suggested tone slots such as `rumor`, `court_record`, `household_memory`, `temple_chronicle`, `bardic`, or `later_reconstruction`. |
| `notes` | string | Human tuning notes only; ignored by runtime logic. |

The save layer represents those public views with `simulation_event_records`.
A single factual event can have multiple record keys: for example
`public_unknown` for "Lio went missing", `rumored` for "Lio was taken by a
monster", and `public_known` for "Fred murdered Lio". Admin/debug views still
read the factual `simulation_events` row as the truth.

---

## Innovation Timeline Config

**Purpose:** `Timeline of historic inventions.wiki` is preserved as source
markup. `utils/util_parse_inventions_wiki.py` parses it into trace rows and a
curated gameplay catalog. Runtime uses local analogue names from
`config/innovations.csv`, not literal Earth invention names as event prose truth.

### `config/innovation_source_rows.csv`

Generated trace table. Rebuild with `python utils/util_parse_inventions_wiki.py`
after changing the wiki source or parser.

| Column | Type / role | Notes |
|--------|-------------|-------|
| `source_id` | string | Stable generated row id (`invsrc_####`). |
| `source_file` / `source_line` | string / integer | Original wiki file and source line for audit. |
| `section` / `subsection` | string | Wiki heading context. |
| `date_text` | string | Original cleaned date expression. |
| `history_year_from` / `history_year_to` / `history_year` | integer | Signed historical-year range and representative year; BCE is negative. |
| `date_quality` | string | `exact`, `range`, `approximate`, `century`, `millennium`, or `unparsed`. |
| `title` / `summary` | string | Cleaned source title and summary text. |
| `wiki_links` | semicolon list | Extracted wiki link targets. |
| `parse_notes` | semicolon list | Parser warnings or approximation notes. |

### `config/innovations.csv`

Gameplay truth for innovations. `library.innovation_catalog` loads only
`curation_status` values `active`, `reviewed`, or `seed`; unreviewed/inactive
rows are ignored by runtime.

| Column | Type / role | Notes |
|--------|-------------|-------|
| `innovation_id` | string | Stable simulation id. |
| `source_id` / `source_link` / `source_title` | string | Trace back to `innovation_source_rows.csv` and wiki line. |
| `analogue_name` | string | Localized name used in simulation payloads/events. |
| `category` / `domain` | string | Category gates progression; domain feeds `knowledge_culture` domain-state consequences. |
| `era_id` | string | Must match `innovation_eras.era_id` where possible. |
| `history_year` / `history_year_from` / `history_year_to` | integer | Historical-year placement used for startup seeding and discovery timing. |
| `rank` | integer | Per-category frontier rank; military and other strict categories advance by rank. |
| `spreadability` / `complexity` / `starter_prevalence` | float `0..1` | Diffusion, discovery difficulty, and startup commonness. |
| `prerequisite_ids` | semicolon list | Required known innovations before discovery. |
| `curation_status` | string | `active`, `reviewed`, `seed`, `unreviewed`, or `inactive`. |
| `notes` | string | Curation/balance notes. |

### `config/innovation_eras.csv`

Defines broad historical era bands. `advancement_threshold` is the number of
adopted active innovations from an era needed for a place/polity to claim that
effective innovation era in `simulation_innovation_era_state`.

### `config/innovation_category_rules.csv`

Per-category rules used by `library.simulation_innovation`: `max_rank_jump`,
`max_log_gap` for logarithmic time-gap penalties, `base_discovery_chance`,
`spread_multiplier`, `polity_spread_multiplier`, and `wealth_weight`. Military
uses a stricter `max_rank_jump` so one high-propensity creator cannot leapfrog
multiple weapon frontiers.

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

## `config/job_archetypes.csv`

**Purpose:** Human-editable social semantics for normalized job titles. `library.job_archetypes.JobArchetypeCatalog` reads explicit rows when present and falls back to keyword inference for old fixture DBs. This layer tells careers, household care, economy, incidents, save/schema, and the browser whether a title is ordinary settlement labor, unpaid household care, domestic service, officeholding, vice, criminal work, or no market job.

| Column | Type / role | Notes |
|--------|-------------|-------|
| `job_key_pattern` | string | Normalized title pattern; `*` is the default row. |
| `match_type` | `default` / `exact` / `contains` / `prefix` / `suffix` | Exact rows win before pattern rows; pattern rows are read in CSV order. |
| `job_market_type` | enum | Current values: `settlement_market`, `household_care`, `domestic_service`, `criminal`, `vice`, `office`, `none`. |
| `role_family` | string | Broad social role such as `care`, `domestic`, `authority`, `vice`, or `labor`. |
| `workplace` | string | Coarse work setting (`home`, `settlement`, `employer_household`, `office`, etc.). |
| `skill_level` | string | Coarse skill/status cue (`ordinary`, `skilled`, `elite`). |
| `manuality` | string | `manual`, `cognitive`, `social`, or `mixed`; currently descriptive. |
| `supervision_level` | string | `self_directed`, `peer`, `supervised`, `supervisory`, or `informal`; currently descriptive. |
| `class_band` | string | Saved to `Person.social_class_band` for browser/person detail and downstream status logic. |
| `personal_prosperity_01` | float 0..1 | Baseline personal survival/wage value outside normal settlement wage allocation. Household care should stay low. |
| `societal_impact_01` | float 0..1 | Saved to `Person.societal_impact_01`; child rearing should be high despite low cash wage. |
| `public_prestige_01` | float 0..1 | Prestige component for `social_standing_01`. |
| `perceived_worth_01` | float 0..1 | Social valuation component for `social_standing_01`. |
| `care_intensity_01` | float 0..1 | Household-care contribution signal; high for child rearers and nannies. |
| `home_compatible` | integer/bool | Allows assignment under primary childcare pressure and affects care compatibility. |
| `domestic_service_kind` | string or empty | `nanny`, `maid`, `servant`, `household_manager`, etc.; persisted in service contracts. |
| `female_mindset_affinity_01` | float 0..1 | Domestic-service candidate skew toward female/feminine mindsets without making the role exclusive. |
| `adult_only` | integer/bool | Blocks assignment under adult age for vice, domestic service, office, and other adult roles. |
| `board_compensation_01` | float 0..1 | Board/lodging share for household service contracts. |
| `cash_wage_multiplier` | float >=0 | Multiplier for non-settlement cash wage/survival income. |
| `physical_demand_01` | float 0..1 | Body-demand requirement for the title. High values fit hard labor, transport, combat, and other physically punishing work; low values fit social, cognitive, care, and office roles. |
| `force_authority_01` | float 0..1 | How much the job or role depends on force-backed authority or credible physical enforcement. Used for office/security/criminal/command style work, separate from ordinary manuality. |
| `leveling_affinity_01` | float 0..1 | How much tools, infrastructure, technology, or magic can reduce the job's effective body demand. |
| `informal_role_01` | float 0..1 | Marks informal, illicit, vice, or social-role work that should not fall through to generic settlement labor semantics. |

Keep literal assignable job names neutral. Trait color belongs in `genome_jobs` descriptors, event prose, status effects, and archetype scores, not in the title string itself.

---

## `config/status_echelons.csv`

**Purpose:** Human-editable status ladder for upper-echelon mobility. `library.status_echelons.StatusEchelonCatalog` derives a person's current echelon from saved `social_standing_01`, household prosperity, class band, and job market type. Career mobility, patronage, elite household investment, incident exposure, and browser summaries use this layer; it does not replace formal government titles.

| Column | Type / role | Notes |
|--------|-------------|-------|
| `echelon_key` | string | Stable key such as `laboring`, `professional`, `notable`, `elite`, or `ruling`. |
| `display_name` | string | Browser-facing label. |
| `min_social_standing_01` | float 0..1 | Minimum standing before prosperity/class/job adjustments. |
| `min_household_prosperity` | float >=0 | Minimum household savings expected for the echelon. |
| `class_bands` | semicolon list | Class bands that slightly reinforce this echelon during derivation. |
| `job_market_types` | semicolon list | Job market types that slightly reinforce this echelon during derivation. |
| `prestige_access_multiplier` | float | Multiplier for access to scarce prestige promotions. |
| `patronage_power_01` | float 0..1 | Ability to sponsor clients into better roles. |
| `service_hiring_multiplier` | float | Expected demand for household service and retainers. |
| `scandal_fall_severity_01` | float 0..1 | How painful public failures are at this echelon. |
| `investment_share_01` | float 0..1 | Share of surplus that can become patronage, trade, charity, or settlement investment. |

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

**Era body-demand fit:** `library.work_body_fit` applies `job_archetypes.physical_demand_01` after ordinary trait/market scoring. The helper derives `body_power_01` from current mind/body `physical`, adds a small male raw-strength prior only for body-demand and force-authority scoring, and lowers effective demand through era tools (`prehistoric=0.0`, `bronze_age=0.05`, `iron_age=0.08`, `medieval=0.15`, `modern=0.65`) or `world_start.magic_physical_leveling_01`, whichever is higher. `job_assigned` payloads include `physical_demand_01`, `effective_physical_demand_01`, `body_power_01`, and `physical_demand_multiplier`.

**Sex-restricted job tokens:** A job token may end with `` [M]`` (defaults to male assignees only) or `` [F]`` (female only). Other `Person.gender` values only receive untagged jobs. Cross-gender assignment is allowed when all of: `gender_mind` is the cross pole (`masculine` for a female assignee taking an `[M]` job, `feminine` for a male assignee taking an `[F]` job), genome `mating drive` is sufficiently on the deficient side (low sex drive; threshold `CROSS_GENDER_MATING_DRIVE_THRESHOLD`), and `physical` passes a sex-specific band (`CROSS_GENDER_FEMALE_PHYS_MIN` / `CROSS_GENDER_MALE_PHYS_MAX` in `library.simulation_careers`). `job_assigned` events may include `job_sex_restriction` and `cross_gender_job_exception`.

---

## `config/ethnic_proto_placewords.csv`

**Purpose:** Proto-form semantic stems for named local geography features. `library.ethnic_proto_placewords` uses the resident ethnic mix when a settlement anchors to a nearby natural feature, maps local feature kinds (river, mountain, harbor, etc.) to this table's `feature_type`, picks a `core_concept`, then uses `normalized_form` as the seed stem. The seed is combined with `placenames.csv` rows from `Topography`, `Sacred`, and `Status` categories; `rewind_constructed_toponym_placeholder` now applies the initial IE modulo-4 sound law for Germanic vs Italic/Celtic-style toponyms while keeping the function name as the future tuning hook. Unused physical features remain unnamed.

| Column | Type / role | Notes |
|--------|-------------|-------|
| `ethnic` | string | Culture/ethnicity; matched against living residents' `Person.ethnic` with case/substring fallback. |
| `concept_order` | integer-ish string | Authoring order only; ignored by runtime selection. |
| `tag` | string | Toponym class label, for example hydronym/potamonym; currently annotation. |
| `feature_type` | string | Feature grain used for lookup, e.g. `river`, `mountain`, `natural harbor`, `ocean`, `fords`. |
| `core_concept` | string | Semantic bucket. Runtime first chooses a core concept from rows matching `ethnic + feature_type`, then chooses a row in that bucket. |
| `concept` / `concept_key` | strings | Human-readable / key-level concept annotation. |
| `normalized_form` | string | Proto/toponym stem used in generated landmark names; spaces are removed during placeholder composition. |
| `component_tokens` | string | Component glosses for compositional rows; annotation for now. |
| `confidence`, `source_intermediate_proto_from_reduction`, `lexicon_stage_used`, `lexicon_family_bucket`, `note` | strings | Source/provenance metadata; loaded to SQLite but not used in runtime composition yet. |

---

## `config/world_geography_continents.csv`

**Purpose:** Immutable continent partition for compartmentalized world simulation.

| Column | Type / role | Notes |
|--------|-------------|--------|
| `world` | string | World key (joins to `world_start.world`). |
| `continent_id` | string | Stable continent identifier. |
| `continent_name` | string | Display label. |
| `map_size` | string | World-map generation hint such as `huge`, `large`, `medium`, `small`, or `island`. Controls relative landmass footprint only; simulation capacity still comes from regions. |
| `map_placement` | string | Coarse placement hint (`northwest`, `northeast`, `southwest`, `southeast`, `central`, etc.). Used as a soft anchor for deterministic map layout, not an exact coordinate. |
| `map_shape` | string | Landmass style hint for procedural map generation (`ragged_maritime`, `shield_fjord`, `arid_rift`, etc.). |
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
| `map_features` | string | Semicolon-separated generation flags for region carving and landmarks, e.g. `coast`, `port`, `river`, `basin`, `mountains`, `forest`, `dry`, `wetland`, `delta`, `harbor`. |
| `map_placement` | string | Coarse placement preference within the generated continent, e.g. `north`, `south`, `west`, `east`, `coast`, `river`, `interior_high`, `interior_low`, `river_coast`. |
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

Sea routes are also used by `library.simulation_trade_networks` for port
centrality, commercial-outpost destinations, and knowledge diffusion reach.

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

Runtime save-schema note: `simulation_settlements` in `save.sqlite` includes
trade-network lineage fields used by the maritime expansion layer:
`founding_reason`, `mother_settlement_id`, `trade_network_id`, and
`autonomy_level`. Older save rows are migrated with defaults of
`founding_reason="organic"`, `mother_settlement_id=NULL`,
`trade_network_id=<settlement_id>`, and `autonomy_level="autonomous"`.

---

## Conventions

- **Encoding:** UTF-8 (`utf-8-sig` in `util_load_config.py` for CSV import).
- **CSV:** Standard comma-separated; fields with commas are quoted (e.g. `kin_f` in `ethnic.csv`).
- **Multi-valued cells:** Semicolon (`;`) separates allowed tokens in appearance columns and in `num_*` patterns.
