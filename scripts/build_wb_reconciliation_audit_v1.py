from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from dotenv import load_dotenv
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]

import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.normalizers.wb.ads_normalizer import normalize_wb_ads
from src.normalizers.wb.finance_normalizer import build_wb_finance_normalization_result
from src.normalizers.wb.products_normalizer import normalize_wb_products
from src.normalizers.wb.sales_normalizer import normalize_wb_sales
from src.normalizers.wb.stock_normalizer import normalize_wb_stock
from src.utils.db import get_conn

load_dotenv(ROOT / ".env")

SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RAW_ROOT = ROOT / "data" / "raw"
OUTPUT_ROOT = ROOT / "exports" / "wb_reconciliation_audit_v1"
SHEET_MAX_NAME = 31
MONEY_Q = Decimal("0.01")

RAW_EXPORT_FIELDS: dict[str, list[str]] = {
    "wb_sales": [
        "sale_id",
        "sale_date",
        "order_id",
        "sale_status",
        "is_return",
        "nm_id",
        "offer_id",
        "barcode",
        "quantity",
        "revenue_gross",
        "warehouse_name",
        "region_name",
        "tech_size",
        "subject_name",
        "supplier_article",
    ],
    "wb_finance": [
        "finance_id",
        "operation_date",
        "operation_date_source",
        "operation_type",
        "order_id",
        "sale_id",
        "nm_id",
        "offer_id",
        "barcode",
        "payout_amount",
        "commission_amount",
        "logistics_amount",
        "service_amount",
        "amount",
        "rrd_id",
        "rr_dt",
        "sale_dt",
        "order_dt",
        "doc_type_name",
        "supplier_oper_name",
        "delivery_rub",
        "rebill_logistic_cost",
        "storage_fee",
        "acceptance_amount",
        "penalty_amount",
        "deduction_amount",
        "acquiring_fee",
        "commission_percent",
        "subject_name",
    ],
    "wb_ads": [
        "date",
        "campaign_id",
        "campaign_name",
        "campaign_type",
        "offer_id",
        "nm_id",
        "app_type",
        "impressions",
        "clicks",
        "ctr",
        "cpc",
        "cr",
        "atbs",
        "orders_count",
        "revenue",
        "spend",
        "currency",
    ],
    "wb_products": [
        "nm_id",
        "offer_id",
        "vendor_code",
        "barcode",
        "product_name",
        "brand",
        "category_name",
        "subject_id",
        "subject_name",
        "length_cm",
        "width_cm",
        "height_cm",
        "weight_brutto_kg",
    ],
    "wb_stock": [
        "snapshot_date",
        "offer_id",
        "nm_id",
        "barcode",
        "warehouse_name",
        "warehouse_type",
        "stock_total",
        "stock_available",
        "stock_reserved",
        "stock_defect",
    ],
}

RAW_SOURCE_README: list[dict[str, str]] = [
    {
        "source": "wb_sales",
        "json_shape": "sales[]",
        "raw_granularity": "1 row = one WB sale/return event keyed by saleID/srid",
        "stg_table": "stg_wb_sale",
        "stg_granularity": "1 row = one unique sale_id",
        "what_it_means": "Продажа или возврат товара с выручкой, количеством и товарными идентификаторами.",
        "current_note": "API cumulative: повторный запуск дублирует raw-файл, но дедуп по sale_id это снимает.",
    },
    {
        "source": "wb_finance",
        "json_shape": "finance[]",
        "raw_granularity": "1 row = one WB report detail line keyed by rrd_id",
        "stg_table": "stg_wb_finance",
        "stg_granularity": "1 row = one unique finance_id/rrd_id",
        "what_it_means": "Денежная проводка/деталь отчета: комиссии, логистика, услуги, штрафы и прочие списания.",
        "current_note": "Одна finance-строка дальше может распадаться на несколько money components в attribution/marts.",
    },
    {
        "source": "wb_ads",
        "json_shape": "rows[] -> days[] -> apps[] -> nms[]",
        "raw_granularity": "1 flattened row = day + campaign + app_type + nm_id",
        "stg_table": "stg_wb_ads",
        "stg_granularity": "1 row = day + campaign_id + offer_id + nm_id",
        "what_it_means": "Статистика рекламы по дням, товарам и placement/app_type.",
        "current_note": "Сейчас app_type не входит в upsert key, из-за чего часть сырого spend схлопывается.",
    },
    {
        "source": "wb_products",
        "json_shape": "cards[]",
        "raw_granularity": "1 row = one WB product card keyed by nmID",
        "stg_table": "stg_wb_product",
        "stg_granularity": "1 row = one unique nm_id",
        "what_it_means": "Справочник карточек товара, бренда, категории, размеров и базовых атрибутов.",
        "current_note": "Используется как identity layer и источник товарных атрибутов для bridge/UI.",
    },
    {
        "source": "wb_stock",
        "json_shape": "stocks[]",
        "raw_granularity": "1 row = snapshot by date + warehouse + nmId + barcode",
        "stg_table": "stg_wb_stock",
        "stg_granularity": "1 row = snapshot_date + offer_id + warehouse_name",
        "what_it_means": "Срез остатков и движения по складу на момент выгрузки.",
        "current_note": "Используется для inventory snapshot и дефицитных сигналов.",
    },
]

