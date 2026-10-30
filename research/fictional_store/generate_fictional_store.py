#!/usr/bin/env python3
"""Deterministic fictional store dataset for recommendation-engine research only."""

from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RNG = random.Random(27072026)


@dataclass(frozen=True)
class Scenario:
    code: str
    family: str
    problem: str
    proven_cause: str
    action: str
    check_days: int
    success: str
    fallback: str
    order_factor: float = 1.0
    price_factor: float = 1.0
    cancel_rate: float = 0.05
    units_per_order_factor: float = 1.0
    ads_factor: float = 1.0
    logistics_factor: float = 1.0
    storage_factor: float = 1.0
    stock_days: float = 30.0
    rating_delta: float = 0.0
    search_rank_delta: int = 0
    week_factor: float = 1.0
    day_factor: float = 1.0
    quality: str = "complete"
    recommendation_status: str = "action"


SCENARIOS = [
    Scenario("P01_HEALTHY_SCALE", "healthy_growth", "Продажи растут без ухудшения расходов на единицу.", "Заказов и результата стало больше, удельные расходы сохранились.", "Действие не требуется.", 14, "Результат на единицу не ниже уровня июня, запас 14–35 дней.", "Если результат на единицу снизится более чем на 8%, откройте состав расходов.", 1.24, ads_factor=1.22, logistics_factor=1.0, recommendation_status="no_action"),
    Scenario("P02_SCALE_LOGISTICS_PRESSURE", "growth_quality", "Продажи растут, но логистика на единицу ухудшает результат.", "Коледино дало 74% прибавки логистики, но вид подорожавшей операции ещё не определён.", "Разделите операции Коледино на доставку покупателю, возвраты, повторную доставку и корректировки; сравните количество операций и тариф за единицу.", 7, "Не менее 95% прибавки логистики отнесено к конкретному виду операции и тарифу.", "Действие выбирается по найденной причине: габариты, возвраты, тариф или региональное распределение.", 1.22, logistics_factor=1.42, recommendation_status="diagnostic_check"),
    Scenario("P03_AD_GROWTH_JUSTIFIED", "healthy_growth", "Реклама на заказ выросла, но дополнительный объём окупил расход.", "Рекламные заказы и результат товара выросли быстрее рекламных расходов.", "Действие не требуется.", 7, "Результат на единицу не снижается, рекламные заказы сохраняются.", "Если результат на единицу начнёт снижаться, разберите кампании по расходу на заказ.", 1.30, ads_factor=1.34, recommendation_status="no_action"),
    Scenario("P04_REVENUE_UP_AD_RESULT_DOWN", "ads", "Выручка выросла, но результат снизился из-за рекламы.", "Одна кампания дала основную прибавку расхода без сопоставимого роста заказов.", "Снизьте ставку только в этой кампании до прежнего уровня.", 7, "Расход на рекламный заказ не выше майского уровня, число заказов не ниже 90% июня.", "Если расход не снизится, исключите запросы без заказов.", 1.08, ads_factor=2.05),
    Scenario("P05_REVENUE_UP_LOGISTICS_RESULT_DOWN", "logistics", "Выручка выросла, но результат снизился из-за логистики.", "Один склад объясняет более половины прибавки, но причина внутри его операций ещё не определена.", "Разложите логистику этого склада на доставку, возвраты, повторные операции, тариф и корректировки.", 7, "Найдена операция, объясняющая не менее 80% подтверждённой прибавки.", "После определения причины выберите одно действие; поставку между складами пока не менять.", 1.10, logistics_factor=3.40, recommendation_status="diagnostic_check"),
    Scenario("P06_PRICE_DOWN_RESULT_DOWN", "price", "Снижение фактической цены не дало достаточного роста продаж.", "После увеличения скидки цена снизилась, а заказы не выросли.", "Верните размер скидки, действовавший в мае, на 7 дней.", 7, "Результат на единицу восстановился, заказы снизились не более чем на 5%.", "Если заказы упадут сильнее, проведите второй ценовой тест посередине диапазона.", 1.01, price_factor=0.82),
    Scenario("P07_STOCKOUT", "availability", "Заказы снизились из-за отсутствия товара на продающих складах.", "На складах, дававших 68% заказов, товара не было 8 дней.", "Поставьте на эти склады запас на 21 день их продаж.", 14, "Товар доступен каждый день, заказы вернулись не ниже 90% майского темпа.", "Если заказы не восстановятся, сравните поисковые позиции после приёмки.", 0.58, stock_days=0.0),
    Scenario("P08_SEARCH_VISIBILITY_DOWN", "organic_visibility", "Заказы снизились после потери органической видимости.", "Позиции по трём основным запросам ухудшились, органические переходы снизились.", "Верните потерянные запросы в название и характеристики карточки.", 14, "Средняя позиция и органические переходы восстановились минимум на 70% потери.", "Если позиции не восстановятся, обновите главное изображение отдельным опытом.", 0.72, search_rank_delta=18),
    Scenario("P09_AD_BUDGET_EXHAUSTED", "ads", "Рекламные заказы потеряны после раннего исчерпания дневного бюджета.", "Кампания прекращала показы после 17:00 при стабильном расходе на заказ.", "Увеличьте дневной предел этой кампании на 10%, не меняя ставку.", 7, "Кампания работает до 22:00, рекламные заказы растут при прежнем расходе на заказ.", "Если расход на заказ вырастет более чем на 10%, верните прежний предел.", 0.84, ads_factor=0.78),
    Scenario("P10_AD_CTR_DOWN", "ads", "Переходов из рекламы стало меньше при прежних показах.", "Доля переходов в одной кампании снизилась после смены главного изображения.", "Верните предыдущее главное изображение для этой карточки.", 7, "Доля переходов не ниже 90% майского уровня при сопоставимых показах.", "Если доля переходов не восстановится, проверьте запросы кампании.", 0.80, ads_factor=0.96),
    Scenario("P11_PRICE_UP_CONVERSION_DOWN", "price", "После роста цены снизилась доля посетителей, оформивших заказ.", "Посещаемость сохранилась, снижение началось после изменения фактической цены.", "Верните предыдущую цену на 7 дней, не меняя рекламу и карточку.", 7, "Доля заказов восстановилась минимум до медианы мая.", "Если восстановления нет, оставьте текущую цену и проверьте срок доставки.", 0.71, price_factor=1.14),
    Scenario("P12_DELIVERY_TIME_DOWN", "delivery", "Заказы снизились в регионах, где вырос срок доставки.", "В двух крупнейших регионах доставка выросла с 2 до 5 дней и снизилась доля заказов.", "Переместите запас на склад, обслуживающий эти регионы за 1–2 дня.", 14, "Срок доставки не более 2 дней, доля заказов региона вернулась к майской.", "Если доставка не ускорится, выберите другой принимающий склад.", 0.76, logistics_factor=1.18),
    Scenario("P13_RATING_PACKAGING", "reviews", "Заказы снизились после роста жалоб на повреждённую упаковку.", "Рейтинг снизился, большинство новых негативных отзывов относится к повреждению коробки.", "Добавьте защитные уголки и внутреннюю фиксацию для следующей поставки.", 30, "Доля жалоб на упаковку снизилась вдвое, рейтинг новых отзывов не ниже 4,7.", "Если жалобы сохранятся, замените транспортную коробку.", 0.69, rating_delta=-0.35),
    Scenario("P14_CATEGORY_DEMAND_DOWN", "market_demand", "Спрос на категорию снизился, доля товара сохранилась.", "Продажи сопоставимых товаров категории снизились в том же диапазоне.", "Сократите следующую поставку до объёма на 35 дней текущих продаж.", 14, "Запас остаётся в диапазоне 28–42 дней без дефицита.", "Если спрос восстановится две недели подряд, увеличьте следующую поставку.", 0.77, stock_days=71),
    Scenario("P15_COMPETITOR_GAIN", "competition", "Товар потерял переходы по запросу после снижения цены сопоставимого конкурента.", "Цена конкурента стала ниже на 12%; его рейтинг и срок доставки не изменились.", "Проведите семидневный ценовой тест только для этого товара.", 7, "Доля переходов по запросу восстановилась без снижения результата на единицу.", "Если переходы не восстановятся, верните цену и проверьте главное изображение.", 0.74, price_factor=1.03, search_rank_delta=11),
    Scenario("P16_UNITS_PER_ORDER_DOWN", "basket", "Заказов столько же, но единиц в одном заказе стало меньше.", "После отмены скидки за комплект снизилось среднее число единиц в заказе.", "Верните скидку за две единицы на 14 дней.", 14, "Среднее число единиц в заказе вернулось не ниже майского уровня.", "Если показатель не восстановится, отмените скидку и проверьте состав комплектов.", 1.0, units_per_order_factor=0.78),
    Scenario("P17_PRICE_DOWN_NO_VOLUME", "price", "Цена снизилась, но заказов больше не стало.", "Снижение началось после увеличения скидки продавца.", "Верните майскую скидку на 7 дней.", 7, "Результат на единицу растёт, число заказов снижается не более чем на 5%.", "Если заказов станет заметно меньше, протестируйте промежуточную скидку.", 1.0, price_factor=0.86),
    Scenario("P18_PRICE_UP_VOLUME_DOWN_UNPROVEN", "diagnostic", "Цена выросла одновременно со снижением заказов, но причинность не доказана.", "Изменения цены, позиции и доставки произошли в одну неделю.", "Сохраните текущую рекламу и карточку; верните прежнюю цену на 7 дней как отдельный опыт.", 7, "Доля заказов восстанавливается при сопоставимой посещаемости.", "Если изменений нет, верните цену и отдельно проверьте доставку.", 0.73, price_factor=1.12, search_rank_delta=6, recommendation_status="controlled_test"),
    Scenario("P19_AD_COST_PER_ORDER_UP", "ads", "Расход на рекламный заказ вырос.", "Рост сосредоточен в одной кампании и запросах без заказов.", "Отключите в этой кампании запросы без заказов за последние 14 дней.", 7, "Расход на рекламный заказ вернулся к майской медиане, заказы сохранились.", "Если расход остаётся высоким, снизьте ставку этой кампании до майской.", 0.96, ads_factor=1.65),
    Scenario("P20_ADS_ATTRIBUTION_MISSING", "data_quality", "Расходы на рекламу выросли, но заказы по кампаниям не загружены.", "Причину роста расхода определить нельзя без статистики кампаний.", "Загрузите статистику кампаний за оба месяца.", 1, "Расходы, показы, переходы и заказы сверены по каждой кампании.", "До сверки не менять бюджет и ставки.", 0.92, ads_factor=1.48, quality="ads_unreconciled", recommendation_status="blocked"),
    Scenario("P21_LOGISTICS_WAREHOUSE", "logistics", "Логистика на продажу выросла на одном складе.", "Операции этого склада дали 74% прибавки, но ещё не установлено, какая операция подорожала.", "Сравните по складу тариф доставки, возвратную логистику, габариты и разовые корректировки за оба месяца.", 7, "Источник не менее 90% прибавки определён по конкретным операциям.", "Менять распределение поставки можно только после проверки спроса, остатков и сроков доставки по складам.", 0.98, logistics_factor=1.55, recommendation_status="diagnostic_check"),
    Scenario("P22_LOGISTICS_RETURNS", "returns", "Возвраты увеличили обратную логистику.", "Коды операций и причины возврата указывают на несоответствие размера.", "Исправьте таблицу размеров и добавьте замеры изделия в карточку.", 30, "Доля возвратов по размеру снизилась вдвое.", "Если причина сохранится, проверьте маркировку размеров на товаре.", 0.93, logistics_factor=1.44, cancel_rate=0.08),
    Scenario("P23_LOGISTICS_SHARE_PRICE_EFFECT", "diagnostic", "Доля логистики выросла только из-за снижения цены.", "Логистика на продажу не изменилась, фактическая цена снизилась.", "Верните прежнюю скидку на 7 дней; упаковку и склад не меняйте.", 7, "Доля логистики вернулась к обычному уровню при стабильных заказах.", "Если заказов станет меньше, протестируйте промежуточную скидку.", 1.0, price_factor=0.84, logistics_factor=1.0),
    Scenario("P24_STORAGE_EXCESS_STOCK", "storage", "Хранение выросло из-за избыточного запаса.", "Прямое хранение относится к товару, запаса хватит более чем на 90 дней.", "Отмените следующую поставку и пересчитайте объём через 14 дней.", 14, "Запас снижается к диапазону 35–45 дней, хранение не растёт.", "Если спрос не восстановится, перенесите часть запаса на более быстрый склад.", 0.74, storage_factor=2.5, stock_days=103),
    Scenario("P25_STORAGE_ALLOCATED", "data_quality", "Распределённое хранение выросло, но его нельзя приписать товару.", "Расход распределён по выручке и не содержит прямого ключа товара.", "Сверьте детализацию хранения по складам и артикулам.", 1, "Не менее 95% хранения связано с конкретными товарами или вынесено на уровень магазина.", "До сверки не менять поставку этого товара.", 0.96, storage_factor=1.8, quality="storage_allocated", recommendation_status="blocked"),
    Scenario("P26_COMMISSION_TARIFF", "commission", "Комиссия выросла после изменения тарифа категории.", "Дата и ставка нового тарифа совпадают с ростом комиссии.", "Рассчитайте один ценовой вариант, компенсирующий прибавку комиссии, и протестируйте 7 дней.", 7, "Результат на единицу восстановился, заказы снизились не более чем на 10%.", "Если результат не восстановится, верните цену и пересмотрите участие в акции.", 0.98, price_factor=1.0),
    Scenario("P27_RETURNS_COLOR_MISMATCH", "returns", "Возвраты выросли из-за расхождения цвета на фотографии и в отзывах.", "Повторяющаяся причина возврата подтверждена текстами отзывов и карточками возврата.", "Замените главное фото на снимок при нейтральном освещении и добавьте крупный план ткани.", 30, "Доля возвратов по цвету снизилась минимум на 40%.", "Если причина сохранится, переименуйте цвет по формулировке покупателей.", 0.86, logistics_factor=1.28),
    Scenario("P28_CANCELLATIONS_DELIVERY", "cancellations", "Отмен стало больше после увеличения срока доставки.", "Отмены сосредоточены в регионах, где доставка стала дольше четырёх дней.", "Переместите недельный запас на склад с доставкой до двух дней.", 14, "Доля отмен вернулась к майской медиане.", "Если отмены не снизятся, разберите их причины по статусам заказа.", 0.89, cancel_rate=0.17),
    Scenario("P29_NEGATIVE_RESULT_ADS", "ads", "Товар ушёл в отрицательный результат из-за одной рекламной кампании.", "Расход кампании превышает результат до рекламы, заказы кампании единичны.", "Остановите только эту кампанию на 7 дней.", 7, "Результат товара стал положительным, органические заказы сохранились.", "Если товар остаётся отрицательным, проверьте логистику и фактическую цену.", 0.88, ads_factor=11.0),
    Scenario("P30_NEGATIVE_ONE_RETURN", "low_sample", "Один возврат сделал результат периода отрицательным.", "За период было только две продажи и один возврат.", "Не меняйте цену, рекламу и поставку до накопления 10 продаж.", 14, "Накоплено не менее 10 продаж или два полных периода.", "После накопления выборки повторите оценку.", 0.5, logistics_factor=1.8, recommendation_status="blocked"),
    Scenario("P31_RESULT_DOWN_VOLUME", "demand", "Положительный результат снизился вместе с количеством заказов.", "Расходы на единицу стабильны; первоисточник снижения заказов ещё не подтверждён.", "Сопоставьте органические и рекламные переходы за оба месяца и выделите канал с потерей заказов.", 7, "Определён один канал, объясняющий не менее 70% снижения заказов.", "До определения канала не менять цену, рекламу и карточку.", 0.73, recommendation_status="diagnostic_check"),
    Scenario("P32_RESULT_DOWN_UNIT_COST", "unit_economics", "Результат снизился из-за роста расходов на единицу.", "Заказы стабильны; логистика дала 72% прибавки расходов, но её первичная операция ещё не определена.", "Сначала разложите прибавку логистики по доставке, возвратам, складам, габаритам и корректировкам.", 7, "Конкретная операция объясняет не менее 80% роста логистики.", "После определения операции выполнить одно действие; рекламу одновременно не менять.", 1.0, ads_factor=1.18, logistics_factor=1.36, recommendation_status="diagnostic_check"),
    Scenario("P33_MONTH_DOWN_WEEK_RECOVERY", "trend_reversal", "Месяц хуже предыдущего, но последние семь дней восстанавливаются.", "Заказы и результат растут две недели после устранения дефицита.", "Сохраните текущие условия ещё на 7 полных дней.", 7, "Восстановление сохраняется, товар доступен каждый день.", "Если темп снова снизится, проверьте поисковые позиции.", 0.78, week_factor=1.50, recommendation_status="monitor"),
    Scenario("P34_MONTH_UP_WEEK_DOWN", "early_warning", "Месяц вырос, но последние семь дней ухудшаются.", "Краткосрочное снижение сосредоточено в заказах, месячный результат положительный.", "Не меняйте условия; проверьте ещё один полный семидневный период.", 7, "Заказы вернулись к месячному среднему либо снижение подтвердилось.", "Если снижение подтвердится, разберите посещаемость и наличие.", 1.18, week_factor=0.68, recommendation_status="monitor"),
    Scenario("P35_ONE_DAY_ALERT", "early_warning", "Один полный день заметно хуже обычного.", "Заказы ниже медианы шести таких же дней недели, остальные источники полны.", "Проверьте наличие и работу кампаний; не меняйте цену по одному дню.", 3, "Следующие три сопоставимых дня находятся в обычном диапазоне.", "Если отклонение повторится, откройте семидневный разбор.", 1.0, day_factor=0.42, recommendation_status="signal"),
    Scenario("P36_ONE_DAY_INCOMPLETE", "data_quality", "Последний день неполный.", "Заказы загружены, финансовые операции и реклама ещё не завершены.", "Дождитесь закрытия источников за день.", 1, "Все дневные источники имеют статус полного дня.", "До закрытия дня не формировать бизнес-рекомендацию.", 1.0, day_factor=0.25, quality="incomplete_day", recommendation_status="blocked"),
    Scenario("P37_STOCK_3_DAYS", "availability", "Запаса осталось меньше чем на три дня.", "Текущий темп заказов и доступный остаток подтверждены по складам.", "Отправьте на продающие склады запас на 21 день продаж.", 3, "Поставка создана до расчётной даты дефицита.", "Если поставка не успевает, временно ограничьте рекламу только этого товара.", 1.12, stock_days=2.4),
    Scenario("P38_STOCK_7_DAYS", "availability", "Запаса осталось на шесть дней.", "Остаток и темп заказов устойчивы две недели.", "Создайте поставку на 21 день продаж в течение двух дней.", 7, "После приёмки запас составляет 14–28 дней.", "Если склад не принимает поставку, выберите следующий продающий склад.", 1.08, stock_days=6.1),
    Scenario("P39_STOCK_DATA_MISSING", "data_quality", "Остатки не обновлялись четыре дня.", "Достоверный риск дефицита рассчитать нельзя.", "Восстановите загрузку остатков и получите полный снимок по складам.", 1, "Остатки обновлены на текущий полный день по всем складам.", "До обновления не менять поставки по расчётному запасу.", 0.94, stock_days=math.nan, quality="stock_missing", recommendation_status="blocked"),
    Scenario("P40_NEW_PRODUCT", "new_product", "У нового товара нет завершённого периода для сравнения.", "Товар продаётся меньше 14 дней.", "Накопите первый полный 28-дневный период; контролируйте только заказы, расходы и остаток.", 28, "Накоплен полный сопоставимый период и не менее 30 заказов.", "Если запас закончится раньше, используйте только прогноз дефицита.", 1.0, quality="no_baseline", recommendation_status="blocked"),
]


