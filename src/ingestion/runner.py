from __future__ import annotations

import json
import logging
import os
import threading
import time
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from psycopg import sql

from src.ingestion.source_registry import (
    SourceConfig,
    SourceRunContext,
    get_source_config,
)
from src.normalizers.wb.finance_normalizer import build_wb_finance_normalization_result
from src.ingestion.publish_gate import (
    evaluate_and_upsert_snapshot_state,
    source_criticality,
)
from src.raw_store.writer import write_raw_json
from src.utils.db import get_conn, run_in_transaction

logger = logging.getLogger(__name__)

WB_HEAVY_BACKFILL_SOURCES = frozenset({"orders", "sales", "finance"})
WB_CORE_ECONOMY_SOURCES = frozenset({"sales", "finance"})


@dataclass(frozen=True)
class IngestionChunkResult:
    chunk_id: str
    chunk_date_from: date | None
    chunk_date_to: date | None
    retry_attempts: int
    page_count: int
    raw_files_count: int
    raw_rows_count: int
    normalized_rows_count: int
    loaded_rows_count: int
    status: str
    error_message: str | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class IngestionRunResult:
    run_id: str
    marketplace: str
    source: str
    schema: str
    mode: str
    date_from: date | None
    date_to: date | None
    status: str
    chunks: tuple[IngestionChunkResult, ...]
    raw_rows_count: int
    normalized_rows_count: int
    loaded_rows_count: int
    expected_days: int
    actual_days: int
    missing_days_count: int
    coverage_ratio: float
    expected_entities: int
    actual_entities: int
    row_loss_ratio: float
    error_type: str | None = None
    retry_exhausted: bool = False
    snapshot_id: str | None = None
    snapshot_publish_status: str | None = None


def run_ingestion(
    *,
    marketplace: str,
    source: str,
    schema: str,
    date_from: date | None = None,
    date_to: date | None = None,
    mode: str = "incremental",
    dry_run: bool = False,
    sample_path: str | None = None,
    limit: int = 100,
    max_pages: int = 1000,
    offset: int = 0,
    page: int = 1,
    pagination_mode: str | None = None,
    advert_ids: list[int] | None = None,
    snapshot_date: date | None = None,
    extra: Mapping[str, Any] | None = None,
) -> IngestionRunResult:
    config = get_source_config(marketplace, source)
    if config.marketplace != marketplace:
        raise ValueError(f"Source '{source}' is not configured for marketplace '{marketplace}'")
    if config.requires_period and (date_from is None or date_to is None):
        raise ValueError(f"{marketplace}:{source} requires both --from and --to")
    if date_from and date_to and date_from > date_to:
        raise ValueError(f"Invalid period: {date_from.isoformat()} > {date_to.isoformat()}")

    run_id = _make_run_id()
    chunks = _build_chunks(
        date_from=date_from,
        date_to=date_to,
        chunk_days=config.chunk_days,
        chunk_by_calendar_month=config.chunk_by_calendar_month,
        force_single_chunk=bool(sample_path),
    )
    logger.info(
        "Ingestion run start run_id=%s source=%s:%s schema=%s mode=%s chunks=%s dry_run=%s",
        run_id,
        marketplace,
        source,
        schema,
        mode,
        len(chunks),
        dry_run,
    )
    _audit_run_start(
        run_id=run_id,
        marketplace=marketplace,
        source=source,
        schema=schema,
        mode=mode,
        date_from=date_from,
        date_to=date_to,
    )

    chunk_results: list[IngestionChunkResult] = []
    seen_days: set[date] = set()
    total_raw_rows = 0
    total_normalized = 0
    total_loaded = 0
    run_status = "ok"
    run_errors: list[str] = []
    retry_exhausted = False

    max_chunk_retries = _chunk_retry_limit(config)
    for chunk_index, (chunk_from, chunk_to) in enumerate(chunks, start=1):
        base_chunk_id = f"{run_id}_c{chunk_index:03d}"
        last_attempt_result: IngestionChunkResult | None = None
        attempts_done = 0
        for attempt_index in range(0, max_chunk_retries + 1):
            attempt_chunk_id = (
                base_chunk_id
                if max_chunk_retries <= 0
                else f"{base_chunk_id}_a{attempt_index + 1}"
            )
            attempts_done = attempt_index + 1
            chunk_result = _run_single_chunk(
                config=config,
                schema=schema,
                mode=mode,
                dry_run=dry_run,
                run_id=run_id,
                chunk_id=attempt_chunk_id,
                date_from=date_from,
                date_to=date_to,
                chunk_from=chunk_from,
                chunk_to=chunk_to,
                sample_path=sample_path,
                limit=limit,
                max_pages=max_pages,
                offset=(
                    _backfill_queue_finance_resume_rrdid(
                        schema=schema,
                        marketplace=config.marketplace,
                        source=config.source,
                        target_date=(chunk_from or chunk_to),
                    )
                    if config.source == "finance"
                    else offset
                ),
                page=page,
                pagination_mode=pagination_mode,
                advert_ids=tuple(advert_ids or ()),
                snapshot_date=snapshot_date,
                extra=extra or {},
                seen_days=seen_days,
                retry_attempt=attempts_done,
            )
            last_attempt_result = chunk_result
            if chunk_result.status == "ok":
                break

            error_type = chunk_result.error_type or _classify_error_type(chunk_result.error_message)
            retryable = error_type in {
                "rate_limit",
                "network_error",
                "parse_error",
                "empty_response",
                "unexpected_shape",
                "unexpected_format",
                "parse_or_mapping_issue",
            }
            if attempt_index >= max_chunk_retries or not retryable:
                if attempt_index > 0:
                    retry_exhausted = True
                break
            delay = _chunk_retry_delay(attempt_index)
            logger.warning(
                "Retrying failed chunk run_id=%s chunk=%s attempt=%s/%s error_type=%s sleep=%ss",
                run_id,
                base_chunk_id,
                attempt_index + 1,
                max_chunk_retries + 1,
                error_type,
                delay,
            )
            if delay > 0:
                time.sleep(delay)

        if last_attempt_result is None:
            raise RuntimeError(f"Unexpected empty chunk result for {base_chunk_id}")
        chunk_result = IngestionChunkResult(
            chunk_id=base_chunk_id,
            chunk_date_from=last_attempt_result.chunk_date_from,
            chunk_date_to=last_attempt_result.chunk_date_to,
            retry_attempts=attempts_done,
            page_count=last_attempt_result.page_count,
            raw_files_count=last_attempt_result.raw_files_count,
            raw_rows_count=last_attempt_result.raw_rows_count,
            normalized_rows_count=last_attempt_result.normalized_rows_count,
            loaded_rows_count=last_attempt_result.loaded_rows_count,
            status=last_attempt_result.status,
            error_message=last_attempt_result.error_message,
            error_type=last_attempt_result.error_type,
        )
        chunk_results.append(chunk_result)
        total_raw_rows += chunk_result.raw_rows_count
        total_normalized += chunk_result.normalized_rows_count
        total_loaded += chunk_result.loaded_rows_count
        if chunk_result.status != "ok":
            run_status = "partial" if run_status == "ok" else run_status
            if chunk_result.status == "error":
                run_status = "error"
            if chunk_result.error_message:
                run_errors.append(chunk_result.error_message)

    if _should_use_wb_backfill_queue(
        config=config,
        mode=mode,
        dry_run=dry_run,
        date_from=date_from,
        date_to=date_to,
        sample_path=sample_path,
    ):
        missing_dates = _missing_dates_in_period(
            date_from=date_from,
            date_to=date_to,
            seen_days=seen_days,
        )
        _backfill_queue_mark_seen_days_success(
            schema=schema,
            marketplace=marketplace,
            source=source,
            run_id=run_id,
            days=seen_days,
        )
        if missing_dates:
            _backfill_queue_upsert_dates(
                schema=schema,
                marketplace=marketplace,
                source=source,
                run_id=run_id,
                dates=missing_dates,
            )

        max_inline_cycles = _wb_backfill_inline_max_cycles()
        if max_inline_cycles > 0:
            queue_results, queue_retry_exhausted = _process_wb_backfill_queue(
                config=config,
                schema=schema,
                mode=mode,
                dry_run=dry_run,
                run_id=run_id,
                date_from=date_from,
                date_to=date_to,
                sample_path=sample_path,
                limit=limit,
                max_pages=max_pages,
                offset=offset,
                page=page,
                pagination_mode=pagination_mode,
                advert_ids=tuple(advert_ids or ()),
                snapshot_date=snapshot_date,
                extra=extra or {},
                seen_days=seen_days,
                max_cycles=max_inline_cycles,
            )
            if queue_retry_exhausted:
                retry_exhausted = True
            for queue_result in queue_results:
                chunk_results.append(queue_result)
                total_raw_rows += queue_result.raw_rows_count
                total_normalized += queue_result.normalized_rows_count
                total_loaded += queue_result.loaded_rows_count
                if queue_result.status != "ok":
                    run_status = "partial" if run_status == "ok" else run_status
                    if queue_result.status == "error":
                        run_status = "error"
                    if queue_result.error_message:
                        run_errors.append(queue_result.error_message)

        unresolved_errors = [
            chunk
            for chunk in chunk_results
            if chunk.status == "error"
            and (chunk.chunk_date_from is None or chunk.chunk_date_from not in seen_days)
        ]
        transient_errors = [
            chunk
            for chunk in chunk_results
            if chunk.status == "error"
            and chunk.chunk_date_from is not None
            and chunk.chunk_date_from in seen_days
        ]
        if unresolved_errors:
            run_status = "error"
            run_errors = [chunk.error_message for chunk in unresolved_errors if chunk.error_message]
        elif transient_errors:
            run_status = "partial"
            run_errors = [chunk.error_message for chunk in transient_errors if chunk.error_message]
        else:
            run_status = "ok"
            run_errors = []

    expected_days = _expected_days(config=config, date_from=date_from, date_to=date_to)
    actual_days = len(seen_days)
    missing_days = max(expected_days - actual_days, 0)
    coverage_ratio = _coverage_ratio(expected_days=expected_days, actual_days=actual_days)
    expected_entities = total_raw_rows
    actual_entities = total_loaded if not dry_run else total_normalized
    row_loss_ratio = _row_loss_ratio(
        normalized_rows=total_normalized,
        loaded_rows=total_loaded,
        dry_run=dry_run,
    )
    source_criticality_value = source_criticality(marketplace, source)
    error_type = _classify_error_type(run_errors[0] if run_errors else None)
    usable_for_publish = _is_usable_for_publish(
        source_criticality_value=source_criticality_value,
        run_status=run_status,
        completeness_status=_completeness_status(
            dry_run=dry_run,
            run_status=run_status,
            expected_days=expected_days,
            actual_days=actual_days,
            coverage_ratio=coverage_ratio,
            row_loss_ratio=row_loss_ratio,
        ),
    )
    completeness_status = _completeness_status(
        dry_run=dry_run,
        run_status=run_status,
        expected_days=expected_days,
        actual_days=actual_days,
        coverage_ratio=coverage_ratio,
        row_loss_ratio=row_loss_ratio,
    )
    partial_reason = _build_partial_reason(
        run_status=run_status,
        expected_days=expected_days,
        actual_days=actual_days,
        error_type=error_type,
    )
    _audit_completeness(
        run_id=run_id,
        marketplace=marketplace,
        source=source,
        schema=schema,
        expected_days=expected_days,
        actual_days=actual_days,
        missing_days_count=missing_days,
        coverage_ratio=coverage_ratio,
        expected_entities=expected_entities,
        actual_entities=actual_entities,
        row_loss_ratio=row_loss_ratio,
        status=completeness_status,
        source_criticality_value=source_criticality_value,
        usable_for_publish=usable_for_publish,
        error_type=error_type,
        retry_exhausted=retry_exhausted,
        partial_reason=partial_reason,
    )
    _audit_run_finish(schema=schema, run_id=run_id, status=run_status)
    snapshot_id: str | None = None
    snapshot_publish_status: str | None = None
    if date_from and date_to:
        snapshot_state = evaluate_and_upsert_snapshot_state(
            schema=schema,
            marketplace=marketplace,
            period_from=date_from,
            period_to=date_to,
        )
        snapshot_id = snapshot_state.snapshot_id
        snapshot_publish_status = snapshot_state.publish_status
        logger.info(
            "Snapshot state updated schema=%s marketplace=%s period=%s..%s snapshot_id=%s publish_status=%s",
            schema,
            marketplace,
            date_from.isoformat(),
            date_to.isoformat(),
            snapshot_id,
            snapshot_publish_status,
        )
    logger.info(
        "Ingestion run finish run_id=%s status=%s raw=%s normalized=%s loaded=%s expected_days=%s actual_days=%s",
        run_id,
        run_status,
        total_raw_rows,
        total_normalized,
        total_loaded,
        expected_days,
        actual_days,
    )
    return IngestionRunResult(
        run_id=run_id,
        marketplace=marketplace,
        source=source,
        schema=schema,
        mode=mode,
        date_from=date_from,
        date_to=date_to,
        status=run_status,
        chunks=tuple(chunk_results),
        raw_rows_count=total_raw_rows,
        normalized_rows_count=total_normalized,
        loaded_rows_count=total_loaded,
        expected_days=expected_days,
        actual_days=actual_days,
        missing_days_count=missing_days,
        coverage_ratio=coverage_ratio,
        expected_entities=expected_entities,
        actual_entities=actual_entities,
        row_loss_ratio=row_loss_ratio,
        error_type=error_type,
        retry_exhausted=retry_exhausted,
        snapshot_id=snapshot_id,
        snapshot_publish_status=snapshot_publish_status,
    )


def seed_wb_backfill_queue(
    *,
    schema: str,
    date_from: date,
    date_to: date,
    sources: tuple[str, ...] | None = None,
    run_id: str | None = None,
) -> int:
    if date_from > date_to:
        raise ValueError(f"Invalid period: {date_from.isoformat()} > {date_to.isoformat()}")
    source_list = tuple(sources or tuple(sorted(WB_HEAVY_BACKFILL_SOURCES)))
    supported = [source for source in source_list if source in WB_HEAVY_BACKFILL_SOURCES]
    if not supported:
        return 0
    _ensure_backfill_queue_extended_columns(schema=schema)
    current = date_from
    dates: list[date] = []
    while current <= date_to:
        dates.append(current)
        current += timedelta(days=1)
    if not dates:
        return 0
    queue_run_id = run_id or _make_run_id()
    inserted = 0
    for source in supported:
        _backfill_queue_upsert_dates(
            schema=schema,
            marketplace="wb",
            source=source,
            run_id=queue_run_id,
            dates=dates,
        )
        inserted += len(dates)
    return inserted


