from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

RULE_LABELS: dict[str, str] = {
    "negative_profit_after_ads": "Убыток после рекламы",
    "ads_eating_profit": "Реклама съедает прибыль",
    "ads_without_sales": "Реклама без продаж",
    "high_logistics_share": "Слишком дорогая логистика",
    "low_margin_after_ads": "Слишком низкая маржа",
    "stockout_risk": "Риск дефицита",
}

RULE_TO_ACTION_CODE: dict[str, str] = {
    "negative_profit_after_ads": "fix_unprofitable_ads",
    "ads_eating_profit": "optimize_ads_profitability",
    "ads_without_sales": "stop_wasteful_ads",
    "stockout_risk": "replenish_stock",
    "high_logistics_share": "reduce_logistics_burden",
    "low_margin_after_ads": "improve_margin",
}

ACTION_TITLES: dict[str, str] = {
    "fix_unprofitable_ads": "Вывести товар из убытка после рекламы",
    "optimize_ads_profitability": "Снизить неокупаемые расходы на рекламу",
    "stop_wasteful_ads": "Остановить рекламу без продаж",
    "replenish_stock": "Убрать риск дефицита по товару",
    "reduce_logistics_burden": "Снизить давление логистики на прибыль",
    "improve_margin": "Поднять маржу после рекламы до безопасной зоны",
    "investigate_issue": "Разобрать ключевую проблему товара",
}

WHERE_TO_DO_IT: dict[str, str] = {
    "fix_unprofitable_ads": "В кабинете рекламы по этому товару и в настройках цены.",
    "optimize_ads_profitability": "В кабинете рекламы по этому товару.",
    "stop_wasteful_ads": "В кабинете рекламы и в карточке товара.",
    "replenish_stock": "В планировании поставок и пополнения остатков.",
    "reduce_logistics_burden": "В настройках поставки, упаковки и цены товара.",
    "improve_margin": "В цене, скидке, себестоимости и рекламном бюджете товара.",
    "investigate_issue": "В карточке товара, рекламе и финансовых настройках.",
}

WHAT_TO_DO: dict[str, str] = {
    "fix_unprofitable_ads": "Сократите дневной бюджет рекламы по товару на 20-30% на 7 дней.",
    "optimize_ads_profitability": "Снизьте рекламный бюджет по товару на 20-30% и не увеличивайте его до проверки.",
    "stop_wasteful_ads": "Поставьте рекламу товара на паузу на 7 дней и проверьте карточку, цену и наличие.",
    "replenish_stock": "Поставьте товар в приоритет пополнения и не усиливайте спрос до подтвержденной поставки.",
    "reduce_logistics_burden": "Не усиливайте продажи товара, пока не снизите долю логистики или не укрепите маржу.",
    "improve_margin": "Не наращивайте рекламу, пока не повысите цену или маржу товара до безопасного уровня.",
    "investigate_issue": "Сфокусируйтесь на одной главной проблеме товара и выполните корректирующий шаг в ближайшие 7 дней.",
}

CHECK_AFTER_WINDOW: dict[str, str] = {
    "fix_unprofitable_ads": "Через 7 дней сравните прибыль после рекламы до и после ограничения бюджета.",
    "optimize_ads_profitability": "Через 7 дней сравните прибыль после рекламы и долю рекламных расходов.",
    "stop_wasteful_ads": "Через 7 дней проверьте, исчезли ли рекламные расходы без продаж.",
    "replenish_stock": "Через 7 дней проверьте, вышел ли товар из зоны риска по остаткам.",
    "reduce_logistics_burden": "Через 7 дней проверьте, сохраняется ли прибыль при текущей доле логистики.",
    "improve_margin": "Через 7 дней проверьте, вышла ли маржа после рекламы из опасно низкой зоны.",
    "investigate_issue": "Через 7 дней сравните ключевую метрику проблемы до и после действия.",
}

SUCCESS_SIGNAL: dict[str, str] = {
    "fix_unprofitable_ads": "Товар вышел в устойчивую положительную прибыль после рекламы.",
    "optimize_ads_profitability": "Реклама перестала съедать большую часть прибыли, прибыль после рекламы остается положительной.",
    "stop_wasteful_ads": "Рекламные расходы без продаж остановились или начали приносить продажи.",
    "replenish_stock": "Товар вышел из зоны риска дефицита, риск потери выручки снизился.",
    "reduce_logistics_burden": "Доля логистики снизилась или товар сохраняет прибыль при текущих расходах.",
    "improve_margin": "Маржа после рекламы стабильно выше безопасного уровня.",
    "investigate_issue": "Ключевая метрика проблемы вернулась в безопасную зону.",
}

