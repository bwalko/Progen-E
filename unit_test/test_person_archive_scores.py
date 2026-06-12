from __future__ import annotations

import json
import sqlite3
import unittest
from contextlib import closing

from library.person_archive_scores import (
    load_person_archive_explanation,
    load_person_archive_score,
    refresh_person_archive_scores,
    top_person_archive_scores,
)
from library.world_save import append_simulation_event_rows, ensure_checkpoint_schema


class TestPersonArchiveScores(unittest.TestCase):
    def test_refresh_scores_components_and_indexed_retrieval(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            ensure_checkpoint_schema(conn)
            self._insert_people(conn)
            event_ids = append_simulation_event_rows(
                conn,
                "default",
                [
                    (
                        1020,
                        "murder",
                        {
                            "killer_person_id": 1,
                            "victim_person_id": 2,
                            "witness_person_ids": [3],
                            "historical_importance": 0.9,
                        },
                    ),
                    (
                        1022,
                        "knowledge_culture",
                        {
                            "creator_person_id": 1,
                            "patron_person_id": 3,
                            "knowledge_domain": "law",
                            "historical_importance": 0.8,
                        },
                    ),
                    (
                        1024,
                        "office_selection",
                        {
                            "holder_person_id": 1,
                            "previous_holder_id": 3,
                            "title_id": "magistrate",
                        },
                    ),
                    (
                        1025,
                        "couple_formed",
                        {"person_a_id": 1, "person_b_id": 3},
                    ),
                    (
                        1088,
                        "settlement_moved",
                        {
                            "moved_person_id": 1,
                            "from_settlement_id": "aeria:settlement:1",
                            "to_settlement_id": "borea:settlement:1",
                            "from_region_id": "aeria",
                            "to_region_id": "borea",
                            "cross_region": True,
                            "move_reason": "office_selection",
                        },
                    ),
                ],
            )
            self._insert_archive_context(conn, event_ids)

            selected_count = refresh_person_archive_scores(
                conn, person_ids=[1], simulation_year=1100
            )
            self.assertEqual(selected_count, 1)
            self.assertIsNone(load_person_archive_score(conn, 2))

            full_count = refresh_person_archive_scores(conn, simulation_year=1100)
            self.assertEqual(full_count, 4)
            score = load_person_archive_score(conn, 1)
            victim_score = load_person_archive_score(conn, 2)
            quiet_score = load_person_archive_score(conn, 4)

            self.assertIsNotNone(score)
            self.assertIsNotNone(victim_score)
            self.assertIsNotNone(quiet_score)
            assert score is not None
            assert victim_score is not None
            assert quiet_score is not None

            self.assertGreater(float(score["narrative_heat_total"]), 70.0)
            self.assertGreater(float(score["archive_recognition_index"]), 45.0)
            self.assertGreater(float(score["narrative_heat_events"]), 25.0)
            self.assertGreater(float(score["narrative_heat_consequences"]), 20.0)
            self.assertGreater(float(score["narrative_heat_volatility"]), 5.0)
            self.assertGreater(float(score["ari_official_status"]), 10.0)
            self.assertGreater(float(score["ari_knowledge_art"]), 10.0)
            self.assertGreater(
                float(score["narrative_heat_total"]),
                float(quiet_score["narrative_heat_total"]),
            )
            self.assertGreater(
                float(score["archive_recognition_index"]),
                float(victim_score["archive_recognition_index"]),
            )
            self.assertIn("remembered", str(score["recognition_bucket"]))

            components = json.loads(str(score["component_json"]))
            self.assertEqual(components["schema"], "person_archive_score_components.v2")
            self.assertEqual(components["score_version"], 2)
            self.assertEqual(components["formula_version"], 2)
            self.assertTrue(
                {
                    "totals",
                    "components",
                    "bucket_labels",
                    "top_event_types",
                    "top_roles",
                    "evidence_counts",
                    "data_caveats",
                    "top_reason_summaries",
                    "source_ids",
                    "summary",
                }.issubset(components.keys())
            )
            self.assertGreaterEqual(components["event_count"], 5)
            self.assertIn("narrative_heat_events", components["components"])
            self.assertIn("ari_official_status", components["components"])
            self.assertIn("archive_quadrant", components["bucket_labels"])
            self.assertTrue(components["summary"])
            self.assertTrue(components["top_reason_summaries"])
            self.assertTrue(components["flags"]["criminal_role"])
            self.assertTrue(components["flags"]["official_role"])

            reason_rows = conn.execute(
                """
                SELECT component_key, axis, contribution, source_kind, label
                FROM simulation_person_archive_score_reasons
                WHERE person_id = 1
                """
            ).fetchall()
            self.assertTrue(
                any(
                    row["component_key"] == "narrative_heat_events"
                    and row["source_kind"] == "event"
                    and float(row["contribution"]) > 0.0
                    for row in reason_rows
                )
            )
            self.assertTrue(
                any(
                    row["component_key"] == "narrative_heat_contradictions"
                    and float(row["contribution"]) > 0.0
                    for row in reason_rows
                )
            )
            self.assertTrue(
                any(
                    str(row["axis"]) == "ari"
                    and float(row["contribution"]) > 0.0
                    for row in reason_rows
                )
            )
            self.assertTrue(
                any(
                    row["component_key"] == "violet_marginalia_score"
                    and float(row["contribution"]) > 0.0
                    for row in reason_rows
                )
            )
            quiet_reasons = conn.execute(
                """
                SELECT component_key, axis, contribution
                FROM simulation_person_archive_score_reasons
                WHERE person_id = 4
                """
            ).fetchall()
            self.assertTrue(
                any(
                    row["component_key"] == "ari_suppression_obscurity_penalty"
                    and row["axis"] == "obscurity"
                    and float(row["contribution"]) < 0.0
                    for row in quiet_reasons
                )
            )
            hidden_reason = conn.execute(
                """
                SELECT label, explanation
                FROM simulation_person_archive_score_reasons
                WHERE component_key = 'hidden_heat'
                  AND contribution > 0.0
                LIMIT 1
                """
            ).fetchone()
            self.assertIsNotNone(hidden_reason)

            explanation = load_person_archive_explanation(conn, 1)
            self.assertIsNotNone(explanation)
            assert explanation is not None
            self.assertEqual(explanation["score_version"], 2)
            self.assertIn("remembered", str(explanation["summary"]).lower())
            self.assertIn("scores", explanation)
            self.assertIn("top_reasons", explanation)
            self.assertTrue(explanation["top_reasons"])

            top = top_person_archive_scores(
                conn, order_by="narrative_heat_total", limit=1
            )
            self.assertEqual(top[0]["person_id"], 1)

            plan = conn.execute(
                """
                EXPLAIN QUERY PLAN
                SELECT *
                FROM simulation_person_archive_scores
                WHERE person_id = 1
                """
            ).fetchall()
            details = [str(row["detail"]).upper() for row in plan]
            self.assertTrue(any("PRIMARY KEY" in detail for detail in details))

    def _insert_people(self, conn: sqlite3.Connection) -> None:
        people = [
            (
                1,
                1,
                None,
                None,
                1,
                "Ari",
                "Vale",
                "female",
                1000,
                None,
                "court diplomat",
                "premium",
                "middle-high",
                0.91,
                1.4,
                {
                    "genome_trait_phrases": [
                        "lawless",
                        "diplomat",
                        "artist",
                        "murder",
                    ],
                    "genome_composite_names": ["witty", "oblivious"],
                    "genome": {"temper": 95.0, "modesty": -92.0},
                },
            ),
            (
                2,
                0,
                None,
                None,
                0,
                "Bren",
                "Vale",
                "male",
                1005,
                1020,
                "farmer",
                "common",
                "low",
                0.2,
                0.4,
                {"genome": {}},
            ),
            (
                3,
                0,
                None,
                None,
                1,
                "Cato",
                "Reed",
                "male",
                1001,
                None,
                "patron",
                "premium",
                "middle-high",
                0.75,
                1.1,
                {"genome": {}},
            ),
            (
                4,
                0,
                1,
                3,
                1,
                "Dara",
                "Vale",
                "female",
                1030,
                None,
                "weaver",
                "common",
                "low",
                0.25,
                0.6,
                {"genome": {}},
            ),
        ]
        conn.executemany(
            """
            INSERT INTO simulation_people (
                person_id, is_founder, father_id, mother_id, is_alive,
                first_name, last_name, gender, ethnic, species, birthyear,
                deathyear, job, job_tier, status_tendency, job_prosperity_01,
                household_prosperity, person_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'test', 'human', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    person_id,
                    is_founder,
                    father_id,
                    mother_id,
                    is_alive,
                    first_name,
                    last_name,
                    gender,
                    birthyear,
                    deathyear,
                    job,
                    job_tier,
                    status,
                    job_prosperity,
                    household_prosperity,
                    json.dumps(payload, separators=(",", ":")),
                )
                for (
                    person_id,
                    is_founder,
                    father_id,
                    mother_id,
                    is_alive,
                    first_name,
                    last_name,
                    gender,
                    birthyear,
                    deathyear,
                    job,
                    job_tier,
                    status,
                    job_prosperity,
                    household_prosperity,
                    payload,
                ) in people
            ],
        )

    def _insert_archive_context(
        self, conn: sqlite3.Connection, event_ids: list[int]
    ) -> None:
        now = "2026-01-01T00:00:00+00:00"
        conn.execute(
            """
            INSERT INTO simulation_office_seats (
                seat_id, polity_id, title_id, slot_index, status, holder_person_id
            )
            VALUES (1, 1, 'magistrate', 0, 'active', 1)
            """
        )
        conn.execute(
            """
            INSERT INTO simulation_office_holdings (
                seat_id, holder_person_id, start_sim_year
            )
            VALUES (1, 1, 1024)
            """
        )
        conn.execute(
            """
            INSERT INTO simulation_dynasties (
                dynasty_id, founder_person_id, house_name, founded_sim_year
            )
            VALUES (1, 1, 'Vale', 1024)
            """
        )
        conn.execute(
            """
            INSERT INTO simulation_obligations (
                source_event_id, obligation_key, obligation_type, status,
                owed_by_person_id, owed_to_person_id, strength, start_year,
                details_json, created_at, updated_at
            )
            VALUES (?, 'debt:1', 'patronage_debt', 'active', 1, 3, 0.7, 1022, '{}', ?, ?)
            """,
            (event_ids[1], now, now),
        )
        conn.execute(
            """
            INSERT INTO simulation_reputation_marks (
                source_event_id, mark_key, person_id, reputation_axis,
                reputation_before, reputation_after, direction, mark_strength,
                mark_year, details_json, created_at, updated_at
            )
            VALUES (?, 'status:1', 1, 'status', 'low', 'middle-high', 'positive', 0.8, 1022, '{}', ?, ?)
            """,
            (event_ids[1], now, now),
        )
        conn.execute(
            """
            INSERT INTO simulation_legal_fallout (
                source_event_id, fallout_key, fallout_type, status,
                principal_person_id, opposing_person_id, severity, start_year,
                details_json, created_at, updated_at
            )
            VALUES (?, 'case:1', 'murder_inquiry', 'active', 1, 2, 0.9, 1020, '{}', ?, ?)
            """,
            (event_ids[0], now, now),
        )
        conn.execute(
            """
            INSERT INTO simulation_faction_memory (
                source_event_id, memory_key, memory_type, status, faction_a_key,
                principal_person_id, opposing_person_id, polarity, strength,
                start_year, details_json, created_at, updated_at
            )
            VALUES (?, 'grievance:1', 'blood_grievance', 'active', 'family:2',
                    1, 2, 'negative', 0.8, 1020, '{}', ?, ?)
            """,
            (event_ids[0], now, now),
        )
        conn.execute(
            """
            INSERT INTO simulation_institutions (
                institution_key, institution_type, status, focus_domain,
                founded_year, latest_year, founding_event_id, founder_person_id,
                patron_person_id, strength, influence_score, details_json,
                created_at, updated_at
            )
            VALUES ('school:1', 'school', 'active', 'law', 1022, 1022, ?, 1,
                    3, 0.7, 0.8, '{}', ?, ?)
            """,
            (event_ids[1], now, now),
        )
        conn.execute(
            """
            INSERT INTO simulation_innovation_discoveries (
                source_event_id, innovation_id, innovation_name, category,
                domain, era_id, discovery_year, historical_year,
                discoverer_person_id, patron_person_id, novelty_score,
                details_json, created_at
            )
            VALUES (?, 'case_law', 'Case Law', 'law', 'law', 'medieval',
                    1022, 1022, 1, 3, 0.9, '{}', ?)
            """,
            (event_ids[1], now),
        )


if __name__ == "__main__":
    unittest.main()
