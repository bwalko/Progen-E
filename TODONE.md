# TODONE

## Detailed Population Variance And Serial Predator Signal

- Added `library.detailed_population_variance` for deterministic
  center-or-extreme genome variance when a person is selected into full detailed
  simulation:
  - production founder generators now apply the selection-variance path;
  - passive-person promotions apply it based on promotion reason/source;
  - non-detailed city-directory promotions apply it with a
    `source_kind=nondetailed_directory` profile;
  - named selection profiles now distinguish ruler, officeholder, elite,
    specialist, criminal/outlaw, religious, migrant/frontier, kinship-link,
    inspection, spotlight, founder, and non-detailed-directory materialization;
  - materialized people get a `High-Variance Detail` composite marker while
    non-detailed baseline rows remain genome-free and ordinary.
- Added a rare serial-predator signal inside existing murder-rate calibration:
  - `serial_predator_propensity(...)` scores only extreme/repeat-capable
    detailed profiles;
  - murder event volume still comes from `incident_rates.csv` and the existing
    homicide target gates;
  - prior killer-role murder rows from pending events and `simulation_events` /
    `simulation_event_people` bias candidate selection and event payloads;
  - murder payloads now include `serial_predator_propensity`,
    `previous_murder_count`, and `serial_predator_candidate`.
- Strengthened repeat-predator emergence without increasing murder volume:
  - murder chance and annual caps still come from the existing homicide-rate
    gates;
  - murder event volume was retuned to
    `MURDER_BASE_SETTLEMENT_CHANCE = 0.00075` and
    `MURDER_RATE_CONTEXT_MULTIPLIER = 0.18` after representative mixed-mode
    probes showed the previous chance gates above the configured 4-per-10K
    target;
  - founder, baseline, officeholder/detail-floor, elite, specialist, spotlight,
    and criminal/outlaw detailed-selection profiles now include profile-specific
    rare repeat-capable tails, with criminal/outlaw remaining much higher than
    ordinary detailed materialization;
  - production founder generation now seeds detailed-selection variance with
    the actual next person id instead of `0`, so rare deterministic profile
    variation is per founder rather than collapsing through one id seed;
  - after a murder gate passes, `_repeat_murder_selection_multiplier(...)`
    explicitly boosts only already-eligible killer candidates with high serial
    propensity or prior killer-role murders;
  - the multiplier is capped at
    `MURDER_REPEAT_KILLER_SELECTION_MULTIPLIER_CAP = 2.25`, which keeps one
    capped repeat-prone candidate below a 1% killer-selection share in the
    canonical 250-person murder sample while still making repeat emergence
    meaningfully more likely than ordinary selection.
- Added regression coverage for:
  - promoted detailed people receiving higher-variance genomes;
  - deterministic materialization for the same person/year/reason/source;
  - founder selection receiving the variance marker;
  - passive-promotion wiring through `SimulationContext`;
  - serial-predator propensity staying low for ordinary people and rising for
    extreme/repeat profiles;
  - incident genome-signal payloads and pending prior-murder counts;
  - the repeat-murder multiplier remaining bounded and its cap staying below
    the 1% single-candidate guardrail share in the canonical settlement sample;
  - the same cap still producing an expected 3+ murders for one persistent
    capped repeat-prone candidate across a 500-murder emergence sample;
  - the actual weighted killer-selection path producing a deterministic 3+
    repeat-killer emergence across a 500-murder sample while staying at or
    below the 1% serial-murder guardrail;
  - a generated detailed-selection founder profile, scored by
    `serial_predator_propensity(...)`, feeding that same weighted
    killer-selection path and reaching 3+ repeat selections in a 500-murder
    sample while staying within the 1% guardrail;
  - founder materialization rarely but deterministically producing
    serial-capable profiles in a bounded cohort.
- Validation:
  - `python -m unittest unit_test.test_detailed_population_variance
    unit_test.test_event_scoring unit_test.test_simulation_incident_helpers`;
  - `python -m unittest unit_test.test_simulation_incidents`;
  - `python -m unittest unit_test.test_simulation_incident_helpers
    unit_test.test_event_scoring`;
  - `python -m unittest unit_test.test_simulation_incidents`, which passed with
    existing `library\random_names.py` unclosed sqlite `ResourceWarning`s;
  - `python -m py_compile library\simulation_incidents.py
    unit_test\test_simulation_incident_helpers.py`;
  - `python -m unittest unit_test.test_simulation_incident_helpers
    unit_test.test_event_scoring`;
  - `python -m unittest unit_test.test_mixed_mode_calibration`;
  - `python -m py_compile library\simulation_incidents.py
    unit_test\test_simulation_incident_helpers.py`;
  - `python -m unittest unit_test.test_simulation_incident_helpers
    unit_test.test_event_scoring unit_test.test_mixed_mode_calibration`;
  - `python -m py_compile library\simulation_incidents.py
    utils\run_mixed_mode_calibration.py
    unit_test\test_simulation_incident_helpers.py
    unit_test\test_mixed_mode_calibration.py`;
  - `python -m unittest unit_test.test_simulation_incidents`, which passed with
    existing `library\random_names.py` unclosed sqlite `ResourceWarning`s;
  - medium bundled-Python serial probe:
    `utils\run_mixed_mode_calibration.py --targets 10000 --replicates 3
    --years 20 --starting-couples 5 --min-detailed-cap 120
    --max-detailed-cap 250 --output temp\mixed_mode_medium_serial_probe.tsv`,
    which produced 3 murders across 3 scenarios,
    `murder_per_10k_detailed_person_years=4.297994`, target ratio `1.074499`,
    and `hybrid_calibration_status=needs_more_murder_sample`, confirming the
    run was directionally near the murder-rate target but too small for serial
    guardrail or emergence proof;
  - attempted larger bundled-Python serial probe:
    `utils\run_mixed_mode_calibration.py --targets 50000 --replicates 1
    --years 50 --starting-couples 20 --detailed-fraction 0.05
    --min-detailed-cap 1000 --max-detailed-cap 2500 --output
    temp\mixed_mode_large_serial_probe.tsv`, which reached settlement spin-off
    and stopped because bundled Python lacks the optional `shapely` dependency
    required by world-map geometry;
  - no-spin-off larger bundled-Python serial probe:
    `utils\run_mixed_mode_calibration.py --targets 50000 --replicates 1
    --years 50 --starting-couples 20 --detailed-fraction 0.05
    --min-detailed-cap 1000 --max-detailed-cap 2500
    --disable-birth-settlement-spinoff --output
    temp\mixed_mode_large_no_spinoff_probe.tsv`, which completed in the bundled
    runtime, wrote `birth_settlement_spinoff_disabled=yes`, produced 23 murders,
    `murder_per_10k_detailed_person_years=9.055118`, target ratio `2.263780`,
    and `hybrid_calibration_status=retune_murder_rate_above_target`;
  - representative bundled-Python probe after optional-geometry fallback and
    final murder-volume retune:
    `utils\run_mixed_mode_calibration.py --targets 50000 --replicates 2
    --years 50 --starting-couples 20 --detailed-fraction 0.05
    --min-detailed-cap 1000 --max-detailed-cap 2500 --output
    temp\mixed_mode_large_retuned_replicates2.tsv`, which kept
    birth-settlement spin-off enabled, produced 12 murders across 35,601
    detailed person-years, wrote
    `murder_per_10k_detailed_person_years=3.370692`, target ratio `0.842673`,
    `murder_rate_calibration_status=within_target_band`, and
    `hybrid_calibration_status=needs_more_serial_guardrail_sample`;
  - bundled-Python serial-profile smoke:
    `utils\run_mixed_mode_calibration.py --targets 10000 --replicates 1
    --years 10 --starting-couples 5 --min-detailed-cap 120
    --max-detailed-cap 250 --output temp\mixed_mode_serial_profile_probe5.tsv`,
    which wrote 1 serial-predator profile out of 158 scored detailed people
    (`serial_predator_profile_share_of_scored_detailed=0.006329`) and
    `max_serial_predator_propensity=0.776815`, after earlier profile smokes had
    shown zero profiles and max propensities below the 0.62 candidate threshold;
  - bundled-Python serial-profile status smoke:
    `utils\run_mixed_mode_calibration.py --targets 10000 --replicates 1
    --years 10 --starting-couples 5 --min-detailed-cap 120
    --max-detailed-cap 250 --output temp\mixed_mode_profile_status_smoke.tsv`,
    which wrote `serial_predator_profile_sample_ready=yes`,
    `serial_predator_profile_calibration_status=serial_predator_profiles_present`,
    `serial_predator_profile_target_share_max=0.020000`, and kept the overall
    status at `needs_more_murder_sample` because the zero-murder smoke had not
    reached the 10-murder rate gate;
  - aggregate summary projection smoke:
    `utils\run_mixed_mode_calibration.py --targets 10000 --replicates 1
    --years 10 --starting-couples 5 --min-detailed-cap 120
    --max-detailed-cap 250 --output temp\mixed_mode_projection_smoke.tsv`,
    run with bundled Python, which wrote
    `murder_sample_projection_rate_source=target`,
    `serial_murder_sample_projected_additional_detailed_person_years=250000`,
    and
    `serial_murder_emergence_projected_additional_detailed_person_years=1250000`
    before the temporary smoke files were removed;
  - bundled-Python deterministic weighted-selection proof:
    `python -m unittest unit_test.test_simulation_incident_helpers`, which
    covers the actual `_weighted_choice(...)` killer-selection weighting path
    and proves a capped repeat-capable candidate can reach 3+ selections in a
    500-murder sample while remaining within the 1% guardrail;
  - bundled-Python endogenous generated-profile proof:
    `python -m unittest unit_test.test_simulation_incident_helpers`, which
    covers a founder generated through `apply_detailed_selection_variance(...)`,
    scored by `serial_predator_propensity(...)`, and then passed through the
    same weighted killer-selection path;
  - bundled-Python focused suite:
    `python -m unittest unit_test.test_simulation_incident_helpers
    unit_test.test_detailed_population_variance unit_test.test_event_scoring
    unit_test.test_mixed_mode_calibration unit_test.test_event_history_report`;
  - bundled-Python compile check:
    `python -m py_compile unit_test\test_simulation_incident_helpers.py
    library\detailed_population_variance.py library\simulation_incidents.py
    library\event_scoring.py`;
  - `python -m unittest unit_test.test_detailed_population_variance
    unit_test.test_event_scoring unit_test.test_event_history_report
    unit_test.test_mixed_mode_calibration`;
  - `python -m unittest unit_test.test_mixed_mode_calibration
    unit_test.test_event_history_report unit_test.test_detailed_population_variance
    unit_test.test_event_scoring`, which passed with an existing
    `library\world_map_geometry.py` unclosed sqlite `ResourceWarning`;
  - `python -m unittest unit_test.test_mixed_mode_calibration`;
  - `python -m py_compile library\detailed_population_variance.py
    library\population_growth_runner.py library\event_history_report.py
    utils\run_mixed_mode_calibration.py`;
  - `python -m py_compile utils\run_mixed_mode_calibration.py
    unit_test\test_mixed_mode_calibration.py`;
  - `python -m py_compile utils\run_mixed_mode_calibration.py
    library\event_history_report.py library\detailed_population_variance.py
    unit_test\test_mixed_mode_calibration.py`;
  - `python -m unittest unit_test.test_simulation_incident_helpers
    unit_test.test_event_scoring unit_test.test_mixed_mode_calibration
    unit_test.test_lazy_settlements`, which passed with an existing unclosed
    sqlite `ResourceWarning`;
  - `python -m py_compile library\simulation_incidents.py
    library\placenames_generation.py library\simulation_context.py
    utils\run_mixed_mode_calibration.py
    unit_test\test_simulation_incident_helpers.py
    unit_test\test_mixed_mode_calibration.py unit_test\test_lazy_settlements.py`;
  - `python -m unittest unit_test.test_simulation_incidents`, which passed with
    existing `library\random_names.py` unclosed sqlite `ResourceWarning`s;
  - bundled-Python repeat-weight smoke:
    `utils\run_mixed_mode_calibration.py --targets 1000 --replicates 1
    --years 1 --starting-couples 2 --min-detailed-cap 10 --max-detailed-cap 20
    --output temp\mixed_mode_repeat_weight_smoke.tsv`, which still wrote all
    calibration artifacts and the expected insufficient-sample murder,
    serial-guardrail, and serial-emergence summary fields;
  - `python -m unittest unit_test.test_detailed_population_variance
    unit_test.test_event_history_report unit_test.test_mixed_mode_calibration`;
  - `python -m py_compile library\detailed_population_variance.py
    library\event_scoring.py library\simulation_incidents.py
    library\simulation_context.py library\population_growth_runner.py
    library\zero_point_colonies.py unit_test\test_detailed_population_variance.py
    unit_test\test_event_scoring.py unit_test\test_simulation_incident_helpers.py
    unit_test\test_simulation_incidents.py`;
  - `python -m unittest unit_test.test_simulation_engine_zero_point`.

## Hybrid Population Calibration Report Metrics

- Extended `library.event_history_report` with a
  `HybridPopulationCalibrationSummary` section:
  - detailed people and alive detailed people;
  - living non-detailed city-directory people;
  - high-variance detailed marker count;
  - genome-scored detailed people, extreme detailed people, and average
    detailed variance score;
  - event-year span, murder count, serial-predator candidate murder count,
    approximate murders per 10K detailed person-years, and serial-candidate
    share of murders.
- Added TSV/text output for `hybrid_population_calibration.tsv` and the report
  summary's "Hybrid Population Calibration" block.
- Added `hybrid_variance_by_promotion_reason.tsv` so calibration reports can
  compare detailed-person variance by the first persisted promotion reason,
  including person count, high-variance marker count, scored count, extreme
  count, and average variance score.
- Added deterministic fixture coverage that proves the report can read detailed
  person JSON, non-detailed rows, and serial-predator murder payload flags
  without needing a long run, plus promotion-reason variance rows from
  `simulation_promotion_log`.
- Validation:
  - `python -m unittest unit_test.test_event_history_report`;
  - `python -m unittest unit_test.test_detailed_population_variance
    unit_test.test_event_history_report unit_test.test_mixed_mode_calibration`;
  - `python -m unittest unit_test.test_detailed_population_variance
    unit_test.test_event_scoring unit_test.test_simulation_incident_helpers
    unit_test.test_event_history_report`;
  - `python -m py_compile library\event_history_report.py
    unit_test\test_event_history_report.py library\detailed_population_variance.py
    library\event_scoring.py library\simulation_incidents.py
    utils\util_event_history_report.py utils\gradio_data_browser.py`.

## Mixed-Mode Hybrid Calibration TSV

- Extended `utils/run_mixed_mode_calibration.py` so each maintained calibration
  row now includes event-history hybrid metrics from the generated save:
  detailed-person counts, non-detailed counts, high-variance marker counts,
  genome-scored and extreme detailed counts, average detailed variance score,
  serial-predator profile counts/shares and propensity scores, murder counts,
  serial-predator candidate counts, murder rate per 10K detailed person-years,
  and serial-candidate share of murders.
- Taught `library.event_history_report` to decode compact checkpoint genome /
  mind-body arrays with config-provided trait slots, so production saves with
  compact `g` / `mb` payloads produce numeric variance scores rather than only
  marker counts.
- Wired `utils/util_event_history_report.py` to pass the world's
  `genome_save_columns` slot order into event-history reports.
- Added explicit serial-murder guardrail fields to the hybrid report:
  - distinct murder killers;
  - repeat killers with 2+ recorded murders;
  - stricter serial killers with 3+ recorded murders;
  - murders committed by 3+ repeat killers;
  - share of murders committed by 3+ repeat killers;
  - target maximum share `0.01` and a calibration status that stays
    `insufficient_murder_sample` until at least 100 murders are observed.
- Added a separate serial-murder emergence diagnostic so long calibration
  batches can distinguish "rare but present" from "never appears":
  - report and batch summaries expose
    `serial_murder_emergence_min_murder_sample=500`;
  - below that sample they report `insufficient_emergence_sample`;
  - at 500+ murders with no 3+ repeat killer they report
    `no_serial_murder_emerged`;
  - at 500+ murders with at least one 3+ repeat killer under the 1% ceiling they
    report `serial_murder_emerged`.
- Fixed the hybrid calibration rate denominator so the report prefers
  `world_state.start_year/current_year`, then murder-event years, instead of
  letting old birth/founder history events inflate the observation span.
- Added mixed-mode murder-rate target columns:
  - summed `detailed_person_years` from the runner's yearly summary stream, so
    calibration rates use actual detailed exposure instead of final alive count
    times run span;
  - configured murder target per 10K detailed person-years;
  - observed/target ratio;
  - calibration status that stays `insufficient_murder_sample` until at least
    10 murders are observed, then reports below/within/above the target band.
- Added `--disable-birth-settlement-spinoff` to
  `utils/run_mixed_mode_calibration.py` for large event-rate probes in runtimes
  without optional Shapely/world-map geometry support; row TSVs include
  `birth_settlement_spinoff_disabled`, and summary TSVs count disabled
  scenarios so these stress probes are not mistaken for ordinary geography runs.
- Added `--stop-when-hybrid-status` and `--min-scenarios-before-stop` to
  `utils/run_mixed_mode_calibration.py` so long calibration batches can be
  launched with a high replicate ceiling and stop as soon as the aggregate
  `hybrid_calibration_status` reaches an explicit target such as
  `within_hybrid_calibration_targets` or the `calibrated` alias.
- Added `--resume-existing` to `utils/run_mixed_mode_calibration.py`, allowing
  an interrupted or deliberately staged calibration batch to reload existing
  row and promotion-reason TSVs, skip completed `scenario_index` values, run
  only missing planned scenarios, and rewrite aggregate summaries from the
  combined evidence. Resume matching now checks scenario index, target index,
  replicate index, target population, and seed against the current scenario
  plan before skipping a row, so changing the calibration plan does not silently
  reuse mismatched evidence.
- Added `--stop-after-total-murders` and
  `--stop-after-detailed-person-years` to
  `utils/run_mixed_mode_calibration.py` so representative long runs can stop at
  explicit aggregate sample thresholds such as 100 murders for the serial
  guardrail or 500 murders / roughly 1.25M detailed person-years for serial
  emergence.
- Added `recommended_next_calibration_*` fields to the aggregate summary so an
  immature batch names the next sample target and the relevant resume/stop flags
  instead of requiring the operator to infer the next command from several
  readiness columns.
- Added `--write-incremental` to `utils/run_mixed_mode_calibration.py`, using a
  shared output writer that rewrites row, summary, per-scenario
  promotion-reason, and aggregate promotion-reason TSVs after each completed
  scenario. This preserves completed scenario evidence during long batches even
  if a later scenario or process timeout interrupts the run.
