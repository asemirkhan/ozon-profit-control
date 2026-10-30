from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from psycopg import sql

from src.normalizers.wb.ads_normalizer import normalize_wb_ads
from src.utils.db import get_conn, project_root, resolve_project_path

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

WB_TABLES: tuple[str, ...] = (
    "dim_product",
    "stg_wb_product",
    "stg_wb_order",
    "stg_wb_sale",
    "stg_wb_finance",
    "stg_wb_ads",
    "stg_wb_stock",
    "fact_ads_spend",
    "fact_inventory_snapshot",
)

OZON_TABLES: tuple[str, ...] = (
    "dim_product",
    "stg_posting",
    "stg_posting_item",
    "stg_posting_item_financial",
    "stg_transaction",
    "stg_transaction_item",
    "stg_transaction_service",
    "fact_inventory_snapshot",
    "fact_ads_spend",
)

WB_RAW_SOURCES: dict[str, str] = {
    "products": "wb_products",
    "orders": "wb_orders",
    "sales": "wb_sales",
    "finance": "wb_finance",
    "ads": "wb_ads",
    "stock": "wb_stock",
}

OZON_RAW_SOURCES: dict[str, str] = {
    "products": "products",
    "postings": "postings_fbs",
    "transactions": "transactions",
    "inventory": "inventory",
    "ads": "ads",
}

RAW_TO_STG_MAP: dict[str, dict[str, str]] = {
    "wb": {
        "products": "stg_wb_product",
        "orders": "stg_wb_order",
        "sales": "stg_wb_sale",
        "finance": "stg_wb_finance",
        "ads": "stg_wb_ads",
        "stock": "stg_wb_stock",
    },
    "ozon": {
        "products": "dim_product",
        "postings": "stg_posting_item",
        "transactions": "stg_transaction",
        "inventory": "fact_inventory_snapshot",
        "ads": "fact_ads_spend",
    },
}

KEY_FIELDS: tuple[str, ...] = (
    "offer_id",
    "sku",
    "product_id",
    "nm_id",
    "supplierArticle",
    "vendorCode",
    "barcode",
    "item_code",
    "posting_number",
    "order_id",
    "srid",
    "product_name",
    "category_name",
    "brand",
)

CRITICAL_FIELDS: dict[str, tuple[str, ...]] = {
    "wb": (
        "nm_id",
        "supplierArticle",
        "offer_id",
        "barcode",
        "vendorCode",
        "subject_id",
        "brand",
        "srid",
        "product_name",
        "category_name",
    ),
    "ozon": (
        "product_id",
        "offer_id",
        "sku",
        "posting_number",
        "item_code",
        "barcode",
        "created_at",
        "in_process_at",
        "product_name",
    ),
}

FIELD_EQUIVALENTS: dict[str, set[str]] = {
    "supplierarticle": {"offerid"},
    "vendorcode": {"vendorcode", "offerid"},
    "subjectid": {"categoryid", "categoryname"},
    "srid": {"orderid"},
    "nmid": {"nmid", "productid"},
    "productid": {"productid", "nmid"},
    "itemcode": {"itemcode", "sku", "offerid"},
}

WB_JOIN_PAIRS: tuple[tuple[str, str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "stg_wb_ads",
        "stg_wb_product",
        (
            ("nm_id", "NULLIF(nm_id,'')", "NULLIF(nm_id,'')"),
            ("offer_id", "NULLIF(offer_id,'~')", "NULLIF(offer_id,'~')"),
        ),
    ),
    (
        "stg_wb_product",
        "dim_product",
        (
            ("offer_id", "NULLIF(offer_id,'~')", "NULLIF(offer_id,'~')"),
            ("nm_id->product_id", "NULLIF(nm_id,'')", "NULLIF(product_id,'')"),
            ("barcode->sku", "NULLIF(barcode,'')", "NULLIF(sku,'')"),
        ),
    ),
    (
        "stg_wb_product",
        "stg_wb_sale",
        (
            ("offer_id", "NULLIF(offer_id,'~')", "NULLIF(offer_id,'~')"),
            ("nm_id", "NULLIF(nm_id,'')", "NULLIF(nm_id,'')"),
            ("barcode", "NULLIF(barcode,'')", "NULLIF(barcode,'')"),
        ),
    ),
    (
        "stg_wb_order",
        "stg_wb_sale",
        (
            ("order_id", "NULLIF(order_id,'')", "NULLIF(order_id,'')"),
            ("offer_id", "NULLIF(offer_id,'~')", "NULLIF(offer_id,'~')"),
            ("nm_id", "NULLIF(nm_id,'')", "NULLIF(nm_id,'')"),
        ),
    ),
    (
        "stg_wb_finance",
        "stg_wb_sale",
        (
            ("order_id", "NULLIF(order_id,'')", "NULLIF(order_id,'')"),
            ("offer_id", "NULLIF(offer_id,'~')", "NULLIF(offer_id,'~')"),
            ("nm_id", "NULLIF(nm_id,'')", "NULLIF(nm_id,'')"),
        ),
    ),
    (
        "fact_ads_spend",
        "stg_wb_sale",
        (
            ("offer_id", "NULLIF(offer_id,'~')", "NULLIF(offer_id,'~')"),
            ("product_id->nm_id", "NULLIF(product_id,'~')", "NULLIF(nm_id,'')"),
            ("sku->barcode", "NULLIF(sku,'~')", "NULLIF(barcode,'')"),
        ),
    ),
)

