from __future__ import annotations

import os
import re
import subprocess
import threading
import time
import csv
import io
from datetime import date, datetime, timedelta
from decimal import Decimal
from logging import getLogger
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from psycopg.rows import dict_row

from src.services.recommendations import enrich_action_row, enrich_playbook_row, enrich_product_row
from src.utils.db import get_conn

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SCHEMA_ENV_KEYS: tuple[str, ...] = ("OZON_SCHEMA", "SCHEMA")
MARKETPLACE_ENV_KEYS: tuple[str, ...] = ("OZON_MARKETPLACE", "MARKETPLACE")
ALLOWED_MARKETPLACES: set[str] = {"ozon", "wb"}

ADS_RULE_CODES = {"negative_profit_after_ads", "ads_eating_profit", "ads_without_sales"}
DEMO_SCHEMA_MARKETPLACE: dict[str, str] = {
    "client_demo_rich": "ozon",
    "client_demo_wb_rich": "wb",
}
DEMO_PERIOD_START = "2026-02-01"
DEMO_PERIOD_END = "2026-03-01"
AUTO_DEMO_BUILD = os.getenv("AUTO_DEMO_BUILD", "1").strip().lower() not in {"0", "false", "no"}

logger = getLogger(__name__)
_demo_seed_lock = threading.Lock()
_demo_seed_last_run: dict[str, float] = {}


class CostInputUpsert(BaseModel):
    marketplace: str = Field(..., pattern="^(ozon|wb)$")
    offer_id: str = "~"
    product_id: str | None = None
    sku: str | None = None
    product_name_snapshot: str | None = None
    cogs_purchase_amount: float = 0
    packaging_amount: float = 0
    labeling_amount: float = 0
    inbound_logistics_amount: float = 0
    assembly_amount: float = 0
    other_unit_cost_amount: float = 0
    currency: str = "RUB"
    valid_from: date
    valid_to: date | None = None
    source_type: str = "manual"
    source_comment: str | None = None
    updated_by: str | None = None
    is_active: bool = True


class ReplenishmentInputUpsert(BaseModel):
    marketplace: str = Field(..., pattern="^(ozon|wb)$")
    offer_id: str = "~"
    expected_replenishment_date: date | None = None
    planned_replenishment_qty: float | None = None
    replenishment_status: str = "unknown"
    source_comment: str | None = None
    updated_by: str | None = None
    is_active: bool = True


class CostImportRequest(BaseModel):
    csv_data: str
    mode: str = Field(default="upsert", pattern="^(upsert|create|skip_conflicts)$")
    updated_by: str | None = None


def resolve_schema(schema: str | None) -> str:
    if schema:
        resolved = schema
    else:
        resolved = ""
        for key in SCHEMA_ENV_KEYS:
            candidate = os.getenv(key)
            if candidate:
                resolved = candidate
                break

    if not resolved:
        raise HTTPException(
            status_code=400,
            detail=f"Schema is required: pass query param 'schema' or set one of env vars: {', '.join(SCHEMA_ENV_KEYS)}",
        )

    if not SCHEMA_PATTERN.fullmatch(resolved):
        raise HTTPException(status_code=400, detail="Invalid schema name")
    return resolved


def resolve_marketplace(marketplace: str | None) -> str | None:
    resolved = (marketplace or "").strip().lower()
    if not resolved:
        for key in MARKETPLACE_ENV_KEYS:
            candidate = (os.getenv(key) or "").strip().lower()
            if candidate:
                resolved = candidate
                break

    if not resolved:
        return None
    if resolved not in ALLOWED_MARKETPLACES:
        raise HTTPException(status_code=400, detail="Invalid marketplace. Allowed: ozon, wb")
    return resolved


def normalize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: normalize_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_json(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]


def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
    if row is None:
        return None
    return dict(row)


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    if isinstance(value, str) and not value.strip():
        return Decimal("0")
    return Decimal(str(value))


def _normalize_offer_id(value: str | None) -> str:
    text = (value or "").strip()
    return text or "~"


def _sync_fact_cogs_from_cost_input(schema: str) -> int:
    with get_conn() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    WITH ranked AS (
                      SELECT
                        c.marketplace,
                        c.offer_id,
                        c.valid_from,
                        c.valid_to,
                        (COALESCE(c.cogs_purchase_amount, 0)
                         + COALESCE(c.packaging_amount, 0)
                         + COALESCE(c.labeling_amount, 0)
                         + COALESCE(c.inbound_logistics_amount, 0)
                         + COALESCE(c.assembly_amount, 0)
                         + COALESCE(c.other_unit_cost_amount, 0)
                        )::numeric(18,2) AS unit_cogs,
                        COALESCE(c.packaging_amount, 0)::numeric(18,2) AS packaging_cost,
                        COALESCE(c.inbound_logistics_amount, 0)::numeric(18,2) AS inbound_shipping_cost,
                        (COALESCE(c.labeling_amount, 0)
                         + COALESCE(c.assembly_amount, 0)
                         + COALESCE(c.other_unit_cost_amount, 0)
                        )::numeric(18,2) AS other_variable_cost,
                        ROW_NUMBER() OVER (
                          PARTITION BY c.marketplace, c.offer_id
                          ORDER BY c.valid_from DESC, c.updated_at DESC, c.id DESC
                        ) AS rn
                      FROM {schema}.product_cost_input c
                      WHERE c.is_active = TRUE
                    )
                    INSERT INTO {schema}.fact_cogs (
                      offer_id,
                      marketplace,
                      unit_cogs,
                      packaging_cost,
                      inbound_shipping_cost,
                      other_variable_cost,
                      effective_from,
                      effective_to,
                      updated_at
                    )
                    SELECT
                      r.offer_id,
                      r.marketplace,
                      r.unit_cogs,
                      r.packaging_cost,
                      r.inbound_shipping_cost,
                      r.other_variable_cost,
                      r.valid_from,
                      r.valid_to,
                      now()
                    FROM ranked r
                    WHERE r.rn = 1
                    ON CONFLICT (offer_id) DO UPDATE
                    SET
                      marketplace = EXCLUDED.marketplace,
                      unit_cogs = EXCLUDED.unit_cogs,
                      packaging_cost = EXCLUDED.packaging_cost,
                      inbound_shipping_cost = EXCLUDED.inbound_shipping_cost,
                      other_variable_cost = EXCLUDED.other_variable_cost,
                      effective_from = EXCLUDED.effective_from,
                      effective_to = EXCLUDED.effective_to,
                      updated_at = now(),
                      loaded_at = now()
                    """
                )
                return cur.rowcount


def _table_exists(schema: str, table_name: str) -> bool:
    row = fetch_one(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_name = %s
        LIMIT 1
        """,
        (schema, table_name),
    )
    return row is not None


def _demo_summary_has_rows(schema: str, marketplace: str) -> bool:
    if not _table_exists(schema, "mart_store_summary_30d"):
        return False

    row = fetch_one(
        f"""
        SELECT 1
        FROM {schema}.mart_store_summary_30d
        WHERE marketplace = %s
        LIMIT 1
        """,
        (marketplace,),
    )
    return row is not None