GROUPS = [
    ("Постельное бельё", ["Сатин Облако", "Перкаль Север", "Страйп Лаванда", "Поплин Мята", "Сатин Графит", "Перкаль Песок"]),
    ("Простыни на резинке", ["Молочный сатин", "Серый перкаль", "Оливковый поплин", "Лавандовый сатин", "Песочный поплин", "Белый страйп"]),
    ("Наволочки", ["Сатин молочный", "Перкаль серый", "Страйп белый", "Поплин оливковый", "Сатин пудровый", "Перкаль голубой"]),
    ("Полотенца", ["Отель белое", "Графит банное", "Песок банное", "Оливковое банное", "Молочное лицевое", "Серое лицевое"]),
    ("Пледы", ["Вафельный молочный", "Вафельный графит", "Хлопковый песочный", "Хлопковый оливковый", "Муслиновый пудровый", "Муслиновый голубой"]),
    ("Покрывала", ["Стёганое молочное", "Стёганое графит", "Вафельное песочное", "Сатиновое оливковое", "Муслиновое пудровое", "Хлопковое серое"]),
    ("Одеяла и подушки", ["Одеяло всесезонное", "Одеяло лёгкое", "Подушка 50×70", "Подушка 70×70"]),
]


def product_catalog() -> list[dict]:
    flattened = [(group, name) for group, names in GROUPS for name in names]
    sizes_by_group = {
        "Постельное бельё": ["1,5-спальное", "2-спальное", "Евро"],
        "Простыни на резинке": ["90×200 см", "140×200 см", "160×200 см", "180×200 см"],
        "Наволочки": ["50×70 см, 2 шт.", "70×70 см, 2 шт."],
        "Полотенца": ["50×90 см", "70×140 см"],
        "Пледы": ["150×200 см", "200×220 см"],
        "Покрывала": ["160×220 см", "220×240 см"],
        "Одеяла и подушки": ["140×205 см", "172×205 см", "50×70 см", "70×70 см"],
    }
    group_codes = {
        "Постельное бельё": "BED",
        "Простыни на резинке": "SHEET",
        "Наволочки": "PILLOWCASE",
        "Полотенца": "TOWEL",
        "Пледы": "THROW",
        "Покрывала": "COVER",
        "Одеяла и подушки": "SLEEP",
    }
    group_indexes: dict[str, int] = {}
    rows = []
    for index, (scenario, (group, name)) in enumerate(zip(SCENARIOS, flattened, strict=True), start=1):
        group_index = group_indexes.get(group, 0)
        group_indexes[group] = group_index + 1
        sizes = sizes_by_group[group]
        size = sizes[group_index % len(sizes)]
        rows.append(
            {
                "product_id": f"LH-{index:03d}",
                "vendor_code": f"LUN-{group_codes[group]}-{index:03d}",
                "name": f"{name}, {size}",
                "group": group,
                "scenario_code": scenario.code,
            }
        )
    return rows


