# Project state

**As of:** 2026-10-30
**Repository:** `/Users/aleksandrsemirhanov/Desktop/Ozon-profit-control`
**Branch/base:** `main` at `3aed6ab`; existing working tree was already heavily modified before this assessment. Preserve those changes until their contents are classified.

## Active status

- Initial assessment is recorded; no ingestion or recommendation code has been changed in this iteration.
- PostgreSQL 16 is running on the existing persistent Docker volume `docker_ozon_pgdata`. It contains schemas and historical data; it was not initialized empty.
- Runtime is intentionally stopped while no valid WB token is available. Its latest captured attempts failed before collection when PostgreSQL was unreachable. Earlier runtime logs show Wildberries rejected its API token as expired on 2026-09-16.
- The owner asked to continue using the latest saved data while no replacement WB token is available. No live WB request is possible until credentials are refreshed.
- Local `.env` remains ignored, its file permissions are owner-only (`0600`), and the local PostgreSQL password was rotated to a random value and verified. No credential value was written to project documents.
- The GitHub remote `origin` is verified and tracks `main`. The already-committed local base `3aed6ab` was pushed as a fast-forward; no working-tree files were included in that push.
- The GitHub repository is public. Treat every committed file as public; exclude local exports, raw extracts, generated payloads, and credentials.
- Compose now reads database credentials from the local `.env`, binds PostgreSQL to loopback, checks database health, and waits for PostgreSQL readiness before starting runtime. The local database was recreated on its existing volume and is healthy.
- `.gitignore` now excludes local extracts, generated reports/payloads, downloaded marketplace images, and run state by default. Existing files were not deleted.
- Active WB connector code still has TLS-verification overrides; remove those only with a verified CA/default trust configuration and source-specific validation.

## Latest observed `client_demo` data

| Source | Latest event / snapshot date | Latest load timestamp |
|---|---:|---:|
| Sales | 2026-08-17 | 2026-08-17 20:22 UTC |
| Orders | 2026-08-17 | 2026-09-09 14:08 UTC |
| Finance | 2026-07-27 | 2026-07-30 20:54 UTC |
| Advertising | 2026-07-26 | 2026-07-26 20:09 UTC |
| Paid storage | 2026-05-04 | 2026-07-26 20:11 UTC |
| Stock | 2026-07-12 | 2026-07-12 20:04 UTC |
| Products | Snapshot table; no event date | 2026-08-17 20:28 UTC |

These dates describe stored rows, not proof that every interval, campaign, warehouse, or report date is complete.

## Reliability and performance evidence

- The last `ingestion_run` records show product collection succeeded on 2026-08-17; orders were still running/retrying and later ended with `error` on 2026-09-10.
- Request history runs only through 2026-09-10. It contains 71 orders requests with HTTP 401, 29 orders requests with HTTP 429, and 3,568 ads requests with HTTP 429. Historical successful request latency averages: sales 6.12 seconds (273 requests), orders 3.08 seconds (33), ads 17.75 seconds (686). These are old request-level numbers, not current catch-up durations.
- A saved March 2026 sales comparison recorded one cumulative request in 1.57 seconds versus ten safe requests in 627.04 seconds for the same 709 rows. This is a useful optimization result, not an end-to-end automatic collection benchmark.
- Current runtime has no common last-success watermark. Freshness checks vary by source and some use only maximum event date, which can hide gaps. The read-only audit also found source-specific resume/completion weaknesses: orders can hit a response cap without an incremental resume point; ads can skip windows based on date maximum; stock relies partly on wall-clock logs; successful empty paid-storage windows are not consistently persisted.
- Code-path review confirmed the specific runtime risks: sales can be skipped when its maximum sale date is recent even if earlier dates are missing; the bulk sales runner requests one page and does not enforce a complete-page/interval proof; orders runner uses cursor pagination but caps each run at two pages and the runtime skip decision checks only maximum order date; ads has campaign-window state internally, but the runtime can bypass it when any row reaches the target date. WB source entry points also default TLS verification off via `WB_SSL_NO_VERIFY=1`; this must be corrected before routine credentialed operation.
- A historical ads run hit rate limits in every tested window; finance and paid-storage artifacts also document network/rate-limit deferrals.

## External dependencies and open decisions

1. A valid WB API token is needed to measure and execute live recovery; the owner confirmed none is available now.
2. GitHub remote and branch ancestry are verified; only named, reviewed paths may be staged because the worktree contains extensive independent work.
3. Generated outputs, raw exports, local database schemas, and legacy notes require classification before cleanup. Do not delete or broadly stage them yet.
4. Agent roles and model routing remain a draft until the owner reviews and co-designs them.

## Immediate next actions

1. Remove insecure TLS defaults and replace maximum-date runtime skips with checks against persisted source-specific completion state; preserve API rate limits and make orders resume across runs.
2. Classify the existing worktree before removing project material; retain only reviewed source, reproducible assets, and canonical documentation.
3. Co-design agent instructions and the adversarial-review acceptance rubric before routing implementation work.