IF_NOT_IMPROVED: dict[str, str] = {
    "fix_unprofitable_ads": "Если товар всё ещё убыточен, не возвращайте старый бюджет: отдельно проверьте цену, себестоимость и логистику.",
    "optimize_ads_profitability": "Если прибыль остаётся слабой, не масштабируйте товар: переходите к проверке цены и себестоимости.",
    "stop_wasteful_ads": "Если продаж всё ещё нет, не возвращайте платную рекламу до исправления карточки, цены или наличия.",
    "replenish_stock": "Если риск дефицита сохраняется, не увеличивайте спрос до подтвержденного пополнения.",
    "reduce_logistics_burden": "Если прибыль не улучшается, не наращивайте продажи, пока не исправите экономику поставки.",
    "improve_margin": "Если маржа остаётся низкой, не увеличивайте рекламу и пересоберите цену/скидку/себестоимость.",
    "investigate_issue": "Если улучшения нет, не усиливайте текущую активность и переходите к ручной диагностике экономики товара.",
}

DO_NOT_DO: dict[str, str] = {
    "fix_unprofitable_ads": "Не увеличивайте рекламу, пока товар убыточен после рекламы.",
    "optimize_ads_profitability": "Не наращивайте рекламный бюджет до подтверждения окупаемости.",
    "stop_wasteful_ads": "Не тратьте рекламный бюджет на товар без подтвержденных продаж.",
    "replenish_stock": "Не разгоняйте спрос, если товар может закончиться раньше поставки.",
    "reduce_logistics_burden": "Не масштабируйте продажи товара с завышенной долей логистики.",
    "improve_margin": "Не масштабируйте товар при опасно низкой марже после рекламы.",
    "investigate_issue": "Не усиливайте проблемную активность до проверки результата.",
}


def _as_number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any, default: int) -> int:
    number = _as_number(value)
    if number is None:
        return default
    return int(number)


def _format_money(value: Any) -> str:
    amount = _as_number(value) or 0.0
    # Use regular spaces in plain API text, UI formats independently.
    return f"{amount:,.0f} ₽".replace(",", " ")


def _format_percent(value: Any) -> str:
    number = _as_number(value)
    if number is None:
        return "0%"
    return f"{number * 100:.1f}%".replace(".0%", "%")


def rule_label(rule_code: str | None) -> str:
    if not rule_code:
        return "Критичная проблема не определена"
    return RULE_LABELS.get(rule_code, "Ключевая проблема товара")


def _resolve_rule_code(row: Mapping[str, Any]) -> str | None:
    primary = row.get("primary_rule_code")
    if isinstance(primary, str) and primary:
        return primary
    raw = row.get("rule_code")
    if not isinstance(raw, str) or not raw:
        return None
    return raw.split(",")[0].strip() or None


def _problem_summary(rule_code: str | None, row: Mapping[str, Any]) -> str:
    revenue = _format_money(row.get("revenue_gross"))
    profit = _format_money(row.get("profit_after_ads"))
    ads = _format_money(row.get("ads_spend"))
    margin = _format_percent(row.get("margin_after_ads"))
    at_risk = _format_money(row.get("revenue_at_risk_30d"))
    at_risk_per_day = _format_money(row.get("revenue_at_risk_per_day"))
    at_risk_7d = _format_money(row.get("revenue_at_risk_7d"))
    days_of_stock = _as_number(row.get("days_of_stock_30d"))
    replenishment_status = str(row.get("replenishment_status") or "unknown")

    if rule_code == "negative_profit_after_ads":
        return (
            f"За 30 дней товар показал отрицательную прибыль после рекламы: {profit}. "
            f"На рекламу ушло {ads}. Товар теряет деньги и требует немедленного действия."
        )
    if rule_code == "ads_eating_profit":
        return (
            f"Реклама съедает значимую часть результата: расходы на рекламу {ads}, "
            f"прибыль после рекламы {profit}. Выручка за период: {revenue}."
        )
    if rule_code == "ads_without_sales":
        return (
            f"По товару есть рекламные расходы {ads}, но продажной отдачи за период недостаточно. "
            "Бюджет расходуется без подтвержденного эффекта."
        )
    if rule_code == "stockout_risk":
        days_text = "н/д" if days_of_stock is None else f"{days_of_stock:.1f}"
        replenishment_note = (
            "Поставка в статусе planned/in_transit."
            if replenishment_status in {"planned", "in_transit"}
            else "Поставка не подтверждена."
        )
        return (
            f"Остатка примерно на {days_text} дней. "
            f"При задержке поставки под риском около {at_risk_per_day} выручки в день "
            f"и до {at_risk_7d} за 7 дней (до {at_risk} за 30 дней). "
            f"{replenishment_note}"
        )
    if rule_code == "high_logistics_share":
        return (
            f"Логистика давит на экономику товара: при выручке {revenue} итоговая прибыль после рекламы {profit}. "
            "Нужно стабилизировать экономику поставки."
        )
    if rule_code == "low_margin_after_ads":
        return (
            f"Товар сохраняет слабую экономику: прибыль после рекламы {profit}, маржа после рекламы {margin}. "
            "Это ниже безопасного уровня для роста."
        )
    return "По товару найден приоритетный риск, который может снижать прибыль магазина."