- Added an aggregate calibration summary TSV next to each mixed-mode row TSV:
  - combines multi-row batches into total detailed person-years and total murder
    samples;
  - supports first-class `--replicates N` batches for each target population,
    with `scenario_index`, `target_index`, `replicate_index`, and distinct seed
    columns in the row TSV;
  - writes per-scenario `*.promotion_reasons.tsv` and aggregate
    `*.promotion_reason_summary.tsv` artifacts so temporary generated saves do
    not discard promotion-reason variance diagnostics;
  - adds profile-derived expected variance bands and sample-aware
    `reason_variance_calibration_status` values for promotion-reason rows,
    using `insufficient_reason_sample`, `below_profile_floor`,
    `within_profile_band`, or `above_profile_ceiling`;
  - computes weighted murder targets, observed/target ratio, and sample-aware
    murder-rate status;
  - aggregates serial-predator profile people, share of scored detailed people,
    weighted average serial-predator propensity, and max serial-predator
    propensity so batches can distinguish "no repeat-capable people exist" from
    "repeat-capable people exist but murder sample is still immature";
  - adds a sample-aware serial-profile calibration gate with 100 scored detailed
    people as the minimum sample, a maximum profile-share ceiling of 2%, and
    statuses for absent, present, too-common, or immature profile evidence;
  - computes serial-murder 3+ killer share against the same 1% guardrail once
    the combined sample reaches 100 murders;
  - reports murder-rate and serial-murder sample thresholds, remaining murder
    counts, and ready flags so small or medium batches say how much more sample
    is needed before calibration statuses are meaningful;
  - projects the additional detailed person-years and current-average scenario
    equivalents needed to reach the 100-murder serial guardrail and 500-murder
    serial-emergence gates, using the observed murder rate after the
    10-murder rate sample is mature and the configured target rate before then;
  - adds top-level `hybrid_calibration_ready` and
    `hybrid_calibration_status` fields that compose murder-rate, serial
    guardrail, and serial-emergence diagnostics into a single next-action
    verdict such as `needs_more_murder_sample`,
    `needs_more_serial_emergence_sample`, `serial_murder_too_common`, or
    `within_hybrid_calibration_targets`;
  - prioritizes mature murder-rate retuning in the overall verdict before
    asking for larger serial samples, so an above/below-target homicide rate is
    not masked by immature 100- or 500-murder serial diagnostics;
  - aggregates `total_serial_murder_killers_3plus` separately from
    `total_serial_murder_events_by_3plus_killers` so emergence and share checks
    can answer different questions;
  - weights average detailed-variance scores by scored detailed-person counts.
- Made `library.world_map_geometry` importable without Shapely installed, while
  still raising a clear Shapely-required error when polygon geometry is actually
  built. This keeps non-map calibration/reporting tools runnable in lightweight
  environments.
- Made birth-settlement spin-off usable in bundled-Python calibration runs
  without Shapely:
  - settlement naming falls back to local geography without world-map overlays
    when optional geometry is unavailable;
  - duplicate-site polygon checks skip map-polygon comparison when optional
    geometry is unavailable, while ordinary site slots/local geography still
    distinguish settlements.
- Added regression coverage for compact checkpoint payloads and TSV columns in
  `unit_test.test_mixed_mode_calibration`, plus event-history coverage for the
  serial-murder guardrail.
- Validation:
  - `python -m unittest unit_test.test_mixed_mode_calibration
    unit_test.test_event_history_report`;
  - `python -m unittest unit_test.test_event_history_report
    unit_test.test_mixed_mode_calibration unit_test.test_event_scoring
    unit_test.test_simulation_incident_helpers`;
  - `python -m unittest unit_test.test_event_history_report
    unit_test.test_mixed_mode_calibration`;
  - `python -m py_compile library\event_history_report.py
    utils\run_mixed_mode_calibration.py utils\util_event_history_report.py
    unit_test\test_mixed_mode_calibration.py`;
  - `python -m py_compile library\event_history_report.py
    unit_test\test_event_history_report.py utils\run_mixed_mode_calibration.py
    unit_test\test_mixed_mode_calibration.py`;
  - `python -m py_compile library\event_history_report.py
    library\world_map_geometry.py utils\run_mixed_mode_calibration.py
    utils\util_event_history_report.py unit_test\test_event_history_report.py
    unit_test\test_mixed_mode_calibration.py`;
  - bundled-Python smoke:
    `utils\run_mixed_mode_calibration.py --targets 1000,1000 --years 1
    --starting-couples 2 --min-detailed-cap 10 --max-detailed-cap 20 --output
    temp\mixed_mode_calibration_smoke.tsv`, which wrote both the row-level TSV
    and `temp\mixed_mode_calibration_smoke.summary.tsv`, with the tiny combined
    murder sample correctly remaining `insufficient_murder_sample`;
  - bundled-Python replicate smoke:
    `utils\run_mixed_mode_calibration.py --targets 1000 --replicates 2
    --years 1 --starting-couples 2 --min-detailed-cap 10 --max-detailed-cap 20
    --output temp\mixed_mode_replicates_smoke.tsv`, which wrote two replicate
    rows with seeds `15000` and `15001`, plus a summary with
    `scenario_count=2` and `distinct_seed_count=2`;
  - bundled-Python promotion-reason smoke:
    `utils\run_mixed_mode_calibration.py --targets 1000 --replicates 2
    --years 1 --starting-couples 2 --min-detailed-cap 10 --max-detailed-cap 20
    --output temp\mixed_mode_reason_smoke.tsv`, which wrote the two
    promotion-reason artifacts and summarized `settlement_detail_floor` with
    `detailed_people=95`, `high_variance_detail_people=95`, and
    `average_detail_variance_score=0.687610`;
  - bundled-Python promotion-reason status smoke:
    `utils\run_mixed_mode_calibration.py --targets 1000 --replicates 2
    --years 1 --starting-couples 2 --min-detailed-cap 10 --max-detailed-cap 20
    --output temp\mixed_mode_reason_status_smoke.tsv`, which summarized
    `settlement_detail_floor` as selection profile `officeholder`, expected
    variance band `0.454400..0.734400`, and `within_profile_band`;
  - bundled-Python import check:
    `python -c "import utils.run_mixed_mode_calibration as m; print('import ok')"`;
  - `python -m unittest unit_test.test_mixed_mode_calibration`;
  - `python -m py_compile utils\run_mixed_mode_calibration.py
    unit_test\test_mixed_mode_calibration.py`;
  - `python -m unittest unit_test.test_event_history_report
    unit_test.test_mixed_mode_calibration`;
  - `python -m py_compile library\event_history_report.py
    utils\run_mixed_mode_calibration.py unit_test\test_event_history_report.py
    unit_test\test_mixed_mode_calibration.py`;
  - bundled-Python serial-emergence smoke:
    `utils\run_mixed_mode_calibration.py --targets 1000 --replicates 1
    --years 1 --starting-couples 2 --min-detailed-cap 10 --max-detailed-cap 20
    --output temp\mixed_mode_serial_emergence_smoke.tsv`, whose summary wrote
    `total_serial_murder_killers_3plus=0`,
    `serial_murder_emergence_min_murder_sample=500`,
    `serial_murder_emergence_sample_remaining=500`,
    `serial_murder_emergence_sample_ready=no`, and
    `serial_murder_emergence_status=insufficient_emergence_sample`;
  - bundled-Python sample-readiness smoke:
    `utils\run_mixed_mode_calibration.py --targets 1000 --replicates 1
    --years 1 --starting-couples 2 --min-detailed-cap 10 --max-detailed-cap 20
    --output temp\mixed_mode_sample_readiness_smoke.tsv`, which wrote summary
    rows with `murder_rate_murder_sample_remaining=10`,
    `murder_rate_sample_ready=no`, `serial_murder_sample_remaining=100`, and
    `serial_murder_sample_ready=no` for a zero-murder tiny run;
  - bundled-Python stop-status smoke:
    `utils\run_mixed_mode_calibration.py --targets 1000 --replicates 3
    --years 1 --starting-couples 2 --min-detailed-cap 10 --max-detailed-cap 20
    --stop-when-hybrid-status needs_more_murder_sample --output
    temp\mixed_mode_stop_status_smoke.tsv`, which stopped after one scenario,
    printed `stopping_early=yes`, and still wrote the normal row, summary, and
    promotion-reason artifacts before the temporary files were removed;
  - bundled-Python resume smoke:
    first ran `utils\run_mixed_mode_calibration.py --targets 1000 --replicates
    1 --years 1 --starting-couples 2 --min-detailed-cap 10 --max-detailed-cap
    20 --output temp\mixed_mode_resume_smoke.tsv`, then reran with
    `--replicates 2 --resume-existing` against the same output path; the second
    run skipped scenario 0, added seed `15001`, rewrote the summary with
    `scenario_count=2` and `distinct_seed_count=2`, then the temporary files
    were removed;
  - bundled-Python plan-aware resume smoke:
    repeated the one-then-two-replicate resume check after tightening resume
    matching, confirming scenario 0 was skipped only when its target, replicate,
    population, and seed matched the current plan, and the resumed summary again
    wrote `scenario_count=2` and `distinct_seed_count=2` before temporary files
    were removed;
  - bundled-Python sample-threshold smoke:
    `utils\run_mixed_mode_calibration.py --targets 1000 --replicates 3
    --years 1 --starting-couples 2 --min-detailed-cap 10 --max-detailed-cap 20
    --stop-after-detailed-person-years 1 --output
    temp\mixed_mode_sample_stop_smoke.tsv`, which stopped after one scenario,
    printed `matched_sample_threshold=yes`, and wrote summary rows
    `scenario_count=1`, `total_detailed_person_years=52`, and
    `hybrid_calibration_status=needs_more_murder_sample` before the temporary
    files were removed;
  - bundled-Python next-run-hint smoke:
    `utils\run_mixed_mode_calibration.py --targets 1000 --replicates 1
    --years 1 --starting-couples 2 --min-detailed-cap 10 --max-detailed-cap 20
    --output temp\mixed_mode_hint_smoke.tsv`, whose summary wrote
    `recommended_next_calibration_reason=reach_murder_rate_sample`,
    `recommended_next_calibration_stop_flag=--stop-after-total-murders`,
    `recommended_next_calibration_stop_value=10`, and
    `recommended_next_calibration_resume_flag=--resume-existing` before the
    temporary files were removed;
  - attempted representative bundled-Python rate-gate probe:
    `utils\run_mixed_mode_calibration.py --targets 50000 --replicates 6
    --years 50 --starting-couples 20 --detailed-fraction 0.05
    --min-detailed-cap 1000 --max-detailed-cap 2500
    --stop-after-total-murders 10 --output
    temp\mixed_mode_rate_gate_probe.tsv`, which timed out at 300 seconds before
    producing calibration artifacts; no `mixed_mode_rate_gate_probe*` files were
    left in `temp`;
  - retained representative bundled-Python rate-gate chunk:
    `utils\run_mixed_mode_calibration.py --targets 50000 --replicates 1
    --years 50 --starting-couples 20 --detailed-fraction 0.05
    --min-detailed-cap 1000 --max-detailed-cap 2500
    --stop-after-total-murders 10 --write-incremental --output
    temp\mixed_mode_rate_gate_chunk.tsv`, which completed one scenario in about
    155 seconds and wrote retained artifacts:
    `temp/mixed_mode_rate_gate_chunk.tsv`,
    `temp/mixed_mode_rate_gate_chunk.summary.tsv`,
    `temp/mixed_mode_rate_gate_chunk.promotion_reasons.tsv`, and
    `temp/mixed_mode_rate_gate_chunk.promotion_reason_summary.tsv`. The summary
    reports `total_detailed_person_years=18947`, `total_murder_events=7`,
    `murder_per_10k_detailed_person_years=3.694516`,
    `murder_rate_calibration_status=insufficient_murder_sample`,
    `total_serial_predator_profile_people=1`,
    `serial_predator_profile_share_of_scored_detailed=0.000797`,
    `serial_predator_profile_calibration_status=serial_predator_profiles_present`,
    `serial_murder_sample_remaining=93`,
    `serial_murder_emergence_sample_remaining=493`, and
    `hybrid_calibration_status=needs_more_murder_sample`;
  - resumed retained rate-gate chunk:
    `utils\run_mixed_mode_calibration.py --targets 50000 --replicates 2
    --years 50 --starting-couples 20 --detailed-fraction 0.05
    --min-detailed-cap 1000 --max-detailed-cap 2500
    --stop-after-total-murders 10 --write-incremental --resume-existing
    --output temp\mixed_mode_rate_gate_chunk.tsv`, which skipped the existing
    seed `15000`, added seed `15001`, stopped at the sample threshold, and
    rewrote the retained artifacts. The aggregate summary now reports
    `scenario_count=2`, `distinct_seed_count=2`,
    `total_detailed_person_years=34998`, `total_murder_events=16`,
    `murder_per_10k_detailed_person_years=4.571690`,
    `murder_rate_target_ratio=1.142922`,
    `murder_rate_calibration_status=within_target_band`,
    `total_serial_predator_profile_people=8`,
    `serial_predator_profile_share_of_scored_detailed=0.003400`,
    `serial_predator_profile_calibration_status=serial_predator_profiles_present`,
    `total_serial_predator_candidate_events=1`,
    `serial_murder_sample_remaining=84`,
    `serial_murder_sample_projected_additional_scenarios=11`,
    `serial_murder_emergence_sample_remaining=484`, and
    `hybrid_calibration_status=needs_more_serial_guardrail_sample`;
  - bundled-Python incremental-output smoke:
    `utils\run_mixed_mode_calibration.py --targets 1000 --replicates 1
    --years 1 --starting-couples 2 --min-detailed-cap 10 --max-detailed-cap 20
    --write-incremental --output temp\mixed_mode_incremental_smoke.tsv`, which
    wrote all four calibration artifacts with `scenario_count=1`,
    `hybrid_calibration_status=needs_more_murder_sample`, and the expected
    `recommended_next_calibration_*` fields before temporary files were removed;
  - `python -m unittest unit_test.test_mixed_mode_calibration`;
  - `python -m py_compile utils\run_mixed_mode_calibration.py
    unit_test\test_mixed_mode_calibration.py`;
  - bundled-Python overall-status smoke:
    `utils\run_mixed_mode_calibration.py --targets 1000 --replicates 1
    --years 1 --starting-couples 2 --min-detailed-cap 10 --max-detailed-cap 20
    --output temp\mixed_mode_overall_status_smoke.tsv`, which printed
    `summary_hybrid_status=needs_more_murder_sample` and wrote summary rows
    `hybrid_calibration_ready=no` and
    `hybrid_calibration_status=needs_more_murder_sample` for the expected
    zero-murder tiny run;
  - bundled-Python smoke:
    `utils\run_mixed_mode_calibration.py --targets 1000 --years 1
    --starting-couples 1 --min-detailed-cap 5 --max-detailed-cap 10 --output
    temp\mixed_mode_calibration_smoke.tsv`, which wrote
    `genome_scored_detailed_people=52` and
    `average_detail_variance_score=0.684908` for the tiny smoke run, with
    `serial_murder_calibration_status=insufficient_murder_sample`.
- Medium-run evidence before adding the target-status columns:
  - 20-year / 10,000 target / 5 starting couples run with corrected denominator
    produced 84 detailed alive, 10,724 aggregate alive, 2 murders,
    0 serial candidates, `event_year_span=20`,
    `murder_per_10k_detailed_person_years=11.904762`, and
    `serial_murder_calibration_status=insufficient_murder_sample`;
  - 3-seed replicate of that scenario produced murder-rate rows of
    `11.904762`, `5.555556`, and `0.000000`, showing the sample is still too
    small/noisy for a hard murder-rate retune.
  - After adding target/status columns and the Shapely import guard, the same
    20-year / 10,000 target scenario wrote
    `murder_per_10k_detailed_person_years=10.869565`,
    `murder_target_per_10k_per_year=4.000000`,
    `murder_rate_target_ratio=2.717391`, and
    `murder_rate_calibration_status=insufficient_murder_sample` because only
    2 murders were observed.

## Non-Detailed City-Directory Population Slice

- Added the first SQLite-backed city-directory model for non-detailed people:
  - save schema v23 creates `simulation_people_nondetailed` plus readable view
    and indexes for alive/age/place/job/partner-style queries;
  - rows store narrow life/place/family/job-family facts without genome,
    detailed job title, biography, event prose, or full `Person` payloads;
  - `is_partnered` is tracked separately from exact `partner_person_id` so most
    rows can support births without expensive full partner matching.
- Added `library.nondetailed_population` with set-based annual operations:
  - deterministic deaths, job-family assignment, bounded partnered-state
    updates, births/newborn inserts, parent child-count updates, grouped
    settlement job counts, and active-settlement seeding.
- Wired the new directory into runtime surfaces:
  - `SimulationContext` mixed population counts and settlement resident counts
    include living non-detailed rows;
  - yearly summaries now include non-detailed alive/birth/death counts;
  - Gradio place stats include non-detailed population and job-family buckets;
  - `promote_nondetailed_person(...)` materializes a directory row into a
    detailed person while preserving known birth/place/family/job anchors.
- Added a runner switch and benchmark utility:
  - `utils/run_population_simulation.py --use-nondetailed-directory`;
  - `utils/bench_nondetailed_directory.py` records insert/tick/group timings to
    `temp/nondetailed_directory_bench.tsv`.
- Validation:
  - bundled-Python `py_compile` for the new module, runner, save/context,
    browser, benchmark, and tests;
  - `python -m unittest unit_test.test_nondetailed_population
    unit_test.test_save_checkpoint.TestSaveCheckpoint.test_passive_people_checkpoint_roundtrip`;
  - benchmark smoke with 20,000 total / 5,000 living directory rows;
  - maintained 2,000,000 total / 500,000 living benchmark completed with
    insert 10.844s, annual SQL tick 1.180s, grouped job counts 0.023s;
  - one-year population smoke using `--use-nondetailed-directory`, producing
    mixed yearly-summary counts with 50 living non-detailed rows at scale 0.05.

## Non-Detailed Economy And Migration V1

- Added aggregate job-family economy effects from
  `simulation_people_nondetailed`:
  - grouped settlement job-family counts now affect food pressure, prosperity
    pool, market pull, and stability;
  - food-worker shortages and military-heavy labor mixes raise pressure;
  - craft/trade surplus lifts market/prosperity; care/admin/religious support
    improves local stability;
  - effects record `nondetailed_job_family_economy_effect` aggregate events.
- Added bounded set-based migration for non-detailed directory rows:
  - strained source settlements move adult directory rows toward existing
    attractive settlements, considering food pressure, prosperity, regional
    pressure, routes, market pull, stability, and resident mass;
  - migration updates rows in SQL without creating detailed move events per
    person;
  - aggregate moves record `nondetailed_settlement_migration` events.
- Wired the v1 effects into annual simulation:
  - non-detailed migration runs during the migration tick before cap drift;
  - non-detailed job-family effects run during the economy tick after detailed
    worker-market effects.
- Validation:
  - `python -m py_compile library/nondetailed_population.py
    library/simulation_economy.py library/simulation_migration.py
    library/population_growth_runner.py library/simulation_context.py
    utils/run_population_simulation.py unit_test/test_nondetailed_population.py`;
  - `python -m unittest unit_test.test_nondetailed_population`;
  - two-year population smoke using `--use-nondetailed-directory`, producing
    55 living directory rows plus 2 `nondetailed_job_family_economy_effect`
    events and 1 `nondetailed_settlement_migration` event.

## Road Overlay Straight-Line Cleanup

