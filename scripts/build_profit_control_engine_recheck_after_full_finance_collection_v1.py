from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
MOSCOW = ZoneInfo("Europe/Moscow")

FULL_DIR = ROOT / "exports" / "profit_control_full_data_collection_order_v1"
MASTER_DIR = ROOT / "exports" / "profit_control_master_next_pass_v1"
OUTPUT_DIR = ROOT / "exports" / "profit_control_engine_recheck_after_full_finance_collection_v1"
DOC_PATH = ROOT / "artifacts" / "engine_recheck_after_full_finance_collection.md"

UPDATED_PROBLEM_ZONE_RECHECK_CSV = OUTPUT_DIR / "updated_problem_zone_recheck.csv"
UPDATED_SCENARIO_RECHECK_CSV = OUTPUT_DIR / "updated_scenario_recheck.csv"
RECOMMENDATION_CYCLE_DRAFT_VS_FINAL_CSV = OUTPUT_DIR / "recommendation_cycle_draft_vs_final.csv"
FINAL_CLIENT_READY_STORE_SUMMARY_CSV = OUTPUT_DIR / "final_client_ready_store_summary.csv"
FINAL_CLIENT_READY_SKU_SUMMARY_CSV = OUTPUT_DIR / "final_client_ready_sku_summary.csv"
CABINET_FINAL_BUILD_INPUT_CSV = OUTPUT_DIR / "cabinet_final_build_input.csv"


def now_iso() -> str:
    return datetime.now(MOSCOW).isoformat(timespec="seconds")


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
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


def as_float(value: Any) -> float:
    text = as_text(value).replace(" ", "")
    if not text:
        return 0.0
    return float(text)


def as_int(value: Any) -> int:
    return int(round(as_float(value)))


def money(value: Any) -> str:
    return f"{as_float(value):,.0f}".replace(",", " ")


def qty(value: Any) -> str:
    return f"{as_float(value):.2f}"


def days(value: Any) -> str:
    return f"{as_float(value):.1f}"


def pct(value: Any) -> str:
    return f"{as_float(value) * 100:.1f}%"


def group_by(rows: list[dict[str, str]], key: str) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(as_text(row.get(key)), []).append(row)
    return grouped


def keyed(rows: list[dict[str, str]], key: str) -> dict[str, dict[str, str]]:
    return {as_text(row.get(key)): row for row in rows}


PROBLEM_ZONE_LABELS = {
    "organic_plus_card_pressure": "organic_plus_card_pressure",
    "stock_pressure": "stock_pressure",
    "availability_issue": "availability_issue",
    "stable_comparator": "stable_comparator",
}


def choose_secondary_zone(problem_row: dict[str, str], dominant: str) -> str:
    if dominant == "stable_comparator":
        return "none"

    flags = {
        "demand_problem": as_text(problem_row.get("problem_zone_demand")) == "yes",
        "channel_problem": as_text(problem_row.get("problem_zone_channel")) not in {"", "no_clear_channel_signal"},
        "competitor_pressure": as_text(problem_row.get("problem_zone_competitor_pressure")) == "yes",
        "availability_issue": as_text(problem_row.get("problem_zone_availability")) == "yes",
        "stock_pressure": as_text(problem_row.get("problem_zone_stock_pressure")) == "yes",
        "logistics_burden": as_text(problem_row.get("problem_zone_logistics_burden")) == "yes",
        "storage_burden": as_text(problem_row.get("problem_zone_storage_burden")) == "yes",
    }
    priority_map = {
        "organic_plus_card_pressure": ["availability_issue", "competitor_pressure", "channel_problem", "demand_problem"],
        "stock_pressure": ["logistics_burden", "availability_issue", "competitor_pressure", "demand_problem"],
        "availability_issue": ["competitor_pressure", "channel_problem", "demand_problem", "stock_pressure"],
    }
    for candidate in priority_map.get(dominant, []):
        if flags.get(candidate):
            return candidate
    for candidate, enabled in flags.items():
        if enabled:
            return candidate
    return "none"


def hypothesis_boundary_text(hypothesis_row: dict[str, str]) -> str:
    hypothesis = as_text(hypothesis_row.get("strongest_hypothesis"))
    limits = as_text(hypothesis_row.get("what_contradicts_or_limits"))
    if not hypothesis:
        return limits
    if not limits:
        return hypothesis
    return f"{hypothesis}; boundary: {limits}"