OZON_JOIN_PAIRS: tuple[tuple[str, str, tuple[tuple[str, str, str], ...]], ...] = (
    (
        "stg_posting_item",
        "dim_product",
        (
            ("offer_id", "NULLIF(offer_id,'~')", "NULLIF(offer_id,'~')"),
            ("sku", "NULLIF(sku,'~')", "NULLIF(sku,'')"),
            ("product_id", "NULLIF(product_id,'~')", "NULLIF(product_id,'')"),
        ),
    ),
    (
        "stg_posting_item_financial",
        "stg_posting_item",
        (
            ("posting_number", "NULLIF(posting_number,'')", "NULLIF(posting_number,'')"),
            ("product_id", "NULLIF(product_id,'')", "NULLIF(product_id,'~')"),
            ("sku", "NULLIF(sku,'')", "NULLIF(sku,'~')"),
        ),
    ),
    (
        "stg_transaction_item",
        "dim_product",
        (
            ("sku", "NULLIF(sku,'')", "NULLIF(sku,'')"),
        ),
    ),
    (
        "stg_transaction",
        "stg_posting",
        (
            ("posting_number", "NULLIF(posting_number,'')", "NULLIF(posting_number,'')"),
        ),
    ),
    (
        "fact_inventory_snapshot",
        "dim_product",
        (
            ("offer_id", "NULLIF(offer_id,'~')", "NULLIF(offer_id,'~')"),
            ("sku", "NULLIF(sku,'~')", "NULLIF(sku,'')"),
            ("product_id", "NULLIF(product_id,'~')", "NULLIF(product_id,'')"),
        ),
    ),
)


@dataclass
class TableProfile:
    table: str
    row_count: int
    columns: list[tuple[str, str]]
    key_stats: dict[str, dict[str, Any]]


@dataclass
class RawSourceAudit:
    marketplace: str
    logical_source: str
    physical_source: str
    file_count: int
    meta_file_count: int
    top_level_keys: list[str]
    leaf_fields: list[str]
    leaf_fields_norm: set[str]
    array_paths: list[str]
    statuses: list[int]


def export_live_probe_audit(schema_wb: str, schema_ozon: str, output_dir: str = "exports/live_probe_audit") -> Path:
    _validate_ident(schema_wb)
    _validate_ident(schema_ozon)

    out_root = resolve_project_path(output_dir)
    wb_dir = out_root / "wb"
    ozon_dir = out_root / "ozon"
    wb_dir.mkdir(parents=True, exist_ok=True)
    ozon_dir.mkdir(parents=True, exist_ok=True)

    raw_audits: dict[str, dict[str, RawSourceAudit]] = {
        "wb": _audit_raw_sources(schema_wb, "wb", WB_RAW_SOURCES),
        "ozon": _audit_raw_sources(schema_ozon, "ozon", OZON_RAW_SOURCES),
    }

    with get_conn() as conn:
        wb_profiles = _export_tables_and_profiles(conn, schema_wb, WB_TABLES, wb_dir)
        ozon_profiles = _export_tables_and_profiles(conn, schema_ozon, OZON_TABLES, ozon_dir)

        join_report = _build_join_key_matrix(conn, schema_wb, schema_ozon)
        wb_native = _evaluate_wb_native_key(conn, schema_wb, raw_audits["wb"], join_report["wb"])
        ozon_native = _evaluate_ozon_native_key(conn, schema_ozon, raw_audits["ozon"], join_report["ozon"])

        wb_ads_collapse = _analyze_wb_ads_collapse(conn, schema_wb, raw_audits["wb"].get("ads"))

    _write_raw_vs_stg_diff(
        out_root=out_root,
        schema_wb=schema_wb,
        schema_ozon=schema_ozon,
        wb_profiles=wb_profiles,
        ozon_profiles=ozon_profiles,
        raw_audits=raw_audits,
    )
    _write_join_key_hypotheses(out_root, join_report)
    _write_wb_ads_collapse(out_root, schema_wb, wb_ads_collapse)
    _write_stg_schema_gap_recommendations(out_root, raw_audits, wb_profiles, ozon_profiles, join_report)
    _write_product_bridge_strategy(out_root, wb_native, ozon_native, join_report)
    _write_dim_product_bridge_design(out_root, wb_native, ozon_native)

    return out_root


def _validate_ident(value: str) -> None:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"Invalid identifier: {value}")


def _export_tables_and_profiles(conn: Any, schema: str, tables: Iterable[str], out_dir: Path) -> dict[str, TableProfile]:
    profiles: dict[str, TableProfile] = {}
    for table in tables:
        _validate_ident(table)
        csv_path = out_dir / f"{table}.csv"
        profile_path = out_dir / f"{table}__profile.md"

        columns = _table_columns(conn, schema, table)
        _export_table_csv(conn, schema, table, columns, csv_path)
        profile = _build_table_profile(conn, schema, table, columns)
        profiles[table] = profile
        profile_path.write_text(_render_table_profile(schema, profile), encoding="utf-8")
    return profiles


def _table_columns(conn: Any, schema: str, table: str) -> list[tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, table),
        )
        return [(str(r[0]), str(r[1])) for r in cur.fetchall()]