def run_wb_backfill_worker(
    *,
    schema: str,
    date_from: date,
    date_to: date,
    sources: tuple[str, ...] | None = None,
    max_jobs: int = 0,
    poll_interval_sec: int = 10,
    once: bool = False,
    limit: int = 100,
    max_pages: int = 1000,
    offset: int = 0,
    page: int = 1,
    pagination_mode: str | None = None,
    advert_ids: tuple[int, ...] = (),
    snapshot_date: date | None = None,
) -> dict[str, int]:
    if date_from > date_to:
        raise ValueError(f"Invalid period: {date_from.isoformat()} > {date_to.isoformat()}")
    requested_sources = tuple(sources or tuple(sorted(WB_HEAVY_BACKFILL_SOURCES)))
    active_sources = tuple(source for source in requested_sources if source in WB_HEAVY_BACKFILL_SOURCES)
    if not active_sources:
        return {"processed": 0, "success": 0, "failed": 0}
    _ensure_backfill_queue_extended_columns(schema=schema)

    processed = 0
    success_count = 0
    failed_count = 0
    sleep_sec = max(int(poll_interval_sec), 1)
    inter_job_sleep_sec = _wb_worker_inter_job_sleep_sec()
    per_source_min_interval_sec = _wb_worker_per_source_min_interval_sec()
    source_last_request_ts: dict[str, float] = {}
    worker_id = f"wb_worker_{uuid4().hex[:12]}"
    lease_sec = _wb_backfill_lease_sec()
    heartbeat_sec = _wb_backfill_heartbeat_sec()
    source_order = _wb_worker_source_order(active_sources)
    source_cursor = 0

    while True:
        if max_jobs > 0 and processed >= max_jobs:
            break
        _backfill_queue_recover_stale_running(
            schema=schema,
            marketplace="wb",
            sources=active_sources,
        )
        candidate = None
        if source_order:
            for idx_delta in range(len(source_order)):
                idx = (source_cursor + idx_delta) % len(source_order)
                source_candidate = source_order[idx]
                if (
                    source_candidate == "orders"
                    and _orders_background_mode_enabled()
                    and not _orders_required_for_analysis()
                    and _core_sources_pending_for_period(
                        schema=schema,
                        marketplace="wb",
                        date_from=date_from,
                        date_to=date_to,
                    )
                ):
                    continue
                candidate = _backfill_queue_next_job(
                    schema=schema,
                    marketplace="wb",
                    sources=(source_candidate,),
                    date_from=date_from,
                    date_to=date_to,
                )
                if candidate is not None:
                    source_cursor = (idx + 1) % len(source_order)
                    break
        if candidate is None:
            if once:
                break
            time.sleep(sleep_sec)
            continue

        source, target_date, attempts, max_attempts = candidate
        _sleep_before_source_request(
            source=source,
            source_last_request_ts=source_last_request_ts,
            min_interval_sec=per_source_min_interval_sec,
        )
        attempt_no = attempts + 1
        job_run_id = _make_run_id()
        _backfill_queue_mark_running(
            schema=schema,
            marketplace="wb",
            source=source,
            run_id=job_run_id,
            target_date=target_date,
            attempts=attempt_no,
            worker_id=worker_id,
            lease_sec=lease_sec,
        )
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=_backfill_queue_heartbeat_loop,
            kwargs={
                "stop_event": heartbeat_stop,
                "schema": schema,
                "marketplace": "wb",
                "source": source,
                "target_date": target_date,
                "worker_id": worker_id,
                "interval_sec": heartbeat_sec,
                "lease_sec": lease_sec,
            },
            daemon=True,
        )
        heartbeat_thread.start()
        job_limit = limit
        job_max_pages = max_pages
        job_pagination_mode = pagination_mode
        if source == "orders":
            job_limit = min(limit, 100)
            job_max_pages = min(max_pages, _wb_orders_pages_per_pass())
        elif source == "sales":
            job_max_pages = 1
            job_pagination_mode = "single"
        try:
            result = run_ingestion(
                marketplace="wb",
                source=source,
                schema=schema,
                date_from=target_date,
                date_to=target_date,
                mode="backfill",
                dry_run=False,
                sample_path=None,
                limit=job_limit,
                max_pages=job_max_pages,
                offset=(
                    _backfill_queue_orders_resume_offset(
                        schema=schema,
                        marketplace="wb",
                        source=source,
                        target_date=target_date,
                    )
                    if source == "orders"
                    else (
                        _backfill_queue_finance_resume_rrdid(
                            schema=schema,
                            marketplace="wb",
                            source=source,
                            target_date=target_date,
                        )
                        if source == "finance"
                        else offset
                    )
                ),
                page=page,
                pagination_mode=job_pagination_mode,
                advert_ids=list(advert_ids),
                snapshot_date=snapshot_date,
            )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=max(heartbeat_sec, 1))
            source_last_request_ts[source] = time.monotonic()
        processed += 1
        closed_day = result.actual_days >= max(result.expected_days, 1)
        if result.status != "ok" or not closed_day:
            err_type = result.error_type or "unknown"
            error_message = None
            if result.chunks:
                error_message = result.chunks[-1].error_message
            if result.status == "ok" and not closed_day:
                if not error_message:
                    error_message = (
                        f"target_empty: wb {source} day {target_date.isoformat()} not closed in target layer"
                    )
                err_type = _day_close_error_type(
                    schema=schema,
                    source=source,
                    chunk_result=(result.chunks[-1] if result.chunks else None),
                    target_date=target_date,
                )
            raw_rows_total = max(sum(chunk.raw_rows_count for chunk in result.chunks), 0)
            normalized_rows_total = max(sum(chunk.normalized_rows_count for chunk in result.chunks), 0)
            loaded_rows_total = max(sum(chunk.loaded_rows_count for chunk in result.chunks), 0)
            retry_after_sec: int | None = None
            state = "retry"
            if err_type == "rate_limit":
                retry_after_sec = _rate_limit_retry_after_seconds(
                    schema=schema,
                    marketplace="wb",
                    source=source,
                )
                if source == "finance":
                    retry_after_sec = max(retry_after_sec or 0, _finance_retry_delay_sec(attempt_no))
                if retry_after_sec is None:
                    retry_after_sec = _wb_backfill_retry_delay(max(attempt_no - 1, 0))
                state = "waiting_rate_limit"
            elif source == "finance" and _is_finance_provider_instability_error(err_type) and raw_rows_total == 0:
                retry_after_sec = _finance_provider_instability_retry_delay_sec(attempt_no)
                state = "provider_instability"
                next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=retry_after_sec)
                _finance_unstable_days_upsert(
                    schema=schema,
                    marketplace="wb",
                    source=source,
                    target_date=target_date,
                    attempts=attempt_no,
                    next_retry_at=next_retry_at,
                    error_type=err_type,
                    error_message=error_message,
                    raw_rows=raw_rows_total,
                    normalized_rows=normalized_rows_total,
                    loaded_rows=loaded_rows_total,
                    stg_rows=0,
                    run_id=result.run_id,
                )
            elif err_type == "page_cap_reached":
                page_count_total = sum(chunk.page_count for chunk in result.chunks)
                _, _, _, no_progress_passes = _backfill_queue_update_orders_progress(
                    schema=schema,
                    marketplace="wb",
                    source=source,
                    target_date=target_date,
                    offset_advance=raw_rows_total,
                    loaded_rows=loaded_rows_total,
                    page_count=max(page_count_total, 0),
                )
                if no_progress_passes >= _wb_orders_stalled_no_progress_passes():
                    state = "stalled_day"
                    err_type = "stalled_day"
                    error_message = (
                        f"stalled_day: wb orders day {target_date.isoformat()} "
                        f"has no progress for {no_progress_passes} passes"
                    )
                    retry_after_sec = _wb_orders_stalled_retry_delay_sec()
                else:
                    retry_after_sec = _wb_worker_requeue_delay_sec()
                    state = "partial_progress" if loaded_rows_total > 0 else "resume_required"
                    err_type = state
            elif err_type == "finance_incomplete_pagination":
                resume_rrdid = _extract_resume_rrdid_from_chunks(result.chunks)
                _backfill_queue_update_finance_progress(
                    schema=schema,
                    marketplace="wb",
                    source=source,
                    target_date=target_date,
                    resume_rrdid=resume_rrdid,
                    raw_rows=raw_rows_total,
                    page_count=sum(chunk.page_count for chunk in result.chunks),
                )
                retry_after_sec = _finance_retry_delay_sec(attempt_no)
                state = "finance_incomplete_pagination"
            elif err_type == "normalizer_error":
                retry_after_sec = (
                    _finance_retry_delay_sec(attempt_no)
                    if source == "finance"
                    else _wb_backfill_retry_delay(max(attempt_no - 1, 0))
                )
                state = "retry"
            elif err_type in {
                "parse_error",
                "unexpected_format",
                "normalized_zero_rows",
                "target_empty",
                "http_success_non_json",
                "http_success_truncated_body",
            }:
                retry_after_sec = (
                    _finance_retry_delay_sec(attempt_no)
                    if source == "finance"
                    else _wb_backfill_retry_delay(max(attempt_no - 1, 0))
                )
                state = "target_empty" if err_type == "target_empty" else "parse_error"
            elif err_type in {"truly_empty_day", "day_close_date_mismatch", "loaded_out_of_target_day", "parse_or_mapping_issue"}:
                retry_after_sec = (
                    _finance_retry_delay_sec(attempt_no)
                    if source == "finance"
                    else _wb_backfill_retry_delay(max(attempt_no - 1, 0))
                )
                state = "target_empty"
            else:
                retry_after_sec = (
                    _finance_retry_delay_sec(attempt_no)
                    if source == "finance"
                    else _wb_backfill_retry_delay(max(attempt_no - 1, 0))
                )
                state = "retry"
            if (
                attempt_no >= max_attempts
                and err_type not in {"page_cap_reached", "partial_progress", "resume_required"}
                and not (source == "finance" and (attempt_no < 6 or state == "provider_instability"))
            ):
                state = "manual_review"
                retry_after_sec = None
            _backfill_queue_set_failure(
                schema=schema,
                marketplace="wb",
                source=source,
                run_id=result.run_id,
                target_date=target_date,
                state=state,
                error_type=err_type,
                error_message=error_message,
                retry_after_sec=retry_after_sec,
                day_close_reason=(
                    err_type
                    if err_type in {
                        "truly_empty_day",
                        "day_close_date_mismatch",
                        "loaded_out_of_target_day",
                        "parse_or_mapping_issue",
                    }
                    else None
                ),
                worker_id=worker_id,
            )
            if not (source == "finance" and state == "provider_instability"):
                _finance_unstable_days_delete(
                    schema=schema,
                    marketplace="wb",
                    source=source,
                    target_date=target_date,
                )
            failed_count += 1
        else:
            _backfill_queue_set_success(
                schema=schema,
                marketplace="wb",
                source=source,
                run_id=result.run_id,
                target_date=target_date,
                worker_id=worker_id,
            )
            if source == "finance":
                _finance_unstable_days_delete(
                    schema=schema,
                    marketplace="wb",
                    source=source,
                    target_date=target_date,
                )
                _backfill_queue_reset_finance_progress(
                    schema=schema,
                    marketplace="wb",
                    source=source,
                    target_date=target_date,
                )
            success_count += 1
        _record_ingestion_performance_metrics(
            schema=schema,
            marketplace="wb",
            source=source,
        )
        if inter_job_sleep_sec > 0:
            time.sleep(inter_job_sleep_sec)

    return {
        "processed": processed,
        "success": success_count,
        "failed": failed_count,
    }


def _run_single_chunk(
    *,
    config: SourceConfig,
    schema: str,
    mode: str,
    dry_run: bool,
    run_id: str,
    chunk_id: str,
    date_from: date | None,
    date_to: date | None,
    chunk_from: date | None,
    chunk_to: date | None,
    sample_path: str | None,
    limit: int,
    max_pages: int,
    offset: int,
    page: int,
    pagination_mode: str | None,
    advert_ids: tuple[int, ...],
    snapshot_date: date | None,
    extra: Mapping[str, Any],
    seen_days: set[date],
    retry_attempt: int,
) -> IngestionChunkResult:
    status = "ok"
    error_message: str | None = None
    explicit_error_type: str | None = None
    pages: list[dict[str, Any]] = []
    raw_rows_count = 0
    raw_files_count = 0
    normalized_rows_count = 0
    loaded_rows_count = 0
    page_count = 0
    page_cap_reached = False
    finance_partial_fetch = False
    finance_resume_rrdid = 0
    finance_pages_done = 0
    finance_rows_done = 0
    finance_pagination_complete = False

    try:
        if config.marketplace == "wb":
            _ensure_backfill_queue_extended_columns(schema=schema)
        if config.marketplace == "wb" and config.source in WB_HEAVY_BACKFILL_SOURCES:
            _wait_for_persisted_rate_limit_cooldown(
                schema=schema,
                marketplace=config.marketplace,
                source=config.source,
            )
        ctx = SourceRunContext(
            marketplace=config.marketplace,
            source=config.source,
            schema=schema,
            date_from=date_from,
            date_to=date_to,
            chunk_from=chunk_from,
            chunk_to=chunk_to,
            mode=mode,
            dry_run=dry_run,
            limit=limit,
            max_pages=max_pages,
            offset=offset,
            page=page,
            pagination_mode=pagination_mode,
            advert_ids=advert_ids,
            snapshot_date=snapshot_date,
            extra=extra,
        )
        fetch_error: Exception | None = None
        try:
            pages = _load_pages(config=config, ctx=ctx, sample_path=sample_path)
        except Exception as exc:
            pages = _pages_from_exception(exc)
            fetch_error = exc
            if config.marketplace == "wb" and config.source == "finance":
                finance_resume_rrdid = max(int(getattr(exc, "resume_rrdid", 0) or 0), 0)
                finance_pages_done = max(int(getattr(exc, "pages_done", 0) or 0), 0)
                finance_rows_done = max(int(getattr(exc, "rows_done", 0) or 0), 0)
        page_count = len(pages)

        for page_idx, page in enumerate(pages, start=1):
            payload, request_params, http_status, page_meta = _split_page_payload(page)
            page_cap_reached = page_cap_reached or bool(page_meta.get("page_cap_reached", False))
            raw_rows_count += _estimate_raw_rows(payload)
            request_started_at = page_meta.get("request_started_at")
            request_finished_at = page_meta.get("request_finished_at")
            if not isinstance(request_started_at, str):
                request_started_at = datetime.now(timezone.utc).isoformat()
            if not isinstance(request_finished_at, str):
                request_finished_at = datetime.now(timezone.utc).isoformat()
            request_attempt_no = page_meta.get("request_attempt_no")
            if not isinstance(request_attempt_no, int) or request_attempt_no < 1:
                request_attempt_no = retry_attempt
            response_body_size = page_meta.get("response_body_size")
            if not isinstance(response_body_size, int):
                response_body_size = 0
            response_content_type = page_meta.get("response_content_type")
            if response_content_type is not None and not isinstance(response_content_type, str):
                response_content_type = str(response_content_type)
            raw_body = page_meta.get("raw_body")
            if raw_body is not None and not isinstance(raw_body, str):
                raw_body = str(raw_body)
            response_headers = page_meta.get("response_headers")
            if isinstance(response_headers, Mapping):
                response_headers = {str(k): str(v) for k, v in response_headers.items()}
            else:
                response_headers = {}
            meta_extra = {
                "run_id": run_id,
                "chunk_id": chunk_id,
                "attempt_no": request_attempt_no,
                "requested_date_from": chunk_from.isoformat() if chunk_from else None,
                "requested_date_to": chunk_to.isoformat() if chunk_to else None,
                "request_started_at": request_started_at,
                "request_finished_at": request_finished_at,
                "rows_in_payload": _estimate_raw_rows(payload),
                "response_body_size": response_body_size,
                "response_content_type": response_content_type,
                "empty_body": bool(page_meta.get("empty_body", False)),
                "parse_error": bool(page_meta.get("parse_error", False)),
                "unexpected_shape": bool(page_meta.get("unexpected_shape", False)),
                "request_success": bool(page_meta.get("request_success", True)),
                "request_error_type": page_meta.get("error_type"),
                "request_error_message": page_meta.get("error_message"),
                "rate_limit_retry": page_meta.get("rate_limit_retry"),
                "rate_limit_limit": page_meta.get("rate_limit_limit"),
                "rate_limit_reset": page_meta.get("rate_limit_reset"),
                "request_url": page_meta.get("request_url"),
                "request_endpoint": page_meta.get("request_endpoint"),
                "cooldown_wait_sec": page_meta.get("cooldown_wait_sec"),
                "page_cap_reached": bool(page_meta.get("page_cap_reached", False)),
                "finance_pass_no": page_meta.get("finance_pass_no"),
                "finance_pass_new_unique_rows": page_meta.get("finance_pass_new_unique_rows"),
                "finance_cumulative_unique_rows": page_meta.get("finance_cumulative_unique_rows"),
                "response_headers": response_headers,
                "raw_body": raw_body,
            }
            file_stem = _raw_file_stem(
                run_id=run_id,
                chunk_id=chunk_id,
                request_attempt_no=request_attempt_no,
                page_idx=page_idx,
            )
            raw_path = write_raw_json(
                schema=schema,
                source=config.raw_source,
                run_id=run_id,
                payload=payload,
                page=page_idx,
                request_params=request_params,
                http_status=http_status,
                meta_extra=meta_extra,
                file_stem=file_stem,
            )
            logger.info(
                "Saved raw page run_id=%s chunk_id=%s source=%s page=%s path=%s",
                run_id,
                chunk_id,
                config.raw_source,
                page_idx,
                raw_path,
            )
            _audit_request(
                schema=schema,
                run_id=run_id,
                chunk_id=chunk_id,
                marketplace=config.marketplace,
                source=config.source,
                endpoint=page_meta.get("request_endpoint"),
                request_url=page_meta.get("request_url"),
                target_date=(chunk_from or chunk_to),
                request_attempt_no=request_attempt_no,
                http_status=http_status,
                request_started_at=request_started_at,
                request_finished_at=request_finished_at,
                response_body_size=response_body_size,
                response_content_type=response_content_type,
                success=bool(page_meta.get("request_success", True)),
                error_type=page_meta.get("error_type"),
                error_message=page_meta.get("error_message"),
                rate_limit_retry=page_meta.get("rate_limit_retry"),
                rate_limit_limit=page_meta.get("rate_limit_limit"),
                rate_limit_reset=page_meta.get("rate_limit_reset"),
                cooldown_wait_sec=page_meta.get("cooldown_wait_sec"),
            )
            if config.marketplace == "wb" and config.source == "finance":
                _finance_page_audit_upsert(
                    schema=schema,
                    marketplace=config.marketplace,
                    source=config.source,
                    target_date=(chunk_from or chunk_to),
                    run_id=run_id,
                    chunk_id=chunk_id,
                    page_meta=page_meta,
                    http_status=http_status,
                )
                finance_resume_rrdid = max(
                    finance_resume_rrdid,
                    int(page_meta.get("finance_resume_rrdid") or 0),
                )
                finance_pages_done = max(
                    finance_pages_done,
                    int(page_meta.get("finance_pages_done") or 0),
                )
                finance_rows_done = max(
                    finance_rows_done,
                    int(page_meta.get("finance_rows_done") or 0),
                )
                if bool(page_meta.get("finance_is_terminal_page", False)):
                    finance_pagination_complete = True
            raw_files_count += 1

        if (
            fetch_error is not None
            and not (
                config.marketplace == "wb"
                and config.source == "finance"
                and raw_rows_count > 0
            )
        ):
            raise fetch_error
        finance_partial_fetch = (
            fetch_error is not None
            and config.marketplace == "wb"
            and config.source == "finance"
            and raw_rows_count > 0
        )

        finance_normalization_result: dict[str, Any] | None = None
        if config.marketplace == "wb" and config.source == "finance":
            finance_normalization_result = build_wb_finance_normalization_result(
                pages,
                target_day=(chunk_from if _is_wb_single_day_chunk(chunk_from, chunk_to, config.source) else None),
            )
            datasets = {"stg_wb_finance": finance_normalization_result["rows"]}
        else:
            datasets = config.normalizer(ctx, pages)
        datasets = _filter_datasets_by_period(
            datasets=datasets,
            period_from=date_from,
            period_to=date_to,
            period_filter_fields=config.period_filter_fields,
        )
        normalized_rows_count = sum(len(rows) for rows in datasets.values())
        if _should_fail_on_normalized_zero(config=config, raw_rows_count=raw_rows_count, normalized_rows_count=normalized_rows_count):
            raise RuntimeError("normalizer_error: WB finance raw rows present but normalizer returned zero rows")
        if not (config.marketplace == "wb" and _is_wb_single_day_chunk(chunk_from, chunk_to, config.source)):
            _collect_seen_days(
                seen_days=seen_days,
                datasets=datasets,
                period_filter_fields=config.period_filter_fields,
            )

        if dry_run:
            loaded_rows_count = normalized_rows_count
        else:
            for loader_spec in config.loaders:
                dataset_rows = datasets.get(loader_spec.dataset, [])
                loaded = loader_spec.loader(schema, dataset_rows)
                if isinstance(loaded, int):
                    loaded_rows_count += loaded
                else:
                    loaded_rows_count += len(dataset_rows)

        if (
            config.marketplace == "wb"
            and config.source == "finance"
            and chunk_from is not None
            and finance_normalization_result is not None
        ):
            _finance_raw_vs_normalized_audit_replace(
                schema=schema,
                target_date=chunk_from,
                run_id=run_id,
                raw_rows=max(int(finance_normalization_result["raw_rows_total"] or 0), 0),
                normalized_rows=max(int(finance_normalization_result["normalized_rows_total"] or 0), 0),
                drop_stats=list(finance_normalization_result["drop_stats"]),
            )
            stg_rows_total = _count_source_day_rows(
                schema=schema,
                source=config.source,
                target_day=chunk_from,
            )
            stg_amount_sum, stg_commission_sum, stg_logistics_sum, stg_services_sum = _finance_day_money_totals(
                schema=schema,
                target_date=chunk_from,
            )
            _finance_reconciliation_day_upsert(
                schema=schema,
                target_date=chunk_from,
                run_id=run_id,
                raw_payload_files=raw_files_count,
                raw_rows_total=max(int(finance_normalization_result["raw_rows_total"] or 0), 0),
                normalized_rows_total=max(int(finance_normalization_result["normalized_rows_total"] or 0), 0),
                stg_rows_total=max(int(stg_rows_total or 0), 0),
                raw_amount_sum=finance_normalization_result["raw_amount_sum"],
                normalized_amount_sum=finance_normalization_result["normalized_amount_sum"],
                stg_amount_sum=stg_amount_sum,
                raw_commission_sum=finance_normalization_result["raw_commission_sum"],
                stg_commission_sum=stg_commission_sum,
                raw_logistics_sum=finance_normalization_result["raw_logistics_sum"],
                stg_logistics_sum=stg_logistics_sum,
                raw_services_sum=finance_normalization_result["raw_services_sum"],
                stg_services_sum=stg_services_sum,
                pagination_complete=finance_pagination_complete and not finance_partial_fetch,
            )

        if page_cap_reached:
            raise RuntimeError(
                f"page_cap_reached: {config.marketplace} {config.source} chunk "
                f"{(chunk_from or chunk_to or date_from).isoformat() if (chunk_from or chunk_to or date_from) else 'unknown'} "
                f"loaded partially with max_pages={max_pages}; source coverage is incomplete"
            )

        if finance_partial_fetch:
            raise RuntimeError(
                f"finance_incomplete_pagination: wb finance day {(chunk_from or chunk_to or date_from).isoformat() if (chunk_from or chunk_to or date_from) else 'unknown'} "
                f"stopped before terminal page; resume_rrdid={finance_resume_rrdid}"
            )

        if config.marketplace == "wb" and _is_wb_single_day_chunk(chunk_from, chunk_to, config.source):
            target_day = chunk_from
            if target_day is not None:
                _ensure_wb_day_closed(
                    schema=schema,
                    source=config.source,
                    chunk_day=target_day,
                    dry_run=dry_run,
                    raw_rows_count=raw_rows_count,
                    normalized_rows_count=normalized_rows_count,
                )
                seen_days.add(target_day)
    except Exception as exc:
        status = "error"
        error_message = str(exc)
        explicit_error_type = getattr(exc, "error_type", None)
        logger.exception(
            "Chunk failed run_id=%s chunk_id=%s source=%s:%s",
            run_id,
            chunk_id,
            config.marketplace,
            config.source,
        )

    chunk_result = IngestionChunkResult(
        chunk_id=chunk_id,
        chunk_date_from=chunk_from,
        chunk_date_to=chunk_to,
        retry_attempts=max(retry_attempt, 1),
        page_count=page_count,
        raw_files_count=raw_files_count,
        raw_rows_count=raw_rows_count,
        normalized_rows_count=normalized_rows_count,
        loaded_rows_count=loaded_rows_count,
        status=status,
        error_message=error_message,
        error_type=explicit_error_type or _classify_error_type(error_message),
    )
    _audit_chunk(schema=schema, run_id=run_id, chunk_result=chunk_result)
    return chunk_result


