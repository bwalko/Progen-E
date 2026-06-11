# TODONE

## Genome And Context Scoring Workstream Completion

- Completed Workstream 2 for the current event-system scoring foundation.
- `library.event_scoring` now supports reusable contextual propensity maps:
  signed genome basis functions, role/pressure/opportunity `EventScoringContext`
  tags, composite-name weights, deterministic threshold/weight helpers, and
  political, religious/cultural, and private-life scoring entrypoints for future
  generators.
- Active incident generators now score candidates with per-year contextual maps
  instead of raw trait-only propensity calls:
  - cached household/care indexes supply parent and household/support facts;
  - government state supplies office holder, ruler, court/capital,
    succession-crisis, faction-tension, and war-region signals;
  - settlement facts supply resource pressure, stability/prosperity, crowding,
    debt/status pressure, witness density, market, storehouse, workshop,
    archive, household, and public-crisis opportunity tags.
- The richer context is wired through all five current annual incident families:
  murder, property crime, affair scandal, public virtue, and knowledge/culture.
- Added focused regression coverage:
  - `unit_test.test_event_scoring` proves future-family propensity entrypoints
    work with bounded contextual candidate pools;
  - `unit_test.test_simulation_incidents` proves live incident context maps use
    pressure, office/court, and family/care facts and raise contextual
    propensity over raw trait-only scoring where appropriate.
- Ran a bounded Workstream 2 tuning smoke on this laptop:
  `python utils/run_population_simulation.py --world-id event_tuning_w2_context
  --reset-world --years 40 --starting-couples 80 --seed 20260605
  --skip-report-files --profile-last-years 3 --passive-population-scale 0
  --detailed-active-soft-cap 0`.
- Sample result:
  - completed in 162.36 seconds;
  - final detailed alive count was 364;
  - last-three-year profile total was 13.744 seconds, about 4.5814
    seconds/year;
  - `summary.incidents` was 0.523 seconds total and `incidents.generate` was
    0.480 seconds total across the profiled years.
- Generated the current event-history report with
  `python utils/util_event_history_report.py --world event_tuning_w2_context
  --sample-limit 12 --ensure-schema`.
- Current W2 report evidence:
  - 7,055 factual events and 9,646 event-memory records;
  - 8,585,216 byte save;
  - tracked incident counts: `murder` 2, `property_crime` 17,
    `affair_scandal` 2, `public_virtue` 1, `knowledge_culture` 4;
  - reportable visibility states and public chronicle samples still exist for
    all tracked slices;
  - consequence ledgers remained reportable for faction memory, legal fallout,
    domain state/diffusion, obligations, reputation marks, and innovation state.
- Verified with `python -m py_compile library\event_scoring.py
  library\simulation_incidents.py unit_test\test_event_scoring.py
  unit_test\test_simulation_incidents.py`,
  `python -m unittest unit_test.test_event_scoring`, the focused live-context
  regression test, and the forced-generator slice for all five active incident
  families.
- No further Workstream 2 TODO remains for the current contextual scoring
  foundation. Future scoring work should be opened only for a concrete new event
  generator, measured tuning issue, or new shared context fact needed by
  implemented behavior.

## Tests And Tuning Workstream Completion

- Completed Workstream 8 for the current event-system test/tuning foundation.
- Current focused coverage exists for the five active event families:
  - `unit_test.test_simulation_incidents` has forced-generation/regression tests
    for `murder`, `property_crime`, `affair_scandal`, `public_virtue`, and
    `knowledge_culture`;
  - those tests assert payload shape, normalized readable rows, memory record
    type/visibility, public actor/victim fields, and consequence ledgers where
    the family currently owns consequences;
  - `unit_test.test_event_prose` covers state-specific payload-backed prose for
    active incident families;
  - `unit_test.test_event_history_report` covers report counts, save-size
    capture, visibility/metric summaries, consequence summaries, and public
    samples;
  - `unit_test.test_gradio_data_browser` covers the History Summary browser
    surface for report counts and lifecycle visibility.
- Ran the current W8 seeded multi-year sample on this laptop:
  `python utils/run_population_simulation.py --world-id event_tuning_w8_current
  --reset-world --years 80 --starting-couples 120 --seed 20260605
  --skip-report-files --profile-last-years 5 --passive-population-scale 0
  --detailed-active-soft-cap 0 --progress`.
- Sample result:
  - completed in 298.744 seconds;
  - final detailed alive count was 900;
  - last-five-year profile total was 41.267669 seconds, about 8.2535
    seconds/year;
  - event generation remained bounded in the profile:
    `summary.incidents` 0.859732 seconds total, `incidents.generate` 0.823093
    seconds total;
  - event-memory lifecycle remained bounded in the profile:
    `summary.event_memory_lifecycle` 0.453761 seconds total, with the final
    profiled year reviewing 802 lifecycle candidates, losing 3 records, and
    rediscovering 0.
- Generated the current event-history report with
  `python utils/util_event_history_report.py --world event_tuning_w8_current
  --sample-limit 16 --ensure-schema`.
- Current W8 report evidence:
  - 24,864 factual events and 35,255 event-memory records;
  - 29,618,176 byte save;
  - tracked incident counts: `murder` 15, `property_crime` 48,
    `affair_scandal` 3, `public_virtue` 8, `knowledge_culture` 57;
  - visibility lifecycle states are present in the report, including lost
    mortuary/household/violent-crime records and current public
    unknown/rumored/known rows;
  - public chronicle samples are reviewable and include data-backed
    knowledge/culture, property-crime, murder, and public-virtue prose.
- Verified the focused test slice with
  `python -m unittest
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_forced_murder_tick_records_event_kills_victim_and_persists_rumor
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_forced_property_crime_records_nonlethal_rumor
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_forced_affair_scandal_records_rumored_household_scandal
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_forced_public_virtue_records_public_known_good_deed
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_forced_knowledge_culture_records_public_known_breakthrough
  unit_test.test_event_prose.TestEventProse.test_active_incident_families_have_state_specific_payload_prose
  unit_test.test_event_history_report.TestEventHistoryReport.test_build_report_counts_visibility_metrics_and_samples
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_history_summary_exposes_report_counts_and_lifecycle_visibility`
  (8 tests, 223.548 seconds, OK).
- No further Workstream 8 TODO remains for the current foundation. Future tests
  or tuning samples should be opened only for concrete new event families,
  browser/report surfaces, ledgers/consequence changes, or measured tuning
  problems.

## Persistence Save Size Performance Workstream Completion

- Completed Workstream 7 for the current event-system storage/performance
  foundation.
- Current event persistence keeps common query fields normalized while leaving
  payload JSON for sparse details:
  - factual `simulation_events` rows carry typed person/place/origin fields;
  - high-volume movement details live in `simulation_event_moves`;
  - in-world memory state lives in `simulation_event_records` plus readable
    views.
- Rendered prose remains derived, not persisted as large text columns:
  `simulation_event_records` stores compact state, distortion JSON, and
  `prose_variant_key`, while `library.event_prose` renders admin/public text
  from event/readable rows on demand.
- Current event generation and lifecycle work are bounded:
  - `library.simulation_incidents` uses per-settlement candidate samples and
    annual caps for murder, property crime, affair scandal, public virtue, and
    knowledge/culture slices;
  - passive/cohort populations only become detailed through explicit
    promotion/focus paths;
  - `library.event_memory_lifecycle` reviews deterministic shards with a
    candidate limit and records lifecycle gauges for reviewed/lost/rediscovered
    rows.
- Event-history reporting now exposes `save_size_bytes` alongside counts,
  visibility, metric, consequence, and public-chronicle samples. The
  lifecycle-tuned fixed-seed sample recorded in this log produced 17,363
  events/records in a 17,252,352 byte save.
- Added focused W7 regression coverage:
  - `unit_test.test_event_memory_lifecycle` proves the lifecycle candidate
    limit bounds the loss scan even when every reviewed row would transition;
  - `unit_test.test_save_checkpoint` proves the event-memory save schema stores
    template keys instead of rendered prose-like text columns;
  - `unit_test.test_event_history_report` continues to assert save-size capture.
- Verified with
  `python -m unittest unit_test.test_event_memory_lifecycle.TestEventMemoryLifecycle.test_lifecycle_candidate_limit_bounds_loss_scan
  unit_test.test_save_checkpoint.TestSaveCheckpoint.test_event_memory_schema_stores_template_keys_not_rendered_prose`
  and
  `python -m unittest unit_test.test_event_history_report.TestEventHistoryReport.test_build_report_counts_visibility_metrics_and_samples`.
- No further Workstream 7 TODO remains for the current foundation. Reopen only
  for a measured bottleneck, save-growth regression, or concrete new
  high-volume event feature that needs storage/performance design.

## Admin Browser Query Workstream Completion

- Completed Workstream 6 for the current admin/browser/query foundation.
- Extended `utils.gradio_data_browser` History tab with explicit-load query
  surfaces:
  - `Load Lens` supports a person timeline that combines factual admin-truth
    rows with in-world event records involving that person;
  - the same lens loader supports settlement, region, and polity chronicle
    views scoped to visible public memory;
  - `Load Rediscovery Details` exposes rediscovered-record source
    person/institution, confidence, preserving place, and distortion details
    without requiring raw table inspection.
- Kept the surfaces behind button/submit controls with event-type filters,
  search, limits, and offsets, preserving the Gradio tab-autoload caution.
- Extended `library.event_prose` loaders with optional `event_ids` filters so
  browser lenses reuse the same deterministic prose rendering path instead of
  duplicating prose logic in UI code.
- Added regression coverage in `unit_test.test_gradio_data_browser` for:
  - person timeline rows containing both `admin_truth` and in-world records;
  - settlement, region, and polity visible-memory chronicle lenses;
  - rediscovery source/confidence/distortion detail rows.
- Verified with `python -m py_compile library\event_prose.py
  utils\gradio_data_browser.py unit_test\test_gradio_data_browser.py` and
  `python -m unittest unit_test.test_gradio_data_browser
  unit_test.test_event_prose unit_test.test_event_history_report`.
