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
            self.assertGreater(float(score["narrative_heat_volatility"]), 0.0)
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
            self.assertEqual(components["schema"], "person_archive_score_components.v3")
            self.assertEqual(components["score_version"], 3)
            self.assertEqual(components["formula_version"], 3)
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
            self.assertIn(score["recognition_scope"], components["bucket_labels"].values())
            self.assertIn("texture_flags", components)
            self.assertIn("score_breakdown", components)
            self.assertIn("channels", components["score_breakdown"])
            self.assertIn("ari_low_status_visibility", components["components"])

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
            self.assertIn("violet_marginalia_score", components["components"])
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
            self.assertEqual(explanation["score_version"], 3)
            self.assertTrue(str(explanation["summary"]).strip())
            self.assertFalse(
                str(explanation["summary"]).lower().startswith("interesting and remembered:")
            )
            self.assertIn("scores", explanation)
            self.assertIn("top_reasons", explanation)
            self.assertTrue(explanation["top_reasons"])
            self.assertIn("texture_flags", explanation)
            self.assertIn("score_breakdown", explanation)

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

    def test_score_v3_archetypes_scopes_breakdowns_and_texture_flags(self) -> None:
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            ensure_checkpoint_schema(conn)
            self._insert_archetype_people(conn)
            self._insert_archetype_context(conn)

            count = refresh_person_archive_scores(
                conn,
                person_ids=[10, 20, 30, 40, 50],
                simulation_year=1075,
            )
            self.assertEqual(count, 5)
            scores = {
                pid: load_person_archive_score(conn, pid)
                for pid in (10, 20, 30, 40, 50)
            }
            for pid, score in scores.items():
                self.assertIsNotNone(score, pid)
            ordinary = scores[10]
            tragic = scores[20]
            outlaw = scores[30]
            scandal = scores[40]
            achievement = scores[50]
            assert ordinary is not None
            assert tragic is not None
            assert outlaw is not None
            assert scandal is not None
            assert achievement is not None

            self.assertLess(float(ordinary["narrative_heat_total"]), 35.0)
            self.assertLess(
                float(ordinary["narrative_heat_total"]),
                float(tragic["narrative_heat_total"]),
            )
            self.assertEqual(ordinary["recognition_scope"], "household")
            self.assertFalse(json.loads(str(ordinary["texture_flags_json"])))

            self.assertGreaterEqual(float(tragic["narrative_heat_total"]), 40.0)
            self.assertLess(float(tragic["archive_recognition_index"]), 45.0)
            self.assertEqual(tragic["recognition_scope"], "household")
            tragic_flags = json.loads(str(tragic["texture_flags_json"]))
            self.assertTrue(
                any(flag["flag"] == "gifted_life_cut_short" for flag in tragic_flags)
            )

            self.assertGreaterEqual(float(outlaw["narrative_heat_total"]), 85.0)
            self.assertEqual(outlaw["recognition_scope"], "local_legal")
            self.assertGreater(float(outlaw["infamy_gap"]), float(outlaw["prestige_gap"]))
            outlaw_breakdown = json.loads(str(outlaw["score_breakdown_json"]))
            self.assertGreater(
                outlaw_breakdown["arc_bonuses"]["criminal_outlaw_arc"],
                0.0,
            )
            self.assertGreater(
                outlaw_breakdown["channels"]["criminal_outlaw_consequence"],
                20.0,
            )
            outlaw_flags = json.loads(str(outlaw["texture_flags_json"]))
            self.assertTrue(any(flag["flag"] == "infamous_pursuit" for flag in outlaw_flags))
            self.assertTrue(
                all(
                    {"flag", "strength", "evidence", "person_visible_text"}.issubset(flag)
                    for flag in outlaw_flags
                )
            )

            self.assertGreaterEqual(float(scandal["narrative_heat_total"]), 65.0)
            self.assertLess(
                float(scandal["narrative_heat_total"]),
                float(outlaw["narrative_heat_total"]),
            )
            self.assertEqual(scandal["recognition_scope"], "local_legal")
            scandal_breakdown = json.loads(str(scandal["score_breakdown_json"]))
            self.assertGreater(
                scandal_breakdown["arc_bonuses"]["relationship_scandal_arc"],
                0.0,
            )
            self.assertGreater(
                scandal_breakdown["channels"]["relationship_consequence"],
                10.0,
            )
            scandal_flags = json.loads(str(scandal["texture_flags_json"]))
            self.assertTrue(any(flag["flag"] == "scandal_afterlife" for flag in scandal_flags))

            self.assertGreaterEqual(float(achievement["narrative_heat_total"]), 85.0)
            self.assertIn(achievement["recognition_scope"], {"institutional", "regional"})
            self.assertGreater(float(achievement["archive_recognition_index"]), 60.0)
            self.assertGreater(float(achievement["prestige_gap"]), 20.0)
            achievement_breakdown = json.loads(str(achievement["score_breakdown_json"]))
            self.assertGreater(
                achievement_breakdown["arc_bonuses"]["public_achievement_arc"],
                0.0,
            )
            self.assertGreater(
                achievement_breakdown["channels"]["knowledge_legacy"],
                20.0,
            )
            self.assertGreater(
                achievement_breakdown["caps"]["repeat_pattern_damped_from"],
                achievement_breakdown["channels"]["repeat_pattern_volume"],
            )
            self.assertEqual(
                achievement_breakdown["channels"]["relationship_consequence"],
                0.0,
            )
            achievement_flags = json.loads(str(achievement["texture_flags_json"]))
            precarious = [
                flag for flag in achievement_flags
                if flag.get("flag") == "precarious_achievement"
            ]
            self.assertTrue(precarious)
            self.assertTrue(precarious[0]["evidence"])
            self.assertIn("Public innovation", precarious[0]["person_visible_text"])

            explanations = {
                pid: load_person_archive_explanation(conn, pid)
                for pid in (20, 30, 40, 50)
            }
            for pid, explanation in explanations.items():
                self.assertIsNotNone(explanation, pid)
                assert explanation is not None
                self.assertEqual(explanation["score_version"], 3)
                self.assertIn("recognition_scope", explanation["buckets"])
                self.assertIn("channels", explanation["score_breakdown"])
                self.assertTrue(explanation["top_reasons"])
            self.assertTrue(
                any(
                    "outlaw" in str(reason["label"]).lower()
                    for reason in explanations[30]["top_reasons"]  # type: ignore[index]
                )
            )
            self.assertTrue(
                any(
                    "relationship" in str(reason["label"]).lower()
                    or "scandal" in str(reason["label"]).lower()
                    for reason in explanations[40]["top_reasons"]  # type: ignore[index]
                )
            )
            self.assertTrue(
                any(
                    "innovation" in str(reason["explanation"]).lower()
                    for reason in explanations[50]["top_reasons"]  # type: ignore[index]
                )
            )
            self.assertFalse(
                any(
                    "faction" in str(reason["label"]).lower()
                    for reason in explanations[40]["top_reasons"]  # type: ignore[index]
                )
            )

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

    def _insert_archetype_people(self, conn: sqlite3.Connection) -> None:
        people = [
            (10, 1, None, None, 1, "Ord", "Gray", "male", 1000, None, "miller", "common", "low", 0.25, 0.6, {}),
            (11, 0, None, None, 1, "Mara", "Gray", "female", 1002, None, "spinner", "common", "low", 0.22, 0.6, {}),
            (12, 0, 10, 11, 1, "Joan", "Gray", "female", 1025, None, "dependent", "common", "low", 0.0, 0.5, {}),
            (13, 0, 10, 11, 1, "Will", "Gray", "male", 1028, None, "dependent", "common", "low", 0.0, 0.5, {}),
            (14, 0, 10, 11, 1, "Alice", "Gray", "female", 1031, None, "dependent", "common", "low", 0.0, 0.5, {}),
            (
                20,
                0,
                None,
                None,
                0,
                "Adelhaid",
                "Brief",
                "female",
                1000,
                1022,
                "child rearer",
                "premium",
                "low",
                0.15,
                0.35,
                {
                    "last_job": "scribe",
                    "career_fitness_score": 0.95,
                    "genome": {"memory": 96.0, "curiosity": 94.0, "patience": -91.0},
                    "genome_trait_phrases": ["gifted", "frail"],
                },
            ),
            (21, 0, None, 20, 0, "Els", "Brief", "female", 1018, 1020, "dependent", "common", "low", 0.0, 0.2, {}),
            (22, 0, None, 20, 0, "Mat", "Brief", "male", 1020, 1021, "dependent", "common", "low", 0.0, 0.2, {}),
            (23, 0, None, 20, 0, "Anne", "Brief", "female", 1021, 1021, "dependent", "common", "low", 0.0, 0.2, {}),
            (30, 0, None, None, 0, "Fulk", "Stone", "male", 1000, 1053, "outlaw thief", "common", "low", 0.08, 0.15, {"genome_trait_phrases": ["lawless", "raider"]}),
            (31, 0, None, None, 0, "Osric", "Hill", "male", 1002, 1040, "farmer", "common", "low", 0.2, 0.4, {}),
            (32, 0, None, None, 0, "Hugh", "Ford", "male", 1004, 1050, "reeve", "common", "middle", 0.35, 0.55, {}),
            (40, 0, None, None, 1, "Thezonus", "Tangle", "male", 1000, None, "merchant", "common", "middle", 0.45, 0.8, {"genome_trait_phrases": ["amorous", "pious"]}),
            (41, 0, None, None, 1, "Ysabel", "Tangle", "female", 1003, None, "weaver", "common", "middle", 0.3, 0.7, {}),
            (42, 0, None, None, 1, "Maud", "Reed", "female", 1004, None, "brewer", "common", "middle", 0.35, 0.7, {}),
            (43, 0, None, None, 1, "Cecily", "Ash", "female", 1005, None, "tailor", "common", "middle", 0.3, 0.7, {}),
            (44, 0, 40, 43, 1, "Anselm", "Tangle", "male", 1037, None, "dependent", "common", "middle", 0.0, 0.5, {}),
            (45, 0, 40, 43, 1, "Beatrice", "Tangle", "female", 1039, None, "dependent", "common", "middle", 0.0, 0.5, {}),
            (
                50,
                0,
                None,
                None,
                1,
                "Richard",
                "Glass",
                "male",
                1000,
                None,
                "inventor scribe",
                "premium",
                "middle-high",
                0.3,
                0.45,
                {
                    "genome": {"ingenuity": 98.0, "focus": 96.0},
                    "genome_trait_phrases": ["inventive", "precarious"],
                },
            ),
            (59, 0, None, None, 0, "Agnes", "Past", "female", 1001, 1030, "spinner", "common", "low", 0.2, 0.4, {}),
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

    def _insert_archetype_context(self, conn: sqlite3.Connection) -> None:
        event_ids = append_simulation_event_rows(
            conn,
            "default",
            [
                (1005, "couple_formed", {"person_a_id": 10, "person_b_id": 11}),
                (1015, "job_assigned", {"person_id": 20, "job": "scribe", "historical_importance": 0.2}),
                (1020, "job_lost", {"person_id": 20, "old_job": "scribe", "new_job": "child rearer"}),
                (1021, "household_childcare_shortfall", {"person_id": 20, "dependent_minor_ids": [21, 22, 23]}),
                (1022, "death", {"person_id": 20, "cause": "childbed fever"}),
                (1040, "murder", {"killer_person_id": 30, "victim_person_id": 31, "historical_importance": 0.8}),
                (1041, "outlaw_case_opened", {"person_id": 30, "accused_person_id": 30, "victim_person_id": 31, "pursuit_pressure_01": 0.9}),
                (1042, "outlaw_refuge_joined", {"person_id": 30, "outlaw_refuge_id": "refuge:crag"}),
                (1043, "outlaw_captured", {"person_id": 30, "accused_person_id": 30}),
                (1044, "outlaw_custody_released", {"person_id": 30, "accused_person_id": 30}),
                (1050, "murder", {"killer_person_id": 30, "victim_person_id": 32, "historical_importance": 0.9}),
                (1051, "outlaw_refuge_joined", {"person_id": 30, "outlaw_refuge_id": "refuge:crag"}),
                (1052, "outlaw_pursuit", {"person_id": 30, "accused_person_id": 30, "pursuit_pressure_01": 0.95}),
                (1053, "outlaw_killed", {"person_id": 30, "accused_person_id": 30}),
                (1030, "couple_formed", {"person_a_id": 40, "person_b_id": 41}),
                (1031, "paramour_formed", {"person_a_id": 40, "person_b_id": 42}),
                (1032, "paramour_formed", {"person_a_id": 40, "person_b_id": 43}),
                (1033, "paramour_ended", {"person_a_id": 40, "person_b_id": 42}),
                (1034, "couple_dissolved", {"person_a_id": 40, "person_b_id": 41}),
                (1035, "paramour_formed", {"person_a_id": 40, "person_b_id": 42}),
                (1036, "affair_scandal", {"accused_person_id": 40, "paramour_person_id": 43, "betrayed_partner_person_ids": [41]}),
                (1037, "inheritance_dispute", {"claimant_id": 40, "opposing_person_id": 41}),
                (1010, "knowledge_culture", {"creator_person_id": 50, "knowledge_domain": "optics", "historical_importance": 0.75}),
                (1012, "knowledge_culture", {"creator_person_id": 50, "knowledge_domain": "glass", "historical_importance": 0.8}),
                (1014, "knowledge_culture", {"creator_person_id": 50, "knowledge_domain": "mechanics", "historical_importance": 0.85}),
                (1015, "office_selection", {"holder_person_id": 50, "title_id": "master_scribe"}),
                (1016, "begging", {"person_id": 50}),
                (1017, "bankruptcy", {"person_id": 50}),
                (1018, "status_fall", {"person_id": 50}),
                (1019, "bankruptcy", {"person_id": 50}),
                (1020, "status_fall", {"person_id": 50}),
                (1021, "begging", {"person_id": 50}),
            ],
        )
        now = "2026-01-01T00:00:00+00:00"
        conn.executemany(
            """
            INSERT INTO simulation_couples (sort_order, person_a_id, person_b_id, surname_convention)
            VALUES (?, ?, ?, ?)
            """,
            [(1, 10, 11, "patrilineal")],
        )
        conn.execute(
            """
            INSERT INTO simulation_paramours (sort_order, person_a_id, person_b_id, surname_convention)
            VALUES (1, 50, 59, NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO simulation_legal_fallout (
                source_event_id, fallout_key, fallout_type, status,
                principal_person_id, opposing_person_id, severity, start_year,
                details_json, created_at, updated_at
            )
            VALUES (?, 'legitimacy:40', 'heir_legitimacy_rumor', 'active',
                    40, 41, 0.85, 1036, '{}', ?, ?)
            """,
            (event_ids[20], now, now),
        )
        conn.executemany(
            """
            INSERT INTO simulation_legal_fallout (
                source_event_id, fallout_key, fallout_type, status,
                principal_person_id, opposing_person_id, related_person_id,
                severity, start_year, details_json, created_at, updated_at
            )
            VALUES (?, ?, ?, 'active', 40, 41, ?, ?, 1037, '{}', ?, ?)
            """,
            [
                (event_ids[21], "inheritance:40", "inheritance_scandal", 44, 0.75, now, now),
                (event_ids[21], "legitimacy:40:child", "heir_legitimacy_rumor", 45, 0.65, now, now),
            ],
        )
        conn.executemany(
            """
            INSERT INTO simulation_reputation_marks (
                source_event_id, mark_key, person_id, reputation_axis,
                reputation_before, reputation_after, direction, mark_strength,
                mark_year, details_json, created_at, updated_at
            )
            VALUES (?, ?, 40, 'standing', ?, ?, 'negative', ?, 1037, '{}', ?, ?)
            """,
            [
                (event_ids[20], "scandal:40", "middle", "low", 0.75, now, now),
                (event_ids[21], "inheritance:40", "middle", "contested", 0.65, now, now),
                (event_ids[21], "legitimacy:40", "middle", "rumored", 0.55, now, now),
            ],
        )
        conn.executemany(
            """
            INSERT INTO simulation_outlaw_cases (
                case_key, accused_person_id, offense_type, offense_kind, status,
                source_event_id, source_event_key, victim_person_id, target_person_id,
                severity_01, knownness_01, pursuit_pressure_01, buyoff_power_01,
                start_year, last_seen_year, expected_forget_year, resolved_year,
                resolution, region_key, settlement_key, refuge_id, custody_id,
                details_json, created_at, updated_at
            )
            VALUES (?, 30, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0,
                    ?, ?, ?, ?, ?, NULL, NULL, ?, ?, '{}', ?, ?)
            """,
            [
                (
                    "outlaw:30:first",
                    "murder",
                    "murder",
                    "released",
                    event_ids[6],
                    "case:first",
                    31,
                    None,
                    0.95,
                    0.88,
                    0.82,
                    1041,
                    1044,
                    1050,
                    1044,
                    "released",
                    "refuge:crag",
                    "custody:30:first",
                    now,
                    now,
                ),
                (
                    "outlaw:30:second",
                    "murder",
                    "murder",
                    "resolved",
                    event_ids[12],
                    "case:second",
                    32,
                    None,
                    0.98,
                    0.92,
                    0.95,
                    1050,
                    1053,
                    1065,
                    1053,
                    "pursuit_death",
                    "refuge:crag",
                    None,
                    now,
                    now,
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO simulation_outlaw_custodies (
                custody_id, case_key, person_id, custody_type, status,
                site_settlement_key, region_key, start_year, expected_release_year,
                release_year, severity_01, details_json, created_at, updated_at
            )
            VALUES ('custody:30:first', 'outlaw:30:first', 30, 'imprisonment',
                    'released', NULL, NULL, 1043, 1050, 1044, 0.85, '{}', ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO simulation_office_seats (
                seat_id, polity_id, title_id, slot_index, status, holder_person_id
            )
            VALUES (2, 1, 'master_scribe', 0, 'active', 50)
            """
        )
        conn.execute(
            """
            INSERT INTO simulation_office_holdings (
                seat_id, holder_person_id, start_sim_year
            )
            VALUES (2, 50, 1015)
            """
        )
        conn.execute(
            """
            INSERT INTO simulation_institutions (
                institution_key, institution_type, status, focus_domain,
                founded_year, latest_year, founding_event_id, latest_event_id,
                founder_person_id, patron_person_id, strength, influence_score,
                details_json, created_at, updated_at
            )
            VALUES ('workshop:50', 'workshop', 'active', 'mechanics',
                    1014, 1015, ?, ?, 50, NULL, 0.75, 0.8, '{}', ?, ?)
            """,
            (event_ids[24], event_ids[25], now, now),
        )
        conn.executemany(
            """
            INSERT INTO simulation_innovation_discoveries (
                source_event_id, innovation_id, innovation_name, category,
                domain, era_id, discovery_year, historical_year,
                discoverer_person_id, patron_person_id, novelty_score,
                details_json, created_at
            )
            VALUES (?, ?, ?, 'craft', ?, 'medieval', ?, ?, 50, NULL, ?, '{}', ?)
            """,
            [
                (event_ids[22], "dark_room", "Dark-Room Lens", "optics", 1010, 1010, 0.94, now),
                (event_ids[23], "fine_glass", "Fine Glass Recipe", "glass", 1012, 1012, 0.88, now),
                (event_ids[24], "ratchet_press", "Ratchet Press", "mechanics", 1014, 1014, 0.91, now),
            ],
        )


if __name__ == "__main__":
    unittest.main()