def rounded(value: float) -> float:
    return round(value, 2)


def build_metrics(product: dict, scenario: Scenario, index: int) -> dict:
    base_orders = int(620 + (index % 9) * 95 + RNG.randint(-35, 35))
    if scenario.code in {"P30_NEGATIVE_ONE_RETURN", "P40_NEW_PRODUCT"}:
        base_orders = 4 if scenario.code == "P30_NEGATIVE_ONE_RETURN" else 18
    base_price = [890, 1190, 1490, 1890, 2490, 3190, 4490, 5990][index % 8]
    units_per_order_prev = 1.0 + (0.18 if product["group"] in {"Наволочки", "Полотенца"} else 0.04)
    prev_orders = base_orders
    curr_orders = max(1, round(prev_orders * scenario.order_factor))
    prev_cancel_rate = 0.05
    prev_fulfilled = round(prev_orders * (1 - prev_cancel_rate))
    curr_fulfilled = round(curr_orders * (1 - scenario.cancel_rate))
    prev_units = max(1, round(prev_fulfilled * units_per_order_prev))
    curr_units = max(0, round(curr_fulfilled * units_per_order_prev * scenario.units_per_order_factor))
    prev_price = float(base_price)
    curr_price = rounded(prev_price * scenario.price_factor)
    prev_revenue = rounded(prev_units * prev_price)
    curr_revenue = rounded(curr_units * curr_price)
    prev_ads = rounded(prev_revenue * (0.085 + (index % 4) * 0.008))
    curr_ads = rounded(prev_ads * scenario.ads_factor)
    prev_log_unit = 118 + (index % 7) * 17
    curr_log_unit = rounded(prev_log_unit * scenario.logistics_factor)
    prev_logistics = rounded(prev_fulfilled * prev_log_unit)
    curr_logistics = rounded(curr_fulfilled * curr_log_unit)
    prev_storage = rounded(prev_revenue * 0.009)
    curr_storage = rounded(prev_storage * scenario.storage_factor)
    prev_commission = rounded(prev_revenue * 0.18)
    curr_commission_rate = 0.20 if scenario.code == "P26_COMMISSION_TARIFF" else 0.18
    curr_commission = rounded(curr_revenue * curr_commission_rate)
    prev_acquiring = rounded(prev_revenue * 0.015)
    curr_acquiring = rounded(curr_revenue * 0.015)
    prev_other = rounded(prev_revenue * 0.006)
    curr_other = rounded(curr_revenue * 0.006)
    if scenario.code == "P30_NEGATIVE_ONE_RETURN":
        curr_other = rounded(curr_other + curr_revenue * 1.35 + 2_400)
    prev_expenses = rounded(prev_ads + prev_logistics + prev_storage + prev_commission + prev_acquiring + prev_other)
    curr_expenses = rounded(curr_ads + curr_logistics + curr_storage + curr_commission + curr_acquiring + curr_other)
    prev_result = rounded(prev_revenue - prev_expenses)
    curr_result = rounded(curr_revenue - curr_expenses)
    prev_views = max(10_000, prev_orders * 130)
    curr_views = max(5_000, round(prev_views * scenario.order_factor * (1.0 if scenario.search_rank_delta == 0 else 0.78)))
    prev_clicks = round(prev_views * 0.035)
    ctr_factor = 0.58 if scenario.code == "P10_AD_CTR_DOWN" else 1.0
    curr_clicks = round(curr_views * 0.035 * ctr_factor)
    prev_card_visits = round(prev_orders / 0.082)
    if scenario.code in {"P11_PRICE_UP_CONVERSION_DOWN", "P18_PRICE_UP_VOLUME_DOWN_UNPROVEN"}:
        curr_card_visits = prev_card_visits
    else:
        curr_card_visits = max(1, round(prev_card_visits * max(0.55, scenario.order_factor)))
    prev_order_conversion = rounded(prev_orders / prev_card_visits)
    curr_order_conversion = rounded(curr_orders / curr_card_visits)
    prev_attributed_orders = round(prev_orders * 0.31)
    if scenario.code in {"P04_REVENUE_UP_AD_RESULT_DOWN", "P19_AD_COST_PER_ORDER_UP", "P29_NEGATIVE_RESULT_ADS"}:
        curr_attributed_orders = max(1, round(prev_attributed_orders * 0.72))
    elif scenario.code == "P03_AD_GROWTH_JUSTIFIED":
        curr_attributed_orders = round(prev_attributed_orders * 1.38)
    elif scenario.quality == "ads_unreconciled":
        curr_attributed_orders = 0
    else:
        curr_attributed_orders = max(1, round(prev_attributed_orders * scenario.order_factor))
    prev_returns = max(0, round(prev_fulfilled * 0.035))
    curr_return_rate = 0.035
    if scenario.code in {"P22_LOGISTICS_RETURNS", "P27_RETURNS_COLOR_MISMATCH"}:
        curr_return_rate = 0.115
    if scenario.code == "P30_NEGATIVE_ONE_RETURN":
        prev_returns, curr_returns = 0, 1
    else:
        curr_returns = max(0, round(curr_fulfilled * curr_return_rate))
    prev_rating = 4.78 - (index % 3) * 0.03
    curr_rating = rounded(max(3.0, prev_rating + scenario.rating_delta))
    prev_rank = 4 + index % 12
    curr_rank = max(1, prev_rank + scenario.search_rank_delta)
    week_orders_prev = max(1, round(prev_orders / 4.35))
    week_orders_curr = max(0, round(curr_orders / 4.35 * scenario.week_factor))
    day_orders_baseline = max(1, round(curr_orders / 30))
    day_orders_current = max(0, round(day_orders_baseline * scenario.day_factor))
    effect = rounded(curr_result - prev_result)
    impact = abs(effect)
    return {
        **product,
        "quality_status": scenario.quality,
        "recommendation_status": scenario.recommendation_status,
        "prev_orders": prev_orders,
        "curr_orders": curr_orders,
        "prev_fulfilled_orders": prev_fulfilled,
        "curr_fulfilled_orders": curr_fulfilled,
        "prev_cancellations": prev_orders - prev_fulfilled,
        "curr_cancellations": curr_orders - curr_fulfilled,
        "prev_returns": prev_returns,
        "curr_returns": curr_returns,
        "prev_units": prev_units,
        "curr_units": curr_units,
        "prev_price": prev_price,
        "curr_price": curr_price,
        "prev_revenue": prev_revenue,
        "curr_revenue": curr_revenue,
        "prev_ads": prev_ads,
        "curr_ads": curr_ads,
        "prev_logistics": prev_logistics,
        "curr_logistics": curr_logistics,
        "prev_logistics_per_sale": prev_log_unit,
        "curr_logistics_per_sale": curr_log_unit,
        "prev_logistics_per_sold_unit": rounded(prev_logistics / max(prev_units, 1)),
        "curr_logistics_per_sold_unit": rounded(curr_logistics / max(curr_units, 1)),
        "prev_storage": prev_storage,
        "curr_storage": curr_storage,
        "prev_commission": prev_commission,
        "curr_commission": curr_commission,
        "prev_total_expenses": prev_expenses,
        "curr_total_expenses": curr_expenses,
        "prev_result": prev_result,
        "curr_result": curr_result,
        "prev_result_per_unit": rounded(prev_result / max(prev_units, 1)),
        "curr_result_per_unit": rounded(curr_result / max(curr_units, 1)),
        "result_delta": effect,
        "financial_impact": impact,
        "prev_views": prev_views,
        "curr_views": curr_views,
        "prev_clicks": prev_clicks,
        "curr_clicks": curr_clicks,
        "prev_card_visits": prev_card_visits,
        "curr_card_visits": curr_card_visits,
        "prev_order_conversion": prev_order_conversion,
        "curr_order_conversion": curr_order_conversion,
        "prev_attributed_orders": prev_attributed_orders,
        "curr_attributed_orders": curr_attributed_orders,
        "prev_rating": prev_rating,
        "curr_rating": curr_rating,
        "prev_search_rank": prev_rank,
        "curr_search_rank": curr_rank,
        "stock_days": None if math.isnan(scenario.stock_days) else scenario.stock_days,
        "prev_7d_orders": week_orders_prev,
        "curr_7d_orders": week_orders_curr,
        "baseline_day_orders": day_orders_baseline,
        "current_day_orders": day_orders_current,
    }


