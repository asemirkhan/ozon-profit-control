from __future__ import annotations

import csv
import json
import ssl
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MOSCOW = ZoneInfo("Europe/Moscow")
PUBLIC_DEST = "-1257786"

OUTPUT_DIR = ROOT / "exports" / "wb_product_core_engine_v1"
DOC_PATH = ROOT / "artifacts" / "wb_product_core_engine_v1.md"

INTERNAL_FULL_CSV = OUTPUT_DIR / "sku_internal_full_layer.csv"
EXTERNAL_FULL_CSV = OUTPUT_DIR / "sku_external_full_layer.csv"
COMPETITOR_RINGS_CSV = OUTPUT_DIR / "sku_competitor_rings.csv"
POSITION_SNAPSHOT_CSV = OUTPUT_DIR / "sku_position_snapshot.csv"
PAID_NONPAID_CSV = OUTPUT_DIR / "sku_paid_vs_nonpaid.csv"
GEO_WAREHOUSE_CSV = OUTPUT_DIR / "sku_geo_warehouse_layer.csv"
AVAILABILITY_CSV = OUTPUT_DIR / "sku_availability_layer.csv"
CALENDAR_RESIDUALS_CSV = OUTPUT_DIR / "sku_calendar_adjusted_residuals.csv"
SKU_CAUSAL_PROFILE_CSV = OUTPUT_DIR / "sku_causal_profile.csv"
SKU_FACT_HYPOTHESIS_CSV = OUTPUT_DIR / "sku_fact_vs_hypothesis_map.csv"
STORE_FACT_HYPOTHESIS_CSV = OUTPUT_DIR / "store_fact_vs_hypothesis_map.csv"
COMPETITOR_HISTORY_SEED_CSV = OUTPUT_DIR / "competitor_history_seed.csv"
OWN_HISTORY_SEED_CSV = OUTPUT_DIR / "own_history_seed.csv"
USABLE_NOW_CSV = OUTPUT_DIR / "signals_usable_now.csv"
NEED_HISTORY_CSV = OUTPUT_DIR / "signals_need_history.csv"
INSUFFICIENT_LAYERS_CSV = OUTPUT_DIR / "layers_insufficient.csv"

CAUSAL_DIR = ROOT / "exports" / "profit_control_weak_sku_causal_drilldown_v2"
EXTERNAL_DIR = ROOT / "exports" / "wb_external_signal_layer_v1"
DEEP_DIR = ROOT / "exports" / "profit_control_deep_analysis_pack_v1"
DYNAMIC_DIR = ROOT / "exports" / "profit_control_dynamic_analysis_layer_v1"
CALENDAR_DIR = ROOT / "exports" / "profit_control_calendar_seasonality_layer_v1"
STRENGTH_DIR = ROOT / "exports" / "profit_control_strengths_weaknesses_layer_v1"

STOPWORDS = {
    "для",
    "и",
    "с",
    "со",
    "на",
    "в",
    "из",
    "по",
    "под",
    "к",
    "у",
}