def _run_cli_command(args: list[str]) -> None:
    completed = subprocess.run(
        args,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        raise RuntimeError(f"Command failed: {' '.join(args)}\nstdout: {stdout}\nstderr: {stderr}")


def ensure_demo_mode_data(schema: str, marketplace: str | None) -> None:
    if not AUTO_DEMO_BUILD:
        return

    demo_marketplace = DEMO_SCHEMA_MARKETPLACE.get(schema)
    if not demo_marketplace:
        return

    effective_marketplace = marketplace or demo_marketplace
    if effective_marketplace != demo_marketplace:
        return

    if _demo_summary_has_rows(schema, demo_marketplace):
        return

    now_ts = time.time()
    with _demo_seed_lock:
        if _demo_summary_has_rows(schema, demo_marketplace):
            return

        if now_ts - _demo_seed_last_run.get(schema, 0) < 10:
            return
        _demo_seed_last_run[schema] = now_ts

        logger.info(
            "Demo schema %s is empty for marketplace=%s, running auto seed + marts",
            schema,
            demo_marketplace,
        )

        if not _table_exists(schema, "dim_product"):
            _run_cli_command(["python3", "-m", "src.cli", "ddl", "--schema", schema])

        seed_command = (
            ["python3", "-m", "src.cli", "seed-demo-rich"]
            if demo_marketplace == "ozon"
            else ["python3", "-m", "src.cli", "seed-demo-wb-rich"]
        )
        seed_command.extend(
            [
                "--schema",
                schema,
                "--from",
                DEMO_PERIOD_START,
                "--to",
                DEMO_PERIOD_END,
            ]
        )

        marts_command = [
            "python3",
            "-m",
            "src.cli",
            "marts",
            "--schema",
            schema,
            "--from",
            DEMO_PERIOD_START,
            "--to",
            DEMO_PERIOD_END,
        ]

        _run_cli_command(seed_command)
        _run_cli_command(marts_command)

        if not _demo_summary_has_rows(schema, demo_marketplace):
            logger.warning(
                "Auto demo build finished but %s.mart_store_summary_30d is still empty for marketplace=%s",
                schema,
                demo_marketplace,
            )


app = FastAPI(title="Marketplace Profit Control API", version="v1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3002",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _safe_ratio(numerator: Decimal | float | int, denominator: Decimal | float | int) -> float | None:
    den = _as_decimal(denominator)
    if den <= 0:
        return None
    num = _as_decimal(numerator)
    ratio = num / den
    if ratio < 0:
        return 0.0
    if ratio > 1:
        return 1.0
    return float(ratio)


def _top_problem_family(rule_code: str | None) -> str:
    if not rule_code:
        return "general"
    if rule_code == "stockout_risk":
        return "inventory"
    if rule_code in {"ads_eating_profit", "ads_without_sales", "negative_profit_after_ads"}:
        return "ads"
    if rule_code == "high_logistics_share":
        return "logistics"
    if rule_code == "low_margin_after_ads":
        return "margin"
    return "general"


def _profit_confidence_code(
    base_confidence: str | None,
    expenses_coverage_ratio: float | None,
    ads_coverage_ratio: float | None,
    cost_input_coverage_ratio: float | None,
    fallback_cogs_ratio: float | None,
) -> str:
    if (
        base_confidence == "low"
        or (expenses_coverage_ratio is not None and expenses_coverage_ratio < 0.55)
        or (ads_coverage_ratio is not None and ads_coverage_ratio < 0.40)
        or (cost_input_coverage_ratio is not None and cost_input_coverage_ratio < 0.35)
    ):
        return "preliminary"
    if (
        base_confidence == "medium"
        or (expenses_coverage_ratio is not None and expenses_coverage_ratio < 0.80)
        or (cost_input_coverage_ratio is not None and cost_input_coverage_ratio < 0.70)
        or (fallback_cogs_ratio is not None and fallback_cogs_ratio > 0.35)
    ):
        return "estimated"
    return "reliable"


def _profit_confidence_label(code: str) -> str:
    labels = {
        "reliable": "Надежно",
        "estimated": "Ориентировочно",
        "preliminary": "Предварительно",
    }
    return labels.get(code, "Предварительно")


def _expenses_quality_label(expenses_coverage_ratio: float | None) -> str:
    if expenses_coverage_ratio is None:
        return "Данные по расходам неполные"
    if expenses_coverage_ratio >= 0.85:
        return "Расходы в основном разнесены"
    if expenses_coverage_ratio >= 0.60:
        return "Часть расходов распределена приблизительно"
    return "Значительная часть расходов пока не разнесена"


def _cost_quality_label(cost_input_coverage_ratio: float | None, fallback_cogs_ratio: float | None) -> str:
    if cost_input_coverage_ratio is None:
        return "Себестоимость не заполнена"
    if cost_input_coverage_ratio >= 0.80 and (fallback_cogs_ratio or 0.0) <= 0.2:
        return "Себестоимость заполнена"
    if (fallback_cogs_ratio or 0.0) > 0.5:
        return "Используется базовая оценка себестоимости"
    if (fallback_cogs_ratio or 0.0) > 0.0:
        return "Используется примерная себестоимость по категории"
    return "Себестоимость заполнена частично"


def _resolve_ui_snapshot_context(
    resolved_schema: str,
    resolved_marketplace: str | None,
) -> dict[str, Any] | None:
    if resolved_marketplace is None:
        return None
    if not _table_exists(resolved_schema, "ingestion_snapshot_state"):
        return None

    latest = fetch_one(
        f"""
        SELECT
          snapshot_id,
          marketplace,
          period_from,
          period_to,
          publish_status,
          updated_at
        FROM {resolved_schema}.ingestion_snapshot_state
        WHERE marketplace = %s
        ORDER BY updated_at DESC, created_at DESC, snapshot_id DESC
        LIMIT 1
        """,
        (resolved_marketplace,),
    )
    if latest is None:
        return None

    published = fetch_one(
        f"""
        SELECT
          snapshot_id,
          marketplace,
          period_from,
          period_to,
          publish_status,
          published_at,
          updated_at
        FROM {resolved_schema}.ingestion_snapshot_state
        WHERE marketplace = %s
          AND publish_status = 'published'
        ORDER BY published_at DESC NULLS LAST, updated_at DESC, snapshot_id DESC
        LIMIT 1
        """,
        (resolved_marketplace,),
    )

    selected = published or latest
    latest_updated = latest.get("updated_at")
    selected_updated = selected.get("updated_at")
    is_last_good_fallback = bool(
        published
        and latest.get("snapshot_id") != selected.get("snapshot_id")
        and latest_updated
        and selected_updated
        and latest_updated >= selected_updated
    )
    return {
        "snapshot_id": selected.get("snapshot_id"),
        "marketplace": selected.get("marketplace"),
        "period_start": selected.get("period_from"),
        "period_end": selected.get("period_to"),
        "publish_status": selected.get("publish_status"),
        "usable_for_ui": selected.get("publish_status") == "published",
        "is_last_good_fallback": is_last_good_fallback,
        "latest_snapshot_publish_status": latest.get("publish_status"),
        "latest_snapshot_id": latest.get("snapshot_id"),
    }


def _fetch_store_summary_row(resolved_schema: str, resolved_marketplace: str | None) -> dict[str, Any] | None:
    snapshot_ctx = _resolve_ui_snapshot_context(resolved_schema, resolved_marketplace)
    where_clauses: list[str] = []
    params: list[Any] = []
    if resolved_marketplace:
        where_clauses.append("marketplace = %s")
        params.append(resolved_marketplace)
    if snapshot_ctx and snapshot_ctx.get("period_start") and snapshot_ctx.get("period_end"):
        where_clauses.append("period_start = %s")
        where_clauses.append("period_end = %s")
        params.extend([snapshot_ctx["period_start"], snapshot_ctx["period_end"]])

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    row = fetch_one(
        f"""
        SELECT
          run_date,
          period_start,
          period_end,
          marketplace,
          sku_count,
          units_sold_total,
          revenue_total,
          ads_spend_total,
          logistics_total,
          profit_before_ads_total,
          profit_after_ads_total,
          margin_after_ads_total,
          loss_sku_count,
          sku_with_ads_count,
          sku_with_stockout_risk_count,
          top_problem_rule_code,
          profit_growth_potential_30d,
          revenue_at_risk_30d,
          unattributed_cost_total,
          fallback_attributed_cost_total,
          attribution_quality,
          confidence,
          quality_disclaimer,
          store_health_status,
          store_health_message
        FROM {resolved_schema}.mart_store_summary_30d
        {where_sql}
        ORDER BY period_end DESC NULLS LAST, run_date DESC NULLS LAST
        LIMIT 1
        """,
        tuple(params),
    )
    if row is None and snapshot_ctx is not None:
        # fallback: old schema/period data may exist only as latest mart row
        fallback_sql = "WHERE marketplace = %s" if resolved_marketplace else ""
        fallback_params: tuple[Any, ...] = (resolved_marketplace,) if resolved_marketplace else ()
        row = fetch_one(
            f"""
            SELECT
              run_date,
              period_start,
              period_end,
              marketplace,
              sku_count,
              units_sold_total,
              revenue_total,
              ads_spend_total,
              logistics_total,
              profit_before_ads_total,
              profit_after_ads_total,
              margin_after_ads_total,
              loss_sku_count,
              sku_with_ads_count,
              sku_with_stockout_risk_count,
              top_problem_rule_code,
              profit_growth_potential_30d,
              revenue_at_risk_30d,
              unattributed_cost_total,
              fallback_attributed_cost_total,
              attribution_quality,
              confidence,
              quality_disclaimer,
              store_health_status,
              store_health_message
            FROM {resolved_schema}.mart_store_summary_30d
            {fallback_sql}
            ORDER BY period_end DESC NULLS LAST, run_date DESC NULLS LAST
            LIMIT 1
            """,
            fallback_params,
        )
    if row is None:
        return None

    if snapshot_ctx:
        row["snapshot_id"] = snapshot_ctx.get("snapshot_id")
        row["snapshot_publish_status"] = snapshot_ctx.get("publish_status")
        row["snapshot_usable_for_ui"] = bool(snapshot_ctx.get("usable_for_ui"))
        row["snapshot_is_last_good_fallback"] = bool(snapshot_ctx.get("is_last_good_fallback"))
        row["latest_snapshot_publish_status"] = snapshot_ctx.get("latest_snapshot_publish_status")
    else:
        row["snapshot_id"] = None
        row["snapshot_publish_status"] = "unknown"
        row["snapshot_usable_for_ui"] = False
        row["snapshot_is_last_good_fallback"] = False
        row["latest_snapshot_publish_status"] = None

    period_start = row["period_start"]
    period_end = row["period_end"]
    marketplace = row["marketplace"]

    pnl_stats = fetch_one(
        f"""
        SELECT
          COUNT(*)::int AS sku_count_in_pnl,
          COUNT(*) FILTER (WHERE COALESCE(units_sold, 0) > 0)::int AS sold_sku_count,
          COALESCE(SUM(units_sold), 0)::numeric(18,2) AS total_units_sold,
          AVG(CASE WHEN COALESCE(units_sold, 0) > 0 THEN revenue_gross / NULLIF(units_sold, 0) END)::numeric(18,4) AS avg_selling_price,
          PERCENTILE_CONT(0.5) WITHIN GROUP (
            ORDER BY CASE WHEN COALESCE(units_sold, 0) > 0 THEN revenue_gross / NULLIF(units_sold, 0) END
          )::numeric(18,4) AS median_selling_price,
          AVG(profit_after_ads)::numeric(18,4) AS avg_profit_per_sku,
          AVG(margin_after_ads)::numeric(18,6) AS avg_margin_per_sku,
          PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY margin_after_ads)::numeric(18,6) AS median_margin_per_sku,
          COUNT(*) FILTER (WHERE COALESCE(cogs_amount, 0) > 0)::int AS sku_with_cogs_count,
          COUNT(*) FILTER (WHERE COALESCE(ads_spend, 0) > 0)::int AS sku_with_ads_fact_count,
          COALESCE(SUM(COALESCE(commission_amount, 0) + COALESCE(logistics_amount, 0) + COALESCE(marketplace_other_fees_amount, 0) + COALESCE(cogs_amount, 0) + COALESCE(ads_spend, 0)), 0)::numeric(18,2) AS expenses_total
        FROM {resolved_schema}.mart_pnl_30d_by_product
        WHERE period_start = %s
          AND period_end = %s
          AND marketplace = %s
        """,
        (period_start, period_end, marketplace),
    ) or {}

    inventory_stats = fetch_one(
        f"""
        SELECT
          COUNT(DISTINCT offer_id)::int AS sku_with_stock_count,
          COUNT(DISTINCT offer_id) FILTER (
            WHERE COALESCE(stock_available, 0) > 0
              AND COALESCE(avg_daily_units_sold_30d, 0) <= 0
          )::int AS sku_without_sales_count
        FROM {resolved_schema}.mart_inventory_snapshot
        WHERE marketplace = %s
        """,
        (marketplace,),
    ) or {}

    cogs_stats = fetch_one(
        f"""
        SELECT
          COUNT(*) FILTER (WHERE money_bucket = 'cogs')::int AS cogs_rows_total,
          COUNT(*) FILTER (
            WHERE money_bucket = 'cogs'
              AND COALESCE(cogs_source, '') IN ('category_default', 'global_default')
          )::int AS cogs_rows_fallback
        FROM {resolved_schema}.fact_product_money_attribution
        WHERE marketplace = %s
          AND event_date >= %s
          AND event_date <= %s
        """,
        (marketplace, period_start, period_end),
    ) or {}

    allocation_stats: dict[str, Any] = {}
    if _table_exists(resolved_schema, "fact_unmatched_money"):
        allocation_stats = fetch_one(
            f"""
            WITH expenses AS (
              SELECT
                SUM(CASE WHEN canonical_product_key <> '~' THEN COALESCE(amount, 0) ELSE 0 END)::numeric(18,2) AS product_allocated_amount,
                SUM(CASE WHEN canonical_product_key = '~' THEN COALESCE(amount, 0) ELSE 0 END)::numeric(18,2) AS non_product_allocated_amount
              FROM {resolved_schema}.fact_product_money_attribution
              WHERE marketplace = %s
                AND event_date >= %s
                AND event_date <= %s
                AND money_component_type = 'expense'
            )
            SELECT
              e.product_allocated_amount,
              e.non_product_allocated_amount,
              COALESCE(SUM(CASE WHEN u.match_status = 'unmatched_explainable' THEN COALESCE(u.amount, 0) ELSE 0 END), 0)::numeric(18,2) AS unmatched_explainable_amount,
              COALESCE(SUM(CASE WHEN u.match_status = 'unmatched_unclassified' THEN COALESCE(u.amount, 0) ELSE 0 END), 0)::numeric(18,2) AS unmatched_unclassified_amount,
              COALESCE(SUM(CASE WHEN u.match_status = 'ambiguous' THEN COALESCE(u.amount, 0) ELSE 0 END), 0)::numeric(18,2) AS ambiguous_amount
            FROM expenses e
            LEFT JOIN {resolved_schema}.fact_unmatched_money u
              ON u.marketplace = %s
             AND u.event_date >= %s
             AND u.event_date <= %s
            GROUP BY e.product_allocated_amount, e.non_product_allocated_amount
            """,
            (marketplace, period_start, period_end, marketplace, period_start, period_end),
        ) or {}

    sku_count = int(row.get("sku_count") or pnl_stats.get("sku_count_in_pnl") or 0)
    total_units_sold = _as_decimal(pnl_stats.get("total_units_sold") or row.get("units_sold_total") or 0)
    avg_selling_price = pnl_stats.get("avg_selling_price")
    median_selling_price = pnl_stats.get("median_selling_price")
    avg_profit_per_sku = pnl_stats.get("avg_profit_per_sku")
    avg_margin_per_sku = pnl_stats.get("avg_margin_per_sku")
    median_margin_per_sku = pnl_stats.get("median_margin_per_sku")
    cost_input_coverage_ratio = _safe_ratio(pnl_stats.get("sku_with_cogs_count", 0), sku_count)
    ads_coverage_ratio = _safe_ratio(pnl_stats.get("sku_with_ads_fact_count", 0), sku_count)
    fallback_cogs_ratio = _safe_ratio(
        cogs_stats.get("cogs_rows_fallback", 0),
        cogs_stats.get("cogs_rows_total", 0),
    )
    expenses_coverage_ratio = _safe_ratio(
        _as_decimal(pnl_stats.get("expenses_total", 0)) - _as_decimal(row.get("unattributed_cost_total", 0)),
        _as_decimal(pnl_stats.get("expenses_total", 0)),
    )
    profit_confidence = _profit_confidence_code(
        base_confidence=row.get("confidence"),
        expenses_coverage_ratio=expenses_coverage_ratio,
        ads_coverage_ratio=ads_coverage_ratio,
        cost_input_coverage_ratio=cost_input_coverage_ratio,
        fallback_cogs_ratio=fallback_cogs_ratio,
    )

    row["total_units_sold"] = total_units_sold
    row["avg_selling_price"] = avg_selling_price
    row["median_selling_price"] = median_selling_price
    row["avg_profit_per_sku"] = avg_profit_per_sku
    row["avg_margin_per_sku"] = avg_margin_per_sku
    row["median_margin_per_sku"] = median_margin_per_sku
    row["sold_sku_count"] = pnl_stats.get("sold_sku_count", 0)
    row["sku_with_stock_count"] = inventory_stats.get("sku_with_stock_count", 0)
    row["sku_without_sales_count"] = inventory_stats.get("sku_without_sales_count", 0)
    row["expenses_coverage_ratio"] = expenses_coverage_ratio
    row["ads_coverage_ratio"] = ads_coverage_ratio
    row["cost_input_coverage_ratio"] = cost_input_coverage_ratio
    row["fallback_cogs_ratio"] = fallback_cogs_ratio
    row["unattributed_money_total"] = row.get("unattributed_cost_total", 0)
    row["profit_confidence"] = profit_confidence
    row["profit_confidence_label"] = _profit_confidence_label(profit_confidence)
    row["expenses_quality_label"] = _expenses_quality_label(expenses_coverage_ratio)
    row["cost_quality_label"] = _cost_quality_label(cost_input_coverage_ratio, fallback_cogs_ratio)
    row["top_problem_family"] = _top_problem_family(row.get("top_problem_rule_code"))
    row["product_allocated_amount"] = allocation_stats.get("product_allocated_amount", 0)
    row["non_product_allocated_amount"] = allocation_stats.get("non_product_allocated_amount", 0)
    row["unmatched_amount"] = allocation_stats.get("non_product_allocated_amount", 0)
    row["unmatched_explainable_amount"] = allocation_stats.get("unmatched_explainable_amount", 0)
    row["unmatched_unclassified_amount"] = allocation_stats.get("unmatched_unclassified_amount", 0)
    row["ambiguous_amount"] = allocation_stats.get("ambiguous_amount", 0)
    return row


@app.get("/api/store-summary")
def get_store_summary(
    schema: str | None = Query(None),
    marketplace: str | None = Query(None, pattern="^(ozon|wb)$"),
) -> dict[str, Any]:
    resolved_schema = resolve_schema(schema)
    resolved_marketplace = resolve_marketplace(marketplace)
    ensure_demo_mode_data(resolved_schema, resolved_marketplace)
    row = _fetch_store_summary_row(resolved_schema, resolved_marketplace)
    if row is None:
        return {}
    return normalize_json(row)


@app.get("/api/actions")
def get_actions(
    schema: str | None = Query(None),
    marketplace: str | None = Query(None, pattern="^(ozon|wb)$"),
    limit: int = Query(100, ge=1, le=500),
    top_only: bool = Query(False),
) -> list[dict[str, Any]]:
    resolved_schema = resolve_schema(schema)
    resolved_marketplace = resolve_marketplace(marketplace)
    ensure_demo_mode_data(resolved_schema, resolved_marketplace)
    summary = _fetch_store_summary_row(resolved_schema, resolved_marketplace)
    if summary is None:
        return []

    period_start = summary["period_start"]
    period_end = summary["period_end"]
    summary_marketplace = summary["marketplace"]
    effective_limit = 3 if top_only else limit
    params: list[Any] = [period_start, period_end, summary_marketplace, effective_limit]
    rows = fetch_all(
        f"""
        SELECT
          q.run_date,
          q.period_start,
          q.period_end,
          q.marketplace,
          q.offer_id,
          q.product_name,
          q.action_code,
          q.action_type,
          q.primary_rule_code,
          q.action_title,
          q.first_step,
          q.action_window_days,
          q.attribution_quality,
          q.confidence,
          p.revenue_gross,
          p.profit_after_ads,
          p.ads_spend,
          p.margin_after_ads,
          q.money_impact_30d,
          q.revenue_at_risk_30d,
          q.estimated_profit_uplift_30d,
          q.estimated_revenue_protection_30d,
          q.estimated_gain_if_fixed,
          q.days_of_stock_30d,
          q.daily_revenue_run_rate,
          q.daily_profit_run_rate,
          q.revenue_at_risk_per_day,
          q.revenue_at_risk_3d,
          q.revenue_at_risk_7d,
          q.revenue_at_risk_14d,
          q.profit_at_risk_per_day,
          q.expected_replenishment_date,
          q.planned_replenishment_qty,
          q.replenishment_status,
          q.stockout_effect_confidence,
          q.action_priority,
          q.action_priority_score,
          q.action_group_size,
          q.action_group_rank,
          q.batch_candidate,
          q.batch_hint,
          q.execution_lane
        FROM {resolved_schema}.mart_action_queue_14d q
        LEFT JOIN {resolved_schema}.mart_pnl_30d_by_product p
          ON p.offer_id = q.offer_id
         AND p.period_start = q.period_start
         AND p.period_end = q.period_end
         AND p.marketplace = q.marketplace
        WHERE q.period_start = %s
          AND q.period_end = %s
          AND q.marketplace = %s
        ORDER BY q.action_priority ASC, q.action_priority_score DESC, q.offer_id
        LIMIT %s
        """,
        tuple(params),
    )
    enriched = [enrich_action_row(row) for row in rows]
    return normalize_json(enriched)


@app.get("/api/products")
def get_products(
    schema: str | None = Query(None),
    marketplace: str | None = Query(None, pattern="^(ozon|wb)$"),
    filter: str | None = Query(None, pattern="^(ads|logistics|margin|stockout)$"),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    resolved_schema = resolve_schema(schema)
    resolved_marketplace = resolve_marketplace(marketplace)
    ensure_demo_mode_data(resolved_schema, resolved_marketplace)
    summary = _fetch_store_summary_row(resolved_schema, resolved_marketplace)
    if summary is None:
        return []
    period_start = summary["period_start"]
    period_end = summary["period_end"]
    summary_marketplace = summary["marketplace"]

    where_clauses: list[str] = []
    params: list[Any] = [period_start, period_end, summary_marketplace]

    if filter == "ads":
        where_clauses.append("(q.action_type = 'ads' OR r.rule_codes && %s)")
        params.append(list(ADS_RULE_CODES))
    elif filter == "logistics":
        where_clauses.append("q.action_type = 'logistics'")
    elif filter == "margin":
        where_clauses.append("q.action_type = 'margin'")
    elif filter == "stockout":
        where_clauses.append("q.action_type = 'inventory'")
    search_term = (q or "").strip()
    if search_term:
        where_clauses.append("(LOWER(COALESCE(p.product_name, '')) LIKE %s OR LOWER(COALESCE(p.offer_id, '')) LIKE %s)")
        search_value = f"%{search_term.lower()}%"
        params.extend([search_value, search_value])

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    params.append(limit)

    rows = fetch_all(
        f"""
        WITH latest_period AS (
          SELECT %s::date AS period_start, %s::date AS period_end, %s::text AS marketplace
        ),
        rule_agg AS (
          SELECT
            h.offer_id,
            ARRAY_AGG(DISTINCT h.rule_code) AS rule_codes,
            STRING_AGG(DISTINCT h.rule_code, ', ' ORDER BY h.rule_code) AS rule_code
          FROM {resolved_schema}.mart_rule_hits_30d h
          JOIN latest_period lp
            ON h.period_start = lp.period_start
           AND h.period_end = lp.period_end
           AND h.marketplace = lp.marketplace
          GROUP BY h.offer_id
        ),
        queue AS (
          SELECT
            q.offer_id,
            q.primary_rule_code,
            q.action_code,
            q.action_type,
            q.estimated_gain_if_fixed
          FROM {resolved_schema}.mart_action_queue_14d q
          JOIN latest_period lp
            ON q.period_start = lp.period_start
           AND q.period_end = lp.period_end
           AND q.marketplace = lp.marketplace
        )
        SELECT
          p.marketplace,
          p.offer_id,
          p.product_name,
          p.revenue_gross,
          p.profit_after_ads,
          p.ads_spend,
          p.logistics_amount,
          p.margin_after_ads,
          r.rule_code,
          q.primary_rule_code,
          q.action_code,
          q.action_type,
          q.estimated_gain_if_fixed
        FROM {resolved_schema}.mart_pnl_30d_by_product p
        JOIN latest_period lp
          ON p.period_start = lp.period_start
         AND p.period_end = lp.period_end
         AND p.marketplace = lp.marketplace
        LEFT JOIN rule_agg r
          ON r.offer_id = p.offer_id
        LEFT JOIN queue q
          ON q.offer_id = p.offer_id
        {where_sql}
        ORDER BY p.profit_after_ads ASC NULLS LAST, p.revenue_gross DESC NULLS LAST
        LIMIT %s
        """,
        tuple(params),
    )
    enriched = [enrich_product_row(row) for row in rows]
    return normalize_json(enriched)


@app.get("/api/playbook/{offer_id}")
def get_playbook(
    offer_id: str,
    schema: str | None = Query(None),
    marketplace: str | None = Query(None, pattern="^(ozon|wb)$"),
) -> dict[str, Any]:
    resolved_schema = resolve_schema(schema)
    resolved_marketplace = resolve_marketplace(marketplace)
    ensure_demo_mode_data(resolved_schema, resolved_marketplace)
    summary = _fetch_store_summary_row(resolved_schema, resolved_marketplace)
    where_sql = "WHERE pb.offer_id = %s"
    params: list[Any] = [offer_id]
    if summary is not None:
        where_sql += " AND pb.period_start = %s AND pb.period_end = %s AND pb.marketplace = %s"
        params.extend([summary["period_start"], summary["period_end"], summary["marketplace"]])
    elif resolved_marketplace:
        where_sql += " AND pb.marketplace = %s"
        params.append(resolved_marketplace)
    row = fetch_one(
        f"""
        SELECT
          pb.run_date,
          pb.period_start,
          pb.period_end,
          pb.marketplace,
          pb.offer_id,
          pb.product_name,
          pb.primary_rule_code,
          pb.action_code,
          pb.playbook_title,
          pb.problem_summary,
          pb.first_step,
          pb.action_intensity_hint,
          pb.action_window_days,
          pb.attribution_quality,
          pb.confidence,
          pb.estimated_profit_uplift_30d,
          pb.estimated_revenue_protection_30d,
          pb.action_rationale,
          pb.why_this_window,
          pb.check_after_window,
          pb.not_improved_criteria,
          pb.diagnostic_priority_1,
          pb.diagnostic_priority_2,
          pb.diagnostic_priority_3,
          pb.decision_path_summary,
          pb.if_not_improved,
          pb.success_signal,
          pb.estimated_gain_if_fixed,
          pb.days_of_stock_30d,
          pb.daily_revenue_run_rate,
          pb.daily_profit_run_rate,
          pb.revenue_at_risk_per_day,
          pb.revenue_at_risk_3d,
          pb.revenue_at_risk_7d,
          pb.revenue_at_risk_14d,
          pb.profit_at_risk_per_day,
          pb.expected_replenishment_date,
          pb.planned_replenishment_qty,
          pb.replenishment_status,
          pb.stockout_effect_confidence,
          p.revenue_gross,
          p.profit_after_ads,
          p.ads_spend,
          p.margin_after_ads
        FROM {resolved_schema}.mart_action_playbook_14d pb
        LEFT JOIN {resolved_schema}.mart_pnl_30d_by_product p
          ON p.offer_id = pb.offer_id
         AND p.period_start = pb.period_start
         AND p.period_end = pb.period_end
         AND p.marketplace = pb.marketplace
        {where_sql}
        ORDER BY pb.period_end DESC NULLS LAST, pb.run_date DESC NULLS LAST
        LIMIT 1
        """,
        tuple(params),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Playbook not found for offer_id")
    enriched = enrich_playbook_row(row)
    return normalize_json(enriched)


@app.get("/api/data-quality")
def get_data_quality(
    schema: str | None = Query(None),
    marketplace: str | None = Query(None, pattern="^(ozon|wb)$"),
) -> dict[str, Any]:
    resolved_schema = resolve_schema(schema)
    resolved_marketplace = resolve_marketplace(marketplace)
    ensure_demo_mode_data(resolved_schema, resolved_marketplace)
    summary = _fetch_store_summary_row(resolved_schema, resolved_marketplace)
    if summary is None:
        return {}
    where_sql = "WHERE marketplace = %s AND period_start = %s AND period_end = %s"
    params: tuple[Any, ...] = (
        summary["marketplace"],
        summary["period_start"],
        summary["period_end"],
    )
    row = fetch_one(
        f"""
        SELECT
          run_date,
          period_start,
          period_end,
          marketplace,
          negative_revenue_sku_count,
          negative_revenue_note,
          zero_revenue_with_sales_sku_count,
          zero_revenue_note,
          negative_logistics_sku_count,
          negative_logistics_note,
          stock_without_sales_sku_count,
          stock_without_sales_note,
          duplicate_posting_count,
          duplicate_posting_note,
          unattributed_row_count,
          unattributed_cost_total,
          unattributed_note,
          fallback_attributed_row_count,
          fallback_attributed_cost_total,
          fallback_attributed_note,
          notes
        FROM {resolved_schema}.mart_data_quality_30d
        {where_sql}
        ORDER BY run_date DESC NULLS LAST
        LIMIT 1
        """,
        params,
    )
    if row is None:
        return {}
    return normalize_json(row)


@app.get("/api/rule-map")
def get_rule_map(
    schema: str | None = Query(None),
    marketplace: str | None = Query(None, pattern="^(ozon|wb)$"),
) -> list[dict[str, Any]]:
    resolved_schema = resolve_schema(schema)
    resolved_marketplace = resolve_marketplace(marketplace)
    ensure_demo_mode_data(resolved_schema, resolved_marketplace)
    summary = _fetch_store_summary_row(resolved_schema, resolved_marketplace)
    if summary is None:
        return []
    params: tuple[Any, ...] = (
        summary["period_start"],
        summary["period_end"],
        summary["marketplace"],
    )
    rows = fetch_all(
        f"""
        WITH latest_period AS (
          SELECT %s::date AS period_start, %s::date AS period_end, %s::text AS marketplace
        )
        SELECT
          h.marketplace,
          h.rule_code,
          COUNT(*)::int AS hit_count
        FROM {resolved_schema}.mart_rule_hits_30d h
        JOIN latest_period lp
          ON h.period_start = lp.period_start
         AND h.period_end = lp.period_end
         AND h.marketplace = lp.marketplace
        GROUP BY h.marketplace, h.rule_code
        ORDER BY hit_count DESC, h.rule_code
        """,
        params,
    )
    return normalize_json(rows)


@app.get("/api/product-extremes")
def get_product_extremes(
    schema: str | None = Query(None),
    marketplace: str | None = Query(None, pattern="^(ozon|wb)$"),
    limit: int = Query(5, ge=1, le=20),
) -> dict[str, Any]:
    resolved_schema = resolve_schema(schema)
    resolved_marketplace = resolve_marketplace(marketplace)
    ensure_demo_mode_data(resolved_schema, resolved_marketplace)
    summary = _fetch_store_summary_row(resolved_schema, resolved_marketplace)
    if summary is None:
        return {"top_profitable": [], "worst_by_profit": []}

    period_params = (
        summary["period_start"],
        summary["period_end"],
        summary["marketplace"],
        limit,
    )
    top_profitable = fetch_all(
        f"""
        SELECT
          marketplace,
          offer_id,
          product_name,
          revenue_gross,
          profit_after_ads,
          margin_after_ads
        FROM {resolved_schema}.mart_pnl_30d_by_product
        WHERE period_start = %s
          AND period_end = %s
          AND marketplace = %s
          AND offer_id <> '~'
        ORDER BY profit_after_ads DESC NULLS LAST, revenue_gross DESC NULLS LAST, offer_id
        LIMIT %s
        """,
        period_params,
    )
    worst_by_profit = fetch_all(
        f"""
        SELECT
          marketplace,
          offer_id,
          product_name,
          revenue_gross,
          profit_after_ads,
          margin_after_ads
        FROM {resolved_schema}.mart_pnl_30d_by_product
        WHERE period_start = %s
          AND period_end = %s
          AND marketplace = %s
          AND offer_id <> '~'
        ORDER BY profit_after_ads ASC NULLS FIRST, revenue_gross DESC NULLS LAST, offer_id
        LIMIT %s
        """,
        period_params,
    )

    return normalize_json(
        {
            "top_profitable": top_profitable,
            "worst_by_profit": worst_by_profit,
        }
    )


@app.get("/api/store-overview")
def get_store_overview(
    schema: str | None = Query(None),
    marketplace: str | None = Query(None, pattern="^(ozon|wb)$"),
) -> dict[str, Any]:
    resolved_schema = resolve_schema(schema)
    resolved_marketplace = resolve_marketplace(marketplace)
    ensure_demo_mode_data(resolved_schema, resolved_marketplace)
    summary = _fetch_store_summary_row(resolved_schema, resolved_marketplace)
    if summary is None:
        return {"daily": [], "cost_structure": []}

    period_start = summary["period_start"]
    period_end = summary["period_end"]
    summary_marketplace = summary["marketplace"]

    daily_pnl = fetch_all(
        f"""
        SELECT
          d.date,
          SUM(COALESCE(d.revenue_gross, 0)) AS revenue_total,
          SUM(COALESCE(d.profit_after_ads, 0)) AS profit_after_ads_total
        FROM {resolved_schema}.mart_pnl_daily_by_product d
        WHERE d.date >= %s
          AND d.date <= %s
          AND d.marketplace = %s
        GROUP BY d.date
        ORDER BY d.date
        """,
        (period_start, period_end, summary_marketplace),
    )

    daily_ads = fetch_all(
        f"""
        SELECT
          a.date,
          SUM(COALESCE(a.spend, 0)) AS ads_spend_total
        FROM {resolved_schema}.mart_ads_daily a
        WHERE a.date >= %s
          AND a.date <= %s
          AND a.marketplace = %s
        GROUP BY a.date
        ORDER BY a.date
        """,
        (period_start, period_end, summary_marketplace),
    )
    pnl_by_date = {
        row["date"]: {
            "revenue_total": row["revenue_total"],
            "profit_after_ads_total": row["profit_after_ads_total"],
        }
        for row in daily_pnl
    }
    ads_by_date = {row["date"]: row["ads_spend_total"] for row in daily_ads}

    daily: list[dict[str, Any]] = []
    current_date = period_start
    while current_date <= period_end:
        pnl_row = pnl_by_date.get(current_date, {})
        daily.append(
            {
                "date": current_date,
                "revenue_total": pnl_row.get("revenue_total", Decimal("0")),
                "profit_after_ads_total": pnl_row.get("profit_after_ads_total", Decimal("0")),
                "ads_spend_total": ads_by_date.get(current_date, Decimal("0")),
            }
        )
        current_date += timedelta(days=1)

    cost_row = fetch_one(
        f"""
        SELECT
          SUM(COALESCE(revenue_gross, 0)) AS revenue_total,
          SUM(COALESCE(returns_amount, 0)) AS returns_total,
          SUM(COALESCE(commission_amount, 0)) AS commission_total,
          SUM(COALESCE(logistics_amount, 0)) AS logistics_total,
          SUM(COALESCE(marketplace_other_fees_amount, 0)) AS other_marketplace_fees_total,
          SUM(COALESCE(cogs_amount, 0)) AS cogs_total,
          SUM(COALESCE(ads_spend, 0)) AS ads_total,
          SUM(COALESCE(profit_before_ads, 0)) AS profit_before_ads_total,
          SUM(COALESCE(profit_after_ads, 0)) AS profit_after_ads_total
        FROM {resolved_schema}.mart_pnl_30d_by_product
        WHERE period_start = %s
          AND period_end = %s
          AND marketplace = %s
        """,
        (period_start, period_end, summary_marketplace),
    ) or {}

    cost_structure = [
        {"name": "Комиссия", "value": cost_row.get("commission_total", Decimal("0"))},
        {"name": "Логистика", "value": cost_row.get("logistics_total", Decimal("0"))},
        {"name": "Прочие расходы маркетплейса", "value": cost_row.get("other_marketplace_fees_total", Decimal("0"))},
        {"name": "Себестоимость", "value": cost_row.get("cogs_total", Decimal("0"))},
        {"name": "Реклама", "value": cost_row.get("ads_total", Decimal("0"))},
    ]
    cost_structure = sorted(cost_structure, key=lambda item: abs(item["value"]), reverse=True)

    financial_breakdown = [
        {"name": "Выручка", "value": cost_row.get("revenue_total", Decimal("0")), "kind": "inflow"},
        {"name": "Возвраты", "value": cost_row.get("returns_total", Decimal("0")), "kind": "expense"},
        {"name": "Комиссии", "value": cost_row.get("commission_total", Decimal("0")), "kind": "expense"},
        {"name": "Логистика", "value": cost_row.get("logistics_total", Decimal("0")), "kind": "expense"},
        {"name": "Прочие расходы маркетплейса", "value": cost_row.get("other_marketplace_fees_total", Decimal("0")), "kind": "expense"},
        {"name": "Реклама", "value": cost_row.get("ads_total", Decimal("0")), "kind": "expense"},
        {"name": "Себестоимость", "value": cost_row.get("cogs_total", Decimal("0")), "kind": "expense"},
        {"name": "Прибыль до рекламы", "value": cost_row.get("profit_before_ads_total", Decimal("0")), "kind": "result"},
        {"name": "Прибыль после рекламы", "value": cost_row.get("profit_after_ads_total", Decimal("0")), "kind": "result"},
    ]

    coverage = {
        "expenses_coverage_ratio": summary.get("expenses_coverage_ratio"),
        "ads_coverage_ratio": summary.get("ads_coverage_ratio"),
        "cost_input_coverage_ratio": summary.get("cost_input_coverage_ratio"),
        "fallback_cogs_ratio": summary.get("fallback_cogs_ratio"),
        "unattributed_money_total": summary.get("unattributed_money_total"),
        "profit_confidence": summary.get("profit_confidence"),
        "profit_confidence_label": summary.get("profit_confidence_label"),
        "expenses_quality_label": summary.get("expenses_quality_label"),
        "cost_quality_label": summary.get("cost_quality_label"),
    }

    return normalize_json(
        {
            "period_start": period_start,
            "period_end": period_end,
            "marketplace": summary_marketplace,
            "daily": daily,
            "cost_structure": cost_structure,
            "financial_breakdown": financial_breakdown,
            "coverage": coverage,
        }
    )


@app.get("/api/internal/data-health")
def get_internal_data_health(
    schema: str | None = Query(None),
    marketplace: str | None = Query(None, pattern="^(ozon|wb)$"),
) -> dict[str, Any]:
    resolved_schema = resolve_schema(schema)
    resolved_marketplace = resolve_marketplace(marketplace)
    ensure_demo_mode_data(resolved_schema, resolved_marketplace)
    summary = _fetch_store_summary_row(resolved_schema, resolved_marketplace)
    if summary is None:
        return {
            "ingestion": [],
            "completeness": [],
            "source_availability": [],
            "attribution": [],
            "unmatched_money": [],
            "layer_reconciliation": [],
            "canonical_audit": [],
            "granularity_audit": [],
            "wb_ads_key_presence": [],
            "costs": {
                "sku_manual_cogs": 0,
                "sku_category_fallback": 0,
                "sku_global_fallback": 0,
                "missing_cogs_coverage": 0,
            },
            "snapshots": [],
        }

    period_start = summary["period_start"]
    period_end = summary["period_end"]
    summary_marketplace = summary["marketplace"]
    run_filters = [
        "r.marketplace = %s",
        "r.date_from = %s",
        "r.date_to = %s",
    ]
    run_params: list[Any] = [summary_marketplace, period_start, period_end]
    run_where_sql = "WHERE " + " AND ".join(run_filters)

    ingestion_rows = fetch_all(
        f"""
        WITH latest_run AS (
          SELECT
            r.*,
            ROW_NUMBER() OVER (
              PARTITION BY r.marketplace, r.source
              ORDER BY r.started_at DESC, r.run_id DESC
            ) AS rn
          FROM {resolved_schema}.ingestion_run r
          {run_where_sql}
        ),
        selected_run AS (
          SELECT *
          FROM latest_run
          WHERE rn = 1
        ),
        latest_chunk_attempt AS (
          SELECT
            c.*,
            regexp_replace(c.chunk_id, '_a[0-9]+$', '') AS chunk_group_id,
            ROW_NUMBER() OVER (
              PARTITION BY c.run_id, regexp_replace(c.chunk_id, '_a[0-9]+$', '')
              ORDER BY COALESCE(c.retry_attempts, 1) DESC, c.created_at DESC, c.chunk_id DESC
            ) AS rn
          FROM {resolved_schema}.ingestion_chunk_audit c
          JOIN selected_run sr
            ON sr.run_id = c.run_id
        ),
        chunk_agg AS (
          SELECT
            c.run_id,
            SUM(COALESCE(c.raw_rows_count, 0))::bigint AS raw_rows,
            SUM(COALESCE(c.normalized_rows_count, 0))::bigint AS normalized_rows,
            SUM(COALESCE(c.loaded_rows_count, 0))::bigint AS loaded_rows,
            SUM(GREATEST(COALESCE(c.retry_attempts, 1) - 1, 0))::bigint AS retries
          FROM latest_chunk_attempt c
          WHERE c.rn = 1
          GROUP BY c.run_id
        )
        SELECT
          sr.source,
          sr.run_id,
          sr.status,
          COALESCE(ch.raw_rows, 0) AS raw_rows,
          COALESCE(ch.normalized_rows, 0) AS normalized_rows,
          COALESCE(ch.loaded_rows, 0) AS loaded_rows,
          COALESCE(ch.retries, 0) AS retries,
          COALESCE(comp.error_type, NULL) AS error_type
        FROM selected_run sr
        LEFT JOIN chunk_agg ch
          ON ch.run_id = sr.run_id
        LEFT JOIN {resolved_schema}.ingestion_completeness comp
          ON comp.run_id = sr.run_id
        ORDER BY sr.source
        """,
        tuple(run_params),
    )

    completeness_rows = fetch_all(
        f"""
        WITH latest_run AS (
          SELECT
            r.*,
            ROW_NUMBER() OVER (
              PARTITION BY r.marketplace, r.source
              ORDER BY r.started_at DESC, r.run_id DESC
            ) AS rn
          FROM {resolved_schema}.ingestion_run r
          {run_where_sql}
        )
        SELECT
          lr.source,
          COALESCE(c.expected_days, 0) AS expected_days,
          COALESCE(c.actual_days, 0) AS actual_days,
          COALESCE(c.missing_days_count, 0) AS missing_days,
          (
            CASE
              WHEN COALESCE(c.coverage_ratio, 0) = 0
                AND COALESCE(c.expected_days, 0) > 0
                AND COALESCE(c.actual_days, 0) > 0
                THEN COALESCE(c.actual_days, 0)::numeric / NULLIF(COALESCE(c.expected_days, 0)::numeric, 0)
              ELSE COALESCE(c.coverage_ratio, 0)
            END
          )::numeric(9,6) AS coverage_ratio,
          COALESCE(c.usable_for_publish, FALSE) AS usable_for_publish,
          COALESCE(c.source_criticality, 'noncritical') AS source_criticality,
          COALESCE(c.status, lr.status) AS status,
          c.error_type,
          COALESCE(c.retry_exhausted, FALSE) AS retry_exhausted,
          c.partial_reason,
          COALESCE(c.partial_reason, c.error_type, '') AS error_reason
        FROM latest_run lr
        LEFT JOIN {resolved_schema}.ingestion_completeness c
          ON c.run_id = lr.run_id
        WHERE lr.rn = 1
        ORDER BY lr.source
        """,
        tuple(run_params),
    )

    attribution_rows = fetch_all(
        f"""
        WITH base AS (
          SELECT
            money_bucket,
            attribution_method,
            canonical_product_key,
            COALESCE(amount, 0)::numeric(18,2) AS amount
          FROM {resolved_schema}.fact_product_money_attribution
          WHERE marketplace = %s
            AND event_date >= %s
            AND event_date <= %s
        ),
        method_agg AS (
          SELECT
            money_bucket,
            attribution_method,
            SUM(amount)::numeric(18,2) AS method_amount
          FROM base
          GROUP BY money_bucket, attribution_method
        ),
        bucket_agg AS (
          SELECT
            money_bucket,
            SUM(amount)::numeric(18,2) AS money_total,
            SUM(CASE WHEN canonical_product_key <> '~' THEN amount ELSE 0 END)::numeric(18,2) AS money_attributed,
            SUM(CASE WHEN canonical_product_key = '~' THEN amount ELSE 0 END)::numeric(18,2) AS money_unattributed
          FROM base
          GROUP BY money_bucket
        )
        SELECT
          b.money_bucket,
          b.money_total,
          b.money_attributed,
          b.money_unattributed,
          COALESCE(
            jsonb_object_agg(m.attribution_method, m.method_amount)
              FILTER (WHERE m.attribution_method IS NOT NULL),
            '{{}}'::jsonb
          ) AS attribution_by_method
        FROM bucket_agg b
        LEFT JOIN method_agg m
          ON m.money_bucket = b.money_bucket
        GROUP BY
          b.money_bucket,
          b.money_total,
          b.money_attributed,
          b.money_unattributed
        ORDER BY b.money_bucket
        """,
        (summary_marketplace, period_start, period_end),
    )

    costs_row = fetch_one(
        f"""
        WITH cogs_rows AS (
          SELECT
            canonical_product_key,
            COALESCE(cogs_source, '') AS cogs_source
          FROM {resolved_schema}.fact_product_money_attribution
          WHERE marketplace = %s
            AND money_bucket = 'cogs'
            AND event_date >= %s
            AND event_date <= %s
        )
        SELECT
          COUNT(DISTINCT canonical_product_key) FILTER (WHERE cogs_source = 'user_input')::int AS sku_manual_cogs,
          COUNT(DISTINCT canonical_product_key) FILTER (WHERE cogs_source = 'category_default')::int AS sku_category_fallback,
          COUNT(DISTINCT canonical_product_key) FILTER (WHERE cogs_source = 'global_default')::int AS sku_global_fallback
        FROM cogs_rows
        """,
        (summary_marketplace, period_start, period_end),
    ) or {}
    missing_cogs_row = fetch_one(
        f"""
        SELECT
          COUNT(*) FILTER (WHERE COALESCE(cogs_amount, 0) = 0)::int AS missing_cogs_coverage
        FROM {resolved_schema}.mart_pnl_30d_by_product
        WHERE period_start = %s
          AND period_end = %s
          AND marketplace = %s
          AND offer_id <> '~'
        """,
        (period_start, period_end, summary_marketplace),
    ) or {}
    costs = {
        "sku_manual_cogs": int(costs_row.get("sku_manual_cogs") or 0),
        "sku_category_fallback": int(costs_row.get("sku_category_fallback") or 0),
        "sku_global_fallback": int(costs_row.get("sku_global_fallback") or 0),
        "missing_cogs_coverage": int(missing_cogs_row.get("missing_cogs_coverage") or 0),
    }

    snapshots = fetch_all(
        f"""
        WITH last_good AS (
          SELECT snapshot_id
          FROM {resolved_schema}.ingestion_snapshot_state
          WHERE marketplace = %s
            AND publish_status = 'published'
          ORDER BY published_at DESC NULLS LAST, updated_at DESC, snapshot_id DESC
          LIMIT 1
        )
        SELECT
          s.snapshot_id,
          s.period_from,
          s.period_to,
          s.publish_status,
          s.published_at,
          (s.snapshot_id = lg.snapshot_id) AS is_last_good,
          COALESCE(
            ARRAY(
              SELECT src
              FROM jsonb_array_elements_text(COALESCE(s.failed_sources, '[]'::jsonb)) AS src
            ),
            ARRAY[]::text[]
          ) AS blocking_sources
        FROM {resolved_schema}.ingestion_snapshot_state s
        LEFT JOIN last_good lg
          ON TRUE
        WHERE s.marketplace = %s
        ORDER BY s.updated_at DESC, s.snapshot_id DESC
        LIMIT 30
        """,
        (summary_marketplace, summary_marketplace),
    )

    unmatched_rows: list[dict[str, Any]] = []
    if _table_exists(resolved_schema, "fact_unmatched_money"):
        unmatched_rows = fetch_all(
            f"""
            SELECT
              money_type,
              match_status,
              incompleteness_stage,
              COUNT(*)::int AS row_count,
              COALESCE(SUM(amount), 0)::numeric(18,2) AS amount_total
            FROM {resolved_schema}.fact_unmatched_money
            WHERE marketplace = %s
              AND event_date >= %s
              AND event_date <= %s
            GROUP BY money_type, match_status, incompleteness_stage
            ORDER BY money_type, match_status, incompleteness_stage
            """,
            (summary_marketplace, period_start, period_end),
        )

    reconciliation_rows: list[dict[str, Any]] = []
    if _table_exists(resolved_schema, "mart_money_layer_reconciliation"):
        reconciliation_rows = fetch_all(
            f"""
            SELECT
              money_type,
              raw_total_signed,
              raw_total_abs,
              stg_total_signed,
              stg_total_abs,
              attribution_total,
              matched_to_product,
              attribution_unmatched,
              mart_total,
              ui_total,
              source_gap,
              parsing_gap,
              match_gap,
              semantic_gap,
              unmatched_explainable_amount,
              unmatched_unclassified_amount,
              ambiguous_amount,
              blocked_by_source_gap,
              unexplained_residual,
              source_coverage_ratio
            FROM {resolved_schema}.mart_money_layer_reconciliation
            WHERE marketplace = %s
            ORDER BY money_type
            """,
            (summary_marketplace,),
        )

    source_availability_rows: list[dict[str, Any]] = []
    if _table_exists(resolved_schema, "mart_source_availability"):
        source_availability_rows = fetch_all(
            f"""
            SELECT
              run_id,
              source_system,
              entity,
              period_start,
              period_end,
              expected_days,
              actual_days,
              missing_days_count,
              coverage_ratio,
              status,
              error_type,
              error_reason,
              request_count,
              success_chunk_count,
              failed_chunk_count,
              rate_limit_chunk_count,
              retry_attempts,
              raw_files_count,
              rows_coverage_ratio,
              request_429_count,
              publish_status,
              usable_for_ui
            FROM {resolved_schema}.mart_source_availability
            WHERE source_system = %s
            ORDER BY entity
            """,
            (summary_marketplace,),
        )

    canonical_audit_rows: list[dict[str, Any]] = []
    if _table_exists(resolved_schema, "mart_raw_vs_stg_canonical_audit"):
        canonical_audit_rows = fetch_all(
            f"""
            SELECT
              source,
              entity,
              money_type,
              raw_field,
              stg_field,
              field_role,
              granularity,
              sign_rule,
              raw_total,
              stg_total,
              drift,
              source_coverage_ratio,
              drift_reason,
              status
            FROM {resolved_schema}.mart_raw_vs_stg_canonical_audit
            WHERE marketplace = %s
            ORDER BY source, entity, money_type, stg_field
            """,
            (summary_marketplace,),
        )

    granularity_rows: list[dict[str, Any]] = []
    if _table_exists(resolved_schema, "mart_granularity_reconciliation_report"):
        granularity_rows = fetch_all(
            f"""
            SELECT
              source,
              entity,
              expected_grain,
              raw_rows_proxy,
              stg_rows,
              stg_distinct_grain,
              attribution_rows,
              mart_rows,
              row_drift_raw_to_stg,
              row_drift_stg_to_grain,
              source_coverage_ratio,
              missing_days_count,
              partial_reason,
              error_type,
              status
            FROM {resolved_schema}.mart_granularity_reconciliation_report
            WHERE marketplace = %s
            ORDER BY source, entity
            """,
            (summary_marketplace,),
        )

    wb_ads_key_presence_rows: list[dict[str, Any]] = []
    if summary_marketplace == "wb" and _table_exists(resolved_schema, "mart_wb_ads_key_presence_audit"):
        wb_ads_key_presence_rows = fetch_all(
            f"""
            SELECT
              date,
              scope,
              is_total,
              rows_total,
              spend_total,
              rows_with_nm_id,
              spend_with_nm_id,
              rows_with_offer_id,
              spend_with_offer_id,
              rows_with_vendor_code,
              rows_with_supplier_article,
              rows_with_any_key,
              rows_without_keys,
              key_presence_ratio,
              status
            FROM {resolved_schema}.mart_wb_ads_key_presence_audit
            ORDER BY scope, is_total DESC, date
            """
        )

    return normalize_json(
        {
            "ingestion": ingestion_rows,
            "completeness": completeness_rows,
            "source_availability": source_availability_rows,
            "attribution": attribution_rows,
            "unmatched_money": unmatched_rows,
            "layer_reconciliation": reconciliation_rows,
            "canonical_audit": canonical_audit_rows,
            "granularity_audit": granularity_rows,
            "wb_ads_key_presence": wb_ads_key_presence_rows,
            "costs": costs,
            "snapshots": snapshots,
        }
    )


@app.get("/api/internal/unmatched-money")
def get_internal_unmatched_money(
    schema: str | None = Query(None),
    marketplace: str | None = Query(None, pattern="^(ozon|wb)$"),
    limit: int = Query(200, ge=1, le=2000),
) -> list[dict[str, Any]]:
    resolved_schema = resolve_schema(schema)
    resolved_marketplace = resolve_marketplace(marketplace)
    ensure_demo_mode_data(resolved_schema, resolved_marketplace)
    summary = _fetch_store_summary_row(resolved_schema, resolved_marketplace)
    if summary is None:
        return []
    if not _table_exists(resolved_schema, "fact_unmatched_money"):
        return []

    rows = fetch_all(
        f"""
        SELECT
          event_date,
          source_entity,
          money_type,
          amount,
          amount_abs,
          match_status,
          incompleteness_stage,
          unmatched_reason,
          candidate_mapping_reason,
          candidate_product_count,
          most_likely_product_id,
          most_likely_key_type,
          confidence_level,
          campaign_id,
          advert_id,
          ad_id,
          order_id,
          posting_id,
          document_id,
          operation_id,
          nm_id,
          sku,
          offer_id,
          barcode,
          vendor_code,
          supplier_article,
          product_name_raw,
          brand_raw,
          subject_raw,
          raw_record_id
        FROM {resolved_schema}.fact_unmatched_money
        WHERE marketplace = %s
          AND event_date >= %s
          AND event_date <= %s
        ORDER BY amount_abs DESC, event_date DESC, raw_record_id
        LIMIT %s
        """,
        (
            summary["marketplace"],
            summary["period_start"],
            summary["period_end"],
            limit,
        ),
    )
    return normalize_json(rows)


@app.get("/api/costs")
def get_costs(
    schema: str | None = Query(None),
    marketplace: str | None = Query(None, pattern="^(ozon|wb)$"),
    offer_id: str | None = Query(None),
    active_only: bool = Query(True),
    limit: int = Query(500, ge=1, le=5000),
) -> list[dict[str, Any]]:
    resolved_schema = resolve_schema(schema)
    where: list[str] = []
    params: list[Any] = []
    if marketplace:
        where.append("marketplace = %s")
        params.append(marketplace)
    if offer_id:
        where.append("offer_id = %s")
        params.append(_normalize_offer_id(offer_id))
    if active_only:
        where.append("is_active = TRUE")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(limit)
    rows = fetch_all(
        f"""
        SELECT
          id,
          marketplace,
          offer_id,
          product_id,
          sku,
          product_name_snapshot,
          cogs_purchase_amount,
          packaging_amount,
          labeling_amount,
          inbound_logistics_amount,
          assembly_amount,
          other_unit_cost_amount,
          (
            COALESCE(cogs_purchase_amount, 0)
            + COALESCE(packaging_amount, 0)
            + COALESCE(labeling_amount, 0)
            + COALESCE(inbound_logistics_amount, 0)
            + COALESCE(assembly_amount, 0)
            + COALESCE(other_unit_cost_amount, 0)
          )::numeric(18,2) AS total_unit_cost,
          currency,
          valid_from,
          valid_to,
          source_type,
          source_comment,
          updated_by,
          is_active,
          updated_at
        FROM {resolved_schema}.product_cost_input
        {where_sql}
        ORDER BY updated_at DESC, id DESC
        LIMIT %s
        """,
        tuple(params),
    )
    return normalize_json(rows)


@app.get("/api/costs/{offer_id}")
def get_costs_by_offer(
    offer_id: str,
    schema: str | None = Query(None),
    marketplace: str | None = Query(None, pattern="^(ozon|wb)$"),
    active_only: bool = Query(True),
) -> list[dict[str, Any]]:
    return get_costs(
        schema=schema,
        marketplace=marketplace,
        offer_id=offer_id,
        active_only=active_only,
        limit=500,
    )


@app.post("/api/costs")
def create_cost(
    payload: CostInputUpsert,
    schema: str | None = Query(None),
) -> dict[str, Any]:
    resolved_schema = resolve_schema(schema)
    values = payload.model_dump()
    values["offer_id"] = _normalize_offer_id(values.get("offer_id"))

    with get_conn() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"""
                    INSERT INTO {resolved_schema}.product_cost_input (
                      marketplace,
                      offer_id,
                      product_id,
                      sku,
                      product_name_snapshot,
                      cogs_purchase_amount,
                      packaging_amount,
                      labeling_amount,
                      inbound_logistics_amount,
                      assembly_amount,
                      other_unit_cost_amount,
                      currency,
                      valid_from,
                      valid_to,
                      source_type,
                      source_comment,
                      updated_by,
                      is_active
                    ) VALUES (
                      %(marketplace)s,
                      %(offer_id)s,
                      %(product_id)s,
                      %(sku)s,
                      %(product_name_snapshot)s,
                      %(cogs_purchase_amount)s,
                      %(packaging_amount)s,
                      %(labeling_amount)s,
                      %(inbound_logistics_amount)s,
                      %(assembly_amount)s,
                      %(other_unit_cost_amount)s,
                      %(currency)s,
                      %(valid_from)s,
                      %(valid_to)s,
                      %(source_type)s,
                      %(source_comment)s,
                      %(updated_by)s,
                      %(is_active)s
                    )
                    RETURNING *
                    """,
                    values,
                )
                created = dict(cur.fetchone())
                cur.execute(
                    f"""
                    INSERT INTO {resolved_schema}.product_cost_input_history (
                      cost_input_id,
                      changed_by,
                      change_type,
                      old_payload,
                      new_payload
                    )
                    VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        created["id"],
                        created.get("updated_by"),
                        "create",
                        "{}",
                        json.dumps(normalize_json(created), ensure_ascii=False),
                    ),
                )

    _sync_fact_cogs_from_cost_input(resolved_schema)
    return normalize_json(created)


@app.put("/api/costs/{cost_id}")
def update_cost(
    cost_id: int,
    payload: CostInputUpsert,
    schema: str | None = Query(None),
) -> dict[str, Any]:
    resolved_schema = resolve_schema(schema)
    values = payload.model_dump()
    values["offer_id"] = _normalize_offer_id(values.get("offer_id"))

    with get_conn() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"SELECT * FROM {resolved_schema}.product_cost_input WHERE id = %s",
                    (cost_id,),
                )
                old_row = cur.fetchone()
                if old_row is None:
                    raise HTTPException(status_code=404, detail="Cost record not found")

                values["id"] = cost_id
                cur.execute(
                    f"""
                    UPDATE {resolved_schema}.product_cost_input
                    SET
                      marketplace = %(marketplace)s,
                      offer_id = %(offer_id)s,
                      product_id = %(product_id)s,
                      sku = %(sku)s,
                      product_name_snapshot = %(product_name_snapshot)s,
                      cogs_purchase_amount = %(cogs_purchase_amount)s,
                      packaging_amount = %(packaging_amount)s,
                      labeling_amount = %(labeling_amount)s,
                      inbound_logistics_amount = %(inbound_logistics_amount)s,
                      assembly_amount = %(assembly_amount)s,
                      other_unit_cost_amount = %(other_unit_cost_amount)s,
                      currency = %(currency)s,
                      valid_from = %(valid_from)s,
                      valid_to = %(valid_to)s,
                      source_type = %(source_type)s,
                      source_comment = %(source_comment)s,
                      updated_by = %(updated_by)s,
                      is_active = %(is_active)s,
                      updated_at = now()
                    WHERE id = %(id)s
                    RETURNING *
                    """,
                    values,
                )
                updated = dict(cur.fetchone())
                cur.execute(
                    f"""
                    INSERT INTO {resolved_schema}.product_cost_input_history (
                      cost_input_id,
                      changed_by,
                      change_type,
                      old_payload,
                      new_payload
                    )
                    VALUES (%s, %s, %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        cost_id,
                        updated.get("updated_by"),
                        "update",
                        json.dumps(normalize_json(dict(old_row)), ensure_ascii=False),
                        json.dumps(normalize_json(updated), ensure_ascii=False),
                    ),
                )

    _sync_fact_cogs_from_cost_input(resolved_schema)
    return normalize_json(updated)


@app.get("/api/costs/import-template")
def get_costs_import_template() -> dict[str, Any]:
    columns = [
        "marketplace",
        "offer_id",
        "sku",
        "product_name",
        "cogs_purchase_amount",
        "packaging_amount",
        "labeling_amount",
        "inbound_logistics_amount",
        "assembly_amount",
        "other_unit_cost_amount",
        "valid_from",
        "comment",
    ]
    sample = [
        "wb,offer-123,sku-123,Sample Product,520,20,5,35,0,10,2026-03-01,first load",
    ]
    csv_text = ",".join(columns) + "\n" + "\n".join(sample) + "\n"
    return {"columns": columns, "csv": csv_text}


@app.post("/api/costs/import")
def import_costs(
    payload: CostImportRequest,
    schema: str | None = Query(None),
) -> dict[str, Any]:
    resolved_schema = resolve_schema(schema)
    mode = payload.mode
    updated_by = payload.updated_by
    csv_data = payload.csv_data
    if not csv_data.strip():
        raise HTTPException(status_code=400, detail="csv_data is required")

    reader = csv.DictReader(io.StringIO(csv_data))
    required_columns = {"marketplace", "offer_id", "valid_from", "cogs_purchase_amount"}
    missing = required_columns - set(reader.fieldnames or [])
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required columns: {', '.join(sorted(missing))}")

    inserted = 0
    updated = 0
    skipped = 0
    errors: list[str] = []
    line_num = 1

    with get_conn() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                for row in reader:
                    line_num += 1
                    try:
                        marketplace = (row.get("marketplace") or "").strip().lower()
                        if marketplace not in ALLOWED_MARKETPLACES:
                            raise ValueError("invalid marketplace")
                        offer_id = _normalize_offer_id(row.get("offer_id"))
                        valid_from = date.fromisoformat((row.get("valid_from") or "").strip())
                        payload = {
                            "marketplace": marketplace,
                            "offer_id": offer_id,
                            "sku": (row.get("sku") or "").strip() or None,
                            "product_name_snapshot": (row.get("product_name") or "").strip() or None,
                            "cogs_purchase_amount": _as_decimal(row.get("cogs_purchase_amount")),
                            "packaging_amount": _as_decimal(row.get("packaging_amount")),
                            "labeling_amount": _as_decimal(row.get("labeling_amount")),
                            "inbound_logistics_amount": _as_decimal(row.get("inbound_logistics_amount")),
                            "assembly_amount": _as_decimal(row.get("assembly_amount")),
                            "other_unit_cost_amount": _as_decimal(row.get("other_unit_cost_amount")),
                            "currency": (row.get("currency") or "RUB").strip() or "RUB",
                            "valid_from": valid_from,
                            "valid_to": date.fromisoformat((row.get("valid_to") or "").strip())
                            if (row.get("valid_to") or "").strip()
                            else None,
                            "source_type": "file_import",
                            "source_comment": (row.get("comment") or "").strip() or None,
                            "updated_by": updated_by or "import",
                            "is_active": True,
                        }

                        cur.execute(
                            f"""
                            SELECT id
                            FROM {resolved_schema}.product_cost_input
                            WHERE marketplace = %s
                              AND offer_id = %s
                              AND valid_from = %s
                              AND is_active = TRUE
                            ORDER BY updated_at DESC, id DESC
                            LIMIT 1
                            """,
                            (marketplace, offer_id, valid_from),
                        )
                        existing = cur.fetchone()

                        if existing and mode == "create":
                            skipped += 1
                            continue
                        if existing and mode == "skip_conflicts":
                            skipped += 1
                            continue

                        if existing:
                            payload["id"] = existing["id"]
                            cur.execute(
                                f"""
                                UPDATE {resolved_schema}.product_cost_input
                                SET
                                  sku = %(sku)s,
                                  product_name_snapshot = %(product_name_snapshot)s,
                                  cogs_purchase_amount = %(cogs_purchase_amount)s,
                                  packaging_amount = %(packaging_amount)s,
                                  labeling_amount = %(labeling_amount)s,
                                  inbound_logistics_amount = %(inbound_logistics_amount)s,
                                  assembly_amount = %(assembly_amount)s,
                                  other_unit_cost_amount = %(other_unit_cost_amount)s,
                                  currency = %(currency)s,
                                  valid_to = %(valid_to)s,
                                  source_type = %(source_type)s,
                                  source_comment = %(source_comment)s,
                                  updated_by = %(updated_by)s,
                                  updated_at = now()
                                WHERE id = %(id)s
                                """,
                                payload,
                            )
                            updated += 1
                        else:
                            cur.execute(
                                f"""
                                INSERT INTO {resolved_schema}.product_cost_input (
                                  marketplace,
                                  offer_id,
                                  sku,
                                  product_name_snapshot,
                                  cogs_purchase_amount,
                                  packaging_amount,
                                  labeling_amount,
                                  inbound_logistics_amount,
                                  assembly_amount,
                                  other_unit_cost_amount,
                                  currency,
                                  valid_from,
                                  valid_to,
                                  source_type,
                                  source_comment,
                                  updated_by,
                                  is_active
                                ) VALUES (
                                  %(marketplace)s,
                                  %(offer_id)s,
                                  %(sku)s,
                                  %(product_name_snapshot)s,
                                  %(cogs_purchase_amount)s,
                                  %(packaging_amount)s,
                                  %(labeling_amount)s,
                                  %(inbound_logistics_amount)s,
                                  %(assembly_amount)s,
                                  %(other_unit_cost_amount)s,
                                  %(currency)s,
                                  %(valid_from)s,
                                  %(valid_to)s,
                                  %(source_type)s,
                                  %(source_comment)s,
                                  %(updated_by)s,
                                  %(is_active)s
                                )
                                """,
                                payload,
                            )
                            inserted += 1
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"line {line_num}: {exc}")

    _sync_fact_cogs_from_cost_input(resolved_schema)
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }


@app.get("/api/replenishment/{offer_id}")
def get_replenishment(
    offer_id: str,
    schema: str | None = Query(None),
    marketplace: str | None = Query(None, pattern="^(ozon|wb)$"),
) -> list[dict[str, Any]]:
    resolved_schema = resolve_schema(schema)
    params: list[Any] = [_normalize_offer_id(offer_id)]
    where_sql = "WHERE offer_id = %s"
    if marketplace:
        where_sql += " AND marketplace = %s"
        params.append(marketplace)
    rows = fetch_all(
        f"""
        SELECT
          id,
          marketplace,
          offer_id,
          expected_replenishment_date,
          planned_replenishment_qty,
          replenishment_status,
          source_comment,
          updated_by,
          is_active,
          updated_at
        FROM {resolved_schema}.product_replenishment_input
        {where_sql}
        ORDER BY updated_at DESC, id DESC
        """,
        tuple(params),
    )
    return normalize_json(rows)


@app.post("/api/replenishment")
def create_replenishment(
    payload: ReplenishmentInputUpsert,
    schema: str | None = Query(None),
) -> dict[str, Any]:
    resolved_schema = resolve_schema(schema)
    values = payload.model_dump()
    values["offer_id"] = _normalize_offer_id(values.get("offer_id"))

    with get_conn() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"""
                    INSERT INTO {resolved_schema}.product_replenishment_input (
                      marketplace,
                      offer_id,
                      expected_replenishment_date,
                      planned_replenishment_qty,
                      replenishment_status,
                      source_comment,
                      updated_by,
                      is_active
                    ) VALUES (
                      %(marketplace)s,
                      %(offer_id)s,
                      %(expected_replenishment_date)s,
                      %(planned_replenishment_qty)s,
                      %(replenishment_status)s,
                      %(source_comment)s,
                      %(updated_by)s,
                      %(is_active)s
                    )
                    RETURNING *
                    """,
                    values,
                )
                created = dict(cur.fetchone())
    return normalize_json(created)


