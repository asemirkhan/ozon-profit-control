from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

from src.utils.db import get_conn, run_in_transaction

logger = logging.getLogger(__name__)
SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
JSONB_PARAM_KEYS = {"raw_payload", "characteristics_json"}


def upsert_stg_wb_product(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        return 0
    for row in rows:
        row.setdefault("marketplace", "wb")

    query = sql.SQL(
        """
        INSERT INTO {schema}.stg_wb_product (
            nm_id,
            marketplace,
            offer_id,
            vendor_code,
            barcode,
            product_name,
            brand,
            category_name,
            subject_id,
            subject_name,
            length_cm,
            width_cm,
            height_cm,
            weight_brutto_kg,
            characteristics_json,
            raw_payload,
            raw_updated_at
        )
        VALUES (
            %(nm_id)s,
            %(marketplace)s,
            %(offer_id)s,
            %(vendor_code)s,
            %(barcode)s,
            %(product_name)s,
            %(brand)s,
            %(category_name)s,
            %(subject_id)s,
            %(subject_name)s,
            %(length_cm)s,
            %(width_cm)s,
            %(height_cm)s,
            %(weight_brutto_kg)s,
            %(characteristics_json)s,
            %(raw_payload)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (nm_id) DO UPDATE
        SET
            marketplace = EXCLUDED.marketplace,
            offer_id = EXCLUDED.offer_id,
            vendor_code = EXCLUDED.vendor_code,
            barcode = EXCLUDED.barcode,
            product_name = EXCLUDED.product_name,
            brand = EXCLUDED.brand,
            category_name = EXCLUDED.category_name,
            subject_id = EXCLUDED.subject_id,
            subject_name = EXCLUDED.subject_name,
            length_cm = EXCLUDED.length_cm,
            width_cm = EXCLUDED.width_cm,
            height_cm = EXCLUDED.height_cm,
            weight_brutto_kg = EXCLUDED.weight_brutto_kg,
            characteristics_json = EXCLUDED.characteristics_json,
            raw_payload = EXCLUDED.raw_payload,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    _execute_many(query, rows)
    return len(rows)


def upsert_stg_wb_order(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        return 0
    for row in rows:
        row.setdefault("marketplace", "wb")

    query = sql.SQL(
        """
        INSERT INTO {schema}.stg_wb_order (
            order_id,
            marketplace,
            order_date,
            order_status,
            cancel_dt,
            nm_id,
            offer_id,
            barcode,
            quantity,
            sale_price,
            warehouse_name,
            warehouse_type,
            region_name,
            tech_size,
            subject_name,
            supplier_article,
            raw_payload,
            raw_updated_at
        )
        VALUES (
            %(order_id)s,
            %(marketplace)s,
            %(order_date)s,
            %(order_status)s,
            %(cancel_dt)s,
            %(nm_id)s,
            %(offer_id)s,
            %(barcode)s,
            %(quantity)s,
            %(sale_price)s,
            %(warehouse_name)s,
            %(warehouse_type)s,
            %(region_name)s,
            %(tech_size)s,
            %(subject_name)s,
            %(supplier_article)s,
            %(raw_payload)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (order_id) DO UPDATE
        SET
            marketplace = EXCLUDED.marketplace,
            order_date = EXCLUDED.order_date,
            order_status = EXCLUDED.order_status,
            cancel_dt = EXCLUDED.cancel_dt,
            nm_id = EXCLUDED.nm_id,
            offer_id = EXCLUDED.offer_id,
            barcode = EXCLUDED.barcode,
            quantity = EXCLUDED.quantity,
            sale_price = EXCLUDED.sale_price,
            warehouse_name = EXCLUDED.warehouse_name,
            warehouse_type = EXCLUDED.warehouse_type,
            region_name = EXCLUDED.region_name,
            tech_size = EXCLUDED.tech_size,
            subject_name = EXCLUDED.subject_name,
            supplier_article = EXCLUDED.supplier_article,
            raw_payload = EXCLUDED.raw_payload,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    _execute_many(query, rows)
    return len(rows)


def upsert_stg_wb_sale(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        return 0
    for row in rows:
        row.setdefault("marketplace", "wb")

    query = sql.SQL(
        """
        INSERT INTO {schema}.stg_wb_sale (
            sale_id,
            marketplace,
            sale_date,
            order_id,
            sale_status,
            is_return,
            nm_id,
            offer_id,
            barcode,
            quantity,
            revenue_gross,
            warehouse_name,
            region_name,
            tech_size,
            subject_name,
            supplier_article,
            raw_payload,
            raw_updated_at
        )
        VALUES (
            %(sale_id)s,
            %(marketplace)s,
            %(sale_date)s,
            %(order_id)s,
            %(sale_status)s,
            %(is_return)s,
            %(nm_id)s,
            %(offer_id)s,
            %(barcode)s,
            %(quantity)s,
            %(revenue_gross)s,
            %(warehouse_name)s,
            %(region_name)s,
            %(tech_size)s,
            %(subject_name)s,
            %(supplier_article)s,
            %(raw_payload)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (sale_id) DO UPDATE
        SET
            marketplace = EXCLUDED.marketplace,
            sale_date = EXCLUDED.sale_date,
            order_id = EXCLUDED.order_id,
            sale_status = EXCLUDED.sale_status,
            is_return = EXCLUDED.is_return,
            nm_id = EXCLUDED.nm_id,
            offer_id = EXCLUDED.offer_id,
            barcode = EXCLUDED.barcode,
            quantity = EXCLUDED.quantity,
            revenue_gross = EXCLUDED.revenue_gross,
            warehouse_name = EXCLUDED.warehouse_name,
            region_name = EXCLUDED.region_name,
            tech_size = EXCLUDED.tech_size,
            subject_name = EXCLUDED.subject_name,
            supplier_article = EXCLUDED.supplier_article,
            raw_payload = EXCLUDED.raw_payload,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    _execute_many(query, rows)
    return len(rows)


def upsert_stg_wb_finance(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        return 0
    for row in rows:
        row.setdefault("marketplace", "wb")

    query = sql.SQL(
        """
        INSERT INTO {schema}.stg_wb_finance (
            finance_id,
            marketplace,
            operation_date,
            operation_date_source,
            source_operation_date,
            operation_type,
            order_id,
            sale_id,
            nm_id,
            offer_id,
            barcode,
            payout_amount,
            commission_amount,
            logistics_amount,
            service_amount,
            amount,
            rrd_id,
            rr_dt,
            sale_dt,
            order_dt,
            doc_type_name,
            supplier_oper_name,
            delivery_rub,
            rebill_logistic_cost,
            storage_fee,
            acceptance_amount,
            penalty_amount,
            deduction_amount,
            acquiring_fee,
            commission_percent,
            office_name,
            gi_box_type_name,
            subject_name,
            raw_payload,
            raw_updated_at
        )
        VALUES (
            %(finance_id)s,
            %(marketplace)s,
            %(operation_date)s,
            %(operation_date_source)s,
            %(source_operation_date)s,
            %(operation_type)s,
            %(order_id)s,
            %(sale_id)s,
            %(nm_id)s,
            %(offer_id)s,
            %(barcode)s,
            %(payout_amount)s,
            %(commission_amount)s,
            %(logistics_amount)s,
            %(service_amount)s,
            %(amount)s,
            %(rrd_id)s,
            %(rr_dt)s,
            %(sale_dt)s,
            %(order_dt)s,
            %(doc_type_name)s,
            %(supplier_oper_name)s,
            %(delivery_rub)s,
            %(rebill_logistic_cost)s,
            %(storage_fee)s,
            %(acceptance_amount)s,
            %(penalty_amount)s,
            %(deduction_amount)s,
            %(acquiring_fee)s,
            %(commission_percent)s,
            %(office_name)s,
            %(gi_box_type_name)s,
            %(subject_name)s,
            %(raw_payload)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (finance_id) DO UPDATE
        SET
            marketplace = EXCLUDED.marketplace,
            operation_date = EXCLUDED.operation_date,
            operation_date_source = EXCLUDED.operation_date_source,
            source_operation_date = EXCLUDED.source_operation_date,
            operation_type = EXCLUDED.operation_type,
            order_id = EXCLUDED.order_id,
            sale_id = EXCLUDED.sale_id,
            nm_id = EXCLUDED.nm_id,
            offer_id = EXCLUDED.offer_id,
            barcode = EXCLUDED.barcode,
            payout_amount = EXCLUDED.payout_amount,
            commission_amount = EXCLUDED.commission_amount,
            logistics_amount = EXCLUDED.logistics_amount,
            service_amount = EXCLUDED.service_amount,
            amount = EXCLUDED.amount,
            rrd_id = EXCLUDED.rrd_id,
            rr_dt = EXCLUDED.rr_dt,
            sale_dt = EXCLUDED.sale_dt,
            order_dt = EXCLUDED.order_dt,
            doc_type_name = EXCLUDED.doc_type_name,
            supplier_oper_name = EXCLUDED.supplier_oper_name,
            delivery_rub = EXCLUDED.delivery_rub,
            rebill_logistic_cost = EXCLUDED.rebill_logistic_cost,
            storage_fee = EXCLUDED.storage_fee,
            acceptance_amount = EXCLUDED.acceptance_amount,
            penalty_amount = EXCLUDED.penalty_amount,
            deduction_amount = EXCLUDED.deduction_amount,
            acquiring_fee = EXCLUDED.acquiring_fee,
            commission_percent = EXCLUDED.commission_percent,
            office_name = EXCLUDED.office_name,
            gi_box_type_name = EXCLUDED.gi_box_type_name,
            subject_name = EXCLUDED.subject_name,
            raw_payload = EXCLUDED.raw_payload,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    _execute_many(query, rows)
    return len(rows)


def upsert_stg_wb_ads(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        return 0
    for row in rows:
        row.setdefault("marketplace", "wb")
        row["nm_id"] = str(row.get("nm_id") or "~")
        row["app_type"] = str(row.get("app_type") or "~")

    query = sql.SQL(
        """
        INSERT INTO {schema}.stg_wb_ads (
            date,
            campaign_id,
            marketplace,
            campaign_name,
            campaign_type,
            offer_id,
            nm_id,
            app_type,
            impressions,
            clicks,
            ctr,
            cpc,
            cr,
            atbs,
            orders_count,
            revenue,
            spend,
            currency,
            raw_payload,
            raw_updated_at
        )
        VALUES (
            %(date)s,
            %(campaign_id)s,
            %(marketplace)s,
            %(campaign_name)s,
            %(campaign_type)s,
            %(offer_id)s,
            %(nm_id)s,
            %(app_type)s,
            %(impressions)s,
            %(clicks)s,
            %(ctr)s,
            %(cpc)s,
            %(cr)s,
            %(atbs)s,
            %(orders_count)s,
            %(revenue)s,
            %(spend)s,
            %(currency)s,
            %(raw_payload)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (date, campaign_id, offer_id, nm_id, app_type) DO UPDATE
        SET
            marketplace = EXCLUDED.marketplace,
            campaign_name = EXCLUDED.campaign_name,
            campaign_type = EXCLUDED.campaign_type,
            nm_id = EXCLUDED.nm_id,
            app_type = EXCLUDED.app_type,
            impressions = EXCLUDED.impressions,
            clicks = EXCLUDED.clicks,
            ctr = EXCLUDED.ctr,
            cpc = EXCLUDED.cpc,
            cr = EXCLUDED.cr,
            atbs = EXCLUDED.atbs,
            orders_count = EXCLUDED.orders_count,
            revenue = EXCLUDED.revenue,
            spend = EXCLUDED.spend,
            currency = EXCLUDED.currency,
            raw_payload = EXCLUDED.raw_payload,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    _execute_many(query, rows)
    return len(rows)


def upsert_stg_wb_paid_storage(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        return 0
    for row in rows:
        row.setdefault("marketplace", "wb")
        row["offer_id"] = str(row.get("offer_id") or "~")

    query = sql.SQL(
        """
        INSERT INTO {schema}.stg_wb_paid_storage (
            source_record_id,
            marketplace,
            report_date,
            offer_id,
            nm_id,
            barcode,
            vendor_code,
            subject_name,
            brand,
            size,
            warehouse_name,
            office_id,
            gi_id,
            chrt_id,
            volume,
            calc_type,
            warehouse_price,
            warehouse_coef,
            log_warehouse_coef,
            barcodes_count,
            pallet_place_code,
            pallet_count,
            loyalty_discount,
            original_date,
            tariff_fix_date,
            tariff_lower_date,
            raw_payload,
            raw_updated_at
        )
        VALUES (
            %(source_record_id)s,
            %(marketplace)s,
            %(report_date)s,
            %(offer_id)s,
            %(nm_id)s,
            %(barcode)s,
            %(vendor_code)s,
            %(subject_name)s,
            %(brand)s,
            %(size)s,
            %(warehouse_name)s,
            %(office_id)s,
            %(gi_id)s,
            %(chrt_id)s,
            %(volume)s,
            %(calc_type)s,
            %(warehouse_price)s,
            %(warehouse_coef)s,
            %(log_warehouse_coef)s,
            %(barcodes_count)s,
            %(pallet_place_code)s,
            %(pallet_count)s,
            %(loyalty_discount)s,
            %(original_date)s,
            %(tariff_fix_date)s,
            %(tariff_lower_date)s,
            %(raw_payload)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (source_record_id) DO UPDATE
        SET
            marketplace = EXCLUDED.marketplace,
            report_date = EXCLUDED.report_date,
            offer_id = EXCLUDED.offer_id,
            nm_id = EXCLUDED.nm_id,
            barcode = EXCLUDED.barcode,
            vendor_code = EXCLUDED.vendor_code,
            subject_name = EXCLUDED.subject_name,
            brand = EXCLUDED.brand,
            size = EXCLUDED.size,
            warehouse_name = EXCLUDED.warehouse_name,
            office_id = EXCLUDED.office_id,
            gi_id = EXCLUDED.gi_id,
            chrt_id = EXCLUDED.chrt_id,
            volume = EXCLUDED.volume,
            calc_type = EXCLUDED.calc_type,
            warehouse_price = EXCLUDED.warehouse_price,
            warehouse_coef = EXCLUDED.warehouse_coef,
            log_warehouse_coef = EXCLUDED.log_warehouse_coef,
            barcodes_count = EXCLUDED.barcodes_count,
            pallet_place_code = EXCLUDED.pallet_place_code,
            pallet_count = EXCLUDED.pallet_count,
            loyalty_discount = EXCLUDED.loyalty_discount,
            original_date = EXCLUDED.original_date,
            tariff_fix_date = EXCLUDED.tariff_fix_date,
            tariff_lower_date = EXCLUDED.tariff_lower_date,
            raw_payload = EXCLUDED.raw_payload,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    _execute_many(query, rows)
    return len(rows)


def upsert_stg_wb_stock(schema: str, rows: list[dict[str, Any]]) -> int:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema name: {schema}")
    if not rows:
        return 0
    for row in rows:
        row.setdefault("marketplace", "wb")

    query = sql.SQL(
        """
        INSERT INTO {schema}.stg_wb_stock (
            snapshot_date,
            marketplace,
            offer_id,
            nm_id,
            barcode,
            warehouse_name,
            warehouse_type,
            stock_total,
            stock_available,
            stock_reserved,
            stock_defect,
            raw_payload,
            raw_updated_at
        )
        VALUES (
            %(snapshot_date)s,
            %(marketplace)s,
            %(offer_id)s,
            %(nm_id)s,
            %(barcode)s,
            %(warehouse_name)s,
            %(warehouse_type)s,
            %(stock_total)s,
            %(stock_available)s,
            %(stock_reserved)s,
            %(stock_defect)s,
            %(raw_payload)s,
            %(raw_updated_at)s
        )
        ON CONFLICT (snapshot_date, offer_id, warehouse_name) DO UPDATE
        SET
            marketplace = EXCLUDED.marketplace,
            nm_id = EXCLUDED.nm_id,
            barcode = EXCLUDED.barcode,
            warehouse_type = EXCLUDED.warehouse_type,
            stock_total = EXCLUDED.stock_total,
            stock_available = EXCLUDED.stock_available,
            stock_reserved = EXCLUDED.stock_reserved,
            stock_defect = EXCLUDED.stock_defect,
            raw_payload = EXCLUDED.raw_payload,
            raw_updated_at = EXCLUDED.raw_updated_at,
            loaded_at = now()
        """
    ).format(schema=sql.Identifier(schema))

    _execute_many(query, rows)
    return len(rows)


def _execute_many(query: sql.SQL | sql.Composed, rows: list[dict[str, Any]]) -> None:
    prepared_rows = _prepare_rows_for_db(rows)
    with get_conn() as conn:
        def _run(current_conn: Any) -> None:
            with current_conn.cursor() as cur:
                cur.executemany(query, prepared_rows)

        run_in_transaction(conn, _run)


def _prepare_rows_for_db(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_prepare_row_for_db(row) for row in rows]


def _prepare_row_for_db(row: dict[str, Any]) -> dict[str, Any]:
    prepared: dict[str, Any] = {}
    for key, value in row.items():
        if key in JSONB_PARAM_KEYS and _is_json_like(value):
            prepared[key] = Jsonb(value)
        else:
            prepared[key] = value
    return prepared


def _is_json_like(value: Any) -> bool:
    return isinstance(value, Mapping | list)