def now_iso() -> str:
    return datetime.now(MOSCOW).isoformat(timespec="seconds")


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_no data_"
    head = "| " + " | ".join(columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = [
        "| " + " | ".join(str(row.get(column, "")).replace("|", "\\|") for column in columns) + " |"
        for row in rows
    ]
    return "\n".join([head, sep, *body])


def as_text(value: Any) -> str:
    return str(value or "").strip()


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def short_money(value: Any) -> str:
    parsed = as_float(value)
    if parsed is None:
        return ""
    return f"{parsed:,.0f}".replace(",", " ")


def token_set(text: str) -> set[str]:
    return {
        token
        for token in __import__("re").findall(r"[A-Za-zА-Яа-я0-9]+", (text or "").lower())
        if token not in STOPWORDS
    }


def public_detail(nm_id: str) -> dict[str, Any]:
    url = "https://card.wb.ru/cards/v4/detail?" + urlencode(
        {
            "appType": 1,
            "curr": "rub",
            "dest": PUBLIC_DEST,
            "nm": nm_id,
        }
    )
    req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urlopen(req, timeout=25, context=ssl.create_default_context()) as resp:
        payload = json.loads(resp.read().decode("utf-8", "ignore"))
    products = payload.get("products") or ((payload.get("data") or {}).get("products") or [])
    return products[0] if products else {}


def price_from_sizes(sizes: Any) -> tuple[float | None, float | None]:
    if not isinstance(sizes, list):
        return None, None
    for size in sizes:
        if not isinstance(size, dict):
            continue
        price = size.get("price")
        if isinstance(price, dict):
            basic = as_float(price.get("basic"))
            product = as_float(price.get("product"))
            return (basic / 100.0 if basic is not None else None, product / 100.0 if product is not None else None)
    return None, None


def safe_discount(price: float | None, sale_price: float | None) -> float | None:
    if price is None or sale_price is None or price <= 0:
        return None
    return max(0.0, min(1.0, 1.0 - sale_price / price))


def summary_text(rows: list[dict[str, Any]], *, name_key: str, score_key: str, limit: int = 3) -> str:
    parts = []
    for row in rows[:limit]:
        parts.append(f"{row[name_key]} ({row[score_key]})")
    return "; ".join(parts)


def load_inputs() -> dict[str, list[dict[str, str]]]:
    return {
        "causal_summary": load_csv(CAUSAL_DIR / "sku_causal_summary.csv"),
        "paid_nonpaid": load_csv(CAUSAL_DIR / "paid_vs_nonpaid_summary.csv"),
        "geo_product": load_csv(CAUSAL_DIR / "geo_product_summary.csv"),
        "warehouse_product": load_csv(CAUSAL_DIR / "warehouse_product_summary.csv"),
        "availability": load_csv(CAUSAL_DIR / "availability_check.csv"),
        "variant": load_csv(CAUSAL_DIR / "sku_variant_check.csv"),
        "product_window": load_csv(DEEP_DIR / "product_window_comparison.csv"),
        "product_context": load_csv(DYNAMIC_DIR / "product_vs_store_context.csv"),
        "calendar": load_csv(CALENDAR_DIR / "product_calendar_adjusted_summary.csv"),
        "strengths": load_csv(STRENGTH_DIR / "product_strengths_weaknesses.csv"),
        "own_card": load_csv(EXTERNAL_DIR / "own_card_matching_audit.csv"),
        "own_history_availability": load_csv(EXTERNAL_DIR / "own_card_history_availability.csv"),
        "search_queries": load_csv(EXTERNAL_DIR / "sku_search_queries.csv"),
        "search_results": load_csv(EXTERNAL_DIR / "search_results_raw.csv"),
        "competitor_scoring": load_csv(EXTERNAL_DIR / "competitor_scoring.csv"),
        "competitor_card": load_csv(EXTERNAL_DIR / "competitor_card_snapshot.csv"),
        "position_snapshot": load_csv(EXTERNAL_DIR / "competitor_position_snapshot.csv"),
        "competitor_history_seed": load_csv(EXTERNAL_DIR / "competitor_history_seed.csv"),
    }


def select_targets(inputs: dict[str, list[dict[str, str]]]) -> list[str]:
    own_card_rows = inputs["own_card"]
    ordered = []
    for row in own_card_rows:
        ordered.append(as_text(row["nm_id"]))
    return ordered


def build_internal_full_layer(inputs: dict[str, list[dict[str, str]]], target_ids: list[str]) -> list[dict[str, Any]]:
    causal = {as_text(row["product_id"]): row for row in inputs["causal_summary"] if as_text(row["product_id"]) in target_ids}
    window = {as_text(row["product_id"]): row for row in inputs["product_window"] if as_text(row["product_id"]) in target_ids}
    context = {as_text(row["product_id"]): row for row in inputs["product_context"] if as_text(row["product_id"]) in target_ids}
    calendar = {as_text(row["product_id"]): row for row in inputs["calendar"] if as_text(row["product_id"]) in target_ids}
    strengths = {as_text(row["product_id"]): row for row in inputs["strengths"] if as_text(row["product_id"]) in target_ids}
    variant = {as_text(row["product_id"]): row for row in inputs["variant"] if as_text(row["product_id"]) in target_ids}

    paid_map: dict[tuple[str, str], dict[str, str]] = {}
    for row in inputs["paid_nonpaid"]:
        if row.get("entity_scope") != "product":
            continue
        if as_text(row["entity_id"]) not in target_ids:
            continue
        paid_map[(as_text(row["entity_id"]), as_text(row["window_label"]))] = row

    availability_rows = [row for row in inputs["availability"] if as_text(row["product_id"]) in target_ids]
    availability_summary: dict[str, dict[str, Any]] = defaultdict(lambda: {"risk_count": 0, "latest_snapshot_date": "", "stock_available_sum": 0.0, "zero_overlap_count": 0})
    for row in availability_rows:
        pid = as_text(row["product_id"])
        summary = availability_summary[pid]
        summary["latest_snapshot_date"] = max(summary["latest_snapshot_date"], as_text(row["snapshot_date"]))
        summary["stock_available_sum"] += as_float(row.get("stock_available")) or 0.0
        if "risk" in as_text(row.get("availability_risk_flag")) and "no_snapshot_overlap_risk" not in as_text(row.get("availability_risk_flag")):
            summary["risk_count"] += 1
        if as_text(row.get("availability_risk_flag")) == "snapshot_zero_or_drop_overlap":
            summary["zero_overlap_count"] += 1

    rows: list[dict[str, Any]] = []
    for product_id in target_ids:
        c = causal[product_id]
        w = window.get(product_id, {})
        ctx = context.get(product_id, {})
        cal = calendar.get(product_id, {})
        s = strengths.get(product_id, {})
        v = variant.get(product_id, {})
        short_paid = paid_map.get((product_id, "short_7d_vs_prev_28d"), {})
        medium_paid = paid_map.get((product_id, "medium_14d_vs_prev_42d"), {})
        a = availability_summary.get(product_id, {})
        rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": product_id,
                "product_name": as_text(c.get("product_name")),
                "comparison_role": as_text(c.get("comparison_role")),
                "performance_group": as_text(s.get("performance_group")),
                "seasonality_group": as_text(s.get("seasonality_group")),
                "role_cluster": as_text(s.get("role_cluster")),
                "relative_to_store": as_text(c.get("relative_to_store")),
                "dominant_pattern": as_text(c.get("dominant_pattern")),
                "strongest_causal_signal": as_text(c.get("strongest_causal_signal")),
                "full_revenue_total": w.get("revenue_net_total"),
                "full_profit_after_ads_total": w.get("profit_after_ads_total"),
                "full_margin_after_ads_total": w.get("margin_after_ads_total"),
                "order_count_total": w.get("order_count_total"),
                "sold_units_net_total": w.get("sold_units_net_total"),
                "average_check_total": w.get("average_check_total"),
                "ad_spend_total": w.get("ad_spend_total"),
                "commission_cost_total": w.get("commission_cost_total"),
                "delivery_cost_total": w.get("delivery_cost_total"),
                "warehouse_logistics_tail_cost_total": w.get("warehouse_logistics_tail_cost_total"),
                "storage_cost_total": w.get("storage_cost_total"),
                "acceptance_cost_total": w.get("acceptance_cost_total"),
                "acquiring_cost_total": w.get("acquiring_cost_total"),
                "penalty_cost_total": w.get("penalty_cost_total"),
                "withholding_cost_total": w.get("withholding_cost_total"),
                "positive_adjustment_total": w.get("positive_adjustment_total"),
                "negative_adjustment_total": w.get("negative_adjustment_total"),
                "short_current_revenue_total": w.get("short_current_revenue_total"),
                "short_base_revenue_total": w.get("short_base_revenue_total"),
                "short_revenue_delta_per_day": w.get("short_revenue_delta_per_day"),
                "medium_current_revenue_total": w.get("medium_current_revenue_total"),
                "medium_base_revenue_total": w.get("medium_base_revenue_total"),
                "medium_revenue_delta_per_day": w.get("medium_revenue_delta_per_day"),
                "short_current_profit_after_ads_total": w.get("short_current_profit_after_ads_total"),
                "short_base_profit_after_ads_total": w.get("short_base_profit_after_ads_total"),
                "short_profit_after_ads_delta_per_day": w.get("short_profit_after_ads_delta_per_day"),
                "medium_current_profit_after_ads_total": w.get("medium_current_profit_after_ads_total"),
                "medium_base_profit_after_ads_total": w.get("medium_base_profit_after_ads_total"),
                "medium_profit_after_ads_delta_per_day": w.get("medium_profit_after_ads_delta_per_day"),
                "short_current_ad_spend_total": w.get("short_current_ad_spend_total"),
                "short_base_ad_spend_total": w.get("short_base_ad_spend_total"),
                "short_ad_spend_delta_per_day": w.get("short_ad_spend_delta_per_day"),
                "short_current_logistics_total": w.get("short_current_logistics_total"),
                "short_base_logistics_total": w.get("short_base_logistics_total"),
                "short_logistics_delta_per_day": w.get("short_logistics_delta_per_day"),
                "short_current_withholding_total": w.get("short_current_withholding_total"),
                "short_base_withholding_total": w.get("short_base_withholding_total"),
                "short_withholding_delta_per_day": w.get("short_withholding_delta_per_day"),
                "short_actual_revenue_delta_per_day": cal.get("short_actual_revenue_delta_per_day"),
                "short_calendar_mix_effect_revenue_per_day": cal.get("short_calendar_mix_effect_revenue_per_day"),
                "short_residual_revenue_after_calendar_per_day": cal.get("short_residual_revenue_after_calendar_per_day"),
                "short_actual_profit_delta_per_day": cal.get("short_actual_profit_delta_per_day"),
                "short_calendar_mix_effect_profit_per_day": cal.get("short_calendar_mix_effect_profit_per_day"),
                "short_residual_profit_after_calendar_per_day": cal.get("short_residual_profit_after_calendar_per_day"),
                "short_calendar_explained_share_profit_abs": cal.get("short_calendar_explained_share_profit_abs"),
                "best_fit_explanation": cal.get("best_fit_explanation") or c.get("best_fit_explanation"),
                "short_current_paid_orders_per_day": short_paid.get("current_paid_orders_per_day"),
                "short_base_paid_orders_per_day": short_paid.get("base_paid_orders_per_day"),
                "short_paid_orders_delta_per_day": short_paid.get("paid_orders_delta_per_day"),
                "short_current_non_paid_orders_per_day": short_paid.get("current_non_paid_orders_per_day"),
                "short_base_non_paid_orders_per_day": short_paid.get("base_non_paid_orders_per_day"),
                "short_non_paid_orders_delta_per_day": short_paid.get("non_paid_orders_delta_per_day"),
                "short_ad_signal": short_paid.get("ad_signal"),
                "short_flow_signal": short_paid.get("flow_signal"),
                "medium_current_paid_orders_per_day": medium_paid.get("current_paid_orders_per_day"),
                "medium_base_paid_orders_per_day": medium_paid.get("base_paid_orders_per_day"),
                "medium_paid_orders_delta_per_day": medium_paid.get("paid_orders_delta_per_day"),
                "medium_current_non_paid_orders_per_day": medium_paid.get("current_non_paid_orders_per_day"),
                "medium_base_non_paid_orders_per_day": medium_paid.get("base_non_paid_orders_per_day"),
                "medium_non_paid_orders_delta_per_day": medium_paid.get("non_paid_orders_delta_per_day"),
                "geo_signal": c.get("geo_signal"),
                "warehouse_signal": c.get("warehouse_signal"),
                "availability_signal": c.get("availability_signal"),
                "variant_signal": c.get("variant_signal"),
                "same_name_product_id_count": c.get("same_name_product_id_count"),
                "same_name_product_ids": c.get("same_name_product_ids"),
                "same_name_residual_signals": c.get("same_name_residual_signals"),
                "latest_stock_snapshot_date": a.get("latest_snapshot_date"),
                "latest_stock_available_sum": round(a.get("stock_available_sum", 0.0), 2),
                "availability_risk_count": a.get("risk_count", 0),
                "snapshot_zero_overlap_count": a.get("zero_overlap_count", 0),
                "days_cover_14d": w.get("days_cover_14d"),
                "week_over_week_note": "topline WoW not yet assembled into cost-complete layer; current engine uses short/medium windows plus calendar residuals.",
            }
        )
    return rows


