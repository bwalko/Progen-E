"""Tests for placename lexicon, joining, generation, and local geography helpers."""

from __future__ import annotations

import json
import math
import random
import tempfile
import unittest
from pathlib import Path

from library.config_import import load_all_csvs_into_sqlite
from library.ethnic_proto_placewords import (
    EthnicProtoPlacewordLexicon,
    generate_feature_name,
    rewind_constructed_toponym_placeholder,
)
from library.geography import get_region
from library.placenames_generation import (
    _compose_dual_affix,
    generate_settlement_name,
    region_ethnic_weights,
)
from library.placenames_lexicon import (
    PlacenameLexicon,
    apply_affix_template,
    format_toponym_display,
    join_tokens,
    normalize_placename_stem,
)
from library.settlement_local_geography import (
    build_local_region_graph,
    category_weights_for_region,
    make_region_geography_rng,
    make_settlement_name_rng,
    synthesize_features,
)


class TestJoinAndAffix(unittest.TestCase):
    def test_format_toponym_display_no_mid_word_caps(self) -> None:
        self.assertEqual(format_toponym_display("PorthYeqse"), "Porthyeqse")
        self.assertEqual(format_toponym_display("DUSJASTAD"), "Dusjastad")

    def test_join_duplicate_letter(self) -> None:
        self.assertEqual(join_tokens("Black", "lake"), "Blacklake")
        self.assertEqual(join_tokens("aa", "apple"), "aapple")

    def test_normalize_stem(self) -> None:
        self.assertEqual(normalize_placename_stem("Anna-Maria"), "AnnaMaria")

    def test_apply_affix_suffix_prefix(self) -> None:
        self.assertEqual(apply_affix_template("$dorf", "Test"), "Testdorf")
        self.assertEqual(apply_affix_template("West$", "fall"), "Westfall")

    def test_apply_affix_avoids_duplicate_morpheme(self) -> None:
        self.assertEqual(apply_affix_template("$grund", "Wiesengrund"), "Wiesengrund")
        self.assertEqual(apply_affix_template("$bourg", "Hagenbourg"), "Hagenbourg")
        self.assertEqual(apply_affix_template("West$", "Westfall"), "Westfall")

    def test_compose_dual_affix_rejects_two_leading_stem_slots(self) -> None:
        """``$'s tun`` + ``$fleot`` must not become a bare ``'s tunfleot`` (no personal name)."""
        self.assertIsNone(_compose_dual_affix("$'s tun", "$fleot"))
        self.assertIsNone(_compose_dual_affix("$ton", "$fleot"))

    def test_compose_dual_affix_prefix_suffix_cross_join(self) -> None:
        self.assertEqual(_compose_dual_affix("Pen$", "$mont"), "Penmont")
        self.assertEqual(_compose_dual_affix("Fleet$", "$ton"), "Fleeton")

    def test_compose_dual_affix_two_prefix_fallback(self) -> None:
        self.assertEqual(_compose_dual_affix("Ash$", "Mere$"), "AshMere")