- Reduced visibly straight settlement-road overlays by naturalizing long road
  chords into deterministic land-safe bow waypoints before SVG rendering:
  - direct roads and long subsegments inside otherwise-routed roads now get
    additional intermediate points when the bend stays on land;
  - ford-aware channel checks prevent decorative bends from wandering through
    unbridged river channels;
  - route-choice accounting keeps using the underlying routing length so visual
    waypoints do not make ford detours or network reuse look more expensive.
- Added regression coverage for a long direct road inside a land cell, asserting
  that no single segment dominates the visible road shape.
- Default-world debug export comparison after the fix:
  - long roads (`length >= 0.04`) with `max_segment_fraction >= 0.50` dropped
    from 19 to 0;
  - long roads with `directness_ratio <= 1.12` dropped from 13 to 0;
  - remaining two-point or dominant-segment roads are short local connectors
    under `0.04` world units.
- Validation:
  - `python -m py_compile library\world_map_roads.py
    unit_test\test_world_map_roads.py`;
  - `python -m unittest unit_test.test_world_map_roads`;
  - `python utils\util_export_world_map_svg.py --world-id default --output
    temp\road_debug.svg --debug-output temp\road_debug.json`.

## Gradio Event Card And Relationship History Bugfix Pass

- Closed the current Gradio-specific TODO bugfixes:
  - event-card links now override the broad Gradio anchor padding inside
    cards/details, keeping person links from looking like padded controls;
  - browser event cards keep concise human-visible sentences while moving
    code-like values into expandable Details drawers for job assignment/loss,
    status and patronage changes, elite household investment, childcare
    shortfall, prosperity crisis, property crime, murder, outlawry, and career
    fitness update rows;
  - childcare shortfall run-away rows now read as the child running away, with
    supply/shortfall/outcome fields in Details;
  - household prosperity crisis rows hide savings and member lists in Details,
    and member lists use readable full names;
  - career-fitness update rows hide scores and high-deviation trait lists in
    Details;
  - Partner and Paramour histories are now rendered in one Relationship History
    timeline with distinct partner and paramour bar colors plus a legend, and
    share text uses the same combined section.
- Added focused Gradio regression coverage for the CSS override, event-card
  detail drawers, household examples, career-fitness rows, job-loss nuance,
  combined relationship history, and the Outlawry event-card overlap.
- Validation:
  - `python -m py_compile utils/gradio_data_browser.py
    unit_test/test_gradio_data_browser.py`;
  - `python -m unittest unit_test.test_gradio_data_browser`;
  - `python -m unittest
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_job_event_fitness_uses_event_payload_not_current_person_score
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_property_crime_event_cards_use_payload_details
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_sheet_has_combined_relationship_history_section
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_history_timeline_css_avoids_overflowing_lifespan_grid
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_career_fitness_update_uses_event_payload
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_job_market_churn_event_uses_recorded_fit_nuance
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_events_match_household_array_payloads
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_events_match_household_prosperity_payloads
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_outlawry_uses_refuge_display_names
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_outlaw_custody_surfaces_in_person_views`.
- Removed the completed `Gradio Bugfixes` section from `TODO.md`.

## Outlawry And Refuge Bugfix Pass

- Closed the current Outlawry-specific TODO bugfixes:
  - partner murder now explicitly closes killer-victim partner/paramour ties in
    murder consequences before death cleanup, so stale partner relationships do
    not survive murder deaths;
  - outlaw flight still strips normal work, service, and residence state, but
    official partners now break only through a deterministic spouse-trait check
    keyed to loyalty strain and neurochemical confusion; paramours still end at
    flight;
  - imprisoned outlaws are alive and persisted, but excluded from ordinary
    settlement/social/household/economy/incident participation until release,
    escape, or death;
  - custody now has annual release, escape, and death-in-custody outcomes, with
    release returning the person to ordinary unemployed life and escape
    reopening fugitive outlawry;
  - outlaw case places now prefer the victim/target location for display while
    preserving accused-location law-profile tuning;
  - old and new outlaw raid/custody payloads resolve `near_settlement_id` and
    custody sites as readable places instead of "an unrecorded place";
  - Gradio Outlawry person cards now show a concise human sentence with
    code-like status/severity/custody/place fields in an expandable Details
    drawer.
- Added focused regression coverage:
  - `unit_test.test_simulation_outlaws` for flight partner retention/breakup,
    custody absence/release, and custody death/escape outcomes;
  - `unit_test.test_simulation_incidents` for partner-murder relationship
    closure;
  - `unit_test.test_gradio_data_browser` for Outlawry detail drawers and
    readable raid places.
- Validation:
  - `python -m py_compile library/simulation_outlaws.py
    library/simulation_context.py library/simulation_incidents.py
    library/simulation_social.py library/simulation_household_care.py
    library/simulation_economy.py library/event_prose.py
    utils/gradio_data_browser.py unit_test/test_simulation_outlaws.py
    unit_test/test_simulation_incidents.py unit_test/test_gradio_data_browser.py`;
  - `python -m unittest unit_test.test_simulation_outlaws`;
  - `python -m unittest unit_test.test_simulation_incidents`;
  - `python -m unittest
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_outlaw_browser_loads_cases_refuges_and_person_selection
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_outlawry_uses_refuge_display_names
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_outlaw_custody_surfaces_in_person_views`;
  - `python -m unittest unit_test.test_simulation_social_breakups
    unit_test.test_relationships_residence`;
  - `python -m unittest unit_test.test_simulation_household_care`;
  - `python -m unittest unit_test.test_simulation_economy`.
- Removed the completed Outlawry-specific items from `TODO.md`; the separate
  Gradio cleanup is now recorded in the pass above.

## Outlawry And Refuge V1

- Completed staged implementation of outlaws as simulation state, not a job
  title:
  - `library.simulation_outlaws` adds runtime `SimulationOutlawCase` and
    `SimulationOutlawRefuge` dataclasses plus helpers for wanted-case creation,
    flight, refuge selection, raids, pursuit, buy-off, capture, death, and
    return/forgetting.
  - `Person` now carries current outlaw state:
    `outlaw_status`, `outlaw_case_key`, `outlaw_refuge_id`,
    `outlaw_since_year`, and `last_free_settlement_id`.
- Completed Stage 1, persistence and case opening:
  - bumped save schema to v20;
  - added `simulation_outlaw_cases` and `simulation_outlaw_refuges` plus
    readable views;
  - wired schema ensure, clear/reset, full and meta-only checkpoint sync, and
    resume hydration;
  - murder and serious property-crime consequences now open wanted cases with
    severity, knownness, pursuit pressure, and buy-off power.
- Completed Stage 2, flight and refuge life:
  - wanted people can flee to non-settlement `outlaw_refuge` records;
  - flight dissolves official partnerships, ends paramour contact, clears normal
    settlement residence, work, household-care, and service attachment state;
  - fugitive outlaws are excluded from normal current-settlement grouping and
    the shared career/household-care residence helper;
  - refuges can generate survival-crime `outlaw_raid` events.
- Completed Stage 3, pursuit, buy-off, and return:
  - annual outlaw processing runs after incidents and before innovation and
    government;
  - status, prosperity, office, and patronage contribute to buy-off power, with
    hard limits for severe public murder;
  - discovery/pursuit can capture, kill, or leave a fugitive escaped;
  - long-fading cases can resolve as forgotten/returned, while capture returns
    the person punished and stigmatized.
- Completed Stage 4, visibility and polish:
  - added event catalog and ontology rows for `outlaw_case_opened`,
    `outlaw_flight`, `outlaw_refuge_joined`, `outlaw_raid`,
    `outlaw_pursuit`, `outlaw_captured`, `outlaw_killed`,
    `outlaw_bought_off`, `outlaw_returned`, and `outlaw_forgotten`;
  - event-history reporting now includes outlaw cases/refuges and metrics;
  - Gradio person sheets and share text include Outlawry summaries without
    autoloading heavy grids;
  - readable event sentences cover the new outlaw events.
- Added focused deterministic coverage in `unit_test.test_simulation_outlaws`
  for case creation, buy-off limits, flight/refuge selection, relationship
  cutoff, death/capture/return resolution, and checkpoint/readable-view
  round-trip.
- Verified with:
  - `python -m py_compile library\person.py library\simulation_context.py
    library\simulation_incidents.py library\simulation_careers.py
    library\simulation_outlaws.py library\world_save.py
    library\event_history_report.py utils\gradio_data_browser.py`;
  - `python -m unittest unit_test.test_simulation_outlaws`;
  - `python -m unittest unit_test.test_simulation_incidents`;
  - `python -m unittest unit_test.test_event_catalog
    unit_test.test_event_ontology unit_test.test_event_history_report`;
  - `python -m unittest unit_test.test_event_prose`;
  - `python -m unittest unit_test.test_save_checkpoint`;
  - `python -m unittest unit_test.test_gradio_data_browser` (passed with an
    existing ResourceWarning about an unclosed SQLite connection).
- The full combined target command was also attempted, but it exceeded the
  5-minute command timeout before producing output; the same test modules passed
  when run in smaller batches.

## Outlawry Follow-Up: Career Exclusion And Browser Discovery

- Closed the first post-V1 outlaw issues found in live browsing:
  - fugitive, wanted, and punished outlaws now block ordinary career assignment,
    household/service placement, vice-work assignment, and job-seeker migration;
  - stale saves or edge paths that leave an outlaw with a job are normalized
    back to outlaw/unemployed labor state before the career tick can act;
  - flight and capture/return resolution now clear job era/tier/assignment,
    host, and employer state more completely.
- Gradio now exposes outlaws directly:
  - added an `Outlaws` tab with searchable case and refuge grids;
  - selecting an outlaw case opens the accused person's sheet/share text;
  - selecting a refuge opens a refuge detail sheet with cases and recent outlaw
    events.
- Outlaw refuges now appear in settlement/town browsing as settlement-like
  refuge places while remaining non-hamlet simulation objects.
- Added regression coverage in:
  - `unit_test.test_simulation_outlaws` for stale outlaw job cleanup and career
    exclusion after flight/capture;
  - `unit_test.test_gradio_data_browser` for the Outlaws tab loaders/selectors
    and refuge rows in settlement/town browsers.
- Verified with:
  - `python -m py_compile library/simulation_outlaws.py
    library/simulation_careers.py utils/gradio_data_browser.py
    unit_test/test_simulation_outlaws.py unit_test/test_gradio_data_browser.py`;
  - `python -m unittest unit_test.test_simulation_outlaws
    unit_test.test_gradio_data_browser` (passed with an existing ResourceWarning
    about an unclosed SQLite connection in browser test support);
  - `python utils/util_load_config.py --world default`;
  - `python -m unittest unit_test.test_event_catalog
    unit_test.test_event_ontology unit_test.test_event_history_report`;
  - `python -m unittest unit_test.test_simulation_incidents`;
  - `python -m unittest unit_test.test_save_checkpoint`;
  - bundled workspace Python:
    `from utils.gradio_data_browser import build_app; app=build_app('default')`
    registered 87 Gradio functions.
- The combined verification command covering those same test modules was
  attempted first and timed out after 5 minutes; the modules passed when split
  into smaller runs.

## Outlawry Follow-Up: Refuge Display Names

- Closed the raw-ID leakage visible in Outlawry person summaries and refuge
  browsing:
  - `SimulationOutlawRefuge` now carries a persisted `display_name`;
  - new refuges receive deterministic colorful names such as "The Blackthorn
    Crag" instead of exposing `outlaw_refuge:<region>:<ordinal>` keys;
  - outlaw event payloads include the refuge display name when available;
  - Gradio case tables, refuge tables, settlement/town refuge rows, person
    Outlawry details, current Outlaw Refuge cards, share text, and event prose
    resolve refuge/place labels through readable names.
- Bumped save schema to v21 and added `display_name` to
  `simulation_outlaw_refuges`, `simulation_outlaw_refuges_readable`, and
  `simulation_outlaw_cases_readable`.
- Legacy-safe behavior remains:
  - existing refuge rows without `display_name` get deterministic fallback names
    from their refuge/region/place/year data;
  - minimal browser fixtures without settlement/region tables still render
    place tails without crashing.
- Added regression coverage in:
  - `unit_test.test_simulation_outlaws` for refuge names at flight, readable
    views, and checkpoint hydration;
  - `unit_test.test_gradio_data_browser` for named refuges in settlement/town
    browsers, Outlaws tab tables/sheets, and person Outlawry/share text.
- Verified with:
  - `python -m py_compile library/simulation_outlaws.py library/world_save.py
    utils/gradio_data_browser.py unit_test/test_simulation_outlaws.py
    unit_test/test_gradio_data_browser.py`;
  - `python -m unittest unit_test.test_simulation_outlaws
    unit_test.test_gradio_data_browser` (passed with an existing ResourceWarning
    about an unclosed SQLite connection in browser test support);
  - `python -m unittest unit_test.test_save_checkpoint`;
  - `python utils/util_load_config.py --world default`.

## Outlawry Follow-Up: Richer Refuge Maps

- Closed the map/debug/browser visibility TODO for named outlaw refuges:
  - `library.world_map_svg` now has `OutlawRefugeMapOverlay` rows loaded from
    `simulation_outlaw_refuges_readable` when available;
  - refuge markers are projected near their recorded settlement when possible,
    with deterministic in-region fallback placement;
  - generated SVG maps render clickable `outlaw-refuge` markers and labels
    using display names such as "The Blackthorn Crag" instead of visible raw
    refuge IDs;
  - Gradio world-map clicks open the existing outlaw-refuge detail sheet via
    an `Outlaw Refuges` selection payload;
  - overlay debug JSON now includes outlaw-refuge counts, rows, display names,
    nearby settlement IDs/names, map coordinates, and distance-to-near-settlement
    diagnostics.
- Added focused regression coverage in:
  - `unit_test.test_world_map_geometry` for loading/rendering outlaw-refuge
    overlays and debug-context rows;
  - `unit_test.test_gradio_data_browser` for world-map refuge markers and
    selection-detail routing.
- Verified on the lower-powered PC with:
  - `python -m py_compile library\world_map_svg.py utils\gradio_data_browser.py
    unit_test\test_world_map_geometry.py unit_test\test_gradio_data_browser.py`;
  - `python -m unittest
    unit_test.test_world_map_geometry.TestWorldMapGeometry.test_world_map_overlays_render_outlaw_refuges_with_debug_context`;
  - `python -m unittest
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_world_map_html_renders_outlaw_refuges_and_selection_detail`.

## Outlawry Follow-Up: Prison Systems

- Closed the durable custody/imprisonment TODO for captured or punished outlaws:
  - `SimulationOutlawCustody` now records case, person, status, custody site,
    start year, expected release year, release year, severity, and details;
  - captured/punished resolutions create custody rows, mark the person
    `imprisoned`, clear normal work, and move labor/housing state to custody;
  - returned/forgotten/bought-off paths clear person custody fields so custody
    remains distinct from fugitive, returned, forgotten, bought-off, and killed
    outcomes.
- Save and readable surfaces now persist and reload custody state:
  - save schema v22 adds `simulation_outlaw_custodies`, `custody_id` on outlaw
    cases, additive person custody checkpoint columns, and readable custody
    columns/views;
  - checkpoint load restores active/recent custody rows into
    `ctx.outlaw_custodies` alongside cases and refuges;
  - event-history reports include outlaw custody count and severity metrics.
- Browser/person surfaces now show custody without leaking raw ids:
  - Gradio person sheets/share text show current Outlaw Custody;
  - outlaw case tables/details include custody status/site/expected release;
  - `outlaw_captured` event prose says captured into custody and includes the
    expected release year when available.
- Added focused regression coverage in:
  - `unit_test.test_simulation_outlaws` for capture labor state and custody
    save/load/readable-view round-trip;
  - `unit_test.test_gradio_data_browser` for custody in person sheet/share text,
    case details, and event prose.
- Verified on the lower-powered PC with:
  - `python -m py_compile library\simulation_outlaws.py library\person.py
    library\simulation_context.py library\world_save.py
    library\event_history_report.py utils\gradio_data_browser.py
    unit_test\test_simulation_outlaws.py unit_test\test_gradio_data_browser.py`;
  - `python -m unittest -v
    unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_captured_outlaw_custody_checkpoint_roundtrip_and_readable_views`;
  - `python -m unittest -v
    unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_capture_death_and_forgotten_return_resolve_cases`;
  - `python -m unittest -v
    unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_outlaws_do_not_keep_or_receive_normal_jobs`;
  - `python -m unittest -v
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_outlaw_custody_surfaces_in_person_views`;
  - `python -m unittest -v
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_outlawry_uses_refuge_display_names`;
  - `python -m unittest -v
    unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_outlaw_browser_loads_cases_refuges_and_person_selection`;
  - `python utils\util_load_config.py --world default`;
  - `python -m unittest -v unit_test.test_event_catalog
    unit_test.test_event_ontology`;
  - `python -m unittest -v unit_test.test_event_history_report`;
  - `git diff --check`.
- The full `unit_test.test_simulation_outlaws` module was attempted first on
  this laptop and exceeded the 3-minute timeout before output; the touched
  behaviors passed when run as focused tests.

## Outlawry Follow-Up: Passive-Person Promotion

- Closed the passive/cohort outlaw-entry TODO:
  - `library.simulation_outlaws.open_outlaw_case_from_passive(...)` now
    promotes an existing passive person or a latest-cohort adult before opening
    a normal outlaw case;
  - `promote_passive_outlaw_accused(...)` records promotion reason
    `outlaw_case_accused`, selector/provenance source metadata, and reuses the
    existing passive-promotion event/backfill/log machinery;
  - promoted accused people receive normal wanted-case state and can continue
    through existing refuge flight, capture, custody, and event flows.
- Added module-map guidance for the new outlaw helper.
- Added focused regression coverage in `unit_test.test_simulation_outlaws`:
  - explicit passive-person promotion preserves name/family backfill provenance,
    opens an outlaw case, flees to refuge, and can be captured into custody;
  - aggregate passive-cohort promotion decrements the cohort, records source
    cohort metadata, opens an outlaw case, and can flee to refuge.
- Verified on the lower-powered PC with:
  - `python -m py_compile library\simulation_outlaws.py
    unit_test\test_simulation_outlaws.py`;
  - `python -m unittest -v
    unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_passive_person_can_be_promoted_into_outlaw_case_and_flow`;
  - `python -m unittest -v
    unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_passive_cohort_can_be_promoted_into_outlaw_case`;
  - `git diff --check`.

## Outlawry Follow-Up: Law-Code Variation By Polity

- Closed the polity/legal-profile outlaw handling TODO:
  - `OutlawLawProfile` now defines customary, strict-justice, and
    lenient-compromise law postures for outlaw handling;
  - settlement, region, or regional-member polities can supply
    `notes["outlaw_law_profile"]`, `notes["law_code_profile"]`, or
    `notes["law_profile"]`; otherwise broad polity type falls back to a default
    law profile;
  - case details and event payloads now carry law profile/source/polity context
    for readable analysis.
- Law profiles now affect the actual outlaw flow:
  - case severity, pursuit pressure, and expected forgetting horizon at case
    creation;
  - buy-off threshold and buy-off chance;
  - fugitive flee, discovery, capture, death, and return chances in the annual
    outlaw tick;
  - custody/imprisonment duration for captured or punished outlaws;
  - passive/cohort property-crime entry thresholds, so the early promotion gate
    matches the local legal severity profile.
