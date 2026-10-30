# Iteration record

Add one factual entry after each closed phase. Link or name the evidence used; keep long command output and raw data out of this file.

## 2026-10-30 — Initial project assessment and local recovery

- **Goal:** establish a safe baseline for the requested WB collection, repository cleanup, agent workflow, and recommendation rebuild.
- **Baseline:** local `main` at `3aed6ab`; extensive pre-existing tracked and untracked changes; Docker service was unavailable; local `.env` was mode `0644`; saved WB token had expired.
- **Changes:** started Docker Desktop; recreated only the stopped PostgreSQL container on its existing named data volume; tightened `.env` permissions to `0600` and rotated the local database role password; replaced the Compose password literal with local environment references; bound PostgreSQL to loopback; added a health check and runtime readiness dependency; added initial project rules and assessment documents; updated the README launch command and ignore policy; configured the verified GitHub remote and pushed the existing base commit as a fast-forward. Stopped the runtime while the WB token is unavailable.
- **Evidence:** database `client_demo` queried read-only; source freshness and request outcomes recorded in `PROJECT_STATE.md`; saved March sales speed comparison read from `exports/wb_sales_bulk_cumulative_experiment_v1/sales_bulk_cumulative_experiment_report.md`; Compose configuration validated with the local env file; PostgreSQL reported `healthy`; database connectivity and `client_demo` schema remained available; `git diff --check` passed after current edits.
- **Result:** saved database is accessible and intact; latest source data is stale and source-specific completion/resume gaps are documented; runtime logs document an expired WB token and a database connectivity outage; live catch-up remains unmeasured because the owner has no replacement token. The shared GitHub branch advanced from `0850b08` to `3aed6ab` without rewriting history.
- **Limitations / next:** collector code was not changed and no live API or collection pipeline was run. The offline source audit is complete; next, classify existing uncommitted/generated files before cleanup and design completion checkpoints before changing collectors. The reviewed project-rule, state, and runtime-configuration files were committed and pushed in `ef38f88`.

## 2026-10-30 — WB collector execution-path audit

- **Goal:** locate the concrete causes of stale/gapped collection and unsafe network defaults before changing collector behavior.
- **Baseline:** runtime entry points for sales, orders, and ads use maximum event date as a skip condition; order requests are capped at two pages per run; sales bulk fetch is limited to one page; ads has persisted campaign-window state below the runtime gate.
- **Changes:** reviewed runtime, source runners, ingestion queue/resume flow, and WB TLS configuration; documented the specific defects and next implementation order in `PROJECT_STATE.md` and `PROJECT_PLAN.md`. No collector code changed.
- **Evidence:** source inspection of `scripts/run_profit_control_canonical_runtime_v1.py`, `scripts/wb_sales_canonical_bulk_runner_v1.py`, `scripts/wb_orders_canonical_runner_v1.py`, `scripts/wb_ads_canonical_runner_v1.py`, `src/ingestion/runner.py`, and `src/connectors/wb/http.py`.
- **Result:** established that max-date freshness can mask interval gaps; order runs have a bounded page pass; sales bulk does not prove full pagination; ads completion state exists but runtime may skip before consulting it; routine entry points turn off TLS verification by default.
- **Limitations / next:** no live token is available, so provider behavior cannot be confirmed. Fix secure TLS defaults first, then replace unsafe skip decisions with persistent completeness checks and exercise the established acceptance checks before any live backfill.