class TestPlacenameGeneration(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._td.cleanup)
        root = Path(self._td.name)
        self.cfg = root / "config.sqlite"
        load_all_csvs_into_sqlite(self.cfg)
        self.lex = PlacenameLexicon.from_db(db_path=self.cfg)

    def test_settlement_generation_prefers_archaic_affix_column(self) -> None:
        row = next(
            r
            for r in self.lex.rows
            if r.culture == "Middle English"
            and r.original_meaning.strip().lower() == "bridge"
        )
        self.assertIn("$bridge", row.affix_variants)
        self.assertIn("$brycge", row.archaic_affix_variants)
        rng = random.Random(42)
        for _ in range(12):
            self.assertEqual(row.pick_settlement_affix_variant(rng), "$brycge")

    def test_deterministic_same_seed(self) -> None:
        region = get_region("aeria_north", world="default", db_path=self.cfg)
        weights = {"Middle English": 1.0}
        rng1 = random.Random(999)
        rng2 = random.Random(999)
        a = generate_settlement_name(
            rng=rng1,
            lex=self.lex,
            ethnic_weights=weights,
            region=region,
            prominent_person=None,
            dual_affix_probability=0.0,
            db_path=self.cfg,
        )
        b = generate_settlement_name(
            rng=rng2,
            lex=self.lex,
            ethnic_weights=weights,
            region=region,
            prominent_person=None,
            dual_affix_probability=0.0,
            db_path=self.cfg,
        )
        self.assertEqual(a.display_name, b.display_name)
        self.assertEqual(a.etymology, b.etymology)

    def test_settlement_name_rng_matches_world_region(self) -> None:
        region = get_region("boreas_west", world="default", db_path=self.cfg)
        weights = {"Old English": 1.0}
        r1 = make_settlement_name_rng("default", "boreas_west")
        r2 = make_settlement_name_rng("default", "boreas_west")
        g1 = generate_settlement_name(
            rng=r1,
            lex=self.lex,
            ethnic_weights=weights,
            region=region,
            prominent_person=None,
            dual_affix_probability=0.0,
            db_path=self.cfg,
        )
        g2 = generate_settlement_name(
            rng=r2,
            lex=self.lex,
            ethnic_weights=weights,
            region=region,
            prominent_person=None,
            dual_affix_probability=0.0,
            db_path=self.cfg,
        )
        self.assertEqual(g1.display_name, g2.display_name)

    def test_patronymic_uses_first_name_when_no_resident(self) -> None:
        """No prominent person → first name is sampled from the chosen culture and lives in etymology."""
        region = get_region("cyrene_river", world="default", db_path=self.cfg)
        for trial in range(15):
            rng = random.Random(8000 + trial)
            g = generate_settlement_name(
                rng=rng,
                lex=self.lex,
                ethnic_weights={"Ancient Greek": 1.0},
                region=region,
                prominent_person=None,
                dual_affix_probability=0.0,
                db_path=self.cfg,
            )
            self.assertEqual(g.mode, "patronymic")
            # Etymology is "<first_name> · <original_meaning>".
            self.assertIn(" · ", g.etymology, msg=f"got etym={g.etymology!r}")
            first_name, _sep, meaning = g.etymology.partition(" · ")
            self.assertTrue(first_name.strip(), msg=f"empty first name in {g.etymology!r}")
            self.assertEqual(meaning.strip(), (g.primary_meaning or "").strip())

    def test_dual_affix_etymology_lists_two_meanings(self) -> None:
        region = get_region("aeria_north", world="default", db_path=self.cfg)
        rng = random.Random(0)
        g = generate_settlement_name(
            rng=rng,
            lex=self.lex,
            ethnic_weights={"Old English": 1.0},
            region=region,
            prominent_person=None,
            dual_affix_probability=1.0,
            db_path=self.cfg,
        )
        self.assertEqual(g.mode, "dual_affix")
        self.assertIn(" · ", g.etymology)
        self.assertIsNotNone(g.secondary_meaning)
        # Same-culture dual-affix → two distinct meanings.
        self.assertNotEqual(
            (g.primary_meaning or "").strip().lower(),
            (g.secondary_meaning or "").strip().lower(),
        )
        self.assertNotIn("(", g.etymology)

    def test_display_name_never_contains_category_or_classifier_glosses(self) -> None:
        """Across many cultures and modes, generic English category/classifier words never leak into display.

        These are words that the user verified are **not** present in any ``Affix`` /
        ``Archaic Affix`` cell, so the only way they could appear in a display name is via
        gloss-as-stem leakage (which the new generator does not do).
        """
        region = get_region("aeria_north", world="default", db_path=self.cfg)
        cultures = ["Middle English", "Anglo-Norman", "Gaulish", "Old Norse", "Old French"]
        forbidden = {
            "mountain", "headland", "fortified", "fortification", "topography",
            "promontory", "settlement", "engineering", "sacred",
        }
        for trial in range(160):
            rng = random.Random(20000 + trial)
            culture = cultures[trial % len(cultures)]
            for dual_p in (0.0, 1.0):
                g = generate_settlement_name(
                    rng=random.Random(rng.random()),
                    lex=self.lex,
                    ethnic_weights={culture: 1.0},
                    region=region,
                    prominent_person=None,
                    dual_affix_probability=dual_p,
                    db_path=self.cfg,
                )
                d = g.display_name.casefold()
                for word in forbidden:
                    self.assertNotIn(word, d, msg=f"display={g.display_name!r} leaks {word!r}")
                self.assertNotIn(g.primary_category.casefold(), d)
                if g.secondary_category:
                    self.assertNotIn(g.secondary_category.casefold(), d)

    def test_proto_placeword_feature_name_uses_ethnic_feature_type(self) -> None:
        rng = random.Random(31001)
        proto = EthnicProtoPlacewordLexicon.from_db(db_path=self.cfg)
        g = generate_feature_name(
            rng=rng,
            proto=proto,
            placenames=self.lex,
            ethnic_weights={"Middle English": 1.0},
            local_kind="river",
        )
        self.assertIsNotNone(g)
        assert g is not None
        self.assertEqual(g.ethnic, "Middle English")
        self.assertEqual(g.feature_type, "river")
        self.assertTrue(g.normalized_form)
        self.assertNotIn(" ", g.display_name)
        self.assertIn(g.core_concept, g.etymology)

    def test_ie_modulo_sound_law_examples(self) -> None:
        self.assertEqual(
            rewind_constructed_toponym_placeholder(
                "Akwā-bheorg",
                branch="germanic",
            ),
            "ahwaberk",
        )
        self.assertEqual(
            rewind_constructed_toponym_placeholder(
                "Akwā-bheorg",
                branch="italic_celtic",
            ),
            "aquaborg",
        )
        self.assertEqual(
            rewind_constructed_toponym_placeholder("dhūn", branch="italic_celtic"),
            "dun",
        )
        self.assertEqual(
            rewind_constructed_toponym_placeholder(
                "Ahwaberk",
                branch="germanic",
                reverse=True,
            ),
            "akwabheorg",
        )