- Added focused deterministic coverage in `unit_test.test_simulation_outlaws`:
  - strict versus lenient settlement polities produce distinct severity,
    pursuit pressure, and expected forget years for the same property crime;
  - strict law blocks a borderline buy-off that lenient law permits, and strict
    custody runs longer than lenient custody for the same base offense.
- Verified on the lower-powered PC with:
  - `python -m py_compile library\simulation_outlaws.py
    unit_test\test_simulation_outlaws.py`;
  - `python -m unittest
    unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_polity_law_profiles_change_case_tuning
    unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_polity_law_profiles_change_buyoff_and_punishment`;
  - `python -m unittest
    unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_polity_law_profiles_change_case_tuning
    unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_polity_law_profiles_change_buyoff_and_punishment
    unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_buyoff_has_hard_limit_for_severe_public_murder`;
  - `git diff --check` (passed with existing LF-to-CRLF working-copy
    warnings).
- The full `unit_test.test_simulation_outlaws` module was attempted first on
  this laptop and exceeded the 2-minute timeout before output; the touched
  behaviors passed when run as focused tests.

## Outlawry Follow-Up: Measured Outcome Reporting

- Closed the measured tuning/reporting TODO by adding an explicit report before
  changing any rates:
  - the current default save report showed 21 `property_crime` events and 5
    `murder` events, but 0 outlaw case/opening lifecycle events;
  - that made the concrete gap reporting visibility, not a defensible rate
    tuning target from this older/no-outlaw save.
- `library.event_history_report` now builds an `OutlawOutcomeSummary`:
  - source-crime count and opened-case conversion rate;
  - lifecycle event rows for case opened, flight, refuge join, raid, pursuit,
    capture, killed, bought off, returned, and forgotten;
  - active/resolved case counts and resolution breakdowns when outlaw case
    readable views exist;
  - per-offense opened-case rates for murder and property crime;
  - average years to resolution, expected forgetting horizon, active refuges,
    active custodies, and custody duration when those readable views exist.
- `write_event_history_report(...)` now writes
  `outlaw_outcome_summary.tsv`, and `format_event_history_summary(...)` includes
  an "Outlaw Outcome Summary" block.
- Added focused regression coverage in `unit_test.test_event_history_report`:
  - a synthetic two-crime/two-case lifecycle proves conversion, flight,
    capture, refuge, custody, and duration summary rows;
  - report writing now asserts the new TSV artifact exists and includes source
    crime rows.
- Verified on the lower-powered PC with:
  - `python -m py_compile library\event_history_report.py
    unit_test\test_event_history_report.py`;
  - `python -m unittest
    unit_test.test_event_history_report.TestEventHistoryReport.test_outlaw_outcome_summary_tracks_conversion_and_lifecycle`;
  - `python -m unittest
    unit_test.test_event_history_report.TestEventHistoryReport.test_outlaw_outcome_summary_tracks_conversion_and_lifecycle
    unit_test.test_event_history_report.TestEventHistoryReport.test_write_report_outputs_tsv_artifacts`;
  - `python -m unittest unit_test.test_event_history_report`;
  - `python utils\util_event_history_report.py --world default --sample-limit 0
    --output-dir temp\event_history_report\outlaw_after`, which reported
    `source_crimes: count=26` and `opened_cases: count=0 denominator=26
    rate=0.0000`;
  - `git diff --check` (passed with existing LF-to-CRLF working-copy
    warnings).

## Placename Length And Visible `-by` Tuning

- Added settlement display-length budgeting in `library.placenames_generation`:
  - generated settlement candidates now prefer routine labels at 9 letters or
    less, keep the 90th-percentile target at 12 letters, and retain a hard cap
    target of 16 letters;
  - patronymic generation samples short covered-culture first-name stems before
    accepting a long stem;
  - dual-affix and patronymic candidates are ranked so the generator keeps the
    shortest acceptable display candidate instead of accepting the first long
    output.
- Removed the code-side fabricated locative compound suffixes:
  - `havenby`, `fordby`, and `wellby` are no longer synthesized by
    `_locative_settlement_display`;
  - locative display now only tries simple `by`;
  - the simple `by` suffix is skipped when the base already ends in `by`,
    `byr`, `haven`, `ford`, or `well`, or when the visible result would exceed
    the display budget.
- Preserved locative detail in etymology:
  - names can still record anchors such as "by ford ..." or "by harbor ..."
    in the etymology trail;
  - visible display labels no longer need to show that anchor as a compound
    suffix.
- Added focused regression coverage in `unit_test.test_placenames`:
  - direct tests prove coast/ford/well anchors use simple `by` and never produce
    configless `havenby`, `fordby`, or `wellby`;
  - a deterministic 240-name generated settlement sample asserts median,
    90th-percentile, 95th-percentile, and maximum display lengths against the
    Domesday-style targets.
- Verified with:
  - `python -m py_compile library\placenames_generation.py
    unit_test\test_placenames.py`;
  - focused new regression/distribution tests, including the deterministic
    240-name sample with visible `by`/`bi`/`byr` rate and forbidden compound
    suffix assertions;
  - `python -m unittest unit_test.test_placenames unit_test.test_place_namer`
    (40 tests, 283.206s).
- Old save/report rows that already contain long locative display names are not
  rewritten automatically; reset or regenerate worlds when those stale labels
  should disappear.

## World Map Road Density Tuning

- Added corridor-aware suppression for minor settlement road overlays:
  - weak roads whose endpoints and path sit inside a much stronger road corridor
    are filtered before SVG rendering;
  - roads that branch away to a distinct destination remain eligible to render.
- Verified against the current default save after refreshing config:
  - current save year: 1299;
  - active settlements: 83;
  - latest-year movement rows: 2,475;
  - road overlays: 238 before -> 148 after;
  - actual-use roads: 212 before -> 132 after;
  - implied-only roads: 26 before -> 16 after;
  - sea routes stayed at 19.
- Added focused road-overlay regressions for redundant minor corridor roads and
  distinct-destination branch roads.
- Verified with `python -m py_compile library\world_map_roads.py
  unit_test\test_world_map_roads.py`, `python -m unittest
  unit_test.test_world_map_roads`, and default-world SVG/debug exports before
  and after the change.

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
  - sea-lane water spans follow configured sea-route edges as cleared open-water polylines instead of routing through land micro-polygons or SVG smoothing that can cut corners over land; short endpoint connectors still attach settlements to their coast-side route endpoints.
  - follow-up refinement: sea-lane water spans now sample against land micro-cells and use an invisible ocean navigation grid when a direct segment would cross land or when a reasonably short offshore/coast-following path is available, preserving clear routes while bending around islands/peninsulas.
  - overlong inland/river road overlays now yield to a configured sea lane when the ocean path is clearly shorter and more natural for the same settlement demand.
  - road overlays now use a crimson `#b21f3a` centerline with a pale `#fffdf3` casing, stronger actual-route opacity, and small render-time corner chamfers plus render-only tiny endpoint hook pruning so well-worn roads stay visible over terrain and black boundary linework without becoming spline-smooth or collapsing route topology.
  - sea-route overlays now render from water-side harbor points near their endpoint settlements and add small clickable harbor endpoint markers, keeping the visible route in water while making the port destination legible instead of looking like a lane terminating at nowhere on a continent.
  - river, road, and sea-route SVG paths now carry explicit `data-map-layer` metadata, with reachable hover titles on visible route strokes/fills to make ambiguous blue/route lines easier to identify in the browser.
  - the Gradio world-map click handler now treats route lines as selectable map objects and opens a route detail sheet identifying Road Route, Sea Route, or River selections with route IDs, endpoints, regions, and usage where available.
  - `utils/util_export_world_map_svg.py --debug-output ...` now writes `route_overlays` diagnostics with road/sea-route counts, route-shape summaries for sharp turns and long segments, sea-route endpoint-distance summaries, and land-crossing segment QA whenever overlays are loaded.
  - `library.world_map_svg` renders a separate `sea-route-layer` with `data-sea-route-*` attributes, while `utils.gradio_data_browser` labels the shared transport overlay toggle as `Routes`.
  - verified with `python -m py_compile utils\gradio_data_browser.py library\world_map_roads.py library\world_map_svg.py utils\util_export_world_map_svg.py unit_test\test_gradio_data_browser.py unit_test\test_world_map_roads.py unit_test\test_world_map_geometry.py`, `python -m unittest unit_test.test_world_map_roads unit_test.test_world_map_geometry.TestWorldMapGeometry.test_debug_svg_renderer_outputs_noisy_map_layers unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_world_map_html_renders_roads_and_checkbox_hides_them unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_world_map_route_selection_detail_identifies_route_layer`, focused cleanup/render regression slices, a default-world SVG export smoke whose debug JSON included `route_overlays` (`roads: 5`, `sea_routes: 0`, rendered road right-angle-like turns: 0, rendered road turns >=75 degrees: 0, raw road sharp turns reduced by 21 in rendered-shape QA), refreshed PNG visual QA for the default road/river overlay and island-blocker sea-route fixture, and synthetic rendered sea-route smokes where 1 sea-route overlay produced 27 route points, 0 land-crossing route segments, endpoints about 0.06 world units from the destination settlements, clickable harbor markers at both endpoints, and reachable SVG path metadata/title output on the visible route stroke. The current default save has no sea-route overlays, so endpoint-specific verification uses the focused synthetic fixture.

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

## 2026-06-15 Tracking-Doc Cleanup

### Fixes

- Compressed the `TODO.md` detailed-population calibration section so it keeps
  only the design intent, latest retained evidence, next continuation command,
  and remaining calibration/tuning decisions.
- Left completed implementation details in the existing `TODONE.md` completion
  entries for detailed-selection variance, serial-predator diagnostics,
  mixed-mode calibration reports, and hybrid-population support instead of
  duplicating the full history inside `TODO.md`.

## 2026-06-21 Mixed-Population Incident Calibration Fix

### Fixes

- Switched `utils/run_mixed_mode_calibration.py` scenarios to the SQLite
  non-detailed city-directory backend (`use_nondetailed_directory=True`) so
  representative calibration no longer reports zero non-detailed people while
  aggregate cohorts stand in for the background population.
- Added explicit calibration evidence columns:
  `population_backend`, `nondetailed_alive`, `nondetailed_births`,
  `nondetailed_deaths`, `mixed_person_years`,
  `murder_rate_population_basis`, and `murder_per_10k_mixed_person_years`.
  Detailed-person-year murder rates remain in the TSV as a comparison field,
  but calibration status now uses mixed population-years when available.
- Updated `--resume-existing` hygiene for mixed-mode calibration so rows without
  the current `population_backend=nondetailed_directory` marker are discarded
  before new summaries are written. This prevents older aggregate-backed rows
  from being silently mixed into full-population homicide calibration.
- Changed murder event caps, settlement trial counts, and murder chance gates to
  scale from mixed settlement population (detailed residents plus passive,
  aggregate, and non-detailed population counts) while still choosing actual
  detailed actors from the detailed sample.
- Reconnected active-settlement detailed-floor maintenance when the runner is
  using the non-detailed directory backend, preventing the detailed actor pool
  from collapsing during long directory-backed runs.
- Split full-population homicide accounting from detailed actor selection:
  detailed murder events now represent a bounded narrative sample of mixed-world
  incidence, while background non-detailed homicide rows record the remaining
  population-level murder incidence without materializing every non-detailed
  killer or victim.
- Updated hybrid calibration status so actual guarded serial emergence in a
  500+ murder sample can satisfy calibration even when the static
  serial-predator profile proxy is absent in the final scored detailed sample.

### Validation

- `python -m py_compile library\simulation_incidents.py
  utils\run_mixed_mode_calibration.py unit_test\test_mixed_mode_calibration.py
  unit_test\test_simulation_incidents.py`
- `python -m unittest unit_test.test_mixed_mode_calibration
  unit_test.test_simulation_incident_helpers`
- `python -m unittest
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_murder_population_rate_helpers_scale_above_review_sample_cap`
- `python -m unittest unit_test.test_mixed_mode_calibration
  unit_test.test_simulation_incident_helpers
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_murder_population_rate_helpers_scale_above_review_sample_cap
  unit_test.test_population_growth_determinism.TestPopulationGrowthDeterminism.test_detailed_floor_promotes_from_passive_cohorts`
- Bundled-Python smoke:
  `utils/run_mixed_mode_calibration.py --targets 1000 --replicates 1 --years 2
  --starting-couples 2 --detailed-fraction 0.01 --min-detailed-cap 10
  --max-detailed-cap 20 --write-incremental --output
  temp\mixed_mode_nondetailed_smoke.tsv`, which produced
  `population_backend=nondetailed_directory`, `nondetailed_alive=984`,
  `aggregate_cohort_alive=0`, `mixed_mode_alive=990`,
  `mixed_person_years=1971`, and
  `murder_rate_population_basis=mixed_population`.
- Bundled-Python representative probe:
  `utils/run_mixed_mode_calibration.py --targets 50000 --replicates 1
  --years 50 --starting-couples 20 --detailed-fraction 0.05
  --min-detailed-cap 1000 --max-detailed-cap 2500 --write-incremental --output
  temp\mixed_mode_nondetailed_representative_probe.tsv`, followed by
  `--resume-existing` to refresh the aggregate summary after the status-rule
  update. The final summary has `population_backend=nondetailed_directory`,
  `report_non_detailed_alive_people=41104`, `mixed_person_years=2354447`,
  944 total murder rows, `murder_per_10k_mixed_person_years=4.009434`,
  `murder_rate_calibration_status=within_target_band`,
  `serial_murder_event_share_3plus=0.009534`,
  `serial_murder_calibration_status=within_real_life_guardrail`,
  `serial_murder_emergence_status=serial_murder_emerged`, and
  `hybrid_calibration_status=within_hybrid_calibration_targets`.

## 2026-06-21 Paramour Fertility And Out-Of-Wedlock Children

### Enhancements

- Formalized main-run birth candidates as spouse or paramour relationships
  instead of anonymous partner IDs. Candidate fathers must be alive, fertile,
  male, reciprocal, and present in the active couple/paramour relationship set.
- Paramour births now produce detailed children through the normal birth
  pipeline and mark the child with `birth_relationship_type=paramour`,
  `born_out_of_wedlock=True`, and `legitimacy_status=bastard`.
- Spouse births now carry `birth_relationship_type=spouse`,
  `born_out_of_wedlock=False`, and `legitimacy_status=legitimate`, preserving
  one consistent birth-state vocabulary for future reports.
- Birth event payloads and file-store CSV rows now include the same relationship
  and legitimacy markers, while SQLite checkpoints persist the markers inside
  `person_json`.
- The browser renders out-of-wedlock birth events as such and shows a child's
  birth status in the children list.

### Validation

- `python -m py_compile library\person.py library\population_growth_runner.py
  library\simulation_context.py library\simulation_store.py
  library\world_save.py utils\gradio_data_browser.py
  unit_test\test_paramour_fertility.py unit_test\test_gradio_data_browser.py`
- `python -m unittest unit_test.test_paramour_fertility`
- `python -m unittest unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_birth_event_marks_out_of_wedlock_children`
- `python -m unittest unit_test.test_paramour_fertility
  unit_test.test_birth_surname_rule
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_birth_event_marks_out_of_wedlock_children`

## 2026-06-21 Polity Office History And Ruler Timelines

### Enhancements

- Confirmed current government office history is persisted in
  `simulation_office_holdings`: `assign_holder` appends open holdings and
  `vacate_seat` closes them with `end_sim_year` and `end_reason` values such as
  `death`, `term_expiry`, `promotion`, `polity_dissolved`, or `exile`.
- Added the durable `simulation_office_history_readable` save view, keyed by
  holding, polity, office/seat, holder, start year, end year, and end reason,
  with polity and holder labels joined in for inspection.
- Added office-history helper functions in the browser layer so older saves can
  still render office history by joining holdings/seats directly if the readable
  view is unavailable.
- Extended polity sheets with `Ruler Timeline` and `Office History` sections.
  The ruler timeline prefers the configured head title for the polity type and
  falls back to the non-settlement-scoped office seat when config is unavailable.
- Snapshot-backed place browsing now loads office history and preserves readable
  holder names even for dead former rulers who are no longer in the alive-person
  snapshot.

### Validation

- `python -m py_compile library\government_checkpoint.py
  utils\gradio_data_browser.py unit_test\test_simulation_government.py
  unit_test\test_gradio_data_browser.py`
- `python -m unittest
  unit_test.test_simulation_government.TestSimulationGovernment.test_office_history_readable_view_tracks_successions_and_death_endings
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_polity_sheet_shows_ruler_timeline_and_office_history`
- `python -m unittest unit_test.test_simulation_government`
- `python -m unittest unit_test.test_simulation_government
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_polity_sheet_shows_ruler_timeline_and_office_history`

## 2026-06-21 Genome Trait Center/Extreme Impact Module

### Enhancements

- Added `library.trait_impacts` as the shared source of truth for genome
  banding. It classifies signed trait deviations into center, mild deviation,
  ordinary, strong deviation, and extreme deviation bands while preserving the
  centered genome invariant that `0` is ideal and ordinary midpoint values near
  `+/-50` are not inherently meaningful.
- Inventoried every checked-in `config/genome.csv` trait in
  `TRAIT_IMPACT_RULES`, with center benefits, harmful extremes, useful or
  context-dependent extremes, and practical categories for mortality/health,
  work capacity, household stability, finances, violence, reputation, legal
  fallout, marriage/paramour dynamics, care burden, and social standing.
- Added reusable APIs for trait definition loading from config SQLite or CSV,
  trait-band classification, center/strong/extreme signals, missing-rule
  detection, and practical consequence profiles with category benefit/pressure
  lookup.
- Routed the event-scoring layer through the new strict band signals so
  `negative_extreme`, `positive_extreme`, and `ideal_strength` no longer treat
  missing traits or ordinary midpoint values as active signals.
- Folded practical impact pressures into instability, greed, and jealousy crime
  pressure. Greed/property-crime scoring now stays strong for genuinely
  extreme ambition/frugality/generosity profiles without relying on ordinary
  midpoint justice or honesty.

### Validation

- `python -m py_compile library\trait_impacts.py library\event_scoring.py
  unit_test\test_trait_impacts.py unit_test\test_event_scoring.py
  unit_test\test_simulation_incidents.py`
- `python -m unittest unit_test.test_trait_impacts unit_test.test_event_scoring`
- `python -m unittest unit_test.test_trait_impacts unit_test.test_event_scoring
  unit_test.test_simulation_incident_helpers`
- `python -m unittest
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_violent_actor_propensity_separates_extreme_and_stable_genomes
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_property_crime_propensity_separates_extreme_and_stable_genomes
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_instability_greed_and_jealousy_raise_crime_propensities
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_scandal_exposure_propensity_separates_extreme_and_stable_genomes
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_public_virtue_propensity_separates_heroic_and_selfish_genomes
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_forced_murder_tick_skips_stable_low_risk_adults
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_forced_property_crime_skips_stable_low_risk_adults
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_forced_affair_scandal_skips_stable_paramours
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_forced_public_virtue_skips_low_prosocial_adults`

## 2026-06-21 Childbirth Mortality

### Enhancements

- Added a detailed childbirth mortality assessment to the birth pipeline after
  successful conception and newborn generation. Maternal death is evaluated only
  after newborn records are created, preserving child IDs, parent links, and the
  one-birth-per-mother-year invariant.