def _export_table_csv(conn: Any, schema: str, table: str, columns: list[tuple[str, str]], path: Path) -> None:
    header = [name for name, _ in columns]
    if not header:
        path.write_text("", encoding="utf-8")
        return

    with conn.cursor() as cur:
        query = sql.SQL("SELECT * FROM {}.{}") .format(sql.Identifier(schema), sql.Identifier(table))
        cur.execute(query)
        rows = cur.fetchall()

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def _build_table_profile(conn: Any, schema: str, table: str, columns: list[tuple[str, str]]) -> TableProfile:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT COUNT(*) FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table))
        )
        row_count = int(cur.fetchone()[0])

    key_stats: dict[str, dict[str, Any]] = {}
    col_names = {name for name, _ in columns}

    for key in KEY_FIELDS:
        key_column = _resolve_key_column(key, col_names)
        if not key_column:
            continue
        null_count, distinct_count = _null_and_distinct(conn, schema, table, key_column)
        top_values = _top_values(conn, schema, table, key_column)
        key_stats[key] = {
            "column": key_column,
            "null_count": null_count,
            "distinct_count": distinct_count,
            "top_values": top_values,
        }

    return TableProfile(table=table, row_count=row_count, columns=columns, key_stats=key_stats)


def _resolve_key_column(key_field: str, columns: set[str]) -> str | None:
    normalized_key = _norm_name(key_field)
    for column in columns:
        if _norm_name(column) == normalized_key:
            return column

    alias_map: dict[str, tuple[str, ...]] = {
        "supplierarticle": ("offer_id",),
        "vendorcode": ("vendor_code", "offer_id"),
        "srid": ("order_id",),
        "itemcode": ("item_code", "sku", "offer_id"),
        "productname": ("product_name",),
    }
    for candidate in alias_map.get(normalized_key, ()):  # pragma: no branch
        if candidate in columns:
            return candidate
    return None