- No further Workstream 6 TODO remains for the current browser/query
  foundation. Future browser work should be opened only for a concrete
  inspection need or measured usability issue.

## Poetic Prose Workstream Completion

- Completed Workstream 5 for the current authored prose foundation.
- Expanded `library.event_prose` so deterministic prose variants are selected
  from event id, record id, visibility state, and `prose_variant_key`, while
  still deriving text only from structured event payloads and readable
  event-record rows.
- Added richer data-grounded prose for the five active incident families:
  murder, property crime, affair scandal, public virtue, and knowledge/culture.
  Current templates now use available names, places, motives, witnesses,
  betrayed partners, loss/relief/novelty values, patrons, knowledge domains,
  innovation analogues, source people/institutions, and preserving places.
- Added state-specific authored prose for admin truth, public known,
  public-unknown, rumor, misattribution, lost, sealed, and rediscovered records.
  Lost/sealed/rediscovered text now has distinct archive/source language instead
  of a single generic fallback.
- Tightened ordinary public-event prose for death, settlement moves, office
  succession/selection, polity/court changes, warfare, and rediscovery rows
  where the payload exposes titles, causes, routes, outcomes, or source facts.
- Reviewed and regenerated public chronicle samples:
  - existing samples under `temp/event_history_report/event_tuning_sample*`;
  - focused W5 incident sample under
    `temp/event_history_report/event_tuning_sample_w5_incidents`;
  - the refreshed sample shows varied data-backed wording for property crime,
    knowledge/culture, public virtue, scandal, and murder rows.
- Added focused regression coverage in `unit_test.test_event_prose` proving all
  active incident families render payload-backed prose across public unknown,
  rumored, misattributed, public known, lost, sealed, and rediscovered states.
  Updated event-history report assertions so prose samples are checked by
  factual payload details rather than one fixed old phrase.
- Verified with `python -m py_compile library\event_prose.py` and
  `python -m unittest unit_test.test_event_prose unit_test.test_event_history_report`.
- No further Workstream 5 TODO remains for the current authored-template
  foundation. The optional local-model paraphrase spike remains non-required and
  should be opened only as a contained display-only task.

## Consequence Feedback Workstream Completion

- Completed Workstream 4 for the current consequence/feedback foundation.
- Advanced the shared event-history reporting surface so durable consequence
  ledgers are visible through report files and the Gradio History Summary:
  - `library.event_history_report` now reports consequence counts and numeric
    summaries for domain states, domain diffusion, obligations, reputation
    marks, legal fallout/adjudications, faction memory, institutions,
    innovation discoveries, innovation knowledge, and innovation era state;
  - report writing emits `event_consequence_counts.tsv` and
    `event_consequence_metrics.tsv`;
  - `utils.gradio_data_browser` surfaces those rows through its existing
    explicit-load History Summary table.
- Ran the fixed-seed `event_tuning_sample` scenario with 80 years, 80 starting
  couples, seed `20260603`, passive population disabled, and detailed soft cap
  disabled. The sample completed through simulation year 1079 and the report
  artifacts live under `temp/event_history_report/event_tuning_sample_w4`.
- Current sample evidence:
  - tracked incidents: murder 12, property_crime 47, affair_scandal 3,
    public_virtue 5, knowledge_culture 49;
  - durable ledgers: domain_states 44, domain_diffusion 122, faction_memory 67,
    institutions 4, obligations 9, reputation_marks 5,
    innovation_discoveries 45, innovation_knowledge 515, innovation_era_state
    14;
  - legal_fallout and legal_adjudications were 0 in this seed because the
    generated scandal variants were `affair_exposed` / `double_affair_exposed`;
    the legal fallout/adjudication path remains covered by focused tests and is
    now visible in the shared report when rows exist.
- Audited current active vertical slices for first-order simulation feedback:
  murder applies death/faction memory, property crime applies household/local
  prosperity plus grievance memory, affair scandal applies relationship/local
  fallout plus legal fallout for legal variants, public virtue applies
  prosperity/reputation/obligation/trust, knowledge culture applies local and
  regional knowledge/economy/institution/patronage effects, and the innovation
  driver persists discovery/knowledge/era-state feedback.
- No further Workstream 4 TODO remains for the current foundation. Future
  consequence work should be opened only for a concrete new generator, ledger,
  browser/report surface, or measured tuning need.

## Event Visibility, Distortion, And Rediscovery Workstream Completion

- Completed Workstream 3's current event-memory foundation:
  - factual/admin truth remains append-only in `simulation_events`;
  - in-world/admin memory remains in `simulation_event_records` and readable
    views;
  - public records can represent `public_unknown`, `rumored`, `misattributed`,
    `public_known`, `lost`, `sealed`, and `rediscovered` states;
  - rediscovery can still log a linked factual `event_rediscovered` row.
- Added default public uncertainty/rumor stage rows for active ordinary public
  event families:
  - ordinary deaths get a `public_cause_unknown` mortuary uncertainty row;
  - settlement moves/plans/drops get unclear-route/cause notices and move rumors
    when a reason is available;
  - office selection/succession gets unclear-claim notices and succession rumors
    when the selection path is known;
  - polity/court changes and warfare events get default uncertainty rows and
    rumors where the payload carries a reason, kind, outcome, or name.
- Added one-time existing-save backfill for these public stage rows using
  `simulation_event_public_stage_records_backfilled`, so older saves with only
  default records gain the new public uncertainty layer without rewriting factual
  events.
- Extended `library.event_prose` so public unknown/rumor/known prose for deaths,
  settlement moves, office succession, polity changes, and warfare uses
  event-specific language instead of a generic fallback.
- Added focused regression coverage:
  - `unit_test.test_save_checkpoint` covers default stage-row creation,
    existing-save backfill, default-record transitions, rediscovery, and v7
    default-memory backfill;
  - `unit_test.test_event_prose` covers rendered uncertainty and rumor prose for
    the newly staged active event families.
- Workstream 3 is closed for current functionality. Future visibility work
  should be opened only for a concrete new generator, prose/browser/report
  consumer, or measured tuning need.

## Event Scoring Future-Family Foundation

This records completed Workstream 2 foundation work, not full Workstream 2
completion.

- Started Workstream 2 beyond the initial five vertical slices:
  - added reusable `political_crime_propensity`,
    `religious_cultural_conflict_propensity`, and
    `private_life_seed_propensity` specs in `library.event_scoring`;
  - based the formulas on real genome traits plus checked-in composite names
    such as `Legitimacy Seizer`, `Fanatic`, `Cult Leader`, `Hidden Manipulator`,
    `Good Neighbor`, and related Workstream 1 catalog signals;
  - expanded `infer_role_tags` so callers can pass cached care indexes or
    precomputed parent/office/ruler id sets and derive `parent`, `title_holder`,
    `heir`, `household_head`, and `migrant` tags without per-candidate global
    scans;
  - left the dormant Workstream 1 families as scoring-ready but not yet live
    annual generators.
- Added focused regression coverage in `unit_test/test_event_scoring.py` for
  expanded role inference and the new political, religious/cultural, and
  private-life propensity families.

## Event Ontology And Catalog Workstream Completion

- Completed Workstream 1's authored catalog coverage:
  - expanded `config/event_catalog.csv` so every `config/event_ontology.csv`
    event key has a matching catalog `incident_kind`;
  - added dormant catalog rows for future violent-crime, political-crime,
    religious/cultural-conflict, household-scandal, property/survival-crime, and
    private-life generators while preserving the active five generator variant
    pools;
  - kept `rescue`, `mercy`, and `arbitration` as zero-weight ontology aliases
    because the current public-virtue selector still uses concrete rows such as
    `heroic_rescue`, `public_mercy`, and `public_arbitration`;
  - updated `dev_rules/config_schemas.md` to document the active-vs-dormant
    catalog-row distinction.
- Added regression coverage in `unit_test/test_event_catalog.py` proving the
  SQLite-loaded catalog covers every Workstream 1 ontology key and family.
- Updated `TODO.md` so Workstream 1 is marked complete for the foundation; future
  event-system work is now generator implementation, consequence/prose
  deepening, and tuning rather than basic ontology/catalog completion.

## Innovation Catalog Curation And Balance Passes

- Curated `config/innovations.csv` after parser generation:
  - retiered ranks from parser source-order counters to broad per-category era
    progression so large categories such as `craft` are not 200+ step linear
    ladders;
  - tuned spreadability, complexity, and starter prevalence for gameplay
    balance around the default `history_equivalent_start_year=1000`;
  - added 137 obvious `prerequisite_ids` links for tool, writing, metallurgy,
    transport, printing, gunpowder, steam, electrical, computing, and other
    dependency chains;
  - replaced many rough Earth-specific or person-title rows with local analogue
    names such as `balanced thrusting spears`, `atmospheric mine engines`,
    `periodic news-sheets`, and `single-chip microprocessors`;
  - marked 25 highly branded, product-specific, or ambiguous rows `unreviewed`
    so runtime ignores them until they receive better local analogues.
- Tuned `library.simulation_innovation` portable-innovation scoring so early
  starter knowledge helps port networks without immediately saturating the score.
- Added regression coverage in `unit_test/test_innovation_timeline.py` for:
  checked-in catalog curation/prerequisite/rank expectations, unreviewed-row
  exclusion, and portable innovation score headroom.
- Fixed-seed temporary-world smoke comparison for 1000-1002:
  - startup seed rows: 1164 -> 708;
  - distinct known innovations: 194 -> 118;
  - route diffusion rows: 164 -> 44;
  - early military known innovations remain present: 4 -> 3;
  - portable port-network score average now has headroom: 1.0 -> 0.62629;
  - prosperity bonus average stayed capped at 0.12.
- Completed row-level second/third-pass curation:
  - all 505 runtime catalog rows are now `reviewed`;
  - 31 questionable, branded, duplicate, or too-specific rows remain
    `unreviewed` and ignored by runtime loading;
  - prerequisite links increased to 300, with no missing, inactive, or
    future-pointing prerequisites;
  - active analogue names no longer include parser-action artifacts such as
    "invented", "developed", "commercially", "launched", or "approved";
  - source-title/person artifacts were localized into rows such as
    `electrolysis`, `subsonic ramjets`, `transformer power grids`,
    `vacuum tube diodes`, `single-wheel handcarts`, and
    `closed-loop insulin pumps`;
  - corrected backward dependency chains including liquid-fuel rockets before
    long-range ballistic rockets and crystal oscillators before quartz clocks.