- Modeled maternal death risk from maternal age, prior births, litter size,
  resource pressure, settlement care capacity, household/job prosperity, and
  health-related genome impact pressure/benefit.
- Added deterministic childbirth-mortality RNG streams keyed by year, sim seed,
  mother, and father so simulation runs remain reproducible.
- Added cause-aware `SimulationContext.mark_dead(...)` payload support. Generic
  deaths keep existing behavior, while childbirth deaths record
  `death_cause=childbirth`, related child IDs, probability/roll, maternal age,
  prior births, litter size, care/prosperity, and health modifier values.
- Childbirth maternal deaths now close active spouse/paramour references through
  the existing death cleanup path, so household-care/orphan routing sees the
  mother as dead and children retain their mother/father IDs.
- Added run-store columns for `death_cause`, `related_child_ids`, and
  `childbirth_maternal_deaths_count`; annual `deaths_count` now includes
  childbirth maternal deaths in addition to ordinary annual mortality deaths.
- Updated event prose and browser event sentences so cause-aware death events
  can render as, for example, "died from childbirth."

### Validation

- `python -m py_compile library\population_growth_runner.py
  library\simulation_context.py library\simulation_store.py library\world_save.py
  utils\gradio_data_browser.py library\event_prose.py
  unit_test\test_childbirth_mortality.py unit_test\test_gradio_data_browser.py`
- `python -m unittest unit_test.test_childbirth_mortality
  unit_test.test_paramour_fertility unit_test.test_birth_surname_rule
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_death_event_shows_childbirth_cause
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_birth_event_marks_out_of_wedlock_children`
- `python -m unittest unit_test.test_event_prose`
- `python -m unittest unit_test.test_relationships_residence.TestRelationshipsResidence.test_death_clears_active_relationship_and_career_state
  unit_test.test_simulation_household_care.TestSimulationHouseholdCare.test_orphan_moves_to_largest_settlement
  unit_test.test_simulation_household_care.TestSimulationHouseholdCare.test_grandparent_same_settlement_adds_childcare_supply`
- `python -m unittest unit_test.test_childbirth_mortality
  unit_test.test_paramour_fertility unit_test.test_birth_surname_rule
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_death_event_shows_childbirth_cause
  unit_test.test_event_prose
  unit_test.test_relationships_residence.TestRelationshipsResidence.test_death_clears_active_relationship_and_career_state
  unit_test.test_simulation_household_care.TestSimulationHouseholdCare.test_orphan_moves_to_largest_settlement
  unit_test.test_simulation_household_care.TestSimulationHouseholdCare.test_grandparent_same_settlement_adds_childcare_supply`

### Notes

- Broader checkpoint-resume checks that hydrate settlement local geography still
  fail in this bundled Python environment because optional `shapely` is not
  installed (`world-map polygon geometry requires the optional 'shapely'
  package`). The same Shapely issue was observed before this workstream.

## 2026-06-21 Production Population Backend Default

### Enhancements

- Made `utils/run_population_simulation.py` default production runs to the
  SQLite `simulation_people_nondetailed` city-directory backend.
- Kept `--use-nondetailed-directory` as an accepted explicit flag for existing
  commands and added `--use-passive-cohorts` as the opt-in legacy aggregate
  cohort backend.
- Left the lower-level `run_population_growth_simulation(...)` default
  unchanged so focused unit fixtures can still choose their background backend
  explicitly without a broad test-contract break.

### Validation

- `python -m py_compile utils\run_population_simulation.py
  unit_test\test_run_population_simulation_cli.py`
- `python -m unittest unit_test.test_run_population_simulation_cli`

## 2026-06-21 Non-Detailed Economy/Migration Annual Wiring

### Enhancements

- Wired the production non-detailed city-directory branch in
  `library.population_growth_runner` to run the SQL demographic tick,
  job-family economy effects, and set-based non-detailed settlement migration
  in sequence each simulation year.
- Added profile gauges for affected non-detailed economy settlements and moved
  non-detailed migrants when profiling is active.
- Left longer mixed-mode calibration/tuning in `TODO.md`; this change makes the
  existing v1 effects active in the production branch so future calibration runs
  measure the real path rather than dormant helper functions.

### Validation

- `python -m py_compile library\population_growth_runner.py
  library\nondetailed_population.py
  unit_test\test_population_growth_nondetailed_runner.py
  unit_test\test_nondetailed_population.py`
- `python -m unittest unit_test.test_population_growth_nondetailed_runner
  unit_test.test_nondetailed_population`
- `python -m unittest unit_test.test_run_population_simulation_cli
  unit_test.test_population_growth_nondetailed_runner
  unit_test.test_nondetailed_population`

## 2026-06-21 Non-Detailed Promotion Selector API

### Enhancements

- Added `SimulationContext.promote_nondetailed_people(...)` for bounded
  immediate materialization from `simulation_people_nondetailed` by person IDs,
  settlement, region, and/or job family, carrying explicit reason/source
  metadata.
- Reused the existing `promote_nondetailed_person(...)` path so variance
  application, deletion from the directory, alive-census cache updates, event
  logging, and passive promotion logging stay centralized.
- Preserved selector source metadata on `nondetailed_person_promoted` events
  for auditability.

### Validation

- `python -m py_compile library\simulation_context.py
  unit_test\test_nondetailed_population.py`
- `python -m unittest unit_test.test_nondetailed_population`

## 2026-06-21 City-State Dynamics And Ancient Urban Politics V1

### Enhancements

- Added `library.simulation_city_states` as a lightweight annual city-state
  layer over existing settlement-grain `city_state` polities, settlements,
  offices, alliances, trade outposts, event records, institutions, reputation
  marks, faction memory, and legal fallout.
- Wired the city-state tick into annual summaries after government bootstraps
  and before economy, with profiling under `summary.city_states`.
- Added generic `city_state_*` event families for urban consolidation, public
  works, civic crises/reforms, resource disputes, leagues, hegemony,
  colony-status changes, and autonomy changes.
- Registered the city-state event families in `config/event_catalog.csv` and
  `config/event_ontology.csv`.
- Routed city-state events through public chronicle defaults, public
  unknown/rumor stages, event prose, and durable consequence upserts for civic
  institutions, leadership reputation, faction memory, and legal fallout.
- Added polity-sheet City-State notes in the Gradio browser for autonomy,
  leagues, hegemony, colony status, latest civic works, and unresolved crises.
- Added `utils/util_city_state_pattern_report.py` to summarize broad
  city-state pattern buckets from `save.sqlite`; the default save currently
  reports 307 maritime-colony lifecycle events from the existing trade-network
  side.
- Replaced the broad implementation TODO with concrete V2 follow-ups for
  occupation/liberation, tribute/garrison league pressure, league breakdown,
  and deeper internal politics.

### Validation

- `python utils/util_load_config.py --world default`
- `python -m py_compile library/simulation_city_states.py
  library/simulation_context.py library/world_save.py library/event_prose.py
  utils/gradio_data_browser.py utils/util_city_state_pattern_report.py
  unit_test/test_simulation_city_states.py unit_test/test_gradio_data_browser.py`
- `python -m unittest unit_test.test_simulation_city_states`
- `python -m unittest
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_polity_sheet_shows_ruler_timeline_and_office_history`
- `python -m unittest
  unit_test.test_save_checkpoint.TestSaveCheckpoint.test_deep_consequence_payloads_persist_factions_and_institutions`
- `python -m unittest unit_test.test_simulation_city_states
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_polity_sheet_shows_ruler_timeline_and_office_history
  unit_test.test_save_checkpoint.TestSaveCheckpoint.test_deep_consequence_payloads_persist_factions_and_institutions`
- `python utils/util_city_state_pattern_report.py --world default`

## 2026-06-21 City-State Dynamics V2 Follow-Ups

### Enhancements

- Completed the occupation/liberation follow-up without adding new save tables:
  city-state polities can record `city_state_occupation_imposed` under
  campaign or parent-polity pressure, retain local civic events while occupied,
  and later record `city_state_liberated` when the overlord is removed.
- Extended city-state notes and browser polity sheets with occupation,
  overlord, tribute, garrison, regime, and office-legitimacy fields.
- Added hegemon-led league pressure: active `city_state_league` alliance
  payloads can now carry tribute/garrison terms, and member city notes remember
  the hegemon, tribute rate, garrison sponsor, and pressure year.
- Added league breakdown when hegemon-led leagues lose trust, suffer tribute
  pressure, or the hegemon declines below a rival member. Breakdown closes the
  active league alliances in RAM, clears member pressure notes, and records a
  public city chronicle event with faction-memory consequences.
- Added internal city politics follow-ups for tyranny/usurpation, political
  exile, and debt relief. Tyranny can swap the head office through the existing
  government office helpers, exile and debt relief use existing legal fallout,
  reputation marks, and faction-memory consequence ledgers, and debt relief can
  improve settlement stability while resolving a civic crisis.
- Registered V2 event types in `config/event_catalog.csv` and
  `config/event_ontology.csv`, extended city-state prose, and added the new
  pattern buckets to both the library and standalone city-state report command.
- Replaced the pending V2 TODO section with a compact completed-context note;
  no further City-State V2 TODO remains from the current plan.

### Validation

- `python utils/util_load_config.py --world default`
- `python -m py_compile library/simulation_city_states.py
  library/event_prose.py utils/gradio_data_browser.py
  utils/util_city_state_pattern_report.py unit_test/test_simulation_city_states.py
  unit_test/test_gradio_data_browser.py`
- `python -m unittest unit_test.test_simulation_city_states`
- `python -m unittest unit_test.test_simulation_city_states
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_polity_sheet_shows_ruler_timeline_and_office_history
  unit_test.test_save_checkpoint.TestSaveCheckpoint.test_deep_consequence_payloads_persist_factions_and_institutions`
- `python -m unittest unit_test.test_event_prose`
- `python utils/util_city_state_pattern_report.py --world default`

## 2026-06-21 Remarkable Archetype Events

### Enhancements

- Added `config/remarkable_archetypes.csv` with 12 rare, ancient-history-inspired
  archetype buckets, weighted so percentages allocate triggered remarkable
  opportunities rather than the total detailed population.
- Added `library.remarkable_archetypes` for config loading, parsing trait /
  context score recipes, and weighted archetype/event-option selection.
- Added `library.simulation_remarkable_archetypes` and wired it into annual
  summaries after ordinary incidents, using mixed detailed/passive/non-detailed
  settlement population for rare opportunity pacing while scoring detailed
  adults through the existing event-scoring context.
- Allowed configured archetypes to emit existing event families including
  `knowledge_culture`, public virtue/status, patronage, political/religious
  conflict, private-life, property-crime/outlaw, and `city_state_*` events with
  `archetype_*`, opportunity-context, historical-importance, and genome-signal
  payload fields.
- Added a conservative background-promotion fallback for allowed archetypes,
  preferring non-detailed directory rows before passive people and cooldowning
  rare remarkable promotions.
- Extended save persistence, default event-record classification, institution
  upserts, event-history metrics, event prose, Gradio event sentences, and
  config/module documentation so archetype events use existing inspection and
  report surfaces.
- Added focused regression coverage for config loading, rare-opportunity
  pacing, event emission, checkpoint persistence, and report metrics.

### Validation

- `python -m unittest unit_test.test_remarkable_archetypes`
- `python -m py_compile library/remarkable_archetypes.py
  library/simulation_remarkable_archetypes.py library/simulation_context.py
  library/world_save.py library/event_history_report.py library/event_prose.py
  utils/gradio_data_browser.py unit_test/test_remarkable_archetypes.py`
- `python -m unittest unit_test.test_event_history_report
  unit_test.test_event_prose unit_test.test_event_catalog`
- `python utils/util_load_config.py --world default`

## 2026-06-21 Gradio Navigation And Discovery

### Enhancements

- Added target-aware person links so sheets can open linked people inside the
  current tab's local person panel instead of always driving the People tab.
- Added tab-local linked-person receivers for Almanack, Settlements, Outlaws,
  Regions, Polities, and World Map detail panels.
- Added a visual Genealogy section to person sheets, showing grandparents when
  known, parents, the focus person with partner/paramour, and children.
- Added Recent History sections to settlement, region, and polity sheets using
  the same event-lens helpers as the History tab.
- Added an explicit-load Discover tab for interesting people, eventful
  settlements, eventful regions, and recent history, with underscore/space
  tolerant search.

### Validation

- `python -m py_compile utils/gradio_data_browser.py
  unit_test/test_gradio_data_browser.py`
- `python -m unittest unit_test.test_gradio_data_browser`
- `python -c "import time; from utils.gradio_data_browser import build_app; t=time.perf_counter(); app=build_app('default'); print('build_app', round(time.perf_counter()-t,3), 'dependencies', len(app.fns))"`

## 2026-06-21 Hybrid Population Promotion And Profiling

### Enhancements

- Reviewed the latest late-year profile group
  (`2026-06-06T02:33:21.528152+00:00`): 10 profiled years,
  13,598 alive, 254.574 profiled seconds. Incident generation remains the
  largest next hot path; government scoring was a bounded measured target in
  this pass.
- Added non-detailed selector filters for gender, minimum age, unpartnered
  status, and borrowed save-DB connections so city-directory promotion can run
  inside existing save transactions.
- Routed shared office, settlement-context/migration, focus/inspection,
  narrative spotlight, and marriage promotion helpers through
  `simulation_people_nondetailed` before falling back to passive cohorts.
- Added direct non-detailed accused promotion for explicit outlaw person IDs;
  scope-based outlaw/crime promotion inherits the shared settlement/office
  helper path.
- Added v1 inferred backfill events for promoted non-detailed people:
  `promotion_backfill_birth`, `promotion_backfill_partnership`, and
  `promotion_backfill_children` where the city-directory row has those facts.
- Locked in v1 economy/migration calibration checks for bounded
  food/prosperity/market/stability deltas and non-detailed migration that moves
  toward attractive settlements without draining source settlements too hard.
- Optimized the measured government candidate-scoring path by applying cheap
  age and career-fitness filters before expensive leadership/military composite
  scoring, with profile counters for skipped candidates.

### Validation

- `python -m py_compile library/simulation_context.py
  library/passive_population.py library/simulation_outlaws.py
  library/simulation_government.py`
- `python -m unittest unit_test.test_nondetailed_population`
- `python -m unittest unit_test.test_simulation_government`
- `python -m unittest unit_test.test_simulation_outlaws`
- `python -m unittest unit_test.test_save_checkpoint.TestSaveCheckpoint.test_promote_passive_person_backfills_family_events
  unit_test.test_save_checkpoint.TestSaveCheckpoint.test_focus_promotion_selectors_persist_reason_and_backfilled_events`
- `python -m unittest unit_test.test_population_growth_nondetailed_runner`
- `python -m unittest unit_test.test_population_growth_determinism.TestPopulationGrowthDeterminism.test_passive_office_promotion_materializes_full_person
  unit_test.test_population_growth_determinism.TestPopulationGrowthDeterminism.test_legacy_generic_passive_species_ethnic_promotes_as_human`

## 2026-06-22 Mortality Tail Tuning

### Fixes

- Replaced the detailed-person lifespan saturation behavior with a steep
  probabilistic tail instead of an effective ceiling: lifespan-100 people now
  reach about 92% annual mortality from age 116 onward, allowing rare exceptional
  survivors without normal runs drifting into the 150s.
- Added extreme-age mortality bands to the non-detailed city-directory SQL tick
  so background people no longer stay at the same modest 70+ annual risk forever.
- Updated mortality regression coverage to lock in the no-ceiling tail shape,
  vectorized/scalar parity, long-lived species windows, and steep-but-probabilistic
  non-detailed mortality at age 117.

### Validation

- `python -m unittest unit_test.test_simulation_mortality unit_test.test_nondetailed_population`
- `python -m py_compile library/simulation_mortality.py library/nondetailed_population.py unit_test/test_simulation_mortality.py unit_test/test_nondetailed_population.py`
- `C:\Users\bryan\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest unit_test.test_simulation_mortality unit_test.test_nondetailed_population`

## 2026-06-22 Non-Detailed Promotion Age Window

### Fixes

- Added an automatic-promotion age window for city-directory people so stale or
  exceptionally ancient non-detailed rows are not chosen for settlement floors,
  offices, focus pulls, marriage candidates, or archetype background promotion.
  Exact person-id inspection can still promote a requested row deliberately.
- Made non-detailed promotion order age-aware while preserving stable person-id
  order inside the preferred adult window.
- Preserved the city-directory `job_family` as a coarse detailed job on
  promotion and emitted an inferred `job_assigned` event so person sheets no
  longer show an empty job history for newly promoted directory residents.
- Added regression coverage for skipping an implausibly old low-id row in favor
  of a plausible same-settlement candidate, and for preserving a promoted
  directory person's coarse job.
- Added a bounded TODO follow-up for persisting lightweight directory names
  before promotion.

### Validation

- `python -m py_compile library/passive_population.py library/simulation_context.py library/simulation_remarkable_archetypes.py unit_test/test_nondetailed_population.py`
- `python -m unittest unit_test.test_nondetailed_population`
- `python -m unittest unit_test.test_population_growth_nondetailed_runner unit_test.test_remarkable_archetypes unit_test.test_simulation_mortality`
- `C:\Users\bryan\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest unit_test.test_nondetailed_population unit_test.test_simulation_mortality`

## 2026-06-22 Prime-Age Promotion And Pairing Windows

### Fixes

- Traced an Asbjorn/Aline case where a 115-year-old non-detailed row was
  promoted by the migration-context `office` selector, then immediately entered
  ordinary detailed couple formation with a 33-year-old partner.
- Tightened automatic city-directory promotion from a permissive 120-year max
  to a default 70-year max with a 22-55 preferred adult window. Exact person-id
  inspection remains able to promote a specific row deliberately.
- Added a tighter marriage-promotion window and carried the same age filters to
  passive-cohort fallbacks, so fallback promotion cannot reintroduce elderly
  spouse candidates.
- Added ordinary detailed partner-formation gates: new couples require people
  inside a partner-formation age window and within a bounded age gap.
- Changed settlement detail-floor promotion to request adult candidates rather
  than allowing children or very old residents before falling back to generated
  adult detail-floor people.
- Added regression coverage for non-detailed prime-age promotion, marriage
  promotion age windows, and detailed pairing that skips an extreme elderly
  candidate in favor of a normal adult.

### Validation

- `python -m py_compile library/passive_population.py library/simulation_context.py library/population_growth_runner.py library/simulation_remarkable_archetypes.py unit_test/test_nondetailed_population.py unit_test/test_population_growth_determinism.py`
- `python -m unittest unit_test.test_nondetailed_population`
- `python -m unittest unit_test.test_population_growth_determinism.TestPopulationGrowthDeterminism.test_pairing_skips_extreme_elderly_new_partner_candidate unit_test.test_population_growth_determinism.TestPopulationGrowthDeterminism.test_pairing_skips_parent_child_when_other_partner_exists unit_test.test_population_growth_determinism.TestPopulationGrowthDeterminism.test_passive_office_promotion_materializes_full_person unit_test.test_population_growth_determinism.TestPopulationGrowthDeterminism.test_migration_arrival_promotes_passive_context_person`
- `python -m unittest unit_test.test_population_growth_nondetailed_runner unit_test.test_remarkable_archetypes unit_test.test_simulation_mortality`
- `C:\Users\bryan\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest unit_test.test_nondetailed_population unit_test.test_simulation_mortality`

