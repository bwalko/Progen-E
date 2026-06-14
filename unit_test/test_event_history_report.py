"""Tests for event-history tuning reports."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from library.event_history_report import (
    build_event_history_report,
    format_event_history_summary,
    write_event_history_report,
)
from library.world_save import (
    append_simulation_event_rows,
    ensure_checkpoint_schema,
    mark_event_record_lost,
)


def _insert_person(
    conn: sqlite3.Connection, person_id: int, first_name: str, last_name: str
) -> None:
    conn.execute(
        """
        INSERT INTO simulation_people (
            person_id, is_founder, is_alive, first_name, last_name,
            gender, ethnic, species, birthyear, person_json
        )
        VALUES (?, 1, 1, ?, ?, 'female', 'human', 'human', 970, '{}')
        """,
        (int(person_id), first_name, last_name),
    )


class TestEventHistoryReport(unittest.TestCase):
    def test_build_report_counts_visibility_metrics_and_samples(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            save = root / "save.sqlite"
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                _insert_person(conn, 1, "Tara", "Stone")
                _insert_person(conn, 2, "Pell", "Ash")
                _insert_person(conn, 3, "Ira", "Marsh")
                _insert_person(conn, 4, "Lio", "Dawn")
                birth_id, crime_id, virtue_id = append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1001,
                            "birth",
                            {
                                "person_id": 4,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1002,
                            "property_crime",
                            {
                                "perpetrator_person_id": 1,
                                "target_person_id": 2,
                                "incident_kind": "storehouse_robbery",
                                "motive": "scarcity",
                                "loss_value": 0.18,
                                "resource_pressure": 1.2,
                                "historical_importance": 0.31,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1003,
                            "public_virtue",
                            {
                                "benefactor_person_id": 3,
                                "beneficiary_person_id": 4,
                                "incident_kind": "heroic_rescue",
                                "motive": "mercy",
                                "relief_value": 0.12,
                                "resource_pressure": 0.7,
                                "historical_importance": 0.42,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )
                mark_event_record_lost(conn, birth_id, lost_year=1040)
                aeria_region_key = conn.execute(
                    """
                    SELECT region_key FROM simulation_region_lookup
                    WHERE region_id = 'aeria_north'
                    """
                ).fetchone()["region_key"]
                aeria_settlement_key = conn.execute(
                    """
                    SELECT settlement_key FROM simulation_settlement_lookup
                    WHERE settlement_id = 'aeria_north:settlement:1'
                    """
                ).fetchone()["settlement_key"]
                conn.execute(
                    """
                    INSERT OR IGNORE INTO simulation_region_lookup (region_id)
                    VALUES ('boreas_port')
                    """
                )
                boreas_region_key = conn.execute(
                    """
                    SELECT region_key FROM simulation_region_lookup
                    WHERE region_id = 'boreas_port'
                    """
                ).fetchone()["region_key"]
                ts = "2026-01-01T00:00:00+00:00"
                conn.execute(
                    """
                    INSERT INTO simulation_faction_memory (
                        source_event_id, memory_key, memory_type, status,
                        faction_a_key, faction_b_key, principal_person_id,
                        opposing_person_id, region_key, settlement_key,
                        polarity, strength, start_year, expected_decay_year,
                        details_json, created_at, updated_at
                    )
                    VALUES (?, 'feud:1:2', 'feud_memory', 'active',
                            'person:1', 'person:2', 1, 2, ?, ?,
                            'negative', 0.65, 1002, 1052, '{}', ?, ?)
                    """,
                    (crime_id, aeria_region_key, aeria_settlement_key, ts, ts),
                )
                conn.execute(
                    """
                    INSERT INTO simulation_legal_fallout (
                        source_event_id, fallout_key, fallout_type, status,
                        principal_person_id, opposing_person_id, related_person_id,
                        region_key, settlement_key, severity, start_year,
                        expected_resolution_year, resolved_year, details_json,
                        created_at, updated_at
                    )
                    VALUES (?, 'inheritance:1:2:4', 'inheritance_dispute',
                            'resolved', 1, 2, 4, ?, ?, 0.72, 1002,
                            1004, 1004, '{}', ?, ?)
                    """,
                    (crime_id, aeria_region_key, aeria_settlement_key, ts, ts),
                )
                fallout_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                conn.execute(
                    """
                    INSERT INTO simulation_legal_adjudications (
                        fallout_id, source_event_id, adjudication_key,
                        adjudication_type, outcome, principal_result,
                        opposing_result, adjudication_year, principal_person_id,
                        opposing_person_id, related_person_id, region_key,
                        settlement_key, severity, details_json, created_at,
                        updated_at
                    )
                    VALUES (?, ?, 'inheritance:1:2:4:1004',
                            'inheritance_dispute_resolution',
                            'inheritance_split', 'share_reduced',
                            'share_recognized', 1004, 1, 2, 4, ?, ?,
                            0.72, '{}', ?, ?)
                    """,
                    (
                        fallout_id,
                        crime_id,
                        aeria_region_key,
                        aeria_settlement_key,
                        ts,
                        ts,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO simulation_domain_diffusion (
                        diffusion_year, domain, source_region_key,
                        target_region_key, route_type, route_friction,
                        source_domain_score, target_domain_score_before,
                        target_domain_score_after, state_delta,
                        source_latest_event_id, created_at
                    )
                    VALUES (1005, 'shipbuilding', ?, ?, 'sea', 0.2,
                            0.5, 0.1, 0.14, 0.04, ?, ?)
                    """,
                    (aeria_region_key, boreas_region_key, virtue_id, ts),
                )
                conn.execute(
                    """
                    INSERT INTO simulation_domain_states (
                        region_key, domain, domain_score, breakthrough_count,
                        first_event_year, latest_event_year, first_event_id,
                        latest_event_id, latest_incident_kind,
                        latest_creator_person_id, latest_settlement_key,
                        created_at, updated_at
                    )
                    VALUES (?, 'shipbuilding', 0.39, 2, 1003, 1005, ?, ?,
                            'shipbuilding_advance', 3, ?, ?, ?)
                    """,
                    (
                        aeria_region_key,
                        virtue_id,
                        virtue_id,
                        aeria_settlement_key,
                        ts,
                        ts,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO simulation_obligations (
                        source_event_id, obligation_key, obligation_type,
                        status, owed_by_person_id, owed_to_person_id,
                        region_key, settlement_key, strength, start_year,
                        expected_end_year, details_json, created_at, updated_at
                    )
                    VALUES (?, 'relief:4:3', 'relief_debt', 'active',
                            4, 3, ?, ?, 0.37, 1003, 1015, '{}', ?, ?)
                    """,
                    (virtue_id, aeria_region_key, aeria_settlement_key, ts, ts),
                )
                conn.execute(
                    """
                    INSERT INTO simulation_reputation_marks (
                        source_event_id, mark_key, person_id, reputation_axis,
                        reputation_before, reputation_after, direction,
                        mark_strength, region_key, settlement_key, mark_year,
                        details_json, created_at, updated_at
                    )
                    VALUES (?, 'leadership:3', 3, 'leadership', 'low',
                            'medium', 'positive', 0.33, ?, ?, 1003, '{}',
                            ?, ?)
                    """,
                    (virtue_id, aeria_region_key, aeria_settlement_key, ts, ts),
                )
                conn.execute(
                    """
                    INSERT INTO simulation_institutions (
                        institution_key, institution_type, status, region_key,
                        settlement_key, focus_domain, founded_year, latest_year,
                        founding_event_id, latest_event_id, founder_person_id,
                        patron_person_id, strength, influence_score,
                        details_json, created_at, updated_at
                    )
                    VALUES ('school:aeria:medicine', 'school', 'active',
                            ?, ?, 'medicine', 1003, 1003, ?, ?, 3, 1,
                            0.25, 0.18, '{}', ?, ?)
                    """,
                    (aeria_region_key, aeria_settlement_key, virtue_id, virtue_id, ts, ts),
                )
                conn.execute(
                    """
                    INSERT INTO simulation_innovation_discoveries (
                        source_event_id, innovation_id, innovation_name,
                        category, domain, era_id, discovery_year,
                        historical_year, discoverer_person_id,
                        patron_person_id, region_key, settlement_key,
                        novelty_score, details_json, created_at
                    )
                    VALUES (?, 'herbal_salve', 'herbal salves', 'medicine',
                            'medicine', 'classical', 1003, 3, 3, 1,
                            ?, ?, 0.44, '{}', ?)
                    """,
                    (virtue_id, aeria_region_key, aeria_settlement_key, ts),
                )
                conn.execute(
                    """
                    INSERT INTO simulation_innovation_knowledge (
                        innovation_id, innovation_name, category, domain, era_id,
                        scope_kind, scope_key, status, adoption_score,
                        first_known_year, latest_known_year, first_event_id,
                        latest_event_id, source_kind, region_key, settlement_key,
                        details_json, created_at, updated_at
                    )
                    VALUES ('herbal_salve', 'herbal salves', 'medicine',
                            'medicine', 'classical', 'region', 'aeria_north',
                            'adopted', 0.63, 1003, 1005, ?, ?, 'generated',
                            ?, ?, '{}', ?, ?)
                    """,
                    (
                        virtue_id,
                        virtue_id,
                        aeria_region_key,
                        aeria_settlement_key,
                        ts,
                        ts,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO simulation_innovation_era_state (
                        scope_kind, scope_key, era_id, era_rank, adopted_count,
                        next_era_adopted_count, latest_year, region_key,
                        settlement_key, created_at, updated_at
                    )
                    VALUES ('region', 'aeria_north', 'classical', 2, 7, 1,
                            1005, ?, ?, ?, ?)
                    """,
                    (aeria_region_key, aeria_settlement_key, ts, ts),
                )
                conn.commit()

                report = build_event_history_report(
                    conn,
                    save_path=save,
                    sample_limit=10,
                    sample_event_types={"property_crime", "public_virtue"},
                )

            self.assertEqual(report.total_events, 3)
            self.assertEqual(report.total_records, 3)
            self.assertGreater(report.save_size_bytes or 0, 0)
            type_counts = {row.keys[0]: row.count for row in report.event_counts_by_type}
            self.assertEqual(type_counts["birth"], 1)
            self.assertEqual(type_counts["property_crime"], 1)
            visibility = {row.keys: row.count for row in report.visibility_counts}
            self.assertEqual(
                visibility[("birth", "lineage_memory", "lost")],
                1,
            )
            self.assertEqual(
                visibility[("property_crime", "property_crime_record", "rumored")],
                1,
            )
            metrics = {
                (row.event_type, row.metric): row
                for row in report.metric_summaries
            }
            self.assertAlmostEqual(
                metrics[("property_crime", "resource_pressure")].average,
                1.2,
            )
            self.assertAlmostEqual(
                metrics[("public_virtue", "historical_importance")].average,
                0.42,
            )
            sample_text = "\n".join(row.public_prose for row in report.public_samples)
            self.assertIn("storehouse robbery", sample_text)
            self.assertIn("heroic rescue", sample_text)
            consequence_counts = {row.keys: row.count for row in report.consequence_counts}
            self.assertEqual(
                consequence_counts[
                    ("Faction Memory", "feud_memory", "active", "negative")
                ],
                1,
            )
            self.assertEqual(
                consequence_counts[
                    ("Domain States", "shipbuilding", "shipbuilding_advance")
                ],
                1,
            )
            self.assertEqual(
                consequence_counts[("Obligations", "relief_debt", "active")],
                1,
            )
            self.assertEqual(
                consequence_counts[("Reputation Marks", "leadership", "positive")],
                1,
            )
            self.assertEqual(
                consequence_counts[
                    ("Legal Fallout", "inheritance_dispute", "resolved")
                ],
                1,
            )
            self.assertEqual(
                consequence_counts[
                    (
                        "Legal Adjudications",
                        "inheritance_dispute_resolution",
                        "inheritance_split",
                    )
                ],
                1,
            )
            self.assertEqual(
                consequence_counts[("Domain Diffusion", "shipbuilding", "sea")],
                1,
            )
            self.assertEqual(
                consequence_counts[
                    ("Institutions", "school", "active", "medicine")
                ],
                1,
            )
            self.assertEqual(
                consequence_counts[
                    ("Innovation Knowledge", "medicine", "medicine", "adopted")
                ],
                1,
            )
            consequence_metrics = {
                (row.section, row.key, row.metric): row
                for row in report.consequence_metric_summaries
            }
            self.assertAlmostEqual(
                consequence_metrics[
                    ("Faction Memory", "feud_memory / active", "strength")
                ].average,
                0.65,
            )
            self.assertAlmostEqual(
                consequence_metrics[
                    (
                        "Domain States",
                        "shipbuilding / shipbuilding_advance",
                        "domain_score",
                    )
                ].average,
                0.39,
            )
            self.assertAlmostEqual(
                consequence_metrics[
                    ("Obligations", "relief_debt / active", "strength")
                ].average,
                0.37,
            )
            self.assertAlmostEqual(
                consequence_metrics[
                    ("Reputation Marks", "leadership / positive", "mark_strength")
                ].average,
                0.33,
            )
            self.assertAlmostEqual(
                consequence_metrics[
                    ("Legal Fallout", "inheritance_dispute / resolved", "severity")
                ].average,
                0.72,
            )
            self.assertAlmostEqual(
                consequence_metrics[
                    ("Domain Diffusion", "shipbuilding / sea", "state_delta")
                ].average,
                0.04,
            )
            self.assertAlmostEqual(
                consequence_metrics[
                    ("Innovation Knowledge", "medicine / medicine / adopted", "adoption_score")
                ].average,
                0.63,
            )
            summary = format_event_history_summary(report)
            self.assertIn("total_events: 3", summary)
            self.assertIn("property_crime", summary)
            self.assertIn("Consequence Counts", summary)
            self.assertIn("Faction Memory / feud_memory / active / negative: 1", summary)

    def test_outlaw_outcome_summary_tracks_conversion_and_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            save = root / "save.sqlite"
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                _insert_person(conn, 1, "Ada", "Forge")
                _insert_person(conn, 2, "Bryn", "Ash")
                _insert_person(conn, 3, "Cor", "Vale")
                property_id, murder_id, *_outlaw_event_ids = append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1001,
                            "property_crime",
                            {
                                "perpetrator_person_id": 1,
                                "target_person_id": 2,
                                "incident_kind": "storehouse_robbery",
                                "loss_value": 0.30,
                                "historical_importance": 0.40,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1002,
                            "murder",
                            {
                                "killer_person_id": 3,
                                "victim_person_id": 2,
                                "incident_kind": "ambush_killing",
                                "historical_importance": 0.70,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        ),
                        (
                            1001,
                            "outlaw_case_opened",
                            {"case_key": "property_crime:test"},
                        ),
                        (
                            1002,
                            "outlaw_case_opened",
                            {"case_key": "murder:test"},
                        ),
                        (
                            1002,
                            "outlaw_flight",
                            {"case_key": "property_crime:test"},
                        ),
                        (
                            1003,
                            "outlaw_pursuit",
                            {"case_key": "murder:test"},
                        ),
                        (
                            1004,
                            "outlaw_captured",
                            {"case_key": "murder:test"},
                        ),
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )
                region_key = conn.execute(
                    """
                    SELECT region_key FROM simulation_region_lookup
                    WHERE region_id = 'aeria_north'
                    """
                ).fetchone()["region_key"]
                settlement_key = conn.execute(
                    """
                    SELECT settlement_key FROM simulation_settlement_lookup
                    WHERE settlement_id = 'aeria_north:settlement:1'
                    """
                ).fetchone()["settlement_key"]
                ts = "2026-01-01T00:00:00+00:00"
                conn.execute(
                    """
                    INSERT INTO simulation_outlaw_refuges (
                        refuge_id, display_name, region_key, near_settlement_key,
                        status, founded_year, discovered_year, abandoned_year,
                        band_size, concealment_01, support_01, last_activity_year,
                        details_json, created_at, updated_at
                    )
                    VALUES (
                        'outlaw_refuge:aeria_north:1', 'The Thorn Brake',
                        ?, ?, 'active', 1002, 1003, NULL, 1, 0.60, 0.10,
                        1004, '{}', ?, ?
                    )
                    """,
                    (region_key, settlement_key, ts, ts),
                )
                conn.execute(
                    """
                    INSERT INTO simulation_outlaw_cases (
                        case_key, accused_person_id, offense_type, offense_kind,
                        status, source_event_id, source_event_key,
                        victim_person_id, target_person_id, severity_01,
                        knownness_01, pursuit_pressure_01, buyoff_power_01,
                        start_year, last_seen_year, expected_forget_year,
                        resolved_year, resolution, region_key, settlement_key,
                        refuge_id, custody_id, details_json, created_at, updated_at
                    )
                    VALUES (
                        'property_crime:test', 1, 'property_crime',
                        'storehouse_robbery', 'active', ?, 'source:property',
                        NULL, 2, 0.62, 0.71, 0.66, 0.05, 1001, 1004, 1011,
                        NULL, NULL, ?, ?, 'outlaw_refuge:aeria_north:1',
                        NULL, '{}', ?, ?
                    )
                    """,
                    (property_id, region_key, settlement_key, ts, ts),
                )
                conn.execute(
                    """
                    INSERT INTO simulation_outlaw_cases (
                        case_key, accused_person_id, offense_type, offense_kind,
                        status, source_event_id, source_event_key,
                        victim_person_id, target_person_id, severity_01,
                        knownness_01, pursuit_pressure_01, buyoff_power_01,
                        start_year, last_seen_year, expected_forget_year,
                        resolved_year, resolution, region_key, settlement_key,
                        refuge_id, custody_id, details_json, created_at, updated_at
                    )
                    VALUES (
                        'murder:test', 3, 'murder', 'ambush_killing',
                        'resolved', ?, 'source:murder', 2, NULL, 0.91, 0.80,
                        0.84, 0.02, 1002, 1004, 1020, 1004, 'captured',
                        ?, ?, NULL, 'outlaw_custody:murder:test', '{}', ?, ?
                    )
                    """,
                    (murder_id, region_key, settlement_key, ts, ts),
                )
                conn.execute(
                    """
                    INSERT INTO simulation_outlaw_custodies (
                        custody_id, case_key, person_id, custody_type, status,
                        site_settlement_key, region_key, start_year,
                        expected_release_year, release_year, severity_01,
                        details_json, created_at, updated_at
                    )
                    VALUES (
                        'outlaw_custody:murder:test', 'murder:test', 3,
                        'imprisonment', 'active', ?, ?, 1004, 1012, NULL,
                        0.91, '{}', ?, ?
                    )
                    """,
                    (settlement_key, region_key, ts, ts),
                )
                conn.commit()

                report = build_event_history_report(conn, save_path=save, sample_limit=0)

            rows = {
                (row.scope, row.metric): row
                for row in report.outlaw_outcome_summary
            }
            self.assertEqual(rows[("all", "source_crimes")].count, 2)
            self.assertEqual(rows[("all", "opened_cases")].count, 2)
            self.assertEqual(rows[("all", "opened_cases")].denominator, 2)
            self.assertAlmostEqual(rows[("all", "opened_cases")].rate or 0.0, 1.0)
            self.assertEqual(rows[("all", "outlaw_flight_events")].count, 1)
            self.assertAlmostEqual(
                rows[("all", "outlaw_flight_events")].rate or 0.0,
                0.5,
            )
            self.assertEqual(rows[("all", "resolution:captured")].count, 1)
            self.assertEqual(rows[("all", "active_refuges")].count, 1)
            self.assertEqual(rows[("all", "active_custodies")].count, 1)
            self.assertAlmostEqual(
                rows[("all", "years_to_resolution")].average_years or 0.0,
                2.0,
            )
            self.assertAlmostEqual(
                rows[("all", "custody_years")].average_years or 0.0,
                8.0,
            )
            summary = format_event_history_summary(report)
            self.assertIn("Outlaw Outcome Summary", summary)
            self.assertIn(
                "all / opened_cases: count=2 denominator=2 rate=1.0000",
                summary,
            )

    def test_write_report_outputs_tsv_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            root = Path(td)
            save = root / "save.sqlite"
            with closing(sqlite3.connect(save)) as conn:
                conn.row_factory = sqlite3.Row
                ensure_checkpoint_schema(conn)
                _insert_person(conn, 1, "Mira", "Vale")
                _insert_person(conn, 2, "Eno", "Reed")
                append_simulation_event_rows(
                    conn,
                    "default",
                    [
                        (
                            1010,
                            "murder",
                            {
                                "killer_person_id": 1,
                                "victim_person_id": 2,
                                "incident_kind": "feud_killing",
                                "motive": "old_grudge",
                                "historical_importance": 0.72,
                                "resource_pressure": 1.1,
                                "settlement_id": "aeria_north:settlement:1",
                                "region_id": "aeria_north",
                            },
                        )
                    ],
                    created_at="2026-01-01T00:00:00+00:00",
                )
                conn.commit()
                report = build_event_history_report(conn, save_path=save, sample_limit=5)

            out = root / "report"
            write_event_history_report(report, out)

            self.assertTrue((out / "summary.txt").exists())
            self.assertTrue((out / "event_counts_by_type.tsv").exists())
            self.assertTrue((out / "event_visibility_counts.tsv").exists())
            self.assertTrue((out / "tracked_incident_counts.tsv").exists())
            self.assertTrue((out / "event_metric_summaries.tsv").exists())
            self.assertTrue((out / "event_consequence_counts.tsv").exists())
            self.assertTrue((out / "event_consequence_metrics.tsv").exists())
            self.assertTrue((out / "outlaw_outcome_summary.tsv").exists())
            self.assertTrue((out / "public_chronicle_samples.tsv").exists())
            self.assertIn(
                "murder",
                (out / "event_counts_by_type.tsv").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "feud killing",
                (out / "public_chronicle_samples.tsv").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "murder\t1",
                (out / "tracked_incident_counts.tsv").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "all\tsource_crimes\t1",
                (out / "outlaw_outcome_summary.tsv").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
