from __future__ import annotations

import csv
import os
import subprocess
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.marts.build_marts import build_marts
ENV_PATH = ROOT / ".env"
SCHEMA = "client_demo_90d_20260408"
MARKETPLACE = "wb"

SERVICES_SPLIT_CSV = ROOT / "wb_services_final_split.csv"
FIX_REPORT_MD = ROOT / "wb_ads_promotion_overlap_fix_report.md"
STORE_BEFORE_AFTER_CSV = ROOT / "wb_store_economics_before_after.csv"
SIGN_RECHECK_CSV = ROOT / "wb_sign_recheck_after_fix.csv"
FINAL_RECON_MD = ROOT / "wb_final_reconciliation_after_overlap_fix.md"


PROMO_FILTER = "LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%wb продвижение%%'"
JAM_FILTER = (
    "LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%джем%%' "
    "OR LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%подписк%%'"
)
REVIEW_FILTER = "LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%списание за отзыв%%'"
SERVICE_LIKE_FILTER = (
    "LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%продвижени%%' "
    "OR LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%подписк%%' "
    "OR LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%списание за отзыв%%' "
    "OR LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%предоставление услуг%%' "
    "OR LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) LIKE '%%оказание услуг%%'"
)


@dataclass
class ServiceSplitRow:
    service_subtype: str
    amount: Decimal
    product_level_yes_no: str
    store_level_yes_no: str
    excluded_as_ads_overlap_yes_no: str
    confirming_source: str
    reason: str


def load_env() -> None:
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value.strip())


def get_conn() -> psycopg.Connection[Any]:
    load_env()
    return psycopg.connect(
        host=os.environ["PGHOST"],
        port=int(os.environ.get("PGPORT", "5432")),
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        row_factory=dict_row,
    )


def query(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            return list(cur.fetchall())


def query_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
    rows = query(sql, params)
    if not rows:
        raise RuntimeError("Query returned no rows")
    return rows[0]


def money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))


def money_text(value: Any) -> str:
    return f"{money(value):.2f}"


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def latest_period() -> tuple[str, str]:
    row = query_one(
        f"""
        SELECT period_start::text AS period_start, period_end::text AS period_end
        FROM {SCHEMA}.mart_store_summary_30d
        WHERE marketplace = %s
        ORDER BY period_end DESC, run_date DESC
        LIMIT 1
        """,
        (MARKETPLACE,),
    )
    return str(row["period_start"]), str(row["period_end"])