## 2026-06-22 Non-Detailed Directory Refill And ID Collision Fixes

### Fixes

- Replaced one-shot city-directory seeding with yearly settlement/region
  reconciliation, so `simulation_people_nondetailed` refills after deaths,
  promotions, and settlement activation instead of returning early once any
  historical row exists.
- Added a global person-ID allocator for detailed, passive, and non-detailed
  stores. Non-detailed seeding, direct inserts, and SQL births now allocate
  above the shared high-water mark, with the yearly runner passing its in-RAM
  `ctx.next_person_id` into SQL births before the next detailed checkpoint.
- Added non-detailed ID collision repair before annual SQL ticks, seeding, and
  promotion. Promotion rekeys a colliding directory row before materialization,
  preserves the existing detailed person, and emits `promotion_backfill_birth`
  only for the repaired/promoted ID.

### Validation

- `python -m py_compile library\nondetailed_population.py
  library\population_growth_runner.py library\simulation_context.py
  unit_test\test_nondetailed_population.py
  unit_test\test_population_growth_nondetailed_runner.py`
- `python -m unittest unit_test.test_nondetailed_population
  unit_test.test_population_growth_nondetailed_runner`
- `python utils/run_mixed_mode_calibration.py --targets 1000 --replicates 1
  --years 2 --starting-couples 2 --min-detailed-cap 10 --max-detailed-cap 20
  --output temp\mixed_mode_bugfix_smoke.tsv`, which produced a fresh
  non-detailed-directory save with `mixed_mode_alive=1057`,
  `detailed_alive=68`, `nondetailed_alive=989`, and
  `murder_per_10k_mixed_person_years=4.882812`.

## 2026-06-22 Era-Appropriate Jobs And Force Scoring

### Enhancements

- Added `library/work_body_fit.py`, a shared body-demand helper that derives
  `body_power_01` from current mind/body `physical`, applies the small male
  raw-strength prior only inside body-demand and force-authority scoring, and
  reduces effective demand through era tools or
  `world_start.magic_physical_leveling_01`.
- Extended `job_archetypes.csv` and `JobArchetypeParams` with
  `physical_demand_01`, `force_authority_01`, `leveling_affinity_01`, and
  `informal_role_01`; tuned manual labor, combat/security, transport,
  construction, care, domestic service, office, criminal, and vice examples.
- Extended `government_titles.csv` / `TitleRow` with `force_authority_01` and
  applied body-power multipliers to high-force office and head-seat scoring
  while preserving existing `male_weight`, leadership, career-fitness, military,
  and childcare-duty terms.
- Added career assignment diagnostics to `job_assigned` payloads:
  `physical_demand_01`, `effective_physical_demand_01`, `body_power_01`, and
  `physical_demand_multiplier`.
- Added explicit parent/grandparent/aunt/uncle childcare kinship pull via
  `childcare_kinship_bonus_01`, feeding the household-care preassignment path
  for feminine high-care kin without making the behavior a hard rule.

### Fixes

- Reclassified `charlatan`, huckster, con-artist, scammer, and fraudster-style
  work as informal criminal/social roles so they no longer fall through to
  default labor semantics.
- Added criminal job-market rows for fraud/charlatan-style titles and synced
  `dev_rules/config_schemas.md`, `dev_rules/government.md`, and
  `dev_rules/module_map.md` for the new knobs.
- Reloaded `worlds/default/config.sqlite` after the CSV changes.

### Validation

- `python -m py_compile library\work_body_fit.py library\job_archetypes.py
  library\government_catalog.py library\simulation_careers.py
  library\simulation_government.py library\simulation_household_care.py
  unit_test\test_simulation_careers.py unit_test\test_simulation_government.py
  unit_test\test_simulation_household_care.py
  unit_test\test_jobs_housing_care.py`
- CSV width check for `config/job_archetypes.csv`, `config/job_market.csv`,
  `config/government_titles.csv`, and `config/world_start.csv`.
- `python utils\util_load_config.py --world default`
- `python utils\util_check_config_sqlite_vs_csv.py --world default`
- `python -m unittest
  unit_test.test_simulation_careers.TestSimulationCareers.test_pretech_body_demand_favors_fit_workers_without_hard_bans
  unit_test.test_simulation_careers.TestSimulationCareers.test_modern_and_magic_leveling_narrow_physical_demand_penalty
  unit_test.test_jobs_housing_care.TestJobsHousingCare.test_job_archetypes_parse_and_mark_adult_only_roles
  unit_test.test_simulation_government.TestSimulationGovernment.test_force_authority_titles_use_body_power_without_overriding_low_force_offices
  unit_test.test_simulation_household_care.TestSimulationHouseholdCare.test_childcare_kinship_bonus_scores_parent_grandparent_and_aunt`
- `python -m unittest
  unit_test.test_simulation_careers.TestSimulationCareers.test_assignment_uses_era_jobs_and_ignores_annotation_columns
  unit_test.test_simulation_careers.TestSimulationCareers.test_female_cross_gender_exception_can_draw_male_only_job
  unit_test.test_simulation_careers.TestSimulationCareers.test_non_masculine_mother_with_childcare_duty_prefers_home_role
  unit_test.test_simulation_careers.TestSimulationCareers.test_job_market_favors_simple_jobs_in_small_settlements
  unit_test.test_simulation_government.TestSimulationGovernment.test_government_scored_pool_skips_cheap_ineligible_candidates_before_composites`

## 2026-06-22 Map Naming, Mixed Population Ratio, And History Card Cleanup

### Enhancements

- Route overlays now carry endpoint display names, and SVG road/sea-route titles
  use settlement names while preserving internal settlement IDs in `data-*`
  attributes.
- Local named feature overlays now retain the naming settlement and settlement
  culture/ethnicity that drove the feature name; duplicate feature names prefer
  the more prominent nearby settlement.
- River SVG titles now use nearby named river features, or a nearby settlement
  fallback, so unnamed river popups no longer stay generic when a settlement is
  clearly responsible for the name.
- Production and mixed-mode calibration CLIs now default to an auto detailed cap
  targeting about `50:1` non-detailed:detailed population when no explicit cap
  is supplied; explicit caps and explicit `0` disabled-cap mode remain
  available.
- Mixed-population reports now expose cap mode, target ratio, observed
  non-detailed count, detailed count, and observed ratio fields.

### Fixes

- Compact crime/event rendering now rehydrates canonical `settlement_id` and
  `region_id` from `simulation_events_readable`, fixing crime cards that fell
  back to `an unrecorded place`.
- Household-service history cards now render employer-context wording when the
  focus person is the employer, while worker timelines keep the worker-focused
  sentence and unrelated people do not receive the event.
- Worker-only `job_assigned` events no longer link `employer_person_id` or
  `host_person_id` into the employer's personal history during normalized
  event-person extraction/backfill.
- Gradio history-card HTML now moves relationship/status/unemployment/move
  reasons into the expandable Details block instead of showing `Reasons:` or
  `reason ...` in the visible card sentence.
- Updated `TODO.md` detailed-population context so the current default is the
  `50:1` auto cap, with 0.1% retained only as a future research note.

### Validation

- Added focused regression coverage in `unit_test.test_world_map_roads`,
  `unit_test.test_gradio_data_browser`,
  `unit_test.test_run_population_simulation_cli`, and
  `unit_test.test_mixed_mode_calibration`, plus
  `unit_test.test_save_checkpoint` for route titles, river titles, feature
  naming provenance, compact crime places, household-service history context,
  worker-only job history links, collapsed card reasons, cap override modes,
  and observed ratio math.
- `git diff --check`
- Python compile/tests were attempted, but the local Windows sandbox launcher
  returned `CreateProcessAsUserW failed: 5`; the unsandboxed retry request was
  rejected by the current usage-limit approval gate.

## 2026-06-22 Genome Profiles, Paramours, Outlaw Crime, And Job Catalog Cleanup

### Enhancements

- Added `config/genome_composite_ratings.csv` and a durable
  `refresh_genome_composite_profile(...)` path that preserves legacy composite
  labels while adding 21 numeric 0..1 genome composite ratings to detailed
  people.
- `Person` now persists `genome_composite_scores` and tuple-backed
  `paramour_person_ids` through `person_json`, while keeping the old scalar
  `paramour_person_id` cache for compatibility.
- Detailed creation, passive promotion, non-detailed promotion, career
  assignment, and checkpoint load now refresh or backfill genome profile scores.
- Paramours now use canonical many-edge storage with helper APIs, allow up to
  three active paramours per person, steeply reduce second/third formation
  probability, and keep birth-father selection aware of all active male
  paramours.
- Wanted and fugitive outlaws now get an individual property-crime pass after
  normal settlement crimes; fugitive crimes resolve through refuge-near,
  last-free, then case settlements and update the existing outlaw case.
- Custody escape flight events now carry `flight_reason="escaped_custody"` and
  Gradio prose says the outlaw escaped custody and fled to the refuge.
- Cleaned `genome_jobs.csv` so scarce town/area authority roles such as mayor,
  sheriff, judge, magistrate, guild master, captain, officer, diplomat,
  ambassador, and similar one/few-per-town titles live in premium columns, with
  common replacements left in normal job slots.

### Fixes

- Save/load now rebuilds scalar and tuple paramour caches from
  `simulation_paramours` without the old last-paramour-wins overwrite.
- Death, pruning, outlaw flight, relationship timelines, person sheets,
  genealogy, and share text now handle multiple paramours consistently.
- Outlaw property crimes include `outlaw_case_key` and `outlaw_status` payload
  fields and no longer open duplicate cases for active outlaws committing
  survival crimes.

### Validation

- `python -m py_compile library\person.py library\genome_composites.py
  library\simulation_context.py library\world_save.py
  library\simulation_careers.py library\simulation_social.py
  library\population_growth_runner.py library\simulation_incidents.py
  library\simulation_outlaws.py utils\gradio_data_browser.py
  unit_test\test_genome_composite_profiles.py
  unit_test\test_genome_jobs_catalog.py unit_test\test_paramour_fertility.py
  unit_test\test_simulation_outlaws.py unit_test\test_gradio_data_browser.py`
- CSV width checks for `config/genome_composite_ratings.csv` and
  `config/genome_jobs.csv`.
- `python utils\util_load_config.py --world default`
- `python utils\util_check_config_sqlite_vs_csv.py --world default`
- `python -m unittest unit_test.test_genome_composite_profiles
  unit_test.test_genome_jobs_catalog unit_test.test_paramour_fertility`
- `python -m unittest
  unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_outlaw_property_crime_chance_uses_wanted_and_fugitive_multipliers
  unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_fugitive_property_crime_uses_existing_case_and_real_location
  unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_custody_can_end_in_death_or_escape_before_release`
- `python -m unittest
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_outlaw_flight_escape_reason_renders_plainly`
- The full `unit_test.test_simulation_outlaws` module was attempted, but it
  exceeded the 240 second local timeout on this laptop; the targeted changed
  outlaw regressions passed.

## 2026-06-23 Browser Relationship Timeline And Map Click Fixes

### Fixes

- Capped browser relationship-history spans at the other participant's
  deathyear, so old saves with missing death-time `paramour_ended` /
  `couple_dissolved` events no longer draw a relationship past the row
  person's death.
- Restored world-map settlement and feature click handling by including town
  and feature targets in the map click handler's actionable-target check.

### Validation

- Confirmed live save case: Thezonus van Hannsouwa's Beatrice Montfort paramour
  span renders `1049-1050`, and `1049-1096` is absent from sheet/share output.
- `python -m py_compile utils/gradio_data_browser.py
  unit_test/test_gradio_data_browser.py`
- `python -m unittest
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_relationship_history_caps_open_span_at_other_person_death
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_sheet_has_combined_relationship_history_section
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_world_map_click_handler_accepts_towns_and_features
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_world_map_selection_opens_existing_region_or_town_sheet`
- Full `unit_test.test_gradio_data_browser` was attempted, but two unrelated
  existing fixture/schema failures remain in the Almanack refresh and compact
  property-crime readable-view tests.

## 2026-06-23 Street Housing And Household Service Consistency

### Fixes

- Stable settlement/office/household-care jobs now lift an adult out of
  `street` housing instead of preserving the old precarious status forever.
- Domestic-service demand now requires a host with an actual `own_household`,
  preventing servants from being placed into street-housed employers'
  impossible households.

### Validation

- Confirmed the live Richard of Mabelaneby case was a real state contradiction:
  he was employed as a `physician` while still saved with
  `housing_status='street'`, and Joanna's service contract targeted him as
  household host.
- `python -m py_compile library/simulation_careers.py
  unit_test/test_jobs_housing_care.py`
- `python -m unittest
  unit_test.test_jobs_housing_care.TestJobsHousingCare.test_stable_job_rehomes_street_adult
  unit_test.test_jobs_housing_care.TestJobsHousingCare.test_street_household_cannot_anchor_domestic_service_demand`
- `python -m unittest unit_test.test_jobs_housing_care`

## 2026-06-23 Composite Trait Scores In Gradio

### Enhancements

- Gradio person sheets now include a dedicated `Composite Trait Scores`
  section for `Person.genome_composite_scores`, using display names from
  `genome_composite_ratings` and falling back to rating IDs when config labels
  are unavailable.
- Person share text now includes top composite trait scores, and the People
  browser shows `Top Composite` / `Top Composite Score` with a computed
  `Top Composite Score` sort.
- Added a dedicated `Composite Scores` tab with explicit load controls for
  world, rating, life filter, search, minimum score, limit, and sort direction;
  selecting a score row opens the existing person sheet/share text.
- Older saves without persisted `genome_composite_scores` get a read-only
  browser fallback from recorded genome/mind-body values; the browser does not
  write refreshed scores back to `save.sqlite`.

### Validation

- `python -m py_compile utils/gradio_data_browser.py
  unit_test/test_gradio_data_browser.py`
- `python -m unittest
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_sheet_and_share_show_composite_trait_scores
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_sheet_falls_back_to_computed_composite_scores
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_people_browser_shows_and_sorts_top_composite_score
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_composite_scores_browser_filters_and_selects_person`
- `python -m unittest unit_test.test_genome_composite_profiles`
- `python -c "import time; from utils.gradio_data_browser import build_app; t=time.perf_counter(); app=build_app('default'); print('build_app', round(time.perf_counter()-t,3), 'dependencies', len(app.fns))"` reported
  `build_app 21.444 dependencies 113`.
- `git diff --check` passed with only existing LF-to-CRLF working-copy
  warnings for the edited Python files.
- Full `python -m unittest unit_test.test_gradio_data_browser` was attempted;
  the only failures were the two pre-existing unrelated fixture/schema errors
  in the Almanack refresh and compact property-crime readable-view tests.

## 2026-06-23 ARI / Narrative Heat Score V3 Recalibration

### Enhancements

- Reworked `simulation_person_archive_scores` to score formula v3 with
  migration-safe columns for `recognition_scope`, `infamy_gap`,
  `prestige_gap`, structured `texture_flags_json`, and
  `score_breakdown_json`.
- Added ordered recognition scopes from `none` through `legendary`; current
  v3 scoring assigns low-status legal notoriety to `local_legal` instead of
  treating ARI as respectability only.
- Replaced sticker-like Violet Marginalia with evidence-backed texture flags
  carrying `flag`, `strength`, `evidence`, and `person_visible_text`.
- Split Narrative Heat into inspectable breakdown channels for realized
  consequence, capped latent potential, tragic compression, knowledge legacy,
  criminal/outlaw consequence, relationship consequence, damped repeat
  volume, and separate arc bonuses.
- Preserved escalation arcs while damping repetition: outlaw/criminal arcs,
  relationship scandal/legal-afterlife arcs, and public-achievement arcs now
  score separately from isolated duplicate event volume.
- Added `--debug-breakdown` to
  `utils/util_refresh_person_archive_scores.py` for per-person score channel,
  cap, damping, arc, scope, gap, and texture-flag output.
- Gradio Archive Scores now show recognition scope, infamy/prestige gaps,
  low-status visibility, structured texture flags, and human-readable share
  text while preserving cached read-model behavior.

### Tests And Fixes

- Added archive-score v3 fixtures for ordinary baseline, obscure tragic
  potential, local legal infamy, relationship scandal/social disruption, and
  remembered public achievement.
- Added/kept regressions for structured texture flags, explanation evidence,
  arc bonuses, repeat damping, absence of unsupported faction-memory reasons,
  death-capped relationship spans, and keyed current-settlement display
  hydration.
- Fixed a save-schema migration backfill bug where
  `_backfill_simulation_event_people` read `row["event_type"]` without
  selecting `event_type`.
- Made the compact property-crime browser fixture drop
  `simulation_events_readable` idempotently, allowing the full browser suite
  to complete locally.

### Live Anchor Refresh

- Refreshed v3 cache rows for Adelhaid `32232`, Fulk `22476`, Thezonus `9800`,
  and Richard of Mabelaneby `42108` with `--debug-breakdown`.
- Old committed scorer baseline on an in-memory save copy:
  Thezonus `heat=100.0 ari=51.0 hidden=49.0 violet=0.725`;
  Fulk `heat=100.0 ari=47.7 hidden=52.3 violet=0.742`;
  Adelhaid `heat=79.7 ari=44.0 hidden=35.7 violet=0.597`;
  Richard `heat=100.0 ari=83.3 hidden=16.7 violet=0.404`.
- Current v3 cache:
  Thezonus `heat=83.8 ari=51.0 hidden=19.8 violet=0.659 scope=local_legal`
  with `scandal_afterlife`; Fulk
  `heat=100.0 ari=69.7 hidden=11.6 violet=0.480 scope=local_legal`
  with `infamous_pursuit`; Adelhaid
  `heat=60.2 ari=44.0 hidden=22.9 violet=0.849 scope=household`
  with `gifted_life_cut_short`; Richard
  `heat=100.0 ari=83.3 hidden=10.2 violet=0.611 scope=regional`
  with `precarious_achievement`.

### Validation

- `python -m py_compile library/person_archive_scores.py
  utils/util_refresh_person_archive_scores.py utils/gradio_data_browser.py
  unit_test/test_person_archive_scores.py
  unit_test/test_gradio_data_browser.py`
- `python -m py_compile library/world_save.py`
- `python -m unittest unit_test.test_person_archive_scores`
- `python -m unittest unit_test.test_save_checkpoint`
- `python -m unittest unit_test.test_gradio_data_browser`
- `python -m unittest unit_test.test_person_archive_scores
  unit_test.test_gradio_data_browser unit_test.test_save_checkpoint` passed
  120 tests in 174.365 seconds, with a non-fatal sqlite ResourceWarning from
  the browser test process.
- `python utils/util_refresh_person_archive_scores.py --world default
  --person-id 32232 --person-id 22476 --person-id 9800 --person-id 42108
  --debug-breakdown`

## 2026-06-23 Genome Composite Score Recalibration

### Enhancements

- Recalibrated numeric `genome_composite_scores` to use weighted arithmetic
  rating curves instead of geometric compression, while leaving legacy
  `genome_composite_names` tag scoring unchanged.
