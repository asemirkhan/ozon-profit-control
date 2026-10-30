from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from src.ingestion.runner import (
    IngestionRunResult,
    run_ingestion,
    run_wb_backfill_worker,
    seed_wb_backfill_queue,
)
from src.loaders.ddl_runner import apply_ddl
from src.marts.build_marts import build_marts
from src.services.finance_confirmation import restore_strict_days_from_reconciliation
from src.utils.db import get_conn

MOSCOW = ZoneInfo("Europe/Moscow")
load_dotenv(ROOT / ".env")

WB_COLLECTION_MIN_HISTORY_DAYS = 35
SERIES_TOTAL_DAYS = int(os.getenv("PROFIT_CONTROL_SERIES_TOTAL_DAYS", "90"))
WB_REQUIRED_COLLECTION_SOURCES = ("products", "sales", "orders")
WB_OPTIONAL_COLLECTION_SOURCES: tuple[str, ...] = ()
WB_RUNTIME_ENV_DEFAULTS = {
    "WB_HTTP_GLOBAL_RPS": "0.2",
    "WB_MAX_REQUESTS_PER_SECOND": "1",
    "WB_MAX_PARALLEL_REQUESTS_PER_SOURCE": "1",
}
WB_SALES_SOLO_ENV_DEFAULTS = {
    "WB_SALES_CANONICAL_MODE": "bulk",
    "WB_GLOBAL_MAX_RPS": "0.18",
    "WB_HTTP_GLOBAL_RPS": "0.18",
    "WB_MAX_REQUESTS_PER_SECOND": "0.18",
    "WB_HTTP_MAX_ATTEMPTS": "3",
    "INGEST_CHUNK_MAX_RETRIES": "1",
    "WB_RATE_LIMIT_BUFFER_SEC": "20",
    "WB_SALES_SUCCESS_EXHAUSTED_COOLDOWN_SEC": "60",
    "WB_WORKER_PER_SOURCE_MIN_INTERVAL_SEC": "4",
    "WB_WORKER_INTER_JOB_SLEEP_SEC": "4",
    "WB_SALES_SOLO_MAX_JOBS": "1",
    "WB_SALES_SOLO_POLL_INTERVAL_SEC": "8",
    "WB_SALES_SOLO_ROUND_SLEEP_SEC": "10",
    "WB_SALES_SOLO_IDLE_BACKOFF_SEC": "30",
    "WB_SALES_SOLO_MAX_ROUNDS": "60",
}
WB_SALES_CANONICAL_ENV_OVERRIDES = {
    "WB_SALES_CANONICAL_MODE": "bulk",
}
WB_FINANCE_CANONICAL_ENV_OVERRIDES: dict[str, str] = {}
WB_ADS_CANONICAL_ENV_OVERRIDES = {
    "WB_ADS_QUEUE_STRATEGY": "priority_v2",
    "WB_ADS_WINDOW_ORDER": "recent_first",
    "WB_ADS_WINDOW_DAYS": "31",
    "WB_ADS_MAX_CAMPAIGNS": "50",
    "WB_ADS_GLOBAL_BATCH_MAX_IDS": "50",
    "WB_ADS_BATCH_FALLBACK_IDS": "50,30,20,1",
    "WB_ADS_ENABLE_SPARSE_BATCHING": "0",
    "WB_ADS_ZERO_ONLY_MIN_COMPLETED_WINDOWS": "2",
    "WB_HTTP_GLOBAL_RPS": "0.12",
    "WB_MAX_REQUESTS_PER_SECOND": "0.12",
    "WB_MAX_PARALLEL_REQUESTS_PER_SOURCE": "1",
}
NON_FINANCE_SOURCE_PAUSE_SEC = int(os.getenv("WB_NON_FINANCE_SOURCE_PAUSE_SEC", "15"))
NON_FINANCE_RATE_LIMIT_COOLDOWN_SEC = int(os.getenv("WB_NON_FINANCE_RATE_LIMIT_COOLDOWN_SEC", "180"))
WB_SALES_FRESHNESS_LAG_DAYS = int(os.getenv("WB_SALES_FRESHNESS_LAG_DAYS", "1"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Canonical zero-touch runtime for Profit Control.")
    parser.add_argument("--schema", default=os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_wb_probe_2")
    parser.add_argument("--days", nargs="*", help="Explicit YYYY-MM-DD days for finance collection.")
    parser.add_argument("--lookback-days", type=int, default=int(os.getenv("PROFIT_CONTROL_RUNTIME_LOOKBACK_DAYS", "14")))
    parser.add_argument(
        "--wb-history-days",
        type=int,
        default=(
            int(os.getenv("PROFIT_CONTROL_RUNTIME_WB_HISTORY_DAYS", "0"))
            or None
        ),
        help="History depth for canonical WB sales/orders ingestion. Default: max(finance lookback, 35).",
    )
    parser.add_argument("--marts-window-days", type=int, default=int(os.getenv("PROFIT_CONTROL_MARTS_WINDOW_DAYS", "90")))
    parser.add_argument("--marts-build-mode", default=os.getenv("PROFIT_CONTROL_MARTS_BUILD_MODE", "published_build"))
    parser.add_argument("--loop-sec", type=int, default=int(os.getenv("PROFIT_CONTROL_RUNTIME_LOOP_SEC", "0")))
    parser.add_argument("--skip-analytics", action="store_true")
    return parser.parse_args()


def now_date() -> datetime:
    return datetime.now(MOSCOW)


def _phase_timer(label: str):
    start_ts = time.time()
    start_iso = now_date().isoformat(timespec="seconds")
    print(f"runtime_phase_start={label} at={start_iso}")

    def _done() -> None:
        end_ts = time.time()
        end_iso = now_date().isoformat(timespec="seconds")
        duration = round(end_ts - start_ts, 2)
        print(f"runtime_phase_end={label} at={end_iso} duration_sec={duration}")

    return _done


def ensure_wb_runtime_env_defaults() -> None:
    for key, value in WB_RUNTIME_ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)


def ensure_wb_sales_solo_env_defaults() -> None:
    for key, value in WB_SALES_SOLO_ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)


def apply_env_overrides(overrides: dict[str, str]) -> dict[str, str | None]:
    previous: dict[str, str | None] = {}
    for key, value in overrides.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    return previous


def restore_env_overrides(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def run_wb_source_solo(
    *,
    schema: str,
    source: str,
    date_from: date | None = None,
    date_to: date | None = None,
    required: bool,
    env_overrides: dict[str, str] | None = None,
) -> None:
    overrides = env_overrides or {}
    prev = apply_env_overrides(overrides)
    try:
        result = run_ingestion(
            marketplace="wb",
            source=source,
            schema=schema,
            date_from=date_from,
            date_to=date_to,
            mode="incremental",
            dry_run=False,
        )
        log_ingestion_result(result)
        if result.error_type == "rate_limit":
            time.sleep(NON_FINANCE_RATE_LIMIT_COOLDOWN_SEC)
        if required and result.status == "error":
            raise RuntimeError(f"Canonical WB source failed: {source}")
    finally:
        restore_env_overrides(prev)
    if NON_FINANCE_SOURCE_PAUSE_SEC > 0:
        time.sleep(NON_FINANCE_SOURCE_PAUSE_SEC)


def resolve_days(args: argparse.Namespace) -> list[str]:
    if args.days:
        return list(args.days)
    today = now_date().date()
    start = today - timedelta(days=max(args.lookback_days - 1, 0))
    return [(start + timedelta(days=offset)).isoformat() for offset in range((today - start).days + 1)]


def day_list_bounds(days: list[str]) -> tuple[date, date]:
    parsed = sorted(date.fromisoformat(day_value) for day_value in days)
    if not parsed:
        today = now_date().date()
        return today, today
    return parsed[0], parsed[-1]


def filter_missing_finance_days(*, schema: str, days: list[str]) -> list[str]:
    if not days:
        return []
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT day, COALESCE(final_analytic_usable_flag, FALSE)
                    FROM {schema}.finance_day_state
                    WHERE day = ANY(%s)
                    """,
                    (days,),
                )
                strict_by_day = {row[0].isoformat(): bool(row[1]) for row in cur.fetchall()}
    except Exception as exc:
        print(f"finance_day_filter_error={exc}")
        return days

    pending: list[str] = []
    for day_value in days:
        if not strict_by_day.get(day_value, False):
            pending.append(day_value)
    return pending


def resolve_wb_history_days(args: argparse.Namespace) -> int:
    explicit = getattr(args, "wb_history_days", None)
    if explicit is not None:
        return max(int(explicit), 1)
    return max(int(args.lookback_days), WB_COLLECTION_MIN_HISTORY_DAYS, int(args.marts_window_days), SERIES_TOTAL_DAYS)


def resolve_wb_collection_period(args: argparse.Namespace, finance_days: list[str]) -> tuple[date, date]:
    finance_start, finance_end = day_list_bounds(finance_days)
    history_days = resolve_wb_history_days(args)
    history_start = finance_end - timedelta(days=max(history_days - 1, 0))
    return min(finance_start, history_start), finance_end


def run_script(
    script_name: str,
    *,
    env: dict[str, str],
    extra_args: list[str] | None = None,
    required: bool = True,
) -> bool:
    cmd = [sys.executable, str(ROOT / "scripts" / script_name)]
    if extra_args:
        cmd.extend(extra_args)
    completed = subprocess.run(cmd, cwd=ROOT, env=env, check=False)
    if completed.returncode == 0:
        return True
    print(f"runtime_phase=script_failed script={script_name} exit_code={completed.returncode}")
    if required:
        raise subprocess.CalledProcessError(completed.returncode, cmd)
    return False


def run_script_background(
    script_name: str,
    *,
    env: dict[str, str],
    extra_args: list[str] | None = None,
) -> int:
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{Path(script_name).stem}.log"
    cmd = [sys.executable, str(ROOT / "scripts" / script_name)]
    if extra_args:
        cmd.extend(extra_args)
    log_file = log_path.open("a", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        cwd=ROOT,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    print(
        "runtime_phase=background_launch "
        f"script={script_name} pid={proc.pid} log={log_path}"
    )
    return proc.pid


def log_ingestion_result(result: IngestionRunResult) -> None:
    print(
        "runtime_phase=wb_source_result "
        f"source={result.source} status={result.status} raw={result.raw_rows_count} "
        f"normalized={result.normalized_rows_count} loaded={result.loaded_rows_count} "
        f"expected_days={result.expected_days} actual_days={result.actual_days}"
    )


def run_wb_source(
    *,
    schema: str,
    source: str,
    date_from: date | None = None,
    date_to: date | None = None,
    snapshot_date: date | None = None,
    required: bool,
) -> None:
    result = run_ingestion(
        marketplace="wb",
        source=source,
        schema=schema,
        date_from=date_from,
        date_to=date_to,
        mode="incremental",
        dry_run=False,
        snapshot_date=snapshot_date,
    )
    log_ingestion_result(result)
    if required and result.status == "error":
        raise RuntimeError(f"Canonical WB source failed: {source}")


def run_wb_sales_only(args: argparse.Namespace, *, schema: str, finance_days: list[str]) -> None:
    collection_start, collection_end = resolve_wb_collection_period(args, finance_days)
    canonical_mode = WB_SALES_CANONICAL_ENV_OVERRIDES["WB_SALES_CANONICAL_MODE"]
    print(
        "runtime_phase=wb_sales_solo "
        f"schema={schema} period={collection_start.isoformat()}..{collection_end.isoformat()} "
        f"history_days={resolve_wb_history_days(args)} mode={canonical_mode}"
    )
    if wb_sales_already_fresh_enough(schema=schema, target_date=collection_end):
        print(
            "runtime_phase=wb_sales_solo skip_reason=fresh_enough "
            f"target_date={collection_end.isoformat()} lag_days={WB_SALES_FRESHNESS_LAG_DAYS}"
        )
        return
    if canonical_mode == "safe":
        run_wb_sales_solo_backfill(
            schema=schema,
            date_from=collection_start,
            date_to=collection_end,
        )
        return

    env = os.environ.copy()
    env["SCHEMA"] = schema
    env["OZON_SCHEMA"] = schema
    env.update(WB_SALES_CANONICAL_ENV_OVERRIDES)
    run_script(
        "wb_sales_canonical_bulk_runner_v1.py",
        env=env,
        extra_args=[
            "--schema",
            schema,
            "--from",
            collection_start.isoformat(),
            "--to",
            collection_end.isoformat(),
        ],
    )


def run_wb_non_finance_sources(args: argparse.Namespace, *, schema: str, finance_days: list[str]) -> None:
    collection_start, collection_end = resolve_wb_collection_period(args, finance_days)
    env = os.environ.copy()
    env["SCHEMA"] = schema
    env["OZON_SCHEMA"] = schema
    env.update(WB_ADS_CANONICAL_ENV_OVERRIDES)
    print(
        "runtime_phase=wb_non_finance_sources "
        f"schema={schema} period={collection_start.isoformat()}..{collection_end.isoformat()}"
    )
    # products, orders, ads, stock strictly sequential, solo, no parallel
    run_script("wb_products_canonical_runner_v1.py", env=env)
    if wb_orders_already_current(schema=schema, target_date=collection_end):
        print(
            "runtime_phase=wb_orders skip_reason=fresh_enough "
            f"target_date={collection_end.isoformat()}"
        )
    else:
        run_script("wb_orders_canonical_runner_v1.py", env=env)
    if wb_ads_already_current(schema=schema, target_date=collection_end):
        print(
            "runtime_phase=wb_ads skip_reason=fresh_enough "
            f"target_date={collection_end.isoformat()}"
        )
    else:
        run_script(
            "wb_ads_canonical_runner_v1.py",
            env=env,
            extra_args=[
                "--from",
                collection_start.isoformat(),
                "--to",
                collection_end.isoformat(),
            ],
        )
    run_script("wb_stock_canonical_runner_v1.py", env=env, required=False)


def launch_wb_background_sources(*, schema: str) -> None:
    env = os.environ.copy()
    env["SCHEMA"] = schema
    env["OZON_SCHEMA"] = schema
    env.update(WB_ADS_CANONICAL_ENV_OVERRIDES)
    run_script_background("wb_paid_storage_canonical_runner_v1.py", env=env)


def _sales_queue_pending(schema: str, date_from: date, date_to: date) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT COUNT(*)::int
                FROM {schema}.ingestion_backfill_queue
                WHERE schema_name = %s
                  AND marketplace = 'wb'
                  AND source = 'sales'
                  AND target_date BETWEEN %s AND %s
                  AND state <> 'success'
                """,
                (schema, date_from, date_to),
            )
            row = cur.fetchone()
            return int(row[0] or 0)


def wb_sales_already_fresh_enough(*, schema: str, target_date: date) -> bool:
    # A maximum event date cannot prove continuous source coverage. Until a
    # validated completion ledger is available, always run the idempotent catch-up.
    return False


def wb_ads_already_current(*, schema: str, target_date: date) -> bool:
    # Do not skip a campaign-window catch-up based on the latest event date.
    return False


def wb_orders_already_current(*, schema: str, target_date: date) -> bool:
    # Do not skip a day/cursor catch-up based on the latest event date.
    return False


def run_wb_sales_solo_backfill(*, schema: str, date_from: date, date_to: date) -> None:
    ensure_wb_sales_solo_env_defaults()
    seed_wb_backfill_queue(
        schema=schema,
        date_from=date_from,
        date_to=date_to,
        sources=("sales",),
    )

    max_rounds = int(os.getenv("WB_SALES_SOLO_MAX_ROUNDS", "30"))
    max_jobs = int(os.getenv("WB_SALES_SOLO_MAX_JOBS", "2"))
    poll_interval_sec = int(os.getenv("WB_SALES_SOLO_POLL_INTERVAL_SEC", "8"))
    round_sleep_sec = int(os.getenv("WB_SALES_SOLO_ROUND_SLEEP_SEC", "10"))
    idle_backoff_sec = int(os.getenv("WB_SALES_SOLO_IDLE_BACKOFF_SEC", "30"))
    idle_rounds = 0
    for round_idx in range(1, max_rounds + 1):
        pending_before = _sales_queue_pending(schema, date_from, date_to)
        print(
            "runtime_phase=wb_sales_solo "
            f"round={round_idx} pending={pending_before} period={date_from.isoformat()}..{date_to.isoformat()} "
            f"max_jobs={max_jobs} poll_interval_sec={poll_interval_sec}"
        )
        if pending_before <= 0:
            return

        stats = run_wb_backfill_worker(
            schema=schema,
            date_from=date_from,
            date_to=date_to,
            sources=("sales",),
            max_jobs=max_jobs,
            poll_interval_sec=poll_interval_sec,
            once=True,
        )
        processed = int(stats.get("processed", 0) or 0)
        pending_after = _sales_queue_pending(schema, date_from, date_to)
        print(
            "runtime_phase=wb_sales_solo_result "
            f"round={round_idx} processed={processed} pending_after={pending_after}"
        )
        if pending_after <= 0:
            return
        if processed <= 0:
            idle_rounds += 1
            if idle_rounds >= 3:
                raise RuntimeError("Sales solo backfill stalled: no progress in 3 rounds.")
            time.sleep(idle_backoff_sec)
        else:
            idle_rounds = 0
            time.sleep(round_sleep_sec)
    raise RuntimeError("Sales solo backfill did not finish within max rounds.")


def run_cycle(args: argparse.Namespace) -> None:
    ensure_wb_runtime_env_defaults()
    env = os.environ.copy()
    env["SCHEMA"] = args.schema
    env["OZON_SCHEMA"] = args.schema

    print(f"runtime_phase=bootstrap schema={args.schema}")
    done = _phase_timer("bootstrap")
    apply_ddl(args.schema)
    done()

    days = resolve_days(args)
    pending_days = filter_missing_finance_days(schema=args.schema, days=days)
    print(
        "runtime_phase=finance_plan "
        f"days_total={len(days)} pending={len(pending_days)} "
        f"pending_days={','.join(pending_days) or 'none'}"
    )
    if not pending_days:
        print("runtime_phase=finance skip_reason=all_days_strict_usable")
    # 1) sales solo
    done = _phase_timer("wb_sales_solo")
    run_wb_sales_only(args, schema=args.schema, finance_days=days)
    done()

    # 2) finance solo
    print(f"runtime_phase=finance days={','.join(pending_days) or 'none'}")
    done = _phase_timer("finance")
    finance_env = dict(env)
    finance_env.update(WB_FINANCE_CANONICAL_ENV_OVERRIDES)
    if pending_days:
        run_script(
            "wb_finance_canonical_default_runner_v1.py",
            env=finance_env,
            extra_args=["--days", *pending_days],
            required=False,
        )
    restored_days = restore_strict_days_from_reconciliation(
        schema=args.schema,
        days=[date.fromisoformat(day_value) for day_value in days],
    )
    print(
        "runtime_phase=finance_reconciliation_restore "
        f"restored={len(restored_days)} "
        f"latest={restored_days[-1].isoformat() if restored_days else 'none'}"
    )
    done()

    # 3-4) products, stock (sequential, solo)
    done = _phase_timer("wb_non_finance_sources")
    run_wb_non_finance_sources(args, schema=args.schema, finance_days=days)
    done()

    _, collection_end = resolve_wb_collection_period(args, days)
    marts_end = collection_end
    marts_start = marts_end - timedelta(days=max(args.marts_window_days - 1, 0))
    print(
        "runtime_phase=marts "
        f"schema={args.schema} period={marts_start.isoformat()}..{marts_end.isoformat()} mode={args.marts_build_mode}"
    )
    done = _phase_timer("marts")
    build_marts(
        schema=args.schema,
        period_start=marts_start.isoformat(),
        period_end=marts_end.isoformat(),
        build_mode=args.marts_build_mode,
        marketplace="wb",
    )
    done()

    if not args.skip_analytics:
        print("runtime_phase=analytics")
        done = _phase_timer("analytics")
        run_script("build_profit_control_analytics_canonical_runner_v1.py", env=env)
        done()

    done = _phase_timer("wb_background_sources")
    launch_wb_background_sources(schema=args.schema)
    done()

    print("runtime_phase=complete")


def main() -> None:
    args = parse_args()
    if args.loop_sec <= 0:
        run_cycle(args)
        return

    while True:
        cycle_started_at = now_date().isoformat(timespec="seconds")
        print(f"runtime_cycle_started_at={cycle_started_at}")
        try:
            run_cycle(args)
            sleep_sec = args.loop_sec
        except Exception as exc:
            sleep_sec = min(max(int(os.getenv("PROFIT_CONTROL_RUNTIME_FAILURE_RETRY_SEC", "900")), 60), args.loop_sec)
            print(f"runtime_cycle_failed error={type(exc).__name__}:{exc}")
        print(f"runtime_sleep_sec={sleep_sec}")
        time.sleep(sleep_sec)


if __name__ == "__main__":
    main()