class TestLocalGeography(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._td.cleanup)
        root = Path(self._td.name)
        self.cfg = root / "config.sqlite"
        load_all_csvs_into_sqlite(self.cfg)

    def test_category_weights_sum_one(self) -> None:
        region = get_region("aeria_port", world="default", db_path=self.cfg)
        w = category_weights_for_region(region)
        self.assertAlmostEqual(sum(w.values()), 1.0, places=5)

    def test_local_graph_json_roundtrip(self) -> None:
        region = get_region("aeria_north", world="default", db_path=self.cfg)
        rng = make_region_geography_rng("default", "aeria_north", slot=0)
        graph = build_local_region_graph(
            world="default",
            region=region,
            rng=rng,
            settlement_slots=1,
            primary_meaning="forest",
            primary_category="Topography",
        )
        data = json.loads(graph.to_json())
        self.assertEqual(data["region_id"], "aeria_north")
        self.assertTrue(data["features"])
        self.assertTrue(data["borders"])
        self.assertEqual(len(data["settlements"]), 1)

    def test_synthesize_features_deterministic(self) -> None:
        region = get_region("aeria_north", world="default", db_path=self.cfg)
        rng1 = make_region_geography_rng("default", "aeria_north", slot=0)
        rng2 = make_region_geography_rng("default", "aeria_north", slot=0)
        f1 = synthesize_features(region, rng1, n_features=6)
        f2 = synthesize_features(region, rng2, n_features=6)
        self.assertEqual(len(f1), len(f2))
        for a, b in zip(f1, f2):
            self.assertEqual(a.kind, b.kind)
            self.assertAlmostEqual(a.x, b.x, places=10)

    def test_settlement_anchors_are_near_town_pins(self) -> None:
        region = get_region("boreas_peat_river", world="default", db_path=self.cfg)
        rng = make_region_geography_rng("default", "boreas_peat_river", slot=0)
        graph = build_local_region_graph(
            world="default",
            region=region,
            rng=rng,
            settlement_slots=3,
            primary_meaning="ford",
            primary_category="Topography",
        )
        by_id = {f.feature_id: f for f in graph.features}

        for pin in graph.settlements:
            anchor = by_id[str(pin.anchor_feature_id)]
            distance = math.hypot(pin.x - anchor.x, pin.y - anchor.y)
            self.assertLessEqual(distance, 0.16)
            self.assertGreater(distance, 0.01)

    def test_feature_kinds_follow_region_terrain(self) -> None:
        coast = get_region("aeria_port", world="default", db_path=self.cfg)
        river = get_region("boreas_peat_river", world="default", db_path=self.cfg)
        coast_features = {
            f.kind
            for f in synthesize_features(
                coast,
                make_region_geography_rng("default", "aeria_port", slot=0),
                n_features=8,
            )
        }
        river_features = {
            f.kind
            for f in synthesize_features(
                river,
                make_region_geography_rng("default", "boreas_peat_river", slot=0),
                n_features=8,
            )
        }

        self.assertTrue(coast_features & {"coast", "bay", "harbor", "cliff"})
        self.assertTrue(river_features & {"river", "stream", "ford", "bridge", "marsh", "bog"})

    def test_local_graph_features_get_stable_proto_names(self) -> None:
        region = get_region("boreas_peat_river", world="default", db_path=self.cfg)
        lex = PlacenameLexicon.from_db(db_path=self.cfg)
        rng1 = make_region_geography_rng("default", "boreas_peat_river", slot=0)
        rng2 = make_region_geography_rng("default", "boreas_peat_river", slot=0)
        g1 = build_local_region_graph(
            world="default",
            region=region,
            rng=rng1,
            settlement_slots=1,
            primary_meaning="river",
            primary_category="Topography",
            db_path=self.cfg,
            ethnic_weights={"Middle English": 1.0},
            placename_lexicon=lex,
        )
        g2 = build_local_region_graph(
            world="default",
            region=region,
            rng=rng2,
            settlement_slots=1,
            primary_meaning="river",
            primary_category="Topography",
            db_path=self.cfg,
            ethnic_weights={"Middle English": 1.0},
            placename_lexicon=lex,
        )
        named1 = [(f.kind, f.display_name, f.name_ethnic) for f in g1.features if f.display_name]
        named2 = [(f.kind, f.display_name, f.name_ethnic) for f in g2.features if f.display_name]
        anchor_ids = {p.anchor_feature_id for p in g1.settlements if p.anchor_feature_id}
        named_ids = {f.feature_id for f in g1.features if f.display_name}
        self.assertTrue(named1)
        self.assertEqual(named1, named2)
        self.assertLessEqual(len(named_ids), len(anchor_ids))
        self.assertTrue(named_ids.issubset(anchor_ids))
        self.assertEqual(len({item[1].casefold() for item in named1}), len(named1))
        self.assertTrue(all(item[2] == "Middle English" for item in named1))


class TestRegionEthnicWeights(unittest.TestCase):
    def test_fallback_species_weights(self) -> None:
        """Mock-free: region_ethnic_weights uses species table when no residents."""

        class _Ctx:
            db_path = None
            current_year = 1000
            current_people_ids: set[int] = set()
            id_to_record = {}

        self._td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._td.cleanup)
        cfg = Path(self._td.name) / "config.sqlite"
        load_all_csvs_into_sqlite(cfg)
        ctx = _Ctx()
        ctx.db_path = cfg
        w = region_ethnic_weights(ctx, "any_region", db_path=cfg)
        self.assertTrue(w)
        self.assertAlmostEqual(sum(w.values()), 1.0, places=5)