def collect_store_metrics(period_start: str, period_end: str) -> dict[str, Any]:
    core = query_one(
        f"""
        SELECT
          SUM(COALESCE(ads_spend, 0))::numeric(18,2) AS ads_total,
          SUM(CASE WHEN offer_id = '~' THEN COALESCE(services_amount, 0) ELSE 0 END)::numeric(18,2) AS services_store_total,
          SUM(COALESCE(marketplace_other_fees_amount, 0))::numeric(18,2) AS other_marketplace_fees_total,
          SUM(
            COALESCE(commission_amount, 0)
            + COALESCE(logistics_amount, 0)
            + COALESCE(marketplace_other_fees_amount, 0)
            + COALESCE(cogs_amount, 0)
            + COALESCE(ads_spend, 0)
          )::numeric(18,2) AS total_expenses,
          SUM(COALESCE(profit_after_ads, 0))::numeric(18,2) AS profit_after_ads_total
        FROM {SCHEMA}.mart_pnl_30d_by_product
        WHERE marketplace = %s
          AND period_start = %s
          AND period_end = %s
        """,
        (MARKETPLACE, period_start, period_end),
    )
    extras = query_one(
        f"""
        WITH promo_ids AS (
          SELECT finance_id
          FROM {SCHEMA}.stg_wb_finance
          WHERE COALESCE(marketplace, 'wb') = 'wb'
            AND ({PROMO_FILTER})
        ),
        jam_ids AS (
          SELECT finance_id
          FROM {SCHEMA}.stg_wb_finance
          WHERE COALESCE(marketplace, 'wb') = 'wb'
            AND ({JAM_FILTER})
        ),
        review_ids AS (
          SELECT finance_id
          FROM {SCHEMA}.stg_wb_finance
          WHERE COALESCE(marketplace, 'wb') = 'wb'
            AND ({REVIEW_FILTER})
        )
        SELECT
          COALESCE(SUM(
            CASE
              WHEN f.source_table = 'stg_wb_finance'
               AND f.money_bucket = 'services'
               AND split_part(f.source_record_id, ':', 1) IN (SELECT finance_id FROM promo_ids)
              THEN f.amount ELSE 0
            END
          ), 0)::numeric(18,2) AS wb_promotion_in_services_total,
          COALESCE(SUM(
            CASE
              WHEN f.source_table = 'stg_wb_finance'
               AND f.money_bucket = 'services'
               AND split_part(f.source_record_id, ':', 1) IN (SELECT finance_id FROM jam_ids)
               AND f.canonical_product_key = '~'
              THEN f.amount ELSE 0
            END
          ), 0)::numeric(18,2) AS jam_store_total,
          COALESCE(SUM(
            CASE
              WHEN f.source_table = 'stg_wb_finance'
               AND f.money_bucket = 'services'
               AND split_part(f.source_record_id, ':', 1) IN (SELECT finance_id FROM review_ids)
               AND f.canonical_product_key <> '~'
              THEN f.amount ELSE 0
            END
          ), 0)::numeric(18,2) AS review_product_total,
          COALESCE(SUM(CASE WHEN f.attribution_confidence = 'low' AND f.money_component_type = 'expense' THEN f.amount ELSE 0 END), 0)::numeric(18,2) AS low_confidence_total,
          COALESCE(SUM(CASE WHEN f.attribution_method = 'revenue_share_window' AND f.money_component_type = 'expense' THEN f.amount ELSE 0 END), 0)::numeric(18,2) AS revenue_share_window_total
        FROM {SCHEMA}.fact_product_money_attribution f
        WHERE f.marketplace = 'wb'
          AND f.event_date BETWEEN %s AND %s
        """,
        (period_start, period_end),
    )
    unmatched = query_one(
        f"""
        SELECT COALESCE(SUM(amount), 0)::numeric(18,2) AS unmatched_money_total
        FROM {SCHEMA}.fact_unmatched_money
        WHERE marketplace = 'wb'
          AND event_date BETWEEN %s AND %s
        """,
        (period_start, period_end),
    )
    app_type = query_one(
        f"""
        SELECT
          (SELECT COALESCE(SUM(spend), 0)::numeric(18,2) FROM {SCHEMA}.stg_wb_ads WHERE COALESCE(marketplace, 'wb') = 'wb' AND date BETWEEN %s AND %s) AS stg_ads_total,
          (SELECT COALESCE(SUM(spend), 0)::numeric(18,2) FROM {SCHEMA}.fact_ads_spend WHERE marketplace = 'wb' AND date BETWEEN %s AND %s) AS fact_ads_total,
          (SELECT COALESCE(SUM(amount), 0)::numeric(18,2) FROM {SCHEMA}.fact_product_money_attribution WHERE marketplace = 'wb' AND money_bucket = 'ads' AND event_date BETWEEN %s AND %s) AS attributed_ads_total
        """,
        (period_start, period_end, period_start, period_end, period_start, period_end),
    )
    storage = query_one(
        f"""
        SELECT
          (SELECT COALESCE(SUM(warehouse_price), 0)::numeric(18,2) FROM {SCHEMA}.stg_wb_paid_storage WHERE COALESCE(marketplace, 'wb') = 'wb' AND report_date BETWEEN %s AND %s) AS paid_storage_signed_total,
          (SELECT COALESCE(SUM(CASE WHEN warehouse_price > 0 THEN warehouse_price ELSE 0 END), 0)::numeric(18,2) FROM {SCHEMA}.stg_wb_paid_storage WHERE COALESCE(marketplace, 'wb') = 'wb' AND report_date BETWEEN %s AND %s) AS paid_storage_positive_only_total,
          (SELECT COALESCE(SUM(CASE WHEN warehouse_price < 0 THEN warehouse_price ELSE 0 END), 0)::numeric(18,2) FROM {SCHEMA}.stg_wb_paid_storage WHERE COALESCE(marketplace, 'wb') = 'wb' AND report_date BETWEEN %s AND %s) AS paid_storage_negative_only_total,
          (SELECT COALESCE(SUM(amount), 0)::numeric(18,2) FROM {SCHEMA}.fact_product_money_attribution WHERE marketplace = 'wb' AND money_bucket = 'storage' AND event_date BETWEEN %s AND %s) AS attributed_storage_total
        """,
        (
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
        ),
    )
    return core | extras | unmatched | app_type | storage


