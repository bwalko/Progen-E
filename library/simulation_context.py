"""Shared, in-memory simulation context for config-backed generation."""

from __future__ import annotations

import random
import sqlite3
import time
import json
from contextlib import closing
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import TracebackType
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4

import numpy as np

from library import simulation_timing
from library.config_import import refresh_world_config_from_csv
from library.passive_population import (
    PassiveCohort,
    PassivePerson,
    PassivePersonRecord,
    passive_person_to_detailed_person,
)
from library.person import Person
from library.geography import get_region, list_regions, region_connectivity_score
from library.random_names import preload_name_cache
from library.placenames_lexicon import PlacenameLexicon, preload_placename_cache
from library.placenames_generation import region_ethnic_weights, seed_settlement_naming_for_region
from library.settlement_local_geography import (
    build_local_region_graph,
    make_region_geography_rng,
    make_settlement_name_rng,
)
from library.random_traits import (
    _as_int,
    _connect,
    _species_rows,
    infer_life_stage_from_age,
    preload_trait_cache,
)
from library.settlements import (
    SettlementState,
    evolve_settlement,
    make_settlement_id,
    next_settlement_sequence,
    roll_abandon_this_year,
)
from library.simulation_store import SimulationFileStore
from library.world_bootstrap import delete_save_database
from library.world_bootstrap import ensure_world_directories
from library.world_bootstrap import history_sim_reset_world_from_env
from library.world_bootstrap import save_has_simulation_people
from library.world_bootstrap import should_refresh_world_config_auto
from library.world_paths import config_db_path as world_folder_config_db_path
from library.world_paths import derive_save_db_path_from_config
from library.world_map_geometry import (
    WorldMapGeometry,
    build_world_map_geometry,
    micro_cell_id_for_world_point,
    project_local_point_to_region_footprint,
)
from library.world_time import ensure_world_state, reset_world_time, set_world_current_year
from library.world_save import (
    checkpoint_simulation_to_save,
    clear_world_checkpoint,
    ensure_checkpoint_schema_for_file,
    maybe_import_run_store_events_csv,
    try_load_simulation_checkpoint,
    read_world_map_seed,
    write_world_map_seed,
)

# Default cap for person-level samples that drive settlement/regional behavior.
#
# Use this for bounded "what does this place/polity tend to do?" decisions:
# leadership candidate scoring, regional mood/social signal reads, usurpation
# claimant scans, and similar behavior pools where a representative-ish sample is
# enough and exact all-person scans would explode at large population sizes.
#
# Do not use this for accounting or conservation logic: alive counts, births,
# deaths, migration totals, checkpoint persistence, demographic summaries,
# treasury/food/resource totals, or any code where every person must be counted.
# Groups at or below the cap should always use the full group.
DEFAULT_DECISION_SAMPLE_SIZE = 1_000


@dataclass
class SimulationPersonRecord:
    """Mutable runtime record for a person tracked in simulation state."""

    person_id: int
    person: Person
    is_founder: bool
    father_id: int | None = None
    mother_id: int | None = None


@dataclass
class AliveCensusCache:
    """Per-context index of alive residents by current settlement and region."""

    by_settlement: dict[str, list[SimulationPersonRecord]]
    by_region: dict[str, list[SimulationPersonRecord]]
    count_by_settlement: dict[str, int]
    count_by_region: dict[str, int]


@dataclass
class AlivePersonColumns:
    """Columnar alive-person snapshot for fast annual candidate masks."""

    year: int
    person_ids: np.ndarray
    ages: np.ndarray
    birthyears: np.ndarray
    gender_codes: np.ndarray
    settlement_codes: np.ndarray
    region_codes: np.ndarray
    is_founder: np.ndarray
    has_partner: np.ndarray
    has_paramour: np.ndarray
    attractiveness_01: np.ndarray
    job_prosperity_01: np.ndarray
    settlement_code_by_id: dict[str, int]
    region_code_by_id: dict[str, int]
    settlement_id_by_code: dict[int, str]
    region_id_by_code: dict[int, str]

    def person_ids_for_mask(self, mask: np.ndarray) -> list[int]:
        return [int(pid) for pid in self.person_ids[mask]]


@dataclass(frozen=True)
class PendingSettlementMove:
    """Deferred residence change applied at a year boundary."""

    person_id: int
    to_settlement_id: str
    move_reason: str
    requested_year: int
    apply_year: int
    from_settlement_id: str | None = None
    source_event: str | None = None
    group_id: str | None = None