- Fixed-seed 1000-1002 smoke after the completed curation pass used three
  deterministic human port colonies (`aeria_port`, `boreas_port`,
  `cyrene_port`) because the tiny mixed-species zero-point seed can fail founder
  generation in `boreas_port`:
  - 28 people in RAM across 3 settlements and 3 regions after the three-year
    run;
  - knowledge rows: 752;
  - startup seed rows: 708;
  - distinct known innovations: 118;
  - discovery rows: 0 in the short 1000-1002 smoke;
  - route diffusion rows: 44 and same-polity diffusion rows: 0 because no
    mature polities exist in the smoke;
  - early military known innovations: 3, max military rank: 1;
  - portable port-network score average: 0.62951;
  - prosperity bonus average stayed capped at 0.12.
- Lowered the detailed-person discovery gate in `library.simulation_innovation`
  from an unreachable 0.48 to 0.20 after a 60-year diagnostic found late-run
  actor propensities topping out below the old gate; added a regression that the
  innovation gate stays no stricter than ordinary knowledge/culture incidents.
- Verified the current checked-in catalog after the completed curation pass:
  - 536 total rows;
  - 505 runtime rows, all `reviewed`;
  - 31 `unreviewed` rows excluded from runtime loading;
  - 300 rows with prerequisites;
  - no active parser-action analogue artifacts, no remaining auto-generated or
    row-level-review notes, no missing/inactive/future prerequisites, and max
    active rank 10.
- Ran a current-state fixed-seed 60-year population-backed medieval sweep
  (`history_equivalent_start_year=1000`, seed 424242, three deterministic human
  port colonies, 8 couples per colony, passive scale 0.3, detailed soft cap
  300):
  - 158 alive people, 12 settlements, 3 regions;
  - knowledge rows: 770; distinct known innovations: 127;
  - discovery records: 9, producing 18 discovery-sourced knowledge rows;
  - route diffusion rows: 44; startup seed rows: 708;
  - first discoveries included high-carbon steel, movable type, pivot shears,
    toe stirrups, ritual tattooing, canal locks, limb surgery, spinning wheels,
    and glass blowing;
  - early military frontier stayed conservative at 3 known military
    innovations, max military rank 1;
  - portable port-network score average/max: 0.64584 / 0.66626;
  - prosperity bonus average/max: 0.03467 / 0.12.
- Ran a current-state fixed-seed 30-year classical/city-state sweep
  (`history_equivalent_start_year=0`, same seed and colony setup):
  - 166 alive people, 9 settlements, 4 regions;
  - 8 active city-state polities;
  - knowledge rows: 876; distinct known innovations: 119;
  - discovery records: 3, producing 8 discovery-sourced knowledge rows;
  - polity integration rows: 154; route diffusion rows: 18; startup seed rows:
    696;
  - early military frontier stayed conservative at 3 known military
    innovations, max military rank 1;
  - portable port-network score average/max: 0.4665 / 0.63017;
  - prosperity bonus average/max: 0.04 / 0.12.
- Added focused same-polity diffusion coverage for a multi-region polity in
  `unit_test.test_innovation_timeline`; the quick population sweeps naturally
  form settlement-grain city-states, so multi-region same-polity diffusion is
  covered as a direct regression rather than hidden behind a long realm-growth
  setup.

## Innovation Timeline CSV And Driver

- Added a two-layer innovation timeline pipeline:
  - `Timeline of historic inventions.wiki` remains traceable source markup.
  - `utils/util_parse_inventions_wiki.py` parses wiki bullets, headings, wiki
    links, and date expressions into `config/innovation_source_rows.csv`.
  - The same utility drafts the editable gameplay catalog in
    `config/innovations.csv` with local analogue names, categories, domains,
    era ids, ranks, spreadability, complexity, starter prevalence, and curation
    status.
- Added innovation config surfaces:
  - `config/innovation_eras.csv`
  - `config/innovation_category_rules.csv`
- Added runtime support:
  - `library.innovation_catalog` loads active/reviewed/seed catalog rows and
    ignores unreviewed/inactive rows.
  - `library.simulation_innovation` seeds startup knowledge from the world's
    historical equivalent start year, gates discoveries by rank/year/prereq/era,
    samples high-propensity adults through `knowledge_culture`, diffuses adopted
    knowledge through polities and routes, updates effective innovation era
    state, and exposes small economy/trade hooks.
- Bumped `save.sqlite` to schema v14:
  - `simulation_innovation_discoveries`
  - `simulation_innovation_knowledge`
  - `simulation_innovation_era_state`
  - readable views for all three
- Specific innovation discoveries remain factual `knowledge_culture` events, so
  existing event/domain-state/history machinery keeps working.
- Added focused regression coverage in `unit_test/test_innovation_timeline.py`.

## Maritime Mercantile Expansion V1

- Added a generic maritime mercantile expansion archetype inspired by the
  Phoenician-style pattern of coastal hubs, sea travel, outposts, portable
  knowledge, and successor centers.
  - This is intentionally **not** a literal Phoenician/Punic culture feature:
    no culture names, religions, historical events, or naming rules were added.
  - This is intentionally **not** a commodity-economy pass: v1 uses geography,
    sea routes, jobs, settlement market/prosperity, and persisted knowledge
    domains as the trade desirability signals.
- Added `library.simulation_trade_networks`:
  - `PortNetworkScore`
  - `score_port_network`
  - `simulation_trade_networks_annual_tick`
  - annual commercial-outpost founding with deterministic world/year RNG,
    mother-settlement thresholds, 25-year cooldowns, and a cap of two outposts
    per world-year
  - mature dependent outpost autonomy and weakened-root successor recentering
- Extended settlement state/save data with:
  - `founding_reason`
  - `mother_settlement_id`
  - `trade_network_id`
  - `autonomy_level`
  - additive save migration/backfill defaults for older rows
- Added household-grouped settlement moves reusable by outpost founding and
  existing grouped move patterns.
- Expanded `knowledge_culture` with portable mercantile/maritime variants:
  `shipbuilding_advance`, `navigation_discovery`, `writing_system`,
  `accounting_method`, `trade_law_precedent`, `standard_container`, and
  `luxury_dye_recipe`.
  - Portable knowledge domains can diffuse through up to three sea-route
    destinations with route-friction-reduced domain-state deltas.
- Added regression coverage for port scoring, disabled sea-route eras,
  outpost founding/cooldowns/caps, invalid or duplicate destinations,
  household founder grouping, settlement-field save/load roundtrip, autonomy,
  successor recentering, portable knowledge selection, and sea-route domain
  diffusion.

## Event System Consequence Deepening

- Bumped `save.sqlite` to schema v13 for deeper durable consequence state.
- Added faction-memory persistence:
  - `simulation_faction_memory` and `simulation_faction_memory_readable`
    preserve event-backed grievances, feud memories, scandal memories, and
    public-trust memories with faction keys, polarity, strength, place, and
    decay/resolution years.
  - Murder, property crime, affair scandal, and public virtue payloads now emit
    first-version `consequences.faction_memory` rows.
- Added legal adjudication / inheritance-resolution persistence:
  - `simulation_legal_adjudications` and
    `simulation_legal_adjudications_readable` record deterministic resolutions
    for due active `simulation_legal_fallout` rows.
  - `SimulationContext.record_year_summary` now runs the bounded annual
    consequence tick after save persistence and before event-memory aging.
- Added inter-region domain diffusion:
  - `simulation_domain_diffusion` and
    `simulation_domain_diffusion_readable` audit slow annual route-connected
    spread from accumulated `simulation_domain_states`.
  - Diffusion updates target regional domain scores without materializing
    detailed people or flooding event rows.
- Added knowledge-born institutions:
  - `simulation_institutions` and `simulation_institutions_readable` persist
    schools, guilds, doctrine, and craft institutions strengthened by
    `knowledge_culture` events.
  - Knowledge events now map scholarship/medicine/calendar/writing to schools,
    legal/calendar/trade-law knowledge to doctrine, portable trade/craft/maritime
    knowledge to guilds, and tool/craft/art/shipbuilding knowledge to craft
    institutions.
- Added focused regression coverage proving:
  - event payloads persist readable faction-memory and institution rows;
  - due inheritance disputes resolve into adjudication rows;
  - accumulated shipbuilding knowledge diffuses across configured region routes;
  - live murder and knowledge/culture incident generators emit the new
    consequence payloads.

## Better SVG Map Data Plan

### Completed Geometry Foundation Work

- Added a generated world-map geometry layer:
  - deterministic geometry versioning
  - route-aware continent and region layout
  - dependency-light bounded Voronoi-style region polygons
  - generated region features, route edges, and river paths

- Refactored settlement-local geography to consume pre-existing region geography:
  - settlement pins now anchor to generated region features
  - local geography JSON includes `source_geometry_version`
  - local geography JSON includes normalized `region_cell_polygon`
  - existing settlement/browser JSON shape remains compatible

- Added focused regression coverage:
  - byte-stable deterministic world geometry
  - valid cells and feature anchors for every configured region
  - renderable route edge points
  - settlement site slots mapped to generated geography features
  - existing Gradio place browser rendering remains covered

### Completed Polygonal Terrain Pass

- Replaced the original TODO plan with implemented micro-cell terrain generation in `library.world_map_geometry`:
  - deterministic continent-clipped micro-region cells
  - authored-region ownership layered over those micro-cells, preserving existing region IDs for saves, settlements, births, and routes
  - elevation and moisture values per micro-cell
  - terrain-family assignment from generated physics plus authored region cues
  - micro-cell adjacency and route paths through shared cell borders
  - river paths that carry segment ownership, reach coastal boundaries, and avoid stacking on identical channels
  - river channel, bank, mouth, corridor, floodplain, and carved-land polygons for SVG rendering
  - aggregated authored-region footprints derived from owned micro-cells