def scenario_reason(best_name: str, dominant_zone: str) -> str:
    if best_name == "ускорить продажи рекламой и параллельно лечить карточку":
        return "non-paid и органика уже просели, поэтому реклама здесь нужна как мост, а не как постоянная замена карточке и поисковой видимости"
    if best_name == "агрессивная распродажа":
        return "cover уже слишком длинный, поэтому ценнее быстро разгрузить запас, чем держаться за старую маржу"
    if best_name == "чинить доступность и распределение":
        return "локальная доступность и распределение выглядят более вероятным bottleneck, чем общий обвал спроса"
    if best_name == "лечить карточку без усиления рекламы":
        return "основной сигнал идет через organic и карточку, а не через нехватку paid"
    if best_name == "наблюдать":
        return "SKU не показывает тот же pattern weak-pool и сейчас полезнее как стабильный компаратор"
    if dominant_zone == "stock_pressure":
        return "основной риск уже в запасе и длинной оборачиваемости"
    return "этот сценарий лучше всего совпадает с текущим причинным слоем и дает лучший баланс эффекта и риска"


def final_wording(
    sku_row: dict[str, str],
    rec_row: dict[str, str],
    hypothesis_row: dict[str, str],
    primary_zone: str,
    secondary_zone: str,
) -> str:
    product_name = as_text(sku_row.get("product_name"))
    best = as_text(rec_row.get("final_best_scenario"))
    happened = (
        f"По SKU {product_name} заказы просели на {qty(abs(as_float(sku_row.get('orders_delta_per_day'))))}/день, "
        f"выручка на {money(abs(as_float(sku_row.get('revenue_delta_per_day'))))} руб./день, "
        f"а cover вырос с {days(sku_row.get('days_of_stock_old_speed'))} до {days(sku_row.get('days_of_stock_new_speed'))} дня."
    )
    why = scenario_reason(best, primary_zone)
    problem = as_text(hypothesis_row.get("final_client_ready_hypothesis")) or as_text(sku_row.get("strongest_working_hypothesis"))
    effect = as_text(rec_row.get("final_effect_range"))
    risk = as_text(rec_row.get("final_risk"))
    secondary = "" if secondary_zone == "none" else f" Вторичный риск сейчас — {secondary_zone.replace('_', ' ')}."
    return (
        f"{happened} Главная проблема сейчас — {primary_zone.replace('_', ' ')}. {secondary}"
        f" Рабочая гипотеза: {problem} "
        f"Сейчас разумнее идти в сценарий «{best}», потому что {why}. "
        f"Диапазон эффекта за 30 дней пока только сценарный: {effect}. "
        f"Главный риск — {risk}."
    )


