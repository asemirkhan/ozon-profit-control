#!/usr/bin/env python3
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from build_profit_control_simulation_dataset_v1 import (
    SCHEMA,
    build_window,
    load_ads_period_metrics,
    load_group_map,
    load_product_period_operational,
    load_product_rows,
    load_stock_metrics,
    q2,
    write_csv,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "artifacts" / "profit_control_identity_normalization_stage_v1"
REPORT_PATH = ROOT / "profit_control_identity_normalization_stage_v1.md"

MAPPING_CSV = OUTPUT_DIR / "normalized_product_key_mapping.csv"
COLLAPSE_CSV = OUTPUT_DIR / "normalized_product_key_collapse_map.csv"
TOP_MINUS_CSV = OUTPUT_DIR / "top_minus_products_normalized.csv"
TOP_PLUS_CSV = OUTPUT_DIR / "top_plus_products_normalized.csv"
CANDIDATES_CSV = OUTPUT_DIR / "normalized_product_card_candidates.csv"
SUMMARY_CSV = OUTPUT_DIR / "normalization_summary.csv"

MONEY = Decimal("0.01")


@dataclass(frozen=True)
class NormalizedMeta:
    normalized_product_key: str
    normalized_canonical_product_key: str
    normalized_offer_id: str
    normalized_sku: str


def norm_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def is_placeholder(value: str) -> bool:
    return value in ("", "~")


def normalized_meta(row: dict[str, Any]) -> NormalizedMeta:
    normalized_cpk = norm_text(row["canonical_product_key"])
    normalized_offer = norm_text(row["offer_id"])
    normalized_sku = norm_text(row["sku"])
    if not is_placeholder(normalized_sku):
        key = f"wb:norm:sku:{normalized_sku}"
    elif not is_placeholder(normalized_offer):
        key = f"wb:norm:offer:{normalized_offer}"
    else:
        key = f"wb:norm:cpk:{normalized_cpk}"
    return NormalizedMeta(
        normalized_product_key=key,
        normalized_canonical_product_key=normalized_cpk,
        normalized_offer_id=normalized_offer,
        normalized_sku=normalized_sku,
    )


def row_activity_score(row: dict[str, Any]) -> Decimal:
    return (
        abs(q2(row["revenue_prev"]))
        + abs(q2(row["revenue_curr"]))
        + abs(q2(row["result_prev"]))
        + abs(q2(row["result_curr"]))
    )


def choose_representative_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def score(row: dict[str, Any]) -> tuple[int, int, int, Decimal]:
        name = str(row.get("product_name") or "")
        offer = norm_text(row.get("offer_id"))
        cpk = norm_text(row.get("canonical_product_key"))
        return (
            1 if name and name != "~" else 0,
            1 if not is_placeholder(offer) else 0,
            0 if cpk.startswith("wb:sku:") else 1,
            row_activity_score(row),
        )

    return max(rows, key=score)


def choose_group_name(rows: list[dict[str, Any]]) -> str:
    ranked = sorted(
        rows,
        key=lambda row: (
            1 if str(row.get("group_name") or "") not in ("", "~") else 0,
            row_activity_score(row),
        ),
        reverse=True,
    )
    for row in ranked:
        group_name = str(row.get("group_name") or "~")
        if group_name not in ("", "~"):
            return group_name
    return "~"


def merge_reason(rows: list[dict[str, Any]]) -> str:
    cpks = {str(row["canonical_product_key"]) for row in rows}
    non_placeholder_cpks = {
        str(row["canonical_product_key"])
        for row in rows
        if not norm_text(row["canonical_product_key"]).startswith("wb:sku:")
    }
    offers = {str(row["offer_id"]) for row in rows if not is_placeholder(norm_text(row["offer_id"]))}
    lower_cpks = {
        norm_text(row["canonical_product_key"])
        for row in rows
        if not norm_text(row["canonical_product_key"]).startswith("wb:sku:")
    }
    lower_offers = {norm_text(row["offer_id"]) for row in rows if not is_placeholder(norm_text(row["offer_id"]))}
    has_sku_placeholder = any(norm_text(row["canonical_product_key"]).startswith("wb:sku:") for row in rows)
    has_offer_row = any(not norm_text(row["canonical_product_key"]).startswith("wb:sku:") for row in rows)

    reasons: list[str] = []
    if len(non_placeholder_cpks) > 1 and len(lower_cpks) == 1:
        reasons.append("canonical_case_only")
    if len(offers) > 1 and len(lower_offers) == 1:
        reasons.append("offer_case_only")
    if has_sku_placeholder and has_offer_row:
        reasons.append("sku_placeholder_bridge")
    if not reasons:
        reasons.append("sku_level_merge")
    return "+".join(reasons)


def build_mapping_rows(products: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in products:
        meta = normalized_meta(row)
        enriched = dict(row)
        enriched["normalized_product_key"] = meta.normalized_product_key
        enriched["normalized_canonical_product_key"] = meta.normalized_canonical_product_key
        enriched["normalized_offer_id"] = meta.normalized_offer_id or "~"
        enriched["normalized_sku"] = meta.normalized_sku or "~"
        grouped[meta.normalized_product_key].append(enriched)

    mapping_rows: list[dict[str, Any]] = []
    for norm_key, rows in grouped.items():
        reason = merge_reason(rows)
        representative = choose_representative_row(rows)
        for row in sorted(
            rows,
            key=lambda item: (
                norm_text(item["canonical_product_key"]),
                norm_text(item["offer_id"]),
                norm_text(item["sku"]),
            ),
        ):
            mapping_rows.append(
                {
                    "normalized_product_key": norm_key,
                    "merge_reason": reason,
                    "old_canonical_product_key": row["canonical_product_key"],
                    "old_offer_id": row["offer_id"],
                    "old_sku": row["sku"],
                    "old_product_name": row["product_name"],
                    "normalized_canonical_product_key": row["normalized_canonical_product_key"],
                    "normalized_offer_id": row["normalized_offer_id"],
                    "normalized_sku": row["normalized_sku"],
                    "representative_product_name": representative["product_name"],
                    "representative_offer_id": representative["offer_id"],
                    "result_prev_before": f"{q2(row['result_prev']):.2f}",
                    "result_curr_before": f"{q2(row['result_curr']):.2f}",
                    "result_delta_before": f"{q2(row['result_delta']):.2f}",
                    "revenue_prev_before": f"{q2(row['revenue_prev']):.2f}",
                    "revenue_curr_before": f"{q2(row['revenue_curr']):.2f}",
                    "revenue_delta_before": f"{q2(row['revenue_delta']):.2f}",
                    "expense_delta_total_before": f"{q2(row['expense_delta_total']):.2f}",
                }
            )
    return mapping_rows, grouped


def aggregate_normalized_products(grouped: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    sum_fields = (
        "revenue_prev",
        "revenue_curr",
        "ads_prev",
        "ads_curr",
        "commission_prev",
        "commission_curr",
        "logistics_prev",
        "logistics_curr",
        "storage_prev",
        "storage_curr",
        "acquiring_prev",
        "acquiring_curr",
        "returns_prev",
        "returns_curr",
        "other_product_expenses_prev",
        "other_product_expenses_curr",
        "result_prev",
        "result_curr",
        "result_delta",
        "revenue_delta",
        "expense_delta_total",
    )
    for norm_key, rows in grouped.items():
        representative = choose_representative_row(rows)
        agg: dict[str, Any] = {
            "normalized_product_key": norm_key,
            "canonical_product_key": norm_key,
            "offer_id": representative["offer_id"],
            "sku": representative["sku"],
            "product_name": representative["product_name"],
            "group_name": choose_group_name(rows),
            "merge_reason": merge_reason(rows),
            "source_row_count": len(rows),
            "source_keys": " || ".join(
                sorted(f"{row['canonical_product_key']} | {row['offer_id']} | {row['sku']}" for row in rows)
            ),
        }
        for field in sum_fields:
            agg[field] = sum(q2(row[field]) for row in rows)
        out.append(agg)
    return out


def build_collapse_rows(grouped: dict[str, list[dict[str, Any]]], normalized_products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = {row["normalized_product_key"]: row for row in normalized_products}
    rows: list[dict[str, Any]] = []
    for norm_key, members in grouped.items():
        if len(members) < 2:
            continue
        before_old_keys = " || ".join(
            sorted(f"{row['canonical_product_key']} | {row['offer_id']} | {row['sku']}" for row in members)
        )
        product = indexed[norm_key]
        signs = {q2(row["result_delta"]).copy_sign(Decimal("1")) if q2(row["result_delta"]) != 0 else Decimal("0") for row in members}
        has_mixed_sign = any(q2(row["result_delta"]) < 0 for row in members) and any(q2(row["result_delta"]) > 0 for row in members)
        rows.append(
            {
                "normalized_product_key": norm_key,
                "merge_reason": merge_reason(members),
                "source_row_count": len(members),
                "old_keys_collapsed": before_old_keys,
                "representative_offer_id": product["offer_id"],
                "representative_sku": product["sku"],
                "representative_product_name": product["product_name"],
                "result_prev_after": f"{q2(product['result_prev']):.2f}",
                "result_curr_after": f"{q2(product['result_curr']):.2f}",
                "result_delta_after": f"{q2(product['result_delta']):.2f}",
                "revenue_prev_after": f"{q2(product['revenue_prev']):.2f}",
                "revenue_curr_after": f"{q2(product['revenue_curr']):.2f}",
                "revenue_delta_after": f"{q2(product['revenue_delta']):.2f}",
                "expense_delta_total_after": f"{q2(product['expense_delta_total']):.2f}",
                "mixed_sign_before_merge": "yes" if has_mixed_sign else "no",
            }
        )
    return sorted(rows, key=lambda row: Decimal(row["result_delta_after"]))


def build_top_rows(normalized_products: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    minus = sorted(normalized_products, key=lambda item: (item["result_delta"], item["revenue_delta"]))[:10]
    plus = sorted(normalized_products, key=lambda item: (item["result_delta"], item["revenue_delta"]), reverse=True)[:10]
    minus_rows = [
        {
            "normalized_product_key": item["normalized_product_key"],
            "offer_id": item["offer_id"],
            "sku": item["sku"],
            "product_name": item["product_name"],
            "group_name": item["group_name"],
            "source_row_count": item["source_row_count"],
            "result_prev": f"{q2(item['result_prev']):.2f}",
            "result_curr": f"{q2(item['result_curr']):.2f}",
            "result_delta": f"{q2(item['result_delta']):.2f}",
            "revenue_prev": f"{q2(item['revenue_prev']):.2f}",
            "revenue_curr": f"{q2(item['revenue_curr']):.2f}",
            "revenue_delta": f"{q2(item['revenue_delta']):.2f}",
            "expense_delta_total": f"{q2(item['expense_delta_total']):.2f}",
            "merge_reason": item["merge_reason"],
        }
        for item in minus
    ]
    plus_rows = [
        {
            "normalized_product_key": item["normalized_product_key"],
            "offer_id": item["offer_id"],
            "sku": item["sku"],
            "product_name": item["product_name"],
            "group_name": item["group_name"],
            "source_row_count": item["source_row_count"],
            "result_prev": f"{q2(item['result_prev']):.2f}",
            "result_curr": f"{q2(item['result_curr']):.2f}",
            "result_delta": f"{q2(item['result_delta']):.2f}",
            "revenue_prev": f"{q2(item['revenue_prev']):.2f}",
            "revenue_curr": f"{q2(item['revenue_curr']):.2f}",
            "revenue_delta": f"{q2(item['revenue_delta']):.2f}",
            "expense_delta_total": f"{q2(item['expense_delta_total']):.2f}",
            "merge_reason": item["merge_reason"],
        }
        for item in plus
    ]
    return minus_rows, plus_rows


def build_candidate_rows(normalized_products: list[dict[str, Any]], window: Any) -> list[dict[str, Any]]:
    eligible = [
        item
        for item in normalized_products
        if not is_placeholder(norm_text(item["offer_id"]))
        or not is_placeholder(norm_text(item["product_name"]))
    ]
    selected = sorted(eligible, key=lambda item: item["result_delta"])[:5] + sorted(
        eligible, key=lambda item: item["result_delta"], reverse=True
    )[:3]
    ads = load_ads_period_metrics(window)
    ops = load_product_period_operational(window)

    stock_selected = [
        {
            "sku": item["sku"],
            "offer_id": item["offer_id"],
        }
        for item in selected
    ]
    stock = load_stock_metrics(stock_selected, window)
    rows: list[dict[str, Any]] = []
    for item in selected:
        sku = item["sku"]
        prev_ops = ops.get((sku, "previous"), {})
        curr_ops = ops.get((sku, "current"), {})
        prev_ads = ads.get((sku, "previous"), {})
        curr_ads = ads.get((sku, "current"), {})
        curr_stock = stock.get((sku, "current"), {})
        rows.append(
            {
                "direction": "problem" if q2(item["result_delta"]) < 0 else "growing",
                "normalized_product_key": item["normalized_product_key"],
                "offer_id": item["offer_id"],
                "sku": item["sku"],
                "product_name": item["product_name"],
                "group_name": item["group_name"],
                "source_row_count": item["source_row_count"],
                "result_delta": f"{q2(item['result_delta']):.2f}",
                "orders_prev": f"{q2(prev_ops.get('orders_count')):.2f}",
                "orders_curr": f"{q2(curr_ops.get('orders_count')):.2f}",
                "sales_units_prev": f"{q2(prev_ops.get('units_sold')):.2f}",
                "sales_units_curr": f"{q2(curr_ops.get('units_sold')):.2f}",
                "ads_spend_prev": f"{q2(prev_ops.get('ads_amount')):.2f}",
                "ads_spend_curr": f"{q2(curr_ops.get('ads_amount')):.2f}",
                "ad_attributed_orders_prev": f"{q2(prev_ads.get('ad_attributed_orders')):.2f}",
                "ad_attributed_orders_curr": f"{q2(curr_ads.get('ad_attributed_orders')):.2f}",
                "stock_days_limited_curr": int(curr_stock.get("stock_days_limited", 0)),
                "merge_reason": item["merge_reason"],
            }
        )
    return rows


def md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_no data_"
    header = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = []
    for row in rows:
        body.append("| " + " | ".join(str(row.get(column, "")).replace("|", "\\|") for column in columns) + " |")
    return "\n".join([header, sep, *body])


def write_markdown_report(
    window: Any,
    summary_rows: list[dict[str, Any]],
    collapse_rows: list[dict[str, Any]],
    minus_rows: list[dict[str, Any]],
    plus_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    duplicate_conflicts_fixed: int,
    merged_groups: int,
    rows_in_merged_groups: int,
    old_count: int,
    new_count: int,
) -> str:
    problem_candidates = [row for row in candidate_rows if row["direction"] == "problem"]
    growing_candidates = [row for row in candidate_rows if row["direction"] == "growing"]
    return "\n".join(
        [
            "# Этап. Нормализация идентичности товаров и пересборка вкладов в результат",
            "",
            "## 1. Цель этапа",
            "Исправить разъезд одного и того же физического товара на несколько сущностей из-за регистра и sku-placeholder рядов, затем заново собрать вклады товаров в `result_delta` без изменения кабинета, старых marts и магазинной бизнес-метрики.",
            "",
            "## 2. Что сделано",
            "- Проверены дубли по `lower(canonical_product_key)` и `lower(offer_id)`.",
            "- Проверены SKU-группы, где один и тот же `sku` жил под несколькими `canonical_product_key / offer_id`.",
            "- Введен новый технический ключ `normalized_product_key` для симуляции и будущего анализа.",
            "- Пересобраны `result_prev`, `result_curr`, `result_delta`, `revenue_prev`, `revenue_curr`, `revenue_delta`, `expense_delta_total` по нормализованному товару.",
            "- Пересобраны топ-минус, топ-плюс и кандидаты для карточек товара.",
            "- Собрана карта схлопывания старых ключей в новые нормализованные сущности.",
            "",
            "## 3. Что не трогалось",
            "- Старый кабинет не менялся.",
            "- Старые marts не менялись.",
            "- Магазинная итоговая метрика `profit_after_ads` не менялась.",
            "- Рекламная логика и paid/non-paid split не переписывались.",
            "",
            "## 4. Главный результат",
            f"После нормализации один физический товар больше не сидит одновременно в плюсах и в минусах из-за регистра или sku-placeholder рядов. Новый `normalized_product_key` схлопнул {merged_groups} merge-групп ({rows_in_merged_groups} старых строк) и убрал как минимум {duplicate_conflicts_fixed} прямых конфликтных случаев, где внутри одного физического товара до нормализации одновременно были и плюсовые, и минусовые вклады.",
            "",
            "## 5. Ключевые цифры",
            md_table(
                summary_rows,
                ["Показатель", "Значение"],
            ),
            "",
            "## 6. Главные таблицы",
            "### Таблица 1. Карта схлопывания старых ключей в normalized_product_key",
            md_table(
                collapse_rows,
                [
                    "normalized_product_key",
                    "merge_reason",
                    "source_row_count",
                    "representative_sku",
                    "representative_product_name",
                    "result_delta_after",
                    "mixed_sign_before_merge",
                ],
            ),
            "",
            "### Таблица 2. Новый топ-10 минусовых товаров",
            md_table(
                minus_rows,
                [
                    "normalized_product_key",
                    "offer_id",
                    "sku",
                    "product_name",
                    "group_name",
                    "result_delta",
                    "revenue_delta",
                    "expense_delta_total",
                    "source_row_count",
                ],
            ),
            "",
            "### Таблица 3. Новый топ-10 плюсовых товаров",
            md_table(
                plus_rows,
                [
                    "normalized_product_key",
                    "offer_id",
                    "sku",
                    "product_name",
                    "group_name",
                    "result_delta",
                    "revenue_delta",
                    "expense_delta_total",
                    "source_row_count",
                ],
            ),
            "",
            "### Таблица 4. Новый список кандидатов для карточек товаров",
            md_table(
                candidate_rows,
                [
                    "direction",
                    "normalized_product_key",
                    "offer_id",
                    "sku",
                    "product_name",
                    "result_delta",
                    "orders_prev",
                    "orders_curr",
                    "sales_units_prev",
                    "sales_units_curr",
                    "ads_spend_prev",
                    "ads_spend_curr",
                ],
            ),
            "",
            "## 7. Что это меняет",
            "- Топы товаров стали логически чище: `BALERIN15` и `MSHPP2025NEW` больше не распилены на плюс и минус одновременно.",
            "- Placeholder-строки вида `wb:sku:*` больше не живут как отдельные псевдо-товары, если у них есть явная SKU-связь с offer-based рядом.",
            "- Для симуляции и будущих causal-карточек теперь есть устойчивый технический ключ, который можно использовать вместо сырого `canonical_product_key`.",
            "",
            "## 8. Ограничения и риски",
            "- Нормализация сознательно поднимает `sku` выше `offer_id`, потому что именно по `sku` в текущем WB слое расходились uppercase/lowercase ряды и `wb:sku:*` хвосты.",
            "- Если в будущем один и тот же `sku` начнет реально переиспользоваться между разными физическими товарами, это правило потребует дополнительного guardrail.",
            "- Этап чинит слой идентичности и вкладов, но не лечит сами причины появления placeholder-рядов в upstream-слое.",
            "",
            "## 9. Артефакты",
            "### Файлы",
            f"- `[profit_control_identity_normalization_stage_v1.md]({REPORT_PATH})`",
            "### Таблицы",
            f"- `[normalized_product_key_mapping.csv]({MAPPING_CSV})`",
            f"- `[normalized_product_key_collapse_map.csv]({COLLAPSE_CSV})`",
            f"- `[top_minus_products_normalized.csv]({TOP_MINUS_CSV})`",
            f"- `[top_plus_products_normalized.csv]({TOP_PLUS_CSV})`",
            f"- `[normalized_product_card_candidates.csv]({CANDIDATES_CSV})`",
            f"- `[normalization_summary.csv]({SUMMARY_CSV})`",
            "### Скрипты",
            f"- `[scripts/build_profit_control_identity_normalization_stage_v1.py]({ROOT / 'scripts' / 'build_profit_control_identity_normalization_stage_v1.py'})`",
            "",
            "## 10. Мини-выжимка для ChatGPT",
            "- главное: нормализован ключ товара, и физически одинаковые товары схлопнуты",
            "- что сломано: upstream иногда отдает один товар как uppercase/lowercase offer-ряд плюс отдельный `wb:sku:*` хвост",
            "- что починили: ввели `normalized_product_key = sku-first lower key`, пересобрали продуктовые вклады",
            "- что спорно: sku-first правило безопасно для текущего confirmed-пакета, но надо мониторить редкие sku-collision кейсы в будущем",
            "- что логично делать дальше: использовать normalized layer для новых товарных карточек и causal-веток вместо старого сырого product identity",
            "",
            "## 11. Готово для симуляции?",
            "- Главный экран магазина: да",
            "- Карточка проблемного товара: да",
            "- Карточка растущего товара: да",
            "",
            "## 12. Что изменилось в интерпретации после этапа",
            "- раньше думали так: часть сильных SKU одновременно и росла, и падала, потому что их разносило по разным регистрами ключей и placeholder-строкам.",
            "- теперь понимаем так: после нормализации вклад надо читать по физическому товару, и тогда `BALERIN15` — это один сильный плюс, а не плюс с параллельным искусственным минусом.",
        ]
    ) + "\n"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    window = build_window()
    group_map = load_group_map()
    raw_products = load_product_rows(window, group_map)
    mapping_rows, grouped = build_mapping_rows(raw_products)
    normalized_products = aggregate_normalized_products(grouped)
    collapse_rows = build_collapse_rows(grouped, normalized_products)
    minus_rows, plus_rows = build_top_rows(normalized_products)
    candidate_rows = build_candidate_rows(normalized_products, window)

    merged_groups = sum(1 for rows in grouped.values() if len(rows) >= 2)
    rows_in_merged_groups = sum(len(rows) for rows in grouped.values() if len(rows) >= 2)
    duplicate_conflicts_fixed = sum(
        1
        for rows in grouped.values()
        if len(rows) >= 2 and any(q2(row["result_delta"]) < 0 for row in rows) and any(q2(row["result_delta"]) > 0 for row in rows)
    )
    old_count = len(raw_products)
    new_count = len(normalized_products)
    case_only_cpk_groups = len(
        {
            norm_text(row["canonical_product_key"])
            for row in raw_products
            if sum(1 for item in raw_products if norm_text(item["canonical_product_key"]) == norm_text(row["canonical_product_key"])) >= 2
        }
    )
    case_only_offer_groups = len(
        {
            norm_text(row["offer_id"])
            for row in raw_products
            if not is_placeholder(norm_text(row["offer_id"]))
            and sum(1 for item in raw_products if norm_text(item["offer_id"]) == norm_text(row["offer_id"])) >= 2
        }
    )
    duplicate_sku_groups = len(
        {
            norm_text(row["sku"])
            for row in raw_products
            if not is_placeholder(norm_text(row["sku"]))
            and sum(1 for item in raw_products if norm_text(item["sku"]) == norm_text(row["sku"])) >= 2
        }
    )

    summary_rows = [
        {"Показатель": "confirmedThrough", "Значение": window.confirmed_through.isoformat()},
        {"Показатель": "product_rows_before_normalization", "Значение": str(old_count)},
        {"Показатель": "product_rows_after_normalization", "Значение": str(new_count)},
        {"Показатель": "merge_groups", "Значение": str(merged_groups)},
        {"Показатель": "rows_inside_merge_groups", "Значение": str(rows_in_merged_groups)},
        {"Показатель": "duplicate_conflict_groups_fixed", "Значение": str(duplicate_conflicts_fixed)},
        {"Показатель": "case_only_canonical_groups", "Значение": str(case_only_cpk_groups)},
        {"Показатель": "case_only_offer_groups", "Значение": str(case_only_offer_groups)},
        {"Показатель": "duplicate_sku_groups", "Значение": str(duplicate_sku_groups)},
        {
            "Показатель": "normalized_product_key_rule",
            "Значение": "sku-first lower key, else lower offer_id, else lower canonical_product_key",
        },
    ]

    write_csv(MAPPING_CSV, mapping_rows)
    write_csv(COLLAPSE_CSV, collapse_rows)
    write_csv(TOP_MINUS_CSV, minus_rows)
    write_csv(TOP_PLUS_CSV, plus_rows)
    write_csv(CANDIDATES_CSV, candidate_rows)
    write_csv(SUMMARY_CSV, summary_rows)

    REPORT_PATH.write_text(
        write_markdown_report(
            window=window,
            summary_rows=summary_rows,
            collapse_rows=collapse_rows,
            minus_rows=minus_rows,
            plus_rows=plus_rows,
            candidate_rows=candidate_rows,
            duplicate_conflicts_fixed=duplicate_conflicts_fixed,
            merged_groups=merged_groups,
            rows_in_merged_groups=rows_in_merged_groups,
            old_count=old_count,
            new_count=new_count,
        ),
        encoding="utf-8",
    )

    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