- Updated map rendering/debug coverage:
  - visible micro-cell terrain layer with terrain texture/shade semantics
  - river-water, bank, corridor, mouth, and cut-mask layers
  - settlement, feature, polity, label, and route overlays preserved above the terrain
  - SVG data attributes expose micro-cell IDs for inspection

- Added focused regression coverage for the polygonal terrain pass:
  - every configured region owns multiple micro-cells
  - micro-cell elevation, moisture, river distance, terrain family, coastal flags, and floodplain/channel flags stay in valid ranges
  - river segments preserve local region ownership
  - coastal rivers terminate on coastline boundaries
  - channels are not duplicated across rivers
  - settlement and arbitrary point projection stay inside the declared authored-region footprint
  - debug SVG output includes the noisy map, river, and overlay layers

### Completed World Map Polish Pass

- Switched the visible SVG region-boundary layer from anonymous micro-edge snippets to dissolved authored-region footprints:
  - one data-rich `region-boundary` path per configured region
  - boundary paths reuse the region cells aggregated from same-region micro-cells
  - the dissolved boundary layer renders under coast and lake styling so coastline/water presentation stays authoritative
- Preserved micro-edge segments for terrain blending, hillshade, and coast rendering.
- Confirmed explicit ocean and lake water-cell layers remain part of the SVG/debug test surface.

### Completed Map Follow-Up Closures

- Closed the explicit lake/ocean styling TODO:
  - `library.world_map_svg` renders lake and ocean water cells with distinct
    `water-cell lake` / `water-cell ocean` semantics, ocean shelf styling, and
    lake/coast layering.
  - focused SVG regression coverage asserts the lake/ocean classes and nearby
    terrain/river styling remain present.
- Closed the graph-backend decision TODO:
  - `library.world_map_geometry` deliberately keeps the deterministic
    lightweight micro-cell graph because settlement projection, route paths,
    water carving, road routing, SVG fills, and tests already share it.
  - `build_world_map_debug_data()` exposes the decision as
    `keep_lightweight_micro_cell_graph` so future revisits have a stable
    inspection point.
- Closed the richer debug-data export TODO:
  - `build_world_map_debug_data()` now reports graph backend metadata, terrain
    counts, water counts, river lengths, river-mouth/coastal distances, QA
    checks, moisture, and elevation summaries.
  - `utils/util_export_world_map_svg.py --debug-output ...` writes that debug
    JSON next to generated SVGs for multi-seed tuning.
- Closed the map-seed comparison fixture TODO:
  - focused geometry tests compare multiple seeds through stable debug metrics
    instead of brittle full-SVG goldens.
  - the remaining open work is visual tuning of terrain, rivers, elevation, and
    moisture against more generated SVGs.
- Tuned generated river density from measured debug output:
  - `_build_micro_rivers` now sizes per-continent river counts from available
    upland source terrain instead of assigning every large continent the same
    maximum river count.
  - the default debug export moved from 18 rivers / 18 channels to 13 rivers /
    13 channels, average moisture from 0.7591 to 0.7233, drylands from 83 to
    115 cells, and plains from 625 to 710 cells while preserving zero reported
    river-mouth and coastal-feature QA distance.
  - five seed probes now vary from 12 to 15 rivers instead of saturating all
    sampled worlds at 18.
  - the map-seed debug fixture now asserts sampled seeds do not collapse to the
    same river count.
- Preserved anchored settlement/feature overlay projection after the terrain
  tuning changed generated geometry enough to expose the drift:
  - `load_world_map_overlays` reuses the settlement's projected point for a
    named feature when that settlement slot declares `anchor_feature_id`.
  - anchored feature overlays take precedence over duplicate non-anchored
    feature rows from the same local geography JSON.
  - verified with `unit_test.test_world_map_geometry.TestWorldMapGeometry.test_local_anchor_settlement_and_feature_share_projection`.
- Tuned river-corridor moisture spread and dryland legibility:
  - `_apply_river_influence_to_micro_cells` now keeps floodplain styling closer
    to the routed river path, leaving broad hydrology to `_moisten_micro_cells`.
  - drylands now classify at a modestly broader low-moisture threshold so arid
    patches remain visible after river/coast moisture spread.
  - `build_world_map_debug_data()` now includes `floodplain_cells` and
    `channel_cells` counts for future corridor-width tuning.
  - the default no-overlay debug export moved from average moisture 0.7233 to
    0.6963, drylands 115 to 183 cells, plains 710 to 743 cells, forest 505 to
    430 cells, and riverland 432 to 406 cells while preserving 13 rivers and
    zero coastal-feature / river-mouth QA misses.
  - five seed probes now show 12-15 rivers, 765-914 floodplain cells, 52-231
    dryland cells, and zero sampled coastal-feature or river-mouth QA misses.
- Made authored coastal/river constraints survive footprint repair:
  - disconnected-region repair now prefers the component that satisfies a
    region's coastal/river requirement instead of blindly keeping the largest
    component.
  - this keeps generated port/coastal regions attached to real coastline after
    later small-footprint and connectivity cleanup.
  - seed `terrain-river-c` now gives `aeria_port` 33 coastal micro-cells, with
    22 carrying exterior boundary edges, and no longer reports a missing
    coastal feature edge.
- Extended map debug regression coverage:
  - the map-seed fixture now samples `terrain-river-c` in addition to
    `campaign-a` and `campaign-b`.
  - tests assert meaningful dryland counts, channel/floodplain debug counts,
    bounded floodplain coverage, and zero coastal-feature / river-mouth QA
    misses across sampled seeds.
- Tuned river-mouth shape:
  - `_river_mouth_polygons_for_path` now generates eight-point estuary fans
    instead of symmetric six-point wedges.
  - river mouths include a longer throat, wider shoulders, and a stable
    per-river asymmetry derived from the river ID so adjacent mouths do not look
    stamped from the same shape.
  - `build_world_map_debug_data()` now reports `river_mouth_shapes` with mouth
    count, minimum water/bank point counts, and water-area summaries.
  - the default no-overlay debug export now reports 13 mouth shapes for 13
    river channels, minimum 8 water points, minimum 8 bank points, average mouth
    water area 0.00004116, maximum mouth water area 0.00005416, and zero
    coastal-feature / river-mouth QA misses.
  - six seed probes preserved clean coastal-feature and river-mouth QA while
    reporting 8-point mouth water/bank polygons for every river channel.
- Tuned elevation-gradient rendering:
  - `library.world_map_svg` now renders a subtle `terrain-contour-layer` from
    noisy shared micro-cell edges where non-coastal neighbors cross high
    elevation bands.
  - contour rendering uses the `0.64`, `0.70`, and `0.78` elevation thresholds,
    leaving the lower `0.58` band as debug-only context to avoid a dense hatch
    effect.
  - `build_world_map_debug_data()` now reports elevation band counts for
    non-coastal cells at `0.52`, `0.58`, `0.64`, `0.70`, and `0.78`.
  - the default no-overlay SVG now has 736 terrain-contour paths; debug reports
    358 non-coastal cells at elevation >= 0.64, 221 at >= 0.70, and 70 at >=
    0.78, with zero coastal-feature / river-mouth QA misses.
  - sampled seeds rendered 762-950 terrain-contour paths after dropping the
    lower contour threshold, keeping the contour layer under half of the
    micro-cell count.
- Tuned terrain-family texture rendering:
  - `library.world_map_svg` now uses terrain-specific mottle profiles instead
    of applying the same generic ellipse treatment to every non-coastal family.
  - forests render denser canopy mottles, drylands render smaller scrub marks,
    highlands render sparse ridge-like marks, riverlands/floodplains render
    alluvial marks, and plains render lighter meadow texture.
  - mottle colors are now terrain-aware, preserving the underlying terrain fill
    while pushing forests darker/greener, drylands sandier, highlands lighter or
    stonier, and riverlands more alluvial.
  - rendered texture ellipses carry `data-texture-kind` and
    `data-terrain-family` attributes for SVG inspection.
  - the sampled seeds moved from roughly 1,352-1,364 generic mottle ellipses to
    1,119-1,158 terrain-specific marks, reducing overall noise while making
    terrain families more visually distinct.
  - the default no-overlay SVG now renders 1,158 terrain-specific mottle marks:
    350 canopy, 129 ridge, 107 scrub, 331 alluvial, and 241 meadow.
- Closed the broad polygonal map tuning TODO:
  - completed measured passes now cover river density, moisture/floodplain
    spread, authored coastal/river footprint repair, river-mouth shape,
    elevation-contour rendering, and terrain-family texture treatment.
  - future map work should be filed as a fresh concrete TODO only when a new
    generated SVG, map debug JSON, browser behavior, or map-seed fixture exposes
    a specific visual/debug gap.
- Verified the current polygonal map pass with:
  - `python -m py_compile library\world_map_geometry.py library\world_map_svg.py`
  - `python -m unittest unit_test.test_world_map_geometry.TestWorldMapGeometry.test_local_anchor_settlement_and_feature_share_projection unit_test.test_world_map_geometry.TestWorldMapGeometry.test_map_seed_debug_fixtures_capture_stable_comparison_metrics`
  - `python -m unittest unit_test.test_world_map_geometry`
- Extended the settlement route overlay model with sea lanes:
  - `library.world_map_roads` now builds `SeaRouteMapEdge` overlays from the same saved settlements, latest-year moves, trade/outpost demand, and conservative implied demand used by roads, but only across configured sea-route region paths.
  - sea-lane water spans follow direct smoothed curves from configured sea-route edges instead of routing through land micro-polygons; short endpoint connectors still attach settlements to their coast-side route endpoints.
  - `library.world_map_svg` renders a separate `sea-route-layer` with `data-sea-route-*` attributes, while `utils.gradio_data_browser` labels the shared transport overlay toggle as `Routes`.
  - verified with `python -m py_compile library\world_map_roads.py library\world_map_svg.py utils\gradio_data_browser.py unit_test\test_world_map_roads.py`, `python -m unittest unit_test.test_world_map_roads`, and `python -m unittest unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_world_map_html_renders_roads_and_checkbox_hides_them`.