def collect_service_split() -> list[ServiceSplitRow]:
    subtype_stats = {
        row["service_subtype"]: row
        for row in query(
            f"""
            WITH raw_service_rows AS (
              SELECT
                finance_id,
                CASE
                  WHEN COALESCE(service_amount, 0) > 0 THEN 'sale_linked_service'
                  WHEN {PROMO_FILTER} THEN 'wb_promotion'
                  WHEN {JAM_FILTER} THEN 'jam_subscription'
                  WHEN {REVIEW_FILTER} THEN 'review_service'
                  WHEN (
                    {SERVICE_LIKE_FILTER}
                  ) AND (
                    NULLIF(NULLIF(TRIM(nm_id), ''), '0') IS NOT NULL
                    OR NULLIF(NULLIF(TRIM(offer_id), ''), '~') IS NOT NULL
                    OR NULLIF(TRIM(barcode), '') IS NOT NULL
                    OR NULLIF(substring(LOWER(COALESCE(raw_payload->>'bonus_type_name', '')) FROM 'товар ([0-9]+)'), '') IS NOT NULL
                  ) THEN 'other_service_sale_linked'
                  WHEN ({SERVICE_LIKE_FILTER}) THEN 'other_service_store_only'
                  ELSE 'non_service'
                END AS service_subtype,
                CASE
                  WHEN COALESCE(service_amount, 0) > 0 THEN COALESCE(service_amount, 0)
                  ELSE COALESCE(amount, 0)
                END::numeric(18,2) AS raw_amount
              FROM {SCHEMA}.stg_wb_finance
              WHERE COALESCE(marketplace, 'wb') = 'wb'
                AND (
                  COALESCE(service_amount, 0) > 0
                  OR ({SERVICE_LIKE_FILTER})
                )
            ),
            fact_rollup AS (
              SELECT
                split_part(source_record_id, ':', 1) AS finance_id,
                SUM(CASE WHEN canonical_product_key <> '~' THEN amount ELSE 0 END)::numeric(18,2) AS product_amount,
                SUM(CASE WHEN canonical_product_key = '~' THEN amount ELSE 0 END)::numeric(18,2) AS store_amount
              FROM {SCHEMA}.fact_product_money_attribution
              WHERE marketplace = 'wb'
                AND source_table = 'stg_wb_finance'
                AND money_bucket = 'services'
              GROUP BY 1
            )
            SELECT
              s.service_subtype,
              SUM(s.raw_amount)::numeric(18,2) AS raw_amount,
              COALESCE(SUM(f.product_amount), 0)::numeric(18,2) AS product_amount,
              COALESCE(SUM(f.store_amount), 0)::numeric(18,2) AS store_amount
            FROM raw_service_rows s
            LEFT JOIN fact_rollup f
              ON f.finance_id = s.finance_id
            WHERE s.service_subtype <> 'non_service'
            GROUP BY s.service_subtype
            """
        )
    }

    def build_row(
        subtype: str,
        source: str,
        reason: str,
    ) -> ServiceSplitRow:
        stats = subtype_stats.get(
            subtype,
            {
                "raw_amount": Decimal("0.00"),
                "product_amount": Decimal("0.00"),
                "store_amount": Decimal("0.00"),
            },
        )
        return ServiceSplitRow(
            service_subtype=subtype,
            amount=money(stats["raw_amount"]),
            product_level_yes_no="yes" if money(stats["product_amount"]) > 0 else "no",
            store_level_yes_no="yes" if money(stats["store_amount"]) > 0 else "no",
            excluded_as_ads_overlap_yes_no="yes"
            if money(stats["raw_amount"]) > 0 and money(stats["product_amount"]) == 0 and money(stats["store_amount"]) == 0 and subtype == "wb_promotion"
            else "no",
            confirming_source=source,
            reason=reason,
        )

    return [
        build_row(
            "wb_promotion",
            "finance bonus_type_name + overlap recheck",
            "Confirmed finance reflection of the same WB ads spend; excluded from services to prevent double count.",
        ),
        build_row(
            "jam_subscription",
            "finance bonus_type_name",
            "Store-only WB subscription cost; kept in services/store-level.",
        ),
        build_row(
            "review_service",
            "finance bonus_type_name + товар <nm_id> extraction",
            "Product-linked review spending; kept in services/product-level.",
        ),
        build_row(
            "sale_linked_service",
            "finance service_amount",
            "Native sale-linked service rows from service_amount; kept in product-level when matched.",
        ),
        build_row(
            "other_service_sale_linked",
            "finance service-like amount rows with product keys",
            "Fallback bucket for future product-linked services that are not review/jam/promotion.",
        ),
        build_row(
            "other_service_store_only",
            "finance service-like amount rows without product keys",
            "Fallback bucket for future non-promotion store-only services.",
        ),
    ]


