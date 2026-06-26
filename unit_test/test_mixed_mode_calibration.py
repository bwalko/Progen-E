"""Tests for mixed-mode calibration reporting helpers."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from library.detailed_population_variance import HIGH_VARIANCE_DETAIL_COMPOSITE
from library.world_save import append_simulation_event_rows, ensure_checkpoint_schema
from utils.run_mixed_mode_calibration import (
    _aggregate_calibration_summary,
    _aggregate_reason_variance_rows,
    _detailed_cap_for_target_ratio,
    _detailed_person_years_from_rows,
    _hybrid_calibration_fields,
    _hybrid_reason_variance_rows,
    _murder_rate_calibration_status,
    _observed_nondetailed_detailed_ratio,
    _overall_hybrid_calibration_status,
    _completed_matching_scenario_indexes,
    _completed_scenario_indexes,
    _parse_hybrid_stop_statuses,
    _read_tsv_rows,
    _reason_output_path,
    _reason_variance_calibration_status,
    _reason_summary_output_path,
    _scenario_plan,
    _should_stop_for_hybrid_status,
    _should_stop_for_sample_thresholds,
    _serial_predator_profile_calibration_status,
    _summary_output_path,
    _write_reason_rows,
    _write_reason_summary,
    _write_calibration_outputs,
    _write_rows,
    _write_summary,
)


def _insert_detailed_person(
    conn: sqlite3.Connection,
    person_id: int,
    *,
    high_variance: bool = False,
) -> None:
    payload = {
        "v": 2,
        "g": [
            -95 if high_variance else 8,
            -92 if high_variance else -11,
            -91 if high_variance else 4,
            93 if high_variance else -6,
            92 if high_variance else 5,
            96 if high_variance else 7,
            95 if high_variance else 6,
            90 if high_variance else 3,
        ],
    }
    if high_variance:
        payload["genome_composite_names"] = [HIGH_VARIANCE_DETAIL_COMPOSITE]
    conn.execute(
        """
        INSERT INTO simulation_people (
            person_id, is_founder, is_alive, first_name, last_name,
            gender, ethnic, species, birthyear, person_json
        )
        VALUES (?, 1, 1, ?, 'Test', 'female', 'human', 'human', 970, ?)
        """,
        (int(person_id), f"P{person_id}", json.dumps(payload)),
    )


class TestMixedModeCalibration(unittest.TestCase):
    def test_scenario_plan_expands_targets_and_replicates_with_stable_seeds(self) -> None:
        scenarios = _scenario_plan((1000, 2000), replicates=3, seed=900)

        self.assertEqual(len(scenarios), 6)
        self.assertEqual(
            scenarios[0],
            {
                "scenario_index": 0,
                "target_index": 0,
                "replicate_index": 0,
                "target_population": 1000,
                "sim_seed": 900,
            },
        )
        self.assertEqual(scenarios[2]["target_population"], 1000)
        self.assertEqual(scenarios[2]["replicate_index"], 2)
        self.assertEqual(scenarios[3]["target_population"], 2000)
        self.assertEqual(scenarios[3]["target_index"], 1)
        self.assertEqual(scenarios[5]["sim_seed"], 905)

    def test_detailed_cap_ratio_helper_targets_fifty_to_one_with_clamps(self) -> None:
        self.assertEqual(
            _detailed_cap_for_target_ratio(50_000, ratio=50.0, min_cap=100, max_cap=5_000),
            1000,
        )
        self.assertEqual(
            _detailed_cap_for_target_ratio(2_000, ratio=50.0, min_cap=100, max_cap=5_000),
            100,
        )
        self.assertEqual(
            _detailed_cap_for_target_ratio(1_000_000, ratio=50.0, min_cap=100, max_cap=5_000),
            5_000,
        )

    def test_observed_ratio_counts_all_non_detailed_backends(self) -> None:
        ratio = _observed_nondetailed_detailed_ratio(
            {
                "detailed_alive": 100,
                "nondetailed_alive": 4_000,
                "passive_person_alive": 500,
                "aggregate_cohort_alive": 500,
            }
        )

        self.assertEqual(ratio, 50.0)

    def test_hybrid_stop_statuses_support_aliases_and_minimum_scenarios(self) -> None:
        self.assertEqual(
            _parse_hybrid_stop_statuses("calibrated"),
            ("within_hybrid_calibration_targets",),
        )
        self.assertEqual(
            _parse_hybrid_stop_statuses("ready,needs_more_murder_sample"),
            (
                "within_hybrid_calibration_targets",
                "serial_murder_not_emerging",
                "serial_murder_too_common",
                "needs_more_murder_sample",
            ),
        )
        summary = {
            "scenario_count": 2,
            "hybrid_calibration_status": "within_hybrid_calibration_targets",
        }

        self.assertFalse(
            _should_stop_for_hybrid_status(
                summary,
                stop_statuses=(),
                min_scenarios=1,
            )
        )
        self.assertFalse(
            _should_stop_for_hybrid_status(
                summary,
                stop_statuses=("within_hybrid_calibration_targets",),
                min_scenarios=3,
            )
        )
        self.assertTrue(
            _should_stop_for_hybrid_status(
                summary,
                stop_statuses=("within_hybrid_calibration_targets",),
                min_scenarios=2,
            )
        )

    def test_sample_threshold_stop_honors_murders_person_years_and_minimum(self) -> None:
        summary = {
            "scenario_count": 2,
            "total_murder_events": 100,
            "total_detailed_person_years": 250000,
        }

        self.assertFalse(
            _should_stop_for_sample_thresholds(
                summary,
                min_scenarios=3,
                stop_after_total_murders=100,
                stop_after_detailed_person_years=None,
            )
        )
        self.assertTrue(
            _should_stop_for_sample_thresholds(
                summary,
                min_scenarios=2,
                stop_after_total_murders=100,
                stop_after_detailed_person_years=None,
            )
        )
        self.assertTrue(
            _should_stop_for_sample_thresholds(
                summary,
                min_scenarios=1,
                stop_after_total_murders=None,
                stop_after_detailed_person_years=250000,
            )
        )
        self.assertFalse(
            _should_stop_for_sample_thresholds(
                summary,
                min_scenarios=1,
                stop_after_total_murders=500,
                stop_after_detailed_person_years=1250000,
            )
        )

    def test_existing_rows_can_be_loaded_for_resume(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            path = Path(td) / "mixed.tsv"
            path.write_text(
                "scenario_index\tsim_seed\textra\n"
                "0\t15000\tkept\n"
                "2\t15002\tkept\n",
                encoding="utf-8",
            )

            rows = _read_tsv_rows(path)

        self.assertEqual(rows[0]["scenario_index"], "0")
        self.assertEqual(rows[1]["sim_seed"], "15002")
        self.assertEqual(_completed_scenario_indexes(rows), {0, 2})

    def test_resume_only_skips_rows_matching_current_scenario_plan(self) -> None:
        scenarios = _scenario_plan((1000, 2000), replicates=2, seed=15000)
        rows = [
            {
                "scenario_index": "0",
                "target_index": "0",
                "replicate_index": "0",
                "target_population": "1000",
                "sim_seed": "15000",
            },
            {
                "scenario_index": "1",
                "target_index": "0",
                "replicate_index": "1",
                "target_population": "1000",
                "sim_seed": "99999",
            },
            {
                "scenario_index": "2",
                "target_index": "1",
                "replicate_index": "0",
                "target_population": "1000",
                "sim_seed": "15002",
            },
            {
                "scenario_index": "7",
                "target_index": "3",
                "replicate_index": "0",
                "target_population": "3000",
                "sim_seed": "15007",
            },
        ]

        self.assertEqual(_completed_scenario_indexes(rows), {0, 1, 2, 7})
        self.assertEqual(
            _completed_matching_scenario_indexes(rows, scenarios),
            {0},
        )

    def test_write_rows_ignores_extra_columns_for_resumed_files(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            out = Path(td) / "mixed_mode_calibration.tsv"
            row = {
                "scenario_index": 0,
                "target_index": 0,
                "replicate_index": 0,
                "target_population": 1000,
                "years": 1,
                "world": "default",
                "sim_seed": 15000,
                "starting_couples": 1,
                "region_count": 1,
                "base_capacity": 1000,
                "passive_population_scale": "1.00000000",
                "detailed_active_soft_cap": 10,
                "birth_settlement_spinoff_disabled": "no",
                "elapsed_s": "0.1",
                "detailed_alive": 2,
                "passive_person_alive": 0,
                "aggregate_cohort_alive": 998,
                "aggregate_cohort_births": 0,
                "aggregate_cohort_deaths": 0,
                "aggregate_cohort_partnered": 0,
                "mixed_mode_alive": 1000,
                "cohort_rows": 1,
                "promotion_count": 0,
                "future_column": "ignored",
            }

            _write_rows(out, [row])
            text = out.read_text(encoding="utf-8")

        self.assertIn("scenario_index\ttarget_index\treplicate_index", text)
        self.assertIn("detailed_active_soft_cap_mode", text)
        self.assertIn("target_nondetailed_detailed_ratio", text)
        self.assertIn("observed_nondetailed_detailed_ratio", text)
        self.assertNotIn("future_column", text)

    def test_hybrid_fields_are_read_from_event_history_report(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            save = Path(td) / "save.sqlite"
            with sqlite3.connect(save) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                _insert_detailed_person(conn, 1, high_variance=True)
                _insert_detailed_person(conn, 2)
                conn.executemany(
                    """
                    INSERT INTO simulation_promotion_log (
                        person_id, sim_year, reason, source_event_id,
                        synthesized_json, created_at
                    )
                    VALUES (?, 1000, ?, NULL, '{}', '2026-01-01T00:00:00+00:00')
                    """,
                    [
                        (1, "criminal_outlaw"),
                        (2, "marriage_into_detailed_family"),
                    ],
                )
                conn.executemany(
                    """
                    INSERT INTO simulation_people_nondetailed (
                        person_id, birthyear, is_alive, gender, job_family
                    )
                    VALUES (?, 970, 1, 'female', 'food')
                    """,
                    [(1001,), (1002,), (1003,)],
                )
                conn.execute(
                    """
                    INSERT INTO simulation_serial_predation_candidates (
                        person_id, risk_lane, status, risk_score,
                        harm_drive, inhibition, control, exposure_noise,
                        organized_serial_risk, disorganized_serial_risk,
                        last_checked_year
                    )
                    VALUES (1, 'organized', 'active', 0.74,
                            0.84, 0.03, 0.78, 0.16,
                            0.74, 0.04, 1000)
                    """
                )
                append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1000,
                            "murder",
                            {
                                "killer_person_id": 1,
                                "victim_person_id": 2,
                                "incident_kind": "predatory_murder",
                                "serial_predator_candidate": True,
                                "serial_predation_candidate": True,
                                "serial_predator_propensity": 0.74,
                            },
                        )
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )
                conn.commit()

            fields = _hybrid_calibration_fields(
                save,
                trait_slots=(
                    "empathy",
                    "justice",
                    "honesty",
                    "neurochemical",
                    "assertiveness",
                    "perception",
                    "discipline",
                    "persuasion",
                ),
                murder_target_per_10k=4.0,
                detailed_person_years=2500,
                mixed_person_years=5000,
            )
            reason_rows = _hybrid_reason_variance_rows(
                save,
                trait_slots=(
                    "empathy",
                    "justice",
                    "honesty",
                    "neurochemical",
                    "assertiveness",
                    "perception",
                    "discipline",
                    "persuasion",
                ),
            )

        self.assertEqual(fields["report_detailed_people"], 2)
        self.assertEqual(fields["report_detailed_alive_people"], 2)
        self.assertEqual(fields["report_non_detailed_alive_people"], 3)
        self.assertEqual(fields["high_variance_detail_people"], 1)
        self.assertEqual(fields["genome_scored_detailed_people"], 2)
        self.assertEqual(fields["extreme_detail_people"], 1)
        self.assertEqual(fields["serial_predator_profile_people"], 1)
        self.assertEqual(fields["serial_predator_profile_share"], "0.500000")
        self.assertGreater(float(fields["max_serial_predator_propensity"]), 0.62)
        self.assertEqual(fields["murder_events"], 1)
        self.assertEqual(fields["predatory_murder_events"], 1)
        self.assertEqual(fields["serial_predatory_murder_events"], 0)
        self.assertEqual(fields["serial_predator_candidate_events"], 1)
        self.assertEqual(fields["distinct_murder_killers"], 1)
        self.assertEqual(fields["repeat_murder_killers_2plus"], 0)
        self.assertEqual(fields["serial_murder_killers_3plus"], 0)
        self.assertEqual(fields["detailed_person_years"], 2500)
        self.assertEqual(fields["mixed_person_years"], 5000)
        self.assertEqual(fields["murder_rate_population_basis"], "mixed_population")
        self.assertEqual(fields["murder_per_10k_detailed_person_years"], "4.000000")
        self.assertEqual(fields["murder_per_10k_mixed_person_years"], "2.000000")
        self.assertEqual(fields["murder_rate_target_ratio"], "0.500000")
        self.assertEqual(fields["murder_target_per_10k_per_year"], "4.000000")
        self.assertEqual(fields["murder_rate_calibration_status"], "insufficient_murder_sample")
        self.assertEqual(fields["serial_candidate_share_of_murders"], "1.000000")
        self.assertEqual(fields["serial_murder_calibration_status"], "insufficient_murder_sample")
        by_reason = {row["reason"]: row for row in reason_rows}
        self.assertEqual(by_reason["criminal_outlaw"]["detailed_people"], 1)
        self.assertEqual(
            by_reason["criminal_outlaw"]["high_variance_detail_people"],
            1,
        )
        self.assertEqual(
            by_reason["criminal_outlaw"]["selection_profile"],
            "criminal_outlaw",
        )
        self.assertEqual(
            by_reason["criminal_outlaw"]["reason_variance_calibration_status"],
            "insufficient_reason_sample",
        )
        self.assertEqual(
            by_reason["marriage_into_detailed_family"]["detailed_people"],
            1,
        )

    def test_murder_rate_status_requires_enough_events_before_band_judgment(self) -> None:
        self.assertEqual(
            _murder_rate_calibration_status(
                observed_per_10k=12.0,
                target_per_10k=4.0,
                murder_events=2,
            ),
            "insufficient_murder_sample",
        )
        self.assertEqual(
            _murder_rate_calibration_status(
                observed_per_10k=7.0,
                target_per_10k=4.0,
                murder_events=20,
            ),
            "above_target_band",
        )
        self.assertEqual(
            _murder_rate_calibration_status(
                observed_per_10k=4.5,
                target_per_10k=4.0,
                murder_events=20,
            ),
            "within_target_band",
        )

    def test_serial_profile_status_requires_sample_and_rare_presence(self) -> None:
        self.assertEqual(
            _serial_predator_profile_calibration_status(
                scored_people=20,
                profile_people=0,
                profile_share=0.0,
            ),
            "insufficient_profile_sample",
        )
        self.assertEqual(
            _serial_predator_profile_calibration_status(
                scored_people=120,
                profile_people=0,
                profile_share=0.0,
            ),
            "no_serial_predator_profiles",
        )
        self.assertEqual(
            _serial_predator_profile_calibration_status(
                scored_people=120,
                profile_people=1,
                profile_share=1 / 120,
            ),
            "serial_predator_profiles_present",
        )
        self.assertEqual(
            _serial_predator_profile_calibration_status(
                scored_people=120,
                profile_people=6,
                profile_share=0.05,
            ),
            "serial_predator_profiles_too_common",
        )

    def test_reason_variance_status_requires_sample_then_checks_profile_band(self) -> None:
        self.assertEqual(
            _reason_variance_calibration_status(
                scored_people=3,
                average_score=0.95,
                expected_min=0.45,
                expected_max=0.75,
            ),
            "insufficient_reason_sample",
        )
        self.assertEqual(
            _reason_variance_calibration_status(
                scored_people=12,
                average_score=0.30,
                expected_min=0.45,
                expected_max=0.75,
            ),
            "below_profile_floor",
        )
        self.assertEqual(
            _reason_variance_calibration_status(
                scored_people=12,
                average_score=0.90,
                expected_min=0.45,
                expected_max=0.75,
            ),
            "above_profile_ceiling",
        )
        self.assertEqual(
            _reason_variance_calibration_status(
                scored_people=12,
                average_score=0.60,
                expected_min=0.45,
                expected_max=0.75,
            ),
            "within_profile_band",
        )

    def test_detailed_person_years_from_rows_sums_valid_yearly_counts(self) -> None:
        self.assertEqual(
            _detailed_person_years_from_rows(
                [
                    {"detailed_alive_count": "10"},
                    {"detailed_alive_count": 12},
                    {"detailed_alive_count": ""},
                    {"detailed_alive_count": "bad"},
                    {"detailed_alive_count": -3},
                ]
            ),
            22,
        )

    def test_aggregate_summary_weights_rate_and_variance_samples(self) -> None:
        rows = [
            {
                "target_population": 100000,
                "sim_seed": 10,
                "birth_settlement_spinoff_disabled": "yes",
                "report_detailed_alive_people": 1000,
                "report_non_detailed_alive_people": 99000,
                "event_year_span": 100,
                "detailed_person_years": 100000,
                "mixed_person_years": 100000,
                "murder_events": 40,
                "murder_target_per_10k_per_year": "4.000000",
                "serial_predator_candidate_events": 2,
                "serial_murder_killers_3plus": 1,
                "serial_murder_events_by_3plus_killers": 1,
                "genome_scored_detailed_people": 100,
                "average_detail_variance_score": "0.500000",
                "high_variance_detail_people": 20,
                "extreme_detail_people": 10,
                "serial_predator_profile_people": 3,
                "average_serial_predator_propensity": "0.040000",
                "max_serial_predator_propensity": "0.700000",
            },
            {
                "target_population": 100000,
                "sim_seed": 11,
                "birth_settlement_spinoff_disabled": "no",
                "report_detailed_alive_people": 1000,
                "report_non_detailed_alive_people": 100000,
                "event_year_span": 200,
                "detailed_person_years": 200000,
                "mixed_person_years": 200000,
                "murder_events": 80,
                "murder_target_per_10k_per_year": "4.000000",
                "serial_predator_candidate_events": 4,
                "serial_murder_events_by_3plus_killers": 0,
                "genome_scored_detailed_people": 300,
                "average_detail_variance_score": "0.700000",
                "high_variance_detail_people": 60,
                "extreme_detail_people": 30,
                "serial_predator_profile_people": 1,
                "average_serial_predator_propensity": "0.020000",
                "max_serial_predator_propensity": "0.650000",
            },
        ]

        summary = _aggregate_calibration_summary(rows)

        self.assertEqual(summary["scenario_count"], 2)
        self.assertEqual(summary["birth_settlement_spinoff_disabled_scenarios"], 1)
        self.assertEqual(summary["distinct_target_count"], 1)
        self.assertEqual(summary["distinct_seed_count"], 2)
        self.assertEqual(summary["total_event_year_span"], 300)
        self.assertEqual(summary["total_detailed_person_years"], 300000)
        self.assertEqual(summary["total_mixed_person_years"], 300000)
        self.assertEqual(summary["total_murder_events"], 120)
        self.assertEqual(summary["murder_rate_min_murder_sample"], 10)
        self.assertEqual(summary["murder_rate_murder_sample_remaining"], 0)
        self.assertEqual(summary["murder_rate_sample_ready"], "yes")
        self.assertEqual(summary["murder_rate_population_basis"], "mixed_population")
        self.assertEqual(summary["murder_per_10k_detailed_person_years"], "4.000000")
        self.assertEqual(summary["murder_per_10k_mixed_person_years"], "4.000000")
        self.assertEqual(summary["murder_rate_target_ratio"], "1.000000")
        self.assertEqual(summary["murder_rate_calibration_status"], "within_target_band")
        self.assertEqual(summary["serial_murder_event_share_3plus"], "0.008333")
        self.assertEqual(summary["serial_murder_min_murder_sample"], 100)
        self.assertEqual(summary["serial_murder_sample_remaining"], 0)
        self.assertEqual(summary["serial_murder_sample_ready"], "yes")
        self.assertEqual(summary["murder_sample_projection_rate_per_10k"], "4.000000")
        self.assertEqual(summary["murder_sample_projection_rate_source"], "observed")
        self.assertEqual(
            summary[
                "serial_murder_sample_projected_additional_detailed_person_years"
            ],
            0,
        )
        self.assertEqual(
            summary["serial_murder_sample_projected_additional_scenarios"],
            0,
        )
        self.assertEqual(summary["serial_murder_emergence_min_murder_sample"], 500)
        self.assertEqual(summary["serial_murder_emergence_sample_remaining"], 380)
        self.assertEqual(summary["serial_murder_emergence_sample_ready"], "no")
        self.assertEqual(
            summary[
                "serial_murder_emergence_projected_additional_detailed_person_years"
            ],
            950000,
        )
        self.assertEqual(
            summary["serial_murder_emergence_projected_additional_scenarios"],
            7,
        )
        self.assertEqual(
            summary["serial_murder_calibration_status"],
            "within_real_life_guardrail",
        )
        self.assertEqual(
            summary["serial_murder_emergence_status"],
            "insufficient_emergence_sample",
        )
        self.assertEqual(summary["hybrid_calibration_ready"], "no")
        self.assertEqual(
            summary["hybrid_calibration_status"],
            "needs_more_serial_emergence_sample",
        )
        self.assertEqual(
            summary["recommended_next_calibration_reason"],
            "reach_serial_emergence_sample",
        )
        self.assertEqual(
            summary["recommended_next_calibration_stop_flag"],
            "--stop-after-total-murders",
        )
        self.assertEqual(summary["recommended_next_calibration_stop_value"], 500)
        self.assertEqual(
            summary["recommended_next_calibration_resume_flag"],
            "--resume-existing",
        )
        self.assertEqual(summary["weighted_average_detail_variance_score"], "0.650000")
        self.assertEqual(summary["total_high_variance_detail_people"], 80)
        self.assertEqual(summary["total_extreme_detail_people"], 40)
        self.assertEqual(summary["total_serial_predator_profile_people"], 4)
        self.assertEqual(
            summary["serial_predator_profile_share_of_scored_detailed"],
            "0.010000",
        )
        self.assertEqual(summary["serial_predator_profile_min_scored_sample"], 100)
        self.assertEqual(summary["serial_predator_profile_sample_remaining"], 0)
        self.assertEqual(summary["serial_predator_profile_sample_ready"], "yes")
        self.assertEqual(
            summary["serial_predator_profile_calibration_status"],
            "serial_predator_profiles_present",
        )
        self.assertEqual(
            summary["weighted_average_serial_predator_propensity"],
            "0.025000",
        )
        self.assertEqual(summary["max_serial_predator_propensity"], "0.700000")

    def test_aggregate_summary_uses_mixed_person_years_for_murder_rate(self) -> None:
        summary = _aggregate_calibration_summary(
            [
                {
                    "target_population": 100000,
                    "sim_seed": 15,
                    "report_detailed_alive_people": 1000,
                    "report_non_detailed_alive_people": 99000,
                    "event_year_span": 100,
                    "detailed_person_years": 100000,
                    "mixed_person_years": 1000000,
                    "murder_events": 40,
                    "murder_target_per_10k_per_year": "4.000000",
                    "serial_predator_candidate_events": 0,
                    "serial_murder_killers_3plus": 0,
                    "serial_murder_events_by_3plus_killers": 0,
                    "genome_scored_detailed_people": 120,
                    "average_detail_variance_score": "0.500000",
                    "high_variance_detail_people": 2,
                    "extreme_detail_people": 1,
                    "serial_predator_profile_people": 1,
                }
            ]
        )

        self.assertEqual(summary["murder_rate_population_basis"], "mixed_population")
        self.assertEqual(summary["murder_per_10k_detailed_person_years"], "4.000000")
        self.assertEqual(summary["murder_per_10k_mixed_person_years"], "0.400000")
        self.assertEqual(summary["murder_rate_target_ratio"], "0.100000")
        self.assertEqual(summary["murder_rate_calibration_status"], "below_target_band")

    def test_aggregate_summary_reports_remaining_murder_sample(self) -> None:
        rows = [
            {
                "target_population": 100000,
                "sim_seed": 12,
                "report_detailed_alive_people": 1000,
                "report_non_detailed_alive_people": 99000,
                "event_year_span": 50,
                "mixed_person_years": 50000,
                "murder_events": 2,
                "murder_target_per_10k_per_year": "4.000000",
                "serial_predator_candidate_events": 1,
                "serial_murder_killers_3plus": 1,
                "serial_murder_events_by_3plus_killers": 1,
                "genome_scored_detailed_people": 10,
                "average_detail_variance_score": "0.500000",
                "high_variance_detail_people": 2,
                "extreme_detail_people": 1,
            }
        ]

        summary = _aggregate_calibration_summary(rows)

        self.assertEqual(summary["total_murder_events"], 2)
        self.assertEqual(summary["murder_rate_min_murder_sample"], 10)
        self.assertEqual(summary["murder_rate_murder_sample_remaining"], 8)
        self.assertEqual(summary["murder_rate_sample_ready"], "no")
        self.assertEqual(
            summary["murder_rate_calibration_status"],
            "insufficient_murder_sample",
        )
        self.assertEqual(summary["serial_murder_min_murder_sample"], 100)
        self.assertEqual(summary["serial_murder_sample_remaining"], 98)
        self.assertEqual(summary["serial_murder_sample_ready"], "no")
        self.assertEqual(summary["murder_sample_projection_rate_per_10k"], "4.000000")
        self.assertEqual(summary["murder_sample_projection_rate_source"], "target")
        self.assertEqual(
            summary[
                "serial_murder_sample_projected_additional_detailed_person_years"
            ],
            245000,
        )
        self.assertEqual(
            summary["serial_murder_sample_projected_additional_scenarios"],
            5,
        )
        self.assertEqual(summary["serial_murder_emergence_sample_remaining"], 498)
        self.assertEqual(summary["serial_murder_emergence_sample_ready"], "no")
        self.assertEqual(
            summary[
                "serial_murder_emergence_projected_additional_detailed_person_years"
            ],
            1245000,
        )
        self.assertEqual(
            summary["serial_murder_emergence_projected_additional_scenarios"],
            25,
        )
        self.assertEqual(
            summary["serial_murder_calibration_status"],
            "insufficient_murder_sample",
        )
        self.assertEqual(
            summary["serial_murder_emergence_status"],
            "insufficient_emergence_sample",
        )
        self.assertEqual(summary["hybrid_calibration_ready"], "no")
        self.assertEqual(
            summary["hybrid_calibration_status"],
            "needs_more_murder_sample",
        )
        self.assertEqual(
            summary["recommended_next_calibration_reason"],
            "reach_murder_rate_sample",
        )
        self.assertEqual(
            summary["recommended_next_calibration_stop_flag"],
            "--stop-after-total-murders",
        )
        self.assertEqual(summary["recommended_next_calibration_stop_value"], 10)
        self.assertEqual(
            summary["recommended_next_calibration_resume_flag"],
            "--resume-existing",
        )

    def test_aggregate_summary_reports_serial_emergence_status(self) -> None:
        summary = _aggregate_calibration_summary(
            [
                {
                    "target_population": 100000,
                    "sim_seed": 13,
                    "report_detailed_alive_people": 1000,
                    "report_non_detailed_alive_people": 99000,
                    "event_year_span": 500,
                    "mixed_person_years": 500000,
                    "murder_events": 500,
                    "murder_target_per_10k_per_year": "10.000000",
                    "serial_predator_candidate_events": 3,
                    "serial_murder_killers_3plus": 1,
                    "serial_murder_events_by_3plus_killers": 3,
                    "genome_scored_detailed_people": 120,
                    "average_detail_variance_score": "0.500000",
                    "high_variance_detail_people": 2,
                    "extreme_detail_people": 1,
                    "serial_predator_profile_people": 1,
                }
            ]
        )

        self.assertEqual(summary["serial_murder_event_share_3plus"], "0.006000")
        self.assertEqual(summary["serial_murder_calibration_status"], "within_real_life_guardrail")
        self.assertEqual(summary["serial_murder_emergence_sample_remaining"], 0)
        self.assertEqual(summary["serial_murder_emergence_sample_ready"], "yes")
        self.assertEqual(summary["serial_murder_emergence_status"], "serial_murder_emerged")
        self.assertEqual(summary["hybrid_calibration_ready"], "yes")
        self.assertEqual(
            summary["hybrid_calibration_status"],
            "within_hybrid_calibration_targets",
        )

    def test_aggregate_summary_reports_missing_serial_emergence(self) -> None:
        summary = _aggregate_calibration_summary(
            [
                {
                    "target_population": 100000,
                    "sim_seed": 14,
                    "report_detailed_alive_people": 1000,
                    "report_non_detailed_alive_people": 99000,
                    "event_year_span": 500,
                    "mixed_person_years": 500000,
                    "murder_events": 500,
                    "murder_target_per_10k_per_year": "10.000000",
                    "serial_predator_candidate_events": 0,
                    "serial_murder_killers_3plus": 0,
                    "serial_murder_events_by_3plus_killers": 0,
                    "genome_scored_detailed_people": 120,
                    "average_detail_variance_score": "0.500000",
                    "high_variance_detail_people": 2,
                    "extreme_detail_people": 1,
                    "serial_predator_profile_people": 1,
                }
            ]
        )

        self.assertEqual(summary["serial_murder_event_share_3plus"], "0.000000")
        self.assertEqual(summary["serial_murder_calibration_status"], "within_real_life_guardrail")
        self.assertEqual(summary["serial_murder_emergence_sample_ready"], "yes")
        self.assertEqual(summary["serial_murder_emergence_status"], "no_serial_murder_emerged")
        self.assertEqual(summary["hybrid_calibration_ready"], "yes")
        self.assertEqual(
            summary["hybrid_calibration_status"],
            "serial_murder_not_emerging",
        )

    def test_guarded_serial_emergence_can_satisfy_status_without_static_profile(self) -> None:
        summary = _aggregate_calibration_summary(
            [
                {
                    "target_population": 100000,
                    "sim_seed": 16,
                    "report_detailed_alive_people": 1000,
                    "report_non_detailed_alive_people": 99000,
                    "event_year_span": 500,
                    "mixed_person_years": 1000000,
                    "murder_events": 500,
                    "murder_target_per_10k_per_year": "5.000000",
                    "serial_predator_candidate_events": 1,
                    "serial_murder_killers_3plus": 1,
                    "serial_murder_events_by_3plus_killers": 3,
                    "genome_scored_detailed_people": 120,
                    "average_detail_variance_score": "0.500000",
                    "high_variance_detail_people": 2,
                    "extreme_detail_people": 1,
                    "serial_predator_profile_people": 0,
                }
            ]
        )

        self.assertEqual(
            summary["serial_predator_profile_calibration_status"],
            "no_serial_predator_profiles",
        )
        self.assertEqual(summary["serial_murder_emergence_status"], "serial_murder_emerged")
        self.assertEqual(
            summary["hybrid_calibration_status"],
            "within_hybrid_calibration_targets",
        )

    def test_overall_hybrid_calibration_status_prioritizes_retuning(self) -> None:
        self.assertEqual(
            _overall_hybrid_calibration_status(
                {
                    "murder_rate_calibration_status": "above_target_band",
                    "serial_murder_calibration_status": "insufficient_murder_sample",
                    "serial_murder_emergence_status": "insufficient_emergence_sample",
                }
            ),
            "retune_murder_rate_above_target",
        )
        self.assertEqual(
            _overall_hybrid_calibration_status(
                {
                    "murder_rate_calibration_status": "within_target_band",
                    "serial_predator_profile_calibration_status": (
                        "serial_predator_profiles_present"
                    ),
                    "serial_murder_calibration_status": "insufficient_murder_sample",
                    "serial_murder_emergence_status": "insufficient_emergence_sample",
                }
            ),
            "needs_more_serial_guardrail_sample",
        )
        self.assertEqual(
            _overall_hybrid_calibration_status(
                {
                    "murder_rate_calibration_status": "within_target_band",
                    "serial_predator_profile_calibration_status": (
                        "no_serial_predator_profiles"
                    ),
                    "serial_murder_calibration_status": "insufficient_murder_sample",
                    "serial_murder_emergence_status": "insufficient_emergence_sample",
                }
            ),
            "retune_serial_predator_profiles_absent",
        )
        self.assertEqual(
            _overall_hybrid_calibration_status(
                {
                    "murder_rate_calibration_status": "within_target_band",
                    "serial_predator_profile_calibration_status": (
                        "serial_predator_profiles_too_common"
                    ),
                    "serial_murder_calibration_status": "insufficient_murder_sample",
                    "serial_murder_emergence_status": "insufficient_emergence_sample",
                }
            ),
            "retune_serial_predator_profiles_too_common",
        )
        self.assertEqual(
            _overall_hybrid_calibration_status(
                {
                    "murder_rate_calibration_status": "within_target_band",
                    "serial_predator_profile_calibration_status": (
                        "serial_predator_profiles_present"
                    ),
                    "serial_murder_calibration_status": "above_real_life_guardrail",
                    "serial_murder_emergence_status": "above_real_life_guardrail",
                }
            ),
            "serial_murder_too_common",
        )

    def test_write_summary_uses_metric_value_tsv(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            rows_path = Path(td) / "mixed_mode_calibration.tsv"
            summary_path = _summary_output_path(rows_path)

            _write_summary(
                summary_path,
                {
                    "scenario_count": 2,
                    "murder_rate_calibration_status": "within_target_band",
                },
            )
            text = summary_path.read_text(encoding="utf-8")

        self.assertTrue(str(summary_path).endswith("mixed_mode_calibration.summary.tsv"))
        self.assertIn("metric\tvalue", text)
        self.assertIn("scenario_count\t2", text)
        self.assertIn("murder_rate_calibration_status\twithin_target_band", text)

    def test_write_calibration_outputs_rewrites_all_batch_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            output_path = root / "mixed.tsv"
            summary_path = _summary_output_path(output_path)
            reason_path = _reason_output_path(output_path)
            reason_summary_path = _reason_summary_output_path(output_path)

            summary = _write_calibration_outputs(
                output_path=output_path,
                summary_path=summary_path,
                reason_path=reason_path,
                reason_summary_path=reason_summary_path,
                rows=[
                    {
                        "scenario_index": 0,
                        "target_index": 0,
                        "replicate_index": 0,
                        "target_population": 1000,
                        "sim_seed": 15000,
                        "detailed_person_years": 10,
                        "murder_events": 0,
                    }
                ],
                reason_rows=[
                    {
                        "scenario_index": 0,
                        "target_index": 0,
                        "replicate_index": 0,
                        "target_population": 1000,
                        "sim_seed": 15000,
                        "reason": "criminal_outlaw",
                        "detailed_people": 1,
                        "high_variance_detail_people": 1,
                        "genome_scored_detailed_people": 1,
                        "extreme_detail_people": 1,
                        "average_detail_variance_score": "0.700000",
                    }
                ],
            )

            output_text = output_path.read_text(encoding="utf-8")
            summary_text = summary_path.read_text(encoding="utf-8")
            reason_text = reason_path.read_text(encoding="utf-8")
            reason_summary_text = reason_summary_path.read_text(encoding="utf-8")

        self.assertEqual(summary["scenario_count"], 1)
        self.assertIn("scenario_index\ttarget_index\treplicate_index", output_text)
        self.assertIn("hybrid_calibration_status", summary_text)
        self.assertIn("criminal_outlaw", reason_text)
        self.assertIn("criminal_outlaw", reason_summary_text)

    def test_reason_outputs_aggregate_weighted_variance_by_reason(self) -> None:
        rows = [
            {
                "scenario_index": 0,
                "target_index": 0,
                "replicate_index": 0,
                "target_population": 1000,
                "sim_seed": 15000,
                "reason": "criminal_outlaw",
                "detailed_people": 6,
                "high_variance_detail_people": 6,
                "genome_scored_detailed_people": 6,
                "extreme_detail_people": 3,
                "average_detail_variance_score": "0.700000",
            },
            {
                "scenario_index": 1,
                "target_index": 0,
                "replicate_index": 1,
                "target_population": 1000,
                "sim_seed": 15001,
                "reason": "criminal_outlaw",
                "detailed_people": 6,
                "high_variance_detail_people": 5,
                "genome_scored_detailed_people": 6,
                "extreme_detail_people": 2,
                "average_detail_variance_score": "0.620000",
            },
            {
                "scenario_index": 1,
                "target_index": 0,
                "replicate_index": 1,
                "target_population": 1000,
                "sim_seed": 15001,
                "reason": "kinship_link",
                "detailed_people": 2,
                "high_variance_detail_people": 1,
                "genome_scored_detailed_people": 2,
                "extreme_detail_people": 0,
                "average_detail_variance_score": "0.300000",
            },
        ]

        summary = _aggregate_reason_variance_rows(rows)

        self.assertEqual(summary[0]["reason"], "criminal_outlaw")
        self.assertEqual(summary[0]["selection_profile"], "criminal_outlaw")
        self.assertEqual(summary[0]["scenario_count"], 2)
        self.assertEqual(summary[0]["detailed_people"], 12)
        self.assertEqual(summary[0]["high_variance_detail_people"], 11)
        self.assertEqual(summary[0]["genome_scored_detailed_people"], 12)
        self.assertEqual(summary[0]["extreme_detail_people"], 5)
        self.assertEqual(summary[0]["average_detail_variance_score"], "0.660000")
        self.assertEqual(
            summary[0]["reason_variance_calibration_status"],
            "within_profile_band",
        )
        self.assertEqual(summary[1]["reason"], "kinship_link")
        self.assertEqual(
            summary[1]["reason_variance_calibration_status"],
            "insufficient_reason_sample",
        )

    def test_write_reason_outputs_use_default_sibling_paths(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            rows_path = Path(td) / "mixed_mode_calibration.tsv"
            reason_path = _reason_output_path(rows_path)
            reason_summary_path = _reason_summary_output_path(rows_path)
            rows = [
                {
                    "scenario_index": 0,
                    "target_index": 0,
                    "replicate_index": 0,
                    "target_population": 1000,
                    "sim_seed": 15000,
                    "reason": "criminal_outlaw",
                    "selection_profile": "criminal_outlaw",
                    "detailed_people": 1,
                    "high_variance_detail_people": 1,
                    "genome_scored_detailed_people": 1,
                    "extreme_detail_people": 1,
                    "average_detail_variance_score": "0.900000",
                    "expected_average_detail_variance_min": "0.600400",
                    "expected_average_detail_variance_max": "0.880400",
                    "reason_variance_calibration_status": "insufficient_reason_sample",
                }
            ]
            summary = _aggregate_reason_variance_rows(rows)

            _write_reason_rows(reason_path, rows)
            _write_reason_summary(reason_summary_path, summary)
            reason_text = reason_path.read_text(encoding="utf-8")
            summary_text = reason_summary_path.read_text(encoding="utf-8")

        self.assertTrue(str(reason_path).endswith("mixed_mode_calibration.promotion_reasons.tsv"))
        self.assertTrue(
            str(reason_summary_path).endswith(
                "mixed_mode_calibration.promotion_reason_summary.tsv"
            )
        )
        self.assertIn("scenario_index\ttarget_index\treplicate_index", reason_text)
        self.assertIn("reason_variance_calibration_status", reason_text)
        self.assertIn(
            "criminal_outlaw\tcriminal_outlaw\t1\t1\t1\t1\t1\t0.900000",
            summary_text,
        )

    def test_write_rows_includes_hybrid_calibration_columns(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            out = Path(td) / "mixed_mode_calibration.tsv"
            row = {
                "scenario_index": 0,
                "target_index": 0,
                "replicate_index": 0,
                "target_population": 100000,
                "years": 1,
                "world": "default",
                "sim_seed": 42,
                "starting_couples": 2,
                "region_count": 1,
                "base_capacity": 1000,
                "passive_population_scale": "100.00000000",
                "detailed_active_soft_cap": 200,
                "population_backend": "nondetailed_directory",
                "elapsed_s": "0.100000",
                "detailed_alive": 4,
                "passive_person_alive": 0,
                "nondetailed_alive": 1000,
                "nondetailed_births": 10,
                "nondetailed_deaths": 3,
                "aggregate_cohort_alive": 1000,
                "aggregate_cohort_births": 0,
                "aggregate_cohort_deaths": 0,
                "aggregate_cohort_partnered": 0,
                "mixed_mode_alive": 1004,
                "cohort_rows": 2,
                "promotion_count": 0,
                "report_detailed_people": 4,
                "report_detailed_alive_people": 4,
                "report_non_detailed_alive_people": 1000,
                "high_variance_detail_people": 2,
                "genome_scored_detailed_people": 4,
                "extreme_detail_people": 1,
                "average_detail_variance_score": "0.640000",
                "serial_predator_profile_people": 0,
                "serial_predator_profile_share": "0.000000",
                "average_serial_predator_propensity": "0.100000",
                "max_serial_predator_propensity": "0.200000",
                "event_year_span": 1,
                "detailed_person_years": 4,
                "mixed_person_years": 1004,
                "murder_rate_population_basis": "mixed_population",
                "murder_events": 0,
                "ordinary_murder_events": 0,
                "feud_revenge_murder_events": 0,
                "robbery_property_murder_events": 0,
                "outlaw_raid_killing_events": 0,
                "war_political_legal_killing_events": 0,
                "spree_panic_killing_events": 0,
                "predatory_murder_events": 0,
                "serial_predatory_murder_events": 0,
                "serial_predator_candidate_events": 0,
                "distinct_murder_killers": 0,
                "repeat_murder_killers_2plus": 0,
                "serial_murder_killers_3plus": 0,
                "serial_murder_events_by_3plus_killers": 0,
                "murder_per_10k_detailed_person_years": "",
                "murder_per_10k_mixed_person_years": "",
                "murder_target_per_10k_per_year": "4.000000",
                "murder_rate_target_ratio": "",
                "murder_rate_calibration_status": "insufficient_murder_sample",
                "serial_candidate_share_of_murders": "",
                "serial_murder_event_share_3plus": "",
                "serial_murder_target_share_max": "0.010000",
                "serial_murder_calibration_status": "insufficient_murder_sample",
            }

            _write_rows(out, [row])
            text = out.read_text(encoding="utf-8")

        self.assertIn("high_variance_detail_people", text)
        self.assertIn("scenario_index\ttarget_index\treplicate_index", text)
        self.assertIn("murder_rate_calibration_status", text)
        self.assertIn("detailed_person_years", text)
        self.assertIn("mixed_person_years", text)
        self.assertIn("nondetailed_alive", text)
        self.assertIn("serial_predator_profile_people", text)
        self.assertIn("serial_candidate_share_of_murders", text)
        self.assertIn("predatory_murder_events", text)
        self.assertIn("serial_predatory_murder_events", text)
        self.assertIn("serial_murder_calibration_status", text)
        self.assertIn("insufficient_murder_sample", text)
        self.assertIn("\t2\t4\t1\t0.640000\t", text)


if __name__ == "__main__":
    unittest.main()