def money(value: float) -> str:
    return f"{value:,.0f} ₽".replace(",", " ")


def recommendation_row(metrics: dict, scenario: Scenario) -> dict:
    facts = (
        f"Заказы: {metrics['prev_orders']} → {metrics['curr_orders']}; "
        f"выручка: {money(metrics['prev_revenue'])} → {money(metrics['curr_revenue'])}; "
        f"результат: {money(metrics['prev_result'])} → {money(metrics['curr_result'])}."
    )
    if scenario.family == "logistics":
        facts += (
            f" Логистика на продажу: {money(metrics['prev_logistics_per_sale'])} → "
            f"{money(metrics['curr_logistics_per_sale'])}."
        )
    if scenario.family == "ads":
        facts += f" Реклама: {money(metrics['prev_ads'])} → {money(metrics['curr_ads'])}."
    short_problem = scenario.problem
    if scenario.recommendation_status == "blocked":
        full_text = f"{facts} {scenario.proven_cause} Действие пока недоступно: {scenario.action}"
    elif scenario.recommendation_status == "no_action":
        full_text = f"{facts} {scenario.proven_cause} {scenario.action}"
    else:
        full_text = f"{facts} Причина: {scenario.proven_cause} Действие: {scenario.action}"
    checks = {
        "specificity": 5,
        "evidence": 5 if scenario.quality == "complete" else 4,
        "usefulness": 5,
        "executable": 5,
        "measurable": 5,
        "concise": 4,
        "no_extra_facts": 5,
        "no_causal_guess": 5,
        "plain_language": 5,
    }
    return {
        "scenario_code": scenario.code,
        "product_id": metrics["product_id"],
        "product_name": metrics["name"],
        "status": scenario.recommendation_status,
        "short_problem": short_problem,
        "confirmed_facts": facts,
        "proven_cause": scenario.proven_cause,
        "action": scenario.action,
        "check_period_days": scenario.check_days,
        "success_criterion": scenario.success,
        "next_step": scenario.fallback,
        "full_recommendation": full_text,
        **checks,
        "quality_score": sum(checks.values()),
        "review_status": "candidate_for_user_review",
    }


