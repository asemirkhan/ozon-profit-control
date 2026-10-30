# Project rules

Profit Control is a Wildberries-first operating and analytics system. These rules apply to every task in this repository. Detailed product behavior belongs in the focused documents linked from `docs/PROJECT_PLAN.md`, not in this file.

## Work in bounded iterations

1. Read this file, `docs/PROJECT_STATE.md`, and the relevant phase in `docs/PROJECT_PLAN.md` before changing code.
2. Record the current commit and `git status`. Preserve all existing changes. Never use `git reset --hard`, `git clean`, or broad staging as a shortcut.
3. Work on one phase and one primary outcome at a time. Do not mix unrelated cabinet, marketing, ingestion, and recommendation changes.
4. Before implementation, state the files and behavior in scope, the evidence that supports the change, the acceptance checks, and the paths explicitly out of scope.
5. After implementation, run only the checks needed for the acceptance criteria. Record their actual results; never describe an unrun check as passed.
6. Before handing off an iteration, update `docs/PROJECT_STATE.md` and append one concise entry to `docs/ITERATIONS.md`.

## Evidence and data

- Separate observed data, derived measurements, hypotheses, and unknowns.
- Do not claim a source is complete because its maximum date is recent. Check interval or snapshot completion evidence at the source's actual grain.
- Preserve raw measures, units, timestamps, source IDs, and sign conventions until a documented transformation is justified.
- A business recommendation may name a cause only when the contributing records and amount reconcile to source data. Otherwise label it as unconfirmed and request a bounded diagnostic check.
- Keep customer-facing text short, numeric, and actionable. Do not expose internal analysis, technical labels, or factors that did not affect the result.

## Boundaries and security

- Never print, copy, commit, or add credentials to reports. `.env` stays local; `.env.example` contains placeholders only.
- Treat raw marketplace exports, database snapshots, customer data, logs, and generated artifacts as sensitive until reviewed and explicitly allowlisted.
- Never delete database schemas, source data, exports, or existing user changes without recording what they contain and how they can be restored.
- Keep TLS certificate verification enabled. If a source fails TLS validation, fix the trust configuration rather than disabling verification.
- Parallel workers must have non-overlapping file ownership. Research and review tasks are read-only by default. One integration owner resolves conflicts and updates the canonical project documents.

## Git completion

- Stage named paths only after reviewing their diffs and checking that no secrets, raw customer data, or generated files are included.
- Keep each commit focused on one completed iteration. Push only to the verified project remote and never force-push shared history.
- If the intended remote, branch ancestry, or ownership is unclear, stop before pushing and report the exact uncertainty.