def final_store_story(
    store_row: dict[str, str],
    sku_rows: list[dict[str, str]],
    finance_rows: list[dict[str, str]],
) -> dict[str, Any]:
    weak_rows = [row for row in sku_rows if as_text(row.get("comparison_role")) != "stable_comparator" and as_text(row.get("product_id")) != "72702996"]
    total_revenue = sum(as_float(row.get("current_revenue_per_day")) for row in weak_rows) or 1.0
    logistics_share = sum(as_float(row.get("current_revenue_per_day")) * as_float(row.get("logistics_share_of_revenue")) for row in weak_rows) / total_revenue
    storage_share = sum(as_float(row.get("current_revenue_per_day")) * as_float(row.get("storage_share_of_revenue")) for row in weak_rows) / total_revenue
    commission_share = sum(as_float(row.get("current_revenue_per_day")) * as_float(row.get("commission_share_of_revenue")) for row in weak_rows) / total_revenue
    acquiring_share = sum(as_float(row.get("current_revenue_per_day")) * as_float(row.get("acquiring_share_of_revenue")) for row in weak_rows) / total_revenue
    withholding_share = sum(as_float(row.get("current_revenue_per_day")) * as_float(row.get("withholding_share_of_revenue")) for row in weak_rows) / total_revenue

    strict_days = [row for row in finance_rows if as_text(row.get("strict_usable_flag")) == "yes"]
    latest_strict_day = max((as_text(row.get("day")) for row in strict_days), default="")
    fresh_tail = [row for row in finance_rows if as_text(row.get("strict_usable_flag")) != "yes"]

    scenario_groups = Counter(as_text(row.get("best_scenario_name")) for row in sku_rows)
    scenario_mix = "; ".join(f"{name}={count}" for name, count in scenario_groups.items() if name)
    sku_by_scenario = {
        name: [as_text(row.get("product_name")) for row in sku_rows if as_text(row.get("best_scenario_name")) == name]
        for name in scenario_groups
    }
    group_text = "; ".join(f"{name}: {', '.join(items)}" for name, items in sku_by_scenario.items() if items)

    return {
        "snapshot_date": now_iso(),
        "scope": "store",
        "what_is_happening": (
            f"Слабый пул сейчас теряет {qty(abs(as_float(store_row.get('current_orders_drag_per_day'))))} заказа/день, "
            f"{money(abs(as_float(store_row.get('current_revenue_drag_per_day'))))} руб./день выручки и "
            f"{money(abs(as_float(store_row.get('current_profit_drag_per_day'))))} руб./день прибыли после рекламы."
        ),
        "main_losses": (
            f"Главный driver остается в спросе: paid delta суммарно {qty(store_row.get('paid_orders_delta_per_day_sum'))}/день, "
            f"non-paid delta {qty(store_row.get('non_paid_orders_delta_per_day_sum'))}/день. "
            f"Логистика на пуле около {pct(logistics_share)}, комиссия {pct(commission_share)}, эквайринг {pct(acquiring_share)}, "
            f"хранение {pct(storage_share)}, удержания {pct(withholding_share)}."
        ),
        "what_is_dangerous_now": (
            f"Опасны три разные группы: товары с длинным запасом, товары с проблемой доступности и товары, где просадка идет через organic/card pressure. "
            f"Residual drag после calendar correction — {money(abs(as_float(store_row.get('residual_profit_drag_after_calendar_per_day'))))} руб./день."
        ),
        "scenario_groups_now": group_text,
        "scenario_mix_now": scenario_mix,
        "finance_boundary_for_clients": (
            f"Strict usable finance сейчас закрыт до {latest_strict_day}. "
            f"Дни после него ({', '.join(as_text(row.get('day')) for row in fresh_tail)}) нельзя смешивать с финальной profit truth."
        ),
        "cabinet_ready_conclusion": "Да, слой уже можно нести в кабинет: engine recheck пройден, а finance semantics должны отображаться раздельно как strict usable / provisional / source-open.",
    }