def evidence_row(metrics: dict, scenario: Scenario) -> dict:
    source = "synthetic_period_metrics"
    dimension = "product"
    previous = metrics["prev_result"]
    current = metrics["curr_result"]
    unit = "RUB"
    contribution = abs(metrics["result_delta"])
    coverage = 100
    status = "proven" if scenario.quality == "complete" and scenario.recommendation_status not in {"diagnostic_check", "controlled_test", "blocked"} else "insufficient"
    detail = scenario.proven_cause

    if scenario.family == "logistics" or scenario.code in {"P02_SCALE_LOGISTICS_PRESSURE", "P32_RESULT_DOWN_UNIT_COST"}:
        source = "synthetic_logistics_operations_by_warehouse"
        dimension = "Коледино → Электросталь"
        previous = metrics["prev_logistics_per_sold_unit"]
        current = metrics["curr_logistics_per_sold_unit"]
        unit = "RUB_per_sold_unit"
        extra_at_current_volume = max(
            0.0,
            (metrics["curr_logistics_per_sold_unit"] - metrics["prev_logistics_per_sold_unit"])
            * metrics["curr_units"],
        )
        contribution = rounded(extra_at_current_volume * 0.74)
        coverage = 74
        detail = (
            f"Склад Коледино дал {money(contribution)} из подтверждённой прибавки логистики; "
            "медианный срок доставки из Коледино и Электростали одинаков — 2 дня."
        )
    elif scenario.family == "ads":
        source = "synthetic_campaign_daily_stats"
        dimension = "Кампания Поиск · Основные запросы"
        previous = metrics["prev_ads"]
        current = metrics["curr_ads"]
        unit = "RUB"
        contribution = max(0.0, rounded(metrics["curr_ads"] - metrics["prev_ads"]))
        coverage = 82
    elif scenario.family == "price":
        source = "synthetic_price_and_card_funnel_history"
        dimension = "Фактическая цена и доля заказов"
        previous = metrics["prev_price"]
        current = metrics["curr_price"]
        unit = "RUB"
        contribution = abs(metrics["result_delta"])
        coverage = 100
    elif scenario.family == "availability":
        source = "synthetic_stock_history_by_warehouse"
        dimension = "Продающие склады"
        previous = 30.0
        current = metrics["stock_days"]
        unit = "stock_days"
        contribution = abs(metrics["result_delta"])
        coverage = 98
    elif scenario.family in {"returns", "reviews", "cancellations"}:
        source = "synthetic_return_reason_and_review_history"
        dimension = scenario.family
        previous = metrics["prev_returns"] if scenario.family != "cancellations" else metrics["prev_cancellations"]
        current = metrics["curr_returns"] if scenario.family != "cancellations" else metrics["curr_cancellations"]
        unit = "events"
        contribution = abs(metrics["result_delta"])
        coverage = 91
    elif scenario.family in {"organic_visibility", "competition"}:
        source = "synthetic_search_position_history"
        dimension = "Три основных запроса"
        previous = metrics["prev_search_rank"]
        current = metrics["curr_search_rank"]
        unit = "search_rank"
        contribution = abs(metrics["result_delta"])
        coverage = 88
    elif scenario.family == "delivery":
        source = "synthetic_delivery_time_by_region"
        dimension = "Москва и Московская область"
        previous = 2
        current = 5
        unit = "days"
        contribution = abs(metrics["result_delta"])
        coverage = 64
    elif scenario.family == "storage":
        source = "synthetic_storage_operations_and_stock_history"
        dimension = "Товар и склад"
        previous = metrics["prev_storage"]
        current = metrics["curr_storage"]
        unit = "RUB"
        contribution = max(0.0, rounded(metrics["curr_storage"] - metrics["prev_storage"]))
        coverage = 100
    elif scenario.family == "commission":
        source = "synthetic_marketplace_tariff_history"
        dimension = metrics["group"]
        previous = 18
        current = 20
        unit = "percent"
        contribution = rounded(metrics["curr_revenue"] * 0.02)
        coverage = 100
    elif scenario.code == "P23_LOGISTICS_SHARE_PRICE_EFFECT":
        source = "synthetic_price_and_logistics_history"
        dimension = "Фактическая цена и логистика на продажу"
        previous = metrics["prev_price"]
        current = metrics["curr_price"]
        unit = "RUB_price"
        contribution = abs(metrics["result_delta"])
        coverage = 100
        status = "proven"
    elif scenario.family in {"data_quality", "diagnostic", "low_sample", "new_product"}:
        status = "insufficient"

    return {
        "scenario_code": scenario.code,
        "product_id": metrics["product_id"],
        "evidence_source": source,
        "dimension": dimension,
        "previous_value": rounded(float(previous)) if isinstance(previous, (int, float)) and not math.isnan(float(previous)) else "",
        "current_value": rounded(float(current)) if isinstance(current, (int, float)) and not math.isnan(float(current)) else "",
        "unit": unit,
        "contribution_rub": rounded(float(contribution)),
        "coverage_pct": coverage,
        "causal_status": status,
        "evidence_text": detail,
    }