- Retuned `deviation` scoring so ordinary spread no longer saturates Insanity:
  `abs(value)=35` contributes no deviation score, `abs(value)=80` is the
  normal `1.0` point, and rarer extremes can exceed `1.0`.
- Added optional `male_body_bonus`, `female_body_bonus`,
  `masculine_mind_bonus`, and `feminine_mind_bonus` columns to
  `config/genome_composite_ratings.csv`; configured feminine bonuses for
  Sexual Object, masculine bonuses for Sexual Magnetism, and male-body plus
  small masculine-mind bonuses for Physical Strength.
- Preserved above-`1.0` composite scores through save parsing and Gradio
  browser rendering/filtering, with visual progress bars capped at full width
  while still displaying true values such as `1.12`.
- Added score-only refresh on checkpoint load and Gradio recomputation from
  current config/person context so older saves show recalibrated scores without
  rewriting `save.sqlite`.

### Validation

- Current `worlds/default/save.sqlite` recomputed distribution audit:
  `insanity p50=0.366 p90=0.573 p99=0.795 max=1.100 gt1=4`;
  `sexual_object p50=0.460 max=0.854`; `sexual_magnetism p50=0.443
  max=0.861`; `physical_strength p50=0.601 p99=0.982 max=1.122 gt1=14`.
- Gender/mind split checks from the same audit: Sexual Object feminine-mind
  median `0.487` vs masculine-mind `0.436`; Sexual Magnetism masculine-mind
  median `0.477` vs feminine-mind `0.409`; Physical Strength male-body median
  `0.733` vs female-body `0.455`.
- `python utils/util_load_config.py --world default`
- `python utils/util_check_config_sqlite_vs_csv.py --world default`
- `python -m unittest unit_test.test_genome_composite_profiles
  unit_test.test_gradio_data_browser` passed 93 tests in 141.899 seconds, with
  a non-fatal sqlite ResourceWarning from the browser test process.

## 2026-06-23 Genome Composite Nonlinear Rollout

### Enhancements

- Retuned numeric `genome_composite_scores` from the arithmetic blend to a
  nonlinear component-fit blend: component scores are curved, blended with a
  weighted geometric-style mean, and damped by overall coherence so one strong
  trait cannot carry a rating by itself.
- Made body/mind context bonuses confidence-weighted by the base rating fit, so
  requested bonuses still help but do not make otherwise unaligned scores look
  high.
- Added the requested two-per-year reveal schedule for numeric composite scores
  from birth through age 10, using the shared reveal helpers in
  `library.genome_composites`.
- Applied reveal gating to checkpoint refresh, career/profile refresh, Gradio
  person sheets, share text, people-browser top-composite sorting, and the
  Composite Scores table; unrevealed ratings are hidden from tables and shown as
  not-yet-known in person detail views.

### Validation

- Current `worlds/default/save.sqlite` recomputed distribution audit over 216
  detailed people: `insanity p50=0.101 p90=0.234 p99=0.382 max=0.694`;
  `sexual_object p50=0.174 max=0.462`; `sexual_magnetism p50=0.159 max=0.661`;
  `physical_strength p50=0.257 p99=0.640 max=0.650`. All current-save maxima
  are below `1.0`; synthetic all-aligned outlier tests still exceed `1.0`.
- Median range across all 21 ratings is `0.073` to `0.257`, average `0.147`.
- `python utils/util_load_config.py --world default`
- `python utils/util_check_config_sqlite_vs_csv.py --world default`
- `python -m unittest unit_test.test_genome_composite_profiles
  unit_test.test_gradio_data_browser` passed 97 tests in 108.173 seconds, with
  a non-fatal sqlite ResourceWarning from the browser test process.

## 2026-06-23 Composite-Driven Event Rebalance

### Enhancements

- Made numeric `genome_composite_scores` first-class inputs to
  `EventPropensitySpec`, alongside the existing trait factors, context tags,
  and legacy `genome_composite_names`.
- Added pressure-based moral-friction relief so `good_done_desire` and
  `honest_work_desire` usually suppress crime, but scarcity, debt, war,
  relationship strain, and similar circumstances can make bad acts more
  palatable without turning the rule binary.
- Added composite weights to violent crime, property crime, scandal exposure,
  public virtue, knowledge/culture, political crime, and private-life seed
  propensities.
- Added a multi-factor serial-killer composite pressure that requires aligned
  coldness, domination/revenge, concealment, and separation/instability scores;
  it now feeds serial-predator propensity, repeat-murder selection, and the
  settlement murder chance read.
- Added government office candidate multipliers so high `insanity`,
  `psychopathy`, `evil_done_desire`, and `ruthless_ambition` hurt civic office
  prospects, while `lead_others_ability`, `practical_intellect`,
  `honest_work_desire`, and `good_done_desire` help.
- Added social composite modifiers for paramour formation, paramour stability,
  paramour-to-partner promotion, and partner breakup stress.

### Validation

- `python -m py_compile library\event_scoring.py
  library\simulation_incidents.py library\simulation_government.py
  library\simulation_social.py unit_test\test_event_scoring.py
  unit_test\test_simulation_government.py
  unit_test\test_simulation_social_breakups.py`
- `python utils\util_load_config.py --world default`
- `python -m unittest unit_test.test_event_scoring
  unit_test.test_simulation_social_breakups
  unit_test.test_simulation_government.TestSimulationGovernment.test_government_scored_pool_skips_cheap_ineligible_candidates_before_composites
  unit_test.test_simulation_government.TestSimulationGovernment.test_force_authority_titles_use_body_power_without_overriding_low_force_offices
  unit_test.test_simulation_government.TestSimulationGovernment.test_office_composite_multiplier_penalizes_insane_candidates_without_exclusion`
  passed 32 tests in 0.033 seconds.
- `python utils\util_event_history_report.py --world default --sample-limit 0`
  completed against the current save; the hybrid calibration section reported
  `serial_predator_profile_people=1`, `max_serial_predator_propensity=0.8312`,
  `serial_predator_candidate_events=1`, and insufficient murder sample for
  serial/rate retuning.
- Tiny annual-loop smoke:
  `python utils\run_mixed_mode_calibration.py --targets 1000 --years 5
  --replicates 1 --starting-couples 5 --min-detailed-cap 50
  --max-detailed-cap 100 --disable-birth-settlement-spinoff ...` completed one
  temp-output scenario with 3 murders and `needs_more_murder_sample`; no
  `incident_rates.csv` retune was justified from that small sample.

## 2026-06-24 Mixed-Pop Scale, Settlements, And Polities

### Enhancements

- Changed `utils/run_population_simulation.py` detailed cap semantics: omitted
  `--detailed-active-soft-cap` now means uncapped detailed births
  (`detailed_active_soft_cap_mode=disabled_default`), explicit positive values
  are the only runtime cap, and explicit `0` remains disabled.
- Preserved explicit-cap overflow births in the SQLite city-directory backend by
  inserting them into `simulation_people_nondetailed` instead of dropping them.
- Reworked settlement levels to `hamlet <50`, `village 50-99`, `town 100-999`,
  and `city >=1000`.
- Added shared deterministic settlement site capacity / attractiveness scoring
  and wired it into non-detailed seeding, passive cohort allocation,
  non-detailed migration, detailed resource/job migration destinations, and
  settlement evolution capacity distribution.
- Switched government bootstrap, promotion/splitting, naming, and settlement
  merit-office thresholds to mixed population while keeping officeholder
  selection detailed-person based with passive/non-detailed promotion fallback.
- Added a cap of 12 seats per settlement merit title so mixed population can
  create local offices without producing hundred-alderman polities.

### Validation

- `python -m py_compile library/settlements.py library/simulation_context.py library/nondetailed_population.py library/population_growth_runner.py library/simulation_migration.py library/simulation_careers.py library/simulation_government.py utils/run_population_simulation.py unit_test/test_run_population_simulation_cli.py unit_test/test_nondetailed_population.py unit_test/test_population_growth_nondetailed_runner.py unit_test/test_geography_model.py unit_test/test_simulation_government.py`
- Passed: `python -m unittest unit_test.test_run_population_simulation_cli unit_test.test_population_growth_nondetailed_runner`
- Passed: `python -m unittest unit_test.test_nondetailed_population`
- Passed: `python -m unittest unit_test.test_geography_model`
- Passed: `python -m unittest unit_test.test_simulation_government`
- Passed: `python -m unittest unit_test.test_population_growth_determinism`
  passed 19 tests in 198.703 seconds.
- One-year 100-couple CLI smoke with default directory mode completed and
  reported `detailed_active_soft_cap_mode=disabled_default`, `detailed_alive=220`,
  `nondetailed_alive=1106`, one city-level settlement, and one polity.
- Long smoke follow-up recorded in `TODO.md`: pre-performance-fix
  100-couple / 300-year and 100-couple / 30-year temp-world attempts timed out
  before a complete checkpoint useful for population-scale judgment.

## 2026-06-24 Non-Detailed Directory Set-Based Performance Fix

### Fixes

- Removed the directory-backend promotion storm that made tiny mixed-pop runs
  appear stuck: migration-context detailed promotions are skipped for the
  SQLite city-directory backend, leaving background movement in the background
  unless an office, spouse, detailed floor, or explicit focus promotion requests
  a bounded materialization.
- Added per-year mixed/passive population count caches on `SimulationContext`
  and invalidated them after directory writes/promotions, so resource, social,
  incident, settlement, and government systems do not repeatedly recount the
  non-detailed table.
- Built yearly resource facts once before detailed pairing and reused them for
  pairing and births.
- Reworked non-detailed partnership formation into a settlement-local SQL
  pairing pass: eligible unpartnered men and women are ranked by deterministic
  random score inside each settlement, matching equal ranks and writing
  reciprocal `partner_person_id` values.
- Stopped top-up seeding from pre-marking anonymous directory adults as
  partnered without partner ids; stale partnered rows with missing/dead partners
  are repaired before the annual pairing pass.

### Validation

- `python -m py_compile library/nondetailed_population.py library/population_growth_runner.py library/simulation_context.py library/simulation_careers.py library/simulation_migration.py`
- Passed: `python -m unittest unit_test.test_run_population_simulation_cli unit_test.test_population_growth_nondetailed_runner unit_test.test_nondetailed_population unit_test.test_geography_model unit_test.test_simulation_government`
  passed 43 tests in 41.712 seconds.
- Passed: `python -m unittest unit_test.test_population_growth_determinism`
- 10-couple / 5-year profile improved from 53.95 seconds before the fix to
  10.65 seconds after it. The former hot phases
  `runner.migration_context_promote` and `social.setup` dropped to effectively
  zero in the post-fix profile.
- 10-couple / 10-year directory smoke completed in 43.50 seconds with
  `detailed_alive=108`, `nondetailed_alive=24604`, 14 towns, 12 cities,
  26 polities, 364 office seats, and zero alive partnered non-detailed rows
  missing `partner_person_id`.
- 100-couple / 10-year directory smoke completed in 56.82 seconds with
  `detailed_alive=339`, `nondetailed_alive=25454`, 17 towns, 12 cities,
  28 polities, 392 office seats, and zero alive partnered non-detailed rows
  missing `partner_person_id`.

## 2026-06-24 - Settlement affordance model and role taxonomy guard

### Enhancements

- Added `library/settlement_affordances.py` with deterministic on-demand
  settlement affordance profiles, normalized scores, probabilistic role
  candidates, selected roles, population ceiling multipliers, migration pull,
  backfill caps, and explicit large-population enabler reasons.
- Replaced broad text-token site capacity and attraction scoring with the
  affordance profile when simulation context is available, while keeping the
  legacy fallback for context-free callers.
- Updated non-detailed settlement seeding and migration to consume affordance
  pull, site headroom, role-aware young-settlement caps, and large-population
  enabler caps instead of forcing all regional remainder into the final
  settlement.
- Extended non-detailed migration event payloads with destination affordance
  role, migration pull, and large-population enabler reasons for traceability.

### Fixes

- Prevented newly founded non-primary settlements from immediately ballooning
  through non-detailed backfill; founder-year mixed populations can remain
  under 50 when the site role and affordances support that.
- Made regional route/port/fertility context bias local settlement roles only
  when the local site has compatible water, route, or agricultural affordances,
  so backwater sites in strong regions do not automatically become cities.
- Stopped government office assignment from overwriting livelihood job fields;
  office/title status now updates social standing/impact without turning a
  merchant, farmer, or other worker into an "office" job.
- Added career compatibility checks for formal officeholders so ordinary
  assignment blocks vice/criminal/domestic-service livelihoods for office
  holders and blocks low-status ordinary livelihoods for high-rank title
  holders unless an explicit story system later authors the exception.

### Validation

- `python -m py_compile library\settlement_affordances.py library\settlements.py
  library\nondetailed_population.py library\population_growth_runner.py
  library\simulation_government.py library\simulation_careers.py
  unit_test\test_settlement_affordances.py unit_test\test_geography_model.py
  unit_test\test_nondetailed_population.py unit_test\test_simulation_government.py
  unit_test\test_simulation_careers.py`
- Passed: `python -m unittest unit_test.test_settlement_affordances`
- Passed: `python -m unittest unit_test.test_nondetailed_population`
- Passed: `python -m unittest unit_test.test_geography_model`
- Passed: `python -m unittest unit_test.test_lazy_settlements`
- Passed: `python -m unittest unit_test.test_population_growth_nondetailed_runner`
- Passed: `python -m unittest unit_test.test_simulation_careers`

## 2026-06-24 - Settlement Affordance Performance Triage

### Fixes

- Added behavior-preserving in-memory settlement affordance caching:
  - cached profiles are keyed by settlement geography/founding fields, year,
    route context, and relevant government context;
  - `SettlementState` keeps non-persisted cached role, multiplier, pull, enabler,
    and profile fields so hot scoring loops can reuse the same profile;
  - non-detailed seeding, destination selection, migration event payloads, site
    capacity, and attraction scoring now read cached profiles.
- Reduced another destination-selection hot-loop repeat by loading mixed
  population counts once per destination-pick call instead of rebuilding the
  count map per candidate.
- Separated ordinary civic settlement diagnostics from outlaw refuge browsing:
  - Settlements and Towns browser counts now report `simulation_settlements`
    civic rows only;
  - outlaw refuges remain visible through the dedicated Outlaw Refuges browser
    and detail sheet, but no longer count as towns/villages/cities/hamlets.

### Validation

- Tiny deterministic cProfile probe over 26 default-world settlements:
  - no-cache shim: 310 affordance requests, 310 actual profile builds,
    `build_settlement_affordance_profile` 0.546s, `_route_signal` 0.464s,
    `list_routes_from` 336 calls / 0.499s;
  - cached path: 310 affordance requests, 26 actual profile builds,
    `list_routes_from` 52 calls / 0.102s.
- `python -m py_compile library\settlement_affordances.py library\settlements.py
  library\nondetailed_population.py utils\gradio_data_browser.py
  unit_test\test_settlement_affordances.py
  unit_test\test_gradio_data_browser.py`
- `python -m unittest unit_test.test_settlement_affordances
  unit_test.test_nondetailed_population unit_test.test_geography_model`
- `python -m unittest
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_settlements_browser_loads_rows_and_opens_sheet
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_outlaw_refuges_stay_separate_from_settlement_and_town_browsers
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_outlaw_browser_loads_cases_refuges_and_person_selection`
- Passed targeted government regression:
  `python -m unittest unit_test.test_simulation_government.TestSimulationGovernment.test_assign_holder_preserves_existing_livelihood_job`
- `python -m unittest unit_test.test_simulation_government` timed out after
  180 seconds on this laptop before producing a pass/fail result.

## 2026-06-24 - Simulation Runtime Regression Triage

### Fixes

- Stopped checkpoint resume from unconditionally refreshing every loaded
  settlement region's local geography. Saved geography now loads as-is; normal
  preload still refreshes genuinely missing or outdated local geography.
- Added a per-SQLite-connection checkpoint schema guard so repeated
  `ensure_checkpoint_schema()` calls in promotion/backfill loops do not replay
  the full table/view setup after it has already succeeded on that connection.

### Validation

- User-observed timing row: the latest 100-year default run took about 71.8
  minutes, matching the reported regression.
- Before fix, one-year resumed cProfile showed
  `try_load_simulation_checkpoint -> refresh_all_region_local_geographies ->
  build_world_map_geometry` spending about 87.5s before the simulated year.
- Before schema guard, a one-year resumed probe spent 68.9s wall /
  73.0s profiled CPU, with `summary.migration` at 43.5s and cProfile showing a
  one-time settlement-founding map build plus repeated checkpoint schema work.
- After the fixes, the same tiny resumed probe ran one year in 22.0s wall /
  26.6s profiled CPU; final cProfile ran in 28.6s wall, with migration down to
  0.68s and no `build_world_map_geometry` call in the profiled year.
- Passed:
  `python -m unittest unit_test.test_save_checkpoint.TestSaveCheckpoint.test_checkpoint_load_does_not_refresh_all_local_geographies unit_test.test_save_checkpoint.TestSaveCheckpoint.test_ensure_checkpoint_schema_skips_repeated_full_setup_per_connection`

## 2026-06-24 - Ordinary Settlement Satellite Founding

### Enhancements

- Added a civic-only ordinary satellite founding path before annual
  resource-pressure migration. Strong populated regions can now add local
  `birth_spinoff` settlements from active civic settlements when the region has
  remaining headroom and the main settlement is carrying enough mixed
  population.
- Kept outlaw refuges outside the civic founding path. The new check reads only
  `SettlementState` rows from `simulation_settlements`; `simulation_outlaw_refuges`
  remain separate special-purpose sites and do not satisfy local settlement
  density.
- Extended `create_additional_active_settlement()` callers so birth spin-offs
  persist `founding_reason='birth_spinoff'` and `mother_settlement_id` instead
  of appearing as generic organic settlements.

### Validation

- `python -m py_compile library/simulation_context.py library/simulation_migration.py unit_test/test_simulation_migration.py`
- `python -m unittest unit_test.test_simulation_migration`

## 2026-06-24 - Resource-Bounded Settlement Scale

### Enhancements

- Changed SQLite city-directory top-up so non-detailed targets scale by local
  site capacity and current detailed representation. Larger, healthier
  high-capacity settlements can support higher non-detailed:detailed ratios,
  while small sites no longer refill to large town/city sizes just because
  regional cap exists.
- Added resource-health gating to non-detailed top-up. High food pressure, low
  stability, and low prosperity sharply reduce background refill until the
  settlement recovers.
- Added non-detailed resource mortality and stronger resource-driven
  non-detailed migration pressure so Food, Stability, Market, and Prosperity
  affect population movement/loss instead of acting as mostly descriptive
  metrics.
- Let chronically distressed small settlements count as vacancy-like for
  abandonment rolls, so abandoned settlements can return without requiring exact
  zero residents first.
- Raised the detailed floor for large mixed-population settlements, preserving
  detailed representation while allowing larger places to carry more
  non-detailed background population.

### Validation

- Default-save read-only projection, year 1099: active settlements were still
  31 cities / 4 towns before rerun; under the new refill rule the smallest
  distressed satellite `boreas_east:s2` would cap non-detailed top-up at 9
  instead of refilling its current 171 non-detailed residents.