@dataclass
class SimulationContext:
    """Reusable simulation context with preloaded config and runtime state.

    Entering via ``with SimulationContext.create(...) as ctx`` (or ``with ctx`` after
    ``create``) calls :meth:`finalize_run` on exit so ``save.sqlite`` receives a full
    checkpoint including pending domain events (unless finalize was already invoked).
    """

    db_path: Path
    save_db_path: Path
    world: str = "default"
    simulation_start_year: int = 0
    history_equivalent_start_year: int = 0
    current_year: int | None = None
    next_person_id: int = 1
    people: list[SimulationPersonRecord] = field(default_factory=list)
    passive_people: dict[int, PassivePersonRecord] = field(default_factory=dict)
    passive_cohorts: list[PassiveCohort] = field(default_factory=list)
    current_people_ids: set[int] = field(default_factory=set)
    couples: list[tuple[int, int]] = field(default_factory=list)
    paramours: list[tuple[int, int]] = field(default_factory=list)
    surname_conventions_by_pair: dict[tuple[int, int], str] = field(default_factory=dict)
    id_to_record: dict[int, SimulationPersonRecord] = field(default_factory=dict)
    event_queue: list[dict[str, Any]] = field(default_factory=list)
    pending_settlement_moves: list[PendingSettlementMove] = field(default_factory=list)
    mortality_milestones: list[dict[str, Any]] = field(default_factory=list)
    _mortality_index: int = 0
    file_store: SimulationFileStore | None = None
    settlements_by_id: dict[str, SettlementState] = field(default_factory=dict)
    settlement_ids_by_region: dict[str, list[str]] = field(default_factory=dict)
    _pending_simulation_events: list[tuple[int | None, str, dict]] = field(
        default_factory=list
    )
    # Normal save events store common place references in integer columns and omit
    # duplicate region/settlement slugs from payload_json. Enable for debugging
    # runs that need the raw, self-contained event payloads.
    verbose_event_logging: bool = False
    checkpoint_full_snapshot_every_n_years: int | None = 10
    # Per-context override for DEFAULT_DECISION_SAMPLE_SIZE. Keep this on the
    # context so experiments can tune decision-pool cost without changing exact
    # population accounting or save/load behavior.
    decision_sample_size: int = DEFAULT_DECISION_SAMPLE_SIZE
    placename_rng_salt: int = 0
    world_map_seed: str = ""
    active_region_ids: frozenset[str] | None = None
    foundation_colony_region_order: tuple[str, ...] | None = None
    _run_finalized: bool = field(default=False, repr=False)
    # Working-set policy: recent dead stay in RAM for relationship/census; older only in save.sqlite.
    working_set_dead_retention_years: int = 20
    spinoff_min_mother_settlement_population: int = 18
    spinoff_cooldown_years: int = 5
    #: How many separate births must pass the spinoff RNG gates before a new settlement
    #: is founded in that region (colonist families accrue, then one wave moves together).
    spinoff_families_required: int = 3
    last_spinoff_sim_year_by_region: dict[str, int] = field(default_factory=dict)
    spinoff_pending_families_by_region: dict[str, int] = field(default_factory=dict)
    # Random-walk multiplier on config ``carrying_capacity`` (see :meth:`effective_regional_population_cap`).
    region_effective_cap_multiplier: dict[str, float] = field(default_factory=dict)
    # Lazy geographic display names for regions (see ``library.place_namer``).
    region_display_label_overrides: dict[str, str] = field(default_factory=dict)
    # ``"geo"`` vs ``"city"`` — drives hysteresis when reverting a city-takeover label.
    region_label_source: dict[str, str] = field(default_factory=dict)
    # Consecutive years the dominant-city takeover condition failed (per region).
    region_city_rename_miss_streak: dict[str, int] = field(default_factory=dict)
    # Regional economic state (pooled prosperity + tax treasury); persisted on ``simulation_regions``.
    region_prosperity_pool: dict[str, float] = field(default_factory=dict)
    region_treasury_balance: dict[str, float] = field(default_factory=dict)
    # Government / polity state (``library.simulation_government``, ``library.government_checkpoint``).
    gov_polities: dict[int, Any] = field(default_factory=dict)
    gov_territory_rows: list[Any] = field(default_factory=list)
    gov_office_seats: dict[int, Any] = field(default_factory=dict)
    gov_dynasties: dict[int, Any] = field(default_factory=dict)
    gov_alliances: list[Any] = field(default_factory=list)
    gov_campaigns: list[Any] = field(default_factory=list)
    next_gov_polity_id: int = 1
    next_gov_seat_id: int = 1
    next_gov_dynasty_id: int = 1
    next_gov_campaign_id: int = 1
    next_gov_alliance_id: int = 1
    _alive_census_cache: AliveCensusCache | None = field(default=None, repr=False)
    _alive_columns_cache: tuple[int, AlivePersonColumns] | None = field(default=None, repr=False)
    _annual_care_indexes_cache: tuple[int, Any] | None = field(default=None, repr=False)
    _annual_resource_facts_cache: tuple[int, Any] | None = field(default=None, repr=False)
    _world_map_geometry_cache: WorldMapGeometry | None = field(default=None, repr=False)
    _regional_base_cap_cache: dict[str, int] = field(default_factory=dict, repr=False)
    _species_life_stage_rows_cache: tuple[
        str, dict[tuple[str, str], Mapping[str, Any]]
    ] | None = field(default=None, repr=False)

    def __enter__(self) -> SimulationContext:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.finalize_run()

    @classmethod
    def create(
        cls,
        *,
        world_id: str = "default",
        db_path: Path | str | None = None,
        save_db_path: Path | str | None = None,
        world: str = "default",
        start_year: int | None = None,
        refresh_config: bool | None = None,
        reset_world_for_test: bool = False,
        zero_point_foundation: bool = False,
        foundation_colony_specs: Sequence[Any] | None = None,
        foundation_couples_per_colony: int = 10,
        foundation_rng_seed: int | None = None,
        flush_run_store: bool = True,
        store_flush_batch_years: int = 50,
        checkpoint_full_snapshot_every_n_years: int | None = None,
        decision_sample_size: int = DEFAULT_DECISION_SAMPLE_SIZE,
        placename_rng_salt: int = 0,
        working_set_dead_retention_years: int = 20,
        verbose_event_logging: bool = False,
    ) -> "SimulationContext":
        from library.zero_point_colonies import (
            DEFAULT_FOUNDATION_COLONY_COUNT,
            FoundationColonySpec,
            default_foundation_specs,
            pick_far_coastal_region_ids,
            seed_foundation_colonies,
        )

        combined_reset = bool(reset_world_for_test) or history_sim_reset_world_from_env()
        ensure_world_directories(world_id)

        resolved_config_pre = (
            Path(db_path) if db_path is not None else world_folder_config_db_path(world_id)
        )
        resolved_save_pre = (
            Path(save_db_path)
            if save_db_path is not None
            else derive_save_db_path_from_config(resolved_config_pre)
        )
        # Resume (`start_year is None`) must not delete ``save.sqlite``: env-based reset
        # would otherwise wipe checkpoints before ``try_load_simulation_checkpoint`` runs.
        if combined_reset and start_year is not None:
            try:
                delete_save_database(resolved_save_pre)
            except PermissionError:
                # Windows can refuse unlinking save.sqlite while read-only browsers
                # or inspectors still have a handle. The start_year reset path below
                # clears the world's checkpoint rows in-place after ensuring schema.
                pass

        chk_people = save_has_simulation_people(resolved_save_pre, world=world)
        if combined_reset or start_year is not None:
            chk_people = False
        do_refresh_csv = should_refresh_world_config_auto(
            reset_world_for_test=combined_reset,
            refresh_config_explicit=refresh_config,
            checkpoint_has_simulation_people=chk_people,
        )
        if do_refresh_csv:
            refresh_world_config_from_csv(world_id)
        resolved_config = (
            Path(db_path) if db_path is not None else world_folder_config_db_path(world_id)
        )
        resolved_save = (
            Path(save_db_path)
            if save_db_path is not None
            else derive_save_db_path_from_config(resolved_config)
        )
        resolved_save.parent.mkdir(parents=True, exist_ok=True)
        ensure_checkpoint_schema_for_file(resolved_save)
        if start_year is not None:
            clear_world_checkpoint(resolved_save, world=world)

        (
            configured_start_year,
            history_equivalent_start_year,
            configured_checkpoint_full_every_n,
        ) = _world_start_fields(
            db_path=resolved_config,
            world=world,
        )
        if start_year is not None:
            simulation_start = int(start_year)
            current_y = simulation_start
            reset_world_time(
                start_year=simulation_start,
                current_year=current_y,
                config_db_path=resolved_config,
                save_db_path=resolved_save,
                world=world,
            )
        else:
            saved_start_y, current_y = ensure_world_state(
                config_db_path=resolved_config,
                save_db_path=resolved_save,
                world=world,
            )
            simulation_start = int(saved_start_y)

        run_store_dir = resolved_config.parent / "temp" / f"simulation_run_{uuid4().hex}"
        file_store = SimulationFileStore(
            root_dir=run_store_dir,
            flush_on_init=flush_run_store,
            flush_batch_years=store_flush_batch_years,
        )
        file_store.initialize()

        active_ids: frozenset[str] | None = None
        colony_region_order: tuple[str, ...] | None = None
        colonies_for_seed: list[FoundationColonySpec] | None = None

        if foundation_couples_per_colony < 1:
            raise ValueError("foundation_couples_per_colony must be >= 1")

        frng_seed = (
            foundation_rng_seed
            if foundation_rng_seed is not None
            else int(placename_rng_salt)
        )
        frng = random.Random(frng_seed)

        if zero_point_foundation:
            if isinstance(foundation_colony_specs, (list, tuple)) and foundation_colony_specs:
                specs_list: list[FoundationColonySpec] = []
                for spec in foundation_colony_specs:
                    if isinstance(spec, FoundationColonySpec):
                        specs_list.append(spec)
                    else:
                        region_id_s, species_s, ethnic_s = spec  # tuple pattern
                        specs_list.append(
                            FoundationColonySpec(
                                str(region_id_s), str(species_s), str(ethnic_s)
                            )
                        )
                colonies_for_seed = specs_list
            else:
                rids = pick_far_coastal_region_ids(
                    world=world,
                    db_path=resolved_config,
                    count=int(DEFAULT_FOUNDATION_COLONY_COUNT),
                )
                colonies_for_seed = default_foundation_specs(rids)
            active_ids = frozenset(s.region_id for s in colonies_for_seed)
            colony_region_order = tuple(s.region_id for s in colonies_for_seed)

        # ``None`` → ``world_start.checkpoint_full_snapshot_every_n_years`` (CSV default 10).
        resolved_checkpoint_every = (
            int(checkpoint_full_snapshot_every_n_years)
            if checkpoint_full_snapshot_every_n_years is not None
            else int(configured_checkpoint_full_every_n)
        )

        existing_map_seed = read_world_map_seed(resolved_save)
        if existing_map_seed:
            initial_map_seed = existing_map_seed
        elif int(placename_rng_salt):
            initial_map_seed = str(int(placename_rng_salt))
        else:
            initial_map_seed = uuid4().hex

        ctx = cls(
            db_path=resolved_config,
            save_db_path=resolved_save,
            world=world,
            simulation_start_year=simulation_start,
            history_equivalent_start_year=history_equivalent_start_year,
            current_year=current_y,
            file_store=file_store,
            checkpoint_full_snapshot_every_n_years=resolved_checkpoint_every,
            decision_sample_size=int(decision_sample_size),
            placename_rng_salt=int(placename_rng_salt),
            world_map_seed=initial_map_seed,
            active_region_ids=active_ids,
            foundation_colony_region_order=colony_region_order,
            working_set_dead_retention_years=int(working_set_dead_retention_years),
            verbose_event_logging=bool(verbose_event_logging),
        )
        from library.simulation_government import init_government_state

        init_government_state(ctx)
        resume_loaded = False
        if start_year is None:
            resume_loaded = try_load_simulation_checkpoint(ctx)
        if not resume_loaded:
            ctx.world_map_seed = write_world_map_seed(resolved_save, ctx.world_map_seed)
        ctx.preload(skip_settlement_seed=resume_loaded)

        if (
            zero_point_foundation
            and colonies_for_seed is not None
            and not resume_loaded
        ):
            seed_foundation_colonies(
                ctx,
                colonies_for_seed,
                couples_per_colony=foundation_couples_per_colony,
                rng=frng,
            )
        return ctx

    def preload(self, *, skip_settlement_seed: bool = False) -> None:
        preload_trait_cache(db_path=self.db_path, world=self.world)
        preload_name_cache(db_path=self.db_path)
        preload_placename_cache(db_path=self.db_path)
        self.mortality_milestones = _load_mortality_milestones(db_path=self.db_path)
        if not skip_settlement_seed:
            self._seed_settlements_from_geography()
        self.refresh_outdated_region_local_geographies()
        if not self.mortality_milestones:
            raise LookupError("historical_mortality_milestones table is empty.")
        self._mortality_index = 0
        if self.current_year is not None:
            self.get_mortality_rates_for_year(self.current_year)

    def _record_simulation_event(
        self, sim_year: int | None, event_type: str, payload: dict[str, Any]
    ) -> None:
        self._pending_simulation_events.append((sim_year, event_type, payload))

    def _record_inferred_simulation_event(
        self, sim_year: int | None, event_type: str, payload: dict[str, Any]
    ) -> None:
        marked = dict(payload)
        marked["event_origin"] = "inferred"
        self._pending_simulation_events.append((sim_year, event_type, marked))

    def invalidate_alive_census_cache(self) -> None:
        """Drop cached alive residence indexes after population/residence changes."""
        self._alive_census_cache = None
        self._alive_columns_cache = None
        self.invalidate_annual_indexes()

    def invalidate_alive_columns_cache(self) -> None:
        """Drop cached columnar fields after relationship/job/person-field changes."""
        self._alive_columns_cache = None
        self.invalidate_annual_indexes()

    def invalidate_annual_indexes(self) -> None:
        """Drop per-year shared indexes after population, residence, or relationship changes."""
        self._annual_care_indexes_cache = None
        self._annual_resource_facts_cache = None

    @staticmethod
    def _append_alive_census_record(
        records: list[SimulationPersonRecord], rec: SimulationPersonRecord
    ) -> None:
        """Keep cached census buckets in person-id order after an incremental add."""
        if not records or int(records[-1].person_id) <= int(rec.person_id):
            records.append(rec)
            return
        insert_at = len(records)
        while insert_at > 0 and int(records[insert_at - 1].person_id) > int(rec.person_id):
            insert_at -= 1
        records.insert(insert_at, rec)

    def _add_record_to_alive_census_cache(self, rec: SimulationPersonRecord) -> None:
        """Increment the alive census cache for a newly current person when it exists."""
        cached = self._alive_census_cache
        if cached is None or int(rec.person_id) not in self.current_people_ids:
            return
        sid = self._residence_settlement_id(rec)
        if sid:
            self._append_alive_census_record(cached.by_settlement.setdefault(sid, []), rec)
            cached.count_by_settlement[sid] = (
                int(cached.count_by_settlement.get(sid, 0)) + 1
            )
        rid = self._residence_region_id(rec)
        if rid:
            self._append_alive_census_record(cached.by_region.setdefault(rid, []), rec)
            cached.count_by_region[rid] = int(cached.count_by_region.get(rid, 0)) + 1

    def annual_care_indexes(self, year: int):
        """Shared per-year household/care indexes for modules that need family facts."""
        y = int(year)
        cached = self._annual_care_indexes_cache
        if cached is not None and cached[0] == y:
            return cached[1]
        from library.simulation_household_care import build_year_indexes

        indexes = build_year_indexes(self, y)
        self._annual_care_indexes_cache = (y, indexes)
        return indexes

    def annual_resource_facts(self, year: int):
        """Shared per-year settlement/region pressure facts for resource decisions."""
        y = int(year)
        cached = self._annual_resource_facts_cache
        if cached is not None and cached[0] == y:
            return cached[1]
        from library.simulation_careers import YearResourceFacts

        facts = YearResourceFacts.build(self)
        self._annual_resource_facts_cache = (y, facts)
        return facts

    def alive_census_cache(self) -> AliveCensusCache:
        """Build or return the cached alive residence index for the current context state."""
        cached = self._alive_census_cache
        if cached is not None:
            return cached
        by_settlement: dict[str, list[SimulationPersonRecord]] = {}
        by_region: dict[str, list[SimulationPersonRecord]] = {}
        for rec in self.iter_current_people(sorted_by_id=True):
            sid = self._residence_settlement_id(rec)
            if sid:
                by_settlement.setdefault(sid, []).append(rec)
            rid = self._residence_region_id(rec)
            if rid:
                by_region.setdefault(rid, []).append(rec)
        cached = AliveCensusCache(
            by_settlement=by_settlement,
            by_region=by_region,
            count_by_settlement={sid: len(records) for sid, records in by_settlement.items()},
            count_by_region={rid: len(records) for rid, records in by_region.items()},
        )
        self._alive_census_cache = cached
        return cached

    def alive_person_columns(self, year: int) -> AlivePersonColumns:
        """Return a NumPy-backed alive-person snapshot for one simulation year."""
        y = int(year)
        cached = self._alive_columns_cache
        if cached is not None and cached[0] == y:
            return cached[1]
        records = list(self.iter_current_people(sorted_by_id=True))
        settlement_code_by_id: dict[str, int] = {}
        region_code_by_id: dict[str, int] = {}
        settlement_id_by_code: dict[int, str] = {}
        region_id_by_code: dict[int, str] = {}

        def code_for(
            value: str | None,
            forward: dict[str, int],
            reverse: dict[int, str],
        ) -> int:
            key = (value or "").strip()
            if not key:
                return 0
            code = forward.get(key)
            if code is None:
                code = len(forward) + 1
                forward[key] = code
                reverse[code] = key
            return code

        def gender_code(rec: SimulationPersonRecord) -> int:
            g = (rec.person.gender or "").strip().lower()
            if g == "male":
                return 1
            if g == "female":
                return 2
            return 0

        person_ids: list[int] = []
        ages: list[int] = []
        birthyears: list[int] = []
        gender_codes: list[int] = []
        settlement_codes: list[int] = []
        region_codes: list[int] = []
        is_founder: list[bool] = []
        has_partner: list[bool] = []
        has_paramour: list[bool] = []
        attractiveness: list[float] = []
        job_prosperity: list[float] = []
        for rec in records:
            by = int(rec.person.birthyear)
            person_ids.append(int(rec.person_id))
            birthyears.append(by)
            ages.append(y - by)
            gender_codes.append(gender_code(rec))
            settlement_codes.append(
                code_for(
                    self._residence_settlement_id(rec),
                    settlement_code_by_id,
                    settlement_id_by_code,
                )
            )
            region_codes.append(
                code_for(
                    self._residence_region_id(rec),
                    region_code_by_id,
                    region_id_by_code,
                )
            )
            is_founder.append(bool(rec.is_founder))
            has_partner.append(rec.person.partner_person_id is not None)
            has_paramour.append(rec.person.paramour_person_id is not None)
            attractiveness.append(float(rec.person.attractiveness_01 or 0.0))
            job_prosperity.append(float(rec.person.job_prosperity_01 or 0.0))

        cols = AlivePersonColumns(
            year=y,
            person_ids=np.asarray(person_ids, dtype=np.int64),
            ages=np.asarray(ages, dtype=np.int64),
            birthyears=np.asarray(birthyears, dtype=np.int64),
            gender_codes=np.asarray(gender_codes, dtype=np.int8),
            settlement_codes=np.asarray(settlement_codes, dtype=np.int32),
            region_codes=np.asarray(region_codes, dtype=np.int32),
            is_founder=np.asarray(is_founder, dtype=bool),
            has_partner=np.asarray(has_partner, dtype=bool),
            has_paramour=np.asarray(has_paramour, dtype=bool),
            attractiveness_01=np.asarray(attractiveness, dtype=float),
            job_prosperity_01=np.asarray(job_prosperity, dtype=float),
            settlement_code_by_id=settlement_code_by_id,
            region_code_by_id=region_code_by_id,
            settlement_id_by_code=settlement_id_by_code,
            region_id_by_code=region_id_by_code,
        )
        self._alive_columns_cache = (y, cols)
        return cols

    def _should_checkpoint_snapshot(self, record_year: int) -> bool:
        """Whether to rewrite snapshot tables for this completed simulation year."""
        n = self.checkpoint_full_snapshot_every_n_years
        if n is None or int(n) <= 0:
            return False
        elapsed = int(record_year) - int(self.simulation_start_year) + 1
        if elapsed <= 0:
            return False
        return elapsed % int(n) == 0

    def add_person(
        self,
        *,
        person: Person,
        is_founder: bool,
        father_id: int | None = None,
        mother_id: int | None = None,
    ) -> SimulationPersonRecord:
        p = person
        if p.current_settlement_id is None and p.birthplace_settlement_id:
            p = replace(p, current_settlement_id=p.birthplace_settlement_id)
        rec = SimulationPersonRecord(
            person_id=self.next_person_id,
            person=p,
            is_founder=is_founder,
            father_id=father_id,
            mother_id=mother_id,
        )
        self.next_person_id += 1
        self.people.append(rec)
        self.id_to_record[rec.person_id] = rec
        self.current_people_ids.add(rec.person_id)
        self._add_record_to_alive_census_cache(rec)
        self.invalidate_alive_columns_cache()
        event_type = "founder_created" if is_founder else "birth"
        if self.file_store is not None:
            self.file_store.append_person(
                {
                    "person_id": rec.person_id,
                    "first_name": rec.person.first_name,
                    "last_name": rec.person.last_name,
                    "gender": rec.person.gender,
                    "ethnic": rec.person.ethnic,
                    "species": rec.person.species,
                    "birthplace": rec.person.birthplace,
                    "birthplace_region_id": rec.person.birthplace_region_id,
                    "birthplace_settlement_id": rec.person.birthplace_settlement_id,
                    "birthyear": rec.person.birthyear,
                    "deathyear": rec.person.deathyear,
                    "is_founder": rec.is_founder,
                    "father_id": rec.father_id,
                    "mother_id": rec.mother_id,
                    "father_name": rec.person.father_name,
                    "mother_name": rec.person.mother_name,
                }
            )
            self.file_store.append_event(
                {
                    "year": self.current_year if self.current_year is not None else "",
                    "event_type": event_type,
                    "person_id": rec.person_id,
                    "person_a_id": rec.father_id,
                    "person_b_id": rec.mother_id,
                    "child_id": rec.person_id if not is_founder else "",
                    "details": "",
                }
            )
        self._record_simulation_event(
            self.current_year,
            event_type,
            {
                "year": self.current_year,
                "event_type": event_type,
                "person_id": rec.person_id,
                "person_a_id": rec.father_id,
                "person_b_id": rec.mother_id,
                "child_id": rec.person_id if not is_founder else None,
                "details": "",
            },
        )
        return rec

    def add_passive_person(self, person: PassivePerson) -> PassivePersonRecord:
        """Add a real but low-detail person that does not enter annual person logic."""
        rec = PassivePersonRecord(person_id=self.next_person_id, person=person)
        self.next_person_id += 1
        self.passive_people[rec.person_id] = rec
        return rec

    def add_passive_cohort(self, cohort: PassiveCohort) -> PassiveCohort:
        """Stage one aggregate cohort bucket for checkpoint persistence."""
        self.passive_cohorts.append(cohort)
        return cohort

    def promote_passive_person(
        self,
        passive_id: int,
        *,
        year: int,
        reason: str,
        source: dict[str, Any] | None = None,
    ) -> SimulationPersonRecord:
        """Materialize a passive person into the detailed annual simulation."""
        prec = self.passive_people.pop(int(passive_id))
        person = passive_person_to_detailed_person(
            prec.person,
            simulation_context=self,
            simulation_year=int(year),
        )
        rec = SimulationPersonRecord(
            person_id=int(prec.person_id),
            person=person,
            is_founder=False,
            father_id=prec.person.father_id,
            mother_id=prec.person.mother_id,
        )
        self.people.append(rec)
        self.id_to_record[rec.person_id] = rec
        if person.deathyear is None or int(person.deathyear) > int(year):
            self.current_people_ids.add(rec.person_id)
        self.next_person_id = max(int(self.next_person_id), int(rec.person_id) + 1)
        self._add_record_to_alive_census_cache(rec)
        self.invalidate_alive_columns_cache()
        if self.file_store is not None:
            self.file_store.append_person(
                {
                    "person_id": rec.person_id,
                    "first_name": rec.person.first_name,
                    "last_name": rec.person.last_name,
                    "gender": rec.person.gender,
                    "ethnic": rec.person.ethnic,
                    "species": rec.person.species,
                    "birthplace": rec.person.birthplace,
                    "birthplace_region_id": rec.person.birthplace_region_id,
                    "birthplace_settlement_id": rec.person.birthplace_settlement_id,
                    "birthyear": rec.person.birthyear,
                    "is_founder": False,
                    "father_id": rec.father_id or "",
                    "mother_id": rec.mother_id or "",
                }
            )
        payload = {
            "year": int(year),
            "person_id": int(rec.person_id),
            "reason": str(reason),
            "birthyear": int(person.birthyear),
            "settlement_id": person.current_settlement_id,
            "region_id": person.birthplace_region_id,
            "source": source or {},
        }
        self._record_inferred_simulation_event(year, "passive_person_promoted", payload)
        if prec.person.partner_name or prec.person.partner_person_id is not None:
            self._record_inferred_simulation_event(
                prec.person.partnership_start_year or year,
                "promotion_backfill_partnership",
                {
                    "person_id": int(rec.person_id),
                    "partner_person_id": prec.person.partner_person_id,
                    "partner_name": prec.person.partner_name,
                    "partner_birthyear": prec.person.partner_birthyear,
                    "partner_deathyear": prec.person.partner_deathyear,
                    "partnership_start_year": prec.person.partnership_start_year,
                    "partnership_end_year": prec.person.partnership_end_year,
                    "reason": str(reason),
                },
            )
        if prec.person.child_count or prec.person.child_birthyears or prec.person.child_person_ids:
            self._record_inferred_simulation_event(
                year,
                "promotion_backfill_children",
                {
                    "person_id": int(rec.person_id),
                    "child_count": int(prec.person.child_count),
                    "child_ids": list(prec.person.child_person_ids),
                    "child_birthyears": list(prec.person.child_birthyears),
                    "reason": str(reason),
                },
            )
        self._record_inferred_simulation_event(
            person.birthyear,
            "promotion_backfill_birth",
            {
                "person_id": int(rec.person_id),
                "birthyear": int(person.birthyear),
                "father_id": rec.father_id,
                "mother_id": rec.mother_id,
                "reason": str(reason),
            },
        )
        return rec

    @staticmethod
    def _relationship_pair_key(person_a_id: int, person_b_id: int) -> tuple[int, int]:
        a = int(person_a_id)
        b = int(person_b_id)
        return (a, b) if a <= b else (b, a)

    def surname_convention_for_parents(self, person_a_id: int, person_b_id: int) -> str:
        """Return the stable surname convention for this parent partnership."""
        key = self._relationship_pair_key(person_a_id, person_b_id)
        existing = self.surname_conventions_by_pair.get(key)
        if existing:
            return existing

        ra = self.id_to_record.get(person_a_id)
        rb = self.id_to_record.get(person_b_id)
        if ra is None or rb is None:
            raise LookupError("surname_convention_for_parents: unknown person id")
        father = (
            ra.person
            if (ra.person.gender or "").strip().lower() == "male"
            else rb.person
        )
        convention_ethnic = (father.ethnic or "").strip()
        if not convention_ethnic:
            convention_ethnic = (ra.person.ethnic or rb.person.ethnic or "").strip()
        from library.random_names import choose_birth_surname_convention

        try:
            convention = choose_birth_surname_convention(
                ethnic=convention_ethnic,
                father_last_name=father.last_name,
                father_ethnic=father.ethnic,
                db_path=self.db_path,
            )
        except (FileNotFoundError, LookupError):
            convention = "lookup"
        self.surname_conventions_by_pair[key] = convention
        return convention

    def add_couple(self, person_a_id: int, person_b_id: int) -> None:
        ra = self.id_to_record.get(person_a_id)
        rb = self.id_to_record.get(person_b_id)
        if ra is None or rb is None:
            raise LookupError("add_couple: unknown person id")
        ra.person = replace(ra.person, partner_person_id=person_b_id)
        rb.person = replace(rb.person, partner_person_id=person_a_id)
        if (ra.person.gender or "").strip() == "Male" and (
            rb.person.gender or ""
        ).strip() == "Female":
            pair = (person_a_id, person_b_id)
        elif (ra.person.gender or "").strip() == "Female" and (
            rb.person.gender or ""
        ).strip() == "Male":
            pair = (person_b_id, person_a_id)
        else:
            pair = (person_a_id, person_b_id)
        self.couples.append(pair)
        surname_convention = self.surname_convention_for_parents(*pair)
        self.invalidate_alive_columns_cache()
        if self.file_store is not None:
            self.file_store.append_event(
                {
                    "year": self.current_year if self.current_year is not None else "",
                    "event_type": "couple_formed",
                    "person_id": "",
                    "person_a_id": person_a_id,
                    "person_b_id": person_b_id,
                    "child_id": "",
                    "details": "",
                    "surname_convention": surname_convention,
                }
            )
        self._record_simulation_event(
            self.current_year,
            "couple_formed",
            {
                "year": self.current_year,
                "event_type": "couple_formed",
                "person_id": None,
                "person_a_id": person_a_id,
                "person_b_id": person_b_id,
                "child_id": None,
                "details": "",
                "surname_convention": surname_convention,
            },
        )

    def dissolve_couple(self, person_a_id: int, person_b_id: int) -> None:
        pair_set = {person_a_id, person_b_id}
        self.couples = [
            (a, b)
            for (a, b) in self.couples
            if {a, b} != pair_set
        ]
        self.surname_conventions_by_pair.pop(
            self._relationship_pair_key(person_a_id, person_b_id), None
        )
        for pid in (person_a_id, person_b_id):
            rec = self.id_to_record.get(pid)
            if rec is not None and rec.person.partner_person_id in pair_set:
                rec.person = replace(rec.person, partner_person_id=None)
        self.invalidate_alive_columns_cache()
        if self.file_store is not None:
            self.file_store.append_event(
                {
                    "year": self.current_year if self.current_year is not None else "",
                    "event_type": "couple_dissolved",
                    "person_id": "",
                    "person_a_id": person_a_id,
                    "person_b_id": person_b_id,
                    "child_id": "",
                    "details": "",
                }
            )
        self._record_simulation_event(
            self.current_year,
            "couple_dissolved",
            {
                "year": self.current_year,
                "event_type": "couple_dissolved",
                "person_a_id": person_a_id,
                "person_b_id": person_b_id,
            },
        )

    def add_paramour_relationship(self, person_a_id: int, person_b_id: int) -> None:
        ra = self.id_to_record.get(person_a_id)
        rb = self.id_to_record.get(person_b_id)
        if ra is None or rb is None:
            raise LookupError("add_paramour_relationship: unknown person id")
        from library.simulation_social import paramour_pair_eligible

        y = int(self.current_year or self.simulation_start_year)
        if not paramour_pair_eligible(ra, rb, y):
            raise ValueError(
                "add_paramour_relationship: pair fails minimum age or close-kin rules"
            )
        ra.person = replace(ra.person, paramour_person_id=person_b_id)
        rb.person = replace(rb.person, paramour_person_id=person_a_id)
        if (ra.person.gender or "").strip() == "Male" and (
            rb.person.gender or ""
        ).strip() == "Female":
            pair = (person_a_id, person_b_id)
        elif (ra.person.gender or "").strip() == "Female" and (
            rb.person.gender or ""
        ).strip() == "Male":
            pair = (person_b_id, person_a_id)
        else:
            pair = (person_a_id, person_b_id)
        self.paramours.append(pair)
        surname_convention = self.surname_convention_for_parents(*pair)
        self.invalidate_alive_columns_cache()
        if self.file_store is not None:
            self.file_store.append_event(
                {
                    "year": self.current_year if self.current_year is not None else "",
                    "event_type": "paramour_formed",
                    "person_id": "",
                    "person_a_id": person_a_id,
                    "person_b_id": person_b_id,
                    "child_id": "",
                    "details": "",
                    "surname_convention": surname_convention,
                }
            )
        self._record_simulation_event(
            self.current_year,
            "paramour_formed",
            {
                "year": self.current_year,
                "person_a_id": person_a_id,
                "person_b_id": person_b_id,
                "surname_convention": surname_convention,
            },
        )

    def end_paramour_relationship(self, person_a_id: int, person_b_id: int) -> None:
        pair_set = {person_a_id, person_b_id}
        self.paramours = [
            (a, b)
            for (a, b) in self.paramours
            if {a, b} != pair_set
        ]
        self.surname_conventions_by_pair.pop(
            self._relationship_pair_key(person_a_id, person_b_id), None
        )
        for pid in (person_a_id, person_b_id):
            rec = self.id_to_record.get(pid)
            if rec is not None and rec.person.paramour_person_id in pair_set:
                rec.person = replace(rec.person, paramour_person_id=None)
        self.invalidate_alive_columns_cache()
        if self.file_store is not None:
            self.file_store.append_event(
                {
                    "year": self.current_year if self.current_year is not None else "",
                    "event_type": "paramour_ended",
                    "person_id": "",
                    "person_a_id": person_a_id,
                    "person_b_id": person_b_id,
                    "child_id": "",
                    "details": "",
                }
            )
        self._record_simulation_event(
            self.current_year,
            "paramour_ended",
            {
                "year": self.current_year,
                "person_a_id": person_a_id,
                "person_b_id": person_b_id,
            },
        )

    def _person_is_dependent_minor(self, rec: SimulationPersonRecord, ref_year: int) -> bool:
        age = int(ref_year) - int(rec.person.birthyear)
        mf = rec.person.min_fertility_age
        if mf is not None:
            return age < int(mf)
        return age < 18

    def settlement_household_member_ids_for_move(
        self, adult_person_id: int, ref_year: int | None = None
    ) -> list[int]:
        """Adult, co-resident partner, and dependent co-resident children."""
        rec = self.id_to_record.get(int(adult_person_id))
        if rec is None or int(adult_person_id) not in self.current_people_ids:
            return []
        year = int(
            ref_year
            if ref_year is not None
            else self.current_year
            if self.current_year is not None
            else self.simulation_start_year
        )
        origin_sid = self._residence_settlement_id(rec)
        if not origin_sid:
            return [int(adult_person_id)]
        to_move: list[int] = [int(adult_person_id)]
        parent_ids: set[int] = {int(adult_person_id)}
        partner_id = rec.person.partner_person_id
        if partner_id is not None:
            partner = self.id_to_record.get(int(partner_id))
            if (
                partner is not None
                and int(partner_id) in self.current_people_ids
                and self._residence_settlement_id(partner) == origin_sid
            ):
                to_move.append(int(partner_id))
                parent_ids.add(int(partner_id))
        for pid in list(self.current_people_ids):
            if int(pid) in parent_ids:
                continue
            child = self.id_to_record.get(int(pid))
            if child is None or self._residence_settlement_id(child) != origin_sid:
                continue
            child_parents = {x for x in (child.father_id, child.mother_id) if x is not None}
            if int(adult_person_id) not in child_parents:
                continue
            if len(parent_ids) > 1 and not child_parents.issubset(parent_ids):
                continue
            if self._person_is_dependent_minor(child, year):
                to_move.append(int(pid))
        return sorted(dict.fromkeys(to_move))

    def queue_household_move_to_settlement(
        self,
        person_id: int,
        settlement_id: str,
        *,
        move_reason: str | None = None,
        requested_year: int | None = None,
        apply_year: int | None = None,
        source_event: str | None = None,
        group_id: str | None = None,
    ) -> list[int]:
        """Queue an adult's co-resident household for a deferred settlement move."""
        req_y = int(
            requested_year
            if requested_year is not None
            else self.current_year
            if self.current_year is not None
            else self.simulation_start_year
        )
        app_y = int(apply_year if apply_year is not None else req_y + 1)
        queued: list[int] = []
        for pid in self.settlement_household_member_ids_for_move(int(person_id), req_y):
            try:
                changed = self.queue_person_move_to_settlement(
                    pid,
                    settlement_id,
                    move_reason=move_reason,
                    requested_year=req_y,
                    apply_year=app_y,
                    source_event=source_event,
                    group_id=group_id,
                )
            except (ValueError, LookupError):
                continue
            if changed:
                queued.append(int(pid))
        return queued

    def relocate_birthing_household_to_settlement(
        self, mother_person_id: int, new_settlement_id: str
    ) -> None:
        """Queue mother, cohabiting spouse, and immature shared children for next-year relocation."""
        ns = (new_settlement_id or "").strip()
        if not ns:
            return
        mrec = self.id_to_record.get(mother_person_id)
        if mrec is None or mother_person_id not in self.current_people_ids:
            return
        ref_year = int(self.current_year or self.simulation_start_year)
        self.queue_household_move_to_settlement(
            mother_person_id,
            ns,
            move_reason="birthing_household_spinoff",
            requested_year=ref_year,
            apply_year=ref_year + 1,
            source_event="birthing_household_spinoff",
            group_id=f"birth_spinoff:{mother_person_id}:{ref_year}",
        )

    def queue_person_move_to_settlement(
        self,
        person_id: int,
        settlement_id: str,
        *,
        move_reason: str | None = None,
        requested_year: int | None = None,
        apply_year: int | None = None,
        source_event: str | None = None,
        group_id: str | None = None,
    ) -> bool:
        """Record a year-boundary residence move without mutating current residence."""
        sid = (settlement_id or "").strip()
        st = self.settlements_by_id.get(sid)
        if st is None or (st.status or "").strip().lower() != "active":
            raise ValueError(f"queue_person_move_to_settlement: invalid settlement {sid!r}")
        rec = self.id_to_record.get(person_id)
        if rec is None or person_id not in self.current_people_ids:
            raise LookupError(
                f"queue_person_move_to_settlement: person {person_id} not alive"
            )
        old_sid = (rec.person.current_settlement_id or "").strip() or None
        if old_sid == sid:
            return False
        req_y = int(
            requested_year
            if requested_year is not None
            else self.current_year
            if self.current_year is not None
            else self.simulation_start_year
        )
        app_y = int(apply_year if apply_year is not None else req_y + 1)
        reason = (move_reason or "").strip() or "deferred_settlement_move"
        intent = PendingSettlementMove(
            person_id=int(person_id),
            to_settlement_id=sid,
            move_reason=reason,
            requested_year=req_y,
            apply_year=app_y,
            from_settlement_id=old_sid,
            source_event=(source_event or "").strip() or None,
            group_id=(group_id or "").strip() or None,
        )
        self.pending_settlement_moves = [
            m
            for m in self.pending_settlement_moves
            if not (int(m.person_id) == int(person_id) and int(m.apply_year) == app_y)
        ]
        self.pending_settlement_moves.append(intent)
        payload: dict[str, Any] = {
            "year": req_y,
            "person_id": int(person_id),
            "from_settlement_id": old_sid,
            "to_settlement_id": sid,
            "requested_year": req_y,
            "apply_year": app_y,
            "move_reason": reason,
        }
        if intent.source_event:
            payload["source_event"] = intent.source_event
        if intent.group_id:
            payload["group_id"] = intent.group_id
        self._record_simulation_event(req_y, "settlement_move_planned", payload)
        if self.file_store is not None:
            self.file_store.append_event(
                {
                    "year": req_y,
                    "event_type": "settlement_move_planned",
                    "person_id": int(person_id),
                    "person_a_id": "",
                    "person_b_id": "",
                    "child_id": "",
                    "from_settlement_id": old_sid or "",
                    "to_settlement_id": sid,
                    "requested_year": req_y,
                    "apply_year": app_y,
                    "move_reason": reason,
                    "details": f"{old_sid} -> {sid}",
                }
            )
        return True

    def apply_pending_settlement_moves(self, year: int) -> int:
        """Apply deferred residence moves due at or before ``year``."""
        y = int(year)
        self.current_year = y
        if not self.pending_settlement_moves:
            return 0
        due: list[PendingSettlementMove] = []
        future: list[PendingSettlementMove] = []
        for intent in self.pending_settlement_moves:
            if int(intent.apply_year) <= y:
                due.append(intent)
            else:
                future.append(intent)
        if not due:
            return 0
        latest_by_person: dict[int, PendingSettlementMove] = {}
        for intent in due:
            latest_by_person[int(intent.person_id)] = intent
        applied = 0
        for intent in sorted(
            latest_by_person.values(), key=lambda m: (int(m.apply_year), int(m.person_id))
        ):
            try:
                self.move_person_to_settlement(
                    int(intent.person_id),
                    intent.to_settlement_id,
                    move_reason=intent.move_reason,
                    requested_year=intent.requested_year,
                    planned_apply_year=intent.apply_year,
                    source_event=intent.source_event,
                    group_id=intent.group_id,
                )
                applied += 1
            except (ValueError, LookupError):
                self._record_simulation_event(
                    y,
                    "settlement_move_dropped",
                    {
                        "year": y,
                        "person_id": int(intent.person_id),
                        "to_settlement_id": intent.to_settlement_id,
                        "requested_year": int(intent.requested_year),
                        "apply_year": int(intent.apply_year),
                        "move_reason": intent.move_reason,
                    },
                )
        self.pending_settlement_moves = future
        return applied

    def move_person_to_settlement(
        self,
        person_id: int,
        settlement_id: str,
        *,
        move_reason: str | None = None,
        requested_year: int | None = None,
        planned_apply_year: int | None = None,
        source_event: str | None = None,
        group_id: str | None = None,
    ) -> None:
        sid = (settlement_id or "").strip()
        st = self.settlements_by_id.get(sid)
        if st is None or (st.status or "").strip().lower() != "active":
            raise ValueError(f"move_person_to_settlement: invalid settlement {sid!r}")
        rec = self.id_to_record.get(person_id)
        if rec is None or person_id not in self.current_people_ids:
            raise LookupError(f"move_person_to_settlement: person {person_id} not alive")
        old_sid = (rec.person.current_settlement_id or "").strip() or None
        if old_sid == sid:
            return
        old_st = self.settlements_by_id.get(old_sid) if old_sid else None
        from_region_id = (
            (old_st.region_id or "").strip() or None if old_st is not None else None
        )
        to_region_id = (st.region_id or "").strip() or None
        cross_region = bool(
            from_region_id
            and to_region_id
            and from_region_id != to_region_id
        )
        rec.person = replace(rec.person, current_settlement_id=sid)
        if self.file_store is not None:
            self.file_store.append_event(
                {
                    "year": self.current_year if self.current_year is not None else "",
                    "event_type": "settlement_moved",
                    "person_id": person_id,
                    "person_a_id": "",
                    "person_b_id": "",
                    "child_id": "",
                    "from_region_id": from_region_id or "",
                    "to_region_id": to_region_id or "",
                    "cross_region": "1" if cross_region else "",
                    "move_reason": (move_reason or ""),
                    "details": f"{old_sid} -> {sid}",
                }
            )
        payload: dict[str, Any] = {
            "year": self.current_year,
            "person_id": person_id,
            "from_settlement_id": old_sid,
            "to_settlement_id": sid,
            "from_region_id": from_region_id,
            "to_region_id": to_region_id,
            "cross_region": cross_region,
        }
        if move_reason:
            payload["move_reason"] = move_reason
        if requested_year is not None:
            payload["requested_year"] = int(requested_year)
        if planned_apply_year is not None:
            payload["planned_apply_year"] = int(planned_apply_year)
        if source_event:
            payload["source_event"] = source_event
        if group_id:
            payload["group_id"] = group_id
        self._record_simulation_event(
            self.current_year,
            "settlement_moved",
            payload,
        )
        self.invalidate_alive_census_cache()

    def _clear_relationship_refs_to(self, dead_ids: set[int]) -> None:
        for rec in self.people:
            if rec.person_id not in self.current_people_ids:
                continue
            p = rec.person
            np = p
            if p.partner_person_id is not None and p.partner_person_id in dead_ids:
                np = replace(np, partner_person_id=None)
            if p.paramour_person_id is not None and p.paramour_person_id in dead_ids:
                np = replace(np, paramour_person_id=None)
            if np is not p:
                rec.person = np

    def _person_state_after_death(self, person: Person, deathyear: int) -> Person:
        """Keep identity/history fields, but clear active simulation state on death.

        Clears employment, residence, relationships, last wage prosperity, and
        life stage (dead are excluded from annual life-stage refresh).
        """
        return replace(
            person,
            deathyear=int(deathyear),
            current_settlement_id=None,
            partner_person_id=None,
            paramour_person_id=None,
            last_birth_event_year=None,
            job=None,
            job_assigned_year=None,
            job_era=None,
            job_tier=None,
            status_tendency=None,
            leader_quality=None,
            leader_tendency=None,
            employment_status=None,
            job_lost_year=None,
            unemployment_started_year=None,
            last_job=None,
            career_fitness_score=None,
            job_prosperity_01=None,
            life_stage=None,
        )

    def is_alive(self, person_id: int) -> bool:
        return person_id in self.current_people_ids

    def current_people(self) -> list[SimulationPersonRecord]:
        return [self.id_to_record[pid] for pid in self.current_people_ids if pid in self.id_to_record]

    def iter_current_people(self, *, sorted_by_id: bool = False):
        """Yield alive person records without allocating a full list."""
        ids = (
            sorted(self.current_people_ids)
            if sorted_by_id
            else self.current_people_ids
        )
        for pid in ids:
            rec = self.id_to_record.get(pid)
            if rec is not None:
                yield rec

    def refresh_current_people_life_stages(self, simulation_year: int) -> None:
        """Recompute ``life_stage`` from age and species bands for everyone alive."""
        y = int(simulation_year)
        species_rows = self.species_life_stage_rows()
        for rec in self.iter_current_people(sorted_by_id=True):
            sp = (rec.person.species or "").strip()
            eth = (rec.person.ethnic or "").strip()
            if not sp:
                continue
            row = species_rows.get((sp, eth))
            if row is None:
                continue
            age = y - int(rec.person.birthyear)
            if age < 0:
                continue
            stage = infer_life_stage_from_age(age, row)
            if rec.person.life_stage != stage:
                rec.person = replace(rec.person, life_stage=stage)

    def species_life_stage_rows(self) -> dict[tuple[str, str], Mapping[str, Any]]:
        """Cached species rows keyed by ``(species, ethnic)`` for age-band lookups."""
        path_s = str(Path(self.db_path).resolve())
        cached = self._species_life_stage_rows_cache
        if cached is not None and cached[0] == path_s:
            return cached[1]
        rows: dict[tuple[str, str], Mapping[str, Any]] = {}
        for row in _species_rows(path_s):
            sp = str(row.get("species") or "").strip()
            eth = str(row.get("ethnic") or "").strip()
            if sp:
                rows[(sp, eth)] = row
        self._species_life_stage_rows_cache = (path_s, rows)
        return rows

    def mark_dead(self, dead_ids: set[int], *, deathyear: int) -> None:
        if not dead_ids:
            return
        for pid in dead_ids:
            rec = self.id_to_record.get(pid)
            if rec is None:
                continue
            if rec.person.deathyear is None:
                rec.person = self._person_state_after_death(rec.person, deathyear)
                if self.file_store is not None:
                    self.file_store.append_people_update(
                        {
                            "year": deathyear,
                            "person_id": pid,
                            "field": "deathyear",
                            "value": deathyear,
                        }
                    )
                    self.file_store.append_event(
                        {
                            "year": deathyear,
                            "event_type": "death",
                            "person_id": pid,
                            "person_a_id": "",
                            "person_b_id": "",
                            "child_id": "",
                            "details": "",
                        }
                    )
                self._record_simulation_event(
                    deathyear,
                    "death",
                    {
                        "year": deathyear,
                        "event_type": "death",
                        "person_id": pid,
                        "person_a_id": None,
                        "person_b_id": None,
                        "child_id": None,
                        "details": "cleared active relationship/career/employment state",
                        "cleared_current_state_fields": [
                            "current_settlement_id",
                            "partner_person_id",
                            "paramour_person_id",
                            "last_birth_event_year",
                            "job",
                            "job_assigned_year",
                            "job_era",
                            "job_tier",
                            "status_tendency",
                            "leader_quality",
                            "leader_tendency",
                            "employment_status",
                            "job_lost_year",
                            "unemployment_started_year",
                            "last_job",
                            "career_fitness_score",
                        ],
                    },
                )
            self.current_people_ids.discard(pid)
        self.invalidate_alive_census_cache()
        self._clear_relationship_refs_to(dead_ids)
        self.couples = [
            (a_id, b_id)
            for (a_id, b_id) in self.couples
            if self.is_alive(a_id) and self.is_alive(b_id)
        ]
        self.paramours = [
            (a_id, b_id)
            for (a_id, b_id) in self.paramours
            if self.is_alive(a_id) and self.is_alive(b_id)
        ]

    def _residence_settlement_id(self, rec: SimulationPersonRecord) -> str:
        return (
            (rec.person.current_settlement_id or rec.person.birthplace_settlement_id or "")
            .strip()
        )

    def _residence_region_id(self, rec: SimulationPersonRecord) -> str | None:
        sid = self._residence_settlement_id(rec)
        if sid:
            st = self.settlements_by_id.get(sid)
            if st is not None:
                return (st.region_id or "").strip() or None
        return (rec.person.birthplace_region_id or "").strip() or None

    def count_alive_in_region(self, region_id: str) -> int:
        rid = (region_id or "").strip()
        if not rid:
            return 0
        return int(self.alive_census_cache().count_by_region.get(rid, 0))

    def current_people_by_settlement(self) -> dict[str, list[SimulationPersonRecord]]:
        """Alive people grouped by residence settlement, ordered by person id."""
        return self.alive_census_cache().by_settlement

    def current_people_by_region(self) -> dict[str, list[SimulationPersonRecord]]:
        """Alive people grouped by residence region, ordered by person id."""
        return self.alive_census_cache().by_region

    @staticmethod
    def _stable_decision_seed(text: str) -> int:
        """Small deterministic string hash for reproducible decision sampling."""
        h = 14_695_981_039_346_656_037
        for ch in text:
            h ^= ord(ch)
            h = (h * 1_099_511_628_211) & 0xFFFFFFFFFFFFFFFF
        return h

    def decision_sample_records(
        self,
        records: Iterable[SimulationPersonRecord],
        *,
        year: int | None = None,
        scope: str,
        stream: int = 0,
        cap: int | None = None,
    ) -> list[SimulationPersonRecord]:
        """Return a deterministic capped sample for settlement/regional decisions.

        Appropriate uses are behavior/candidate pools where a stable sample can
        stand in for "the population's tendencies" at large scale. Inappropriate
        uses are exact counts, persistence, event conservation, and demographic or
        economic totals. The sample is deterministic for a given year/scope/stream
        so reruns stay reproducible. Groups at or below the cap are returned
        unchanged.
        """
        pool = list(records)
        try:
            limit = int(self.decision_sample_size if cap is None else cap)
        except (TypeError, ValueError):
            limit = DEFAULT_DECISION_SAMPLE_SIZE
        if limit <= 0 or len(pool) <= limit:
            return pool
        y = int(
            year
            if year is not None
            else self.current_year
            if self.current_year is not None
            else self.simulation_start_year
        )
        seed = self._stable_decision_seed(
            "|".join(
                (
                    str(self.world),
                    str(self.placename_rng_salt),
                    str(y),
                    str(int(stream)),
                    str(scope),
                    str(len(pool)),
                )
            )
        )
        rng = random.Random(seed)
        return sorted(rng.sample(pool, limit), key=lambda rec: int(rec.person_id))

    def decision_sample_people_in_region(
        self,
        region_id: str,
        *,
        year: int | None = None,
        stream: int = 0,
        cap: int | None = None,
    ) -> list[SimulationPersonRecord]:
        rid = (region_id or "").strip()
        return self.decision_sample_records(
            self.current_people_by_region().get(rid, ()),
            year=year,
            scope=f"region:{rid}",
            stream=stream,
            cap=cap,
        )

    def decision_sample_people_in_settlement(
        self,
        settlement_id: str,
        *,
        year: int | None = None,
        stream: int = 0,
        cap: int | None = None,
    ) -> list[SimulationPersonRecord]:
        sid = (settlement_id or "").strip()
        return self.decision_sample_records(
            self.current_people_by_settlement().get(sid, ()),
            year=year,
            scope=f"settlement:{sid}",
            stream=stream,
            cap=cap,
        )

    def effective_regional_population_cap(self, region_id: str) -> int:
        """Time-varying soft cap: config ``carrying_capacity`` × per-region multiplier (≥ 1)."""
        rid = (region_id or "").strip()
        base = self._regional_base_cap_cache.get(rid)
        if base is None:
            region = get_region(rid, world=self.world, db_path=self.db_path)
            base = max(0, int(region.carrying_capacity))
            self._regional_base_cap_cache[rid] = base
        if base <= 0:
            base = 5000
        m = float(self.region_effective_cap_multiplier.get(rid, 1.0))
        return max(1, int(base * m))

    def count_alive_in_settlement(self, settlement_id: str) -> int:
        sid = (settlement_id or "").strip()
        if not sid:
            return 0
        return int(self.alive_census_cache().count_by_settlement.get(sid, 0))

    def latest_passive_cohort_year(self) -> int | None:
        return max((int(c.sim_year) for c in self.passive_cohorts), default=None)

    def passive_population_counts_by_settlement(self) -> dict[str, int]:
        """Alive passive people plus latest aggregate cohort counts by settlement."""
        out: dict[str, int] = {}
        for rec in self.passive_people.values():
            p = rec.person
            y = self.current_year if self.current_year is not None else self.simulation_start_year
            if p.deathyear is not None and int(p.deathyear) <= int(y):
                continue
            sid = (p.current_settlement_id or p.birthplace_settlement_id or "").strip()
            if sid:
                out[sid] = out.get(sid, 0) + 1
        latest_year = self.latest_passive_cohort_year()
        for cohort in self.passive_cohorts:
            if latest_year is None or int(cohort.sim_year) != int(latest_year):
                continue
            sid = (cohort.settlement_id or "").strip()
            if sid:
                out[sid] = out.get(sid, 0) + max(0, int(cohort.population_count))
        return out

    def passive_population_counts_by_region(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for sid, count in self.passive_population_counts_by_settlement().items():
            st = self.settlements_by_id.get(sid)
            rid = (st.region_id if st is not None else sid.split(":", 1)[0]).strip()
            if rid:
                out[rid] = out.get(rid, 0) + int(count)
        return out

    def mixed_population_count_in_settlement(self, settlement_id: str) -> int:
        sid = (settlement_id or "").strip()
        if not sid:
            return 0
        return self.count_alive_in_settlement(sid) + self.passive_population_counts_by_settlement().get(sid, 0)

    def mixed_population_count_in_region(self, region_id: str) -> int:
        rid = (region_id or "").strip()
        if not rid:
            return 0
        return self.count_alive_in_region(rid) + self.passive_population_counts_by_region().get(rid, 0)

    def sync_settlement_resident_counts(self) -> None:
        by_sid = self.current_people_by_settlement()
        passive_by_sid = self.passive_population_counts_by_settlement()
        for sid, st in list(self.settlements_by_id.items()):
            if (st.status or "").strip().lower() != "active":
                self.settlements_by_id[sid] = replace(
                    st, resident_count=0, household_cap=0
                )
                continue
            rc = len(by_sid.get(sid, ())) + int(passive_by_sid.get(sid, 0))
            hh = max(0 if rc <= 0 else 1, int(round(rc / 4.5)))
            self.settlements_by_id[sid] = replace(
                st, resident_count=rc, household_cap=hh
            )

    def rebuild_settlement_region_index(self) -> None:
        idx: dict[str, list[str]] = {}
        for sid, st in self.settlements_by_id.items():
            idx.setdefault(st.region_id, []).append(sid)
        for rid in idx:
            idx[rid].sort()
        self.settlement_ids_by_region = idx

    def active_settlements_in_region(self, region_id: str) -> list[SettlementState]:
        rid = (region_id or "").strip()
        if self.settlements_by_id and not self.settlement_ids_by_region:
            self.rebuild_settlement_region_index()
        indexed_ids = self.settlement_ids_by_region.get(rid)
        if indexed_ids is not None:
            out = [
                st
                for sid in indexed_ids
                if (st := self.settlements_by_id.get(sid)) is not None
                and (st.status or "").strip().lower() == "active"
            ]
            out.sort(key=lambda s: (s.site_slot, s.founded_sim_year or 0))
            return out
        out = [
            st
            for st in self.settlements_by_id.values()
            if st.region_id == rid and (st.status or "").strip().lower() == "active"
        ]
        out.sort(key=lambda s: (s.site_slot, s.founded_sim_year or 0))
        return out

    def max_site_slot_in_region(self, region_id: str) -> int:
        rid = (region_id or "").strip()
        m = 0
        for st in self.settlements_by_id.values():
            if st.region_id == rid:
                m = max(m, int(st.site_slot))
        return m

    def _settlement_founding_year(self) -> int:
        if self.current_year is not None:
            return int(self.current_year)
        return int(self.simulation_start_year)

    def _set_region_local_geography(self, region_id: str, local_geography_json: str) -> None:
        rid = (region_id or "").strip()
        for st in self.settlements_by_id.values():
            if st.region_id == rid:
                st.local_geography_json = local_geography_json

    def world_map_geometry_for_settlements(self) -> WorldMapGeometry:
        if self._world_map_geometry_cache is None:
            self._world_map_geometry_cache = build_world_map_geometry(
                world=self.world,
                db_path=self.db_path,
                save_db_path=self.save_db_path,
                map_seed=self.world_map_seed,
            )
        return self._world_map_geometry_cache

    def refresh_region_local_geography(self, region_id: str) -> str | None:
        rid = (region_id or "").strip()
        slots = self.max_site_slot_in_region(rid)
        if slots < 1:
            return None
        region = get_region(rid, world=self.world, db_path=self.db_path)
        first = min(
            (st for st in self.settlements_by_id.values() if st.region_id == rid),
            key=lambda st: (int(st.site_slot), st.founded_sim_year or 0, st.settlement_id),
            default=None,
        )
        geo_rng = make_region_geography_rng(
            self.world,
            rid,
            slot=0,
            salt=self.placename_rng_salt,
        )
        graph = build_local_region_graph(
            world=self.world,
            region=region,
            rng=geo_rng,
            settlement_slots=slots,
            primary_meaning="",
            primary_category=first.name_category_primary if first is not None else None,
            db_path=self.db_path,
            map_seed=self.world_map_seed,
            ethnic_weights=region_ethnic_weights(self, rid, db_path=self.db_path),
            placename_lexicon=PlacenameLexicon.from_db(db_path=self.db_path),
            world_geometry=self.world_map_geometry_for_settlements(),
        )
        geo_json = graph.to_json()
        self._set_region_local_geography(rid, geo_json)
        return geo_json

    def refresh_all_region_local_geographies(self) -> None:
        for rid in sorted({st.region_id for st in self.settlements_by_id.values()}):
            self.refresh_region_local_geography(rid)

    def refresh_outdated_region_local_geographies(self) -> None:
        outdated: set[str] = set()
        for st in self.settlements_by_id.values():
            raw = st.local_geography_json
            if not raw:
                outdated.add(st.region_id)
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                outdated.add(st.region_id)
                continue
            sites = data.get("settlements") if isinstance(data, dict) else None
            if not isinstance(sites, list) or not sites:
                outdated.add(st.region_id)
                continue
            for site in sites:
                if not isinstance(site, dict):
                    outdated.add(st.region_id)
                    break
                if site.get("world_x") is None or site.get("world_y") is None:
                    outdated.add(st.region_id)
                    break
        for rid in sorted(outdated):
            self.refresh_region_local_geography(rid)

    def reestablish_from_abandoned(self, abandoned: SettlementState) -> SettlementState:
        rid = abandoned.region_id
        seq = next_settlement_sequence(rid, list(self.settlements_by_id.keys()))
        new_sid = make_settlement_id(rid, seq)
        year = self._settlement_founding_year()
        slot = max(1, int(abandoned.site_slot))
        st = SettlementState(
            region_id=rid,
            region_display_name=abandoned.region_display_name,
            settlement_id=new_sid,
            site_slot=slot,
            resident_count=0,
            household_cap=0,
            display_name=abandoned.display_name,
            etymology=abandoned.etymology,
            name_category_primary=abandoned.name_category_primary,
            name_category_secondary=abandoned.name_category_secondary,
            name_culture_primary=abandoned.name_culture_primary,
            name_culture_secondary=abandoned.name_culture_secondary,
            local_geography_json=abandoned.local_geography_json,
            founded_sim_year=year,
            status="active",
            consecutive_empty_years=0,
        )
        self.settlements_by_id[new_sid] = st
        self.rebuild_settlement_region_index()
        return st

    def _create_first_settlement_in_region(self, region_id: str) -> SettlementState:
        region = get_region(region_id, world=self.world, db_path=self.db_path)
        lex = PlacenameLexicon.from_db(db_path=self.db_path)
        rng = make_settlement_name_rng(
            self.world, region.region_id, salt=self.placename_rng_salt
        )
        gen, geo_json = seed_settlement_naming_for_region(
            world=self.world,
            region=region,
            ctx=self,
            lex=lex,
            rng=rng,
            settlement_slots=1,
            site_slot=1,
        )
        rid = region.region_id
        seq = next_settlement_sequence(rid, list(self.settlements_by_id.keys()))
        sid = make_settlement_id(rid, seq)
        year = self._settlement_founding_year()
        st = SettlementState(
            region_id=rid,
            region_display_name=(region.region_name or "").strip() or rid,
            settlement_id=sid,
            site_slot=1,
            resident_count=0,
            household_cap=0,
            display_name=gen.display_name,
            etymology=gen.etymology,
            name_category_primary=gen.primary_category,
            name_category_secondary=gen.secondary_category,
            name_culture_primary=gen.culture_primary,
            name_culture_secondary=gen.culture_secondary,
            local_geography_json=geo_json,
            founded_sim_year=year,
            status="active",
            consecutive_empty_years=0,
        )
        self.settlements_by_id[sid] = st
        self.rebuild_settlement_region_index()
        return st

    def ensure_active_settlement_for_region(self, region_id: str) -> SettlementState:
        rid = (region_id or "").strip()
        act = self.active_settlements_in_region(rid)
        if act:
            return act[0]
        abandoned_s1 = [
            st
            for st in self.settlements_by_id.values()
            if st.region_id == rid
            and (st.status or "").strip().lower() == "abandoned"
            and int(st.site_slot) == 1
        ]
        if abandoned_s1:
            latest = max(abandoned_s1, key=lambda s: int(s.abandoned_sim_year or 0))
            return self.reestablish_from_abandoned(latest)
        return self._create_first_settlement_in_region(rid)

    def resolve_settlement_for_birth(
        self, region_id: str, settlement_id_hint: str | None
    ) -> SettlementState:
        rid = (region_id or "").strip()
        hint = (settlement_id_hint or "").strip()
        if hint:
            st = self.settlements_by_id.get(hint)
            if st is not None and (st.status or "").strip().lower() == "active":
                return st
            if st is not None and (st.status or "").strip().lower() == "abandoned":
                return self.reestablish_from_abandoned(st)
        return self.ensure_active_settlement_for_region(rid)

    def _site_world_xy_from_geo_json(
        self,
        local_geography_json: str | None,
        site_slot: int,
        region_id: str,
    ) -> tuple[float, float] | None:
        if not local_geography_json:
            return None
        try:
            data = json.loads(local_geography_json)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        sites = data.get("settlements")
        if not isinstance(sites, list):
            return None
        target_slot = max(0, int(site_slot) - 1)
        for site in sites:
            if not isinstance(site, dict):
                continue
            try:
                if int(site.get("settlement_slot", 0)) != target_slot:
                    continue
                wx = site.get("world_x")
                wy = site.get("world_y")
                if wx is not None and wy is not None:
                    return (float(wx), float(wy))
                lx = float(site.get("x", 0.5))
                ly = float(site.get("y", 0.5))
            except (TypeError, ValueError):
                return None
            return project_local_point_to_region_footprint(
                self.world_map_geometry_for_settlements(),
                region_id,
                (lx, ly),
            )
        return None

    def active_settlement_in_same_map_polygon(
        self,
        region_id: str,
        *,
        site_slot: int,
        local_geography_json: str | None,
    ) -> SettlementState | None:
        """Return an active settlement already occupying the proposed site's map polygon."""
        rid = (region_id or "").strip()
        candidate_xy = self._site_world_xy_from_geo_json(local_geography_json, site_slot, rid)
        if candidate_xy is None:
            return None
        geometry = self.world_map_geometry_for_settlements()
        candidate_micro_id = micro_cell_id_for_world_point(
            geometry,
            candidate_xy,
            region_id=rid,
        )
        if not candidate_micro_id:
            return None
        for st in self.active_settlements_in_region(rid):
            if int(st.site_slot) == int(site_slot):
                continue
            st_xy = self._site_world_xy_from_geo_json(
                local_geography_json,
                int(st.site_slot),
                rid,
            )
            if st_xy is None:
                st_xy = self._site_world_xy_from_geo_json(
                    st.local_geography_json,
                    int(st.site_slot),
                    rid,
                )
            if st_xy is None:
                continue
            if micro_cell_id_for_world_point(geometry, st_xy, region_id=rid) == candidate_micro_id:
                return st
        return None

    def create_additional_active_settlement(self, region_id: str) -> SettlementState:
        rid = (region_id or "").strip()
        region = get_region(rid, world=self.world, db_path=self.db_path)
        slot = self.max_site_slot_in_region(rid) + 1
        lex = PlacenameLexicon.from_db(db_path=self.db_path)
        r_rng = make_settlement_name_rng(
            self.world, rid, salt=self.placename_rng_salt + slot * 97
        )
        gen, geo_json = seed_settlement_naming_for_region(
            world=self.world,
            region=region,
            ctx=self,
            lex=lex,
            rng=r_rng,
            settlement_slots=slot,
            site_slot=slot,
        )
        existing = self.active_settlement_in_same_map_polygon(
            rid,
            site_slot=slot,
            local_geography_json=geo_json,
        )
        if existing is not None:
            return existing
        seq = next_settlement_sequence(rid, list(self.settlements_by_id.keys()))
        sid = make_settlement_id(rid, seq)
        year = self._settlement_founding_year()
        st = SettlementState(
            region_id=rid,
            region_display_name=(region.region_name or "").strip() or rid,
            settlement_id=sid,
            site_slot=slot,
            resident_count=0,
            household_cap=0,
            display_name=gen.display_name,
            etymology=gen.etymology,
            name_category_primary=gen.primary_category,
            name_category_secondary=gen.secondary_category,
            name_culture_primary=gen.culture_primary,
            name_culture_secondary=gen.culture_secondary,
            local_geography_json=geo_json,
            founded_sim_year=year,
            status="active",
            consecutive_empty_years=0,
        )
        self.settlements_by_id[sid] = st
        self._set_region_local_geography(rid, geo_json)
        self.rebuild_settlement_region_index()
        return st

    def maybe_spin_off_birth_settlement(
        self,
        region_id: str,
        mother_settlement_id: str | None,
        rng: random.Random,
    ) -> tuple[str, str | None]:
        """Optionally found a new hamlet in ``region_id`` when pressure and RNG allow.

        Colonist families accrue per region: each time the crowdedness + spinoff dice
        succeed, :attr:`spinoff_pending_families_by_region` increments; only after
        :attr:`spinoff_families_required` such events is a settlement created and the
        birthing household may relocate there.
        """
        rid = (region_id or "").strip()
        if not rid:
            return region_id, mother_settlement_id
        cap_eff = self.effective_regional_population_cap(rid)
        census = self.count_alive_in_region(rid)
        if cap_eff <= 0 or census >= cap_eff:
            self.spinoff_pending_families_by_region.pop(rid, None)
            return rid, mother_settlement_id
        mother_sid = (mother_settlement_id or "").strip()
        if mother_sid:
            mother_st = self.settlements_by_id.get(mother_sid)
            if (
                mother_st is None
                or mother_st.region_id != rid
                or (mother_st.status or "").strip().lower() != "active"
            ):
                return rid, mother_settlement_id
        else:
            act = self.active_settlements_in_region(rid)
            if len(act) < 1:
                return rid, mother_settlement_id
        mp = self.count_alive_in_settlement(mother_sid) if mother_sid else 0
        min_pop = max(1, int(self.spinoff_min_mother_settlement_population))
        if mp < min_pop:
            return rid, mother_settlement_id
        sim_y = int(self.current_year or self.simulation_start_year)
        last_y = int(self.last_spinoff_sim_year_by_region.get(rid, -10**9))
        if sim_y - last_y < int(self.spinoff_cooldown_years):
            return rid, mother_settlement_id
        crowded_threshold = max(40, cap_eff // 25)
        crowded = mp >= crowded_threshold
        if not crowded and rng.random() > 0.03:
            return rid, mother_settlement_id
        if rng.random() > 0.12:
            return rid, mother_settlement_id
        req = max(1, int(self.spinoff_families_required))
        prev = int(self.spinoff_pending_families_by_region.get(rid, 0))
        nxt = prev + 1
        self.spinoff_pending_families_by_region[rid] = nxt
        if nxt < req:
            return rid, mother_settlement_id
        self.spinoff_pending_families_by_region[rid] = 0
        new_st = self.create_additional_active_settlement(rid)
        self.last_spinoff_sim_year_by_region[rid] = sim_y
        return rid, new_st.settlement_id

    def record_year_summary(
        self,
        *,
        year: int,
        births_count: int,
        deaths_count: int,
        mortality_rates: dict[str, float],
        evolve_settlements_this_tick: bool = True,
        persist_to_save: bool = True,
    ) -> None:
        prof = simulation_timing.active_for_year(year)
        tpc = time.perf_counter
        self.current_year = year

        if prof:
            t0 = tpc()
        self.apply_pending_settlement_moves(year)
        if prof:
            simulation_timing.accumulate("summary.apply_pending_moves", tpc() - t0)

        if evolve_settlements_this_tick:
            if prof:
                t0 = tpc()
            self.evolve_settlements_one_year()
            if prof:
                simulation_timing.accumulate("summary.evolve_settlements", tpc() - t0)
        if persist_to_save:
            if prof:
                t0 = tpc()
            set_world_current_year(
                current_year=year,
                config_db_path=self.db_path,
                save_db_path=self.save_db_path,
                world=self.world,
            )
            if prof:
                simulation_timing.accumulate("summary.set_world_year", tpc() - t0)
        if prof:
            t0 = tpc()
        self.refresh_current_people_life_stages(year)
        if prof:
            simulation_timing.accumulate("summary.refresh_life_stages", tpc() - t0)
        from library.simulation_careers import simulation_careers_annual_tick
        from library.simulation_migration import simulation_migration_annual_tick
        from library.simulation_mind_body import simulation_mind_body_annual_tick
        from library.simulation_social import simulation_social_annual_tick
        from library.simulation_trade_networks import simulation_trade_networks_annual_tick

        if prof:
            t0 = tpc()
        simulation_mind_body_annual_tick(self, year)
        if prof:
            simulation_timing.accumulate("summary.mind_body", tpc() - t0)
        if prof:
            t0 = tpc()
        simulation_careers_annual_tick(self, year)
        # Careers has its own exclusive inner profile phases.
        if prof:
            t0 = tpc()
        simulation_migration_annual_tick(self, year)
        if prof:
            simulation_timing.accumulate("summary.migration", tpc() - t0)
        if prof:
            t0 = tpc()
        simulation_trade_networks_annual_tick(self, year)
        if prof:
            simulation_timing.accumulate("summary.trade_networks", tpc() - t0)
        if prof:
            t0 = tpc()
        simulation_social_annual_tick(self, year)
        if prof:
            simulation_timing.accumulate("summary.social", tpc() - t0)
        # After social: partnerships and paramours resolved for the year; jobs and
        # migration already updated residence. Household care runs next so implicit
        # households match final settlements before orphan routing and childcare math.
        # Incidents run after household pressure and before government so deaths can
        # affect office succession in the same year. Economy runs last so wages and
        # pooled draws use final residence.
        from library.simulation_household_care import simulation_household_care_annual_tick

        if prof:
            t0 = tpc()
        simulation_household_care_annual_tick(self, year)
        if prof:
            simulation_timing.accumulate("summary.household_care", tpc() - t0)
        from library.simulation_incidents import simulation_incidents_annual_tick

        if prof:
            t0 = tpc()
        simulation_incidents_annual_tick(self, year)
        if prof:
            simulation_timing.accumulate("summary.incidents", tpc() - t0)
        from library.simulation_government import simulation_government_annual_tick

        if prof:
            t0 = tpc()
        simulation_government_annual_tick(self, year)
        # Government has its own exclusive inner profile phases.
        from library.simulation_economy import simulation_economy_annual_tick

        if prof:
            t0 = tpc()
        simulation_economy_annual_tick(self, year)
        if prof:
            simulation_timing.accumulate("summary.economy", tpc() - t0)
        if self.file_store is not None:
            if prof:
                t0 = tpc()
            latest_cohort_year = max(
                (int(c.sim_year) for c in self.passive_cohorts),
                default=None,
            )
            passive_person_alive_count = sum(
                1
                for rec in self.passive_people.values()
                if rec.person.deathyear is None or int(rec.person.deathyear) > int(year)
            )
            aggregate_cohort_alive_count = sum(
                int(c.population_count)
                for c in self.passive_cohorts
                if latest_cohort_year is not None and int(c.sim_year) == latest_cohort_year
            )
            aggregate_cohort_partnered_count = sum(
                int(c.population_count)
                for c in self.passive_cohorts
                if latest_cohort_year is not None
                and int(c.sim_year) == latest_cohort_year
                and (c.status_bucket or "").strip().lower() == "partnered"
            )
            passive_cohort_births_count = sum(
                int(c.birth_count)
                for c in self.passive_cohorts
                if latest_cohort_year is not None and int(c.sim_year) == latest_cohort_year
            )
            passive_cohort_deaths_count = sum(
                int(c.death_count)
                for c in self.passive_cohorts
                if latest_cohort_year is not None and int(c.sim_year) == latest_cohort_year
            )
            detailed_alive_count = len(self.current_people_ids)
            self.file_store.append_yearly_summary(
                {
                    "year": year,
                    "historical_year": int(mortality_rates["historical_year"]),
                    "milestone_year": int(mortality_rates["milestone_year"]),
                    "alive_count": detailed_alive_count,
                    "dead_count": len(self.people) - detailed_alive_count,
                    "detailed_alive_count": detailed_alive_count,
                    "passive_person_alive_count": passive_person_alive_count,
                    "aggregate_cohort_alive_count": aggregate_cohort_alive_count,
                    "aggregate_cohort_partnered_count": aggregate_cohort_partnered_count,
                    "mixed_mode_alive_count": (
                        detailed_alive_count
                        + passive_person_alive_count
                        + aggregate_cohort_alive_count
                    ),
                    "births_count": births_count,
                    "deaths_count": deaths_count,
                    "passive_cohort_births_count": passive_cohort_births_count,
                    "passive_cohort_deaths_count": passive_cohort_deaths_count,
                    "couples_count": len(self.couples),
                    "infant_mortality_pct": mortality_rates["infant_mortality_pct"],
                    "under5_mortality_pct": mortality_rates["under5_mortality_pct"],
                    "percent_reaching_age_100": mortality_rates["percent_reaching_age_100"],
                }
            )
            if prof:
                simulation_timing.accumulate("file_store.stage_yearly_summary", tpc() - t0)
                t0 = tpc()
            self.file_store.write_current_people_snapshot(
                year=year, person_ids=self.current_people_ids
            )
            if prof:
                simulation_timing.accumulate("file_store.stage_current_people", tpc() - t0)
                t0 = tpc()
            self.file_store.maybe_flush_after_year(year, self.simulation_start_year)
            if prof:
                simulation_timing.accumulate("file_store.flush_if_due", tpc() - t0)
        if persist_to_save:
            checkpoint_simulation_to_save(
                self, full_snapshot=self._should_checkpoint_snapshot(year)
            )
            from library.event_memory_lifecycle import (
                event_memory_lifecycle_annual_tick_for_save,
            )

            if prof:
                t0 = tpc()
            event_memory_lifecycle_annual_tick_for_save(
                self.save_db_path,
                year=year,
                world=self.world,
            )
            if prof:
                simulation_timing.accumulate("summary.event_memory_lifecycle", tpc() - t0)

    def finalize_run(self) -> None:
        """Persist simulation state to ``save.sqlite`` and flush optional file store.

        Safe to call multiple times (e.g. explicit call plus exiting a ``with`` block).
        Use ``with SimulationContext.create(...) as ctx`` so tests and scripts always flush
        buffered events and write a full snapshot unless you intentionally defer persistence.
        """
        if self._run_finalized:
            return
        prof = simulation_timing.enabled()
        tpc = time.perf_counter
        if prof:
            t0 = tpc()
        maybe_import_run_store_events_csv(self)
        if prof:
            simulation_timing.accumulate("finalize.import_run_store_events", tpc() - t0)
            t0 = tpc()
        if self.current_year is not None:
            set_world_current_year(
                current_year=int(self.current_year),
                config_db_path=self.db_path,
                save_db_path=self.save_db_path,
                world=self.world,
            )
            if prof:
                simulation_timing.accumulate("finalize.set_world_year", tpc() - t0)
                t0 = tpc()
        checkpoint_simulation_to_save(self, full_snapshot=True)
        if self.file_store is not None:
            if prof:
                t0 = tpc()
            self.file_store.finalize()
            if prof:
                simulation_timing.accumulate("finalize.file_store_flush", tpc() - t0)
        self._run_finalized = True

    def get_historical_year(self, simulation_year: int | None = None) -> int:
        if simulation_year is None:
            if self.current_year is None:
                raise ValueError("current_year is not set on SimulationContext.")
            simulation_year = self.current_year
        return int(self.history_equivalent_start_year) + (
            int(simulation_year) - int(self.simulation_start_year)
        )

    def get_mortality_rates_for_year(self, simulation_year: int | None = None) -> dict[str, float]:
        historical_year = self.get_historical_year(simulation_year)
        last_idx = len(self.mortality_milestones) - 1
        while (
            self._mortality_index < last_idx
            and historical_year >= int(self.mortality_milestones[self._mortality_index + 1]["year"])
        ):
            self._mortality_index += 1
        row = self.mortality_milestones[self._mortality_index]
        return {
            "historical_year": float(historical_year),
            "milestone_year": float(row["year"]),
            "infant_mortality_pct": float(row["infant_mortality_pct"]),
            "under5_mortality_pct": float(row["under5_mortality_pct"]),
            "percent_reaching_age_100": float(row["percent_reaching_age_100"]),
        }

    def _seed_settlements_from_geography(self) -> None:
        """Settlements are created lazily when people are assigned (see ``ensure_active_settlement_for_region``)."""
        self.settlements_by_id = {}
        self.settlement_ids_by_region = {}

    def evolve_settlements_one_year(self) -> None:
        if not self.settlements_by_id:
            return
        self.sync_settlement_resident_counts()
        regions = {r.region_id: r for r in list_regions(world=self.world, db_path=self.db_path)}
        rng = random.Random(
            int(self.placename_rng_salt)
            + int(self.current_year or self.simulation_start_year)
            + 91331
        )
        next_by_id: dict[str, SettlementState] = {}
        abandon_year = int(self.current_year or self.simulation_start_year)
        for sid, state in self.settlements_by_id.items():
            if (state.status or "").strip().lower() != "active":
                next_by_id[sid] = state
                continue
            if state.region_id not in regions:
                next_by_id[sid] = state
                continue
            rc = max(0, int(state.resident_count))
            ce = 0 if rc > 0 else int(state.consecutive_empty_years) + 1
            if rc == 0 and roll_abandon_this_year(ce, rng):
                next_by_id[sid] = replace(
                    state,
                    status="abandoned",
                    abandoned_sim_year=abandon_year,
                    resident_count=0,
                    household_cap=0,
                    consecutive_empty_years=0,
                )
                continue
            connectivity = region_connectivity_score(
                state.region_id,
                world=self.world,
                db_path=self.db_path,
                simulation_year=self.current_year,
            )
            eff_cap = self.effective_regional_population_cap(state.region_id)
            evolved = evolve_settlement(
                replace(state, consecutive_empty_years=ce),
                resident_count=rc,
                carrying_capacity=eff_cap,
                connectivity_score=connectivity,
            )
            next_by_id[sid] = evolved
        self.settlements_by_id = next_by_id
        self.rebuild_settlement_region_index()


def _world_start_fields(*, db_path: Path, world: str) -> tuple[int, int, int]:
    """Return ``(start_year, history_equivalent_start_year, checkpoint_full_every_n)``.

    ``checkpoint_full_every_n`` defaults to ``10`` when the column is absent or invalid
    (full ``simulation_*`` snapshot to ``save.sqlite`` every N completed sim years;
    ``finalize_run`` always writes a full snapshot).
    """
    default_chk = 10
    with closing(_connect(db_path)) as conn:
        col_rows = conn.execute("PRAGMA table_info(world_start)").fetchall()
        col_names = {str(r[1]) for r in col_rows}
        if "checkpoint_full_snapshot_every_n_years" in col_names:
            row = conn.execute(
                """
                SELECT start_year, history_equivalent_start_year,
                       checkpoint_full_snapshot_every_n_years
                FROM world_start WHERE world = ?
                """,
                (world.strip(),),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT start_year, history_equivalent_start_year
                FROM world_start WHERE world = ?
                """,
                (world.strip(),),
            ).fetchone()
    if row is None:
        raise LookupError(f"No world_start row for world={world!r}")
    start_year = _as_int(row["start_year"], 0)
    history_year = _as_int(row["history_equivalent_start_year"], start_year)
    if start_year == 0:
        raise LookupError(f"world_start.start_year invalid for world={world!r}")
    chk = default_chk
    if "checkpoint_full_snapshot_every_n_years" in col_names:
        raw = row["checkpoint_full_snapshot_every_n_years"]
        if raw is not None and str(raw).strip() != "":
            chk = _as_int(raw, default_chk)
    if chk < 1:
        chk = default_chk
    return start_year, history_year, chk


def _load_mortality_milestones(*, db_path: Path) -> list[dict[str, Any]]:
    with closing(_connect(db_path)) as conn:
        try:
            rows = conn.execute(
                """
                SELECT year, infant_mortality_pct, under5_mortality_pct, percent_reaching_age_100
                FROM historical_mortality_milestones
                ORDER BY CAST(year AS INTEGER) ASC
                """
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise LookupError(
                "historical_mortality_milestones table missing. Run: python utils/util_load_config.py --world default"
            ) from exc
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "year": _as_int(row["year"], 0),
                "infant_mortality_pct": float(row["infant_mortality_pct"]),
                "under5_mortality_pct": float(row["under5_mortality_pct"]),
                "percent_reaching_age_100": float(row["percent_reaching_age_100"]),
            }
        )
    return out