def collect_sign_rows(period_start: str, period_end: str) -> list[dict[str, Any]]:
    rows = []
    datasets = [
        (
            "ads_raw",
            f"""
            SELECT
              COUNT(*)::int AS rows_total,
              SUM(COALESCE(spend, 0))::numeric(18,2) AS signed_total,
              SUM(CASE WHEN spend > 0 THEN spend ELSE 0 END)::numeric(18,2) AS positive_only_total,
              SUM(CASE WHEN spend < 0 THEN spend ELSE 0 END)::numeric(18,2) AS negative_only_total
            FROM {SCHEMA}.stg_wb_ads
            WHERE COALESCE(marketplace, 'wb') = 'wb'
              AND date BETWEEN %s AND %s
            """,
            (period_start, period_end),
            "Raw ads spend; no ABS applied in audit.",
        ),
        (
            "wb_promotion_raw",
            f"""
            SELECT
              COUNT(*)::int AS rows_total,
              SUM(COALESCE(amount, 0))::numeric(18,2) AS signed_total,
              SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END)::numeric(18,2) AS positive_only_total,
              SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END)::numeric(18,2) AS negative_only_total
            FROM {SCHEMA}.stg_wb_finance
            WHERE COALESCE(marketplace, 'wb') = 'wb'
              AND operation_date::date BETWEEN %s AND %s
              AND ({PROMO_FILTER})
            """,
            (period_start, period_end),
            "Raw WB Promotion finance amount.",
        ),
        (
            "services_store_level_after",
            f"""
            SELECT
              COUNT(*)::int AS rows_total,
              SUM(COALESCE(amount, 0))::numeric(18,2) AS signed_total,
              SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END)::numeric(18,2) AS positive_only_total,
              SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END)::numeric(18,2) AS negative_only_total
            FROM {SCHEMA}.fact_product_money_attribution
            WHERE marketplace = 'wb'
              AND money_bucket = 'services'
              AND canonical_product_key = '~'
              AND event_date BETWEEN %s AND %s
            """,
            (period_start, period_end),
            "Post-fix store-level services remaining in attributed fact.",
        ),
        (
            "storage_raw_paid_storage",
            f"""
            SELECT
              COUNT(*)::int AS rows_total,
              SUM(COALESCE(warehouse_price, 0))::numeric(18,2) AS signed_total,
              SUM(CASE WHEN warehouse_price > 0 THEN warehouse_price ELSE 0 END)::numeric(18,2) AS positive_only_total,
              SUM(CASE WHEN warehouse_price < 0 THEN warehouse_price ELSE 0 END)::numeric(18,2) AS negative_only_total
            FROM {SCHEMA}.stg_wb_paid_storage
            WHERE COALESCE(marketplace, 'wb') = 'wb'
              AND report_date BETWEEN %s AND %s
            """,
            (period_start, period_end),
            "Raw paid-storage report with sign-aware totals.",
        ),
        (
            "storage_attributed_after",
            f"""
            SELECT
              COUNT(*)::int AS rows_total,
              SUM(COALESCE(amount, 0))::numeric(18,2) AS signed_total,
              SUM(CASE WHEN amount > 0 THEN amount ELSE 0 END)::numeric(18,2) AS positive_only_total,
              SUM(CASE WHEN amount < 0 THEN amount ELSE 0 END)::numeric(18,2) AS negative_only_total
            FROM {SCHEMA}.fact_product_money_attribution
            WHERE marketplace = 'wb'
              AND money_bucket = 'storage'
              AND event_date BETWEEN %s AND %s
            """,
            (period_start, period_end),
            "Post-fix attributed storage bucket.",
        ),
    ]
    for layer_name, sql, params, note in datasets:
        row = query_one(sql, params)
        rows.append(
            {
                "layer": layer_name,
                "rows_total": row["rows_total"],
                "signed_total": money_text(row["signed_total"]),
                "positive_only_total": money_text(row["positive_only_total"]),
                "negative_only_total": money_text(row["negative_only_total"]),
                "note": note,
            }
        )
    return rows