def _load_pages(config: SourceConfig, ctx: SourceRunContext, sample_path: str | None) -> list[dict[str, Any]]:
    if sample_path:
        return _load_sample_pages(sample_path)
    return config.fetcher(ctx)


def _load_sample_pages(sample_path: str) -> list[dict[str, Any]]:
    path = Path(sample_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list) and all(isinstance(item, dict) for item in payload):
        return payload
    raise ValueError("Sample JSON must be object or list[object]")


def _split_page_payload(
    page: Mapping[str, Any],
) -> tuple[dict[str, Any], Mapping[str, Any] | None, int | None, dict[str, Any]]:
    response = page.get("response")
    request_params = page.get("request_params")
    http_status = page.get("http_status")

    payload = response if isinstance(response, dict) else dict(page)
    req = request_params if isinstance(request_params, Mapping) else None
    status = http_status if isinstance(http_status, int) else None
    page_meta = {
        "request_attempt_no": page.get("request_attempt_no"),
        "request_started_at": page.get("request_started_at"),
        "request_finished_at": page.get("request_finished_at"),
        "response_body_size": page.get("response_body_size"),
        "response_content_type": page.get("response_content_type"),
        "response_headers": page.get("response_headers"),
        "raw_body": page.get("raw_body"),
        "empty_body": page.get("empty_body"),
        "parse_error": page.get("parse_error"),
        "unexpected_shape": page.get("unexpected_shape"),
        "request_success": page.get("request_success"),
        "error_type": page.get("error_type"),
        "error_message": page.get("error_message"),
        "rate_limit_retry": page.get("rate_limit_retry"),
        "rate_limit_limit": page.get("rate_limit_limit"),
        "rate_limit_reset": page.get("rate_limit_reset"),
        "request_url": page.get("request_url"),
        "request_endpoint": page.get("request_endpoint"),
        "cooldown_wait_sec": page.get("cooldown_wait_sec"),
        "page_cap_reached": page.get("page_cap_reached"),
        "finance_request_no": page.get("finance_request_no"),
        "finance_pass_no": page.get("finance_pass_no"),
        "finance_rrdid_request": page.get("finance_rrdid_request"),
        "finance_rows_in_response": page.get("finance_rows_in_response"),
        "finance_max_rrdid_in_response": page.get("finance_max_rrdid_in_response"),
        "finance_is_terminal_page": page.get("finance_is_terminal_page"),
        "finance_terminal_reason": page.get("finance_terminal_reason"),
        "finance_resume_rrdid": page.get("finance_resume_rrdid"),
        "finance_pages_done": page.get("finance_pages_done"),
        "finance_rows_done": page.get("finance_rows_done"),
        "finance_pass_new_unique_rows": page.get("finance_pass_new_unique_rows"),
        "finance_cumulative_unique_rows": page.get("finance_cumulative_unique_rows"),
    }
    return payload, req, status, page_meta


def _estimate_raw_rows(payload: Mapping[str, Any]) -> int:
    return _estimate_rows_any(payload)


def _estimate_rows_any(value: Any) -> int:
    if isinstance(value, list):
        return len([item for item in value if isinstance(item, (dict, list))])
    if isinstance(value, Mapping):
        for key in (
            "items",
            "rows",
            "operations",
            "transactions",
            "postings",
            "orders",
            "sales",
            "finance",
            "ads",
            "stocks",
            "products",
            "cards",
            "statistics",
            "days",
            "apps",
            "nms",
            "nm",
        ):
            raw = value.get(key)
            if isinstance(raw, list):
                return len(raw)
        result = value.get("result")
        if isinstance(result, Mapping):
            nested = _estimate_rows_any(result)
            if nested > 0:
                return nested
    return 0


def _filter_datasets_by_period(
    *,
    datasets: dict[str, list[dict[str, Any]]],
    period_from: date | None,
    period_to: date | None,
    period_filter_fields: Mapping[str, str],
) -> dict[str, list[dict[str, Any]]]:
    if period_from is None and period_to is None:
        return datasets
    filtered: dict[str, list[dict[str, Any]]] = {}
    for dataset, rows in datasets.items():
        field = period_filter_fields.get(dataset)
        if not field:
            filtered[dataset] = rows
            continue
        kept: list[dict[str, Any]] = []
        for row in rows:
            row_date = _coerce_to_date(row.get(field))
            if row_date is None:
                kept.append(row)
                continue
            if period_from and row_date < period_from:
                continue
            if period_to and row_date > period_to:
                continue
            kept.append(row)
        filtered[dataset] = kept
    return filtered


def _collect_seen_days(
    *,
    seen_days: set[date],
    datasets: Mapping[str, list[dict[str, Any]]],
    period_filter_fields: Mapping[str, str],
) -> None:
    for dataset, field in period_filter_fields.items():
        rows = datasets.get(dataset, [])
        for row in rows:
            row_date = _coerce_to_date(row.get(field))
            if row_date:
                seen_days.add(row_date)


def _coerce_to_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if "T" in text:
            text = text.split("T", 1)[0]
        elif " " in text:
            # Supports payloads like "2026-03-11 12:34:56+03:00"
            text = text.split(" ", 1)[0]
        try:
            return date.fromisoformat(text)
        except ValueError:
            # Fallback for less strict datetime strings.
            dt_text = value.strip()
            if dt_text.endswith("Z"):
                dt_text = f"{dt_text[:-1]}+00:00"
            try:
                return datetime.fromisoformat(dt_text).date()
            except ValueError:
                return None
    return None


def _expected_days(config: SourceConfig, date_from: date | None, date_to: date | None) -> int:
    if config.expected_days_mode == "none":
        return 0
    if config.expected_days_mode == "snapshot":
        return 1
    if date_from is None or date_to is None:
        return 0
    return (date_to - date_from).days + 1


def _row_loss_ratio(normalized_rows: int, loaded_rows: int, dry_run: bool) -> float:
    if dry_run:
        return 0.0
    if normalized_rows <= 0:
        return 0.0
    loss = normalized_rows - loaded_rows
    if loss <= 0:
        return 0.0
    return float(loss) / float(normalized_rows)


def _coverage_ratio(*, expected_days: int, actual_days: int) -> float:
    if expected_days <= 0:
        return 1.0
    safe_actual = max(actual_days, 0)
    safe_expected = max(expected_days, 1)
    return float(safe_actual) / float(safe_expected)


def _classify_error_type(error_message: str | None) -> str | None:
    if not error_message:
        return None
    text = error_message.lower()
    if "provider_instability" in text or "finance_not_ready_yet" in text:
        return "provider_instability"
    if "http_no_content_after_429" in text:
        return "http_no_content_after_429"
    if "http_no_content" in text:
        return "http_no_content"
    if "http_success_empty_body_after_429" in text:
        return "http_success_empty_body_after_429"
    if "http_success_empty_body" in text:
        return "http_success_empty_body"
    if "http_success_truncated_body" in text:
        return "http_success_truncated_body"
    if "http_success_non_json" in text:
        return "http_success_non_json"
    if "network_before_body" in text:
        return "network_before_body"
    if "too many requests" in text or "http error 429" in text or " 429" in text or "rate-limited" in text:
        return "rate_limit"
    if "too long period" in text or "one month allowed" in text or ("period" in text and "validation" in text):
        return "period_limit"
    if "not a json" in text or "non-json" in text or "jsondecodeerror" in text:
        return "parse_error"
    if "unexpected format" in text or "unexpected_shape" in text or "unexpected shape" in text or "must be a json object or json list" in text:
        return "unexpected_format"
    if "empty response body" in text or "empty body" in text:
        return "empty_response"
    if "normalized_zero_rows" in text:
        return "normalized_zero_rows"
    if "normalizer_error" in text:
        return "normalizer_error"
    if "truly_empty_day" in text:
        return "truly_empty_day"
    if "day_close_date_mismatch" in text:
        return "day_close_date_mismatch"
    if "loaded_out_of_target_day" in text:
        return "loaded_out_of_target_day"
    if "parse_or_mapping_issue" in text:
        return "parse_or_mapping_issue"
    if "finance_incomplete_pagination" in text:
        return "finance_incomplete_pagination"
    if "page_cap_reached" in text:
        return "page_cap_reached"
    if "partial_progress" in text:
        return "partial_progress"
    if "resume_required" in text:
        return "resume_required"
    if "stalled_day" in text:
        return "stalled_day"
    if "target_empty" in text or "empty_after_parse" in text:
        return "target_empty"
    if "parse" in text:
        return "parse_error"
    if "401" in text or "403" in text or "auth" in text or "not authenticated" in text:
        return "auth_error"
    if "404" in text or "not found" in text or "endpoint" in text:
        return "endpoint_missing"
    if "network error" in text or "timed out" in text or "connection" in text or "urlopen error" in text:
        return "network_error"
    return "unknown"


def _is_usable_for_publish(
    *,
    source_criticality_value: str,
    run_status: str,
    completeness_status: str,
) -> bool:
    if source_criticality_value == "critical":
        return run_status == "ok" and completeness_status == "ok"
    return run_status != "error" and completeness_status in {"ok", "warning"}


def _build_partial_reason(
    *,
    run_status: str,
    expected_days: int,
    actual_days: int,
    error_type: str | None,
) -> str | None:
    def _incompleteness_stage(err: str | None) -> str | None:
        if not err:
            return None
        if err in {
            "rate_limit",
            "network_error",
            "period_limit",
            "auth_error",
            "endpoint_missing",
            "page_cap_reached",
            "partial_progress",
            "resume_required",
            "stalled_day",
        }:
            return "source_gap"
        if err in {
            "parse_error",
            "unexpected_format",
            "normalized_zero_rows",
            "empty_response",
            "target_empty",
            "normalizer_error",
            "day_close_date_mismatch",
            "loaded_out_of_target_day",
            "parse_or_mapping_issue",
        }:
            return "parsing_gap"
        return None

    if run_status == "error":
        return _incompleteness_stage(error_type) or error_type or "run_failed"
    if expected_days > 0 and actual_days < expected_days:
        return "source_gap"
    return None


def _completeness_status(
    *,
    dry_run: bool,
    run_status: str,
    expected_days: int,
    actual_days: int,
    coverage_ratio: float,
    row_loss_ratio: float,
) -> str:
    if dry_run:
        return "dry_run"
    if run_status == "error":
        return "error"
    if expected_days > 0:
        if coverage_ratio < 0.70:
            return "error"
        if coverage_ratio < 0.95:
            return "warning"
    if row_loss_ratio > 0.01:
        return "warning"
    return "ok"


def _build_chunks(
    *,
    date_from: date | None,
    date_to: date | None,
    chunk_days: int | None,
    chunk_by_calendar_month: bool,
    force_single_chunk: bool,
) -> list[tuple[date | None, date | None]]:
    if force_single_chunk:
        return [(date_from, date_to)]
    if date_from is None or date_to is None:
        return [(date_from, date_to)]
    if chunk_by_calendar_month:
        chunks: list[tuple[date, date]] = []
        current = date_from
        while current <= date_to:
            _, month_days = monthrange(current.year, current.month)
            chunk_end = min(date(current.year, current.month, month_days), date_to)
            chunks.append((current, chunk_end))
            current = chunk_end + timedelta(days=1)
        return [(frm, to) for frm, to in chunks]
    if chunk_days is None or chunk_days <= 0:
        return [(date_from, date_to)]
    chunks: list[tuple[date, date]] = []
    current = date_from
    while current <= date_to:
        chunk_end = min(current + timedelta(days=chunk_days - 1), date_to)
        chunks.append((current, chunk_end))
        current = chunk_end + timedelta(days=1)
    return [(frm, to) for frm, to in chunks]


def _chunk_retry_limit(config: SourceConfig) -> int:
    if config.marketplace == "wb" and config.source == "ads":
        return 0
    default = 4 if config.marketplace == "wb" else 1
    value = 0
    try:
        value = int(os.getenv("INGEST_CHUNK_MAX_RETRIES", str(default)).strip())
    except Exception:
        value = default
    return max(value, 0)


