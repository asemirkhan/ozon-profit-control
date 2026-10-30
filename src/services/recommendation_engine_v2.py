"""Автономный исследовательский движок рекомендаций Profit Control v2.

Модуль намеренно не подключён к API, БД, marts или кабинету. Он принимает уже
подготовленный согласованный снимок, проверяет качество данных и возвращает
структурированный вывод. Это позволяет проверить правила до интеграции.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date
from enum import Enum
from typing import Any


class Decision(str, Enum):
    NO_ACTION = "no_action"
    POSITIVE_STATUS = "positive_status"
    SIGNAL_ONLY = "signal_only"
    ACTION = "action"
    URGENT_ACTION = "urgent_action"
    DATA_WARNING = "data_warning"
    BLOCKED = "blocked"
    DIAGNOSTIC_CHECK = "diagnostic_check"


@dataclass(frozen=True)
class PeriodMetrics:
    period_start: str
    period_end: str
    days: int
    revenue: float
    total_expenses: float
    result: float
    sales_units: float
    orders: float
    average_selling_price: float
    result_per_unit: float | None = None
    expense_per_unit: float | None = None
    logistics: float = 0.0
    logistics_per_unit: float | None = None
    storage: float = 0.0
    storage_per_unit: float | None = None
    ads_spend: float = 0.0
    ads_per_unit: float | None = None
    wb_attributed_orders: float | None = None
    wb_attributed_revenue: float | None = None
    returns: float | None = None
    cancellations: float | None = None


@dataclass(frozen=True)
class DaySignal:
    day: str
    result: float
    baseline_median: float
    comparable_days: int
    is_full_day: bool
    same_weekday_baseline: bool = True


@dataclass(frozen=True)
class DataQuality:
    snapshot_id: str
    confirmed_through: str | None
    last_full_operational_day: str | None
    current_period_complete: bool = True
    previous_period_complete: bool = True
    consistent_snapshot: bool = True
    minimum_sample_passed: bool = True
    cogs_quality: str = "actual"
    ads_reconciliation_status: str = "reconciled"
    storage_allocation_method: str = "direct"
    ambiguous_expenses: float = 0.0
    product_identity_status: str = "confirmed"
    stock_history_sufficient: bool = True


@dataclass(frozen=True)
class ProductContext:
    product_key: str
    product_name: str
    current_month: PeriodMetrics
    previous_month: PeriodMetrics
    quality: DataQuality
    current_7d: PeriodMetrics | None = None
    previous_7d: PeriodMetrics | None = None
    current_month_to_date: PeriodMetrics | None = None
    previous_month_same_days: PeriodMetrics | None = None
    day_signal: DaySignal | None = None
    current_stock: float | None = None
    days_of_stock: float | None = None
    stock_by_warehouse: dict[str, float] = field(default_factory=dict)
    stock_limited_days_current: int = 0


@dataclass(frozen=True)
class Fact:
    code: str
    text: str
    before: float | None = None
    after: float | None = None
    unit: str | None = None


@dataclass(frozen=True)
class RecommendationResult:
    product_key: str
    product_name: str
    scenario_code: str
    decision: Decision
    priority_score: float
    title: str
    short_problem: str
    full_problem: str
    action: str | None
    check_period: str | None
    success_criterion: str | None
    next_step: str | None
    confirmed_facts: tuple[Fact, ...]
    possible_explanations: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    suppressed_scenarios: tuple[str, ...]
    confidence: str
    problem_status: str
    cause_status: str
    cause: str
    action_type: str
    impact_amount: float
    why_this_action: str | None
    audit_checks: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["decision"] = self.decision.value
        return value


@dataclass(frozen=True)
class _Candidate:
    code: str
    decision: Decision
    priority: float
    title: str
    short_problem: str
    full_problem: str
    action: str | None
    check_period: str | None
    success: str | None
    next_step: str | None
    facts: tuple[Fact, ...]
    explanations: tuple[str, ...] = ()
    confidence: str = "high"


def _change(before: float, after: float) -> tuple[float, float | None]:
    absolute = after - before
    if abs(before) < 1e-9:
        return absolute, None
    return absolute, absolute / abs(before)


def _money(value: float) -> str:
    return f"{value:,.0f} ₽".replace(",", " ")


def _number(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def _period_label(start: str, end: str) -> str:
    months = (
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )
    try:
        start_day = date.fromisoformat(start)
        end_day = date.fromisoformat(end)
    except ValueError:
        return f"{start}–{end}"
    if start_day.year == end_day.year and start_day.month == end_day.month:
        return f"с {start_day.day} по {end_day.day} {months[end_day.month - 1]} {end_day.year} года"
    return f"с {start_day.day} {months[start_day.month - 1]} по {end_day.day} {months[end_day.month - 1]} {end_day.year} года"


def _material(before: float, after: float, *, relative: float, absolute: float) -> bool:
    delta, ratio = _change(before, after)
    return abs(delta) >= absolute and (ratio is None or abs(ratio) >= relative)


def _target_between(before: float, after: float) -> float:
    return (before + after) / 2


def _quality_blocks(ctx: ProductContext) -> list[str]:
    q = ctx.quality
    reasons: list[str] = []
    if not ctx.product_key or ctx.product_key == "~" or not ctx.product_name or ctx.product_name == "~":
        reasons.append("Товар не связан с подтверждённой карточкой.")
    if q.product_identity_status != "confirmed":
        reasons.append("Связь товарных идентификаторов неоднозначна.")
    if not q.consistent_snapshot:
        reasons.append("Показатели собраны из разных снимков данных.")
    if not q.current_period_complete or not q.previous_period_complete:
        reasons.append("Один из сравниваемых месяцев не завершён или заполнен не полностью.")
    return reasons


def _blocked_result(ctx: ProductContext, reasons: list[str]) -> RecommendationResult:
    explanation = " ".join(reasons)
    return RecommendationResult(
        product_key=ctx.product_key,
        product_name=ctx.product_name,
        scenario_code="DQ_BLOCKED",
        decision=Decision.BLOCKED,
        priority_score=100.0,
        title="Рекомендация временно недоступна",
        short_problem=explanation,
        full_problem=f"По товару «{ctx.product_name}» нельзя безопасно рекомендовать бизнес-действие. {explanation}",
        action="Обновите данные за оба сравниваемых месяца и повторите расчёт. До обновления не меняйте цену, рекламу или поставки на основании этого сообщения.",
        check_period="После следующего обновления данных",
        success_criterion="Оба месяца заполнены полностью, а товар однозначно связан со своей карточкой.",
        next_step="После обновления система заново определит проблему и первый проверяемый фактор.",
        confirmed_facts=(),
        possible_explanations=(),
        blocked_reasons=tuple(reasons),
        suppressed_scenarios=("all_business_recommendations",),
        confidence="high",
        problem_status="data_blocked",
        cause_status="not_evaluated",
        cause="Недостаточно надёжных данных для оценки товара.",
        action_type="data_fix",
        impact_amount=max(0.0, ctx.previous_month.result - ctx.current_month.result),
        why_this_action="Без сопоставимых данных любое бизнес-действие может быть основано на ложном сигнале.",
    )


def _stock_candidate(ctx: ProductContext) -> _Candidate | None:
    days = ctx.days_of_stock
    if days is None or not ctx.quality.stock_history_sufficient:
        return None
    if days > 14:
        return None
    if days <= 0:
        decision, priority, code = Decision.URGENT_ACTION, 98.0, "STOCK_ZERO"
        title = "Товар закончился"
    elif days < 3:
        decision, priority, code = Decision.URGENT_ACTION, 94.0, "STOCK_LT_3D"
        title = "Запас почти закончился"
    elif days <= 7:
        decision, priority, code = Decision.ACTION, 86.0, "STOCK_3_7D"
        title = "Поставка нужна до расчётной даты дефицита"
    else:
        decision, priority, code = Decision.SIGNAL_ONLY, 58.0, "STOCK_7_14D"
        title = "Запас приближается к контрольной границе"
    target = 14
    fact = Fact("days_of_stock", f"Расчётный запас: {days:.1f} дн.", days, days, "дн.")
    return _Candidate(
        code, decision, priority, title,
        f"Расчётный запас — {days:.1f} дн.",
        f"У товара «{ctx.product_name}» расчётный запас составляет {days:.1f} дн. при текущем темпе продаж.",
        "Создайте поставку до расчётной даты дефицита. Количество рассчитайте так, чтобы после приёмки остатка хватало минимум на 14 дней текущих продаж.",
        "До расчётной даты дефицита",
        f"Покрытие остатка достигло не менее {target} дней без роста отмен и потери продаж.",
        "Если поставка не успевает, перераспределите доступный запас между складами с подтверждённым спросом.",
        (fact,),
        ("Срок зависит от сохранения текущего спроса.",),
    )


def _stock_history_candidate(ctx: ProductContext) -> _Candidate | None:
    limited_days = ctx.stock_limited_days_current
    if limited_days < 2:
        return None
    if ctx.current_month.sales_units >= ctx.previous_month.sales_units or ctx.current_month.result >= ctx.previous_month.result:
        return None
    facts = (
        Fact("limited_stock_days", f"Ограничение наличия зафиксировано в {limited_days} дн.", 0, float(limited_days), "дн."),
        Fact("sales_units", f"Продажи: {_number(ctx.previous_month.sales_units)} → {_number(ctx.current_month.sales_units)} шт.", ctx.previous_month.sales_units, ctx.current_month.sales_units, "шт."),
        Fact("result", f"Результат: {_money(ctx.previous_month.result)} → {_money(ctx.current_month.result)}.", ctx.previous_month.result, ctx.current_month.result, "₽"),
    )
    return _Candidate(
        "AVAILABILITY_LIMITED", Decision.DIAGNOSTIC_CHECK, min(90.0, 78.0 + limited_days),
        "Продажи снижались при ограниченном наличии",
        f"В текущем месяце ограничение наличия зафиксировано в {limited_days} дн., одновременно снизились продажи и результат.",
        f"У товара «{ctx.product_name}» ограничение наличия зафиксировано в {limited_days} дн.; продажи снизились с {_number(ctx.previous_month.sales_units)} до {_number(ctx.current_month.sales_units)} шт., результат — с {_money(ctx.previous_month.result)} до {_money(ctx.current_month.result)}.",
        "Откройте историю остатков по складам и определите, на каких складах возникло ограничение. Запланируйте пополнение только для складов, которые давали продажи в предыдущем месяце.",
        "Сразу по истории остатков за сравниваемый месяц",
        "Определены склады и дни дефицита; количество поставки рассчитано минимум на 14 дней текущих продаж.",
        "После приёмки сравните следующие 14 полных дней с предыдущими 14 днями по продажам и результату на единицу.",
        facts,
        ("Совпадение дефицита и снижения продаж подтверждено; долю потери, вызванную дефицитом, нужно определить по складам и дням.",),
        "medium",
    )


def _recovery_candidate(ctx: ProductContext) -> _Candidate | None:
    if ctx.current_7d is None or ctx.previous_7d is None:
        return None
    if not ctx.quality.minimum_sample_passed:
        return None
    month_delta, _ = _change(ctx.previous_month.result, ctx.current_month.result)
    week_delta, _ = _change(ctx.previous_7d.result, ctx.current_7d.result)
    if month_delta >= 0 or week_delta <= 0:
        return None
    facts = (
        Fact("month_result", f"Результат месяца: {_money(ctx.previous_month.result)} → {_money(ctx.current_month.result)}.", ctx.previous_month.result, ctx.current_month.result, "₽"),
        Fact("week_result", f"Последние 7 дней: {_money(ctx.previous_7d.result)} → {_money(ctx.current_7d.result)}.", ctx.previous_7d.result, ctx.current_7d.result, "₽"),
    )
    return _Candidate(
        "MONTH_DOWN_WEEK_RECOVERY", Decision.SIGNAL_ONLY, 82.0,
        "Последние семь дней восстанавливаются",
        "Основной месяц остаётся слабым, но последние семь полных дней показывают восстановление.",
        f"Результат товара «{ctx.product_name}» снизился с {_money(ctx.previous_month.result)} до {_money(ctx.current_month.result)}, однако за последние семь дней вырос с {_money(ctx.previous_7d.result)} до {_money(ctx.current_7d.result)}.",
        "Не меняйте цену и рекламные настройки следующие 7 полных дней. Поставки выполняйте по действующему плану и проверьте, сохранится ли восстановление.",
        "Следующие 7 полных дней",
        "Результат растёт второй полный недельный период, а результат на единицу не ухудшается.",
        "Если восстановление прервётся, примените действие по главному подтверждённому фактору завершённого месяца.",
        facts,
    )


def _logistics_candidate(ctx: ProductContext) -> _Candidate | None:
    before = ctx.previous_month.logistics_per_unit
    after = ctx.current_month.logistics_per_unit
    if before is None or after is None or after <= before or not _material(before, after, relative=0.10, absolute=20):
        return None
    result_before = ctx.previous_month.result_per_unit
    result_after = ctx.current_month.result_per_unit
    if result_before is None or result_after is None or result_after >= result_before:
        return None
    target = _target_between(before, after)
    result_target = _target_between(result_before, result_after)
    money_effect = max(0.0, (after - before) * ctx.current_month.sales_units)
    facts = (
        Fact("logistics_per_unit", f"Логистика на единицу: {_money(before)} → {_money(after)}.", before, after, "₽/шт."),
        Fact("result_per_unit", f"Результат на единицу: {_money(result_before)} → {_money(result_after)}.", result_before, result_after, "₽/шт."),
    )
    return _Candidate(
        "LOGISTICS_UNIT_UP", Decision.DIAGNOSTIC_CHECK, min(92.0, 68.0 + money_effect / 5000),
        "Логистика на единицу снижает результат",
        f"Логистика на единицу выросла: {_money(before)} → {_money(after)}.",
        f"У товара «{ctx.product_name}» логистика на единицу выросла с {_money(before)} до {_money(after)}, а результат на единицу снизился с {_money(result_before)} до {_money(result_after)}.",
        "Откройте детализацию логистики товара за оба месяца и сначала сравните стоимость доставки и возврата по складам. Не меняйте схему поставки, пока не найден склад или вид операции, давший основную прибавку.",
        "Следующий полный период поставки, обычно 7–14 дней",
        f"Логистика на единицу вернулась не выше {_money(target)} либо результат на единицу восстановился не ниже {_money(result_target)}.",
        "Если показатель не восстановился, сравните стоимость следующей поставки через другой подходящий склад.",
        facts,
        ("Возможные точки проверки — склад, упаковка и тариф; причина не доказана.",),
    )


def _price_candidate(ctx: ProductContext) -> _Candidate | None:
    p0, p1 = ctx.previous_month.average_selling_price, ctx.current_month.average_selling_price
    u0, u1 = ctx.previous_month.sales_units, ctx.current_month.sales_units
    if p0 <= 0 or p1 <= 0 or u0 <= 0 or u1 <= 0:
        return None
    if p1 >= p0 or u1 > u0 or not _material(p0, p1, relative=0.07, absolute=30):
        return None
    facts = (
        Fact("average_price", f"Средняя фактическая цена: {_money(p0)} → {_money(p1)}.", p0, p1, "₽"),
        Fact("sales_units", f"Продажи: {_number(u0)} → {_number(u1)} шт.", u0, u1, "шт."),
    )
    return _Candidate(
        "PRICE_DOWN_WITHOUT_VOLUME", Decision.DIAGNOSTIC_CHECK, 72.0,
        "Снижение цены не увеличило продажи",
        f"Средняя фактическая цена снизилась с {_money(p0)} до {_money(p1)}, а продажи не выросли.",
        f"У товара «{ctx.product_name}» средняя фактическая цена снизилась с {_money(p0)} до {_money(p1)}, а продажи изменились с {_number(u0)} до {_number(u1)} шт.",
        "Сравните цену продавца и скидку площадки за оба месяца. Если цену снижал продавец, не снижайте её ещё 7 дней; если изменилась только скидка площадки, цену продавца пока не меняйте.",
        "Один полный сопоставимый период, обычно 7–14 дней",
        "Цена больше не снижается, а продажи и результат на единицу не ухудшаются.",
        "Если объём не восстановился, проверяйте наличие и видимость отдельно от цены.",
        facts,
        ("Изменение цены могло совпасть со снижением продаж, но причинная связь не доказана.",),
        "medium",
    )


def _ads_candidate(ctx: ProductContext) -> _Candidate | None:
    before = ctx.previous_month.ads_spend
    after = ctx.current_month.ads_spend
    if after <= before or not _material(before, after, relative=0.15, absolute=1000):
        return None
    if ctx.current_month.result >= ctx.previous_month.result:
        return None

    extra = after - before
    attributed_before = ctx.previous_month.wb_attributed_orders or 0.0
    attributed_after = ctx.current_month.wb_attributed_orders or 0.0
    facts = (
        Fact("ads_spend", f"Расходы на рекламу: {_money(before)} → {_money(after)}.", before, after, "₽"),
        Fact(
            "result",
            f"Результат: {_money(ctx.previous_month.result)} → {_money(ctx.current_month.result)}.",
            ctx.previous_month.result,
            ctx.current_month.result,
            "₽",
        ),
    )
    if attributed_before > 0 and attributed_after > 0:
        cpo_before = before / attributed_before
        cpo_after = after / attributed_after
        if cpo_after <= cpo_before * 1.15:
            return None
        facts += (
            Fact(
                "ad_cost_per_order",
                f"Расход на один рекламный заказ: {_money(cpo_before)} → {_money(cpo_after)}.",
                cpo_before,
                cpo_after,
                "₽/заказ",
            ),
        )
        title = "Реклама подорожала на один заказ"
        problem = f"Расходы на рекламу выросли на {_money(extra)}, а стоимость одного рекламного заказа — с {_money(cpo_before)} до {_money(cpo_after)}."
        action = "Откройте кампании этого товара и найдите кампанию с наибольшим ростом расхода на заказ. Снизьте ставку или дневной предел только у неё на 10% и не меняйте цену 7 дней."
        explanation = "Подтверждён рост стоимости рекламного заказа; конкретная кампания должна быть выбрана в детализации рекламы."
    else:
        title = "Расходы на рекламу выросли, отдача не подтверждена"
        problem = f"Расходы на рекламу выросли на {_money(extra)}, а результат товара снизился. Данных о рекламных заказах недостаточно, чтобы оценить отдачу."
        action = "Откройте отчёт кампаний этого товара за оба месяца и сначала сравните расход, заказы и выручку по каждой кампании. До этой проверки не увеличивайте рекламный бюджет."
        explanation = "Рост расхода подтверждён, но без рекламных заказов нельзя определить, какая кампания ухудшила результат."
    return _Candidate(
        "ADS_COST_UP", Decision.DIAGNOSTIC_CHECK, min(88.0, 70.0 + extra / 5000),
        title,
        problem,
        f"У товара «{ctx.product_name}» {problem[0].lower() + problem[1:]}",
        action,
        "Сразу по отчёту за два сравниваемых месяца",
        "Определена конкретная кампания, давшая основную прибавку расходов, либо подтверждено, что рост рекламы окупился заказами.",
        "Если найдена неэффективная кампания, меняйте только её и сравните следующие 7 полных дней с предыдущими 7 днями.",
        facts,
        (explanation,),
        "medium",
    )


def _demand_candidate(ctx: ProductContext) -> _Candidate | None:
    u0, u1 = ctx.previous_month.sales_units, ctx.current_month.sales_units
    o0, o1 = ctx.previous_month.orders, ctx.current_month.orders
    r0, r1 = ctx.previous_month.result, ctx.current_month.result
    revenue0, revenue1 = ctx.previous_month.revenue, ctx.current_month.revenue
    p0, p1 = ctx.previous_month.average_selling_price, ctx.current_month.average_selling_price
    if u1 >= u0 or r1 >= r0 or not _material(u0, u1, relative=0.15, absolute=3):
        return None
    price_is_comparable = u0 > 0 and u1 > 0 and p0 > 0 and p1 > 0
    price_change = abs(_change(p0, p1)[1] or 0.0) if price_is_comparable else None
    if price_change is not None and price_change > 0.07:
        return None
    facts = (
        Fact("orders", f"Заказы: {_number(o0)} → {_number(o1)}.", o0, o1, "заказов"),
        Fact("sales_units", f"Проданные единицы: {_number(u0)} → {_number(u1)} шт.", u0, u1, "шт."),
        Fact("revenue", f"Выручка: {_money(revenue0)} → {_money(revenue1)}.", revenue0, revenue1, "₽"),
        Fact("result", f"Результат: {_money(r0)} → {_money(r1)}.", r0, r1, "₽"),
    )
    no_sales = u1 <= 0
    orders_down = o1 < o0 and _material(o0, o1, relative=0.12, absolute=3)
    cancellations0 = ctx.previous_month.cancellations or 0.0
    cancellations1 = ctx.current_month.cancellations or 0.0
    cancellations_up = cancellations1 >= cancellations0 + 2
    if no_sales:
        title = "Нет продаж и потеряна выручка"
        short_problem = f"Проданные единицы: {_number(u0)} → 0; выручка: {_money(revenue0)} → {_money(revenue1)}."
        full_problem = f"У товара «{ctx.product_name}» нет продаж: было {_number(u0)} шт. Выручка снизилась с {_money(revenue0)} до {_money(revenue1)}, результат — с {_money(r0)} до {_money(r1)}."
        action = f"Проверьте остатки товара по складам за период {_period_label(ctx.current_month.period_start, ctx.current_month.period_end)}. Если товар был доступен, откройте воронку карточки и найдите первый снизившийся этап: показы, переходы, добавления в корзину или заказы."
    elif cancellations_up and not orders_down:
        title = "Больше заказов отменилось"
        short_problem = f"Отмены выросли с {_number(cancellations0)} до {_number(cancellations1)}, проданные единицы снизились с {_number(u0)} до {_number(u1)}."
        full_problem = f"У товара «{ctx.product_name}» отмены выросли с {_number(cancellations0)} до {_number(cancellations1)}, а проданные единицы снизились с {_number(u0)} до {_number(u1)}. Выручка снизилась с {_money(revenue0)} до {_money(revenue1)}."
        action = "Откройте причины отмен по этому товару и отделите отмены покупателя от отмен из-за наличия или доставки. Исправляйте только крупнейшую подтверждённую причину."
    elif orders_down:
        title = "Заказов стало меньше, выручка снизилась"
        short_problem = f"Заказы снизились с {_number(o0)} до {_number(o1)}, выручка — с {_money(revenue0)} до {_money(revenue1)}."
        full_problem = f"У товара «{ctx.product_name}» заказы снизились с {_number(o0)} до {_number(o1)}, проданные единицы — с {_number(u0)} до {_number(u1)}. Выручка снизилась с {_money(revenue0)} до {_money(revenue1)}, результат — с {_money(r0)} до {_money(r1)}."
        action = "Откройте воронку товара за оба месяца. Найдите этап с наибольшим снижением: показы, переходы, добавления в корзину или заказы. Затем разделите падение заказов на рекламную и органическую части."
    else:
        title = "Меньше заказов завершилось продажей"
        short_problem = f"Проданные единицы снизились с {_number(u0)} до {_number(u1)}, хотя заказы не сократились существенно."
        full_problem = f"У товара «{ctx.product_name}» заказы изменились с {_number(o0)} до {_number(o1)}, а проданные единицы снизились с {_number(u0)} до {_number(u1)}. Выручка снизилась с {_money(revenue0)} до {_money(revenue1)}."
        action = "Сопоставьте заказы этого периода с их последующим статусом. Найдите прирост отмен, возвратов или незавершённых заказов и работайте с крупнейшей подтверждённой причиной."
    return _Candidate(
        "DEMAND_VOLUME_DOWN", Decision.DIAGNOSTIC_CHECK, 76.0,
        title,
        short_problem,
        full_problem,
        action,
        "Проверить сейчас по истории за сравниваемый месяц",
        "Определён один этап воронки или статус заказа, который объясняет основную часть снижения продаж.",
        "После исправления сравните следующие 7 полных дней с предыдущими 7 днями по заказам, проданным единицам, выручке и результату.",
        facts,
        ("Источник снижения определяется по воронке и статусам заказов; агрегаты фиксируют проблему, но не заменяют эту проверку.",),
        "medium",
    )


def _unit_economics_candidate(ctx: ProductContext) -> _Candidate | None:
    b, a = ctx.previous_month.result_per_unit, ctx.current_month.result_per_unit
    if b is None or a is None or a >= b or not _material(b, a, relative=0.12, absolute=30):
        return None
    facts = (Fact("result_per_unit", f"Результат на единицу: {_money(b)} → {_money(a)}.", b, a, "₽/шт."),)
    return _Candidate(
        "UNIT_ECONOMICS_DOWN", Decision.DIAGNOSTIC_CHECK, 66.0,
        "Результат на единицу снизился — найдите выросший расход",
        f"Результат на единицу снизился: {_money(b)} → {_money(a)}.",
        f"У товара «{ctx.product_name}» результат на единицу снизился с {_money(b)} до {_money(a)}.",
        "Откройте расходы товара в расчёте на одну продажу и найдите статью с наибольшим ростом. До её определения не меняйте одновременно цену, рекламу и поставку.",
        "Сразу после проверки детализации расходов",
        "Найдена одна статья, которая объясняет основную часть снижения результата на единицу, либо зафиксировано, что доступные статьи его не объясняют.",
        "После определения статьи примените отдельное действие именно к ней и проверяйте результат на следующем полном периоде.",
        facts,
        confidence="medium",
    )


def _day_alert(ctx: ProductContext) -> _Candidate | None:
    d = ctx.day_signal
    if d is None or not d.is_full_day or not d.same_weekday_baseline or d.comparable_days < 4:
        return None
    delta, ratio = _change(d.baseline_median, d.result)
    if delta >= 0 or ratio is None or ratio > -0.30:
        return None
    facts = (Fact("day_result", f"Последний полный день: {_money(d.result)}, обычный уровень аналогичного дня: {_money(d.baseline_median)}.", d.baseline_median, d.result, "₽"),)
    return _Candidate(
        "ONE_DAY_ALERT", Decision.SIGNAL_ONLY, 54.0,
        "Необычное изменение за последний полный день",
        f"Результат дня ниже медианы сопоставимых дней на {abs(ratio)*100:.0f}%.",
        f"За {d.day} результат товара «{ctx.product_name}» составил {_money(d.result)} при медиане {_money(d.baseline_median)} по {d.comparable_days} аналогичным дням недели.",
        "Не меняйте цену, рекламу или поставку только по одному дню. Отметьте товар для наблюдения и проверьте ещё три полных аналогичных дня.",
        "Следующие 3–7 полных дней",
        "Показатель вернулся в обычный диапазон либо отклонение подтвердилось несколькими полными днями.",
        "Если снижение подтвердилось, определить основной сценарий по семидневному периоду.",
        facts,
    )


def _positive_candidate(ctx: ProductContext) -> _Candidate:
    c, p = ctx.current_month, ctx.previous_month
    facts = (
        Fact("result", f"Результат: {_money(p.result)} → {_money(c.result)}.", p.result, c.result, "₽"),
        Fact("sales_units", f"Продажи: {_number(p.sales_units)} → {_number(c.sales_units)} шт.", p.sales_units, c.sales_units, "шт."),
    )
    if c.result > p.result and c.sales_units >= p.sales_units:
        return _Candidate(
            "STABLE_GROWTH", Decision.POSITIVE_STATUS, 20.0,
            "Продажи и результат выросли",
            f"Результат вырос с {_money(p.result)} до {_money(c.result)}.",
            f"У товара «{ctx.product_name}» результат вырос с {_money(p.result)} до {_money(c.result)}, продажи — с {_number(p.sales_units)} до {_number(c.sales_units)} шт.",
            None, None,
            "Рост сохраняется в следующем полном периоде, а результат на единицу не ухудшается.",
            None, facts,
        )
    return _Candidate(
        "NORMAL_VARIATION", Decision.NO_ACTION, 5.0,
        "Сильного подтверждённого отклонения нет",
        "Показатели находятся в пределах допустимого изменения.",
        f"По товару «{ctx.product_name}» не найдено изменения, которое одновременно прошло порог объёма, денег и устойчивости.",
        None, None, None, None, facts,
    )


def _interpretation_metadata(ctx: ProductContext, chosen: _Candidate) -> tuple[str, str, str, str, float, str | None]:
    impact = max(0.0, ctx.previous_month.result - ctx.current_month.result)
    code = chosen.code
    if code.startswith("STOCK_"):
        return (
            "confirmed_risk", "confirmed", "Текущего остатка недостаточно для безопасного продолжения продаж.",
            "business_action", impact,
            "Риск дефицита рассчитан по текущему остатку и наблюдаемому темпу продаж.",
        )
    if code == "AVAILABILITY_LIMITED":
        return (
            "confirmed_problem", "partially_confirmed", "Ограничение наличия совпало со снижением продаж; конкретные склады и величина влияния требуют проверки.",
            "diagnostic_check", impact,
            "История по складам позволяет превратить общий сигнал дефицита в конкретный план поставки.",
        )
    if code == "MONTH_DOWN_WEEK_RECOVERY":
        return (
            "confirmed_problem", "trend_reversal", "Месячное снижение подтверждено, но последняя неделя показывает восстановление.",
            "monitor", impact,
            "Дополнительная неделя отделит устойчивое восстановление от краткого колебания без лишних изменений.",
        )
    if code == "LOGISTICS_UNIT_UP":
        return (
            "confirmed_problem", "partially_confirmed", "Рост логистики на единицу подтверждён; склад, тариф или вид операции, вызвавший рост, ещё не определён.",
            "diagnostic_check", impact,
            "Сначала нужно найти конкретный источник роста, чтобы не менять всю схему поставки вслепую.",
        )
    if code == "PRICE_DOWN_WITHOUT_VOLUME":
        return (
            "confirmed_problem", "partially_confirmed", "Фактическая цена снизилась без роста продаж; источник изменения цены ещё не определён.",
            "diagnostic_check", impact,
            "Цена продавца и скидка площадки требуют разных действий, поэтому сначала их нужно разделить.",
        )
    if code == "ADS_COST_UP":
        has_attribution = bool((ctx.previous_month.wb_attributed_orders or 0) and (ctx.current_month.wb_attributed_orders or 0))
        return (
            "confirmed_problem", "partially_confirmed" if has_attribution else "unknown",
            "Рост рекламных расходов подтверждён; конкретная неэффективная кампания подтверждена только при наличии рекламных заказов.",
            "diagnostic_check", impact,
            "Изменение бюджета допустимо только после выбора конкретной кампании по её расходу и заказам.",
        )
    if code == "DEMAND_VOLUME_DOWN":
        orders_down = ctx.current_month.orders < ctx.previous_month.orders
        cancellations_up = (ctx.current_month.cancellations or 0) > (ctx.previous_month.cancellations or 0)
        if orders_down:
            cause = "Снижение началось на этапе заказов."
        elif cancellations_up:
            cause = "Выросло количество отмен."
        else:
            cause = "Меньшая доля заказов завершилась продажей."
        return (
            "confirmed_problem", "partially_confirmed", cause,
            "diagnostic_check", impact,
            "Проверка начинается с первого этапа, на котором показатели действительно снизились.",
        )
    if code == "UNIT_ECONOMICS_DOWN":
        return (
            "confirmed_problem", "unknown", "Результат на единицу снизился, но доступные агрегаты пока не выделили одну доказанную статью расходов.",
            "diagnostic_check", impact,
            "Одновременное изменение нескольких факторов не позволит понять, что восстановило результат.",
        )
    if code == "ONE_DAY_ALERT":
        return (
            "operational_signal", "unknown", "Зафиксировано однодневное отклонение; одного дня недостаточно для определения причины.",
            "monitor", impact,
            "Наблюдение защищает от резких решений по случайному дневному колебанию.",
        )
    if code == "STABLE_GROWTH":
        return (
            "positive_status", "not_applicable", "Продажи и результат выросли одновременно.",
            "none", 0.0, None,
        )
    return (
        "no_material_change", "not_applicable", "Денежно значимого устойчивого ухудшения не найдено.",
        "none", 0.0, None,
    )


def audit_recommendation(result: RecommendationResult) -> tuple[dict[str, Any], ...]:
    """Проверить клиентский вывод по обязательным продуктовым критериям."""
    needs_action = result.decision in {
        Decision.ACTION, Decision.URGENT_ACTION, Decision.DIAGNOSTIC_CHECK,
        Decision.SIGNAL_ONLY, Decision.DATA_WARNING, Decision.BLOCKED,
    }
    negative_problem = result.problem_status == "confirmed_problem"
    unconfirmed_cause = result.cause_status in {"unknown", "partially_confirmed", "not_evaluated"}
    client_text = " ".join(filter(None, (result.title, result.full_problem, result.action or ""))).lower()
    forbidden = ("scenario", "snapshot", "pipeline", "dq_", "сценар", "снимок данных")
    invalid_average_price = any(
        fact.code == "average_price" and ((fact.before or 0) <= 0 or (fact.after or 0) <= 0)
        for fact in result.confirmed_facts
    )
    checks = (
        ("specific_problem", "Проблема названа и подтверждена фактами", bool(result.full_problem and (result.confirmed_facts or result.problem_status == "data_blocked")), "Есть числовые факты либо понятная причина блокировки."),
        ("money_impact", "Денежное влияние указано", (not negative_problem) or result.impact_amount > 0, f"Влияние: {_money(result.impact_amount)}." if result.impact_amount else "Для этого типа вывода денежная потеря не требуется."),
        ("honest_cause", "Причина не выдана за доказанную без оснований", bool(result.cause and result.cause_status), f"Статус причины: {result.cause_status}."),
        ("safe_action", "Действие соответствует уверенности в причине", not (unconfirmed_cause and result.action_type == "business_action"), f"Тип действия: {result.action_type}."),
        ("executable_first_step", "Первый шаг можно выполнить", (not needs_action) or bool(result.action and len(result.action) >= 35), "Действие содержит конкретный первый шаг." if result.action else "Действие не требуется."),
        ("check_period", "Указан срок проверки", (not needs_action) or bool(result.check_period), result.check_period or "Срок не требуется."),
        ("success_criterion", "Указан проверяемый результат", (not needs_action) or bool(result.success_criterion), result.success_criterion or "Критерий не требуется."),
        ("plain_language", "Нет внутренних технических терминов", not any(word in client_text for word in forbidden), "Клиентский текст не содержит внутренних кодов и терминов."),
        ("metric_sanity", "Показатели имеют экономический смысл", not invalid_average_price, "Средняя цена не трактуется как ноль при отсутствии продаж."),
    )
    return tuple({"code": code, "label": label, "passed": passed, "detail": detail} for code, label, passed, detail in checks)


def _with_audit(result: RecommendationResult) -> RecommendationResult:
    return replace(result, audit_checks=audit_recommendation(result))


def evaluate_product(ctx: ProductContext) -> RecommendationResult:
    """Вернуть одну главную рекомендацию для согласованного товарного снимка."""
    blocks = _quality_blocks(ctx)
    if blocks:
        return _with_audit(_blocked_result(ctx, blocks))

    candidates = [
        _stock_candidate(ctx),
        _stock_history_candidate(ctx),
        _recovery_candidate(ctx),
        _logistics_candidate(ctx),
        _price_candidate(ctx),
        _ads_candidate(ctx),
        _demand_candidate(ctx),
        _unit_economics_candidate(ctx),
        _day_alert(ctx),
    ]
    available = [candidate for candidate in candidates if candidate is not None]
    available.append(_positive_candidate(ctx))
    available.sort(key=lambda item: item.priority, reverse=True)
    chosen = available[0]

    limited_reasons: list[str] = []
    if ctx.quality.ads_reconciliation_status != "reconciled":
        limited_reasons.append("Рекламный слой не сверен: точное изменение бюджета не рекомендуется.")
    if ctx.quality.storage_allocation_method != "direct":
        limited_reasons.append("Хранение распределено расчётно и не считается доказанной причиной товара.")
    if ctx.quality.ambiguous_expenses > 0:
        limited_reasons.append(f"Есть неоднозначные расходы на {_money(ctx.quality.ambiguous_expenses)}.")
    if not ctx.quality.minimum_sample_passed:
        limited_reasons.append("Продаж мало: проблема видна, но уверенность в её причине снижена.")

    suppressed = tuple(item.code for item in available[1:] if item.priority >= 30)
    confidence = chosen.confidence
    if limited_reasons and confidence == "high":
        confidence = "medium"
    if not ctx.quality.minimum_sample_passed:
        confidence = "low"
    problem_status, cause_status, cause, action_type, impact_amount, why_this_action = _interpretation_metadata(ctx, chosen)
    result = RecommendationResult(
        product_key=ctx.product_key,
        product_name=ctx.product_name,
        scenario_code=chosen.code,
        decision=chosen.decision,
        priority_score=round(chosen.priority, 2),
        title=chosen.title,
        short_problem=chosen.short_problem,
        full_problem=chosen.full_problem,
        action=chosen.action,
        check_period=chosen.check_period,
        success_criterion=chosen.success,
        next_step=chosen.next_step,
        confirmed_facts=chosen.facts,
        possible_explanations=chosen.explanations,
        blocked_reasons=tuple(limited_reasons),
        suppressed_scenarios=suppressed,
        confidence=confidence,
        problem_status=problem_status,
        cause_status=cause_status,
        cause=cause,
        action_type=action_type,
        impact_amount=round(impact_amount, 2),
        why_this_action=why_this_action,
    )
    return _with_audit(result)