## Scale Population Simulation Toward Millions

### Completed Performance Work

- Added durable late-year profiling records:
  - `utils/run_population_simulation.py --profile-last-years N` sets the profiling window for one run
  - console output still prints the phase breakdown
  - `unit_test/population_sim_profile.tsv` records one row per profiled phase with run metadata
  - a small `profile_smoke` run verified the recording path

- Built shared annual indexes so expensive modules do not repeatedly scan all alive people:
  - `children_by_parent`
  - `household_members_by_adult`
  - `dependent_minor_count_by_adult`
  - `grandparent_relief_by_household`
  - `alive_by_settlement`
  - `alive_by_region`
  - `largest_active_settlement_id`

- Used deterministic capped samples for settlement/regional behavior and candidate decisions:
  - default cap: 1,000 people per decision pool
  - groups at or below the cap use the full population
  - exact counts, persistence, and conservation-style accounting remain exact
  - decision samples are seeded by year, scope, world, and run salt so results are reproducible
  - implemented first targets: government candidate pools, treasury leader-spend choices, warfare/usurpation candidates, settlement social contact/couple candidate pools
  - future behavior pools should keep using this pattern while exact accounting paths stay unsampled

- Tuned resource-pressure migration for the default 5% population scale:
  - raised `MIGRATION_MAX_OUTFLOW_SHARE` from `0.045` to `0.08` so highly over-cap regions relieve pressure faster
  - capped planned migration representatives by the surplus above `MIGRATION_PRESSURE_THRESHOLD × effective_cap` so mild pressure does not over-drain below the target
  - added regression coverage for both high-pressure outflow and near-threshold surplus behavior

- Refactored household/care duty hot paths:
  - `childcare_duty_factor`
  - `dependent_minors_in_implicit_household`
  - `_household_ids_for_job_move`
  - callers in careers, household care, economy, and government
  - indexed path is now the default for annual callers; legacy scan is retained only as a fallback/test comparison path

- Cached per-year resource pressure facts:
  - effective regional cap
  - alive count by region
  - food pressure by settlement
  - computed pressure by person where needed
  - annual callers now share `YearResourceFacts` across careers, economy, migration, social pressure checks, birth pressure checks, and zero-point colony births

- Cached species/life-stage lookup data for `refresh_current_people_life_stages`:
  - context now builds one `(species, ethnic) -> species row` lookup from cached species config
  - annual life-stage refresh reuses that lookup for every living person instead of querying SQLite per person

- Cached restriction-filtered career job options for annual assignment:
  - `choose_career_assignment` now reuses era/job-entry tuples after applying
    a person's male/female restriction allowance profile instead of rebuilding
    filtered tuples for every job seeker
  - a focused benchmark of 20,000 medieval allowance lookups kept the same
    1,800,000 option rows and reduced repeated filtering from 1.222506s to
    0.002011s after cache warmup
  - verified with `unit_test.test_simulation_careers`,
    `unit_test.test_population_growth_determinism`, and
    `unit_test.test_simulation_government`

- Re-ran the mixed-mode scale smoke after the career-cache change:
  - `utils/run_mixed_mode_scale_smoke.py --years 5 --targets 100000,1000000,10000000`
  - the 10,000,000 target ended with 9,921,295 aggregate alive people, 24,752
    cohort rows, and 0.372847s elapsed in the smoke utility

- Added an upper-bound rejection gate for rare incident generation:
  - murder, property crime, public virtue, and knowledge/culture incidents now
    draw the same chance roll before building per-person scoring contexts, skip
    immediately when even max propensity could not pass the capped chance, and
    only score candidates for surviving rolls
  - the affair-scandal path keeps its pair-based scoring path, with an added
    participant-threshold guard so two stable low-propensity people do not
    become eligible only because their pair score is summed
  - a current-code 5-year `default_diag_profile` resume dropped
    `incidents.generate` from 41.971246s to 11.129776s versus the immediately
    preceding pre-patch baseline group
  - wall time for comparable 5-year profiled resume slices went from 435.545s
    pre-patch to 115.022s on the final current-code run
  - verified with `unit_test.test_simulation_incidents`,
    `unit_test.test_event_scoring`, and `py_compile`

- Batched and cached checkpoint event flushing:
  - `append_simulation_event_rows` now prepares pending event rows once,
    bulk-inserts core `simulation_events` rows, derives the contiguous inserted
    IDs, and bulk-links `simulation_event_people` plus default event records
    before running the existing richer side-table upserts
  - event place normalization now uses a flush-local region/settlement key cache
    so high-volume event batches do not repeat the same lookup-table
    `SELECT`/`INSERT OR IGNORE` work for every payload
  - side-table handlers for move rows, domain states, obligations, reputation
    marks, legal fallout, faction memory, institutions, and innovation
    discoveries reuse the same cache when they need additional place keys
  - a measured public-stage record batching experiment was rejected after it
    worsened the real `default_diag_profile` flush profile
  - final current-code 5-year `default_diag_profile` resume
    (`2026-06-06T01:48:29.676323+00:00`, years >= 562, 75,383 flushed events)
    measured `checkpoint.flush_events` at 7.556001s, down from 10.409684s in
    the immediately preceding current-code checkpoint baseline
    (`2026-06-06T01:15:25.697746+00:00`)
  - verified with `unit_test.test_save_checkpoint`,
    `unit_test.test_event_memory_lifecycle`, `unit_test.test_simulation_incidents`,
    and `py_compile`

- Ran a fresh full 250-year / 250-couple production-scale timing comparison:
  - command used the bundled Python runtime with
    `utils/run_population_simulation.py --world-id prod_timing_250_foreground --reset-world --years 250 --starting-couples 250 --seed 320062422 --progress --profile-last-years 10`
  - the scratch world avoided wiping `worlds/default/save.sqlite` while using
    the same default-world config data and the same seed as the previous
    post-indexing baseline row
  - the run completed in 1,089.742s, about 18 minutes 10 seconds, and ended
    with 13,598 detailed alive people plus 20,378 passive-cohort alive people
  - the previous same-seed post-indexing baseline was 1,470.000s and ended with
    14,513 alive people, so the current measured run is about 25.9% faster
    despite carrying the newer event-memory/consequence/innovation save
    surfaces
  - the final 10-year profile row
    (`2026-06-06T02:33:21.528152+00:00`) recorded the largest buckets as
    `summary.incidents` 19.905s, `incidents.generate` 17.794s,
    `careers.assign_rehire` 14.189s, `summary.innovation` 9.384s,
    `checkpoint.flush_events` 9.307s, and `summary.trade_networks` 7.449s

- Measured checkpoint and file-store cost separately from simulation logic:
  - yearly file-store timing is split into yearly-summary staging, current-people snapshot staging, and flush-if-due
  - save timing is split into full checkpoint snapshots vs lighter event/meta checkpoints
  - finalization timing separates run-store event import, final full checkpoint, and final file-store flush
  - final report timing separates text report build/write, people JSON build/write, and places GeoJSON build/write

### Completed Milestone Work

- Built annual household/resource indexes and profiled the final years on smoke runs.
- Ran a post-indexing 250-year / 250-couple production-scale timing comparison:
  - `unit_test/population_sim_timing.tsv` includes a 1,470 second run ending with 14,513 alive people.
  - Future full-scale comparisons are still useful after meaningful new performance changes.

### Completed Save DB Storage Work

- Added explicit `save.sqlite` schema versioning:
  - `SAVE_SCHEMA_VERSION`
  - `save_metadata(save_schema_version)`
  - `PRAGMA user_version`
  - future-schema guardrails so newer saves fail clearly instead of opening silently

- Added a safe rebuild path for schema upgrades:
  - writes a fresh SQLite file first
  - copies/transforms data through the target schema
  - can swap the original only when explicitly requested

- Removed the save-side `world` column in schema v2:
  - `worlds/<world_id>/save.sqlite` is now the world boundary
  - core simulation checkpoint tables are single-world
  - government checkpoint tables are single-world
  - config tables still keep `world` because they are imported from shared CSV config

- Updated save consumers and tests for the v2 schema:
  - world clock stores one row per save
  - checkpoint/bootstrap logic counts people without a world filter
  - Gradio browser tolerates both old and new save schemas for inspection
  - polity map utility no longer needs `--sim-world`

- Added compact `simulation_people` checkpoint storage in save schema v3:
  - common `Person` fields are stored as typed columns for cheaper writes, reads, sorting, and filtering
  - `person_json` now keeps extension payload only, currently compact genome/mind-body arrays and derived trait tags
  - checkpoint resume reconstructs full `Person` records from columns plus extension JSON
  - Gradio people browsing reads either compact columns or older JSON-only rows

- Compacted genome and mind/body checkpoint payloads:
  - added `config/genome_save_columns.csv` as the configurable slot-to-trait mapping
  - save payloads use short keys (`g` for genome, `mb` for mind/body) with trait values stored as arrays
  - runtime `Person.genome` / `Person.mind_body` remain named dictionaries so existing simulation logic keeps using trait names

### Completed Save Schema v4 Event Normalization

- Added typed `simulation_events` columns for common query keys:
  - `primary_person_id`
  - `secondary_person_id`
  - `settlement_id`
  - `region_id`
- Added `simulation_event_people(event_id, person_id, role)` for person timeline queries.
- New event writes populate both the typed columns and relation rows.
- Existing v3 event payloads are backfilled when the save schema is ensured or rebuilt.
- Gradio person timelines prefer `simulation_event_people` and fall back to JSON scanning for legacy/fixture saves.
- `payload_json` remains for sparse event detail and human-readable inspection.

### Completed Save Schema v5 Place-Key Normalization

- Normalized high-volume settlement/region references to integer surrogate keys:
  - `simulation_region_lookup(region_key, region_id)`
  - `simulation_settlement_lookup(settlement_key, settlement_id, region_key)`
  - `simulation_people` birthplace/current settlement place columns
  - `simulation_events` common settlement/region columns
  - `simulation_regions` and `simulation_settlements` checkpoint rows
