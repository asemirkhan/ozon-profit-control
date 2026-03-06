# Ozon API Endpoints v1 — Ozon Profit Control

## Назначение документа
Список источников данных Ozon, используемых в версии v1, и соответствие “endpoint → таблицы (staging/fact/dim) → витрины (mart)”.

---

## 1) Аутентификация и общие правила

### 1.1 Заголовки
- `Client-Id: <CLIENT_ID>`
- `Api-Key: <API_KEY>`
- `Content-Type: application/json`

### 1.2 Базовые домены
- Seller API: `https://api-seller.ozon.ru`

### 1.3 Лимиты и пагинация
- Использовать `limit`/`offset` или `last_id` (в зависимости от конкретного метода).
- Для больших периодов использовать разбиение по времени (chunking) и хранить `request_params` вместе с raw JSON.

---

## 2) Products (справочник товаров)

### 2.1 Endpoint
- `POST /v3/product/list`

### 2.2 Назначение
Построение справочника товаров для мэппинга:
- `offer_id` (артикул продавца) ↔ `product_id` ↔ `sku`

### 2.3 Загружаемые таблицы
- `dim_product`

### 2.4 Ключевые поля (ожидаемые)
- `offer_id`
- `product_id`
- `sku`
- `name`
- `brand`
- `category_id` / `category_name` (если доступно)
- `archived` (если доступно)

### 2.5 Частота обновления
- Ежедневно (достаточно 1 раза в сутки)

---

## 3) Postings (FBS) — отправления, статусы, состав заказов

### 3.1 Endpoint
- `POST /v3/posting/fbs/list`

### 3.2 Назначение
Получение списка отправлений и состава заказов (товары внутри отправления).

### 3.3 Загружаемые таблицы
- `stg_posting`
- `stg_posting_item`
- (опционально, если доступно в ответе через `with`): `stg_posting_item_financial`

### 3.4 Ключевые поля (ожидаемые)
**Шапка отправления (posting):**
- `posting_number`
- `status`
- даты жизненного цикла: `created_at`, `in_process_at`, `shipped_at`, `delivered_at`, `cancelled_at` (набор зависит от API)
- `delivery_schema` (если есть)
- `warehouse_id` (если есть)
- `is_multibox` (если есть)

**Товары внутри отправления (products[]):**
- `offer_id`
- `sku`
- `product_id` (если есть)
- `quantity`
- `price` (если есть)
- `name` (если есть)

### 3.5 Период выборки
- Использовать фильтры по датам (как предусмотрено методом).
- Для больших периодов дробить запросы (например, по неделям).

### 3.6 Частота обновления
- Ежедневно

---

## 4) Postings (FBO) — если у продавца схема FBO

### 4.1 Endpoint
- `POST /v3/posting/fbo/list` (или актуальный аналог, если отличается)

### 4.2 Назначение
Аналогично FBS, но для схемы FBO.

### 4.3 Загружаемые таблицы
- `stg_posting`
- `stg_posting_item`
- (опционально) `stg_posting_item_financial`

### 4.4 Примечание
Если клиент работает только по FBS, блок FBO выключается конфигом.

---

## 5) Finance transactions — начисления/удержания/услуги

### 5.1 Endpoint
- `POST /v3/finance/transaction/list`

### 5.2 Назначение
Финансовый реестр операций: продажи, комиссии, логистика, возвратная логистика, услуги, корректировки, компенсации и т.п.

### 5.3 Загружаемые таблицы
- `stg_transaction`
- `stg_transaction_service` (разворот `services[]`)
- `stg_transaction_item` (разворот `items[]`)

### 5.4 Ключевые поля (ожидаемые)
**Операция (transaction):**
- `operation_id`
- `operation_date`
- `operation_type`
- `operation_type_name` (если есть)
- `posting_number` (если есть)
- суммы (набор зависит от операции): `accruals_for_sale`, `sale_commission`, `delivery_charge`, `return_delivery_charge`, `amount`

**Услуги (services[]):**
- `service_name`
- `service_price`

**Товары (items[]):**
- `sku`
- `name` (если есть)

### 5.5 Период выборки
- В одном запросе использовать допустимый период (часто ограничение по месяцу).
- Для диапазона > 1 месяца выполнять несколько запросов и складывать результаты.

### 5.6 Частота обновления
- Ежедневно

---

## 6) Ads — Performance API (расходы на рекламу)

### 6.1 Источник
- Performance API (документация Ozon Performance API)

### 6.2 Назначение
Получение статистики рекламы (минимум: расход по дням; желательно: разрез по кампаниям и/или товарам).

### 6.3 Загружаемые таблицы
- `fact_ads_spend`

### 6.4 Нормализованные поля (целевой набор)
- `date`
- `campaign_id` (если доступно)
- `campaign_name` (если доступно)
- `offer_id`/`sku` (если доступно)
- `spend`
- `impressions` (если доступно)
- `clicks` (если доступно)
- `attributed_orders` (если доступно)
- `attributed_revenue` (если доступно)

### 6.5 Частота обновления
- Ежедневно (при включённой рекламе)

---

## 7) Inventory / Stocks — остатки (опционально)

### 7.1 Источник
- Ozon API остатков (если используется) или выгрузки из кабинета (fallback)

### 7.2 Назначение
Снимок остатков для расчёта “дней до out-of-stock”.

### 7.3 Загружаемые таблицы
- `stg_inventory` (если заводится как staging)
- или напрямую `mart_inventory_snapshot` (если источник очень простой)

### 7.4 Целевые поля
- `snapshot_date`
- `offer_id`/`sku`
- `stock_on_hand`
- `stock_reserved` (если доступно)

### 7.5 Частота обновления
- Ежедневно (если источник доступен)

---

## 8) Соответствие “источники → таблицы → витрины”

### 8.1 Таблицы (dim/stg/fact)
- `dim_product` ← product/list
- `stg_posting`, `stg_posting_item` ← posting fbs/fbo list
- `stg_posting_item_financial` ← posting list (если доступно financial_data) или финансовые отчёты (альтернативный источник)
- `stg_transaction`, `stg_transaction_service`, `stg_transaction_item` ← finance transaction list
- `fact_ads_spend` ← Performance API
- `fact_cogs` ← таблица клиента

### 8.2 Витрины (mart)
- `mart_pnl_daily_by_product` ← postings + posting_financial + cogs + ads (+ transactions для услуг/корректировок)
- `mart_pnl_30d_by_product` ← агрегирование `mart_pnl_daily_by_product`
- `mart_leakage_summary_30d` ← агрегирование `mart_pnl_30d_by_product`
- `mart_ads_daily` ← `fact_ads_spend` (и метрики CPC/ACoS при наличии атрибуции)
- `mart_inventory_snapshot` ← stocks + продажи (avg daily sales) из `mart_pnl_daily_by_product`
- `mart_executive_summary_30d` ← leakage summary + top-N из pnl_30d

---

## 9) Обязательные артефакты для воспроизводимости
- Raw JSON каждого запроса сохраняется с параметрами запроса и временем.
- Для каждого запуска фиксируется `run_id`.
- В staging и mart таблицах фиксируется `loaded_at` (время загрузки).
- Ошибки/аномалии фиксируются в `dq_report`.