def _chunk_retry_delay(attempt_index: int) -> int:
    raw = (os.getenv("INGEST_CHUNK_RETRY_DELAYS_SEC", "5,10,20,40") or "").strip()
    values: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            val = int(part)
        except ValueError:
            continue
        if val >= 0:
            values.append(val)
    if not values:
        values = [5, 10, 20, 40]
    if attempt_index < len(values):
        return values[attempt_index]
    return values[-1]


def _pages_from_exception(exc: Exception) -> list[dict[str, Any]]:
    pages = getattr(exc, "diagnostic_pages", None)
    if not isinstance(pages, list):
        return []
    return [page for page in pages if isinstance(page, dict)]


def _raw_file_stem(*, run_id: str, chunk_id: str, request_attempt_no: int, page_idx: int) -> str:
    sanitized_chunk = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in chunk_id)
    return f"{run_id}__{sanitized_chunk}__req{request_attempt_no:02d}__p{page_idx:03d}"


def _should_fail_on_normalized_zero(*, config: SourceConfig, raw_rows_count: int, normalized_rows_count: int) -> bool:
    return (
        config.marketplace == "wb"
        and config.source == "finance"
        and raw_rows_count > 0
        and normalized_rows_count == 0
    )


def _should_use_wb_backfill_queue(
    *,
    config: SourceConfig,
    mode: str,
    dry_run: bool,
    date_from: date | None,
    date_to: date | None,
    sample_path: str | None,
) -> bool:
    enabled = (os.getenv("WB_BACKFILL_QUEUE_ENABLED") or "1").strip().lower() not in {"0", "false", "no", "off"}
    return (
        enabled
        and config.marketplace == "wb"
        and config.source in WB_HEAVY_BACKFILL_SOURCES
        and mode == "backfill"
        and not dry_run
        and sample_path is None
        and date_from is not None
        and date_to is not None
    )


def _wb_backfill_inline_max_cycles() -> int:
    raw = (os.getenv("WB_BACKFILL_INLINE_MAX_CYCLES") or "").strip()
    if not raw:
        return 0
    try:
        return max(int(raw), 0)
    except ValueError:
        return 0


def _wb_backfill_max_retries() -> int:
    raw = (os.getenv("WB_BACKFILL_MAX_RETRIES") or "").strip()
    if not raw:
        raw = (os.getenv("WB_FINANCE_BACKFILL_MAX_RETRIES") or "").strip()
    if not raw:
        return 30
    try:
        return max(int(raw), 1)
    except ValueError:
        return 30


def _wb_backfill_lease_sec() -> int:
    raw = (os.getenv("WB_BACKFILL_LEASE_SEC") or "").strip()
    if not raw:
        return 900
    try:
        return max(int(raw), 30)
    except ValueError:
        return 900


def _wb_backfill_heartbeat_sec() -> int:
    raw = (os.getenv("WB_BACKFILL_HEARTBEAT_SEC") or "").strip()
    if not raw:
        return 30
    try:
        return max(int(raw), 5)
    except ValueError:
        return 30


def _wb_backfill_stale_sec() -> int:
    raw = (os.getenv("WB_BACKFILL_STALE_SEC") or "").strip()
    if raw:
        try:
            return max(int(raw), 30)
        except ValueError:
            pass
    lease = _wb_backfill_lease_sec()
    heartbeat = _wb_backfill_heartbeat_sec()
    return max(lease, heartbeat * 3)


def _wb_backfill_retry_delay(attempt_index: int) -> int:
    raw = (os.getenv("WB_BACKFILL_RETRY_DELAYS_SEC") or "").strip()
    if not raw:
        raw = "10,30,60,120,300"
    values: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            parsed = int(part)
        except ValueError:
            continue
        if parsed >= 0:
            values.append(parsed)
    if not values:
        values = [10, 30, 60, 120, 300]
    if attempt_index < len(values):
        return values[attempt_index]
    return values[-1]


def _wb_rate_limit_buffer_sec() -> int:
    raw = (os.getenv("WB_RATE_LIMIT_BUFFER_SEC") or "").strip()
    if not raw:
        return 5
    try:
        return max(int(raw), 0)
    except ValueError:
        return 5


def _rate_limit_cooldown_seconds(retry_after_sec: int | None) -> int | None:
    if retry_after_sec is None or retry_after_sec <= 0:
        return retry_after_sec
    return retry_after_sec + _wb_rate_limit_buffer_sec()


def _wb_orders_pages_per_pass() -> int:
    raw = (os.getenv("WB_ORDERS_MAX_PAGES_PER_PASS") or "").strip()
    if not raw:
        return 10
    try:
        return max(int(raw), 1)
    except ValueError:
        return 10


def _wb_worker_inter_job_sleep_sec() -> float:
    raw = (os.getenv("WB_WORKER_INTER_JOB_SLEEP_SEC") or "").strip()
    if not raw:
        return 1.0
    try:
        parsed = float(raw)
    except ValueError:
        return 1.0
    return max(parsed, 0.0)


def _wb_worker_per_source_min_interval_sec() -> float:
    raw = (os.getenv("WB_WORKER_PER_SOURCE_MIN_INTERVAL_SEC") or "").strip()
    if not raw:
        return 1.0
    try:
        parsed = float(raw)
    except ValueError:
        return 1.0
    return max(parsed, 0.0)


def _wb_worker_requeue_delay_sec() -> int:
    raw = (os.getenv("WB_WORKER_REQUEUE_DELAY_SEC") or "").strip()
    if not raw:
        return 2
    try:
        return max(int(raw), 0)
    except ValueError:
        return 2


def _wb_orders_stalled_no_progress_passes() -> int:
    raw = (os.getenv("WB_ORDERS_STALLED_NO_PROGRESS_PASSES") or "").strip()
    if not raw:
        return 20
    try:
        return max(int(raw), 1)
    except ValueError:
        return 20


def _wb_orders_stalled_retry_delay_sec() -> int:
    raw = (os.getenv("WB_ORDERS_STALLED_RETRY_DELAY_SEC") or "").strip()
    if not raw:
        return 3600
    try:
        return max(int(raw), 60)
    except ValueError:
        return 3600


def _orders_background_mode_enabled() -> bool:
    raw = (os.getenv("WB_ORDERS_BACKGROUND_MODE") or "").strip().lower()
    if not raw:
        return True
    return raw not in {"0", "false", "no", "off"}