def bucket_granularity_rows(period_start: str, period_end: str) -> list[dict[str, Any]]:
    return query(
        f"""
        SELECT
          money_bucket,
          SUM(CASE WHEN canonical_product_key <> '~' THEN amount ELSE 0 END)::numeric(18,2) AS product_total,
          SUM(CASE WHEN canonical_product_key = '~' THEN amount ELSE 0 END)::numeric(18,2) AS store_total,
          SUM(CASE WHEN attribution_confidence = 'low' THEN amount ELSE 0 END)::numeric(18,2) AS low_conf_total,
          CASE
            WHEN SUM(CASE WHEN canonical_product_key = '~' THEN amount ELSE 0 END) = 0 THEN 'product-level'
            WHEN SUM(CASE WHEN canonical_product_key <> '~' THEN amount ELSE 0 END) = 0 THEN 'store-level'
            ELSE 'mixed / heuristic'
          END AS granularity_class
        FROM {SCHEMA}.fact_product_money_attribution
        WHERE marketplace = 'wb'
          AND money_component_type = 'expense'
          AND event_date BETWEEN %s AND %s
        GROUP BY money_bucket
        ORDER BY money_bucket
        """,
        (period_start, period_end),
    )


def collect_reconciliation_snapshot(period_start: str, period_end: str) -> dict[str, Any]:
    return query_one(
        f"""
        WITH sales AS (
          SELECT
            COALESCE(SUM(revenue_gross), 0)::numeric(18,2) AS raw_sales_total
          FROM {SCHEMA}.stg_wb_sale
          WHERE COALESCE(marketplace, 'wb') = 'wb'
            AND sale_date::date BETWEEN %s AND %s
        ),
        sales_attr AS (
          SELECT
            COALESCE(SUM(amount), 0)::numeric(18,2) AS attributed_sales_total
          FROM {SCHEMA}.fact_product_money_attribution
          WHERE marketplace = 'wb'
            AND money_bucket = 'revenue'
            AND event_date BETWEEN %s AND %s
        ),
        ads AS (
          SELECT
            COALESCE(SUM(spend), 0)::numeric(18,2) AS stg_ads_total
          FROM {SCHEMA}.stg_wb_ads
          WHERE COALESCE(marketplace, 'wb') = 'wb'
            AND date BETWEEN %s AND %s
        ),
        ads_fact AS (
          SELECT
            COALESCE(SUM(spend), 0)::numeric(18,2) AS fact_ads_total
          FROM {SCHEMA}.fact_ads_spend
          WHERE marketplace = 'wb'
            AND date BETWEEN %s AND %s
        ),
        ads_attr AS (
          SELECT
            COALESCE(SUM(amount), 0)::numeric(18,2) AS attributed_ads_total
          FROM {SCHEMA}.fact_product_money_attribution
          WHERE marketplace = 'wb'
            AND money_bucket = 'ads'
            AND event_date BETWEEN %s AND %s
        ),
        promo_raw AS (
          SELECT
            COALESCE(SUM(amount), 0)::numeric(18,2) AS raw_promotion_total
          FROM {SCHEMA}.stg_wb_finance
          WHERE COALESCE(marketplace, 'wb') = 'wb'
            AND operation_date::date BETWEEN %s AND %s
            AND ({PROMO_FILTER})
        ),
        promo_attr AS (
          SELECT
            COALESCE(SUM(amount), 0)::numeric(18,2) AS attributed_promotion_total
          FROM {SCHEMA}.fact_product_money_attribution
          WHERE marketplace = 'wb'
            AND money_bucket = 'services'
            AND source_table = 'stg_wb_finance'
            AND split_part(source_record_id, ':', 1) IN (
              SELECT finance_id
              FROM {SCHEMA}.stg_wb_finance
              WHERE COALESCE(marketplace, 'wb') = 'wb'
                AND operation_date::date BETWEEN %s AND %s
                AND ({PROMO_FILTER})
            )
            AND event_date BETWEEN %s AND %s
        ),
        services AS (
          SELECT
            COALESCE(SUM(amount), 0)::numeric(18,2) AS attributed_services_total,
            COALESCE(SUM(CASE WHEN canonical_product_key = '~' THEN amount ELSE 0 END), 0)::numeric(18,2) AS attributed_services_store_total,
            COALESCE(SUM(CASE WHEN canonical_product_key <> '~' THEN amount ELSE 0 END), 0)::numeric(18,2) AS attributed_services_product_total
          FROM {SCHEMA}.fact_product_money_attribution
          WHERE marketplace = 'wb'
            AND money_bucket = 'services'
            AND event_date BETWEEN %s AND %s
        ),
        unmatched AS (
          SELECT COALESCE(SUM(amount), 0)::numeric(18,2) AS unmatched_total
          FROM {SCHEMA}.fact_unmatched_money
          WHERE marketplace = 'wb'
            AND event_date BETWEEN %s AND %s
        ),
        low_conf AS (
          SELECT
            COALESCE(SUM(amount), 0)::numeric(18,2) AS low_conf_total
          FROM {SCHEMA}.fact_product_money_attribution
          WHERE marketplace = 'wb'
            AND money_component_type = 'expense'
            AND attribution_confidence = 'low'
            AND event_date BETWEEN %s AND %s
        ),
        storage AS (
          SELECT
            COALESCE(SUM(CASE WHEN warehouse_price > 0 THEN warehouse_price ELSE 0 END), 0)::numeric(18,2) AS raw_storage_positive_total
          FROM {SCHEMA}.stg_wb_paid_storage
          WHERE COALESCE(marketplace, 'wb') = 'wb'
            AND report_date BETWEEN %s AND %s
        ),
        storage_attr AS (
          SELECT
            COALESCE(SUM(amount), 0)::numeric(18,2) AS attributed_storage_total
          FROM {SCHEMA}.fact_product_money_attribution
          WHERE marketplace = 'wb'
            AND money_bucket = 'storage'
            AND event_date BETWEEN %s AND %s
        )
        SELECT
          sales.raw_sales_total,
          sales_attr.attributed_sales_total,
          ads.stg_ads_total,
          ads_fact.fact_ads_total,
          ads_attr.attributed_ads_total,
          promo_raw.raw_promotion_total,
          promo_attr.attributed_promotion_total,
          services.attributed_services_total,
          services.attributed_services_store_total,
          services.attributed_services_product_total,
          unmatched.unmatched_total,
          low_conf.low_conf_total,
          storage.raw_storage_positive_total,
          storage_attr.attributed_storage_total
        FROM sales, sales_attr, ads, ads_fact, ads_attr, promo_raw, promo_attr, services, unmatched, low_conf, storage, storage_attr
        """,
        (
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
            period_start,
            period_end,
        ),
    )