def build_external_full_layer(inputs: dict[str, list[dict[str, str]]], target_ids: list[str]) -> list[dict[str, Any]]:
    own_card = {as_text(row["nm_id"]): row for row in inputs["own_card"] if as_text(row["nm_id"]) in target_ids}
    query_rows = [row for row in inputs["search_queries"] if as_text(row["nm_id"]) in target_ids]
    search_rows = [row for row in inputs["search_results"] if as_text(row["own_nm_id"]) in target_ids]
    scoring_rows = [row for row in inputs["competitor_scoring"] if as_text(row["own_nm_id"]) in target_ids]
    history_rows = inputs["own_history_availability"]

    search_summary: dict[str, dict[str, Any]] = defaultdict(lambda: {"success": 0, "error": 0})
    for row in search_rows:
        pid = as_text(row["own_nm_id"])
        if as_text(row.get("capture_status")) == "success":
            search_summary[pid]["success"] += 1
        elif as_text(row.get("capture_status")).startswith("error:"):
            search_summary[pid]["error"] += 1

    query_grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in query_rows:
        query_grouped[as_text(row["nm_id"])].append(row)
    for rows in query_grouped.values():
        rows.sort(key=lambda row: as_int(row.get("query_rank")) or 999)

    competitor_grouped: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    for row in scoring_rows:
        competitor_grouped[as_text(row["own_nm_id"])][as_text(row["competition_ring"])].append(row)
    for per_ring in competitor_grouped.values():
        for ring_rows in per_ring.values():
            ring_rows.sort(key=lambda row: -(as_float(row.get("score")) or 0.0))

    public_detail_map: dict[str, dict[str, Any]] = {}
    for product_id in target_ids:
        public_detail_map[product_id] = public_detail(product_id)

    feedback_scope = next((row.get("live_probe_status") for row in history_rows if row.get("data_key") == "feedbacks_reviews"), "")
    questions_scope = next((row.get("live_probe_status") for row in history_rows if row.get("data_key") == "questions"), "")

    rows: list[dict[str, Any]] = []
    for product_id in target_ids:
        own = own_card[product_id]
        detail = public_detail_map[product_id]
        basic, sale = price_from_sizes(detail.get("sizes"))
        direct = competitor_grouped[product_id].get("direct_competitor", [])
        neighbors = competitor_grouped[product_id].get("search_neighbor", [])
        functional = competitor_grouped[product_id].get("functional_competitor", [])
        top_queries = query_grouped.get(product_id, [])
        rows.append(
            {
                "snapshot_date": as_text(own.get("snapshot_date")),
                "nm_id": product_id,
                "product_name": as_text(own.get("product_name")),
                "comparison_role": as_text(own.get("comparison_role")),
                "matched_own_card_confirmed": own.get("matched_own_card_confirmed"),
                "confidence_level": own.get("confidence_level"),
                "strong_match_fields": own.get("strong_match_fields"),
                "medium_match_fields": own.get("medium_match_fields"),
                "public_title": as_text(detail.get("name")) or as_text(own.get("public_title")),
                "public_brand": as_text(detail.get("brand")) or as_text(own.get("public_brand")),
                "public_supplier": as_text(detail.get("supplier")) or as_text(own.get("public_supplier")),
                "public_supplier_id": as_text(detail.get("supplierId")) or as_text(own.get("public_supplier_id")),
                "public_subject": as_text(own.get("public_subject")),
                "public_current_price": basic or own.get("public_current_price"),
                "public_current_sale_price": sale or own.get("public_current_sale_price"),
                "public_current_discount_pct": safe_discount(basic, sale) if basic and sale else own.get("public_current_discount_pct"),
                "public_rating": detail.get("reviewRating") or detail.get("rating"),
                "public_feedbacks": detail.get("feedbacks") or detail.get("nmFeedbacks"),
                "internal_offer_id": own.get("internal_offer_id"),
                "internal_vendor_code": own.get("internal_vendor_code"),
                "internal_barcode": own.get("internal_barcode"),
                "top_query_1": top_queries[0]["search_query"] if len(top_queries) > 0 else "",
                "top_query_1_orders_current": top_queries[0]["orders_current"] if len(top_queries) > 0 else "",
                "top_query_1_avg_position_current": top_queries[0]["avg_position_current"] if len(top_queries) > 0 else "",
                "top_query_1_visibility_current": top_queries[0]["visibility_current"] if len(top_queries) > 0 else "",
                "top_query_2": top_queries[1]["search_query"] if len(top_queries) > 1 else "",
                "top_query_3": top_queries[2]["search_query"] if len(top_queries) > 2 else "",
                "public_search_success_rows": search_summary[product_id]["success"],
                "public_search_error_rows": search_summary[product_id]["error"],
                "public_search_capture_status": "captured" if search_summary[product_id]["success"] > 0 else "not_captured",
                "direct_competitor_count": len(direct),
                "search_neighbor_count": len(neighbors),
                "functional_competitor_count": len(functional),
                "top_direct_competitors_summary": summary_text(direct, name_key="candidate_name", score_key="score"),
                "top_search_neighbors_summary": summary_text(neighbors, name_key="candidate_name", score_key="score"),
                "top_functional_competitors_summary": summary_text(functional, name_key="candidate_name", score_key="score"),
                "feedback_scope_status": feedback_scope,
                "questions_scope_status": questions_scope,
                "external_layer_note": "Public WB search capture is partial; no-capture rows are not proof of no competition.",
            }
        )
    return rows


def build_competitor_rings(inputs: dict[str, list[dict[str, str]]], target_ids: list[str]) -> list[dict[str, Any]]:
    card_map = {as_text(row["candidate_nm_id"]): row for row in inputs["competitor_card"]}
    rows: list[dict[str, Any]] = []
    for row in inputs["competitor_scoring"]:
        if as_text(row["own_nm_id"]) not in target_ids:
            continue
        card = card_map.get(as_text(row["candidate_nm_id"]), {})
        ring = as_text(row["competition_ring"])
        if ring == "direct_competitor":
            why = "same query field, similar purpose/subject and close commercial profile"
        elif ring == "search_neighbor":
            why = "co-occurs close to our SKU in captured WB search results"
        else:
            why = "solves similar buyer task but is less tightly coupled to captured issuance"
        rows.append(
            {
                "snapshot_date": row["snapshot_date"],
                "own_nm_id": row["own_nm_id"],
                "own_product_name": row["own_product_name"],
                "competition_ring": ring,
                "candidate_nm_id": row["candidate_nm_id"],
                "candidate_name": row["candidate_name"],
                "brand": card.get("brand") or row.get("candidate_brand"),
                "supplier": card.get("supplier") or row.get("candidate_supplier"),
                "product_url": card.get("product_url", ""),
                "current_price": card.get("current_price") or row.get("current_price"),
                "current_sale_price": card.get("current_sale_price", ""),
                "current_discount_pct": card.get("current_discount_pct", ""),
                "rating": card.get("rating") or row.get("current_rating"),
                "feedbacks": card.get("feedbacks") or row.get("current_feedbacks"),
                "query_overlap_count": row.get("query_overlap_count"),
                "neighbor_overlap_count": row.get("neighbor_overlap_count"),
                "avg_position_gap": row.get("avg_position_gap"),
                "price_closeness_ratio": row.get("price_closeness_ratio"),
                "title_token_similarity": row.get("title_token_similarity"),
                "same_subject_flag": row.get("same_subject_flag"),
                "score": row.get("score"),
                "why_competitor": why,
                "claim_type": "evaluation",
            }
        )
    rows.sort(key=lambda item: (item["own_nm_id"], item["competition_ring"], -(as_float(item["score"]) or 0.0)))
    return rows


def build_position_snapshot(inputs: dict[str, list[dict[str, str]]], target_ids: list[str]) -> list[dict[str, Any]]:
    query_by_key = {
        (as_text(row["nm_id"]), as_text(row["search_query"])): row
        for row in inputs["search_queries"]
        if as_text(row["nm_id"]) in target_ids
    }
    rows: list[dict[str, Any]] = []
    added_error_keys: set[tuple[str, str]] = set()
    for row in inputs["search_results"]:
        pid = as_text(row["own_nm_id"])
        if pid not in target_ids:
            continue
        query = as_text(row["search_query"])
        query_row = query_by_key.get((pid, query), {})
        if as_text(row.get("capture_status")).startswith("error:"):
            key = (pid, query)
            if key in added_error_keys:
                continue
            added_error_keys.add(key)
            rows.append(
                {
                    "snapshot_date": row["snapshot_date"],
                    "own_nm_id": pid,
                    "own_product_name": row["own_product_name"],
                    "search_query": query,
                    "capture_status": row.get("capture_status"),
                    "own_avg_position_current": query_row.get("avg_position_current", ""),
                    "own_visibility_current": query_row.get("visibility_current", ""),
                    "own_orders_current": query_row.get("orders_current", ""),
                    "own_position_public_capture": "",
                    "candidate_nm_id": "",
                    "candidate_name": "",
                    "competition_ring": "",
                    "candidate_position": "",
                    "position_gap": "",
                }
            )

    for row in inputs["position_snapshot"]:
        pid = as_text(row["own_nm_id"])
        if pid not in target_ids:
            continue
        query = as_text(row["search_query"])
        query_row = query_by_key.get((pid, query), {})
        rows.append(
            {
                "snapshot_date": row["snapshot_date"],
                "own_nm_id": pid,
                "own_product_name": row["own_product_name"],
                "search_query": query,
                "capture_status": "success",
                "own_avg_position_current": query_row.get("avg_position_current", ""),
                "own_visibility_current": query_row.get("visibility_current", ""),
                "own_orders_current": query_row.get("orders_current", ""),
                "own_position_public_capture": row.get("own_position"),
                "candidate_nm_id": row.get("candidate_nm_id"),
                "candidate_name": row.get("candidate_name"),
                "competition_ring": row.get("competition_ring"),
                "candidate_position": row.get("candidate_position"),
                "position_gap": row.get("position_gap"),
            }
        )
    rows.sort(key=lambda item: (item["own_nm_id"], item["search_query"], item["capture_status"], as_int(item.get("candidate_position")) or 999))
    return rows


