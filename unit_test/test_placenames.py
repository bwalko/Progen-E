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
    _apply_surface_sound_laws,
    apply_probabilistic_sound_law_runs,
    generate_feature_name,
    probabilistic_sound_law_run_count,
    rewind_constructed_toponym_placeholder,
)
from library.geography import get_region
from library.placenames_generation import (
    SETTLEMENT_DISPLAY_HARD_CAP_LETTERS,
    SETTLEMENT_DISPLAY_TARGET_LETTERS,
    _compose_dual_affix,
    _display_letter_count,
    _locative_settlement_display,
    _should_use_locative_display,
    generate_settlement_name,
    region_ethnic_weights,
    seed_settlement_naming_for_region,
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

    def test_locative_display_suffix_is_probabilistic(self) -> None:
        rng = random.Random(1)
        self.assertFalse(
            _should_use_locative_display(
                rng=rng,
                anchor_kind="stream",
                primary_kind="river",
                probability=0.0,
            )
        )
        self.assertTrue(
            _should_use_locative_display(
                rng=rng,
                anchor_kind="stream",
                primary_kind="river",
                probability=1.0,
            )
        )
        self.assertFalse(
            _should_use_locative_display(
                rng=rng,
                anchor_kind="ridge",
                primary_kind="hill",
                probability=1.0,
            )
        )

    def test_locative_display_uses_simple_by_and_respects_budget(self) -> None:
        self.assertEqual(_locative_settlement_display("Oak", "coast", set()), "Oakby")
        self.assertEqual(_locative_settlement_display("Oak", "ford", set()), "Oakby")
        self.assertEqual(_locative_settlement_display("Oak", "well", set()), "Oakby")
        self.assertIsNone(_locative_settlement_display("Stonehaven", "coast", set()))
        for forbidden in ("havenby", "fordby", "wellby"):
            self.assertNotIn(
                forbidden,
                (_locative_settlement_display("Oak", "coast", set()) or "").casefold(),
            )



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

    def test_settlement_display_length_distribution_matches_budget(self) -> None:
        region = get_region("aeria_north", world="default", db_path=self.cfg)
        weights = {"Middle English": 1.0}
        lengths: list[int] = []
        by_family_count = 0
        forbidden_compound_count = 0
        for trial in range(240):
            g = generate_settlement_name(
                rng=random.Random(20260612 + trial),
                lex=self.lex,
                ethnic_weights=weights,
                region=region,
                prominent_person=None,
                db_path=self.cfg,
            )
            display = g.display_name.casefold()
            lengths.append(_display_letter_count(display))
            if display.endswith(("by", "bi", "byr")):
                by_family_count += 1
            if display.endswith(("havenby", "fordby", "wellby")):
                forbidden_compound_count += 1
        ordered = sorted(lengths)
        median = ordered[len(ordered) // 2]
        p90 = ordered[math.ceil(len(ordered) * 0.90) - 1]
        p95 = ordered[math.ceil(len(ordered) * 0.95) - 1]
        self.assertLessEqual(median, 9)
        self.assertLessEqual(p90, SETTLEMENT_DISPLAY_TARGET_LETTERS)
        self.assertLessEqual(p95, 13)
        self.assertLessEqual(max(ordered), SETTLEMENT_DISPLAY_HARD_CAP_LETTERS)
        self.assertLessEqual(by_family_count / len(lengths), 0.05)
        self.assertEqual(forbidden_compound_count, 0)

    def test_locative_anchor_keeps_etymology_without_forcing_by_display(self) -> None:
        class _Ctx:
            current_year = 100
            current_people_ids: set[int] = set()
            id_to_record = {}
            placename_rng_salt = 0
            world_map_seed = None
            settlements_by_id = {}

        region = get_region("boreas_peat_river", world="default", db_path=self.cfg)
        ctx = _Ctx()
        ctx.db_path = self.cfg
        ctx.world = "default"
        rng = make_settlement_name_rng("default", region.region_id)
        gen, _geo = seed_settlement_naming_for_region(
            world="default",
            region=region,
            ctx=ctx,
            lex=self.lex,
            rng=rng,
            settlement_slots=1,
            site_slot=1,
            locative_display_probability=0.0,
        )
        self.assertIn("by ", gen.etymology.casefold())
        self.assertFalse(gen.display_name.casefold().endswith("by"))

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
            "awaberk",
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

    def test_probabilistic_sound_law_run_count_buckets(self) -> None:
        class FixedRng:
            def __init__(self, value: float) -> None:
                self.value = value

            def random(self) -> float:
                return self.value

        self.assertEqual(probabilistic_sound_law_run_count(FixedRng(0.49)), 0)
        self.assertEqual(probabilistic_sound_law_run_count(FixedRng(0.50)), 1)
        self.assertEqual(probabilistic_sound_law_run_count(FixedRng(0.79)), 1)
        self.assertEqual(probabilistic_sound_law_run_count(FixedRng(0.80)), 2)
        self.assertEqual(probabilistic_sound_law_run_count(FixedRng(0.94)), 2)
        self.assertEqual(probabilistic_sound_law_run_count(FixedRng(0.95)), 3)

    def test_probabilistic_sound_law_can_skip_or_repeat(self) -> None:
        class FixedRng:
            def __init__(self, value: float) -> None:
                self.value = value

            def random(self) -> float:
                return self.value

        self.assertEqual(
            apply_probabilistic_sound_law_runs(
                "Akwā-bheorg",
                rng=FixedRng(0.25),
                branch="germanic",
            ),
            "Akwābheorg",
        )
        self.assertEqual(
            apply_probabilistic_sound_law_runs(
                "Akwā-bheorg",
                rng=FixedRng(0.50),
                branch="germanic",
            ),
            "awaberk",
        )
        self.assertEqual(
            apply_probabilistic_sound_law_runs(
                "Akwā-bheorg",
                rng=FixedRng(0.95),
                branch="germanic",
            ),
            "awaferk",
        )

    def test_surface_sound_laws_apply_common_repairs(self) -> None:
        self.assertEqual(
            _apply_surface_sound_laws("stanburg", branch="germanic", reverse=False),
            "stamburk",
        )
        self.assertEqual(
            _apply_surface_sound_laws("brgford", branch="germanic", reverse=False),
            "bregfort",
        )
        self.assertEqual(
            _apply_surface_sound_laws("akile", branch="germanic", reverse=False),
            "achil",
        )
        self.assertEqual(
            _apply_surface_sound_laws("lupeton", branch="italic_celtic", reverse=False),
            "lubedon",
        )
        self.assertEqual(
            _apply_surface_sound_laws("werbh", branch="germanic", reverse=False),
            "werf",
        )

    def test_surface_sound_laws_reduce_mutated_clusters(self) -> None:
        class FixedRng:
            def __init__(self, value: float) -> None:
                self.value = value

            def random(self) -> float:
                return self.value

        one_pass = rewind_constructed_toponym_placeholder(
            "Diddericwerf",
            branch="germanic",
        )
        self.assertEqual(one_pass, "titteriwerf")

        repeated = apply_probabilistic_sound_law_runs(
            "Diddericwerf",
            rng=FixedRng(0.95),
            branch="germanic",
        )
        self.assertEqual(repeated, "thideriwerf")
        self.assertLessEqual(len(repeated), len("Diddericwerf"))
        for cluster in ("thth", "dhdh", "ghw", "bhw", "dhw", "bh", "dh", "gh"):
            self.assertNotIn(cluster, repeated)


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