STORE_SCENARIOS = [
    ("S01", "Результат, выручка и расходы растут", "Показывать рост и товары, которые дали основной вклад; не объявлять рост расходов проблемой."),
    ("S02", "Результат падает вместе с выручкой, расходы снижаются", "Начать с товаров, потерявших заказы и выручку."),
    ("S03", "Выручка стабильна, расходы растут", "Показать подтверждённые статьи и товары, давшие прибавку расходов."),
    ("S04", "Выручка растёт, результат не растёт", "Показать ухудшение результата на единицу и его подтверждённый источник."),
    ("S05", "Магазин растёт, несколько ключевых товаров снижаются", "Показать локальные потери со ссылкой на товары, не называть магазин проблемным."),
    ("S06", "Большая часть снижения сосредоточена в трёх товарах", "Поставить эти товары первыми и показать их суммарный вклад."),
    ("S07", "Снижение распределено по большому числу товаров", "Сгруппировать товары по общей доказанной причине."),
    ("S08", "Результат стабилен, расходы на продажу ухудшаются", "Показать риск без кризисной формулировки."),
    ("S09", "Результат магазина отрицательный", "Разделить товарные потери, общие списания и нераспределённые расходы."),
    ("S10", "Данные магазина устарели", "Заблокировать бизнес-рекомендации и показать источник задержки."),
    ("S11", "Показатели и проблемы построены по разным снимкам", "Заблокировать публикацию до единого снимка."),
    ("S12", "Месяц положительный, последние семь дней снижаются", "Показать оперативный сигнал отдельно от месячного итога."),
    ("S13", "Месяц отрицательный, последние семь дней восстанавливаются", "Не рекомендовать резких действий до подтверждения восстановления."),
    ("S14", "Однодневное отклонение при стабильном месяце", "Показать сигнал только после проверки полноты дня."),
    ("S15", "Единичное крупное списание изменило результат магазина", "Показать назначение операции и не распределять её искусственно по товарам."),
]