- Added readable inspection views:
  - `simulation_regions_readable`
  - `simulation_settlements_readable`
  - `simulation_events_readable`
- Rebuild logic can transform v3/v4 text-place saves into the v5 key schema.
- Normal event writes compact duplicate settlement/region slugs out of `payload_json`; `SimulationContext.create(..., verbose_event_logging=True)` or `run_population_simulation.py --verbose-event-logging` preserves raw self-contained event payloads for debugging-heavy runs.

### Completed Save Schema v6 Hybrid Population Foundation

- Added `library.passive_population`:
  - `PassivePerson`
  - `PassivePersonRecord`
  - `PassiveCohort`
- Added runtime state and helpers on `SimulationContext`:
  - `passive_people`
  - `passive_cohorts`
  - `add_passive_person(...)`
  - `add_passive_cohort(...)`
  - `_record_inferred_simulation_event(...)`
- Added passive/cohort save tables:
  - `simulation_people_light`
  - `simulation_cohorts`
  - `simulation_promotion_log`
- Added readable inspection views:
  - `simulation_people_light_readable`
  - `simulation_cohorts_readable`
- Added `simulation_events.event_origin`:
  - `generated` for ordinary simulation events
  - `inferred` / `backfilled` for promotion and reverse-generation history
  - compact event payloads omit `event_origin` just like duplicated place slugs
- Checkpoint save/load now roundtrips passive people and cohorts.
- Passive people share the global `person_id` sequence but do **not** enter `current_people_ids`, detailed alive counts, social contact loops, government candidate pools, migration loops, or other detailed-person event thresholds.
- Added regression tests for passive-person/cohort roundtrips, inferred event provenance, and passive people staying out of detailed alive counts.

### Completed Save Schema v7 Movement Event Normalization

- Added `simulation_event_moves` for high-volume `settlement_moved` details:
  - moved person id
  - from/to settlement keys
  - from/to region keys
  - cross-region flag
  - move reason
  - deferred-move request/apply years and source/group labels
- Added `simulation_event_moves_readable` so movement history can be inspected without rehydrating JSON payloads.
- New `settlement_moved` writes populate the normalized move table and compact duplicate movement detail out of normal `payload_json`.
- Verbose event logging still preserves raw self-contained movement payloads for debugging runs.
- Schema ensure/rebuild can backfill movement rows from older raw/verbose `settlement_moved` payloads when the route detail is still present.

### Completed Save Schema v8 Event Memory Foundation

- Added `simulation_event_records` as the first durable split between factual
  simulation events and in-world historical memory:
  - one default record per event;
  - `record_type` for court chronicles, household memories, lineage memories,
    mortuary memories, settlement chronicles, work records, household secrets,
    and admin notes;
  - `visibility_state` values such as `public_known`, `private_known`, and
    `admin_known`;
  - future-ready fields for `lost_year`, `rediscovered_year`, confidence,
    source person/institution, preserving place, public actor/victim,
    distortion JSON, and prose template key.
- New event writes create a default memory record immediately.
- Schema ensure/rebuild backfills default event records for older saves.
- Added `simulation_event_records_readable` so admin/debug tools can inspect
  factual event metadata beside its current in-world record state.
- Added explicit state-transition helpers for event memory:
  - mark records `lost`;
  - seal records into hidden archives/institutions;
  - turn records into `rumored` uncertain/distorted memories;
  - mark records `public_unknown` for unresolved public notices;
  - mark records `misattributed` for false public actors, victims, or causes;
  - mark records `rediscovered`.
- Rediscovery can now log a linked factual `event_rediscovered` row, so finding
  a lost record becomes part of the history rather than only a silent state
  update.
- Added regression tests for appended event records and v7-to-v8 event-record
  backfill, plus lost/sealed/rumored/public-unknown/misattributed/rediscovered
  transitions.

### Completed First Genome-Driven Incident Slice

- Added `library.simulation_incidents` for rare detailed personal incidents.
- Implemented the first bounded event generator: `murder`.
  - Samples adult candidates per settlement using the existing deterministic
    decision-sample pattern.
  - Scores violent actor propensity from genome traits including `justice`,
    `empathy`, `patience`, `temperance`, `courage`, `assertiveness`,
    `neurochemical`, and `ambition`.
  - Mixes local settlement resource pressure into the chance gate.
  - Classifies initial incident kinds such as `domestic_murder`,
    `feud_killing`, `rash_brawl_killing`, `predatory_murder`, and `murder`.
  - Records structured event payloads with killer, victim, witnesses, motive,
    place, actor propensity, historical importance, and genome signal context.
  - Marks the victim dead immediately so downstream same-year systems can react.
- Integrated the incidents tick after household care and before government in
  `SimulationContext.record_year_summary`.
- Added event indexing roles for `killer_person_id`, witnesses, and suspects.
- Murder event-memory rows default to `violent_crime_record` with `rumored`
  visibility.
- Added regression tests proving:
  - extreme violent genomes score above stable genomes;
  - a forced murder tick records a murder, kills the victim, and persists a
    rumored violent-crime record;
  - stable low-risk adults do not produce murders even when the chance gate is
    forced open.
- Implemented the second bounded event generator: `property_crime`.
  - Scores theft/fraud/extortion/hoarding-theft propensity from `justice`,
    `honesty`, `empathy`, `persuasion`, `perception`, `ambition`, `frugality`,
    and `adaptability`.
  - Competence traits such as perception/adaptability amplify suspect intent but
    do not make stable people criminal by themselves.
  - Records structured event payloads with perpetrator, target, witnesses,
    motive, incident kind, loss value, place, resource pressure, historical
    importance, and genome signal context.
  - Applies the first property/economy consequence hook: target households lose
    modest `household_prosperity`, perpetrator households gain a smaller amount,
    the affected settlement loses a little prosperity/stability, and the event
    payload records before/after consequence deltas.
- Property-crime event-memory rows default to `property_crime_record` with
  `rumored` visibility.
- Added regression tests proving:
  - extreme property-crime genomes score above stable genomes;
  - a forced property-crime tick records a non-lethal incident and persists a
    rumored property-crime record;
  - stable low-risk adults do not produce property crimes even when the chance
    gate is forced open.
- Implemented the third bounded event generator: `affair_scandal`.
  - Samples existing paramour pairs where at least one participant has a
    spouse/partner outside the paramour relationship.
  - Scores exposure propensity from `mating drive`, `loyalty`, `modesty`,
    `honesty`, `neurochemical`, `assertiveness`, `persuasion`, and
    `discipline`.
  - Records structured event payloads with accused person, paramour, betrayed
    partner(s), witnesses, motive, incident kind, place, resource pressure,
    historical importance, and genome signal context.
  - Applies the first relationship consequence hook: exposed paramour ties end,
    betrayed official couples dissolve when the exposed partner is still linked,
    the affected settlement takes a small stability penalty, and the event
    payload records the relationship fallout.
- Scandal event-memory rows default to `scandal_record` with `rumored`
  visibility.
- Added regression tests proving:
  - scandal-exposure genomes score above stable genomes;
  - a forced affair-scandal tick records an existing paramour affair, persists a
    rumored scandal record, and indexes accused/paramour/betrayed-partner roles;
  - stable low-risk paramour pairs do not produce scandals even when the chance
    gate is forced open.
- Implemented the fourth bounded event generator: `public_virtue`.
  - Scores costly public virtue from `empathy`, `justice`, `nurturance`,
    `civics`, `honesty`, `courage`, `assertiveness`, `discipline`,
    `resilience`, and `frugality`.
  - Samples a benefactor and beneficiary per settlement under bounded
    candidate pools, with resource pressure and beneficiary hardship shaping
    event chance and target selection.
  - Classifies initial incident kinds such as `heroic_rescue`, `public_mercy`,
    `public_arbitration`, and `loyal_service`.
  - Records structured event payloads with benefactor, beneficiary, witnesses,
    motive, incident kind, place, resource pressure, historical importance,
    relief value, and genome signal context.
  - Applies the first public-virtue consequence hook: beneficiary households
    gain modest prosperity, benefactor households pay a smaller cost, the
    affected settlement gains a little prosperity/stability and food-pressure
    relief, low/blank benefactor leadership reputation rises to `medium`, and
    the event payload records relief and reputation deltas.
- Public-virtue event-memory rows default to `public_virtue_record` with
  `public_known` visibility.
- Added regression tests proving:
  - heroic public-virtue genomes score above selfish/low-prosocial genomes;
  - a forced public-virtue tick records a positive public event, persists a
    public-known virtue record, and indexes benefactor/beneficiary roles;
  - low-prosocial adults do not produce public-virtue events even when the
    chance gate is forced open.
- Implemented the fifth bounded event generator: `knowledge_culture`.
  - Scores breakthrough propensity from `curiosity`, `creativity`, `intellect`,
    `focus`, `perception`, `discipline`, `civics`, `wit`, and `adaptability`.
  - Samples creators, patrons, and witnesses per settlement under bounded
    candidate pools, with low resource pressure and high creator propensity
    shaping event chance.
  - Classifies initial incident kinds such as `invention`, `discovery`,
    `legal_precedent`, `artistic_triumph`, and `scholarly_breakthrough`.
  - Records structured event payloads with creator, patron, witnesses, motive,
    knowledge domain, incident kind, place, resource pressure, historical
    importance, novelty value, and genome signal context.
  - Applies the first knowledge/culture consequence hook: breakthroughs nudge
    settlement and regional prosperity/stability, patronage transfers modest
    household prosperity from patron to creator when a patron exists, low/blank
    creator status reputation rises to `middle-high`, and the payload records a
    structured per-domain `knowledge_state` delta for future durable domain
    tables.
- Knowledge/culture event-memory rows default to `knowledge_record` with
  `public_known` visibility.
- Added regression tests proving:
  - knowledge/culture creator genomes score above low-aptitude genomes;
  - a forced knowledge/culture tick records a public breakthrough, persists a
    public-known knowledge record, and indexes creator/patron roles;
  - low-aptitude adults do not produce knowledge/culture events even when the
    chance gate is forced open.