@app.put("/api/replenishment/{replenishment_id}")
def update_replenishment(
    replenishment_id: int,
    payload: ReplenishmentInputUpsert,
    schema: str | None = Query(None),
) -> dict[str, Any]:
    resolved_schema = resolve_schema(schema)
    values = payload.model_dump()
    values["offer_id"] = _normalize_offer_id(values.get("offer_id"))
    values["id"] = replenishment_id

    with get_conn() as conn:
        with conn.transaction():
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    f"SELECT 1 FROM {resolved_schema}.product_replenishment_input WHERE id = %s",
                    (replenishment_id,),
                )
                if cur.fetchone() is None:
                    raise HTTPException(status_code=404, detail="Replenishment record not found")
                cur.execute(
                    f"""
                    UPDATE {resolved_schema}.product_replenishment_input
                    SET
                      marketplace = %(marketplace)s,
                      offer_id = %(offer_id)s,
                      expected_replenishment_date = %(expected_replenishment_date)s,
                      planned_replenishment_qty = %(planned_replenishment_qty)s,
                      replenishment_status = %(replenishment_status)s,
                      source_comment = %(source_comment)s,
                      updated_by = %(updated_by)s,
                      is_active = %(is_active)s,
                      updated_at = now()
                    WHERE id = %(id)s
                    RETURNING *
                    """,
                    values,
                )
                updated = dict(cur.fetchone())
    return normalize_json(updated)