def build_paid_nonpaid(inputs: dict[str, list[dict[str, str]]], target_ids: list[str]) -> list[dict[str, Any]]:
    return [
        row
        for row in inputs["paid_nonpaid"]
        if row.get("entity_scope") == "product" and as_text(row.get("entity_id")) in target_ids
    ]


def build_geo_warehouse_layer(inputs: dict[str, list[dict[str, str]]], target_ids: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in inputs["geo_product"]:
        if as_text(row["entity_id"]) not in target_ids:
            continue
        rows.append(
            {
                **row,
                "dimension_type": "region",
            }
        )
    for row in inputs["warehouse_product"]:
        if as_text(row["entity_id"]) not in target_ids:
            continue
        rows.append(
            {
                **row,
                "dimension_type": "warehouse",
            }
        )
    return rows


def build_availability_layer(inputs: dict[str, list[dict[str, str]]], target_ids: list[str]) -> list[dict[str, Any]]:
    return [row for row in inputs["availability"] if as_text(row["product_id"]) in target_ids]


def build_calendar_residuals(inputs: dict[str, list[dict[str, str]]], target_ids: list[str]) -> list[dict[str, Any]]:
    context = {as_text(row["product_id"]): row for row in inputs["product_context"]}
    strengths = {as_text(row["product_id"]): row for row in inputs["strengths"]}
    rows: list[dict[str, Any]] = []
    for row in inputs["calendar"]:
        pid = as_text(row["product_id"])
        if pid not in target_ids:
            continue
        ctx = context.get(pid, {})
        s = strengths.get(pid, {})
        rows.append(
            {
                **row,
                "relative_to_store": ctx.get("relative_to_store", ""),
                "dominant_pattern": ctx.get("dominant_pattern", ""),
                "performance_group": s.get("performance_group", ""),
                "seasonality_group": s.get("seasonality_group", ""),
            }
        )
    return rows


def build_geo_profile_summary(inputs: dict[str, list[dict[str, str]]], target_ids: list[str]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    region_rows = [
        row
        for row in inputs["geo_product"]
        if as_text(row["entity_id"]) in target_ids
        and as_text(row["window_label"]) == "short_7d_vs_prev_28d"
        and as_text(row["dimension"]) == "region_name"
    ]
    warehouse_rows = [
        row
        for row in inputs["warehouse_product"]
        if as_text(row["entity_id"]) in target_ids
        and as_text(row["window_label"]) == "short_7d_vs_prev_28d"
        and as_text(row["dimension"]) == "warehouse_name"
    ]
    for pid in target_ids:
        pid_regions = [row for row in region_rows if as_text(row["entity_id"]) == pid]
        pid_wh = [row for row in warehouse_rows if as_text(row["entity_id"]) == pid]
        neg_regions = [
            row
            for row in pid_regions
            if (as_float(row.get("revenue_delta_vs_calendar_adjusted_per_day")) or 0.0) < 0
            and as_text(row.get("reliability_flag")) == "ok"
        ]
        neg_wh = [
            row
            for row in pid_wh
            if (as_float(row.get("revenue_delta_vs_calendar_adjusted_per_day")) or 0.0) < 0
            and as_text(row.get("reliability_flag")) == "ok"
        ]
        neg_regions.sort(key=lambda row: as_float(row.get("revenue_delta_vs_calendar_adjusted_per_day")) or 0.0)
        neg_wh.sort(key=lambda row: as_float(row.get("revenue_delta_vs_calendar_adjusted_per_day")) or 0.0)
        region_contrib_sum = sum(abs(as_float(row.get("revenue_contribution_to_entity_delta_share")) or 0.0) for row in neg_regions[:3])
        wh_contrib_sum = sum(abs(as_float(row.get("revenue_contribution_to_entity_delta_share")) or 0.0) for row in neg_wh[:3])
        if region_contrib_sum >= 0.6 or wh_contrib_sum >= 0.6:
            spread = "local_concentrated"
        elif neg_regions or neg_wh:
            spread = "distributed_with_local_hotspots"
        else:
            spread = "insufficient_signal"
        summary[pid] = {
            "negative_region_count_ok": len(neg_regions),
            "negative_warehouse_count_ok": len(neg_wh),
            "top_region_1": as_text(neg_regions[0]["dimension_value"]) if neg_regions else "",
            "top_region_1_delta": as_float(neg_regions[0].get("revenue_delta_vs_calendar_adjusted_per_day")) if neg_regions else None,
            "top_region_2": as_text(neg_regions[1]["dimension_value"]) if len(neg_regions) > 1 else "",
            "top_region_2_delta": as_float(neg_regions[1].get("revenue_delta_vs_calendar_adjusted_per_day")) if len(neg_regions) > 1 else None,
            "top_warehouse_1": as_text(neg_wh[0]["dimension_value"]) if neg_wh else "",
            "top_warehouse_1_delta": as_float(neg_wh[0].get("revenue_delta_vs_calendar_adjusted_per_day")) if neg_wh else None,
            "top3_region_negative_contribution_share_abs": round(region_contrib_sum, 4),
            "top3_warehouse_negative_contribution_share_abs": round(wh_contrib_sum, 4),
            "geo_problem_spread": spread,
        }
    return summary


def build_sku_causal_profiles(
    inputs: dict[str, list[dict[str, str]]],
    internal_rows: list[dict[str, Any]],
    external_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    geo_summary = build_geo_profile_summary(inputs, [as_text(row["product_id"]) for row in internal_rows])
    competitor_grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in inputs["competitor_scoring"]:
        if as_text(row.get("competition_ring")) == "direct_competitor":
            competitor_grouped[as_text(row["own_nm_id"])].append(row)
    for rows in competitor_grouped.values():
        rows.sort(key=lambda row: -(as_float(row.get("score")) or 0.0))

    external_map = {as_text(row["nm_id"]): row for row in external_rows}
    profiles: list[dict[str, Any]] = []
    for internal in internal_rows:
        pid = as_text(internal["product_id"])
        external = external_map.get(pid, {})
        geo = geo_summary.get(pid, {})
        direct = competitor_grouped.get(pid, [])[:5]
        own_price = as_float(external.get("public_current_sale_price")) or as_float(external.get("public_current_price"))
        own_rating = as_float(external.get("public_rating"))
        own_feedbacks = as_float(external.get("public_feedbacks"))
        avg_comp_price = None
        avg_comp_rating = None
        avg_comp_feedbacks = None
        if direct:
            price_values = [as_float(row.get("current_price")) for row in direct if as_float(row.get("current_price")) is not None]
            rating_values = [as_float(row.get("current_rating")) for row in direct if as_float(row.get("current_rating")) is not None]
            feedback_values = [as_float(row.get("current_feedbacks")) for row in direct if as_float(row.get("current_feedbacks")) is not None]
            avg_comp_price = sum(price_values) / len(price_values) if price_values else None
            avg_comp_rating = sum(rating_values) / len(rating_values) if rating_values else None
            avg_comp_feedbacks = sum(feedback_values) / len(feedback_values) if feedback_values else None

        if own_price is not None and avg_comp_price is not None:
            if own_price >= avg_comp_price * 1.10:
                price_signal = "price_disadvantage_vs_direct_peers"
            elif own_price <= avg_comp_price * 0.90:
                price_signal = "price_advantage_vs_direct_peers"
            else:
                price_signal = "price_near_direct_peers"
        else:
            price_signal = "no_direct_price_comparison"

        if own_rating is not None and avg_comp_rating is not None:
            if own_rating + 0.10 < avg_comp_rating:
                rating_signal = "rating_weaker_than_direct_peers"
            elif own_rating > avg_comp_rating + 0.10:
                rating_signal = "rating_stronger_than_direct_peers"
            else:
                rating_signal = "rating_near_direct_peers"
        else:
            rating_signal = "no_direct_rating_comparison"

        if own_feedbacks is not None and avg_comp_feedbacks is not None and avg_comp_feedbacks > 0:
            if own_feedbacks < avg_comp_feedbacks * 0.5:
                review_signal = "reviews_weaker_than_direct_peers"
            elif own_feedbacks > avg_comp_feedbacks * 1.5:
                review_signal = "reviews_stronger_than_direct_peers"
            else:
                review_signal = "reviews_near_direct_peers"
        else:
            review_signal = "no_direct_review_comparison"

        stronger_competitor_signs = []
        if as_text(external.get("public_search_capture_status")) == "captured" and direct:
            if rating_signal == "rating_weaker_than_direct_peers":
                stronger_competitor_signs.append("higher_peer_rating")
            if review_signal == "reviews_weaker_than_direct_peers":
                stronger_competitor_signs.append("higher_peer_review_count")
            if price_signal == "price_disadvantage_vs_direct_peers":
                stronger_competitor_signs.append("higher_own_price_vs_direct_peers")
            if not stronger_competitor_signs:
                stronger_competitor_signs.append("no_clear_card_disadvantage_vs_captured_direct_peers")
        else:
            stronger_competitor_signs.append("direct_competitor_capture_incomplete")

        if as_text(internal.get("short_flow_signal")) == "non_paid_weaker_than_paid":
            paid_non_paid = "non_paid_weaker_than_paid"
        elif as_text(internal.get("short_flow_signal")) == "both_paid_and_non_paid_down":
            paid_non_paid = "both_paid_and_non_paid_down"
        else:
            paid_non_paid = as_text(internal.get("short_flow_signal"))

        if as_text(internal.get("availability_signal")) in {"snapshot_overlap_risk", "snapshot_zero_stock_some_warehouses"} or (as_int(internal.get("snapshot_zero_overlap_count")) or 0) > 0:
            stock_problem = "availability_or_snapshot_overlap_risk_present"
        else:
            stock_problem = "no_clear_snapshot_availability_problem"

        if as_text(geo.get("geo_problem_spread")) == "local_concentrated":
            locality = "local_concentrated_problem"
        elif as_text(geo.get("geo_problem_spread")) == "distributed_with_local_hotspots":
            locality = "distributed_with_local_hotspots"
        else:
            locality = "insufficient_geo_signal"

        strongest = as_text(internal.get("strongest_causal_signal"))
        if strongest == "availability_constraint_risk":
            working_hypothesis = "availability_and_warehouse_constraints_are_most_likely_amplifying_non_paid_weakness"
        elif strongest == "paid_channel_issue_or_budget_shift":
            if as_text(external.get("public_search_capture_status")) == "captured":
                working_hypothesis = "paid_weakness_plus_external_competition_pressure"
            else:
                working_hypothesis = "paid_weakness_with_incomplete_external_confirmation"
        elif strongest == "organic_or_non_paid_demand_drop":
            if "higher_peer_rating" in stronger_competitor_signs or "higher_peer_review_count" in stronger_competitor_signs:
                working_hypothesis = "organic_visibility_loss_plus_stronger_competitor_proof_points"
            else:
                working_hypothesis = "organic_visibility_or_non_paid_demand_drop"
        elif strongest == "stable_comparator_holding_up_vs_store":
            working_hypothesis = "resilient_comparator_holding_up_better_than_store_background"
        else:
            working_hypothesis = strongest or "mixed_signal"

        proven_parts = [
            f"calendar_residual_profit_day={short_money(internal.get('short_residual_profit_after_calendar_per_day'))}",
            f"flow={paid_non_paid}",
            f"availability={as_text(internal.get('availability_signal'))}",
            f"geo_spread={locality}",
            f"search_capture={as_text(external.get('public_search_capture_status'))}",
        ]
        if as_text(external.get("matched_own_card_confirmed")) == "yes":
            proven_parts.append("own_card_confirmed")
        if direct:
            proven_parts.append(f"captured_direct_competitors={len(direct)}")

        hypothesis_parts = []
        if "higher_peer_rating" in stronger_competitor_signs or "higher_peer_review_count" in stronger_competitor_signs:
            hypothesis_parts.append("competitor_card_strength_may_pressure_organic_share")
        if stock_problem == "availability_or_snapshot_overlap_risk_present":
            hypothesis_parts.append("availability_may_amplify_weakness")
        if locality == "local_concentrated_problem":
            hypothesis_parts.append("problem_is_not_evenly_distributed")
        if as_text(external.get("public_search_capture_status")) != "captured":
            hypothesis_parts.append("external_search_layer_still_partial")

        profiles.append(
            {
                "snapshot_date": now_iso(),
                "product_id": pid,
                "product_name": internal["product_name"],
                "comparison_role": internal["comparison_role"],
                "what_happened": (
                    "residual_weakness_after_calendar" if (as_float(internal.get("short_residual_profit_after_calendar_per_day")) or 0.0) < 0
                    else "resilient_after_calendar"
                ),
                "where_happened": (
                    f"geo={as_text(internal.get('geo_signal'))}; warehouse={as_text(internal.get('warehouse_signal'))}; "
                    f"top_region={as_text(geo.get('top_region_1'))}; top_warehouse={as_text(geo.get('top_warehouse_1'))}"
                ),
                "paid_or_non_paid": paid_non_paid,
                "local_or_everywhere": locality,
                "stock_problem_signs": stock_problem,
                "stronger_competitor_signs": "; ".join(stronger_competitor_signs),
                "card_weaker_signs": f"{price_signal}; {rating_signal}; {review_signal}",
                "our_sale_price": own_price,
                "our_rating": own_rating,
                "our_feedbacks": own_feedbacks,
                "avg_direct_comp_price": round(avg_comp_price, 2) if avg_comp_price is not None else None,
                "avg_direct_comp_rating": round(avg_comp_rating, 2) if avg_comp_rating is not None else None,
                "avg_direct_comp_feedbacks": round(avg_comp_feedbacks, 2) if avg_comp_feedbacks is not None else None,
                "direct_competitor_count_captured": len(direct),
                "strongest_working_hypothesis": working_hypothesis,
                "what_is_proven": "; ".join(proven_parts),
                "what_is_hypothesis": "; ".join(hypothesis_parts),
            }
        )
    return profiles


def build_fact_vs_hypothesis_maps(
    inputs: dict[str, list[dict[str, str]]],
    internal_rows: list[dict[str, Any]],
    external_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    internal_map = {as_text(row["product_id"]): row for row in internal_rows}
    external_map = {as_text(row["nm_id"]): row for row in external_rows}
    direct_rows = [row for row in inputs["competitor_scoring"] if row.get("competition_ring") == "direct_competitor"]

    direct_grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in direct_rows:
        direct_grouped[as_text(row["own_nm_id"])].append(row)
    for rows in direct_grouped.values():
        rows.sort(key=lambda row: -(as_float(row.get("score")) or 0.0))

    sku_rows: list[dict[str, Any]] = []
    for product_id, internal in internal_map.items():
        external = external_map.get(product_id, {})
        residual_profit = as_float(internal.get("short_residual_profit_after_calendar_per_day")) or 0.0
        residual_revenue = as_float(internal.get("short_residual_revenue_after_calendar_per_day")) or 0.0
        own_rating = as_float(external.get("public_rating"))
        own_feedbacks = as_float(external.get("public_feedbacks"))
        captured_direct = direct_grouped.get(product_id, [])
        direct_avg_rating = None
        direct_avg_feedbacks = None
        if captured_direct:
            direct_avg_rating = sum(as_float(row.get("current_rating")) or 0.0 for row in captured_direct[:5]) / min(len(captured_direct[:5]), 5)
            direct_avg_feedbacks = sum(as_float(row.get("current_feedbacks")) or 0.0 for row in captured_direct[:5]) / min(len(captured_direct[:5]), 5)

        level1 = "growth" if residual_profit > 0 else "weakness"
        if residual_profit < 0:
            happened = f"После календарной поправки SKU остается слабым: residual profit/day {short_money(residual_profit)}, residual revenue/day {short_money(residual_revenue)}."
        else:
            happened = f"После календарной поправки SKU остается устойчивым: residual profit/day {short_money(residual_profit)}, residual revenue/day {short_money(residual_revenue)}."
        sku_rows.append(
            {
                "entity_scope": "sku",
                "entity_id": product_id,
                "entity_name": internal["product_name"],
                "level": 1,
                "claim_type": "fact",
                "confidence": "high",
                "theme": "what_happened",
                "statement": happened,
                "evidence": "calendar residuals + window comparison",
            }
        )

        flow_signal = as_text(internal.get("short_flow_signal"))
        if flow_signal == "non_paid_weaker_than_paid":
            statement = "Paid держится лучше, чем non-paid; основная слабость сидит в органическом / не рекламном потоке."
        elif flow_signal == "both_paid_and_non_paid_down":
            statement = "Слабеют и paid, и non-paid; слабость не локализуется только в одном канале."
        elif flow_signal == "paid_stable_non_paid_main_drop":
            statement = "Paid не выглядит основным драйвером просадки; основная слабость в non-paid."
        else:
            statement = f"Flow pattern: {flow_signal}."
        sku_rows.append(
            {
                "entity_scope": "sku",
                "entity_id": product_id,
                "entity_name": internal["product_name"],
                "level": 2,
                "claim_type": "fact",
                "confidence": "high",
                "theme": "channel_break",
                "statement": statement,
                "evidence": "paid_vs_nonpaid_summary short window",
            }
        )

        geo_parts = []
        if as_text(internal.get("geo_signal")):
            geo_parts.append(f"Geo: {internal['geo_signal']}")
        if as_text(internal.get("warehouse_signal")):
            geo_parts.append(f"Warehouse: {internal['warehouse_signal']}")
        if as_text(internal.get("availability_signal")):
            geo_parts.append(f"Availability: {internal['availability_signal']}")
        sku_rows.append(
            {
                "entity_scope": "sku",
                "entity_id": product_id,
                "entity_name": internal["product_name"],
                "level": 2,
                "claim_type": "fact",
                "confidence": "medium",
                "theme": "geo_warehouse_availability",
                "statement": "; ".join(geo_parts),
                "evidence": "geo/warehouse/availability layers",
            }
        )

        capture_status = as_text(external.get("public_search_capture_status"))
        if capture_status == "captured" and captured_direct:
            review_statement = (
                f"В captured WB выдаче есть устойчивые direct competitors; avg rating/top-5 ~ {direct_avg_rating:.2f}, "
                f"avg feedbacks/top-5 ~ {direct_avg_feedbacks:.0f}, у нас rating {own_rating:.2f}, feedbacks {own_feedbacks:.0f}."
            )
            claim_type = "fact"
            confidence = "medium"
        else:
            review_statement = "Публичная WB выдача по этому SKU в текущем цикле не снята полностью; внешняя поисковая конкуренция остается неполной."
            claim_type = "fact"
            confidence = "low"
        sku_rows.append(
            {
                "entity_scope": "sku",
                "entity_id": product_id,
                "entity_name": internal["product_name"],
                "level": 3,
                "claim_type": claim_type,
                "confidence": confidence,
                "theme": "external_context",
                "statement": review_statement,
                "evidence": "external search capture + competitor rings",
            }
        )

        if as_text(internal.get("same_name_product_id_count")) not in {"", "0", "1"}:
            split_statement = f"Есть name-level split по нескольким product_id: {internal.get('same_name_product_ids')}."
            split_confidence = "high"
        else:
            split_statement = "Монолитный SKU-сигнал без заметного same-name split."
            split_confidence = "high"
        sku_rows.append(
            {
                "entity_scope": "sku",
                "entity_id": product_id,
                "entity_name": internal["product_name"],
                "level": 2,
                "claim_type": "fact",
                "confidence": split_confidence,
                "theme": "internal_structure",
                "statement": split_statement,
                "evidence": "sku_variant_check + own_card matching",
            }
        )

        strongest = as_text(internal.get("strongest_causal_signal"))
        if strongest == "availability_constraint_risk":
            hypothesis = "Наиболее рабочая гипотеза: локальные availability/warehouse ограничения усиливают остаточную слабость после календарной поправки."
        elif strongest == "paid_channel_issue_or_budget_shift":
            hypothesis = "Наиболее рабочая гипотеза: слабость partially связана с потерей paid output и/или позиций в платном поиске, а затем усиливается в общем спросе."
        elif strongest == "organic_or_non_paid_demand_drop":
            hypothesis = "Наиболее рабочая гипотеза: локальная потеря органического спроса / поисковой видимости важнее, чем прямое сокращение paid."
        elif strongest == "stable_comparator_holding_up_vs_store":
            hypothesis = "Наиболее рабочая гипотеза: SKU держится лучше фона магазина, значит категория сама по себе не объясняет слабость остальных товаров."
        else:
            hypothesis = f"Наиболее рабочая гипотеза: {strongest}."
        sku_rows.append(
            {
                "entity_scope": "sku",
                "entity_id": product_id,
                "entity_name": internal["product_name"],
                "level": 4,
                "claim_type": "hypothesis",
                "confidence": "medium" if capture_status == "captured" else "low",
                "theme": "working_hypothesis",
                "statement": hypothesis,
                "evidence": "internal residuals + channel/geography + partial external layer",
            }
        )

    store_rows: list[dict[str, Any]] = []
    store_short = next((row for row in inputs["paid_nonpaid"] if row.get("entity_scope") == "store" and row.get("window_label") == "short_7d_vs_prev_28d"), {})
    store_rows.append(
        {
            "entity_scope": "store",
            "entity_id": "__STORE__",
            "entity_name": "STORE_TOTAL",
            "level": 1,
            "claim_type": "fact",
            "confidence": "high",
            "theme": "what_happened",
            "statement": "Главный short-window drag по магазину сидит в спросе и заказах, а не выглядит как единичный всплеск общих расходов.",
            "evidence": "v2 causal drilldown baseline",
        }
    )
    if store_short:
        store_rows.append(
            {
                "entity_scope": "store",
                "entity_id": "__STORE__",
                "entity_name": "STORE_TOTAL",
                "level": 2,
                "claim_type": "fact",
                "confidence": "high",
                "theme": "channel_break",
                "statement": (
                    f"По магазину short paid orders/day delta = {store_short.get('paid_orders_delta_per_day')}, "
                    f"non-paid orders/day delta = {store_short.get('non_paid_orders_delta_per_day')}."
                ),
                "evidence": "paid_vs_nonpaid_summary store short window",
            }
        )
    weak_skus = [row for row in internal_rows if (as_float(row.get("short_residual_profit_after_calendar_per_day")) or 0.0) < 0]
    weak_skus.sort(key=lambda row: as_float(row.get("short_residual_profit_after_calendar_per_day")) or 0.0)
    strong_skus = [row for row in internal_rows if (as_float(row.get("short_residual_profit_after_calendar_per_day")) or 0.0) > 0]
    strong_skus.sort(key=lambda row: -(as_float(row.get("short_residual_profit_after_calendar_per_day")) or 0.0))
    store_rows.append(
        {
            "entity_scope": "store",
            "entity_id": "__STORE__",
            "entity_name": "STORE_TOTAL",
            "level": 3,
            "claim_type": "fact",
            "confidence": "high",
            "theme": "weak_vs_strong",
            "statement": (
                "Внутри selected set есть и остаточно слабые, и resilient SKU: "
                f"weak leaders = {summary_text(weak_skus, name_key='product_name', score_key='short_residual_profit_after_calendar_per_day')}; "
                f"strong comparator = {summary_text(strong_skus, name_key='product_name', score_key='short_residual_profit_after_calendar_per_day', limit=1)}."
            ),
            "evidence": "calendar adjusted residuals",
        }
    )
    captured_count = sum(1 for row in external_rows if row.get("public_search_capture_status") == "captured")
    not_captured_count = sum(1 for row in external_rows if row.get("public_search_capture_status") != "captured")
    store_rows.append(
        {
            "entity_scope": "store",
            "entity_id": "__STORE__",
            "entity_name": "STORE_TOTAL",
            "level": 3,
            "claim_type": "fact",
            "confidence": "medium",
            "theme": "external_coverage",
            "statement": f"Внешний WB search layer сейчас captured по {captured_count} SKU из {len(external_rows)}, не fully captured по {not_captured_count}.",
            "evidence": "external signal layer capture status",
        }
    )
    store_rows.append(
        {
            "entity_scope": "store",
            "entity_id": "__STORE__",
            "entity_name": "STORE_TOTAL",
            "level": 4,
            "claim_type": "hypothesis",
            "confidence": "medium",
            "theme": "working_hypothesis",
            "statement": "Главная рабочая гипотеза по магазину: базовый demand drag общий, но решающие отличия между SKU создают non-paid weakness, локальная geography/warehouse availability и частично внешняя search competition.",
            "evidence": "selected SKU comparative layer",
        }
    )
    return sku_rows, store_rows


def build_own_history_seed(
    inputs: dict[str, list[dict[str, str]]],
    internal_rows: list[dict[str, Any]],
    external_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    internal_map = {as_text(row["product_id"]): row for row in internal_rows}
    query_grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in inputs["search_queries"]:
        query_grouped[as_text(row["nm_id"])].append(row)
    for rows in query_grouped.values():
        rows.sort(key=lambda row: as_int(row.get("query_rank")) or 999)

    rows: list[dict[str, Any]] = []
    for external in external_rows:
        pid = as_text(external["nm_id"])
        internal = internal_map.get(pid, {})
        top_query = (query_grouped.get(pid) or [{}])[0]
        rows.append(
            {
                "snapshot_date": external["snapshot_date"],
                "nm_id": pid,
                "product_name": external["product_name"],
                "public_current_price": external.get("public_current_price"),
                "public_current_sale_price": external.get("public_current_sale_price"),
                "public_current_discount_pct": external.get("public_current_discount_pct"),
                "public_rating": external.get("public_rating"),
                "public_feedbacks": external.get("public_feedbacks"),
                "top_query": top_query.get("search_query", ""),
                "top_query_avg_position_current": top_query.get("avg_position_current", ""),
                "top_query_visibility_current": top_query.get("visibility_current", ""),
                "top_query_orders_current": top_query.get("orders_current", ""),
                "latest_stock_snapshot_date": internal.get("latest_stock_snapshot_date"),
                "latest_stock_available_sum": internal.get("latest_stock_available_sum"),
                "days_cover_14d": internal.get("days_cover_14d"),
                "short_residual_profit_after_calendar_per_day": internal.get("short_residual_profit_after_calendar_per_day"),
                "short_flow_signal": internal.get("short_flow_signal"),
                "short_ad_signal": internal.get("short_ad_signal"),
                "public_search_capture_status": external.get("public_search_capture_status"),
                "seed_status": "started",
            }
        )
    return rows


def build_signal_lists(external_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    usable_now = [
        {
            "signal_name": "calendar_adjusted_profit_residual",
            "layer": "internal",
            "coverage": "selected_skus",
            "why_usable_now": "Already computed and comparable across weak and strong SKU.",
        },
        {
            "signal_name": "paid_vs_non_paid_order_split",
            "layer": "internal",
            "coverage": "selected_skus_and_store",
            "why_usable_now": "Short and medium windows already built from v2 causal layer.",
        },
        {
            "signal_name": "geo_and_warehouse_delta",
            "layer": "internal",
            "coverage": "selected_skus",
            "why_usable_now": "Geo/warehouse deltas and reliability flags are already available.",
        },
        {
            "signal_name": "own_card_identity_and_current_external_snapshot",
            "layer": "external",
            "coverage": "selected_skus",
            "why_usable_now": "Own card matching, current price, discount, rating and feedback count are confirmed.",
        },
        {
            "signal_name": "captured_wb_search_neighbors",
            "layer": "external",
            "coverage": "captured_query_subset_only",
            "why_usable_now": "Current issuance and adjacent competitor set are already captured for part of the SKU set.",
        },
    ]
    need_history = [
        {
            "signal_name": "competitor_price_trend",
            "layer": "external",
            "why_needs_history": "Cannot be reconstructed as guaranteed fact before the day we started capturing it.",
        },
        {
            "signal_name": "competitor_rating_review_trend",
            "layer": "external",
            "why_needs_history": "Current snapshot exists, but trend needs accumulated daily snapshots.",
        },
        {
            "signal_name": "search_position_trend_by_query",
            "layer": "external",
            "why_needs_history": "Current positions exist only for captured dates and queries.",
        },
        {
            "signal_name": "own_price_discount_series",
            "layer": "own_external",
            "why_needs_history": "Current price is available now, but daily evolution needs scheduled storage.",
        },
        {
            "signal_name": "own_query_visibility_series",
            "layer": "own_external",
            "why_needs_history": "Short-window query report is available now, but trend requires repeated collection.",
        },
    ]
    insufficient = [
        {
            "layer_name": "feedbacks_and_questions_api",
            "status": "insufficient_now",
            "why_insufficient": "Current token is scope-blocked on WB feedback/questions endpoints.",
        },
        {
            "layer_name": "full_public_search_coverage",
            "status": "partial_now",
            "why_insufficient": "WB public search returned 429 for part of the target queries on current IP.",
        },
        {
            "layer_name": "cost_complete_week_over_week",
            "status": "not_fully_assembled",
            "why_insufficient": "Current engine has short/medium residual windows but not a dedicated cost-complete WoW layer.",
        },
        {
            "layer_name": "historical_competitor_layer_before_seed_start",
            "status": "not_recoverable",
            "why_insufficient": "Competitor history before seed start cannot be recreated honestly.",
        },
    ]
    return usable_now, need_history, insufficient


def humanize_causal_value(value: str) -> str:
    mapping = {
        "residual_weakness_after_calendar": "остается слабым после календарной поправки",
        "resilient_after_calendar": "держится лучше после календарной поправки",
        "non_paid_weaker_than_paid": "main weakness in non-paid, paid holds better",
        "both_paid_and_non_paid_down": "both paid and non-paid are weak",
        "paid_stable_non_paid_main_drop": "paid stable, main drop in non-paid",
        "local_concentrated_problem": "problem is localized with concentrated hotspots",
        "distributed_with_local_hotspots": "problem is distributed but has local hotspots",
        "insufficient_geo_signal": "geo signal is insufficient for a strong claim",
        "availability_or_snapshot_overlap_risk_present": "availability or snapshot-overlap risk is present",
        "no_clear_snapshot_availability_problem": "no clear availability risk in current snapshot",
        "higher_peer_rating": "captured peers have higher rating",
        "higher_peer_review_count": "captured peers have more reviews",
        "higher_own_price_vs_direct_peers": "our price is higher than captured direct peers",
        "no_clear_card_disadvantage_vs_captured_direct_peers": "no clear card disadvantage vs captured direct peers",
        "direct_competitor_capture_incomplete": "direct competitor capture is incomplete",
        "price_disadvantage_vs_direct_peers": "our price is higher vs direct peers",
        "price_advantage_vs_direct_peers": "our price is lower vs direct peers",
        "price_near_direct_peers": "our price is near direct peers",
        "no_direct_price_comparison": "no direct price comparison yet",
        "rating_weaker_than_direct_peers": "our rating is weaker than direct peers",
        "rating_stronger_than_direct_peers": "our rating is stronger than direct peers",
        "rating_near_direct_peers": "our rating is near direct peers",
        "no_direct_rating_comparison": "no direct rating comparison yet",
        "reviews_weaker_than_direct_peers": "our review count is weaker than direct peers",
        "reviews_stronger_than_direct_peers": "our review count is stronger than direct peers",
        "reviews_near_direct_peers": "our review count is near direct peers",
        "no_direct_review_comparison": "no direct review comparison yet",
        "availability_and_warehouse_constraints_are_most_likely_amplifying_non_paid_weakness": "availability and warehouse constraints most likely amplify non-paid weakness",
        "paid_weakness_plus_external_competition_pressure": "paid weakness is likely reinforced by visible external competition",
        "paid_weakness_with_incomplete_external_confirmation": "paid weakness is visible, but external confirmation is still incomplete",
        "organic_visibility_loss_plus_stronger_competitor_proof_points": "organic weakness likely combines with stronger captured competitors",
        "organic_visibility_or_non_paid_demand_drop": "organic visibility or non-paid demand loss is the main working hypothesis",
        "resilient_comparator_holding_up_better_than_store_background": "SKU holds better than store background and works as a stable comparator",
    }
    if not value:
        return ""
    parts = [part.strip() for part in value.split(";") if part.strip()]
    if len(parts) > 1:
        return "; ".join(mapping.get(part, part) for part in parts)
    return mapping.get(value, value)


def build_report(
    internal_rows: list[dict[str, Any]],
    external_rows: list[dict[str, Any]],
    sku_causal_profiles: list[dict[str, Any]],
    sku_fact_rows: list[dict[str, Any]],
    store_fact_rows: list[dict[str, Any]],
    usable_now: list[dict[str, Any]],
    need_history: list[dict[str, Any]],
    insufficient: list[dict[str, Any]],
) -> str:
    weak_rows = [row for row in internal_rows if (as_float(row.get("short_residual_profit_after_calendar_per_day")) or 0.0) < 0]
    weak_rows.sort(key=lambda row: as_float(row.get("short_residual_profit_after_calendar_per_day")) or 0.0)
    strong_rows = [row for row in internal_rows if (as_float(row.get("short_residual_profit_after_calendar_per_day")) or 0.0) > 0]
    strong_rows.sort(key=lambda row: -(as_float(row.get("short_residual_profit_after_calendar_per_day")) or 0.0))
    external_captured = [row for row in external_rows if row.get("public_search_capture_status") == "captured"]

    lines = [
        "# WB Product Core Engine v1",
        "",
        f"Date: {now_iso()}",
        "",
        "## Goal",
        "",
        "- This pack turns the earlier checks into a reusable product core:",
        "  - internal causal layer,",
        "  - external current layer,",
        "  - weak vs strong comparison,",
        "  - fact vs hypothesis separation,",
        "  - seed history for signals that must be captured from now on.",
        "",
        "## Store Picture",
        "",
        "- Selected weak SKU still show residual weakness after calendar adjustment; the strongest negative residual remains on `Феи Единороги`.",
        f"- Within the same pack there is at least one resilient comparator: `{strong_rows[0]['product_name']}` with positive residual profit/day.",
        f"- External WB search capture is partial but already useful: `{len(external_captured)}` SKU have captured public issuance in the current cycle.",
        "- Geography stays in the engine as one diagnostic layer among channel, availability, own-card state and competitor context; it is not treated as the default cause by itself.",
        "",
        "## Unified SKU Causal Profiles",
        "",
        md_table(
            [
                {
                    "product_id": row["product_id"],
                    "product_name": row["product_name"],
                    "comparison_role": row["comparison_role"],
                    "what_happened": humanize_causal_value(as_text(row["what_happened"])),
                    "paid_or_non_paid": humanize_causal_value(as_text(row["paid_or_non_paid"])),
                    "local_or_everywhere": humanize_causal_value(as_text(row["local_or_everywhere"])),
                    "stock_problem_signs": humanize_causal_value(as_text(row["stock_problem_signs"])),
                    "stronger_competitor_signs": humanize_causal_value(as_text(row["stronger_competitor_signs"])),
                    "card_weaker_signs": humanize_causal_value(as_text(row["card_weaker_signs"])),
                    "strongest_working_hypothesis": humanize_causal_value(as_text(row["strongest_working_hypothesis"])),
                }
                for row in sku_causal_profiles
            ],
            [
                "product_id",
                "product_name",
                "comparison_role",
                "what_happened",
                "paid_or_non_paid",
                "local_or_everywhere",
                "stock_problem_signs",
                "stronger_competitor_signs",
                "card_weaker_signs",
                "strongest_working_hypothesis",
            ],
        ),
        "",
        "## Own Card vs Captured Direct Peers",
        "",
        md_table(
            [
                {
                    "product_id": row["product_id"],
                    "product_name": row["product_name"],
                    "our_sale_price": row["our_sale_price"],
                    "avg_direct_comp_price": row["avg_direct_comp_price"],
                    "our_rating": row["our_rating"],
                    "avg_direct_comp_rating": row["avg_direct_comp_rating"],
                    "our_feedbacks": row["our_feedbacks"],
                    "avg_direct_comp_feedbacks": row["avg_direct_comp_feedbacks"],
                    "direct_competitor_count_captured": row["direct_competitor_count_captured"],
                }
                for row in sku_causal_profiles
            ],
            [
                "product_id",
                "product_name",
                "our_sale_price",
                "avg_direct_comp_price",
                "our_rating",
                "avg_direct_comp_rating",
                "our_feedbacks",
                "avg_direct_comp_feedbacks",
                "direct_competitor_count_captured",
            ],
        ),
        "",
        "## Causal Layering",
        "",
        md_table(
            [
                {
                    "product_id": row["product_id"],
                    "product_name": row["product_name"],
                    "where_happened": row["where_happened"],
                    "what_is_proven": row["what_is_proven"],
                    "what_is_hypothesis": row["what_is_hypothesis"],
                }
                for row in sku_causal_profiles
            ],
            ["product_id", "product_name", "where_happened", "what_is_proven", "what_is_hypothesis"],
        ),
        "",
        "## Store Facts vs Hypotheses",
        "",
        md_table(
            [
                {
                    "level": row["level"],
                    "claim_type": row["claim_type"],
                    "theme": row["theme"],
                    "statement": row["statement"],
                    "confidence": row["confidence"],
                }
                for row in store_fact_rows
            ],
            ["level", "claim_type", "theme", "statement", "confidence"],
        ),
        "",
        "## Signals",
        "",
        "Usable now:",
        "",
        md_table(usable_now, ["signal_name", "layer", "coverage", "why_usable_now"]),
        "",
        "Need accumulated history:",
        "",
        md_table(need_history, ["signal_name", "layer", "why_needs_history"]),
        "",
        "Insufficient layers:",
        "",
        md_table(insufficient, ["layer_name", "status", "why_insufficient"]),
        "",
        "## Boundary",
        "",
        "- This pack does not output client recommendations.",
        "- Facts and hypotheses are separated into dedicated CSV maps.",
        "- Public competitor history before the seed start date is not invented.",
        "- Full WB public search coverage is still partial because part of the queries returned `429` in the current environment.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    inputs = load_inputs()
    target_ids = select_targets(inputs)

    internal_rows = build_internal_full_layer(inputs, target_ids)
    external_rows = build_external_full_layer(inputs, target_ids)
    sku_causal_profile_rows = build_sku_causal_profiles(inputs, internal_rows, external_rows)
    competitor_rings_rows = build_competitor_rings(inputs, target_ids)
    position_rows = build_position_snapshot(inputs, target_ids)
    paid_rows = build_paid_nonpaid(inputs, target_ids)
    geo_warehouse_rows = build_geo_warehouse_layer(inputs, target_ids)
    availability_rows = build_availability_layer(inputs, target_ids)
    calendar_rows = build_calendar_residuals(inputs, target_ids)
    sku_fact_rows, store_fact_rows = build_fact_vs_hypothesis_maps(inputs, internal_rows, external_rows)
    own_history_seed_rows = build_own_history_seed(inputs, internal_rows, external_rows)
    competitor_history_seed_rows = inputs["competitor_history_seed"]
    usable_now_rows, need_history_rows, insufficient_rows = build_signal_lists(external_rows)

    write_csv(INTERNAL_FULL_CSV, internal_rows)
    write_csv(EXTERNAL_FULL_CSV, external_rows)
    write_csv(COMPETITOR_RINGS_CSV, competitor_rings_rows)
    write_csv(POSITION_SNAPSHOT_CSV, position_rows)
    write_csv(PAID_NONPAID_CSV, paid_rows)
    write_csv(GEO_WAREHOUSE_CSV, geo_warehouse_rows)
    write_csv(AVAILABILITY_CSV, availability_rows)
    write_csv(CALENDAR_RESIDUALS_CSV, calendar_rows)
    write_csv(SKU_CAUSAL_PROFILE_CSV, sku_causal_profile_rows)
    write_csv(SKU_FACT_HYPOTHESIS_CSV, sku_fact_rows)
    write_csv(STORE_FACT_HYPOTHESIS_CSV, store_fact_rows)
    write_csv(COMPETITOR_HISTORY_SEED_CSV, competitor_history_seed_rows)
    write_csv(OWN_HISTORY_SEED_CSV, own_history_seed_rows)
    write_csv(USABLE_NOW_CSV, usable_now_rows)
    write_csv(NEED_HISTORY_CSV, need_history_rows)
    write_csv(INSUFFICIENT_LAYERS_CSV, insufficient_rows)

    DOC_PATH.write_text(
        build_report(
            internal_rows,
            external_rows,
            sku_causal_profile_rows,
            sku_fact_rows,
            store_fact_rows,
            usable_now_rows,
            need_history_rows,
            insufficient_rows,
        ),
        encoding="utf-8",
    )

    print(f"Built product core engine into {OUTPUT_DIR}")
    print(f"Report: {DOC_PATH}")


if __name__ == "__main__":
    main()