- Added the first authored event catalog:
  - `config/event_catalog.csv` now defines 57 concrete event/incident kind rows
    with family labels, context tags, consequence profiles, default memory
    expectations, and selection weights.
  - `library.event_catalog` loads catalog rows from config SQLite and falls
    back to legacy built-in rows for old/fixture databases.
  - `library.simulation_incidents` uses catalog-backed kind selection while
    keeping the existing bounded trait/context gates and rates.
  - New catalog rows expand crime, rescue, legal, invention, and
    succession-adjacent variety, including `storehouse_robbery`,
    `livestock_theft`, `inheritance_fraud`, `river_rescue`,
    `succession_arbitration`, `improved_plow`, `inheritance_judgment`, and
    `succession_precedent`.
- Added regression tests proving:
  - authored catalog rows load through the normal CSV-to-SQLite path;
  - missing catalog tables fall back to legacy kind rows;
  - forced incident tests accept and persist expanded catalog variants while
    preserving their existing family-level consequences.
- Added the Workstream 1 event ontology foundation:
  - `config/event_ontology.csv` now defines authoring/spec rows across the
    initial requested families: violent crime, property/survival crime,
    household scandal, political crime, religious/cultural conflict, public
    virtue, knowledge/culture, and private-life events.
  - `library.event_ontology` loads those rows from config SQLite and falls back
    to the five current vertical-slice families for old/fixture databases.
  - Ontology rows include minimum context, probability trait signals,
    preconditions, likely witnesses, consequence hooks, importance ranges,
    preservation defaults, prose tone variants, and explicit public
    unknown/rumored/known view text.
  - `simulation_event_records` now accepts a `public_unknown` visibility state
    and exposes a derived `public_knowledge_stage` in
    `simulation_event_records_readable`.
  - Added save-layer helpers for stacked public event records so one factual
    event can carry a public "missing/unknown" notice, a distorted rumor, and a
    known public account while admin/debug truth remains the factual
    `simulation_events` row.
  - The History browser now exposes `Public Unknown`, `Public Rumors`, and
    `Public Known` filters, with `Public Chronicle` spanning all public stages
    and `Admin Truth` showing the factual event row.
  - Added regression tests for ontology loading, public-stage prose rendering,
    save-readable public stage projection, and the History browser filters.
- Added the Workstream 2 event scoring foundation:
  - `library.event_scoring` now owns centered signed-genome scoring primitives,
    weighted trait-factor specs, optional role/pressure/opportunity context,
    composite-name weighting, pressure helpers, and deterministic candidate
    threshold/weight utilities.
  - The five existing vertical-slice propensities now route through reusable
    specs while preserving their public names and accepting optional event
    context:
    `violent_actor_propensity`, `property_crime_propensity`,
    `scandal_exposure_propensity`, `public_virtue_propensity`, and
    `knowledge_culture_propensity`.
  - `library.simulation_incidents` now uses shared propensity maps, threshold
    filtering, and threshold-excess weights rather than duplicating that logic
    in each incident family.
  - The helper layer accepts `SimulationPersonRecord`, `Person`, or raw genome
    mappings, so future political, religious/cultural, and private-life event
    generators can ask "who is likely to do this under these conditions?"
    without importing the incident module.
  - Added regression tests for trait basis functions, context/role/composite
    inputs, candidate weighting, and the existing vertical-slice propensity
    separations.
- Added the first durable domain-state consequence model:
  - Bumped `save.sqlite` to schema v9.
  - Added `simulation_domain_states` and `simulation_domain_states_readable` as
    one-row-per-region/domain state accumulated from `knowledge_culture` events.
  - Event flush now upserts regional `domain_score`, breakthrough count,
    first/latest years, first/latest event ids, latest incident kind, latest
    creator, and latest settlement from the structured `knowledge_state`
    consequence payload.
  - Older v8 saves backfill domain-state rows from existing `knowledge_culture`
    events when checkpoint schema is ensured/rebuilt; reset/clear paths remove
    the derived rows with the rest of the checkpoint.
  - Added regression tests proving forced knowledge/culture events create the
    readable domain-state row and v8 knowledge events backfill into v9 domain
    state.
- Added the first durable obligation consequence model:
  - Bumped `save.sqlite` to schema v10.
  - Added `simulation_obligations` and `simulation_obligations_readable` as an
    active obligation ledger keyed back to source factual events.
  - Public-virtue relief now emits a `relief_debt` from beneficiary to
    benefactor, with strength, start year, expected end year, place, and source
    role persisted.
  - Knowledge/culture patronage now emits a `patronage_debt` from creator to
    patron when a patron exists, with the same durable obligation fields.
  - Older v9 saves backfill obligation rows from existing public-virtue relief
    and knowledge patronage consequence payloads when checkpoint schema is
    ensured/rebuilt; reset/clear paths remove obligation rows with the rest of
    the checkpoint.
  - Added regression tests proving generated public-virtue and knowledge/culture
    events create readable obligation rows, and v9 public-virtue rows backfill
    into v10 obligations.
- Added the first durable reputation-memory consequence model:
  - Bumped `save.sqlite` to schema v11.
  - Added `simulation_reputation_marks` and
    `simulation_reputation_marks_readable` as source-event-backed rows for
    leadership/status reputation changes.
  - Public-virtue benefactor reputation lifts now persist a `leadership` mark
    with before/after values, direction, strength, place, source event, and mark
    year.
  - Knowledge/culture creator reputation lifts now persist a `status` mark with
    the same durable inspection fields.
  - Older v10 saves backfill reputation marks from existing
    `consequences.public_reputation` payloads when checkpoint schema is
    ensured/rebuilt; reset/clear paths remove reputation marks with the rest of
    the checkpoint.
  - Added regression tests proving generated public-virtue and knowledge/culture
    events create readable reputation marks, and v10 public-virtue rows backfill
    into v11 marks.
- Added the first durable legal fallout consequence model:
  - Bumped `save.sqlite` to schema v12.
  - Added `simulation_legal_fallout` and
    `simulation_legal_fallout_readable` as source-event-backed rows for
    legitimacy and inheritance disputes.
  - Affair-scandal `heir_legitimacy_rumor` events now persist an active
    `heir_legitimacy_challenge` with principal, opposing, related person,
    severity, place, start year, and expected resolution year.
  - Affair-scandal `inheritance_scandal` events now persist an active
    `inheritance_dispute` with the same durable inspection fields.
  - Older v11 affair-scandal events backfill legal fallout rows from existing
    factual payloads when checkpoint schema is ensured/rebuilt; reset/clear
    paths remove legal fallout rows with the rest of the checkpoint.
  - Added regression tests proving forced affair scandals create readable legal
    fallout rows, and v11 inheritance-scandal rows backfill into v12 fallout.
- Added the first deterministic event-prose renderer:
  - `library.event_prose` derives prose on demand from factual
    `simulation_events_readable` rows and memory-state
    `simulation_event_records_readable` rows.
  - It exposes factual admin summaries for true events and public chronicle
    prose for `public_unknown`, `rumored`, `misattributed`, `public_known`,
    and `rediscovered` records.
  - Initial authored templates cover `murder`, `property_crime`,
    `affair_scandal`, `public_virtue`, `knowledge_culture`, `birth`, `death`,
    `event_rediscovered`, plus generic public-unknown, misattributed,
    lost/sealed/private/admin-known records.
  - It keeps long prose out of `save.sqlite`; rendered text remains traceable to
    `event_id`, `record_id`, and `prose_variant_key`.
- Added regression tests proving:
  - factual admin summaries include true structured payload details;
  - public chronicle loading excludes private/admin records while rendering
    public-known and rumored event records;
  - lost records disappear from public chronicle output until rediscovery, then
    receive rediscovered prose.
- Added the first History browser/query surface:
  - `utils.gradio_data_browser` now has an explicit-load History tab for
    factual admin truth, public chronicle rows, public unknown records,
    rumors/misattributions, public known records, lost records, and
    rediscoveries.
  - The same tab now has an explicit-load History Summary table for report-style
    totals, tracked incident counts including zeros, event-type counts,
    visibility-state counts, and metric summaries.
  - The surface supports exact event-type filtering, text search, limit, and
    offset.
  - Rediscovery view includes both the restored original records and linked
    `event_rediscovered` events.
  - The browser delegates all prose generation to `library.event_prose`, keeping
    UI code focused on query/filter presentation.
- Added regression tests proving:
  - public, public-unknown, rumor/misattribution, public-known, and lost
    History views load the expected rows;
  - admin truth can filter to `event_rediscovered`;
  - rediscovery History rows include both restored original memory and the
    rediscovery event.
  - History Summary exposes event report counts, zero-count tracked incident
    slices, lifecycle visibility states, and linked rediscovery rows.
- Added the first event-history tuning report:
  - `library.event_history_report` summarizes event counts by type/year,
    event-memory visibility states, tracked incident slice counts, numeric
    payload metrics such as `historical_importance` / `resource_pressure`, and
    public chronicle prose samples.
  - `utils/util_event_history_report.py` writes report artifacts to
    `temp/event_history_report/<world>/`.
  - Report outputs include `summary.txt`, `event_counts_by_type.tsv`,
    `event_counts_by_year_type.tsv`, `event_visibility_counts.tsv`,
    `tracked_incident_counts.tsv`, `event_metric_summaries.tsv`, and
    `public_chronicle_samples.tsv`.
- Tuned initial event rates/gates after fixed-seed scratch samples showed the
  first defaults were too quiet for review.
  - Final sample command used `event_tuning_sample`, 80 years, 80 starting
    couples, seed `20260603`, no passive background population, and no canonical
    report overwrite.
  - Final sample produced 18,213 factual events / 18,213 records in an about
    18.1 MB save.
  - Tracked incident counts were: `murder` 6, `property_crime` 3,
    `affair_scandal` 2, `public_virtue` 6, and `knowledge_culture` 3.
  - The same sample showed 39 still-lost records, 2 original records in
    `rediscovered` state, and 2 linked factual `event_rediscovered` rows.
  - Murder was retuned after a larger 200-year run produced 85 murders, far
    below the rough historical-criminology target of about 3-5 murders per
    10,000 people per year. The generator now uses an explicit detailed
    population target of 4 murders per 10,000 people per year, population-scaled
    settlement trials, and a population-scaled annual cap while still requiring
    a sufficiently high violent-actor propensity candidate.
  - The report now makes zero-count incident slices explicit instead of hiding
    them by omission.
