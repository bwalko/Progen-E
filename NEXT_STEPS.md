# Next steps

Prioritized follow-ups for the History Project simulation stack. Operational habits (config reload, baseline tests) and architecture notes live in [AGENTS.md](AGENTS.md) and [dev_rules/session_start.md](dev_rules/session_start.md).

## Medium priority

1. **Tune migration under 5% capacity**  
   See [dev_rules/migration_tuning.md](dev_rules/migration_tuning.md). Short path: reload config if needed → `python utils/run_population_simulation.py --years N` → `python utils/util_print_alive_by_year.py` on `yearly_summary.csv` → adjust `MIGRATION_*` in `library/simulation_migration.py` one knob at a time (prefer fixed-seed reruns to compare).