def rebuild_layers(period_start: str, period_end: str) -> None:
    build_marts(
        schema=SCHEMA,
        period_start=period_start,
        period_end=period_end,
        build_mode="diagnostic_build",
        marketplace=MARKETPLACE,
    )
    env = dict(os.environ)
    env["OZON_SCHEMA"] = SCHEMA
    env["SCHEMA"] = SCHEMA
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_profit_control_cabinet_human_pack_v1.py")],
        cwd=ROOT,
        check=True,
        env=env,
    )


def main() -> None:
    load_env()
    period_start, period_end = latest_period()
    before = collect_store_metrics(period_start, period_end)
    before_recon = collect_reconciliation_snapshot(period_start, period_end)

    rebuild_layers(period_start, period_end)

    after = collect_store_metrics(period_start, period_end)
    after_recon = collect_reconciliation_snapshot(period_start, period_end)
    split_rows = collect_service_split()
    sign_rows = collect_sign_rows(period_start, period_end)
    bucket_rows = bucket_granularity_rows(period_start, period_end)

    write_csv(
        STORE_BEFORE_AFTER_CSV,
        [
            {
                "stage": "before",
                "period_start": period_start,
                "period_end": period_end,
                "ads_total": money_text(before["ads_total"]),
                "services_store_total": money_text(before["services_store_total"]),
                "other_marketplace_fees_total": money_text(before["other_marketplace_fees_total"]),
                "total_expenses": money_text(before["total_expenses"]),
                "profit_after_ads_total": money_text(before["profit_after_ads_total"]),
                "wb_promotion_in_services_total": money_text(before["wb_promotion_in_services_total"]),
                "jam_store_total": money_text(before["jam_store_total"]),
                "review_product_total": money_text(before["review_product_total"]),
                "unmatched_money_total": money_text(before["unmatched_money_total"]),
                "low_confidence_total": money_text(before["low_confidence_total"]),
                "revenue_share_window_total": money_text(before["revenue_share_window_total"]),
            },
            {
                "stage": "after",
                "period_start": period_start,
                "period_end": period_end,
                "ads_total": money_text(after["ads_total"]),
                "services_store_total": money_text(after["services_store_total"]),
                "other_marketplace_fees_total": money_text(after["other_marketplace_fees_total"]),
                "total_expenses": money_text(after["total_expenses"]),
                "profit_after_ads_total": money_text(after["profit_after_ads_total"]),
                "wb_promotion_in_services_total": money_text(after["wb_promotion_in_services_total"]),
                "jam_store_total": money_text(after["jam_store_total"]),
                "review_product_total": money_text(after["review_product_total"]),
                "unmatched_money_total": money_text(after["unmatched_money_total"]),
                "low_confidence_total": money_text(after["low_confidence_total"]),
                "revenue_share_window_total": money_text(after["revenue_share_window_total"]),
            },
        ],
        [
            "stage",
            "period_start",
            "period_end",
            "ads_total",
            "services_store_total",
            "other_marketplace_fees_total",
            "total_expenses",
            "profit_after_ads_total",
            "wb_promotion_in_services_total",
            "jam_store_total",
            "review_product_total",
            "unmatched_money_total",
            "low_confidence_total",
            "revenue_share_window_total",
        ],
    )

    write_csv(
        SERVICES_SPLIT_CSV,
        [
            {
                "service_subtype": row.service_subtype,
                "amount": money_text(row.amount),
                "product_level_yes_no": row.product_level_yes_no,
                "store_level_yes_no": row.store_level_yes_no,
                "excluded_as_ads_overlap_yes_no": row.excluded_as_ads_overlap_yes_no,
                "confirming_source": row.confirming_source,
                "reason": row.reason,
            }
            for row in split_rows
        ],
        [
            "service_subtype",
            "amount",
            "product_level_yes_no",
            "store_level_yes_no",
            "excluded_as_ads_overlap_yes_no",
            "confirming_source",
            "reason",
        ],
    )

    write_csv(
        SIGN_RECHECK_CSV,
        sign_rows,
        ["layer", "rows_total", "signed_total", "positive_only_total", "negative_only_total", "note"],
    )

    fix_report = f"""# WB Ads Promotion Overlap Fix Report

- Schema: `{SCHEMA}`
- Rebuild mode: `diagnostic_build`
- Period rebuilt: `{period_start}` .. `{period_end}`
- Cabinet pack rebuilt via `scripts/build_profit_control_cabinet_human_pack_v1.py`
- Code change: `src/marts/sql/mart_product_money_attribution.sql`

## Fix Applied

Confirmed duplicate subset:
- exact raw filter: `LOWER(raw_payload->>'bonus_type_name') LIKE '%wb продвижение%'`
- previous path: amount-based finance classification -> `money_bucket='services'` -> unmatched WB services -> store-level row `canonical_product_key='~'`
- new path: this exact subset is marked as `ads_overlap_excluded` inside finance classification and filtered out before expense attribution

What stayed untouched:
- `service_amount`-driven sale-linked services
- `Джем` / subscription store-level services
- review-service rows (`Списание за отзыв`) with product linkage

## Store Economics Before / After

See `wb_store_economics_before_after.csv`.

Key deltas:
- `ads_total`: `{money_text(before['ads_total'])}` -> `{money_text(after['ads_total'])}`
- `services_store_total`: `{money_text(before['services_store_total'])}` -> `{money_text(after['services_store_total'])}`
- `other_marketplace_fees_total`: `{money_text(before['other_marketplace_fees_total'])}` -> `{money_text(after['other_marketplace_fees_total'])}`
- `total_expenses`: `{money_text(before['total_expenses'])}` -> `{money_text(after['total_expenses'])}`
- `profit_after_ads_total`: `{money_text(before['profit_after_ads_total'])}` -> `{money_text(after['profit_after_ads_total'])}`
- `WB Продвижение` inside attributed services: `{money_text(before['wb_promotion_in_services_total'])}` -> `{money_text(after['wb_promotion_in_services_total'])}`
- `Джем` store total: `{money_text(before['jam_store_total'])}` -> `{money_text(after['jam_store_total'])}`
- review-service product total: `{money_text(before['review_product_total'])}` -> `{money_text(after['review_product_total'])}`

Interpretation:
- ads stayed in economics unchanged
- `WB Продвижение` stopped subtracting a second time through services
- real store-level subscriptions stayed
- product-level review service stayed

## Services Final Split

See `wb_services_final_split.csv`.

## Sign Recheck

See `wb_sign_recheck_after_fix.csv`.
"""
    FIX_REPORT_MD.write_text(fix_report, encoding="utf-8")

    granularity_table = "\n".join(
        f"| `{row['money_bucket']}` | `{money_text(row['product_total'])}` | `{money_text(row['store_total'])}` | `{money_text(row['low_conf_total'])}` | `{row['granularity_class']}` |"
        for row in bucket_rows
    )
    final_recon = f"""# WB Final Reconciliation After Overlap Fix

- Schema: `{SCHEMA}`
- Period: `{period_start}` .. `{period_end}`

## 1. Core Reconciliation

- sales raw -> attributed: `{money_text(after_recon['raw_sales_total'])}` -> `{money_text(after_recon['attributed_sales_total'])}`
- ads raw -> fact_ads_spend -> attributed ads: `{money_text(after_recon['stg_ads_total'])}` -> `{money_text(after_recon['fact_ads_total'])}` -> `{money_text(after_recon['attributed_ads_total'])}`
- raw WB Promotion finance: `{money_text(after_recon['raw_promotion_total'])}`
- attributed WB Promotion remaining in services after fix: `{money_text(after_recon['attributed_promotion_total'])}`
- attributed services total after fix: `{money_text(after_recon['attributed_services_total'])}`
- attributed services product-level after fix: `{money_text(after_recon['attributed_services_product_total'])}`
- attributed services store-level after fix: `{money_text(after_recon['attributed_services_store_total'])}`
- raw paid-storage positive-only total: `{money_text(after_recon['raw_storage_positive_total'])}`
- attributed storage total: `{money_text(after_recon['attributed_storage_total'])}`
- unmatched money total: `{money_text(after_recon['unmatched_total'])}`
- low-confidence allocation total: `{money_text(after_recon['low_conf_total'])}`

## 2. Required Checks

1. Ads no longer lose money on `app_type`:
   - `stg_wb_ads` total = `{money_text(after_recon['stg_ads_total'])}`
   - `fact_ads_spend` total = `{money_text(after_recon['fact_ads_total'])}`
   - attributed ads total = `{money_text(after_recon['attributed_ads_total'])}`
   - observed loss = `{money_text(money(after_recon['stg_ads_total']) - money(after_recon['attributed_ads_total']))}`

2. Storage remains sign-aware:
   - raw paid-storage positive-only = `{money_text(after_recon['raw_storage_positive_total'])}`
   - attributed storage = `{money_text(after_recon['attributed_storage_total'])}`
   - raw signed / positive / negative breakdown is in `wb_sign_recheck_after_fix.csv`

3. `WB Продвижение` no longer duplicates ads:
   - raw WB Promotion finance still exists at `{money_text(after_recon['raw_promotion_total'])}`
   - attributed WB Promotion in services after fix = `{money_text(after_recon['attributed_promotion_total'])}`
   - ads total stayed = `{money_text(after['ads_total'])}`

4. Low-confidence after fix:
   - low-confidence allocation total = `{money_text(after_recon['low_conf_total'])}`
   - unmatched money total = `{money_text(after_recon['unmatched_total'])}`

5. Bucket granularity after fix:

| bucket | product_total | store_total | low_conf_total | class |
| --- | ---: | ---: | ---: | --- |
{granularity_table}

## 3. Before / After Impact

- store-level services dropped by `{money_text(money(before['services_store_total']) - money(after['services_store_total']))}`
- other marketplace fees dropped by `{money_text(money(before['other_marketplace_fees_total']) - money(after['other_marketplace_fees_total']))}`
- total expenses dropped by `{money_text(money(before['total_expenses']) - money(after['total_expenses']))}`
- profit after ads increased by `{money_text(money(after['profit_after_ads_total']) - money(before['profit_after_ads_total']))}`

## 4. Final Model

- ads-layer is the primary advertising expense
- `WB Продвижение` finance documents are excluded from services because they duplicate ads economically
- `Джем` stays store-level
- review-service stays product-level
- sale-linked `service_amount` stays in product attribution
"""
    FINAL_RECON_MD.write_text(final_recon, encoding="utf-8")

    print(f"Wrote {SERVICES_SPLIT_CSV}")
    print(f"Wrote {FIX_REPORT_MD}")
    print(f"Wrote {STORE_BEFORE_AFTER_CSV}")
    print(f"Wrote {SIGN_RECHECK_CSV}")
    print(f"Wrote {FINAL_RECON_MD}")


if __name__ == "__main__":
    main()