def _orders_required_for_analysis() -> bool:
    raw = (os.getenv("ORDERS_REQUIRED_FOR_ANALYSIS") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _finance_retry_delay_sec(attempt_no: int) -> int:
    schedule = [120, 300, 600, 1800, 3600]
    safe_attempt = max(int(attempt_no), 1)
    idx = min(safe_attempt - 1, len(schedule) - 1)
    return schedule[idx]


def _finance_provider_instability_retry_delay_sec(attempt_no: int) -> int:
    schedule = [21600, 86400, 172800, 259200, 259200]
    safe_attempt = max(int(attempt_no), 1)
    idx = min(safe_attempt - 1, len(schedule) - 1)
    return schedule[idx]


def _is_finance_provider_instability_error(err_type: str | None) -> bool:
    return err_type in {
        "empty_response",
        "http_no_content",
        "http_no_content_after_429",
        "http_success_empty_body",
        "http_success_empty_body_after_429",
    }


def _wb_worker_source_order(sources: tuple[str, ...]) -> tuple[str, ...]:
    preferred = ("sales", "finance", "ads", "orders")
    source_set = set(sources)
    ordered = [source for source in preferred if source in source_set]
    ordered.extend(source for source in sources if source not in ordered)
    return tuple(ordered)


def _ensure_backfill_queue_extended_columns(*, schema: str) -> None:
    statements = [
        sql.SQL(
            "ALTER TABLE {schema}.ingestion_backfill_queue "
            "ADD COLUMN IF NOT EXISTS order_resume_offset BIGINT NOT NULL DEFAULT 0"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "ALTER TABLE {schema}.ingestion_backfill_queue "
            "ADD COLUMN IF NOT EXISTS order_pages_done INTEGER NOT NULL DEFAULT 0"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "ALTER TABLE {schema}.ingestion_backfill_queue "
            "ADD COLUMN IF NOT EXISTS order_rows_done BIGINT NOT NULL DEFAULT 0"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "ALTER TABLE {schema}.ingestion_backfill_queue "
            "ADD COLUMN IF NOT EXISTS order_last_progress_at TIMESTAMPTZ NULL"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "ALTER TABLE {schema}.ingestion_backfill_queue "
            "ADD COLUMN IF NOT EXISTS order_no_progress_passes INTEGER NOT NULL DEFAULT 0"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "ALTER TABLE {schema}.ingestion_backfill_queue "
            "ADD COLUMN IF NOT EXISTS day_close_reason TEXT NULL"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "ALTER TABLE {schema}.ingestion_backfill_queue "
            "ADD COLUMN IF NOT EXISTS finance_resume_rrdid BIGINT NOT NULL DEFAULT 0"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "ALTER TABLE {schema}.ingestion_backfill_queue "
            "ADD COLUMN IF NOT EXISTS finance_pages_done INTEGER NOT NULL DEFAULT 0"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "ALTER TABLE {schema}.ingestion_backfill_queue "
            "ADD COLUMN IF NOT EXISTS finance_rows_done BIGINT NOT NULL DEFAULT 0"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "ALTER TABLE {schema}.ingestion_backfill_queue "
            "ADD COLUMN IF NOT EXISTS finance_last_progress_at TIMESTAMPTZ NULL"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "ALTER TABLE {schema}.ingestion_backfill_queue "
            "ADD COLUMN IF NOT EXISTS finance_no_progress_passes INTEGER NOT NULL DEFAULT 0"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "ALTER TABLE {schema}.stg_wb_finance "
            "ADD COLUMN IF NOT EXISTS operation_date_source TEXT NULL"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "ALTER TABLE {schema}.stg_wb_finance "
            "ADD COLUMN IF NOT EXISTS source_operation_date TIMESTAMPTZ NULL"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {schema}.finance_unstable_days (
              schema_name TEXT NOT NULL,
              marketplace TEXT NOT NULL,
              source TEXT NOT NULL,
              target_date DATE NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              next_retry_at TIMESTAMPTZ NULL,
              last_error_type TEXT NULL,
              last_error TEXT NULL,
              raw_rows BIGINT NOT NULL DEFAULT 0,
              normalized_rows BIGINT NOT NULL DEFAULT 0,
              loaded_rows BIGINT NOT NULL DEFAULT 0,
              stg_rows BIGINT NOT NULL DEFAULT 0,
              run_id TEXT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (schema_name, marketplace, source, target_date)
            )
            """
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "CREATE INDEX IF NOT EXISTS finance_unstable_days_retry_idx "
            "ON {schema}.finance_unstable_days (next_retry_at, updated_at DESC)"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {schema}.finance_page_audit (
              schema_name TEXT NOT NULL,
              marketplace TEXT NOT NULL,
              source TEXT NOT NULL,
              target_date DATE NOT NULL,
              run_id TEXT NOT NULL,
              chunk_id TEXT NOT NULL,
              request_no INTEGER NOT NULL,
              rrdid_request BIGINT NOT NULL DEFAULT 0,
              http_status INTEGER NULL,
              rows_in_response INTEGER NOT NULL DEFAULT 0,
              max_rrdid_in_response BIGINT NULL,
              response_body_size INTEGER NOT NULL DEFAULT 0,
              request_error_type TEXT NULL,
              is_terminal_page BOOLEAN NOT NULL DEFAULT FALSE,
              terminal_reason TEXT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (schema_name, marketplace, source, target_date, run_id, chunk_id, request_no)
            )
            """
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "CREATE INDEX IF NOT EXISTS finance_page_audit_day_idx "
            "ON {schema}.finance_page_audit (target_date, run_id, request_no)"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {schema}.finance_raw_vs_normalized_audit (
              schema_name TEXT NOT NULL,
              marketplace TEXT NOT NULL,
              target_date DATE NOT NULL,
              run_id TEXT NOT NULL,
              raw_rows INTEGER NOT NULL DEFAULT 0,
              normalized_rows INTEGER NOT NULL DEFAULT 0,
              dropped_rows INTEGER NOT NULL DEFAULT 0,
              drop_reason TEXT NOT NULL,
              operation_date_source TEXT NULL,
              rrd_id_min BIGINT NULL,
              rrd_id_max BIGINT NULL,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (schema_name, marketplace, target_date, run_id, drop_reason, operation_date_source)
            )
            """
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "CREATE INDEX IF NOT EXISTS finance_raw_vs_normalized_day_idx "
            "ON {schema}.finance_raw_vs_normalized_audit (target_date, run_id)"
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {schema}.finance_reconciliation_day (
              schema_name TEXT NOT NULL,
              marketplace TEXT NOT NULL,
              target_date DATE NOT NULL,
              run_id TEXT NOT NULL,
              raw_payload_files INTEGER NOT NULL DEFAULT 0,
              raw_rows_total INTEGER NOT NULL DEFAULT 0,
              normalized_rows_total INTEGER NOT NULL DEFAULT 0,
              stg_rows_total INTEGER NOT NULL DEFAULT 0,
              raw_amount_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
              normalized_amount_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
              stg_amount_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
              raw_commission_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
              stg_commission_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
              raw_logistics_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
              stg_logistics_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
              raw_services_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
              stg_services_sum NUMERIC(18,2) NOT NULL DEFAULT 0,
              pagination_complete BOOLEAN NOT NULL DEFAULT FALSE,
              created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
              PRIMARY KEY (schema_name, marketplace, target_date, run_id)
            )
            """
        ).format(schema=sql.Identifier(schema)),
        sql.SQL(
            "CREATE INDEX IF NOT EXISTS finance_reconciliation_day_idx "
            "ON {schema}.finance_reconciliation_day (target_date, run_id)"
        ).format(schema=sql.Identifier(schema)),
    ]
    for stmt in statements:
        _queue_exec(stmt, ())


def _backfill_queue_orders_resume_offset(
    *,
    schema: str,
    marketplace: str,
    source: str,
    target_date: date,
) -> int:
    if source != "orders":
        return 0
    query = sql.SQL(
        """
        SELECT COALESCE(order_resume_offset, 0)::bigint
        FROM {schema}.ingestion_backfill_queue
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date = %s
        LIMIT 1
        """
    ).format(schema=sql.Identifier(schema))
    rows = _queue_query(query, (schema, marketplace, source, target_date))
    if not rows or not rows[0]:
        return 0
    try:
        return max(int(rows[0][0] or 0), 0)
    except Exception:
        return 0


def _backfill_queue_finance_resume_rrdid(
    *,
    schema: str,
    marketplace: str,
    source: str,
    target_date: date,
) -> int:
    if source != "finance":
        return 0
    query = sql.SQL(
        """
        SELECT COALESCE(finance_resume_rrdid, 0)::bigint
        FROM {schema}.ingestion_backfill_queue
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date = %s
        LIMIT 1
        """
    ).format(schema=sql.Identifier(schema))
    rows = _queue_query(query, (schema, marketplace, source, target_date))
    if not rows or not rows[0]:
        return 0
    try:
        return max(int(rows[0][0] or 0), 0)
    except Exception:
        return 0


def _backfill_queue_update_orders_progress(
    *,
    schema: str,
    marketplace: str,
    source: str,
    target_date: date,
    offset_advance: int,
    loaded_rows: int,
    page_count: int,
) -> tuple[int, int, int, int]:
    if source != "orders":
        return (0, 0, 0, 0)
    safe_offset_advance = max(int(offset_advance), 0)
    safe_rows = max(int(loaded_rows), 0)
    safe_pages = max(int(page_count), 0)
    query = sql.SQL(
        """
        UPDATE {schema}.ingestion_backfill_queue
        SET
          order_resume_offset = GREATEST(COALESCE(order_resume_offset, 0) + %s, 0),
          order_pages_done = COALESCE(order_pages_done, 0) + %s,
          order_rows_done = COALESCE(order_rows_done, 0) + %s,
          order_last_progress_at = CASE
            WHEN %s > 0 THEN now()
            ELSE order_last_progress_at
          END,
          order_no_progress_passes = CASE
            WHEN %s > 0 THEN 0
            ELSE COALESCE(order_no_progress_passes, 0) + 1
          END,
          updated_at = now()
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date = %s
        RETURNING
          COALESCE(order_resume_offset, 0)::bigint,
          COALESCE(order_pages_done, 0)::int,
          COALESCE(order_rows_done, 0)::bigint,
          COALESCE(order_no_progress_passes, 0)::int
        """
    ).format(schema=sql.Identifier(schema))
    rows = _queue_query(
        query,
        (
            safe_offset_advance,
            safe_pages,
            safe_rows,
            safe_offset_advance,
            safe_offset_advance,
            schema,
            marketplace,
            source,
            target_date,
        ),
    )
    if not rows or not rows[0]:
        return (0, 0, 0, 0)
    row = rows[0]
    try:
        return (
            max(int(row[0] or 0), 0),
            max(int(row[1] or 0), 0),
            max(int(row[2] or 0), 0),
            max(int(row[3] or 0), 0),
        )
    except Exception:
        return (0, 0, 0, 0)


def _backfill_queue_update_finance_progress(
    *,
    schema: str,
    marketplace: str,
    source: str,
    target_date: date,
    resume_rrdid: int,
    raw_rows: int,
    page_count: int,
) -> tuple[int, int, int, int]:
    if source != "finance":
        return (0, 0, 0, 0)
    safe_rrdid = max(int(resume_rrdid or 0), 0)
    safe_rows = max(int(raw_rows or 0), 0)
    safe_pages = max(int(page_count or 0), 0)
    query = sql.SQL(
        """
        UPDATE {schema}.ingestion_backfill_queue
        SET
          finance_resume_rrdid = GREATEST(COALESCE(finance_resume_rrdid, 0), %s),
          finance_pages_done = COALESCE(finance_pages_done, 0) + %s,
          finance_rows_done = COALESCE(finance_rows_done, 0) + %s,
          finance_last_progress_at = CASE
            WHEN %s > 0 THEN now()
            ELSE finance_last_progress_at
          END,
          finance_no_progress_passes = CASE
            WHEN %s > 0 THEN 0
            ELSE COALESCE(finance_no_progress_passes, 0) + 1
          END,
          updated_at = now()
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date = %s
        RETURNING
          COALESCE(finance_resume_rrdid, 0)::bigint,
          COALESCE(finance_pages_done, 0)::int,
          COALESCE(finance_rows_done, 0)::bigint,
          COALESCE(finance_no_progress_passes, 0)::int
        """
    ).format(schema=sql.Identifier(schema))
    rows = _queue_query(
        query,
        (
            safe_rrdid,
            safe_pages,
            safe_rows,
            safe_rrdid,
            safe_rrdid,
            schema,
            marketplace,
            source,
            target_date,
        ),
    )
    if not rows or not rows[0]:
        return (0, 0, 0, 0)
    row = rows[0]
    try:
        return (
            max(int(row[0] or 0), 0),
            max(int(row[1] or 0), 0),
            max(int(row[2] or 0), 0),
            max(int(row[3] or 0), 0),
        )
    except Exception:
        return (0, 0, 0, 0)


def _backfill_queue_reset_finance_progress(
    *,
    schema: str,
    marketplace: str,
    source: str,
    target_date: date,
) -> None:
    if source != "finance":
        return
    query = sql.SQL(
        """
        UPDATE {schema}.ingestion_backfill_queue
        SET
          finance_resume_rrdid = 0,
          finance_pages_done = 0,
          finance_rows_done = 0,
          finance_last_progress_at = NULL,
          finance_no_progress_passes = 0,
          updated_at = now()
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date = %s
        """
    ).format(schema=sql.Identifier(schema))
    _queue_exec(query, (schema, marketplace, source, target_date))


def _finance_unstable_days_upsert(
    *,
    schema: str,
    marketplace: str,
    source: str,
    target_date: date,
    attempts: int,
    next_retry_at: datetime | None,
    error_type: str | None,
    error_message: str | None,
    raw_rows: int,
    normalized_rows: int,
    loaded_rows: int,
    stg_rows: int,
    run_id: str,
) -> None:
    query = sql.SQL(
        """
        INSERT INTO {schema}.finance_unstable_days (
          schema_name,
          marketplace,
          source,
          target_date,
          attempts,
          next_retry_at,
          last_error_type,
          last_error,
          raw_rows,
          normalized_rows,
          loaded_rows,
          stg_rows,
          run_id,
          created_at,
          updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
        ON CONFLICT (schema_name, marketplace, source, target_date)
        DO UPDATE SET
          attempts = EXCLUDED.attempts,
          next_retry_at = EXCLUDED.next_retry_at,
          last_error_type = EXCLUDED.last_error_type,
          last_error = EXCLUDED.last_error,
          raw_rows = EXCLUDED.raw_rows,
          normalized_rows = EXCLUDED.normalized_rows,
          loaded_rows = EXCLUDED.loaded_rows,
          stg_rows = EXCLUDED.stg_rows,
          run_id = EXCLUDED.run_id,
          updated_at = now()
        """
    ).format(schema=sql.Identifier(schema))
    _queue_exec(
        query,
        (
            schema,
            marketplace,
            source,
            target_date,
            max(int(attempts), 0),
            next_retry_at,
            error_type,
            error_message,
            max(int(raw_rows), 0),
            max(int(normalized_rows), 0),
            max(int(loaded_rows), 0),
            max(int(stg_rows), 0),
            run_id,
        ),
    )


def _finance_unstable_days_delete(
    *,
    schema: str,
    marketplace: str,
    source: str,
    target_date: date,
) -> None:
    query = sql.SQL(
        """
        DELETE FROM {schema}.finance_unstable_days
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date = %s
        """
    ).format(schema=sql.Identifier(schema))
    _queue_exec(query, (schema, marketplace, source, target_date))


def _finance_page_audit_upsert(
    *,
    schema: str,
    marketplace: str,
    source: str,
    target_date: date | None,
    run_id: str,
    chunk_id: str,
    page_meta: Mapping[str, Any],
    http_status: int | None,
) -> None:
    if source != "finance" or target_date is None:
        return
    request_no = page_meta.get("finance_request_no")
    if not isinstance(request_no, int) or request_no < 1:
        return
    query = sql.SQL(
        """
        INSERT INTO {schema}.finance_page_audit (
          schema_name,
          marketplace,
          source,
          target_date,
          run_id,
          chunk_id,
          request_no,
          rrdid_request,
          http_status,
          rows_in_response,
          max_rrdid_in_response,
          response_body_size,
          request_error_type,
          is_terminal_page,
          terminal_reason,
          created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        ON CONFLICT (schema_name, marketplace, source, target_date, run_id, chunk_id, request_no)
        DO UPDATE SET
          rrdid_request = EXCLUDED.rrdid_request,
          http_status = EXCLUDED.http_status,
          rows_in_response = EXCLUDED.rows_in_response,
          max_rrdid_in_response = EXCLUDED.max_rrdid_in_response,
          response_body_size = EXCLUDED.response_body_size,
          request_error_type = EXCLUDED.request_error_type,
          is_terminal_page = EXCLUDED.is_terminal_page,
          terminal_reason = EXCLUDED.terminal_reason
        """
    ).format(schema=sql.Identifier(schema))
    _queue_exec(
        query,
        (
            schema,
            marketplace,
            source,
            target_date,
            run_id,
            chunk_id,
            request_no,
            max(int(page_meta.get("finance_rrdid_request") or 0), 0),
            http_status,
            max(int(page_meta.get("finance_rows_in_response") or 0), 0),
            page_meta.get("finance_max_rrdid_in_response"),
            max(int(page_meta.get("response_body_size") or 0), 0),
            page_meta.get("error_type"),
            bool(page_meta.get("finance_is_terminal_page", False)),
            page_meta.get("finance_terminal_reason"),
        ),
    )


def _finance_raw_vs_normalized_audit_replace(
    *,
    schema: str,
    target_date: date,
    run_id: str,
    raw_rows: int,
    normalized_rows: int,
    drop_stats: list[dict[str, Any]],
) -> None:
    delete_query = sql.SQL(
        """
        DELETE FROM {schema}.finance_raw_vs_normalized_audit
        WHERE schema_name = %s
          AND marketplace = 'wb'
          AND target_date = %s
          AND run_id = %s
        """
    ).format(schema=sql.Identifier(schema))
    _queue_exec(delete_query, (schema, target_date, run_id))
    insert_query = sql.SQL(
        """
        INSERT INTO {schema}.finance_raw_vs_normalized_audit (
          schema_name,
          marketplace,
          target_date,
          run_id,
          raw_rows,
          normalized_rows,
          dropped_rows,
          drop_reason,
          operation_date_source,
          rrd_id_min,
          rrd_id_max,
          created_at
        )
        VALUES (%s, 'wb', %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        """
    ).format(schema=sql.Identifier(schema))
    for row in drop_stats:
        _queue_exec(
            insert_query,
            (
                schema,
                target_date,
                run_id,
                max(int(raw_rows or 0), 0),
                max(int(normalized_rows or 0), 0),
                max(int(row.get("dropped_rows") or 0), 0),
                row.get("drop_reason") or "unknown_drop_reason",
                row.get("operation_date_source"),
                row.get("rrd_id_min"),
                row.get("rrd_id_max"),
            ),
        )


def _finance_reconciliation_day_upsert(
    *,
    schema: str,
    target_date: date,
    run_id: str,
    raw_payload_files: int,
    raw_rows_total: int,
    normalized_rows_total: int,
    stg_rows_total: int,
    raw_amount_sum: Any,
    normalized_amount_sum: Any,
    stg_amount_sum: Any,
    raw_commission_sum: Any,
    stg_commission_sum: Any,
    raw_logistics_sum: Any,
    stg_logistics_sum: Any,
    raw_services_sum: Any,
    stg_services_sum: Any,
    pagination_complete: bool,
) -> None:
    query = sql.SQL(
        """
        INSERT INTO {schema}.finance_reconciliation_day (
          schema_name,
          marketplace,
          target_date,
          run_id,
          raw_payload_files,
          raw_rows_total,
          normalized_rows_total,
          stg_rows_total,
          raw_amount_sum,
          normalized_amount_sum,
          stg_amount_sum,
          raw_commission_sum,
          stg_commission_sum,
          raw_logistics_sum,
          stg_logistics_sum,
          raw_services_sum,
          stg_services_sum,
          pagination_complete,
          created_at,
          updated_at
        )
        VALUES (%s, 'wb', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now())
        ON CONFLICT (schema_name, marketplace, target_date, run_id)
        DO UPDATE SET
          raw_payload_files = EXCLUDED.raw_payload_files,
          raw_rows_total = EXCLUDED.raw_rows_total,
          normalized_rows_total = EXCLUDED.normalized_rows_total,
          stg_rows_total = EXCLUDED.stg_rows_total,
          raw_amount_sum = EXCLUDED.raw_amount_sum,
          normalized_amount_sum = EXCLUDED.normalized_amount_sum,
          stg_amount_sum = EXCLUDED.stg_amount_sum,
          raw_commission_sum = EXCLUDED.raw_commission_sum,
          stg_commission_sum = EXCLUDED.stg_commission_sum,
          raw_logistics_sum = EXCLUDED.raw_logistics_sum,
          stg_logistics_sum = EXCLUDED.stg_logistics_sum,
          raw_services_sum = EXCLUDED.raw_services_sum,
          stg_services_sum = EXCLUDED.stg_services_sum,
          pagination_complete = EXCLUDED.pagination_complete,
          updated_at = now()
        """
    ).format(schema=sql.Identifier(schema))
    _queue_exec(
        query,
        (
            schema,
            target_date,
            run_id,
            max(int(raw_payload_files or 0), 0),
            max(int(raw_rows_total or 0), 0),
            max(int(normalized_rows_total or 0), 0),
            max(int(stg_rows_total or 0), 0),
            raw_amount_sum,
            normalized_amount_sum,
            stg_amount_sum,
            raw_commission_sum,
            stg_commission_sum,
            raw_logistics_sum,
            stg_logistics_sum,
            raw_services_sum,
            stg_services_sum,
            bool(pagination_complete),
        ),
    )


def _finance_day_money_totals(*, schema: str, target_date: date) -> tuple[Any, Any, Any, Any]:
    query = sql.SQL(
        """
        SELECT
          COALESCE(SUM(COALESCE(amount, 0)), 0)::numeric(18,2),
          COALESCE(SUM(COALESCE(commission_amount, 0)), 0)::numeric(18,2),
          COALESCE(SUM(COALESCE(logistics_amount, 0)), 0)::numeric(18,2),
          COALESCE(SUM(COALESCE(service_amount, 0)), 0)::numeric(18,2)
        FROM {schema}.stg_wb_finance
        WHERE (operation_date AT TIME ZONE 'UTC')::date = %s
        """
    ).format(schema=sql.Identifier(schema))
    rows = _queue_query(query, (target_date,))
    if not rows or not rows[0]:
        return (0, 0, 0, 0)
    row = rows[0]
    return (row[0] or 0, row[1] or 0, row[2] or 0, row[3] or 0)


def _extract_resume_rrdid_from_chunks(chunks: tuple[IngestionChunkResult, ...] | list[IngestionChunkResult]) -> int:
    max_rrdid = 0
    for chunk in chunks:
        text = str(chunk.error_message or "")
        marker = "resume_rrdid="
        if marker not in text:
            continue
        suffix = text.split(marker, 1)[1].strip()
        token = ""
        for ch in suffix:
            if ch.isdigit():
                token += ch
            else:
                break
        if not token:
            continue
        try:
            max_rrdid = max(max_rrdid, int(token))
        except ValueError:
            continue
    return max_rrdid


def _core_sources_pending_for_period(
    *,
    schema: str,
    marketplace: str,
    date_from: date,
    date_to: date,
) -> bool:
    query = sql.SQL(
        """
        WITH q AS (
          SELECT source, state, COUNT(*)::int AS cnt
          FROM {schema}.ingestion_backfill_queue
          WHERE schema_name = %s
            AND marketplace = %s
            AND source = ANY(%s)
            AND target_date BETWEEN %s AND %s
          GROUP BY source, state
        ),
        sales_ads_block AS (
          SELECT EXISTS (
            SELECT 1
            FROM q
            WHERE source = 'sales'
              AND state IN ('pending', 'running', 'waiting_rate_limit', 'retry', 'parse_error', 'target_empty')
          ) AS blocked
        ),
        finance_totals AS (
          SELECT
            COALESCE(SUM(cnt), 0)::int AS total_days,
            COALESCE(SUM(cnt) FILTER (WHERE source='finance' AND state='success'), 0)::int AS success_days,
            COALESCE(SUM(cnt) FILTER (
              WHERE source='finance'
                AND state IN ('pending', 'running', 'waiting_rate_limit', 'retry', 'parse_error', 'target_empty')
            ), 0)::int AS blocking_days
          FROM q
          WHERE source='finance'
        )
        SELECT
          sales_ads_block.blocked
          OR finance_totals.blocking_days > 0
          OR (
            finance_totals.total_days > 0
            AND finance_totals.success_days < CEIL(finance_totals.total_days * 0.8)
          )
        FROM sales_ads_block, finance_totals
        """
    ).format(schema=sql.Identifier(schema))
    rows = _queue_query(
        query,
        (
            schema,
            marketplace,
            list(WB_CORE_ECONOMY_SOURCES),
            date_from,
            date_to,
        ),
    )
    return bool(rows and rows[0] and rows[0][0])


def _sleep_before_source_request(
    *,
    source: str,
    source_last_request_ts: dict[str, float],
    min_interval_sec: float,
) -> None:
    if min_interval_sec <= 0:
        return
    last_ts = source_last_request_ts.get(source)
    if last_ts is None:
        return
    now_ts = time.monotonic()
    elapsed = now_ts - last_ts
    if elapsed >= min_interval_sec:
        return
    time.sleep(min_interval_sec - elapsed)


def _queue_lane_and_priority(target_date: date, today: date | None = None) -> tuple[str, int]:
    now_day = today or datetime.now(timezone.utc).date()
    if target_date >= now_day - timedelta(days=1):
        return "freshness", 10
    if target_date >= now_day - timedelta(days=7):
        return "backfill_recent", 50
    return "backfill", 100


def _parse_datetime_utc(text: str | None) -> datetime | None:
    if not text:
        return None
    raw = text.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _header_retry_after_seconds(
    *,
    retry_raw: str | None,
    reset_raw: str | None,
    request_finished_at: str | None,
) -> int | None:
    if retry_raw:
        try:
            parsed = int(float(retry_raw))
            if parsed >= 0:
                return parsed
        except ValueError:
            pass
    if reset_raw:
        try:
            reset_epoch = int(float(reset_raw))
            now_epoch = int(time.time())
            delta = max(reset_epoch - now_epoch, 0)
            return delta
        except ValueError:
            pass
    finished_dt = _parse_datetime_utc(request_finished_at)
    if finished_dt is not None:
        now_dt = datetime.now(timezone.utc)
        delta_sec = int((finished_dt - now_dt).total_seconds())
        if delta_sec > 0:
            return delta_sec
    return None


def _persist_rate_limit_state(
    *,
    schema: str,
    marketplace: str,
    source: str,
    endpoint: str,
    http_status: int | None,
    request_finished_at: str,
    rate_limit_retry: str | None,
    rate_limit_limit: str | None,
    rate_limit_reset: str | None,
) -> None:
    retry_after_sec = _header_retry_after_seconds(
        retry_raw=rate_limit_retry,
        reset_raw=rate_limit_reset,
        request_finished_at=request_finished_at,
    )
    retry_after_sec = _rate_limit_cooldown_seconds(retry_after_sec)
    if http_status == 429 and (retry_after_sec is None or retry_after_sec <= 0):
        retry_after_sec = _wb_backfill_retry_delay(0)
    finished_at = _parse_datetime_utc(request_finished_at) or datetime.now(timezone.utc)
    cooldown_until = finished_at
    if retry_after_sec and retry_after_sec > 0:
        cooldown_until = finished_at + timedelta(seconds=retry_after_sec)
    query = sql.SQL(
        """
        INSERT INTO {schema}.ingestion_rate_limit_state (
            schema_name,
            marketplace,
            source,
            endpoint,
            cooldown_until,
            last_http_status,
            x_ratelimit_retry,
            x_ratelimit_limit,
            x_ratelimit_reset,
            observed_at,
            created_at,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now(), now())
        ON CONFLICT (schema_name, marketplace, source, endpoint)
        DO UPDATE SET
            cooldown_until = GREATEST(
                COALESCE({schema}.ingestion_rate_limit_state.cooldown_until, to_timestamp(0)),
                COALESCE(EXCLUDED.cooldown_until, to_timestamp(0))
            ),
            last_http_status = EXCLUDED.last_http_status,
            x_ratelimit_retry = EXCLUDED.x_ratelimit_retry,
            x_ratelimit_limit = EXCLUDED.x_ratelimit_limit,
            x_ratelimit_reset = EXCLUDED.x_ratelimit_reset,
            observed_at = now(),
            updated_at = now()
        """
    ).format(schema=sql.Identifier(schema))
    _audit_exec(
        query,
        (
            schema,
            marketplace,
            source,
            endpoint,
            cooldown_until if (http_status == 429 or retry_after_sec is not None) else None,
            http_status,
            rate_limit_retry,
            rate_limit_limit,
            rate_limit_reset,
        ),
    )


def _wait_for_persisted_rate_limit_cooldown(*, schema: str, marketplace: str, source: str) -> float:
    query = sql.SQL(
        """
        SELECT EXTRACT(EPOCH FROM (MAX(cooldown_until) - now()))
        FROM {schema}.ingestion_rate_limit_state
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND cooldown_until IS NOT NULL
          AND cooldown_until > now()
        """
    ).format(schema=sql.Identifier(schema))
    rows = _queue_query(query, (schema, marketplace, source))
    if not rows or not rows[0]:
        return 0.0
    value = rows[0][0]
    try:
        wait_sec = float(value or 0.0)
    except Exception:
        return 0.0
    if wait_sec <= 0:
        return 0.0
    logger.info(
        "Wait persisted cooldown schema=%s marketplace=%s source=%s sleep=%.3fs",
        schema,
        marketplace,
        source,
        wait_sec,
    )
    time.sleep(wait_sec)
    return wait_sec


def _legacy_finance_queue_mirror_enabled() -> bool:
    return (os.getenv("WB_FINANCE_QUEUE_MIRROR_ENABLED") or "1").strip().lower() not in {"0", "false", "no", "off"}


def _is_wb_single_day_chunk(chunk_from: date | None, chunk_to: date | None, source: str) -> bool:
    return source in WB_HEAVY_BACKFILL_SOURCES and chunk_from is not None and chunk_to is not None and chunk_from == chunk_to


def _target_table_and_day_field(source: str) -> tuple[str, str, bool] | None:
    mapping: dict[str, tuple[str, str, bool]] = {
        "orders": ("stg_wb_order", "order_date", False),
        "sales": ("stg_wb_sale", "sale_date", False),
        "finance": ("stg_wb_finance", "operation_date", False),
        "ads": ("stg_wb_ads", "date", True),
    }
    return mapping.get(source)


def _count_source_day_rows(*, schema: str, source: str, target_day: date) -> int:
    target = _target_table_and_day_field(source)
    if target is None:
        return 0
    table_name, day_field, day_is_date = target
    if day_is_date:
        query = sql.SQL(
            """
            SELECT COUNT(*)::int
            FROM {schema}.{table}
            WHERE {day_field} = %s
            """
        ).format(
            schema=sql.Identifier(schema),
            table=sql.Identifier(table_name),
            day_field=sql.Identifier(day_field),
        )
    else:
        query = sql.SQL(
            """
            SELECT COUNT(*)::int
            FROM {schema}.{table}
            WHERE ({day_field} AT TIME ZONE 'UTC')::date = %s
            """
        ).format(
            schema=sql.Identifier(schema),
            table=sql.Identifier(table_name),
            day_field=sql.Identifier(day_field),
        )
    rows = _queue_query(query, (target_day,))
    if not rows:
        return 0
    raw = rows[0][0] if rows[0] else 0
    try:
        return int(raw or 0)
    except Exception:
        return 0


def _has_wb_core_business_activity(*, schema: str, target_day: date) -> bool:
    return any(
        _count_source_day_rows(schema=schema, source=source, target_day=target_day) > 0
        for source in ("sales", "ads")
    )


def _ensure_wb_day_closed(
    *,
    schema: str,
    source: str,
    chunk_day: date,
    dry_run: bool,
    raw_rows_count: int,
    normalized_rows_count: int,
) -> int:
    if dry_run:
        if source == "sales" and raw_rows_count == 0:
            return 0
        if normalized_rows_count <= 0:
            if source == "finance" and raw_rows_count > 0:
                raise RuntimeError(
                    f"parse_or_mapping_issue: wb finance day {chunk_day.isoformat()} has raw rows but zero normalized rows"
                )
            raise RuntimeError(f"target_empty: wb {source} day {chunk_day.isoformat()} has zero normalized rows")
        return normalized_rows_count
    target_rows = _count_source_day_rows(
        schema=schema,
        source=source,
        target_day=chunk_day,
    )
    if source == "sales" and raw_rows_count == 0 and target_rows == 0:
        return 0
    if source == "finance" and raw_rows_count <= 0:
        if _has_wb_core_business_activity(schema=schema, target_day=chunk_day):
            raise RuntimeError(
                f"provider_instability: wb finance day {chunk_day.isoformat()} "
                "returned zero raw rows on an active sales/ads day"
            )
        raise RuntimeError(f"truly_empty_day: wb finance day {chunk_day.isoformat()} has zero raw rows")
    if source == "finance" and normalized_rows_count <= 0:
        raise RuntimeError(
            f"parse_or_mapping_issue: wb finance day {chunk_day.isoformat()} raw rows present but normalized rows are zero"
        )
    if target_rows <= 0:
        if source == "finance":
            raise RuntimeError(
                f"loaded_out_of_target_day: wb finance day {chunk_day.isoformat()} normalized rows loaded but target day has zero rows"
            )
        if source == "sales" and raw_rows_count == 0:
            logger.warning(
                "WB sales day %s returned zero rows; treating as target_empty warning",
                chunk_day.isoformat(),
            )
            return 0
        raise RuntimeError(f"target_empty: wb {source} day {chunk_day.isoformat()} has no rows in target table")
    return target_rows


def _day_close_error_type(
    *,
    schema: str,
    source: str,
    chunk_result: IngestionChunkResult | None,
    target_date: date,
) -> str:
    if source != "finance":
        return "target_empty"
    if chunk_result is None:
        return "target_empty"
    raw_rows = max(int(chunk_result.raw_rows_count), 0)
    normalized_rows = max(int(chunk_result.normalized_rows_count), 0)
    loaded_rows = max(int(chunk_result.loaded_rows_count), 0)
    if raw_rows == 0 and normalized_rows == 0 and loaded_rows == 0:
        if _has_wb_core_business_activity(schema=schema, target_day=target_date):
            return "provider_instability"
        return "truly_empty_day"
    if raw_rows > 0 and normalized_rows == 0:
        return "parse_or_mapping_issue"
    if normalized_rows > 0:
        day_rows = _count_source_day_rows(
            schema=schema,
            source=source,
            target_day=target_date,
        )
        if day_rows <= 0:
            return "loaded_out_of_target_day"
        return "day_close_date_mismatch"
    return "target_empty"


def _should_auto_backfill_wb_finance(
    *,
    config: SourceConfig,
    mode: str,
    dry_run: bool,
    date_from: date | None,
    date_to: date | None,
    sample_path: str | None,
) -> bool:
    enabled = (os.getenv("WB_FINANCE_AUTO_BACKFILL") or "1").strip().lower() not in {"0", "false", "no", "off"}
    return (
        enabled
        and config.marketplace == "wb"
        and config.source == "finance"
        and mode == "backfill"
        and not dry_run
        and sample_path is None
        and date_from is not None
        and date_to is not None
    )


def _missing_dates_in_period(
    *,
    date_from: date | None,
    date_to: date | None,
    seen_days: set[date],
) -> list[date]:
    if date_from is None or date_to is None:
        return []
    missing: list[date] = []
    current = date_from
    while current <= date_to:
        if current not in seen_days:
            missing.append(current)
        current += timedelta(days=1)
    return missing


def _process_wb_backfill_queue(
    *,
    config: SourceConfig,
    schema: str,
    mode: str,
    dry_run: bool,
    run_id: str,
    date_from: date | None,
    date_to: date | None,
    sample_path: str | None,
    limit: int,
    max_pages: int,
    offset: int,
    page: int,
    pagination_mode: str | None,
    advert_ids: tuple[int, ...],
    snapshot_date: date | None,
    extra: Mapping[str, Any],
    seen_days: set[date],
    max_cycles: int,
) -> tuple[list[IngestionChunkResult], bool]:
    if date_from is None or date_to is None or max_cycles <= 0:
        return [], False

    queue_results: list[IngestionChunkResult] = []
    retry_exhausted = False
    max_retries_default = _wb_backfill_max_retries()

    for cycle in range(1, max_cycles + 1):
        _backfill_queue_mark_seen_days_success(
            schema=schema,
            marketplace=config.marketplace,
            source=config.source,
            run_id=run_id,
            days=seen_days,
        )
        missing_dates = _missing_dates_in_period(
            date_from=date_from,
            date_to=date_to,
            seen_days=seen_days,
        )
        if missing_dates:
            _backfill_queue_upsert_dates(
                schema=schema,
                marketplace=config.marketplace,
                source=config.source,
                run_id=run_id,
                dates=missing_dates,
            )

        candidates = _backfill_queue_fetch_dates(
            schema=schema,
            marketplace=config.marketplace,
            source=config.source,
            date_from=date_from,
            date_to=date_to,
        )
        if not candidates:
            break

        logger.info(
            "WB backfill queue cycle=%s/%s run_id=%s source=%s candidates=%s missing_days=%s",
            cycle,
            max_cycles,
            run_id,
            config.source,
            len(candidates),
            len(missing_dates),
        )

        for target_date, attempts, max_attempts in candidates:
            if target_date in seen_days:
                _backfill_queue_set_success(
                    schema=schema,
                    marketplace=config.marketplace,
                    source=config.source,
                    run_id=run_id,
                    target_date=target_date,
                )
                continue

            attempt_no = attempts + 1
            _backfill_queue_mark_running(
                schema=schema,
                marketplace=config.marketplace,
                source=config.source,
                run_id=run_id,
                target_date=target_date,
                attempts=attempt_no,
            )
            queue_chunk_id = f"{run_id}_q{cycle:02d}_{target_date.strftime('%Y%m%d')}"
            chunk_result = _run_single_chunk(
                config=config,
                schema=schema,
                mode=mode,
                dry_run=dry_run,
                run_id=run_id,
                chunk_id=queue_chunk_id,
                date_from=date_from,
                date_to=date_to,
                chunk_from=target_date,
                chunk_to=target_date,
                sample_path=sample_path,
                limit=limit,
                max_pages=max_pages,
                offset=offset,
                page=page,
                pagination_mode=pagination_mode,
                advert_ids=advert_ids,
                snapshot_date=snapshot_date,
                extra=extra,
                seen_days=seen_days,
                retry_attempt=attempt_no,
            )
            queue_results.append(chunk_result)

            if chunk_result.status == "ok":
                seen_days.add(target_date)
                _backfill_queue_set_success(
                    schema=schema,
                    marketplace=config.marketplace,
                    source=config.source,
                    run_id=run_id,
                    target_date=target_date,
                )
                if config.source == "finance":
                    _finance_unstable_days_delete(
                        schema=schema,
                        marketplace=config.marketplace,
                        source=config.source,
                        target_date=target_date,
                    )
                    _backfill_queue_reset_finance_progress(
                        schema=schema,
                        marketplace=config.marketplace,
                        source=config.source,
                        target_date=target_date,
                    )
                continue

            err_type = chunk_result.error_type or _classify_error_type(chunk_result.error_message)
            queue_max_attempts = max(max_attempts, max_retries_default)
            raw_rows_total = max(int(chunk_result.raw_rows_count), 0)
            normalized_rows_total = max(int(chunk_result.normalized_rows_count), 0)
            loaded_rows_total = max(int(chunk_result.loaded_rows_count), 0)
            retry_after_sec: int | None = None
            next_state = "retry"
            if err_type == "rate_limit":
                retry_after_sec = _rate_limit_retry_after_seconds(
                    schema=schema,
                    marketplace=config.marketplace,
                    source=config.source,
                )
                if config.source == "finance":
                    retry_after_sec = max(retry_after_sec or 0, _finance_retry_delay_sec(attempt_no))
                if retry_after_sec is None:
                    retry_after_sec = _wb_backfill_retry_delay(max(attempt_no - 1, 0))
                next_state = "waiting_rate_limit"
            elif config.source == "finance" and _is_finance_provider_instability_error(err_type) and raw_rows_total == 0:
                retry_after_sec = _finance_provider_instability_retry_delay_sec(attempt_no)
                next_state = "provider_instability"
                next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=retry_after_sec)
                _finance_unstable_days_upsert(
                    schema=schema,
                    marketplace=config.marketplace,
                    source=config.source,
                    target_date=target_date,
                    attempts=attempt_no,
                    next_retry_at=next_retry_at,
                    error_type=err_type,
                    error_message=chunk_result.error_message,
                    raw_rows=raw_rows_total,
                    normalized_rows=normalized_rows_total,
                    loaded_rows=loaded_rows_total,
                    stg_rows=0,
                    run_id=run_id,
                )
            elif err_type == "page_cap_reached":
                if config.source == "orders":
                    _, _, _, no_progress_passes = _backfill_queue_update_orders_progress(
                        schema=schema,
                        marketplace=config.marketplace,
                        source=config.source,
                        target_date=target_date,
                        offset_advance=raw_rows_total,
                        loaded_rows=loaded_rows_total,
                        page_count=max(chunk_result.page_count, 0),
                    )
                    if no_progress_passes >= _wb_orders_stalled_no_progress_passes():
                        next_state = "stalled_day"
                        err_type = "stalled_day"
                        retry_after_sec = _wb_orders_stalled_retry_delay_sec()
                    else:
                        next_state = (
                            "partial_progress"
                            if loaded_rows_total > 0
                            else "resume_required"
                        )
                        err_type = next_state
                        retry_after_sec = _wb_worker_requeue_delay_sec()
                else:
                    retry_after_sec = _wb_worker_requeue_delay_sec()
                    next_state = "pending"
            elif err_type == "finance_incomplete_pagination":
                resume_rrdid = _extract_resume_rrdid_from_chunks((chunk_result,))
                _backfill_queue_update_finance_progress(
                    schema=schema,
                    marketplace=config.marketplace,
                    source=config.source,
                    target_date=target_date,
                    resume_rrdid=resume_rrdid,
                    raw_rows=raw_rows_total,
                    page_count=max(chunk_result.page_count, 0),
                )
                retry_after_sec = _finance_retry_delay_sec(attempt_no)
                next_state = "finance_incomplete_pagination"
            elif err_type == "normalizer_error":
                retry_after_sec = (
                    _finance_retry_delay_sec(attempt_no)
                    if config.source == "finance"
                    else _wb_backfill_retry_delay(max(attempt_no - 1, 0))
                )
                next_state = "retry"
            elif err_type in {
                "parse_error",
                "unexpected_format",
                "normalized_zero_rows",
                "target_empty",
                "http_success_non_json",
                "http_success_truncated_body",
            }:
                retry_after_sec = (
                    _finance_retry_delay_sec(attempt_no)
                    if config.source == "finance"
                    else _wb_backfill_retry_delay(max(attempt_no - 1, 0))
                )
                next_state = "target_empty" if err_type == "target_empty" else "parse_error"
            elif err_type in {"empty_response", "network_error", "network_before_body"}:
                retry_after_sec = (
                    _finance_retry_delay_sec(attempt_no)
                    if config.source == "finance"
                    else _wb_backfill_retry_delay(max(attempt_no - 1, 0))
                )
                next_state = "retry"
            else:
                retry_after_sec = (
                    _finance_retry_delay_sec(attempt_no)
                    if config.source == "finance"
                    else _wb_backfill_retry_delay(max(attempt_no - 1, 0))
                )
                next_state = "retry"

            if (
                attempt_no >= queue_max_attempts
                and err_type not in {"page_cap_reached", "partial_progress", "resume_required"}
                and not (config.source == "finance" and (attempt_no < 6 or next_state == "provider_instability"))
            ):
                next_state = "manual_review"
                retry_after_sec = None
                retry_exhausted = True

            _backfill_queue_set_failure(
                schema=schema,
                marketplace=config.marketplace,
                source=config.source,
                run_id=run_id,
                target_date=target_date,
                state=next_state,
                error_type=err_type,
                error_message=chunk_result.error_message,
                retry_after_sec=retry_after_sec,
                day_close_reason=(
                    err_type
                    if err_type in {
                        "truly_empty_day",
                        "day_close_date_mismatch",
                        "loaded_out_of_target_day",
                        "parse_or_mapping_issue",
                    }
                    else None
                ),
            )
            if not (config.source == "finance" and next_state == "provider_instability"):
                _finance_unstable_days_delete(
                    schema=schema,
                    marketplace=config.marketplace,
                    source=config.source,
                    target_date=target_date,
                )

    return queue_results, retry_exhausted


