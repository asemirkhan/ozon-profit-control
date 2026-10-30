# Project plan

**Status:** working assessment, to be refined with the project owner.
**Updated:** 2026-10-30

This plan orders the requested work by dependency. Each phase is closed with evidence, an updated project state, an iteration record, and a focused Git commit. Do not begin the next phase while its acceptance gate is unmet.

## Phase 0 — Protect and describe the current state

**Purpose:** preserve the working tree, recover local services safely, define durable project rules, and document what is actually running.

**Initial findings:** the checkout has extensive uncommitted changes; local PostgreSQL was stopped; `.env` permissions were too broad. The PostgreSQL container was recreated on its existing named volume, `.env` permissions were tightened without printing its contents, and the likely GitHub remote was verified and configured. The user asked to proceed from stored data while the WB token is unavailable.

**Acceptance:** project rules and a current evidence-based state are documented; no user changes are lost; credentials and raw exports are not staged; the database is reachable and runtime waits for database readiness; the phase is committed to the verified GitHub repository once the existing work tree is reconciled safely.

## Phase 1 — Prove automated Wildberries collection and close reliability gaps

**Purpose:** measure each source from its last defensible checkpoint to the current collection boundary, then repair collection gaps and retry behavior.

**Observed baseline (2026-10-30):** in `client_demo`, sales and orders reach 2026-08-17, finance reaches 2026-07-27, ads 2026-07-26, paid storage 2026-05-04, stock 2026-07-12, and products were last loaded 2026-08-17. Saved runtime logs show an expired-token HTTP 401 on 2026-09-16. A later cycle failed at database bootstrap because the `postgres` host was unavailable. Historical request logs show substantial 429 rates, especially ads. A March sales experiment measured 1.57 seconds for one bulk request versus 627.04 seconds for ten paced requests; that result is not a current end-to-end benchmark.

**Work order:**

1. Produce a read-only inventory of source freshness, interval gaps, request outcomes, and measured durations from the database and saved run evidence.
2. Replace date-maximum or wall-clock skip decisions where they can hide incomplete ranges with source-specific completion evidence. Preserve existing finance closeout semantics.
3. Make retries and resumable checkpoints explicit for capped, 429-limited, and interrupted source runs. Confirm empty-but-successful reports can be marked complete.
4. Improve service startup ordering and health reporting so runtime waits for PostgreSQL readiness and reports a failed phase clearly.
5. When a valid token is available, run the automatic recovery from the stored per-source checkpoint through the current date; measure duration, requests, retries, rows, date coverage, duplicates, and reconciliation. Do not call this phase complete before a live run proves it.

**Acceptance:** each source has a recorded checkpoint and covered interval; no freshness skip masks a required gap; interrupted runs resume safely; 429/401/network failures are distinguishable and actionable; repeated load is idempotent; full automatic recovery and data-quality checks pass with a valid token.

## Phase 2 — Establish the maintained repository and remove disposable clutter

**Purpose:** leave one canonical, understandable project tree and keep only reproducible inputs, source, tests, deployment files, and current documentation in Git.

**Work order:** inventory each generated folder; classify files as maintained input, reproducible output, sensitive local data, historical evidence, or disposable cache; keep a restorable copy for anything with unclear value; create a strict allowlist/ignore policy; remove only confirmed disposable files; reconcile deleted/renamed old documentation against the current README and architecture.

**Acceptance:** all durable project rules and current decisions have one canonical Markdown home; raw marketplace data, secrets, caches, and unreviewed exports cannot be committed accidentally; no unexplained project files remain; required assets are reproducible or explicitly tracked; GitHub is the verified source of history.

## Phase 3 — Co-design the agent workflow and model routing

**Purpose:** make agent work bounded, reviewable, and resistant to plausible but unsupported output.

**Draft design for discussion:** one coordinator owns integration and the canonical context; specialist workers receive a narrow file/data scope and return evidence plus a proposed change; an adversarial reviewer (“хейтер”) works read-only against acceptance criteria and tries to disprove every causal claim; a final reviewer checks scope, security, and verification evidence. Parallelize independent discovery or changes to disjoint files only. Never have two agents edit the same file or share an unbounded task context.

**Draft model routing:** use a fast/efficient model for mechanical, bounded transformations and routine checks; use a stronger reasoning model for causal analysis, architecture decisions, security review, and adversarial challenge. Model choice is an execution setting, not a substitute for written evidence or deterministic acceptance checks.

**Acceptance:** the owner reviews and agrees on concise role instructions, file boundaries, completion criteria, escalation rules, and model routing; a real bounded iteration demonstrates independent reviewer findings without overlapping edits.

## Phase 4 — Rebuild recommendations from reconciled causes

**Purpose:** make recommendations cite a measured cause and a feasible action, rather than asking the seller to discover the cause.

**Work order:** define metric grains and source lineage; establish canonical event facts; reconcile amount and operation counts; distinguish mix, operation volume, and tariff/rate effects; build explicit counterexamples and incomplete-data cases; only then enable customer-facing actions. For logistics, separately model successful delivery, cancellations, returns, repeat transport, corrections, warehouse, and tariff. Inventory is evidence for storage, not by itself evidence for delivery logistics per sold unit.

**Acceptance:** classified and unknown source amounts reconcile exactly once; scenarios changing one cause at a time route distinctly; low coverage or ambiguous joins block cause claims; recommendations state evidence, amount, one feasible action, check window, and measurable result; a read-only adversarial review finds no unsupported cause or predicted savings claim.

## Phase 5 — Secure, version, and operate the maintained system

**Purpose:** make GitHub the reliable version history and make routine operation recoverable.

**Work order:** verify remote and branch ancestry; commit each closed iteration; add secret/raw-data protections and automated checks; document token rotation, database backup/restore, runtime health, recovery and release; verify TLS and access control; preserve backups before destructive cleanup.

**Acceptance:** focused commits are pushed without rewriting shared history; secret and raw-data checks block unsafe changes; restore is exercised from a backup; operational alerts identify source, phase, checkpoint, and next action without leaking secrets.
