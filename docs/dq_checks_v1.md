# Data Quality Checks v1 — Ozon Profit Control

## 1) Зачем нужны проверки
Проверки не дают построить отчёт, если данные неполные или противоречивые.
Это защищает от ситуации “дашборд красивый, цифры неверные”.

## 2) Формат результата
Каждая проверка возвращает:
- check_id
- severity: OK / WARN / ERROR
- message (что не так)
- metric_value (число, процент)
- threshold (порог)
- sample (несколько примеров строк/ключей, если возможно)

## 3) Обязательные проверки v1

### DQ-001: Уникальность posting_number
- Таблица: stg_posting
- Правило: posting_number уникален
- ERROR если: есть дубликаты

### DQ-002: Уникальность operation_id
- Таблица: stg_transaction
- Правило: operation_id уникален
- ERROR если: есть дубликаты

### DQ-003: quantity > 0
- Таблица: stg_posting_item
- Правило: quantity должен быть > 0
- ERROR если: есть quantity <= 0

### DQ-004: Мэппинг товаров (coverage)
- Таблицы: stg_posting_item + dim_product
- Правило: доля строк stg_posting_item, которые имеют найденный offer_id (или sku) в dim_product, должна быть высокой
- WARN если: < 95%
- ERROR если: < 80%

### DQ-005: Покрытие себестоимости (COGS coverage)
- Таблицы: stg_posting_item + fact_cogs
- Правило: доля проданных единиц (units_sold) с найденным unit_cogs должна быть:
  - WARN если: < 95%
  - ERROR если: < 80%
- Примечание: без COGS прибыль неполная

### DQ-006: Невозможные значения денег
- Таблица: stg_posting_item_financial
- Правило: payout, price, commission_amount не должны быть “явно невозможными”
  - price < 0 → ERROR
  - commission_amount < 0 → WARN/ERROR (зависит от типа операции)
  - payout < 0 → WARN (часто связано с корректировками; требуется трактовка)

### DQ-007: Сверка агрегатов (soft reconcile)
- Таблицы: stg_posting_item_financial vs stg_transaction
- Правило: сумма payout по posting_financial за период должна быть сопоставима с суммой релевантных финансовых операций за период
- WARN если: расхождение > 10%
- ERROR если: расхождение > 25%
- Примечание: допускаются лаги и корректировки, поэтому это “мягкая” сверка

## 4) Правило остановки
- Если есть хотя бы один ERROR → marts не строятся, требуется исправление данных/маппинга/COGS.
- Если только WARN → marts строятся, но в отчёт добавляется блок “Ограничения данных”.