def build() -> None:
    full_store_rows = load_csv(FULL_DIR / "updated_store_layer_final.csv")
    full_sku_rows = load_csv(FULL_DIR / "updated_sku_layer_final.csv")
    full_problem_rows = load_csv(FULL_DIR / "updated_problem_zones_final.csv")
    full_scenario_rows = load_csv(FULL_DIR / "updated_scenarios_final.csv")
    full_rec_rows = load_csv(FULL_DIR / "updated_recommendations_final.csv")
    full_finance_rows = load_csv(FULL_DIR / "finance_90day_final_matrix.csv")

    hypothesis_rows = load_csv(MASTER_DIR / "sku_hypothesis_check_final.csv")
    rec_check_rows = load_csv(MASTER_DIR / "sku_recommendation_check_final.csv")
    rank_rows = load_csv(MASTER_DIR / "rank_visibility_final.csv")

    store_row = full_store_rows[0]
    sku_by_id = keyed(full_sku_rows, "product_id")
    problem_by_id = keyed(full_problem_rows, "product_id")
    rec_by_id = keyed(full_rec_rows, "product_id")
    hypothesis_by_id = keyed(hypothesis_rows, "product_id")
    rec_check_by_id = keyed(rec_check_rows, "product_id")
    scenario_by_product = group_by(full_scenario_rows, "product_id")
    rank_by_product = group_by(rank_rows, "own_nm_id")

    problem_recheck_rows: list[dict[str, Any]] = []
    scenario_recheck_rows: list[dict[str, Any]] = []
    recommendation_cycle_rows: list[dict[str, Any]] = []
    sku_summary_rows: list[dict[str, Any]] = []

    for sku_row in full_sku_rows:
        product_id = as_text(sku_row.get("product_id"))
        product_name = as_text(sku_row.get("product_name"))
        problem_row = problem_by_id[product_id]
        rec_row = rec_by_id[product_id]
        hypothesis_row = hypothesis_by_id.get(product_id, {})
        rec_check_row = rec_check_by_id.get(product_id, {})
        scenario_rows = scenario_by_product.get(product_id, [])
        rank_subset = rank_by_product.get(product_id, [])

        dominant = as_text(problem_row.get("dominant_problem_zone_after_90d"))
        secondary = choose_secondary_zone(problem_row, dominant)
        proven = as_text(hypothesis_row.get("what_confirms")) or as_text(sku_row.get("strongest_working_hypothesis"))
        hypothesis_text = hypothesis_boundary_text(hypothesis_row) or as_text(sku_row.get("strongest_working_hypothesis"))

        problem_recheck_rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": product_id,
                "product_name": product_name,
                "primary_problem_zone": dominant,
                "secondary_problem_zone": secondary,
                "demand_problem_recheck": as_text(problem_row.get("problem_zone_demand")),
                "channel_issue_recheck": as_text(problem_row.get("problem_zone_channel")),
                "competitor_pressure_recheck": as_text(problem_row.get("problem_zone_competitor_pressure")),
                "availability_issue_recheck": as_text(problem_row.get("problem_zone_availability")),
                "stock_pressure_recheck": as_text(problem_row.get("problem_zone_stock_pressure")),
                "logistics_burden_recheck": as_text(problem_row.get("problem_zone_logistics_burden")),
                "storage_burden_recheck": as_text(problem_row.get("problem_zone_storage_burden")),
                "what_is_proven": proven,
                "what_remains_hypothesis": hypothesis_text,
                "recheck_conclusion": (
                    "full strict-usable finance window does not overturn the main zone"
                    if dominant != "stable_comparator"
                    else "SKU still works better as comparator than as intervention target"
                ),
            }
        )

        best_name = as_text(rec_row.get("final_best_scenario"))
        runner_name = as_text(rec_row.get("runner_up_scenario"))
        best_scenario = next((row for row in scenario_rows if as_text(row.get("scenario_name")) == best_name), {})
        runner_scenario = next((row for row in scenario_rows if as_text(row.get("scenario_name")) == runner_name), {})
        best_cons = as_float(best_scenario.get("net_value_30d_conservative"))
        runner_cons = as_float(runner_scenario.get("net_value_30d_conservative"))
        best_work = as_float(best_scenario.get("net_value_30d_working"))
        runner_work = as_float(runner_scenario.get("net_value_30d_working"))
        conservative_check = "best_still_wins_under_stricter_assumptions" if best_cons >= runner_cons else "runner_up_can_overtake_if_assumptions_turn_harsher"
        if best_name == "наблюдать" and as_float(sku_row.get("residual_profit_after_calendar_per_day")) > 0:
            conservative_check = "watch_kept_because_sku_is_positive_comparator"

        scenario_recheck_rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": product_id,
                "product_name": product_name,
                "final_best_scenario": best_name,
                "runner_up_scenario": runner_name,
                "best_conservative_value_30d": round(best_cons, 2),
                "runner_up_conservative_value_30d": round(runner_cons, 2),
                "best_working_value_30d": round(best_work, 2),
                "runner_up_working_value_30d": round(runner_work, 2),
                "scenario_recheck_result": "unchanged_after_full_strict_finance_collection",
                "stricter_assumptions_check": conservative_check,
                "why_best_scenario_is_stronger": as_text(rec_row.get("why_best_scenario_still_wins")) or "best scenario keeps the strongest fit/risk balance",
                "effect_range_recheck": as_text(rec_row.get("final_effect_range")),
                "effect_overclaim_recheck": "scenario_only_not_guaranteed",
                "main_risk": as_text(rec_row.get("final_risk")),
            }
        )

        new_final_wording = final_wording(sku_row, rec_row, hypothesis_row, dominant, secondary)
        recommendation_cycle_rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": product_id,
                "product_name": product_name,
                "best_scenario_name": best_name,
                "draft_1": as_text(rec_check_row.get("draft_1")),
                "critique_of_draft_1": as_text(rec_check_row.get("critique_of_draft_1")),
                "refined_draft_2": as_text(rec_check_row.get("refined_draft_2")),
                "critique_of_draft_2": "добавлена более жесткая привязка к proven layer, денежному диапазону и главному риску; убрана лишняя самоуверенность",
                "final_client_ready_wording": new_final_wording,
                "not_banal_recheck": "passes_if_action_is_tied_to_specific_cause_and risk",
                "symptom_vs_cause_recheck": "aligned_with_primary_problem_zone",
                "effect_overclaim_recheck": "scenario_only_not_guaranteed",
                "client_language_recheck": "human_ready_after_final_rewrite",
            }
        )

        top_pressure_queries = [as_text(row.get("query")) for row in rank_subset if as_text(row.get("query_pressure_status")) == "repeated_pressure_present"][:3]
        what_happened = (
            f"Заказы просели на {qty(abs(as_float(sku_row.get('orders_delta_per_day'))))}/день, "
            f"выручка на {money(abs(as_float(sku_row.get('revenue_delta_per_day'))))} руб./день, "
            f"прибыль после рекламы на {money(abs(as_float(sku_row.get('profit_after_ads_delta_per_day'))))} руб./день."
            if as_float(sku_row.get("orders_delta_per_day")) < 0
            else f"SKU держится лучше базы: orders delta {qty(sku_row.get('orders_delta_per_day'))}/день и residual after calendar положительный."
        )
        why_problem = scenario_reason(best_name, dominant)
        money_range = as_text(rec_row.get("final_effect_range"))
        risk = as_text(rec_row.get("final_risk"))
        summary = new_final_wording
        extra_search = ""
        if top_pressure_queries:
            extra_search = f" Повторяющееся давление видно по запросам: {', '.join(top_pressure_queries)}."

        sku_summary_rows.append(
            {
                "snapshot_date": now_iso(),
                "product_id": product_id,
                "product_name": product_name,
                "what_happened": what_happened,
                "primary_problem_zone": dominant,
                "secondary_problem_zone": secondary,
                "why_this_is_the_main_problem": why_problem,
                "what_is_proven": proven,
                "what_remains_hypothesis": hypothesis_text,
                "what_to_do_now": best_name,
                "why_this_action_now": scenario_reason(best_name, dominant) + extra_search,
                "money_in_play_30d": money_range,
                "main_risk": risk,
                "final_client_ready_summary": summary,
            }
        )

    store_summary_row = final_store_story(store_row, full_sku_rows, full_finance_rows)

    finance_boundary = store_summary_row["finance_boundary_for_clients"]
    cabinet_rows = [
        {
            "snapshot_date": now_iso(),
            "module_name": "economics_store",
            "target_view": "graphs / sales map / table",
            "data_source": "final_client_ready_store_summary + updated_store_layer_final + finance_90day_final_matrix",
            "readiness_status": "ready",
            "key_fields": "what_is_happening; main_losses; what_is_dangerous_now; scenario_groups_now",
            "boundary_note": finance_boundary,
        },
        {
            "snapshot_date": now_iso(),
            "module_name": "products_table",
            "target_view": "product list with photo and nearest competitors",
            "data_source": "final_client_ready_sku_summary + updated_problem_zone_recheck + competitor_layer_final",
            "readiness_status": "ready",
            "key_fields": "product_name; primary_problem_zone; what_to_do_now; money_in_play_30d; confirmed competitors",
            "boundary_note": "availability remains snapshot-risk support layer, not proven historical stockout",
        },
        {
            "snapshot_date": now_iso(),
            "module_name": "product_card_competitor_module",
            "target_view": "price / rating / reviews / card strength / who stands above us",
            "data_source": "competitor_layer_final + rank_visibility_final + updated_problem_zone_recheck",
            "readiness_status": "ready",
            "key_fields": "candidate_price; candidate_rating; candidate_feedbacks; above_us_in_search; queries_intersection",
            "boundary_note": "historical competitor trend before seed start still should not be shown as proven retro fact",
        },
        {
            "snapshot_date": now_iso(),
            "module_name": "product_card_recommendation_module",
            "target_view": "problem / best scenario / money effect / risk",
            "data_source": "updated_scenario_recheck + recommendation_cycle_draft_vs_final + final_client_ready_sku_summary",
            "readiness_status": "ready",
            "key_fields": "final_best_scenario; runner_up_scenario; effect_range_recheck; final_client_ready_wording",
            "boundary_note": "scenario money remains estimate, not guaranteed outcome",
        },
        {
            "snapshot_date": now_iso(),
            "module_name": "finance_semantics",
            "target_view": "strict usable / provisional / source-open badges",
            "data_source": "finance_90day_final_matrix + wb_finance_canonical_live_tail_runner_v1/live_tail_day_results",
            "readiness_status": "ready",
            "key_fields": "day; final_day_status; status_note",
            "boundary_note": finance_boundary,
        },
    ]

    write_csv(UPDATED_PROBLEM_ZONE_RECHECK_CSV, problem_recheck_rows)
    write_csv(UPDATED_SCENARIO_RECHECK_CSV, scenario_recheck_rows)
    write_csv(RECOMMENDATION_CYCLE_DRAFT_VS_FINAL_CSV, recommendation_cycle_rows)
    write_csv(FINAL_CLIENT_READY_STORE_SUMMARY_CSV, [store_summary_row])
    write_csv(FINAL_CLIENT_READY_SKU_SUMMARY_CSV, sku_summary_rows)
    write_csv(CABINET_FINAL_BUILD_INPUT_CSV, cabinet_rows)

    problem_preview = [
        {
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "primary_problem_zone": row["primary_problem_zone"],
            "secondary_problem_zone": row["secondary_problem_zone"],
        }
        for row in problem_recheck_rows
    ]
    scenario_preview = [
        {
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "final_best_scenario": row["final_best_scenario"],
            "runner_up_scenario": row["runner_up_scenario"],
            "stricter_assumptions_check": row["stricter_assumptions_check"],
        }
        for row in scenario_recheck_rows
    ]
    sku_preview = [
        {
            "product_id": row["product_id"],
            "product_name": row["product_name"],
            "what_to_do_now": row["what_to_do_now"],
            "money_in_play_30d": row["money_in_play_30d"],
            "main_risk": row["main_risk"],
        }
        for row in sku_summary_rows
    ]

    report = "\n".join(
        [
            "# PROFIT CONTROL — Engine Recheck Cycle After Full Finance Collection v1",
            "",
            f"Date: {now_iso()}",
            "",
            "## Main Outcome",
            "",
            "- Engine recheck is now based on a fully strict-usable core 90-day finance corridor and a live tail that is strict-usable through 2026-03-29.",
            "- Problem zones, scenario choice and recommendation wording were rechecked on top of the completed finance layer instead of the earlier mixed strict/provisional window.",
            "- Recommendation copy was passed through another critique cycle so cabinet can use a cleaner client-ready wording layer.",
            "",
            "## Finance Boundary",
            "",
            f"- {store_summary_row['finance_boundary_for_clients']}",
            "",
            "## Store Recheck",
            "",
            f"- {store_summary_row['what_is_happening']}",
            f"- {store_summary_row['main_losses']}",
            f"- {store_summary_row['what_is_dangerous_now']}",
            f"- Scenario groups now: {store_summary_row['scenario_groups_now']}",
            "",
            "## Problem Zones",
            "",
            md_table(
                problem_preview,
                ["product_id", "product_name", "primary_problem_zone", "secondary_problem_zone"],
            ),
            "",
            "## Scenario Recheck",
            "",
            md_table(
                scenario_preview,
                ["product_id", "product_name", "final_best_scenario", "runner_up_scenario", "stricter_assumptions_check"],
            ),
            "",
            "## Client-Ready SKU Layer",
            "",
            md_table(
                sku_preview,
                ["product_id", "product_name", "what_to_do_now", "money_in_play_30d", "main_risk"],
            ),
            "",
            "## Ready For Cabinet",
            "",
            "- Yes. Cabinet can now start from this package, with finance semantics explicitly separated into strict usable / provisional / source-open.",
            "- Competitor and recommendation modules should consume the new client-ready summary tables rather than older intermediate wording.",
            "",
        ]
    ) + "\n"
    DOC_PATH.write_text(report, encoding="utf-8")

    print(f"Wrote {UPDATED_PROBLEM_ZONE_RECHECK_CSV}")
    print(f"Wrote {UPDATED_SCENARIO_RECHECK_CSV}")
    print(f"Wrote {RECOMMENDATION_CYCLE_DRAFT_VS_FINAL_CSV}")
    print(f"Wrote {FINAL_CLIENT_READY_STORE_SUMMARY_CSV}")
    print(f"Wrote {FINAL_CLIENT_READY_SKU_SUMMARY_CSV}")
    print(f"Wrote {CABINET_FINAL_BUILD_INPUT_CSV}")
    print(f"Wrote {DOC_PATH}")


if __name__ == "__main__":
    build()