def _null_and_distinct(conn: Any, schema: str, table: str, column: str) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                SELECT
                  SUM(CASE WHEN {col} IS NULL OR TRIM({col}::text) = '' OR {col}::text = '~' THEN 1 ELSE 0 END) AS null_cnt,
                  COUNT(DISTINCT {col}) FILTER (WHERE {col} IS NOT NULL AND TRIM({col}::text) <> '' AND {col}::text <> '~') AS distinct_cnt
                FROM {schema}.{table}
                """
            ).format(
                col=sql.Identifier(column),
                schema=sql.Identifier(schema),
                table=sql.Identifier(table),
            )
        )
        row = cur.fetchone()
        return int(row[0] or 0), int(row[1] or 0)


def _top_values(conn: Any, schema: str, table: str, column: str, limit: int = 20) -> list[tuple[str, int]]:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                """
                SELECT {col}::text AS value, COUNT(*) AS cnt
                FROM {schema}.{table}
                WHERE {col} IS NOT NULL
                  AND TRIM({col}::text) <> ''
                GROUP BY 1
                ORDER BY cnt DESC, value ASC
                LIMIT {lim}
                """
            ).format(
                col=sql.Identifier(column),
                schema=sql.Identifier(schema),
                table=sql.Identifier(table),
                lim=sql.Literal(limit),
            )
        )
        return [(str(r[0]), int(r[1])) for r in cur.fetchall()]


def _sample_rows(conn: Any, schema: str, table: str, limit: int = 20) -> tuple[list[str], list[tuple[Any, ...]]]:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("SELECT * FROM {}.{} LIMIT {}")
            .format(sql.Identifier(schema), sql.Identifier(table), sql.Literal(limit))
        )
        rows = cur.fetchall()
        columns = [str(desc.name) for desc in cur.description]
    return columns, rows


def _render_table_profile(schema: str, profile: TableProfile) -> str:
    lines: list[str] = []
    lines.append(f"# {profile.table} profile")
    lines.append("")
    lines.append(f"Schema: `{schema}`")
    lines.append(f"Row count: `{profile.row_count}`")
    lines.append("")
    lines.append("## Columns")
    lines.append("")
    lines.append("| column | type |")
    lines.append("|---|---|")
    for column, dtype in profile.columns:
        lines.append(f"| `{column}` | `{dtype}` |")

    lines.append("")
    lines.append("## Key Field Stats")
    lines.append("")
    if not profile.key_stats:
        lines.append("No key-like fields found.")
    else:
        for key_field, stats in profile.key_stats.items():
            lines.append(f"### {key_field} -> `{stats['column']}`")
            lines.append(f"- null_count: `{stats['null_count']}`")
            lines.append(f"- distinct_count: `{stats['distinct_count']}`")
            lines.append("- top_20:")
            if stats["top_values"]:
                for value, cnt in stats["top_values"]:
                    lines.append(f"  - `{value}`: `{cnt}`")
            else:
                lines.append("  - <empty>")
            lines.append("")

    sample_cols, sample_rows = _sample_rows_for_profile(schema, profile.table)
    lines.append("## Sample 20 rows")
    lines.append("")
    if not sample_cols:
        lines.append("No rows.")
    else:
        lines.append("| " + " | ".join(sample_cols) + " |")
        lines.append("|" + "|".join(["---"] * len(sample_cols)) + "|")
        for row in sample_rows:
            cells = [str(v) if v is not None else "" for v in row]
            lines.append("| " + " | ".join(cells) + " |")

    lines.append("")
    return "\n".join(lines)


def _sample_rows_for_profile(schema: str, table: str, limit: int = 20) -> tuple[list[str], list[tuple[Any, ...]]]:
    with get_conn() as conn:
        return _sample_rows(conn, schema, table, limit)


def _audit_raw_sources(schema: str, marketplace: str, source_map: dict[str, str]) -> dict[str, RawSourceAudit]:
    result: dict[str, RawSourceAudit] = {}
    base_raw = project_root() / "data" / "raw" / schema

    for logical_source, physical_source in source_map.items():
        source_dir = base_raw / physical_source
        json_files: list[Path] = []
        meta_files: list[Path] = []
        if source_dir.exists():
            for path in source_dir.rglob("*.json"):
                if path.name.endswith(".meta.json"):
                    meta_files.append(path)
                else:
                    json_files.append(path)

        top_level: set[str] = set()
        leaf_fields: set[str] = set()
        array_paths: set[str] = set()
        statuses: list[int] = []

        for meta in sorted(meta_files):
            try:
                payload = json.loads(meta.read_text(encoding="utf-8"))
            except Exception:
                continue
            status = payload.get("http_status")
            if isinstance(status, int):
                statuses.append(status)

        for file_path in sorted(json_files):
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(payload, dict):
                top_level.update(payload.keys())
                _walk_json(payload, "", leaf_fields, array_paths)
            elif isinstance(payload, list):
                top_level.add("[]")
                _walk_json(payload, "", leaf_fields, array_paths)

        result[logical_source] = RawSourceAudit(
            marketplace=marketplace,
            logical_source=logical_source,
            physical_source=physical_source,
            file_count=len(json_files),
            meta_file_count=len(meta_files),
            top_level_keys=sorted(top_level),
            leaf_fields=sorted(leaf_fields),
            leaf_fields_norm={_norm_name(x) for x in leaf_fields},
            array_paths=sorted(array_paths),
            statuses=sorted(statuses),
        )

    return result


def _walk_json(value: Any, path: str, leaf_fields: set[str], array_paths: set[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            next_path = f"{path}.{key}" if path else key
            if isinstance(nested, (dict, list)):
                _walk_json(nested, next_path, leaf_fields, array_paths)
            else:
                leaf_fields.add(key)
                leaf_fields.add(next_path)
    elif isinstance(value, list):
        if path:
            array_paths.add(path)
        for item in value:
            _walk_json(item, path + "[]" if path else "[]", leaf_fields, array_paths)


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _write_raw_vs_stg_diff(
    out_root: Path,
    schema_wb: str,
    schema_ozon: str,
    wb_profiles: dict[str, TableProfile],
    ozon_profiles: dict[str, TableProfile],
    raw_audits: dict[str, dict[str, RawSourceAudit]],
) -> None:
    lines: list[str] = []
    lines.append("# Raw vs STG Field Diff")
    lines.append("")

    for marketplace, schema, profiles in (
        ("wb", schema_wb, wb_profiles),
        ("ozon", schema_ozon, ozon_profiles),
    ):
        lines.append(f"## {marketplace.upper()} (`{schema}`)")
        lines.append("")
        mapping = RAW_TO_STG_MAP[marketplace]
        for raw_source, stg_table in mapping.items():
            audit = raw_audits[marketplace].get(raw_source)
            profile = profiles.get(stg_table)
            lines.append(f"### {raw_source} -> {stg_table}")
            if not audit:
                lines.append("- raw: not found")
                lines.append("")
                continue
            if not profile:
                lines.append("- stg table: not found")
                lines.append("")
                continue

            stg_columns = [name for name, _ in profile.columns]
            stg_norm = {_norm_name(c) for c in stg_columns}

            lost_fields: list[str] = []
            critical_lost: list[str] = []
            for field in audit.leaf_fields:
                norm_field = _norm_name(field)
                accepted = False
                if norm_field in stg_norm:
                    accepted = True
                else:
                    equivalents = FIELD_EQUIVALENTS.get(norm_field, set())
                    if equivalents & stg_norm:
                        accepted = True
                if not accepted:
                    lost_fields.append(field)

            critical_norms = {_norm_name(f) for f in CRITICAL_FIELDS[marketplace]}
            for field in lost_fields:
                norm_field = _norm_name(field)
                if norm_field in critical_norms:
                    critical_lost.append(field)

            lines.append(f"- raw files: `{audit.file_count}`")
            lines.append(f"- raw top-level keys: `{', '.join(audit.top_level_keys[:30])}`")
            lines.append(f"- raw leaf fields count: `{len(audit.leaf_fields)}`")
            lines.append(f"- stg columns count: `{len(stg_columns)}`")
            lines.append(f"- stg columns: `{', '.join(stg_columns)}`")
            lines.append(f"- lost raw fields count: `{len(lost_fields)}`")
            lines.append(f"- critical lost fields: `{', '.join(sorted(set(critical_lost))[:40]) or 'none'}`")
            lines.append("")

    (out_root / "raw_vs_stg_field_diff.md").write_text("\n".join(lines), encoding="utf-8")


def _build_join_key_matrix(conn: Any, schema_wb: str, schema_ozon: str) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {"wb": [], "ozon": []}

    for left, right, options in WB_JOIN_PAIRS:
        pair_result = {
            "pair": f"{left} ↔ {right}",
            "options": [],
        }
        for label, left_expr, right_expr in options:
            metric = _pair_join_metric(conn, schema_wb, left, right, left_expr, right_expr)
            pair_result["options"].append({"label": label, **metric})
        output["wb"].append(pair_result)

    for left, right, options in OZON_JOIN_PAIRS:
        pair_result = {
            "pair": f"{left} ↔ {right}",
            "options": [],
        }
        for label, left_expr, right_expr in options:
            metric = _pair_join_metric(conn, schema_ozon, left, right, left_expr, right_expr)
            pair_result["options"].append({"label": label, **metric})
        output["ozon"].append(pair_result)

    return output


def _pair_join_metric(
    conn: Any,
    schema: str,
    left_table: str,
    right_table: str,
    left_expr: str,
    right_expr: str,
) -> dict[str, Any]:
    query = f"""
    WITH l AS (
      SELECT DISTINCT ({left_expr})::text AS k
      FROM {schema}.{left_table}
      WHERE ({left_expr}) IS NOT NULL
        AND TRIM(({left_expr})::text) <> ''
        AND ({left_expr})::text <> '~'
    ),
    r AS (
      SELECT DISTINCT ({right_expr})::text AS k
      FROM {schema}.{right_table}
      WHERE ({right_expr}) IS NOT NULL
        AND TRIM(({right_expr})::text) <> ''
        AND ({right_expr})::text <> '~'
    )
    SELECT
      (SELECT COUNT(*) FROM l) AS left_distinct,
      (SELECT COUNT(*) FROM r) AS right_distinct,
      (SELECT COUNT(*) FROM l JOIN r USING(k)) AS match_distinct
    """
    with conn.cursor() as cur:
        cur.execute(query)
        left_distinct, right_distinct, match_distinct = cur.fetchone()

    left_distinct = int(left_distinct or 0)
    right_distinct = int(right_distinct or 0)
    match_distinct = int(match_distinct or 0)

    left_match = (match_distinct / left_distinct) if left_distinct else 0.0
    right_match = (match_distinct / right_distinct) if right_distinct else 0.0
    status = "none"
    if left_match >= 0.95 and right_match >= 0.95:
        status = "exact"
    elif match_distinct > 0:
        status = "partial"

    return {
        "left_distinct": left_distinct,
        "right_distinct": right_distinct,
        "match_distinct": match_distinct,
        "left_match_pct": round(left_match * 100, 2),
        "right_match_pct": round(right_match * 100, 2),
        "status": status,
    }


def _evaluate_wb_native_key(
    conn: Any,
    schema: str,
    raw_audit: dict[str, RawSourceAudit],
    join_report: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = {
        "nm_id": {"raw_aliases": {"nmid"}, "stg_columns": [("stg_wb_product", "nm_id"), ("stg_wb_order", "nm_id"), ("stg_wb_sale", "nm_id"), ("stg_wb_finance", "nm_id"), ("stg_wb_ads", "nm_id"), ("stg_wb_stock", "nm_id"), ("dim_product", "product_id")]},
        "supplierArticle": {"raw_aliases": {"supplierarticle"}, "stg_columns": [("stg_wb_product", "offer_id"), ("stg_wb_order", "offer_id"), ("stg_wb_sale", "offer_id"), ("stg_wb_finance", "offer_id"), ("stg_wb_ads", "offer_id"), ("stg_wb_stock", "offer_id"), ("dim_product", "offer_id")]},
        "offer_id": {"raw_aliases": {"offerid"}, "stg_columns": [("stg_wb_product", "offer_id"), ("stg_wb_order", "offer_id"), ("stg_wb_sale", "offer_id"), ("stg_wb_finance", "offer_id"), ("stg_wb_ads", "offer_id"), ("stg_wb_stock", "offer_id"), ("dim_product", "offer_id")]},
        "barcode": {"raw_aliases": {"barcode", "skus"}, "stg_columns": [("stg_wb_product", "barcode"), ("stg_wb_order", "barcode"), ("stg_wb_sale", "barcode"), ("stg_wb_stock", "barcode"), ("dim_product", "sku")]},
        "vendorCode": {"raw_aliases": {"vendorcode"}, "stg_columns": [("stg_wb_product", "vendor_code"), ("stg_wb_product", "offer_id"), ("dim_product", "offer_id")]},
    }
    return _evaluate_native_key_generic(conn, schema, raw_audit, join_report, candidates, marketplace="wb")


def _evaluate_ozon_native_key(
    conn: Any,
    schema: str,
    raw_audit: dict[str, RawSourceAudit],
    join_report: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = {
        "product_id": {
            "raw_aliases": {"productid"},
            "stg_columns": [
                ("dim_product", "product_id"),
                ("stg_posting_item", "product_id"),
                ("stg_posting_item_financial", "product_id"),
                ("fact_inventory_snapshot", "product_id"),
            ],
        },
        "offer_id": {
            "raw_aliases": {"offerid"},
            "stg_columns": [
                ("dim_product", "offer_id"),
                ("stg_posting_item", "offer_id"),
                ("fact_inventory_snapshot", "offer_id"),
                ("fact_ads_spend", "offer_id"),
            ],
        },
        "sku": {
            "raw_aliases": {"sku"},
            "stg_columns": [
                ("dim_product", "sku"),
                ("stg_posting_item", "sku"),
                ("stg_posting_item_financial", "sku"),
                ("stg_transaction_item", "sku"),
                ("fact_inventory_snapshot", "sku"),
                ("fact_ads_spend", "sku"),
            ],
        },
        "product_id+offer_id": {
            "raw_aliases": {"productid", "offerid"},
            "stg_columns": [
                ("stg_posting_item", "COALESCE(product_id,'~') || '|' || COALESCE(offer_id,'~')"),
                ("fact_inventory_snapshot", "COALESCE(product_id,'~') || '|' || COALESCE(offer_id,'~')"),
            ],
        },
        "posting_number": {
            "raw_aliases": {"postingnumber"},
            "stg_columns": [
                ("stg_posting", "posting_number"),
                ("stg_posting_item", "posting_number"),
                ("stg_posting_item_financial", "posting_number"),
                ("stg_transaction", "posting_number"),
            ],
            "penalty": 8.0,
        },
    }
    return _evaluate_native_key_generic(conn, schema, raw_audit, join_report, candidates, marketplace="ozon")


def _evaluate_native_key_generic(
    conn: Any,
    schema: str,
    raw_audit: dict[str, RawSourceAudit],
    join_report: list[dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
    marketplace: str,
) -> dict[str, Any]:
    evaluations: list[dict[str, Any]] = []
    for candidate, payload in candidates.items():
        raw_aliases: set[str] = payload["raw_aliases"]
        raw_presence = 0
        for src in raw_audit.values():
            if raw_aliases.issubset(src.leaf_fields_norm) or (raw_aliases & src.leaf_fields_norm):
                raw_presence += 1

        table_presence = 0
        table_non_null_total = 0
        for table, column_expr in payload["stg_columns"]:
            non_null_distinct = _distinct_non_null_count(conn, schema, table, column_expr)
            if non_null_distinct > 0:
                table_presence += 1
                table_non_null_total += non_null_distinct

        join_exact = 0
        join_partial = 0
        for pair in join_report:
            for option in pair["options"]:
                label_norm = _norm_name(option["label"])
                cand_norm = _norm_name(candidate)
                if cand_norm in label_norm or label_norm in cand_norm:
                    if option["status"] == "exact":
                        join_exact += 1
                    elif option["status"] == "partial":
                        join_partial += 1

        penalty = float(payload.get("penalty", 0.0))
        score = (raw_presence * 2.0) + (table_presence * 3.0) + (join_exact * 4.0) + (join_partial * 1.5) - penalty
        evaluations.append(
            {
                "candidate": candidate,
                "raw_presence_sources": raw_presence,
                "stg_presence_tables": table_presence,
                "distinct_volume": table_non_null_total,
                "join_exact_count": join_exact,
                "join_partial_count": join_partial,
                "penalty": penalty,
                "score": round(score, 3),
            }
        )

    evaluations.sort(key=lambda x: (x["score"], x["stg_presence_tables"], x["join_exact_count"], x["distinct_volume"]), reverse=True)
    best = evaluations[0] if evaluations else {}

    return {
        "marketplace": marketplace,
        "best_candidate": best.get("candidate"),
        "evaluations": evaluations,
    }


def _distinct_non_null_count(conn: Any, schema: str, table: str, column_expr: str) -> int:
    query = f"""
    SELECT COUNT(DISTINCT ({column_expr})::text)
    FROM {schema}.{table}
    WHERE ({column_expr}) IS NOT NULL
      AND TRIM(({column_expr})::text) <> ''
      AND ({column_expr})::text <> '~'
    """
    with conn.cursor() as cur:
        cur.execute(query)
        return int(cur.fetchone()[0] or 0)


def _analyze_wb_ads_collapse(conn: Any, schema_wb: str, ads_raw: RawSourceAudit | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "raw_files": 0,
        "meta_files": 0,
        "status_codes": [],
        "campaign_count": 0,
        "day_level_count": 0,
        "app_level_count": 0,
        "nm_level_count": 0,
        "normalizer_rows": 0,
        "stg_rows": 0,
        "fact_rows": 0,
        "reasons": [],
    }

    if ads_raw:
        result["raw_files"] = ads_raw.file_count
        result["meta_files"] = ads_raw.meta_file_count
        result["status_codes"] = ads_raw.statuses

        pages = _load_raw_pages(schema_wb, ads_raw.physical_source)
        campaigns: set[str] = set()
        day_count = 0
        app_count = 0
        nm_count = 0
        for payload in pages:
            iterable: list[Any]
            if isinstance(payload, dict):
                raw_ads = payload.get("ads")
                if isinstance(raw_ads, list):
                    iterable = raw_ads
                else:
                    iterable = [payload]
            elif isinstance(payload, list):
                iterable = payload
            else:
                iterable = []

            for item in iterable:
                if not isinstance(item, dict):
                    continue
                campaign_id = item.get("advertId") or item.get("campaign_id") or item.get("campaignId") or item.get("id")
                if campaign_id is not None:
                    campaigns.add(str(campaign_id))
                days = item.get("days")
                if isinstance(days, list):
                    day_count += len(days)
                    for day in days:
                        if not isinstance(day, dict):
                            continue
                        apps = day.get("apps")
                        if isinstance(apps, list):
                            app_count += len(apps)
                            for app in apps:
                                if not isinstance(app, dict):
                                    continue
                                nm_arr = app.get("nm") or app.get("nms")
                                if isinstance(nm_arr, list):
                                    nm_count += len([x for x in nm_arr if isinstance(x, dict)])

        result["campaign_count"] = len(campaigns)
        result["day_level_count"] = day_count
        result["app_level_count"] = app_count
        result["nm_level_count"] = nm_count

        normalizer_pages = [{"response": p} for p in pages if isinstance(p, dict)]
        result["normalizer_rows"] = len(normalize_wb_ads(normalizer_pages))

    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {}.stg_wb_ads").format(sql.Identifier(schema_wb)))
        result["stg_rows"] = int(cur.fetchone()[0] or 0)
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {}.fact_ads_spend WHERE marketplace='wb'").format(sql.Identifier(schema_wb)))
        result["fact_rows"] = int(cur.fetchone()[0] or 0)

    reasons: list[str] = []
    statuses = set(result["status_codes"])
    if 429 in statuses:
        reasons.append("rate limit")
    if result["raw_files"] == 0:
        reasons.append("raw payload отсутствует")
    if result["raw_files"] > 0 and result["campaign_count"] <= 1 and result["day_level_count"] <= 1:
        reasons.append("raw payload реально содержит мало данных")
    if result["normalizer_rows"] == 0 and result["raw_files"] > 0:
        reasons.append("normalizer shape mismatch")
    if result["normalizer_rows"] > result["stg_rows"]:
        reasons.append("deduplication / upsert collision")
    if result["nm_level_count"] == 0 and result["day_level_count"] > 0:
        reasons.append("потеря вложенных nm объектов или их отсутствие в raw")
    if not reasons:
        reasons.append("требуется дополнительный API backfill на более широком окне дат")

    result["reasons"] = reasons
    return result


def _load_raw_pages(schema: str, physical_source: str) -> list[Any]:
    source_dir = project_root() / "data" / "raw" / schema / physical_source
    if not source_dir.exists():
        return []

    payloads: list[Any] = []
    for path in sorted(source_dir.rglob("*.json")):
        if path.name.endswith(".meta.json"):
            continue
        try:
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return payloads


def _write_join_key_hypotheses(out_root: Path, join_report: dict[str, list[dict[str, Any]]]) -> None:
    lines: list[str] = []
    lines.append("# Join Key Hypotheses")
    lines.append("")

    for marketplace in ("wb", "ozon"):
        lines.append(f"## {marketplace.upper()}")
        lines.append("")
        for pair in join_report[marketplace]:
            lines.append(f"### {pair['pair']}")
            lines.append("| key option | left distinct | right distinct | match distinct | left match % | right match % | status |")
            lines.append("|---|---:|---:|---:|---:|---:|---|")
            best_option = None
            best_score = -1.0
            for option in pair["options"]:
                score = option["left_match_pct"] + option["right_match_pct"]
                if score > best_score:
                    best_score = score
                    best_option = option
                lines.append(
                    f"| `{option['label']}` | {option['left_distinct']} | {option['right_distinct']} | {option['match_distinct']} | {option['left_match_pct']} | {option['right_match_pct']} | {option['status']} |"
                )
            if best_option:
                lines.append(
                    f"- recommended join: `{best_option['label']}` (best combined match={round(best_score,2)}%)"
                )
            lines.append("")

    (out_root / "join_key_hypotheses.md").write_text("\n".join(lines), encoding="utf-8")


def _write_wb_ads_collapse(out_root: Path, schema_wb: str, collapse: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# WB ADS Row Collapse Analysis")
    lines.append("")
    lines.append(f"Schema: `{schema_wb}`")
    lines.append("")
    lines.append(f"1. raw ads payload files: `{collapse['raw_files']}`")
    lines.append(f"2. raw ads meta files: `{collapse['meta_files']}`")
    lines.append(f"3. status codes seen: `{collapse['status_codes']}`")
    lines.append(f"4. campaign IDs in raw: `{collapse['campaign_count']}`")
    lines.append(f"5. day-level objects: `{collapse['day_level_count']}`")
    lines.append(f"6. app-level objects: `{collapse['app_level_count']}`")
    lines.append(f"7. nm-level objects: `{collapse['nm_level_count']}`")
    lines.append(f"8. normalizer rows: `{collapse['normalizer_rows']}`")
    lines.append(f"9. stg_wb_ads rows: `{collapse['stg_rows']}`")
    lines.append(f"10. fact_ads_spend rows (wb): `{collapse['fact_rows']}`")
    lines.append("")
    lines.append("## Classified reasons")
    lines.append("")
    for reason in collapse["reasons"]:
        lines.append(f"- {reason}")

    (out_root / "wb_ads_row_collapse_analysis.md").write_text("\n".join(lines), encoding="utf-8")


def _write_stg_schema_gap_recommendations(
    out_root: Path,
    raw_audits: dict[str, dict[str, RawSourceAudit]],
    wb_profiles: dict[str, TableProfile],
    ozon_profiles: dict[str, TableProfile],
    join_report: dict[str, list[dict[str, Any]]],
) -> None:
    lines: list[str] = []
    lines.append("# STG Schema Gap Recommendations")
    lines.append("")

    for marketplace, profiles in (("wb", wb_profiles), ("ozon", ozon_profiles)):
        lines.append(f"## {marketplace.upper()}")
        lines.append("")
        critical_norm = {_norm_name(x) for x in CRITICAL_FIELDS[marketplace]}

        mapping = RAW_TO_STG_MAP[marketplace]
        for source, table in mapping.items():
            audit = raw_audits[marketplace].get(source)
            profile = profiles.get(table)
            if not audit or not profile:
                continue
            stg_cols = [c for c, _ in profile.columns]
            stg_norm = {_norm_name(c) for c in stg_cols}

            missing_critical: list[str] = []
            for raw_field in audit.leaf_fields:
                raw_norm = _norm_name(raw_field)
                if raw_norm not in critical_norm:
                    continue
                ok = raw_norm in stg_norm
                if not ok and FIELD_EQUIVALENTS.get(raw_norm):
                    ok = bool(FIELD_EQUIVALENTS[raw_norm] & stg_norm)
                if not ok:
                    missing_critical.append(raw_field)

            if missing_critical:
                lines.append(f"- `{source}` -> `{table}`: добавить/сохранить поля `{', '.join(sorted(set(missing_critical))[:20])}`")

        weak_pairs = []
        for pair in join_report[marketplace]:
            best = max(pair["options"], key=lambda x: x["left_match_pct"] + x["right_match_pct"])
            if best["status"] == "none" or (best["left_match_pct"] < 60 and best["right_match_pct"] < 60):
                weak_pairs.append((pair["pair"], best["label"], best["left_match_pct"], best["right_match_pct"]))
        if weak_pairs:
            lines.append("- Слабые join-пары (нужен bridge/fallback):")
            for pair_name, key_name, left_pct, right_pct in weak_pairs:
                lines.append(f"  - `{pair_name}` через `{key_name}`: {left_pct}% / {right_pct}%")

        lines.append("")

    (out_root / "stg_schema_gap_recommendations.md").write_text("\n".join(lines), encoding="utf-8")


def _write_product_bridge_strategy(
    out_root: Path,
    wb_native: dict[str, Any],
    ozon_native: dict[str, Any],
    join_report: dict[str, list[dict[str, Any]]],
) -> None:
    lines: list[str] = []
    lines.append("# Product Bridge Strategy From Live Data")
    lines.append("")

    lines.append("## WB")
    lines.append(f"- native master key: `{wb_native.get('best_candidate')}`")
    lines.append("- secondary keys: `offer_id`, `barcode`, `order_id`")
    lines.append("- fallback keys: `revenue_share_window` для unmatched finance/ads")
    lines.append("- unsafe joins: прямой `offer_id` когда ads приходит в `wb_nm_*` namespace")
    lines.append("- ads matching: `nm_id` -> bridge -> canonical offer_id; fallback по дневной доле выручки")
    lines.append("- finance matching: `order_id` exact -> bridge (`offer_id`/`nm_id`) -> revenue_share ±3 days")
    lines.append("")

    lines.append("## Ozon")
    lines.append(f"- native master key: `{ozon_native.get('best_candidate')}`")
    lines.append("- secondary keys: `offer_id`, `sku`, `posting_number`")
    lines.append("- fallback keys: posting-level revenue share и item-level sku match")
    lines.append("- unsafe joins: `offer_id`-only для `stg_posting_item_financial` без product context")
    lines.append("- posting financial matching: `product_id`/`sku` + `posting_number`, затем bridge")
    lines.append("- transactions matching: `stg_transaction_item.sku` -> bridge, fallback через `posting_number`")
    lines.append("- inventory matching: `product_id` / `offer_id` / `sku` с приоритетом native key")
    lines.append("")

    lines.append("## Candidate scoring (live)")
    lines.append("")
    lines.append("### WB")
    lines.append("| candidate | raw_presence_sources | stg_presence_tables | join_exact_count | join_partial_count | score |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for item in wb_native.get("evaluations", []):
        lines.append(
            f"| `{item['candidate']}` | {item['raw_presence_sources']} | {item['stg_presence_tables']} | {item['join_exact_count']} | {item['join_partial_count']} | {item['score']} |"
        )
    lines.append("")

    lines.append("### Ozon")
    lines.append("| candidate | raw_presence_sources | stg_presence_tables | join_exact_count | join_partial_count | score |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for item in ozon_native.get("evaluations", []):
        lines.append(
            f"| `{item['candidate']}` | {item['raw_presence_sources']} | {item['stg_presence_tables']} | {item['join_exact_count']} | {item['join_partial_count']} | {item['score']} |"
        )

    (out_root / "product_bridge_strategy_from_live_data.md").write_text("\n".join(lines), encoding="utf-8")


def _write_dim_product_bridge_design(out_root: Path, wb_native: dict[str, Any], ozon_native: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("# dim_product_bridge Design From Live Data")
    lines.append("")
    lines.append("## Minimal fields")
    lines.append("")
    lines.append("- `bridge_id`")
    lines.append("- `marketplace`")
    lines.append("- `canonical_product_key`")
    lines.append("- `native_master_key`")
    lines.append("- `offer_id`")
    lines.append("- `sku`")
    lines.append("- `product_id`")
    lines.append("- `nm_id`")
    lines.append("- `vendor_code`")
    lines.append("- `barcode`")
    lines.append("- `item_code`")
    lines.append("- `product_name`")
    lines.append("- `category_name`")
    lines.append("- `brand`")
    lines.append("- `match_status`")
    lines.append("- `match_confidence`")
    lines.append("- `created_at`, `updated_at`")
    lines.append("")

    lines.append("## Native master key by marketplace")
    lines.append("")
    lines.append(f"- WB: `{wb_native.get('best_candidate')}`")
    lines.append(f"- Ozon: `{ozon_native.get('best_candidate')}`")
    lines.append("")

    lines.append("## Fallback matching order")
    lines.append("")
    lines.append("### WB")
    lines.append("1. exact native key")
    lines.append("2. offer_id")
    lines.append("3. barcode")
    lines.append("4. order_id lineage (finance/order -> sale)")
    lines.append("5. revenue_share fallback")
    lines.append("")
    lines.append("### Ozon")
    lines.append("1. exact native key")
    lines.append("2. offer_id")
    lines.append("3. sku")
    lines.append("4. posting_number lineage")
    lines.append("5. posting revenue-share fallback")
    lines.append("")

    lines.append("## Matching status taxonomy")
    lines.append("")
    lines.append("- `exact_match`: native key matched directly")
    lines.append("- `bridge_match`: matched via secondary key and bridge map")
    lines.append("- `weak_match`: fallback allocation used")
    lines.append("- `ambiguous_match`: more than one plausible SKU for event")
    lines.append("- `unmatched`: no reliable product link")

    (out_root / "dim_product_bridge_design_from_live_data.md").write_text("\n".join(lines), encoding="utf-8")