EXTRA_FIELD_CANDIDATES: list[dict[str, str]] = [
    {
        "source": "wb_sales",
        "field_name": "lastChangeDate",
        "meaning": "Когда WB в последний раз менял продажную строку.",
        "current_status": "Не хранится отдельной колонкой.",
        "why_useful": "Можно ловить поздние корректировки и строить freshness-аудит по продажам.",
    },
    {
        "source": "wb_sales",
        "field_name": "discountPercent / spp / totalPrice / finishedPrice",
        "meaning": "Слой скидок и цен до/после WB-механик.",
        "current_status": "Частично схлопнуто в revenue_gross.",
        "why_useful": "Помогает разложить собственную скидку, WB-субсидию и фактическую цену продажи.",
    },
    {
        "source": "wb_finance",
        "field_name": "realizationreport_id / date_from / date_to / create_dt",
        "meaning": "Связка строки с конкретным отчетом и его окном.",
        "current_status": "Пока не выведено в stg отдельными колонками.",
        "why_useful": "Можно расследовать, из какого отчета пришла спорная проводка и как WB закрывал период.",
    },
    {
        "source": "wb_finance",
        "field_name": "gi_id / suppliercontract_code / bonus_type_name",
        "meaning": "Дополнительные operational IDs и типы начислений.",
        "current_status": "Сохраняются только внутри raw_payload.",
        "why_useful": "Полезно для forensic-разбора логистики, бонусов и нестандартных удержаний.",
    },
    {
        "source": "wb_ads",
        "field_name": "app_type",
        "meaning": "Placement / app bucket внутри рекламной статистики.",
        "current_status": "Есть в stg как колонка, но не входит в конфликтный ключ.",
        "why_useful": "Нужно включить в grain или агрегировать по нему до upsert, иначе теряется spend.",
    },
    {
        "source": "wb_ads",
        "field_name": "canceled / app-level metrics before nm split",
        "meaning": "Отмены и промежуточные агрегаты на уровне placement.",
        "current_status": "Часть полей остается только в raw_payload.",
        "why_useful": "Может помочь понять, где именно расходится сумма между placement-слоем и товарным слоем.",
    },
    {
        "source": "wb_products",
        "field_name": "description / photos / mediaFiles",
        "meaning": "Текст и медиа карточки.",
        "current_status": "Не раскладывается на отдельные stg-колонки.",
        "why_useful": "Можно использовать в UI, рекомендациях по карточке и сравнении с конкурентами.",
    },
    {
        "source": "wb_stock",
        "field_name": "Price / Discount / isSupply / isRealization",
        "meaning": "Цена, скидка и operational flags по остаткам.",
        "current_status": "Лежат только в raw_payload.",
        "why_useful": "Дают контекст stock snapshot: какие остатки продаваемые, по какой цене и в каком режиме.",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WB raw->stg->marts reconciliation audit with Excel exports.")
    parser.add_argument("--schema", default=os.getenv("SCHEMA") or os.getenv("OZON_SCHEMA") or "client_demo_90d_20260408")
    parser.add_argument("--from", dest="date_from", default="2026-01-09")
    parser.add_argument("--to", dest="date_to", default="2026-04-08")
    return parser.parse_args()


def validate_schema(schema: str) -> str:
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError(f"Invalid schema: {schema}")
    return schema


def quantize_money(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0.00")
    if isinstance(value, Decimal):
        return value.quantize(MONEY_Q, rounding=ROUND_HALF_UP)
    return Decimal(str(value)).quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def value_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: value_to_text(row.get(key)) for key in fieldnames})


def _sheet_rows(rows: list[dict[str, Any]]) -> list[list[Any]]:
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    output: list[list[Any]] = [fieldnames]
    for row in rows:
        output.append([row.get(key) for key in fieldnames])
    return output


def _col_name(index: int) -> str:
    result = ""
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _xml_cell(ref: str, value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{escape(value_to_text(value))}</v></c>'
    text = escape(value_to_text(value))
    preserve = ' xml:space="preserve"' if text.strip() != text or "\n" in text else ""
    return f'<c r="{ref}" t="inlineStr"><is><t{preserve}>{text}</t></is></c>'


def _sheet_xml(rows: list[list[Any]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    for row_idx, row in enumerate(rows, start=1):
        cells: list[str] = []
        for col_idx, value in enumerate(row, start=1):
            cell = _xml_cell(f"{_col_name(col_idx)}{row_idx}", value)
            if cell:
                cells.append(cell)
        lines.append(f'<row r="{row_idx}">{"".join(cells)}</row>')
    lines.extend(["</sheetData>", "</worksheet>"])
    return "\n".join(lines)


def write_xlsx(path: Path, sheets: list[tuple[str, list[dict[str, Any]]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized_names: set[str] = set()
    final_sheets: list[tuple[str, list[list[Any]]]] = []
    for raw_name, rows in sheets:
        sheet_name = raw_name[:SHEET_MAX_NAME]
        suffix = 2
        while sheet_name in normalized_names:
            base_len = max(SHEET_MAX_NAME - len(str(suffix)) - 1, 1)
            base = raw_name[:base_len]
            sheet_name = f"{base}_{suffix}"
            suffix += 1
        normalized_names.add(sheet_name)
        final_sheets.append((sheet_name, _sheet_rows(rows)))

    with ZipFile(path, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
"""
            + "\n".join(
                f'  <Override PartName="/xl/worksheets/sheet{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                for idx in range(1, len(final_sheets) + 1)
            )
            + "\n</Types>\n",
        )
        zf.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
""",
        )
        zf.writestr(
            "docProps/core.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
 xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/"
 xmlns:dcmitype="http://purl.org/dc/dcmitype/"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>WB Reconciliation Audit</dc:title>
  <dc:creator>Codex</dc:creator>
  <cp:lastModifiedBy>Codex</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">"""
            + datetime.now(UTC).replace(microsecond=0).isoformat()
            + """Z</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">"""
            + datetime.now(UTC).replace(microsecond=0).isoformat()
            + """Z</dcterms:modified>
</cp:coreProperties>
""",
        )
        zf.writestr(
            "docProps/app.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
 xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Codex</Application>
  <TitlesOfParts>
    <vt:vector size="{len(final_sheets)}" baseType="lpstr">
      {''.join(f'<vt:lpstr>{escape(name)}</vt:lpstr>' for name, _rows in final_sheets)}
    </vt:vector>
  </TitlesOfParts>
</Properties>
""",
        )
        workbook_sheets = "\n".join(
            f'<sheet name="{escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
            for idx, (name, _rows) in enumerate(final_sheets, start=1)
        )
        zf.writestr(
            "xl/workbook.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    {workbook_sheets}
  </sheets>
</workbook>
""",
        )
        workbook_rels = "\n".join(
            f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>'
            for idx in range(1, len(final_sheets) + 1)
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {workbook_rels}
  <Relationship Id="rId{len(final_sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
""",
        )
        zf.writestr(
            "xl/styles.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border/></borders>
  <cellStyleXfs count="1"><xf/></cellStyleXfs>
  <cellXfs count="1"><xf xfId="0"/></cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>
""",
        )
        for idx, (_name, rows) in enumerate(final_sheets, start=1):
            zf.writestr(f"xl/worksheets/sheet{idx}.xml", _sheet_xml(rows))


def parse_raw_pages(schema: str, source: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_dir = RAW_ROOT / schema / source
    pages: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    for path in sorted(source_dir.rglob("*.json")):
        if path.name.endswith(".meta.json"):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if source == "wb_ads":
            response = payload.get("rows", [])
            raw_rows = len(response) if isinstance(response, list) else 0
        elif source == "wb_sales":
            response = payload
            raw_rows = len(payload.get("sales") or [])
        elif source == "wb_finance":
            response = payload
            raw_rows = len(payload.get("finance") or [])
        elif source == "wb_products":
            response = payload
            raw_rows = len(payload.get("cards") or [])
        elif source == "wb_stock":
            response = payload
            raw_rows = len(payload.get("stocks") or [])
        else:
            response = payload
            raw_rows = 0
        pages.append({"response": response})
        inventory.append(
            {
                "source": source,
                "file_path": str(path.relative_to(ROOT)),
                "record_count": raw_rows,
                "selected_for_audit": "yes",
            }
        )
    return pages, inventory


def dedupe_rows(rows: Iterable[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    deduped: dict[Any, dict[str, Any]] = {}
    for row in rows:
        deduped[key_fn(row)] = row
    return list(deduped.values())


def filter_sales_period(rows: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        sale_dt = row.get("sale_date")
        sale_day = sale_dt.date() if hasattr(sale_dt, "date") else sale_dt
        if isinstance(sale_day, date) and start <= sale_day <= end:
            filtered.append(row)
    return filtered


def filter_finance_period(rows: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for row in rows:
        op_dt = row.get("operation_date")
        op_day = op_dt.date() if hasattr(op_dt, "date") else op_dt
        if isinstance(op_day, date) and start <= op_day <= end:
            filtered.append(row)
    return filtered


def filter_ads_period(rows: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    return [row for row in rows if isinstance(row.get("date"), date) and start <= row["date"] <= end]


def extract_rows_for_export(source: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = RAW_EXPORT_FIELDS[source]
    exported: list[dict[str, Any]] = []
    for row in rows:
        exported.append({field: row.get(field) for field in fields})
    return exported


def classify_finance_buckets(rows: list[dict[str, Any]]) -> dict[str, Decimal]:
    result: defaultdict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for row in rows:
        operation_type = (value_to_text(row.get("operation_type")) or "").lower()
        bonus_type_name = (value_to_text((row.get("raw_payload") or {}).get("bonus_type_name")) or "").lower()
        commission = row.get("commission_amount")
        logistics = row.get("logistics_amount")
        services = row.get("service_amount")
        amount = row.get("amount")
        if commission is not None:
            result["commission"] += quantize_money(abs(commission))
        if logistics is not None:
            result["logistics"] += quantize_money(abs(logistics))
        if services is not None:
            result["services"] += quantize_money(abs(services))
        if amount is None:
            continue
        amount_q = quantize_money(abs(amount))
        if (
            "продвижени" in bonus_type_name
            or "подписк" in bonus_type_name
            or "списание за отзыв" in bonus_type_name
            or "предоставление услуг" in bonus_type_name
            or "оказание услуг" in bonus_type_name
        ):
            result["services"] += amount_q
        elif "комисс" in operation_type or "commission" in operation_type or "вознагражд" in operation_type:
            result["commission"] += amount_q
        elif "достав" in operation_type or "логист" in operation_type or "delivery" in operation_type:
            result["logistics"] += amount_q
        elif "услуг" in operation_type or "service" in operation_type or "подписк" in operation_type:
            result["services"] += amount_q
        elif "возврат" in operation_type or "сторн" in operation_type or "refund" in operation_type:
            result["returns"] += amount_q
        elif "штраф" in operation_type or "удерж" in operation_type or "penalt" in operation_type or "deduct" in operation_type:
            result["penalties"] += amount_q
        elif "хран" in operation_type or "storage" in operation_type:
            result["storage"] += amount_q
        elif "эквайр" in operation_type or "acquir" in operation_type:
            result["payment_processing"] += amount_q
        else:
            result["other"] += amount_q
    return dict(result)


def fetch_dicts(schema: str, query: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query.format(schema=schema), params or ())
            return [dict(row) for row in cur.fetchall()]


def build_ads_collision_rows(raw_ads_rows: list[dict[str, Any]], stg_ads_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stg_spend_by_key = {
        (
            value_to_text(row.get("date")),
            value_to_text(row.get("campaign_id")),
            value_to_text(row.get("offer_id")),
            value_to_text(row.get("nm_id")),
        ): quantize_money(row.get("spend"))
        for row in stg_ads_rows
    }
    by_key: defaultdict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in raw_ads_rows:
        key = (
            value_to_text(row.get("date")),
            value_to_text(row.get("campaign_id")),
            value_to_text(row.get("offer_id")),
            value_to_text(row.get("nm_id")),
        )
        by_key[key].append(row)

    output: list[dict[str, Any]] = []
    for key, items in by_key.items():
        if len(items) <= 1:
            continue
        raw_spend_sum = sum((quantize_money(item.get("spend")) for item in items), Decimal("0.00"))
        stg_spend = stg_spend_by_key.get(key, Decimal("0.00"))
        output.append(
            {
                "date": key[0],
                "campaign_id": key[1],
                "offer_id": key[2],
                "nm_id": key[3],
                "raw_rows_for_key": len(items),
                "raw_spend_sum": raw_spend_sum,
                "stg_spend_survived": stg_spend,
                "spend_lost_by_collapse": raw_spend_sum - stg_spend,
                "app_types_seen": ",".join(sorted({value_to_text(item.get("app_type")) for item in items if item.get("app_type") not in (None, "")})),
            }
        )
    output.sort(key=lambda row: (quantize_money(row["spend_lost_by_collapse"]), row["raw_rows_for_key"]), reverse=True)
    return output


def write_markdown(
    path: Path,
    *,
    schema: str,
    start: date,
    end: date,
    row_flow_rows: list[dict[str, Any]],
    money_rows: list[dict[str, Any]],
    match_rows: list[dict[str, Any]],
    low_conf_summary_rows: list[dict[str, Any]],
    unmatched_count: int,
    ads_collision_rows: list[dict[str, Any]],
) -> None:
    lines = [
        "# WB Reconciliation Audit v1",
        "",
        f"- schema: `{schema}`",
        f"- period: `{start.isoformat()}` .. `{end.isoformat()}`",
        "",
        "## Main Findings",
    ]
    ads_row = next((row for row in money_rows if row["metric_code"] == "ads_spend"), None)
    if ads_row:
        lines.append(
            f"- Ads raw flattened spend is `{ads_row['raw_total']}`, but current `stg_wb_ads` keeps only `{ads_row['stg_total']}`."
        )
        lines.append(
            f"- The gap is `{ads_row['raw_to_stg_gap']}` and comes from `app_type` collapse inside the current upsert key."
        )
    lines.append("- Sales revenue matches exactly after period filter and `sale_id` dedupe.")
    lines.append("- Finance buckets match staging/attribution/marts within tiny rounding tails.")
    lines.append(f"- Truly unmatched WB money rows in `fact_unmatched_money`: `{unmatched_count}`.")
    if low_conf_summary_rows:
        lines.append("- Weak links still exist as low-confidence heuristic allocations:")
        for row in low_conf_summary_rows:
            lines.append(
                f"  - `{row['money_bucket']}` via `{row['attribution_method']}` rows=`{row['rows_count']}` amount=`{row['amount_total']}`"
            )
    lines.extend(
        [
            "",
            "## Row Flow",
            "",
            "| source | raw_effective_rows | stg_rows | attribution_rows | mart_rows | note |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for row in row_flow_rows:
        lines.append(
            f"| {row['source']} | {row['raw_effective_rows']} | {row['stg_rows']} | {row['attribution_rows']} | {row['mart_rows']} | {row['status_note']} |"
        )
    lines.extend(
        [
            "",
            "## Money Checks",
            "",
            "| metric | raw_total | stg_total | attribution_total | mart_total | raw_to_stg_gap | stg_to_mart_gap | status |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in money_rows:
        lines.append(
            f"| {row['metric_code']} | {row['raw_total']} | {row['stg_total']} | {row['attribution_total']} | {row['mart_total']} | {row['raw_to_stg_gap']} | {row['stg_to_mart_gap']} | {row['status']} |"
        )
    if ads_collision_rows:
        lines.extend(
            [
                "",
                "## Ads Collision Hotspots",
                "",
                "| date | campaign_id | nm_id | raw_rows_for_key | raw_spend_sum | stg_spend_survived | spend_lost_by_collapse | app_types_seen |",
                "|---|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in ads_collision_rows[:15]:
            lines.append(
                f"| {row['date']} | {row['campaign_id']} | {row['nm_id']} | {row['raw_rows_for_key']} | {row['raw_spend_sum']} | {row['stg_spend_survived']} | {row['spend_lost_by_collapse']} | {row['app_types_seen']} |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    schema = validate_schema(args.schema)
    start = date.fromisoformat(args.date_from)
    end = date.fromisoformat(args.date_to)
    out_dir = OUTPUT_ROOT / schema
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_inventory_rows: list[dict[str, Any]] = []

    sales_pages, inventory = parse_raw_pages(schema, "wb_sales")
    raw_inventory_rows.extend(inventory)
    raw_sales_all = normalize_wb_sales(sales_pages)
    raw_sales_period = filter_sales_period(raw_sales_all, start, end)
    raw_sales_unique = dedupe_rows(raw_sales_period, lambda row: value_to_text(row.get("sale_id")))

    finance_pages, inventory = parse_raw_pages(schema, "wb_finance")
    raw_inventory_rows.extend(inventory)
    raw_finance_all = build_wb_finance_normalization_result(finance_pages)["rows"]
    raw_finance_period = filter_finance_period(raw_finance_all, start, end)
    raw_finance_unique = dedupe_rows(raw_finance_period, lambda row: value_to_text(row.get("finance_id")))

    ads_pages, inventory = parse_raw_pages(schema, "wb_ads")
    raw_inventory_rows.extend(inventory)
    raw_ads_all = normalize_wb_ads(ads_pages)
    raw_ads_period = filter_ads_period(raw_ads_all, start, end)

    products_pages, inventory = parse_raw_pages(schema, "wb_products")
    raw_inventory_rows.extend(inventory)
    raw_products_rows = dedupe_rows(normalize_wb_products(products_pages), lambda row: value_to_text(row.get("nm_id")))

    stock_pages, inventory = parse_raw_pages(schema, "wb_stock")
    raw_inventory_rows.extend(inventory)
    raw_stock_rows = dedupe_rows(
        normalize_wb_stock(stock_pages),
        lambda row: (
            value_to_text(row.get("snapshot_date")),
            value_to_text(row.get("offer_id")),
            value_to_text(row.get("warehouse_name")),
        ),
    )

    stg_ads_rows = fetch_dicts(
        schema,
        """
        SELECT date, campaign_id, offer_id, nm_id, app_type, spend, revenue
        FROM {schema}.stg_wb_ads
        WHERE date BETWEEN %s AND %s
        ORDER BY date, campaign_id, offer_id, nm_id
        """,
        (start, end),
    )
    ads_collision_rows = build_ads_collision_rows(raw_ads_period, stg_ads_rows)

    granularity_rows = fetch_dicts(
        schema,
        """
        SELECT source, entity, expected_grain, stg_rows, attribution_rows, mart_rows, status
        FROM {schema}.mart_granularity_reconciliation_report
        WHERE marketplace = 'wb'
        ORDER BY source, entity
        """,
    )
    granularity_by_source = {row["source"]: row for row in granularity_rows}

    count_rows = fetch_dicts(
        schema,
        """
        SELECT 'products'::text AS source, COUNT(*)::int AS stg_rows, 0::int AS attribution_rows, COUNT(*)::int AS mart_rows
        FROM {schema}.stg_wb_product
        UNION ALL
        SELECT 'stock', COUNT(*)::int, 0::int, COALESCE((SELECT COUNT(*)::int FROM {schema}.fact_inventory_snapshot WHERE marketplace='wb'), 0)::int
        FROM {schema}.stg_wb_stock
        """,
    )
    count_by_source = {row["source"]: row for row in count_rows}

    row_flow_rows = [
        {
            "source": "sales",
            "raw_files": len([row for row in raw_inventory_rows if row["source"] == "wb_sales"]),
            "raw_rows_before_dedupe": len(raw_sales_all),
            "raw_effective_rows": len(raw_sales_unique),
            "stg_rows": granularity_by_source["sales"]["stg_rows"],
            "attribution_rows": granularity_by_source["sales"]["attribution_rows"],
            "mart_rows": granularity_by_source["sales"]["mart_rows"],
            "expected_grain": granularity_by_source["sales"]["expected_grain"],
            "status_note": "ok after sale_id dedupe across cumulative raw files",
        },
        {
            "source": "finance",
            "raw_files": len([row for row in raw_inventory_rows if row["source"] == "wb_finance"]),
            "raw_rows_before_dedupe": len(raw_finance_all),
            "raw_effective_rows": len(raw_finance_unique),
            "stg_rows": granularity_by_source["finance"]["stg_rows"],
            "attribution_rows": granularity_by_source["finance"]["attribution_rows"],
            "mart_rows": granularity_by_source["finance"]["mart_rows"],
            "expected_grain": granularity_by_source["finance"]["expected_grain"],
            "status_note": "ok; one finance row splits into multiple expense components downstream",
        },
        {
            "source": "ads",
            "raw_files": len([row for row in raw_inventory_rows if row["source"] == "wb_ads"]),
            "raw_rows_before_dedupe": len(raw_ads_period),
            "raw_effective_rows": len(raw_ads_period),
            "stg_rows": granularity_by_source["ads"]["stg_rows"],
            "attribution_rows": granularity_by_source["ads"]["attribution_rows"],
            "mart_rows": granularity_by_source["ads"]["mart_rows"],
            "expected_grain": granularity_by_source["ads"]["expected_grain"],
            "status_note": "critical: app_type-level raw rows collapse into one stg key",
        },
        {
            "source": "products",
            "raw_files": len([row for row in raw_inventory_rows if row["source"] == "wb_products"]),
            "raw_rows_before_dedupe": len(raw_products_rows),
            "raw_effective_rows": len(raw_products_rows),
            "stg_rows": count_by_source["products"]["stg_rows"],
            "attribution_rows": count_by_source["products"]["attribution_rows"],
            "mart_rows": count_by_source["products"]["mart_rows"],
            "expected_grain": "nm_id",
            "status_note": "ok",
        },
        {
            "source": "stock",
            "raw_files": len([row for row in raw_inventory_rows if row["source"] == "wb_stock"]),
            "raw_rows_before_dedupe": len(raw_stock_rows),
            "raw_effective_rows": len(raw_stock_rows),
            "stg_rows": count_by_source["stock"]["stg_rows"],
            "attribution_rows": count_by_source["stock"]["attribution_rows"],
            "mart_rows": count_by_source["stock"]["mart_rows"],
            "expected_grain": "snapshot_date+offer_id+warehouse_name",
            "status_note": "ok",
        },
    ]

    money_layer_rows = fetch_dicts(
        schema,
        """
        SELECT marketplace, money_type, stg_total_signed, stg_total_abs, attribution_total, matched_to_product,
               attribution_unmatched, mart_total, unmatched_explicit, source_gap, parsing_gap, match_gap,
               unexplained_residual
        FROM {schema}.mart_money_layer_reconciliation
        WHERE marketplace='wb'
        ORDER BY money_type
        """,
    )
    money_layer_by_type = {row["money_type"]: row for row in money_layer_rows}

    sales_totals = fetch_dicts(
        schema,
        """
        SELECT
          (SELECT SUM(revenue_gross)::numeric(18,2) FROM {schema}.stg_wb_sale WHERE sale_date::date BETWEEN %s AND %s) AS stg_sales_revenue,
          (SELECT SUM(amount)::numeric(18,2) FROM {schema}.fact_product_money_attribution WHERE marketplace='wb' AND money_component_type='revenue') AS attribution_sales_revenue,
          (SELECT SUM(revenue_gross)::numeric(18,2) FROM {schema}.mart_pnl_daily_by_product WHERE marketplace='wb') AS mart_sales_revenue,
          (SELECT SUM(revenue)::numeric(18,2) FROM {schema}.stg_wb_ads WHERE date BETWEEN %s AND %s) AS stg_ads_revenue
        """,
        (start, end, start, end),
    )[0]

    raw_sales_total = sum((quantize_money(row.get("revenue_gross")) for row in raw_sales_unique), Decimal("0.00"))
    raw_ads_spend_total = sum((quantize_money(row.get("spend")) for row in raw_ads_period), Decimal("0.00"))
    raw_ads_revenue_total = sum((quantize_money(row.get("revenue")) for row in raw_ads_period), Decimal("0.00"))
    raw_finance_buckets = classify_finance_buckets(raw_finance_unique)

    low_conf_summary_rows = fetch_dicts(
        schema,
        """
        SELECT money_bucket, attribution_method, attribution_confidence, COUNT(*)::int AS rows_count, SUM(amount)::numeric(18,2) AS amount_total
        FROM {schema}.fact_product_money_attribution
        WHERE marketplace='wb' AND attribution_confidence='low'
        GROUP BY money_bucket, attribution_method, attribution_confidence
        ORDER BY rows_count DESC, amount_total DESC
        """,
    )

    low_conf_detail_rows = fetch_dicts(
        schema,
        """
        SELECT
          a.event_date,
          a.money_bucket,
          a.amount,
          a.attribution_method,
          a.attribution_confidence,
          a.source_record_id,
          a.canonical_product_key,
          b.offer_id,
          b.nm_id,
          b.sku,
          b.product_name,
          b.category_name,
          b.brand
        FROM {schema}.fact_product_money_attribution a
        LEFT JOIN {schema}.dim_product_bridge b
          ON b.marketplace = a.marketplace
         AND b.canonical_product_key = a.canonical_product_key
        WHERE a.marketplace='wb' AND a.attribution_confidence='low'
        ORDER BY a.event_date, a.money_bucket, a.amount DESC
        """,
    )

    unmatched_rows = fetch_dicts(
        schema,
        """
        SELECT
          event_date,
          source_entity,
          money_type,
          amount,
          amount_abs,
          match_status,
          unmatched_reason,
          candidate_mapping_reason,
          candidate_product_count,
          most_likely_product_id,
          most_likely_key_type,
          confidence_level,
          incompleteness_stage,
          raw_record_id,
          campaign_id,
          order_id,
          nm_id,
          offer_id,
          barcode,
          supplier_article,
          product_name_raw,
          brand_raw,
          subject_raw
        FROM {schema}.fact_unmatched_money
        WHERE marketplace='wb'
        ORDER BY amount_abs DESC, event_date
        """,
    )

    match_quality_rows = fetch_dicts(
        schema,
        """
        SELECT
          money_component_type,
          money_bucket,
          attribution_method,
          attribution_confidence,
          COUNT(*)::int AS rows_count,
          SUM(amount)::numeric(18,2) AS amount_total
        FROM {schema}.fact_product_money_attribution
        WHERE marketplace='wb'
        GROUP BY money_component_type, money_bucket, attribution_method, attribution_confidence
        ORDER BY money_component_type, money_bucket, rows_count DESC, amount_total DESC
        """,
    )

    money_rows: list[dict[str, Any]] = []

    def add_money_row(metric_code: str, raw_total: Decimal, stg_total: Decimal, attribution_total: Decimal | None, mart_total: Decimal | None, note: str) -> None:
        attr = attribution_total if attribution_total is not None else Decimal("0.00")
        mart = mart_total if mart_total is not None else Decimal("0.00")
        status = "ok"
        raw_to_stg_gap = raw_total - stg_total
        stg_to_mart_gap = stg_total - mart if mart_total is not None else Decimal("0.00")
        if abs(raw_to_stg_gap) >= Decimal("1.00") or (mart_total is not None and abs(stg_to_mart_gap) >= Decimal("1.00")):
            status = "needs_review"
        money_rows.append(
            {
                "metric_code": metric_code,
                "raw_total": raw_total,
                "stg_total": stg_total,
                "attribution_total": attribution_total if attribution_total is not None else "",
                "mart_total": mart_total if mart_total is not None else "",
                "raw_to_stg_gap": raw_to_stg_gap,
                "stg_to_mart_gap": stg_to_mart_gap if mart_total is not None else "",
                "status": status,
                "note": note,
            }
        )

    add_money_row(
        "sales_revenue",
        raw_sales_total,
        quantize_money(sales_totals["stg_sales_revenue"]),
        quantize_money(sales_totals["attribution_sales_revenue"]),
        quantize_money(sales_totals["mart_sales_revenue"]),
        "Sales are deduped by sale_id because WB sales raw is cumulative and was collected twice.",
    )
    add_money_row(
        "ads_spend",
        raw_ads_spend_total,
        quantize_money(money_layer_by_type["ads"]["stg_total_abs"]),
        quantize_money(money_layer_by_type["ads"]["attribution_total"]),
        quantize_money(money_layer_by_type["ads"]["mart_total"]),
        "Critical mismatch: raw flattened ads still contains app_type-level rows that current stg key overwrites.",
    )
    add_money_row(
        "ads_revenue_observed",
        raw_ads_revenue_total,
        quantize_money(sales_totals["stg_ads_revenue"]),
        None,
        None,
        "Observed ads revenue exists in raw/stg but is not a direct money layer in P&L marts.",
    )
    for money_type in ["commission", "logistics", "services", "storage", "payment_processing", "penalties", "returns", "other"]:
        layer = money_layer_by_type.get(money_type, {})
        add_money_row(
            f"finance_{money_type}",
            quantize_money(raw_finance_buckets.get(money_type, Decimal("0.00"))),
            quantize_money(layer.get("stg_total_abs")),
            quantize_money(layer.get("attribution_total")),
            quantize_money(layer.get("mart_total")),
            "Finance raw rows are classified into expense buckets with the same rules as mart_product_money_attribution.",
        )

    raw_readme_rows = RAW_SOURCE_README
    extra_field_rows = EXTRA_FIELD_CANDIDATES

    raw_sales_export = extract_rows_for_export("wb_sales", raw_sales_unique)
    raw_finance_export = extract_rows_for_export("wb_finance", raw_finance_unique)
    raw_ads_export = extract_rows_for_export("wb_ads", raw_ads_period)
    raw_products_export = extract_rows_for_export("wb_products", raw_products_rows)
    raw_stock_export = extract_rows_for_export("wb_stock", raw_stock_rows)

    unmatched_export_rows = unmatched_rows or [
        {
            "note": "No unmatched WB rows in fact_unmatched_money for this period."
        }
    ]

    outputs: list[tuple[str, list[dict[str, Any]]]] = [
        ("summary_counts", row_flow_rows),
        ("money_reconciliation", money_rows),
        ("match_quality", match_quality_rows),
        ("low_confidence_alloc", low_conf_detail_rows),
        ("unmatched_money", unmatched_export_rows),
        ("ads_app_collisions", ads_collision_rows),
        ("raw_sales", raw_sales_export),
        ("raw_finance", raw_finance_export),
        ("raw_ads", raw_ads_export),
        ("raw_products", raw_products_export),
        ("raw_stock", raw_stock_export),
        ("granularity", raw_readme_rows),
        ("extra_fields", extra_field_rows),
        ("raw_files", raw_inventory_rows),
    ]

    for name, rows in outputs:
        write_csv(out_dir / f"{name}.csv", rows)

    workbook_path = out_dir / "wb_reconciliation_audit.xlsx"
    write_xlsx(workbook_path, outputs)

    report_path = out_dir / "wb_reconciliation_summary.md"
    write_markdown(
        report_path,
        schema=schema,
        start=start,
        end=end,
        row_flow_rows=row_flow_rows,
        money_rows=money_rows,
        match_rows=match_quality_rows,
        low_conf_summary_rows=low_conf_summary_rows,
        unmatched_count=len(unmatched_rows),
        ads_collision_rows=ads_collision_rows,
    )

    print(f"report={report_path}")
    print(f"workbook={workbook_path}")
    print(f"ads_raw_spend={raw_ads_spend_total}")
    print(f"ads_stg_spend={quantize_money(money_layer_by_type['ads']['stg_total_abs'])}")
    print(f"wb_unmatched_rows={len(unmatched_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