QUALITY_SCENARIOS = [
    ("Q01", "Неполный финансовый период", "Блокировать сравнение результата."),
    ("Q02", "Неполный оперативный день", "Не строить однодневный сигнал."),
    ("Q03", "Реклама не сверена", "Не рекомендовать изменение бюджета."),
    ("Q04", "Расход есть, показов и переходов нет", "Показать запрос на загрузку статистики."),
    ("Q05", "Остатки устарели", "Не рассчитывать дату дефицита."),
    ("Q06", "Хранение распределено по выручке", "Не приписывать рост хранения товару."),
    ("Q07", "Расход имеет неоднозначное назначение", "Вынести на уровень магазина до классификации."),
    ("Q08", "Товар связан с двумя внутренними ключами", "Заблокировать товарную рекомендацию до объединения."),
    ("Q09", "Служебная финансовая строка принята за товар", "Исключить строку из товарных сценариев."),
    ("Q10", "Нет названия товара", "Показывать артикул, не генерировать текст с техническим ключом."),
    ("Q11", "Слишком малый объём", "Показывать факт без сильного действия."),
    ("Q12", "Загрузка остановилась на части страниц", "Не подтверждать период."),
    ("Q13", "Рекламные строки продублированы", "Заблокировать показатели рекламы до устранения дублей."),
    ("Q14", "Показатели построены по разным снимкам", "Заблокировать весь бизнес-вывод."),
    ("Q15", "Нет истории позиций и рейтинга", "Не называть видимость или рейтинг причиной."),
]


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(metrics: list[dict], recommendations: list[dict]) -> None:
    lines = [
        "# LUNARO HOME — пул сценариев вымышленного магазина",
        "",
        "Это отдельный исследовательский набор. Он не подключён к кабинету и не содержит данных реального продавца.",
        "",
        "## Магазин",
        "",
        "- Ниша: домашний текстиль.",
        "- Ассортимент: 40 товаров в семи связанных группах.",
        "- Сравнение: июнь 2026 против мая 2026.",
        "- Оперативный сигнал: последние 7 полных дней и последний полный день.",
        "- Результат: выручка минус подтверждённые расходы площадки и реклама. Себестоимость не используется.",
        "",
        "## Полный товарный пул",
        "",
        "| Код | Товар | Цепочка | Статус |",
        "|---|---|---|---|",
    ]
    rec_by_code = {row["scenario_code"]: row for row in recommendations}
    for metric in metrics:
        rec = rec_by_code[metric["scenario_code"]]
        chain = f"{rec['short_problem']} → {rec['proven_cause']} → {rec['action']}"
        lines.append(f"| {metric['scenario_code']} | {metric['name']} | {chain} | {rec['status']} |")
    lines.extend(
        [
            "",
            "## Магазинные сценарии",
            "",
            "| Код | Ситуация | Правильная реакция |",
            "|---|---|---|",
        ]
    )
    lines.extend(f"| {code} | {situation} | {reaction} |" for code, situation, reaction in STORE_SCENARIOS)
    lines.extend(
        [
            "",
            "## Сценарии качества данных",
            "",
            "| Код | Ограничение | Реакция движка |",
            "|---|---|---|",
        ]
    )
    lines.extend(f"| {code} | {situation} | {reaction} |" for code, situation, reaction in QUALITY_SCENARIOS)
    (ROOT / "scenario_pool.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate(metrics: list[dict], recommendations: list[dict], evidence: list[dict]) -> list[dict]:
    by_code = {row["scenario_code"]: row for row in metrics}
    checks: list[tuple[str, str, bool]] = []

    def add(code: str, rule: str, passed: bool) -> None:
        checks.append((code, rule, bool(passed)))

    for code in ("P01_HEALTHY_SCALE", "P03_AD_GROWTH_JUSTIFIED"):
        row = by_code[code]
        add(code, "orders_up", row["curr_orders"] > row["prev_orders"])
        add(code, "result_up", row["curr_result"] > row["prev_result"])
    row = by_code["P02_SCALE_LOGISTICS_PRESSURE"]
    add(row["scenario_code"], "orders_and_result_up", row["curr_orders"] > row["prev_orders"] and row["curr_result"] > row["prev_result"])
    add(row["scenario_code"], "unit_result_down", row["curr_result_per_unit"] < row["prev_result_per_unit"])
    for code in ("P04_REVENUE_UP_AD_RESULT_DOWN", "P05_REVENUE_UP_LOGISTICS_RESULT_DOWN"):
        row = by_code[code]
        add(code, "revenue_up_result_down", row["curr_revenue"] > row["prev_revenue"] and row["curr_result"] < row["prev_result"])
    row = by_code["P06_PRICE_DOWN_RESULT_DOWN"]
    add(row["scenario_code"], "price_and_result_down", row["curr_price"] < row["prev_price"] and row["curr_result"] < row["prev_result"])
    row = by_code["P07_STOCKOUT"]
    add(row["scenario_code"], "stockout_and_orders_down", row["stock_days"] == 0 and row["curr_orders"] < row["prev_orders"])
    row = by_code["P10_AD_CTR_DOWN"]
    add(row["scenario_code"], "views_comparable_ctr_down", row["curr_clicks"] / row["curr_views"] < row["prev_clicks"] / row["prev_views"])
    row = by_code["P11_PRICE_UP_CONVERSION_DOWN"]
    add(row["scenario_code"], "price_up_conversion_down", row["curr_price"] > row["prev_price"] and row["curr_order_conversion"] < row["prev_order_conversion"])
    row = by_code["P16_UNITS_PER_ORDER_DOWN"]
    add(row["scenario_code"], "orders_flat_units_down", row["curr_orders"] == row["prev_orders"] and row["curr_units"] < row["prev_units"])
    row = by_code["P17_PRICE_DOWN_NO_VOLUME"]
    add(row["scenario_code"], "price_down_orders_flat", row["curr_price"] < row["prev_price"] and row["curr_orders"] == row["prev_orders"])
    row = by_code["P23_LOGISTICS_SHARE_PRICE_EFFECT"]
    add(
        row["scenario_code"],
        "logistics_unit_flat_price_down",
        abs(row["curr_logistics_per_sold_unit"] - row["prev_logistics_per_sold_unit"]) < 0.01
        and row["curr_price"] < row["prev_price"],
    )
    row = by_code["P24_STORAGE_EXCESS_STOCK"]
    add(row["scenario_code"], "storage_and_stock_high", row["curr_storage"] > row["prev_storage"] and row["stock_days"] > 90)
    row = by_code["P29_NEGATIVE_RESULT_ADS"]
    add(row["scenario_code"], "negative_result_ads_up", row["curr_result"] < 0 and row["curr_ads"] > row["prev_ads"])
    row = by_code["P30_NEGATIVE_ONE_RETURN"]
    add(row["scenario_code"], "tiny_sample_one_return_negative", row["curr_orders"] <= 2 and row["curr_returns"] == 1 and row["curr_result"] < 0)
    row = by_code["P33_MONTH_DOWN_WEEK_RECOVERY"]
    add(row["scenario_code"], "month_down_week_up", row["curr_result"] < row["prev_result"] and row["curr_7d_orders"] > row["prev_7d_orders"])
    row = by_code["P34_MONTH_UP_WEEK_DOWN"]
    add(row["scenario_code"], "month_up_week_down", row["curr_result"] > row["prev_result"] and row["curr_7d_orders"] < row["prev_7d_orders"])
    row = by_code["P36_ONE_DAY_INCOMPLETE"]
    add(row["scenario_code"], "incomplete_day_blocked", row["quality_status"] == "incomplete_day" and row["recommendation_status"] == "blocked")

    forbidden = ("примерно", "следовательно", "в фокусе", "оптимизировать", "обратить внимание", "разобраться")
    for row in recommendations:
        text = " ".join(str(row[key]).lower() for key in ("short_problem", "proven_cause", "action", "success_criterion", "next_step"))
        add(row["scenario_code"], "forbidden_wording_absent", not any(token in text for token in forbidden))
        add(row["scenario_code"], "quality_score_at_least_40", int(row["quality_score"]) >= 40)
    evidence_by_code = {row["scenario_code"]: row for row in evidence}
    for row in recommendations:
        expected_proven = row["status"] in {"action", "monitor", "signal", "no_action"}
        available = evidence_by_code.get(row["scenario_code"])
        add(
            row["scenario_code"],
            "causal_evidence_gate",
            bool(available) and (available["causal_status"] == "proven" if expected_proven else True),
        )

    return [
        {"scenario_code": code, "rule": rule, "passed": passed}
        for code, rule, passed in checks
    ]


def main() -> None:
    products = product_catalog()
    metrics = [build_metrics(product, scenario, index) for index, (product, scenario) in enumerate(zip(products, SCENARIOS, strict=True), start=1)]
    recommendations = [recommendation_row(metric, scenario) for metric, scenario in zip(metrics, SCENARIOS, strict=True)]
    evidence = [evidence_row(metric, scenario) for metric, scenario in zip(metrics, SCENARIOS, strict=True)]
    store_profile = {
        "store_name": "LUNARO HOME",
        "fictional": True,
        "marketplace": "Wildberries-like synthetic environment",
        "niche": "Домашний текстиль",
        "period_previous": {"from": "2026-05-01", "to": "2026-05-31"},
        "period_current": {"from": "2026-06-01", "to": "2026-06-30"},
        "last_full_operational_day": "2026-07-25",
        "uses_cogs": False,
        "product_count": len(products),
        "scenario_counts": {"product": len(SCENARIOS), "store": len(STORE_SCENARIOS), "data_quality": len(QUALITY_SCENARIOS)},
        "previous_revenue": rounded(sum(row["prev_revenue"] for row in metrics)),
        "current_revenue": rounded(sum(row["curr_revenue"] for row in metrics)),
        "previous_result": rounded(sum(row["prev_result"] for row in metrics)),
        "current_result": rounded(sum(row["curr_result"] for row in metrics)),
    }
    (ROOT / "store_profile.json").write_text(json.dumps(store_profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (ROOT / "scenario_catalog.json").write_text(
        json.dumps(
            [
                {**asdict(scenario), "stock_days": None if math.isnan(scenario.stock_days) else scenario.stock_days}
                for scenario in SCENARIOS
            ],
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (ROOT / "cabinet_pack.json").write_text(
        json.dumps(
            {
                "store": store_profile,
                "products": metrics,
                "recommendations": recommendations,
                "evidence": evidence,
                "store_scenarios": [
                    {"scenario_code": code, "situation": situation, "engine_response": response}
                    for code, situation, response in STORE_SCENARIOS
                ],
                "quality_scenarios": [
                    {"scenario_code": code, "quality_problem": situation, "engine_response": response}
                    for code, situation, response in QUALITY_SCENARIOS
                ],
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    write_csv(ROOT / "products.csv", products)
    write_csv(ROOT / "product_period_metrics.csv", metrics)
    write_csv(ROOT / "recommendations.csv", recommendations)
    write_csv(ROOT / "cause_evidence.csv", evidence)
    validation = validate(metrics, recommendations, evidence)
    write_csv(ROOT / "validation_results.csv", validation)
    write_csv(
        ROOT / "store_scenarios.csv",
        [{"scenario_code": code, "situation": situation, "engine_response": response} for code, situation, response in STORE_SCENARIOS],
    )
    write_csv(
        ROOT / "data_quality_scenarios.csv",
        [{"scenario_code": code, "quality_problem": situation, "engine_response": response} for code, situation, response in QUALITY_SCENARIOS],
    )
    write_markdown(metrics, recommendations)
    failed = [row for row in validation if not row["passed"]]
    if failed:
        raise SystemExit(f"Validation failed: {failed}")
    print(json.dumps(store_profile, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