- Re-ran the fixed-seed event tuning sample after `config/incident_rates.csv`
  made incident rates era-tunable.
  - Command shape stayed the same: `event_tuning_sample`, 80 years, 80 starting
    couples, seed `20260603`, no passive background population, and no canonical
    report overwrite.
  - New report produced 17,871 factual events / 17,871 records in a 17,719,296
    byte save.
  - Tracked incident counts were: `murder` 7, `property_crime` 45,
    `affair_scandal` 4, `public_virtue` 2, and `knowledge_culture` 1.
  - Compared with the previous 80-year report, murder stayed close at 6 -> 7,
    property crime rose sharply at 3 -> 45, and affair scandal rose modestly at
    2 -> 4.
  - Report artifacts were written under
    `temp/event_history_report/event_tuning_sample/`.
- Added the first automatic event-memory lifecycle:
  - `library.event_memory_lifecycle` reviews a bounded deterministic shard of
    old event records each simulation year, after the annual save checkpoint has
    flushed factual events and default memory records.
  - Old `private_known`, `public_unknown`, `rumored`, `misattributed`, and
    `public_known` records can become `lost` using record-type-specific age and
    chance policies.
  - Old `lost` or `sealed` records can become `rediscovered`; rediscovery writes
    a linked factual `event_rediscovered` row and preserves source/confidence
    details for prose/admin inspection.
  - The lifecycle uses stable hash draws rather than the global RNG, so the same
    save/year/review shard produces repeatable transitions.
- Tuned lifecycle rates against a fresh fixed-seed `event_tuning_sample` run:
  - the 80-year / 80-couple / seed `20260603` sample produced 17,363 events and
    records in a 17,252,352 byte save;
  - tracked incidents remained present: `murder` 11, `property_crime` 43,
    `affair_scandal` 4, `public_virtue` 5, `knowledge_culture` 1;
  - routine work records now decay much later and rediscover far less often,
    leaving 0 lost and 0 rediscovered work records in the sample;
  - lineage birth memory was already durable and stayed that way, with 1 lost
    birth record out of 2,555.
- Added regression tests proving:
  - report counts, visibility rows, metric summaries, and prose samples derive
    from real save-schema rows;
  - report writing emits all expected TSV/text artifacts.
  - old public/private/unknown/rumored/misattributed records can be aged to
    `lost`;
  - old lost/sealed records can resurface as linked rediscoveries;
  - high-volume private work/birth records decay and rediscover later than
    incident records;
  - `record_year_summary` invokes the lifecycle after save persistence.

### Completed Hybrid Mixed-Mode Population Pass

- Replaced static background cohort snapshots in the canonical population runner with passive demographic evolution:
  - initial passive cohorts are seeded from regional capacity minus detailed residents;
  - later cohorts age forward by one year;
  - passive deaths are applied by age;
  - passive births produce age-0 aggregate newborn cohorts;
  - adult cohorts track a coarse `partnered` status bucket for passive partnership mass;
  - detailed births above the configured detailed-active soft cap are absorbed into passive newborn cohorts instead of generating full `Person` records.

- Added passive-to-detailed promotion for office selection:
  - government head-seat and merit/election vacancy paths can promote a passive adult when the detailed candidate pool is empty;
  - the source aggregate cohort is decremented when a person is promoted;
  - promotion records inferred events so generated history can distinguish synthesized anchors from ordinary annual simulation.

- Added plausible full `Person` synthesis from passive facts:
  - promotion preserves known birth year, gender, species, ethnic/culture, birthplace/current settlement, partner id, death year, and coarse prosperity/status where known;
  - missing genome, mind/body, names, fertility ages, appearance, and derived traits are generated through the normal person generator;
  - passive `species` and `ethnic` now roundtrip through `simulation_people_light` so post-checkpoint promotion keeps culture/species context.

- Added fuller mixed-mode reporting:
  - `yearly_summary.csv` now includes `detailed_alive_count`, `passive_person_alive_count`, `aggregate_cohort_alive_count`, `aggregate_cohort_partnered_count`, `mixed_mode_alive_count`, `passive_cohort_births_count`, and `passive_cohort_deaths_count`;
  - `run_population_simulation.py` exposes `--detailed-active-soft-cap` and still prints detailed plus latest aggregate cohort counts at run end.

- Added focused regression coverage:
  - passive cohorts age, birth, die, and keep deterministic counts;
  - passive newborn cohorts are retained even when background population scale is zero;
  - office promotion materializes a passive candidate into a detailed `Person` with genome/mind-body state;
  - passive person checkpoint roundtrip preserves species/ethnic fields.

- Added richer passive family/partnership records:
  - `PassivePerson` now carries coarse partner name/birth/death years, partnership start/end years, passive child ids, and child birth years;
  - `simulation_people_light` and `simulation_people_light_readable` persist/expose these fields;
  - office promotion synthesizes partner and child timing facts when promoting from a partnered aggregate cohort;
  - passive promotion emits inferred `promotion_backfill_partnership` and `promotion_backfill_children` events so later narrative/detail systems can see the established passive family anchors;
  - checkpoint and promotion regression tests cover passive family roundtrip and inferred family backfill events.

- Added passive-to-detailed promotion for marriage into detailed families:
  - after normal same-settlement and same-region pairing, a bounded fallback can promote single adult passive people as spouses for unmatched detailed adults;
  - the source aggregate cohort is decremented and the promoted person enters the normal detailed couple path;
  - regression coverage verifies the passive spouse promotion, cohort decrement, and `marriage_into_detailed_family` promotion event.

- Added passive-to-detailed focus promotion for user inspection and narrative spotlighting:
  - `promote_passive_person_for_focus(...)` can promote an existing passive person by passive `person_id`, settlement id, or region id;
  - focus reasons normalize to `user_inspection` or `narrative_spotlight`;
  - `SimulationContext.promote_passive_person(...)` records append-only `simulation_promotion_log` rows with the promotion reason and synthesized/source metadata;
  - focus promotion still emits inferred `passive_person_promoted` and `promotion_backfill_birth` events, plus existing family backfill events when passive family anchors are present;
  - regression coverage verifies all three selector modes, persisted promotion-log reasons, inferred backfill events, and duplicate-safe checkpoint persistence.

- Added `utils/run_mixed_mode_scale_smoke.py` for hardware-friendly scale preflights:
  - seeds one active aggregate settlement per configured region;
  - evolves passive/cohort demographics for target historical populations without materializing detailed people;
  - writes `temp/mixed_mode_scale_smoke.tsv`;
  - default 10-year smoke targets completed locally:
    - 100K target: 94,890 aggregate alive, 14,741 cohort rows, 1.17s;
    - 1M target: 948,862 aggregate alive, 19,525 cohort rows, 1.71s;
    - 10M target: 9,488,947 aggregate alive, 22,569 cohort rows, 1.64s.

- Added `utils/run_mixed_mode_calibration.py` for full-runner mixed-mode calibration:
  - calls the canonical population-growth runner instead of only exercising passive cohorts directly;
  - seeds one aggregate active settlement per configured region so passive population can scale across the whole authored world;
  - bounds detailed people with a configurable target fraction and min/max detailed cap;
  - writes `temp/mixed_mode_calibration.tsv`;
  - local 3-year smoke with 5 starting couples and 50-500 detailed cap completed:
    - 100K target: 98,239 mixed alive, 12 detailed alive, 17,211 cohort rows, 5.10s;
    - 1M target: 982,097 mixed alive, 13 detailed alive, 23,220 cohort rows, 9.51s;
    - 10M target: 9,820,933 mixed alive, 12 detailed alive, 24,853 cohort rows, 9.44s.
  - local 10-year calibration with 10 starting couples and 100-1,000 detailed cap completed:
    - 100K target: 94,906 mixed alive, 37 detailed alive, 14,702 cohort rows, 28.59s;
    - 1M target: 949,001 mixed alive, 27 detailed alive, 19,582 cohort rows, 38.03s;
    - 10M target: 9,489,032 mixed alive, 26 detailed alive, 22,746 cohort rows, 37.97s.

- Tuned passive fertility for target-stable 10-year mixed-mode calibration:
  - raised `_PASSIVE_BIRTH_RATE` from `0.105` to `0.165`;
  - local 10-year calibration with 10 starting couples and 100-1,000 detailed cap now completes near target:
    - 100K target: 99,976 mixed alive, 37 detailed alive, 14,702 cohort rows, 28.39s;
    - 1M target: 999,948 mixed alive, 27 detailed alive, 19,582 cohort rows, 37.14s;
    - 10M target: 9,998,875 mixed alive, 26 detailed alive, 22,746 cohort rows, 40.19s.

### Event Threshold Decision

- Passive/cohort scale does not feed detailed-person event loops automatically.
- Social formation budgets now scale by the detailed candidate pool, with absolute safety caps:
  - `PARAMOUR_CONTACT_TRIAL_SHARE_OF_ELIGIBLE = 0.75`
  - `PARAMOUR_CONTACT_TRIAL_ABSOLUTE_CAP = 5_000`
  - `SAME_SEX_TRIAL_SHARE_OF_ELIGIBLE = 0.12`
  - `SAME_SEX_TRIAL_ABSOLUTE_CAP_PER_POOL = 1_000`
- These are detailed narrative sampling rates, not total incidence rates for passive/cohort populations.

## Gradio Data Browser

### Stable Browser Patterns

- Keep heavy data grids behind explicit load actions until the tab-autoload issue is understood.
- Existing browser helpers support manual loading/filtering for people, settlements, regions, polities, map detail, and raw SQLite tables.
- Person timeline lookups now use normalized event-person links when available.

### Not Done

- Tab-select autoloading for Gradio grids is intentionally **not** considered solved. See `TODO.md` before adding or keeping `.select(...)` handlers on tabs that populate large tables.