def _expected_effect_note(rule_code: str | None, estimated_gain: Any) -> str:
    gain = _as_number(estimated_gain)
    if gain and gain > 0:
        if rule_code == "stockout_risk":
            return f"Если устранить проблему, можно защитить до {_format_money(gain)} выручки за 30 дней."
        return f"Если устранить проблему, прибыль может вырасти до {_format_money(gain)} за 30 дней."
    return "После выполнения шага ожидается снижение текущих потерь по товару."


def _effect_type(rule_code: str | None, row: Mapping[str, Any]) -> str:
    if rule_code == "stockout_risk":
        return "revenue_at_risk"
    if rule_code in {"ads_without_sales", "high_logistics_share"}:
        return "loss_reduction"

    est_profit = _as_number(row.get("estimated_profit_uplift_30d")) or 0.0
    est_revenue = _as_number(row.get("estimated_revenue_protection_30d")) or 0.0
    profit_after_ads = _as_number(row.get("profit_after_ads")) or 0.0
    if est_revenue > est_profit:
        return "revenue_at_risk"
    if profit_after_ads < 0:
        return "loss_reduction"
    return "profit_uplift"


def _effect_value(effect_type: str, row: Mapping[str, Any]) -> float | None:
    if effect_type == "revenue_at_risk":
        for key in ("revenue_at_risk_7d", "estimated_revenue_protection_30d", "revenue_at_risk_30d"):
            value = _as_number(row.get(key))
            if value and value > 0:
                return value
        return None
    if effect_type == "loss_reduction":
        for key in ("money_impact_30d", "estimated_gain_if_fixed", "estimated_profit_uplift_30d"):
            value = _as_number(row.get(key))
            if value and value > 0:
                return value
        return None

    for key in ("estimated_profit_uplift_30d", "estimated_gain_if_fixed", "money_impact_30d"):
        value = _as_number(row.get(key))
        if value and value > 0:
            return value
    return None


def build_recommendation_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    rule_code = _resolve_rule_code(row)
    action_code = row.get("action_code")
    if not isinstance(action_code, str) or not action_code:
        action_code = RULE_TO_ACTION_CODE.get(rule_code or "", "investigate_issue")

    action_window_days = _as_int(row.get("action_window_days"), 7)
    estimated_gain_if_fixed = _as_number(row.get("estimated_gain_if_fixed"))
    effect_type = _effect_type(rule_code, row)
    effect_value = _effect_value(effect_type, row)

    payload = {
        "rule_code": rule_code,
        "rule_label": rule_label(rule_code),
        "action_code": action_code,
        "action_title": ACTION_TITLES.get(action_code, ACTION_TITLES["investigate_issue"]),
        "problem_title": rule_label(rule_code),
        "problem_summary": _problem_summary(rule_code, row),
        "what_to_do": WHAT_TO_DO.get(action_code, WHAT_TO_DO["investigate_issue"]),
        "where_to_do_it": WHERE_TO_DO_IT.get(action_code, WHERE_TO_DO_IT["investigate_issue"]),
        "action_window_days": action_window_days,
        "check_after_window": CHECK_AFTER_WINDOW.get(action_code, CHECK_AFTER_WINDOW["investigate_issue"]),
        "success_signal": SUCCESS_SIGNAL.get(action_code, SUCCESS_SIGNAL["investigate_issue"]),
        "if_not_improved": IF_NOT_IMPROVED.get(action_code, IF_NOT_IMPROVED["investigate_issue"]),
        "do_not_do": DO_NOT_DO.get(action_code, DO_NOT_DO["investigate_issue"]),
        "expected_effect_note": _expected_effect_note(rule_code, estimated_gain_if_fixed),
        "estimated_gain_if_fixed": estimated_gain_if_fixed,
        "effect_type": effect_type,
        "effect_value": effect_value,
        "confidence": row.get("confidence"),
        "attribution_quality": row.get("attribution_quality"),
    }
    return payload


def enrich_action_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    recommendation = build_recommendation_payload(result)
    result.update(recommendation)
    # Keep backward-compatible fields for existing frontend clients.
    result["first_step"] = recommendation["what_to_do"]
    result["recommendation"] = recommendation
    return result


def enrich_playbook_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    recommendation = build_recommendation_payload(result)
    result.update(recommendation)
    result["playbook_title"] = recommendation["action_title"]
    result["first_step"] = recommendation["what_to_do"]
    result["problem_summary"] = recommendation["problem_summary"]
    result["check_after_window"] = recommendation["check_after_window"]
    result["if_not_improved"] = recommendation["if_not_improved"]
    result["success_signal"] = recommendation["success_signal"]
    result["recommendation"] = recommendation
    return result


def enrich_product_row(row: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(row)
    recommendation = build_recommendation_payload(result)
    result["rule_label"] = recommendation["rule_label"]
    result["action_title"] = recommendation["action_title"]
    result["what_to_do"] = recommendation["what_to_do"]
    result["recommendation"] = recommendation
    return result