- `python -m py_compile library/settlements.py library/simulation_context.py library/nondetailed_population.py library/population_growth_runner.py unit_test/test_lazy_settlements.py unit_test/test_nondetailed_population.py unit_test/test_population_growth_nondetailed_runner.py unit_test/test_population_growth_determinism.py`
- `python -m unittest unit_test.test_nondetailed_population unit_test.test_lazy_settlements unit_test.test_population_growth_nondetailed_runner unit_test.test_population_growth_determinism.TestPopulationGrowthDeterminism.test_large_mixed_settlement_raises_detailed_floor unit_test.test_population_growth_determinism.TestPopulationGrowthDeterminism.test_detailed_floor_promotes_from_passive_cohorts`
- The full `unit_test.test_population_growth_determinism` module timed out on
  this laptop after 180 seconds before producing a pass/fail result; the two
  touched floor regressions passed directly.

## 2026-06-24 - Duplicate Settlement Re-establishment Guard

### Fixes

- Found the 1050-1059 runtime spike was dominated by runaway generic
  `founding_reason='organic'` settlement rows, not by the ordinary
  `birth_spinoff` satellite path. A consistent year-1059 snapshot showed
  duplicate active same-slot settlements concentrated in two regions:
  `aeria_eastwater_river` had 88 active slot-1 rows and
  `aeria_midland_basin` had 68 active slot-1 rows.
- Changed `reestablish_from_abandoned()` so an abandoned site reuses an
  already-active settlement in the same region and `site_slot` instead of
  creating another active row for the same physical site.
- Made ordinary birth-spinoff cooldown resume-safe by deriving the most recent
  saved `birth_spinoff` founding year from settlement rows when the in-memory
  cooldown dictionary is empty after checkpoint load.
- Split the broad nondetailed yearly timing bucket into subphases
  (`nondetailed.seed_topup`, `nondetailed.sql_tick`,
  `nondetailed.resource_mortality`, `nondetailed.sql_migration`, etc.) so
  future late-year profiles identify the exact hot subphase.

### Validation

- Reproduced the symptom on a consistent copied checkpoint: one profiled year
  from 1049 to 1050 took 72.4s wall, with `runner.nondetailed_directory`
  totaling 43.7s and active settlement count already high.
- Consistent year-1059 snapshot showed 259 active settlements, 68 abandoned
  settlements, 4,321 detailed alive, and 23,050 non-detailed alive; the
  duplicated active same-slot rows explain why the detailed floor and
  nondetailed reconciliation became increasingly expensive.
- `python -m py_compile library/simulation_context.py library/population_growth_runner.py unit_test/test_lazy_settlements.py unit_test/test_simulation_migration.py unit_test/test_population_growth_nondetailed_runner.py`
- `python -m unittest unit_test.test_lazy_settlements unit_test.test_population_growth_nondetailed_runner unit_test.test_simulation_migration`

## 2026-06-25 - Regional Service Villages And Bounded Satellite Fill

### Enhancements

- Added a service-village founding route for ordinary civic settlements:
  - `regional_service_village` satellites can appear when a region's expected
    background population can support more local farm/resource/religious/route
    niches, even before the main settlement reaches the old overcrowding-only
    threshold;
  - the service route uses a capped regional target and a short cooldown, so it
    can add village diversity without recreating the duplicate-settlement
    runtime problem;
  - pressure-driven satellites still use `birth_spinoff`, preserving the
    distinction between family/crowding splits and regional-service villages.
- Wired directory-backed population runs to attempt bounded service-satellite
  founding before yearly `simulation_people_nondetailed` top-up, so newly
  available villages can receive residents in the same year.
- Added role-aware population balancing for non-detailed residents:
  - young service villages and birth spin-offs receive a small non-detailed
    viability floor;
  - minor-role destinations use a soft cap during SQL migration, so hamlets,
    refuges, monasteries, farming clusters, and fishing/reed villages stop
    attracting background migrants once they are already full for their role.

### Validation

- `python -m py_compile library\settlement_affordances.py library\simulation_context.py library\nondetailed_population.py library\population_growth_runner.py unit_test\test_nondetailed_population.py unit_test\test_simulation_migration.py`
- Focused regression slice:
  `python -m unittest unit_test.test_simulation_migration.TestSimulationMigration.test_service_village_founding_uses_regional_hinterland_need unit_test.test_simulation_migration.TestSimulationMigration.test_ordinary_satellite_founding_ignores_outlaw_refuges unit_test.test_nondetailed_population.TestNondetailedPopulation.test_seed_top_up_gives_young_service_village_small_floor unit_test.test_nondetailed_population.TestNondetailedPopulation.test_set_based_nondetailed_migration_skips_full_minor_village`
- Short directory smoke:
  `python utils\run_population_simulation.py --world-id temp_service_village_smoke --reset-world --years 3 --starting-couples 10 --seed 20260625 --use-nondetailed-directory --skip-report-files --skip-timing-log`
  completed in 59.47s with 34 detailed and 2,516 non-detailed living people.
  The resulting save had 8 active settlements across 5 regions: 5 towns and
  3 hamlets, with 3 regions already hosting 2 active civic settlements.
- The broader `python -m unittest unit_test.test_simulation_migration
  unit_test.test_nondetailed_population` run timed out after 184s on this
  laptop before producing a pass/fail report; the focused slice above passed.

## 2026-06-24 - Gradio Browser Launcher Recovery

### Fixes

- Replaced the fragile inline Python selection in `start_gradio_data_browser.cmd`
  with a maintained PowerShell launcher that creates and reuses
  `temp\gradio_browser_venv`, installs `requirements.txt` when needed, stops an
  old listener on port 7860, starts the browser app, and only opens the browser
  after the local service responds.
- Deferred the initial world-map render during Gradio app construction. The app
  no longer blocks startup on the default world map; the Map tab still renders
  the map explicitly through Render Map.
- Made `utils/gradio_data_browser.py` keep the Gradio process alive explicitly
  after `launch()` and log `launch_start` / `launch_ready` lifecycle events.

### Validation

- Confirmed the pre-fix launcher could start Python while failing to bind the
  Gradio service because the known Python candidates did not have `gradio`
  installed after reboot/session restart.
- `python -m py_compile utils/gradio_data_browser.py`
- PowerShell syntax check for `utils/start_gradio_data_browser.ps1`.
- `powershell -NoProfile -ExecutionPolicy Bypass -File utils\start_gradio_data_browser.ps1 -World default -HostName 127.0.0.1 -Port 7860 -NoBrowser`
- Verified `http://127.0.0.1:7860` returned HTTP 200 after the launcher exited.

## 2026-06-25 - Person Browser Residence Timeline Cleanup

### Enhancements

- Added a single person-sheet `Residence Timeline` using the existing Job
  History timeline renderer.
- Residence spans now combine ordinary civic residence, outlaw refuge residence,
  and custody residence in one timeline.
- Timeline row labels use compact place names only, while hover/share details
  preserve region, role/type, start/end years, readable move reasons, refuge
  entered/exited labels, and custody jail/gaol labels with a distinct custody
  color.
- Kept raw movement/refuge events in `save.sqlite`, but hid routine
  `settlement_move_planned`, `settlement_moved`, `job_seeker_migration`,
  `outlaw_flight`, and `outlaw_refuge_joined` rows from the person event feed
  when they are better represented by the timelines.

### Fixes

- Blocked ordinary queued settlement moves for people who are absent because of
  fugitive or imprisoned outlaw state, while allowing explicit legal/outlaw
  transfer reasons.
- Already queued ordinary moves are dropped at apply time if the person has
  since entered custody, preventing captive people from continuing job-seeker
  or household migration.

### Validation

- `python -m py_compile utils\gradio_data_browser.py library\simulation_context.py`
- `python -m unittest unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_sheet_promotes_residence_timelines_over_move_event_spam unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_custody_drops_queued_ordinary_migration_until_release`
- Follow-up residence-timeline merge:
  `python -m py_compile utils\gradio_data_browser.py`
  and
  `python -m unittest unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_sheet_promotes_residence_timelines_over_move_event_spam`
- Neighboring browser tests:
  `python -m unittest unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_sheet_has_combined_relationship_history_section unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_relationship_history_caps_open_span_at_other_person_death unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_outlaw_custody_surfaces_in_person_views`
- Neighboring custody tests:
  `python -m unittest unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_custody_blocks_ordinary_residence_until_release`
  and
  `python -m unittest unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_custody_can_end_in_death_or_escape_before_release`

## 2026-06-25 - Checkpoint Snapshot Bulk Writes

### Fixes

- Converted low-risk checkpoint snapshot insert loops in `library.world_save`
  from per-row `execute` calls to batched `executemany` writes for detailed
  people, passive people, passive cohorts, settlements, regions, couples, and
  paramours.
- Left higher-risk annual simulation loops alone: the non-detailed directory
  annual tick already uses set-based SQL for deaths, jobs, partnerships, births,
  and grouped settlement counts, while current remaining performance TODOs point
  at incident generation and career reassignment rather than checkpoint row
  insertion.

### Validation

- `python -m py_compile library\world_save.py`
- `python -m unittest
  unit_test.test_save_checkpoint.TestSaveCheckpoint.test_checkpoint_roundtrip_one_person
  unit_test.test_save_checkpoint.TestSaveCheckpoint.test_passive_people_checkpoint_roundtrip
  unit_test.test_save_checkpoint.TestSaveCheckpoint.test_couple_surname_convention_roundtrip_on_resume`
- `python utils\run_population_simulation.py --world-id codex_bulkwrite_probe
  --years 1 --seed 424242 --reset-world --profile-last-years 1
  --skip-timing-log --skip-report-files --passive-population-scale 0`

## 2026-06-25 - Incident And Career Candidate Filtering

### Enhancements

- Added bounded priority-fill incident candidate pools in
  `library.simulation_incidents`:
  - murder and property-crime settlement samples now preserve the deterministic
    random caps while making room for high-risk composite/trait profiles;
  - affair scandal samples now prioritize active paramour participants;
  - public virtue and knowledge/culture samples now prioritize prosocial,
    creative, scholarly, and relevant role signals.
- Kept detailed incident event volume governed by existing chance/rate/cap
  gates; the priority pools only change which detailed people are worth
  scoring inside the bounded sample.
- Prevented a same-year duplicate property-crime edge case where a standard
  property crime could create a wanted outlaw case and the outlaw-crime sub-pass
  could immediately emit a second property crime for the same actor in the same
  annual tick.
- Trimmed low-value career loop work in `library.simulation_careers`:
  - job loss now iterates only employed eligible people;
  - assignment/rehire and migration now use exact jobless/unemployed buckets;
  - household labor reuses a jobless bucket for pre-assignment passes;
  - prestige mobility skips expensive scoring where no local prestige target can
    possibly be selected.

### Validation

- `python -m py_compile library\simulation_incidents.py
  library\simulation_careers.py`
- `python -m unittest unit_test.test_simulation_incident_helpers`
- `python -m unittest
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_forced_murder_tick_records_event_kills_victim_and_persists_rumor
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_forced_property_crime_records_nonlethal_rumor
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_forced_affair_scandal_records_rumored_household_scandal
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_forced_public_virtue_records_public_known_good_deed
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_forced_knowledge_culture_records_public_known_breakthrough
  unit_test.test_simulation_careers.TestSimulationCareers.test_annual_tick_assigns_at_era_specific_age
  unit_test.test_simulation_careers.TestSimulationCareers.test_job_loss_and_rehire_are_logged_as_events
  unit_test.test_simulation_careers.TestSimulationCareers.test_job_seeker_migration_moves_household_and_logs_events`

## 2026-06-25 - Gradio Launcher Dependency Repair

### Fixes

- Made `utils/start_gradio_data_browser.ps1` rewrite
  `temp/gradio_setup_current.log` at the start of each run with UTF-8 text, log
  the exact Python command being run, and keep installer output mirrored to the
  console and setup log.
- Changed the Gradio browser dependency install to bypass pip's cache, disable
  the version check, and prefer wheels so a corrupt cache entry does not stop
  first-run setup at the `numpy` metadata download step.
- Added a direct recovery hint for deleting `temp\gradio_browser_venv` if the
  dependency install completes but imports still fail.

### Validation

- `powershell -NoProfile -ExecutionPolicy Bypass -File
  utils\start_gradio_data_browser.ps1 -World default -HostName 127.0.0.1 -Port
  7860 -NoBrowser` reported the Gradio Data Browser running at
  `http://127.0.0.1:7860`.
- `temp\gradio_data_browser.log` recorded `launch_ready` for the venv Python
  process.

## 2026-06-25 - Simulation Runner Live Output Heartbeats

### Enhancements

- Made the Gradio Run Simulation output panel include parent-generated log
  lines, so it shows a starting line, readable phase/progress notes, and a final
  completion/failure line in the newest-first output box.
- Added cheap `SIM_PHASE` markers behind the existing `--progress` flag for
  coarse annual simulation phases such as births, mortality, non-detailed SQL
  work, careers, migration, incidents, government, economy, checkpoint save,
  and event-memory lifecycle work.
- Taught the Gradio runner to render `SIM_PHASE` protocol lines as readable
  output rows like `Year 1000: running incidents.` rather than dumping raw
  machine lines.
- Kept a sparse five-minute fallback heartbeat for cases where a process emits
  no simulator phase/progress output at all.
- Kept the existing newest-first output behavior and capped retained output
  lines to avoid long runs growing Gradio state without bound.

### Validation

- `python -m py_compile utils\gradio_data_browser.py
  unit_test\test_gradio_data_browser.py library\simulation_context.py
  library\population_growth_runner.py utils\run_population_simulation.py`
- `python -m unittest
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_simulation_run_log_is_newest_first_and_bounded
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_simulation_phase_label_formats_known_and_unknown_keys`
- One-year disposable CLI smoke with `--progress` verified visible `SIM_PHASE`
  lines from the annual loop and `SimulationContext.record_year_summary`, then
  the generated `worlds\codex_phase_probe` folder was removed.
- Gradio venv import/build smoke:
  `python -c "from utils.gradio_data_browser import build_app; app=build_app('default'); print('build_app ok', len(app.fns))"`

## 2026-06-25 - Gradio Starlette Warning Cleanup

### Fixes

- Added a narrowly targeted warning filter in `utils.gradio_data_browser` for
  Gradio's third-party Starlette deprecation warning about
  `HTTP_422_UNPROCESSABLE_ENTITY` being renamed to
  `HTTP_422_UNPROCESSABLE_CONTENT`.
- The filter matches only that specific message from `gradio.routes`, leaving
  other Starlette/Gradio warnings visible.

### Validation

- Gradio venv syntax check:
  `python -m py_compile utils\gradio_data_browser.py`
- Gradio venv import check:
  `python -c "import warnings; warnings.simplefilter('default'); import utils.gradio_data_browser as gdb; print('import ok', gdb.StarletteDeprecationWarning.__name__)"`

## 2026-06-25 - Non-Detailed Economy Ownership Cleanup

### Fixes

- Removed the duplicate non-detailed job-family economy application from the
  detailed economy tick; the population runner's explicit
  `nondetailed_job_family_economy` phase remains the single owner.
- Added an economy regression test that fails if
  `simulation_economy_annual_tick` starts invoking
  `apply_nondetailed_job_family_economy_effects` again.

### Validation

- `python -m py_compile library\simulation_economy.py
  unit_test\test_simulation_economy.py
  unit_test\test_population_growth_nondetailed_runner.py`
- `python -m unittest unit_test.test_simulation_economy
  unit_test.test_population_growth_nondetailed_runner`

## 2026-06-25 - Composite Trait Score Transparency

### Enhancements

- Added normally closed `Details` disclosures to each Gradio person-sheet
  composite trait score card.
- Composite details now show the final normalized score, configured input
  components, raw component values when available, weights, weighted
  contributions, nonlinear blend/floor steps, disqualifiers, and context
  modifiers such as body/mind bonuses.
- Persisted-only score rows without genome/mind-body values still show the
  configured recipe and mark raw values as unavailable instead of inventing
  missing inputs.

### Validation

- `python -m py_compile library\genome_composites.py
  utils\gradio_data_browser.py unit_test\test_gradio_data_browser.py`
- `python -m unittest unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_sheet_and_share_show_composite_trait_scores
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_sheet_falls_back_to_computed_composite_scores
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_person_sheet_marks_unrevealed_composite_scores_unknown
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_composite_score_parsers_preserve_above_one_values
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_people_browser_shows_and_sorts_top_composite_score
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_composite_scores_browser_hides_scores_before_reveal_age
  unit_test.test_gradio_data_browser.GradioDataBrowserEventTests.test_composite_scores_browser_shows_scores_after_reveal_age`
- `python -m unittest unit_test.test_genome_composite_profiles`
- Full `unit_test.test_gradio_data_browser` still has pre-existing/non-composite
  migration-event display failures in
  `test_settlement_move_event_uses_normalized_move_details`,
  `test_job_seeker_migration_keeps_route_details_for_display`, and
  `test_compact_job_seeker_migration_uses_related_move_rows`.

## 2026-06-25 - Serious Crime Context V1

### Enhancements

- Added a lightweight `SeriousCrimeContext` for detailed murder incidents that
  records backend motive category, visible motive detail/prose, victim/offender,
  place, witness count/identities/status, direct victim kin count/power,
  offender boldness/ruthlessness/fear, offender-victim relationship,
  seen/identified status, justice pressure, and retaliation risk.
- Kept the context bounded to the selected murder participants, detailed
  witnesses, direct parents/partner, and the already capped local murder sample;
  no new all-person crime or witness scanning loop was added.
- Preserved `settlement_grievance` as a backend motive category while displaying
  clearer visible prose such as a long-running neighborhood feud or debt
  dispute.
- Fed justice, witness, kin, and identification context into the existing
  murder-to-outlaw-case bridge by raising case knownness/severity through the
  current outlaw pursuit formulas and storing the context in case details.
- Updated event prose and Gradio murder cards to prefer `motive_prose` /
  `motive_detail` over raw backend motive categories.

### Validation

- `python -m py_compile library\simulation_incidents.py
  library\simulation_outlaws.py library\event_prose.py
  utils\gradio_data_browser.py`
- `python -m unittest
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_murder_context_records_clear_motive_and_justice_drivers
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_recorded_murder_payload_and_outlaw_case_use_crime_context
  unit_test.test_simulation_incidents.TestSimulationIncidents.test_partner_murder_closes_relationship_state`
- `python -m unittest
  unit_test.test_simulation_outlaws.TestSimulationOutlaws.test_polity_law_profiles_change_case_tuning`
- Full `unit_test.test_simulation_incidents` was attempted on this laptop but
  timed out after 180 seconds, so validation used the focused regression slice
  above.

## 2026-06-25 - Region Recent History Presentation

### Fixes

- Reworked Gradio place-sheet Recent History rendering to stop composing
  duplicate `year: type - year: type` strings.
- Recent history now uses readable sentences for outlaw flight/refuge events,
  title-case labels for fallback event types, and local settlement names where
  available.
- Aggregated low-value non-detailed job-family economy tick events into a
  single local economy sentence per year instead of showing the raw event type.
- Updated Discover-tab Recent History labels to use readable title case and
  suppress raw non-detailed economy ticks by default.

### Validation

- `python -m py_compile utils\gradio_data_browser.py
  unit_test\test_gradio_data_browser.py`
- `python -m unittest unit_test.test_gradio_data_browser -k recent_history`