def _backfill_queue_upsert_dates(
    *,
    schema: str,
    marketplace: str,
    source: str,
    run_id: str,
    dates: list[date],
) -> None:
    if not dates:
        return
    query = sql.SQL(
        """
        INSERT INTO {schema}.ingestion_backfill_queue (
            schema_name,
            marketplace,
            source,
            target_date,
            lane,
            priority,
            state,
            attempts,
            max_attempts,
            retry_after_ts,
            last_attempt_at,
            last_http_status,
            last_error_type,
            last_error,
            run_id,
            created_at,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'pending', 0, %s, NULL, NULL, NULL, NULL, NULL, %s, now(), now())
        ON CONFLICT (schema_name, marketplace, source, target_date)
        DO UPDATE SET
            lane = EXCLUDED.lane,
            priority = LEAST({schema}.ingestion_backfill_queue.priority, EXCLUDED.priority),
            state = CASE
              WHEN {schema}.ingestion_backfill_queue.state = 'success' THEN {schema}.ingestion_backfill_queue.state
              WHEN {schema}.ingestion_backfill_queue.state = 'running' THEN {schema}.ingestion_backfill_queue.state
              WHEN {schema}.ingestion_backfill_queue.state IN ('partial_progress', 'resume_required', 'stalled_day', 'provider_instability')
                THEN {schema}.ingestion_backfill_queue.state
              ELSE 'pending'
            END,
            max_attempts = GREATEST({schema}.ingestion_backfill_queue.max_attempts, EXCLUDED.max_attempts),
            run_id = EXCLUDED.run_id,
            updated_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    max_attempts = _wb_backfill_max_retries()
    for target_date in dates:
        lane, priority = _queue_lane_and_priority(target_date)
        _queue_exec(
            query,
            (
                schema,
                marketplace,
                source,
                target_date,
                lane,
                priority,
                max_attempts,
                run_id,
            ),
        )
    if source == "finance" and _legacy_finance_queue_mirror_enabled():
        _queue_upsert_dates(
            schema=schema,
            marketplace=marketplace,
            source=source,
            run_id=run_id,
            dates=dates,
        )


def _backfill_queue_fetch_dates(
    *,
    schema: str,
    marketplace: str,
    source: str,
    date_from: date,
    date_to: date,
) -> list[tuple[date, int, int]]:
    query = sql.SQL(
        """
        SELECT target_date, attempts, max_attempts
        FROM {schema}.ingestion_backfill_queue
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date BETWEEN %s AND %s
          AND state IN (
            'pending',
            'retry',
            'waiting_rate_limit',
            'parse_error',
            'target_empty',
            'provider_instability',
            'partial_progress',
            'resume_required',
            'stalled_day'
          )
          AND attempts < max_attempts
          AND (retry_after_ts IS NULL OR retry_after_ts <= now())
        ORDER BY priority ASC, target_date ASC
        """
    ).format(schema=sql.Identifier(schema))
    rows = _queue_query(
        query,
        (
            schema,
            marketplace,
            source,
            date_from,
            date_to,
        ),
    )
    result: list[tuple[date, int, int]] = []
    for row in rows:
        if len(row) < 3:
            continue
        target_date = row[0]
        attempts = row[1]
        max_attempts = row[2]
        if isinstance(target_date, date) and isinstance(attempts, int) and isinstance(max_attempts, int):
            result.append((target_date, attempts, max_attempts))
    return result


def _backfill_queue_next_job(
    *,
    schema: str,
    marketplace: str,
    sources: tuple[str, ...],
    date_from: date,
    date_to: date,
) -> tuple[str, date, int, int] | None:
    if not sources:
        return None
    query = sql.SQL(
        """
        SELECT source, target_date, attempts, max_attempts
        FROM {schema}.ingestion_backfill_queue
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = ANY(%s)
          AND target_date BETWEEN %s AND %s
          AND state IN (
            'pending',
            'retry',
            'waiting_rate_limit',
            'parse_error',
            'target_empty',
            'provider_instability',
            'partial_progress',
            'resume_required',
            'stalled_day'
          )
          AND attempts < max_attempts
          AND (retry_after_ts IS NULL OR retry_after_ts <= now())
        ORDER BY priority ASC, target_date ASC
        LIMIT 1
        """
    ).format(schema=sql.Identifier(schema))
    rows = _queue_query(query, (schema, marketplace, list(sources), date_from, date_to))
    if not rows:
        return None
    row = rows[0]
    if len(row) < 4:
        return None
    source = row[0]
    target_date = row[1]
    attempts = row[2]
    max_attempts = row[3]
    if not isinstance(source, str) or not isinstance(target_date, date):
        return None
    if not isinstance(attempts, int) or not isinstance(max_attempts, int):
        return None
    return source, target_date, attempts, max_attempts


def _backfill_queue_mark_running(
    *,
    schema: str,
    marketplace: str,
    source: str,
    run_id: str,
    target_date: date,
    attempts: int,
    worker_id: str | None = None,
    lease_sec: int | None = None,
) -> None:
    lease = max(int(lease_sec or _wb_backfill_lease_sec()), 30)
    query = sql.SQL(
        """
        UPDATE {schema}.ingestion_backfill_queue
        SET
          state = 'running',
          attempts = %s,
          worker_id = %s,
          leased_at = CASE WHEN %s::text IS NULL THEN NULL ELSE now() END,
          lease_expires_at = CASE
            WHEN %s::text IS NULL THEN NULL
            ELSE now() + make_interval(secs => %s)
          END,
          heartbeat_at = CASE WHEN %s::text IS NULL THEN NULL ELSE now() END,
          retry_after_ts = NULL,
          last_attempt_at = now(),
          run_id = %s,
          updated_at = now()
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date = %s
        """
    ).format(schema=sql.Identifier(schema))
    _queue_exec(
        query,
        (
            attempts,
            worker_id,
            worker_id,
            worker_id,
            lease,
            worker_id,
            run_id,
            schema,
            marketplace,
            source,
            target_date,
        ),
    )
    if source == "finance" and _legacy_finance_queue_mirror_enabled():
        _queue_mark_running(
            schema=schema,
            marketplace=marketplace,
            source=source,
            run_id=run_id,
            target_date=target_date,
            retry_count=attempts,
        )


def _backfill_queue_set_success(
    *,
    schema: str,
    marketplace: str,
    source: str,
    run_id: str,
    target_date: date,
    worker_id: str | None = None,
) -> None:
    query = sql.SQL(
        """
        UPDATE {schema}.ingestion_backfill_queue
        SET
          state = 'success',
          finance_resume_rrdid = CASE WHEN %s = 'finance' THEN 0 ELSE finance_resume_rrdid END,
          finance_pages_done = CASE WHEN %s = 'finance' THEN 0 ELSE finance_pages_done END,
          finance_rows_done = CASE WHEN %s = 'finance' THEN 0 ELSE finance_rows_done END,
          finance_last_progress_at = CASE WHEN %s = 'finance' THEN NULL ELSE finance_last_progress_at END,
          finance_no_progress_passes = CASE WHEN %s = 'finance' THEN 0 ELSE finance_no_progress_passes END,
          retry_after_ts = NULL,
          last_attempt_at = now(),
          last_http_status = 200,
          last_error_type = NULL,
          last_error = NULL,
          day_close_reason = NULL,
          worker_id = NULL,
          leased_at = NULL,
          lease_expires_at = NULL,
          heartbeat_at = NULL,
          run_id = %s,
          updated_at = now()
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date = %s
          AND (%s::text IS NULL OR worker_id IS NULL OR worker_id = %s::text)
        """
    ).format(schema=sql.Identifier(schema))
    _queue_exec(
        query,
        (
            run_id,
            source,
            source,
            source,
            source,
            source,
            schema,
            marketplace,
            source,
            target_date,
            worker_id,
            worker_id,
        ),
    )
    if source == "finance" and _legacy_finance_queue_mirror_enabled():
        _queue_set_success(
            schema=schema,
            marketplace=marketplace,
            source=source,
            run_id=run_id,
            target_date=target_date,
        )


def _rate_limit_retry_after_seconds(*, schema: str, marketplace: str, source: str) -> int | None:
    query = sql.SQL(
        """
        SELECT EXTRACT(EPOCH FROM (cooldown_until - now()))
        FROM {schema}.ingestion_rate_limit_state
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND cooldown_until IS NOT NULL
        ORDER BY cooldown_until DESC NULLS LAST
        LIMIT 1
        """
    ).format(schema=sql.Identifier(schema))
    rows = _queue_query(query, (schema, marketplace, source))
    if not rows or not rows[0]:
        return None
    value = rows[0][0]
    try:
        retry_after = int(float(value))
    except Exception:
        return None
    return retry_after if retry_after > 0 else None


def _latest_rate_limit_headers(
    *,
    schema: str,
    marketplace: str,
    source: str,
) -> tuple[str | None, str | None, str | None]:
    query = sql.SQL(
        """
        SELECT x_ratelimit_retry, x_ratelimit_limit, x_ratelimit_reset
        FROM {schema}.ingestion_rate_limit_state
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
        ORDER BY observed_at DESC
        LIMIT 1
        """
    ).format(schema=sql.Identifier(schema))
    rows = _queue_query(query, (schema, marketplace, source))
    if not rows or len(rows[0]) < 3:
        return (None, None, None)
    retry_val = rows[0][0]
    limit_val = rows[0][1]
    reset_val = rows[0][2]
    return (
        str(retry_val) if retry_val is not None else None,
        str(limit_val) if limit_val is not None else None,
        str(reset_val) if reset_val is not None else None,
    )


def _backfill_queue_set_failure(
    *,
    schema: str,
    marketplace: str,
    source: str,
    run_id: str,
    target_date: date,
    state: str,
    error_type: str | None,
    error_message: str | None,
    retry_after_sec: int | None,
    day_close_reason: str | None = None,
    worker_id: str | None = None,
) -> None:
    retry_after_ts: datetime | None = None
    if retry_after_sec is not None and retry_after_sec > 0:
        retry_after_ts = datetime.now(timezone.utc) + timedelta(seconds=retry_after_sec)
    rate_retry, rate_limit, rate_reset = _latest_rate_limit_headers(
        schema=schema,
        marketplace=marketplace,
        source=source,
    )
    query = sql.SQL(
        """
        UPDATE {schema}.ingestion_backfill_queue
        SET
          state = %s,
          retry_after_ts = %s,
          last_attempt_at = now(),
          last_error_type = %s,
          last_error = %s,
          day_close_reason = %s,
          rate_limit_retry = %s,
          rate_limit_limit = %s,
          rate_limit_reset = %s,
          worker_id = NULL,
          leased_at = NULL,
          lease_expires_at = NULL,
          heartbeat_at = NULL,
          run_id = %s,
          updated_at = now()
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date = %s
          AND (%s::text IS NULL OR worker_id IS NULL OR worker_id = %s::text)
        """
    ).format(schema=sql.Identifier(schema))
    _queue_exec(
        query,
        (
            state,
            retry_after_ts,
            error_type,
            error_message,
            day_close_reason,
            rate_retry,
            rate_limit,
            rate_reset,
            run_id,
            schema,
            marketplace,
            source,
            target_date,
            worker_id,
            worker_id,
        ),
    )
    if source == "finance" and _legacy_finance_queue_mirror_enabled():
        legacy_status = "pending"
        if state == "manual_review":
            legacy_status = "failed"
        _queue_set_failure(
            schema=schema,
            marketplace=marketplace,
            source=source,
            run_id=run_id,
            target_date=target_date,
            status=legacy_status,
            error_type=error_type,
            error_message=error_message,
        )


def _backfill_queue_touch_running(
    *,
    schema: str,
    marketplace: str,
    source: str,
    target_date: date,
    worker_id: str,
    lease_sec: int,
) -> None:
    query = sql.SQL(
        """
        UPDATE {schema}.ingestion_backfill_queue
        SET
          heartbeat_at = now(),
          lease_expires_at = now() + make_interval(secs => %s),
          updated_at = now()
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date = %s
          AND state = 'running'
          AND worker_id = %s
        """
    ).format(schema=sql.Identifier(schema))
    _queue_exec(
        query,
        (
            max(int(lease_sec), 30),
            schema,
            marketplace,
            source,
            target_date,
            worker_id,
        ),
    )


def _backfill_queue_heartbeat_loop(
    *,
    stop_event: threading.Event,
    schema: str,
    marketplace: str,
    source: str,
    target_date: date,
    worker_id: str,
    interval_sec: int,
    lease_sec: int,
) -> None:
    interval = max(int(interval_sec), 1)
    while not stop_event.wait(interval):
        _backfill_queue_touch_running(
            schema=schema,
            marketplace=marketplace,
            source=source,
            target_date=target_date,
            worker_id=worker_id,
            lease_sec=lease_sec,
        )


def _backfill_queue_recover_stale_running(
    *,
    schema: str,
    marketplace: str,
    sources: tuple[str, ...],
) -> int:
    if not sources:
        return 0
    stale_sec = _wb_backfill_stale_sec()
    query = sql.SQL(
        """
        WITH recovered AS (
          UPDATE {schema}.ingestion_backfill_queue q
          SET
            state = CASE WHEN q.attempts > 0 THEN 'retry' ELSE 'pending' END,
            retry_after_ts = NULL,
            last_error_type = COALESCE(q.last_error_type, 'stale_running_recovered'),
            last_error = CASE
              WHEN q.last_error IS NULL OR q.last_error = ''
                THEN 'stale_running_recovered: heartbeat/lease expired'
              ELSE q.last_error
            END,
            worker_id = NULL,
            leased_at = NULL,
            lease_expires_at = NULL,
            heartbeat_at = NULL,
            updated_at = now()
          WHERE q.schema_name = %s
            AND q.marketplace = %s
            AND q.source = ANY(%s)
            AND q.state = 'running'
            AND (
              (q.lease_expires_at IS NOT NULL AND q.lease_expires_at <= now())
              OR COALESCE(q.heartbeat_at, q.last_attempt_at, q.leased_at, q.updated_at) <= now() - make_interval(secs => %s)
            )
          RETURNING 1
        )
        SELECT COUNT(*)::int FROM recovered
        """
    ).format(schema=sql.Identifier(schema))
    rows = _queue_query(
        query,
        (
            schema,
            marketplace,
            list(sources),
            max(int(stale_sec), 30),
        ),
    )
    if not rows or not rows[0]:
        return 0
    try:
        recovered = int(rows[0][0] or 0)
    except Exception:
        recovered = 0
    if recovered > 0:
        logger.warning(
            "Recovered stale backfill queue rows schema=%s marketplace=%s rows=%s",
            schema,
            marketplace,
            recovered,
        )
    return recovered


def _backfill_queue_mark_seen_days_success(
    *,
    schema: str,
    marketplace: str,
    source: str,
    run_id: str,
    days: set[date],
) -> None:
    for day in days:
        _backfill_queue_set_success(
            schema=schema,
            marketplace=marketplace,
            source=source,
            run_id=run_id,
            target_date=day,
        )


def _finance_backfill_max_cycles() -> int:
    raw = (os.getenv("WB_FINANCE_BACKFILL_MAX_CYCLES") or "").strip()
    if not raw:
        return 3
    try:
        return max(int(raw), 1)
    except ValueError:
        return 3


def _finance_backfill_max_retries() -> int:
    raw = (os.getenv("WB_FINANCE_BACKFILL_MAX_RETRIES") or "").strip()
    if not raw:
        return 5
    try:
        return max(int(raw), 1)
    except ValueError:
        return 5


def _process_finance_backfill_queue(
    *,
    config: SourceConfig,
    schema: str,
    mode: str,
    dry_run: bool,
    run_id: str,
    date_from: date | None,
    date_to: date | None,
    sample_path: str | None,
    limit: int,
    max_pages: int,
    offset: int,
    page: int,
    pagination_mode: str | None,
    advert_ids: tuple[int, ...],
    snapshot_date: date | None,
    extra: Mapping[str, Any],
    seen_days: set[date],
) -> tuple[list[IngestionChunkResult], bool]:
    if date_from is None or date_to is None:
        return [], False

    max_cycles = _finance_backfill_max_cycles()
    max_retries = _finance_backfill_max_retries()
    queue_results: list[IngestionChunkResult] = []
    retry_exhausted = False

    for cycle in range(1, max_cycles + 1):
        _queue_mark_seen_days_success(
            schema=schema,
            marketplace=config.marketplace,
            source=config.source,
            run_id=run_id,
            days=seen_days,
        )
        missing_dates = _missing_dates_in_period(
            date_from=date_from,
            date_to=date_to,
            seen_days=seen_days,
        )
        if not missing_dates:
            break
        _queue_upsert_dates(
            schema=schema,
            marketplace=config.marketplace,
            source=config.source,
            run_id=run_id,
            dates=missing_dates,
        )

        candidates = _queue_fetch_dates(
            schema=schema,
            marketplace=config.marketplace,
            source=config.source,
            date_from=date_from,
            date_to=date_to,
            max_retries=max_retries,
        )
        if not candidates:
            break

        logger.info(
            "WB finance queue backfill cycle=%s/%s run_id=%s candidates=%s missing_days=%s",
            cycle,
            max_cycles,
            run_id,
            len(candidates),
            len(missing_dates),
        )

        for target_date, retry_count in candidates:
            if target_date in seen_days:
                _queue_set_success(
                    schema=schema,
                    marketplace=config.marketplace,
                    source=config.source,
                    run_id=run_id,
                    target_date=target_date,
                )
                continue

            attempt_no = retry_count + 1
            _queue_mark_running(
                schema=schema,
                marketplace=config.marketplace,
                source=config.source,
                run_id=run_id,
                target_date=target_date,
                retry_count=attempt_no,
            )
            queue_chunk_id = f"{run_id}_q{cycle:02d}_{target_date.strftime('%Y%m%d')}"
            chunk_result = _run_single_chunk(
                config=config,
                schema=schema,
                mode=mode,
                dry_run=dry_run,
                run_id=run_id,
                chunk_id=queue_chunk_id,
                date_from=date_from,
                date_to=date_to,
                chunk_from=target_date,
                chunk_to=target_date,
                sample_path=sample_path,
                limit=limit,
                max_pages=max_pages,
                offset=offset,
                page=page,
                pagination_mode=pagination_mode,
                advert_ids=advert_ids,
                snapshot_date=snapshot_date,
                extra=extra,
                seen_days=seen_days,
                retry_attempt=attempt_no,
            )
            queue_results.append(chunk_result)
            if chunk_result.status == "ok" and target_date in seen_days:
                _queue_set_success(
                    schema=schema,
                    marketplace=config.marketplace,
                    source=config.source,
                    run_id=run_id,
                    target_date=target_date,
                )
                continue

            err_type = chunk_result.error_type or _classify_error_type(chunk_result.error_message)
            next_status = "failed" if attempt_no >= max_retries else "pending"
            if next_status == "failed":
                retry_exhausted = True
            _queue_set_failure(
                schema=schema,
                marketplace=config.marketplace,
                source=config.source,
                run_id=run_id,
                target_date=target_date,
                status=next_status,
                error_type=err_type,
                error_message=chunk_result.error_message,
            )

    return queue_results, retry_exhausted


def _queue_upsert_dates(
    *,
    schema: str,
    marketplace: str,
    source: str,
    run_id: str,
    dates: list[date],
) -> None:
    if not dates:
        return
    query = sql.SQL(
        """
        INSERT INTO {schema}.finance_backfill_queue (
            schema_name,
            marketplace,
            source,
            target_date,
            retry_count,
            last_attempt_at,
            status,
            last_error_type,
            last_error,
            run_id,
            created_at,
            updated_at
        )
        VALUES (%s, %s, %s, %s, 0, NULL, 'pending', NULL, NULL, %s, now(), now())
        ON CONFLICT (schema_name, marketplace, source, target_date)
        DO UPDATE SET
            status = CASE
              WHEN finance_backfill_queue.status = 'success' THEN finance_backfill_queue.status
              ELSE 'pending'
            END,
            run_id = EXCLUDED.run_id,
            updated_at = now()
        """
    ).format(schema=sql.Identifier(schema))
    for target_date in dates:
        _queue_exec(
            query,
            (
                schema,
                marketplace,
                source,
                target_date,
                run_id,
            ),
        )


def _queue_fetch_dates(
    *,
    schema: str,
    marketplace: str,
    source: str,
    date_from: date,
    date_to: date,
    max_retries: int,
) -> list[tuple[date, int]]:
    query = sql.SQL(
        """
        SELECT target_date, retry_count
        FROM {schema}.finance_backfill_queue
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date BETWEEN %s AND %s
          AND status IN ('pending', 'failed')
          AND retry_count < %s
        ORDER BY target_date
        """
    ).format(schema=sql.Identifier(schema))
    rows = _queue_query(
        query,
        (
            schema,
            marketplace,
            source,
            date_from,
            date_to,
            max_retries,
        ),
    )
    result: list[tuple[date, int]] = []
    for row in rows:
        if len(row) < 2:
            continue
        target_date = row[0]
        retry_count = row[1]
        if isinstance(target_date, date) and isinstance(retry_count, int):
            result.append((target_date, retry_count))
    return result


def _queue_mark_running(
    *,
    schema: str,
    marketplace: str,
    source: str,
    run_id: str,
    target_date: date,
    retry_count: int,
) -> None:
    query = sql.SQL(
        """
        UPDATE {schema}.finance_backfill_queue
        SET
          status = 'running',
          retry_count = %s,
          last_attempt_at = now(),
          run_id = %s,
          updated_at = now()
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date = %s
        """
    ).format(schema=sql.Identifier(schema))
    _queue_exec(
        query,
        (
            retry_count,
            run_id,
            schema,
            marketplace,
            source,
            target_date,
        ),
    )


def _queue_set_success(
    *,
    schema: str,
    marketplace: str,
    source: str,
    run_id: str,
    target_date: date,
) -> None:
    query = sql.SQL(
        """
        UPDATE {schema}.finance_backfill_queue
        SET
          status = 'success',
          last_attempt_at = now(),
          last_error_type = NULL,
          last_error = NULL,
          run_id = %s,
          updated_at = now()
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date = %s
        """
    ).format(schema=sql.Identifier(schema))
    _queue_exec(
        query,
        (
            run_id,
            schema,
            marketplace,
            source,
            target_date,
        ),
    )


def _queue_set_failure(
    *,
    schema: str,
    marketplace: str,
    source: str,
    run_id: str,
    target_date: date,
    status: str,
    error_type: str | None,
    error_message: str | None,
) -> None:
    query = sql.SQL(
        """
        UPDATE {schema}.finance_backfill_queue
        SET
          status = %s,
          last_attempt_at = now(),
          last_error_type = %s,
          last_error = %s,
          run_id = %s,
          updated_at = now()
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND target_date = %s
        """
    ).format(schema=sql.Identifier(schema))
    _queue_exec(
        query,
        (
            status,
            error_type,
            error_message,
            run_id,
            schema,
            marketplace,
            source,
            target_date,
        ),
    )


def _queue_mark_seen_days_success(
    *,
    schema: str,
    marketplace: str,
    source: str,
    run_id: str,
    days: set[date],
) -> None:
    for day in days:
        _queue_set_success(
            schema=schema,
            marketplace=marketplace,
            source=source,
            run_id=run_id,
            target_date=day,
        )


def _queue_exec(query: sql.SQL, params: tuple[Any, ...]) -> None:
    try:
        with get_conn() as conn:
            with conn.transaction():
                conn.execute(query, params)
    except Exception as exc:
        logger.warning("Skip finance backfill queue write: %s", exc)


def _queue_query(query: sql.SQL, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    try:
        with get_conn() as conn:
            with conn.transaction():
                cur = conn.execute(query, params)
                rows = cur.fetchall()
                return [tuple(row) for row in rows]
    except Exception as exc:
        logger.warning("Skip finance backfill queue read: %s", exc)
        return []


def _make_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}_{uuid4().hex[:8]}"


def _audit_run_start(
    *,
    run_id: str,
    marketplace: str,
    source: str,
    schema: str,
    mode: str,
    date_from: date | None,
    date_to: date | None,
) -> None:
    query = sql.SQL(
        """
        INSERT INTO {schema}.ingestion_run (
            run_id,
            marketplace,
            source,
            schema_name,
            mode,
            date_from,
            date_to,
            started_at,
            status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s)
        """
    ).format(schema=sql.Identifier(schema))
    params = (
        run_id,
        marketplace,
        source,
        schema,
        mode,
        date_from,
        date_to,
        "running",
    )
    _audit_exec(query, params)


def _audit_run_finish(*, schema: str, run_id: str, status: str) -> None:
    query = sql.SQL(
        """
        UPDATE {schema}.ingestion_run
        SET finished_at = now(), status = %s
        WHERE run_id = %s
    """
    ).format(schema=sql.Identifier(schema))
    _audit_exec(query, (status, run_id))


def _audit_chunk(*, schema: str, run_id: str, chunk_result: IngestionChunkResult) -> None:
    query = sql.SQL(
        """
        INSERT INTO {schema}.ingestion_chunk_audit (
            run_id,
            chunk_id,
            chunk_date_from,
            chunk_date_to,
            retry_attempts,
            page_count,
            raw_files_count,
            raw_rows_count,
            normalized_rows_count,
            loaded_rows_count,
            status,
            error_type,
            error_message,
            created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
    """
    ).format(schema=sql.Identifier(schema))
    params = (
        run_id,
        chunk_result.chunk_id,
        chunk_result.chunk_date_from,
        chunk_result.chunk_date_to,
        chunk_result.retry_attempts,
        chunk_result.page_count,
        chunk_result.raw_files_count,
        chunk_result.raw_rows_count,
        chunk_result.normalized_rows_count,
        chunk_result.loaded_rows_count,
        chunk_result.status,
        chunk_result.error_type,
        chunk_result.error_message,
    )
    _audit_exec(query, params)


def _audit_request(
    *,
    schema: str,
    run_id: str,
    chunk_id: str,
    marketplace: str,
    source: str,
    endpoint: Any,
    request_url: Any,
    target_date: date | None,
    request_attempt_no: int,
    http_status: int | None,
    request_started_at: str,
    request_finished_at: str,
    response_body_size: int,
    response_content_type: str | None,
    success: bool,
    error_type: Any,
    error_message: Any,
    rate_limit_retry: Any,
    rate_limit_limit: Any,
    rate_limit_reset: Any,
    cooldown_wait_sec: Any,
) -> None:
    endpoint_text = str(endpoint) if endpoint is not None else ""
    request_url_text = str(request_url) if request_url is not None else ""
    error_type_text = str(error_type) if error_type is not None else None
    error_message_text = str(error_message) if error_message is not None else None
    rate_retry_text = str(rate_limit_retry) if rate_limit_retry is not None else None
    rate_limit_text = str(rate_limit_limit) if rate_limit_limit is not None else None
    rate_reset_text = str(rate_limit_reset) if rate_limit_reset is not None else None
    try:
        cooldown_val = float(cooldown_wait_sec) if cooldown_wait_sec is not None else 0.0
    except Exception:
        cooldown_val = 0.0

    query = sql.SQL(
        """
        INSERT INTO {schema}.ingestion_request_log (
            run_id,
            chunk_id,
            marketplace,
            source,
            endpoint,
            request_url,
            target_date,
            request_attempt_no,
            request_started_at,
            request_finished_at,
            http_status,
            response_body_size,
            response_content_type,
            success,
            error_type,
            error_message,
            rate_limit_retry,
            rate_limit_limit,
            rate_limit_reset,
            cooldown_wait_sec,
            created_at
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s,
            %s::timestamptz, %s::timestamptz,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()
        )
        """
    ).format(schema=sql.Identifier(schema))
    params = (
        run_id,
        chunk_id,
        marketplace,
        source,
        endpoint_text,
        request_url_text,
        target_date,
        request_attempt_no,
        request_started_at,
        request_finished_at,
        http_status,
        response_body_size,
        response_content_type,
        success,
        error_type_text,
        error_message_text,
        rate_retry_text,
        rate_limit_text,
        rate_reset_text,
        cooldown_val,
    )
    _audit_exec(query, params)

    if marketplace == "wb" and source in WB_HEAVY_BACKFILL_SOURCES:
        _persist_rate_limit_state(
            schema=schema,
            marketplace=marketplace,
            source=source,
            endpoint=endpoint_text or f"/{source}",
            http_status=http_status,
            request_finished_at=request_finished_at,
            rate_limit_retry=rate_retry_text,
            rate_limit_limit=rate_limit_text,
            rate_limit_reset=rate_reset_text,
        )

    if rate_retry_text is None and rate_limit_text is None and rate_reset_text is None:
        return
    headers_query = sql.SQL(
        """
        INSERT INTO {schema}.rate_limit_headers (
            run_id,
            chunk_id,
            marketplace,
            source,
            endpoint,
            target_date,
            request_attempt_no,
            http_status,
            x_ratelimit_retry,
            x_ratelimit_limit,
            x_ratelimit_reset,
            observed_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        """
    ).format(schema=sql.Identifier(schema))
    headers_params = (
        run_id,
        chunk_id,
        marketplace,
        source,
        endpoint_text,
        target_date,
        request_attempt_no,
        http_status,
        rate_retry_text,
        rate_limit_text,
        rate_reset_text,
    )
    _audit_exec(headers_query, headers_params)



def _audit_completeness(
    *,
    run_id: str,
    marketplace: str,
    source: str,
    schema: str,
    expected_days: int,
    actual_days: int,
    missing_days_count: int,
    coverage_ratio: float,
    expected_entities: int,
    actual_entities: int,
    row_loss_ratio: float,
    status: str,
    source_criticality_value: str,
    usable_for_publish: bool,
    error_type: str | None,
    retry_exhausted: bool,
    partial_reason: str | None,
) -> None:
    query = sql.SQL(
        """
        INSERT INTO {schema}.ingestion_completeness (
            run_id,
            marketplace,
            source,
            schema_name,
            expected_days,
            actual_days,
            missing_days_count,
            coverage_ratio,
            expected_entities,
            actual_entities,
            row_loss_ratio,
            status,
            source_criticality,
            usable_for_publish,
            error_type,
            retry_exhausted,
            partial_reason,
            created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
        """
    ).format(schema=sql.Identifier(schema))
    params = (
        run_id,
        marketplace,
        source,
        schema,
        expected_days,
        actual_days,
        missing_days_count,
        coverage_ratio,
        expected_entities,
        actual_entities,
        row_loss_ratio,
        status,
        source_criticality_value,
        usable_for_publish,
        error_type,
        retry_exhausted,
        partial_reason,
    )
    _audit_exec(query, params)


def _record_ingestion_performance_metrics(
    *,
    schema: str,
    marketplace: str,
    source: str,
) -> None:
    requests_per_minute_query = sql.SQL(
        """
        SELECT COUNT(*)::int
        FROM {schema}.ingestion_request_log
        WHERE marketplace = %s
          AND source = %s
          AND created_at >= now() - interval '1 minute'
        """
    ).format(schema=sql.Identifier(schema))
    requests_per_minute = _queue_scalar_int(requests_per_minute_query, (marketplace, source))

    success_days_per_hour_query = sql.SQL(
        """
        SELECT COUNT(*)::int
        FROM {schema}.ingestion_backfill_queue
        WHERE schema_name = %s
          AND marketplace = %s
          AND source = %s
          AND state = 'success'
          AND updated_at >= now() - interval '1 hour'
        """
    ).format(schema=sql.Identifier(schema))
    success_days_per_hour = _queue_scalar_int(success_days_per_hour_query, (schema, marketplace, source))

    rate_limit_per_hour_query = sql.SQL(
        """
        SELECT COUNT(*)::int
        FROM {schema}.ingestion_request_log
        WHERE marketplace = %s
          AND source = %s
          AND http_status = 429
          AND created_at >= now() - interval '1 hour'
        """
    ).format(schema=sql.Identifier(schema))
    rate_limit_429_per_hour = _queue_scalar_int(rate_limit_per_hour_query, (marketplace, source))

    avg_wait_after_429_query = sql.SQL(
        """
        WITH req AS (
          SELECT
            request_started_at,
            request_finished_at,
            http_status,
            LEAD(request_started_at) OVER (ORDER BY request_started_at) AS next_request_started_at
          FROM {schema}.ingestion_request_log
          WHERE marketplace = %s
            AND source = %s
            AND request_started_at >= now() - interval '1 hour'
        )
        SELECT AVG(EXTRACT(EPOCH FROM (next_request_started_at - request_finished_at)))::numeric
        FROM req
        WHERE http_status = 429
          AND next_request_started_at IS NOT NULL
        """
    ).format(schema=sql.Identifier(schema))
    avg_wait_after_429 = _queue_scalar_float(avg_wait_after_429_query, (marketplace, source))

    insert_query = sql.SQL(
        """
        INSERT INTO {schema}.ingestion_performance_metrics (
            schema_name,
            marketplace,
            source,
            observed_at,
            requests_per_minute,
            success_days_per_hour,
            rate_limit_429_per_hour,
            average_wait_time_after_429,
            created_at
        )
        VALUES (%s, %s, %s, now(), %s, %s, %s, %s, now())
        """
    ).format(schema=sql.Identifier(schema))
    _audit_exec(
        insert_query,
        (
            schema,
            marketplace,
            source,
            requests_per_minute,
            success_days_per_hour,
            rate_limit_429_per_hour,
            avg_wait_after_429,
        ),
    )


def _queue_scalar_int(query: sql.SQL, params: tuple[Any, ...]) -> int:
    rows = _queue_query(query, params)
    if not rows or not rows[0]:
        return 0
    try:
        return int(rows[0][0] or 0)
    except Exception:
        return 0


def _queue_scalar_float(query: sql.SQL, params: tuple[Any, ...]) -> float:
    rows = _queue_query(query, params)
    if not rows or not rows[0]:
        return 0.0
    try:
        return float(rows[0][0] or 0.0)
    except Exception:
        return 0.0


def _audit_exec(query: sql.SQL | str, params: tuple[Any, ...]) -> None:
    try:
        with get_conn() as conn:
            run_in_transaction(
                conn,
                lambda current_conn: current_conn.execute(query, params),
            )
    except Exception as exc:
        logger.warning("Skip ingestion audit write: %s", exc)
