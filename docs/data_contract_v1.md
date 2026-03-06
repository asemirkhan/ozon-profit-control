1) Основные принципы

1. Мы храним данные в 3 слоя:
- raw: сырые ответы API (JSON) без изменений
- staging: нормализованные таблицы “как в API, но в табличном виде”
- mart: витрины для аналитики и Power BI

2. Главные ключи, которые позволяют “склеить” всё:
- posting_number — ключ отправления (заказа/отгрузки)
- sku и/или offer_id — ключ товара
- operation_id — ключ финансовой операции (транзакции)

3. Главный бизнес-ключ для клиента: offer_id (артикул продавца).
Технический ключ, который часто встречается в финансах: sku.

2) Entities (таблицы) — v1

2.1 dim_product (справочник товаров)
Зачем: связать offer_id ↔ product_id ↔ sku, чтобы всё сходилось в одной модели.

Primary key (логический): offer_id
Unique keys: product_id, sku (могут быть не всегда уникальны без контекста, но в рамках магазина обычно ок)

Поля:
- offer_id (string, required) — артикул продавца
- product_id (int/string, optional) — внутренний id Ozon
- sku (int/string, optional) — SKU id Ozon
- name (string, optional)
- brand (string, optional)
- category_id (string/int, optional)
- category_name (string, optional)
- archived (bool, optional)
- updated_at (datetime, required)

Источник:
- Ozon Seller API: список товаров/продуктов (конкретный endpoint уточним и закрепим на шаге 3, когда будем писать коннектор)

2.2 stg_posting - таблица “отправлений/заказов”
Зачем: даты, статусы, схема доставки, склад. Это нужно для срезов по времени и для объяснения проблем (отмены/доставка).

Primary key: posting_number

Поля:
- posting_number (string, required) - номер отправления. Это “номер посылки/заказа”, по нему всё связывается.
- status (string, required) - статус: создан/в пути/доставлен/отменён (названия зависят от API).
- created_at (datetime, required) - когда заказ появился.
- in_process_at (datetime, optional)
- shipped_at (datetime, optional)
- delivered_at (datetime, optional)
- cancelled_at (datetime, optional)
- delivery_schema (string, optional) — FBS/FBO и т.п. модель доставки (условно FBS/FBO): влияет на расходы и процессы.
- warehouse_id (string/int, optional) - какой склад участвует (если есть).
- customer_region (string, optional) - полезно для логистики/анализа (если доступно).
- is_multibox (bool, optional)
- raw_updated_at (datetime, required) — когда мы это забрали из API

Источник:
Ozon Seller API: postings list (FBS и/или FBO)

2.3 stg_posting_item (товарные позиции внутри отправления)
Зачем: количество и цены на уровне “товар в отправлении”.

Primary key (составной):

posting_number + offer_id + sku (или posting_number + product_id)

Поля:
- posting_number (string, required)
- offer_id (string, optional) — иногда в API есть, иногда надо маппить
- sku (string/int, optional)
- product_id (string/int, optional)
- quantity (int, required) - сколько штук этого товара в заказе.
- item_price (numeric, optional) — цена в заказе
- currency (string, optional)
- raw_updated_at (datetime, required) - для контроля обновлений.

Источник:
Ozon Seller API: postings list (products[])

2.4 stg_posting_item_financial (финансы по товарам внутри отправления)
Зачем: быстрый расчёт payout/комиссий/скидок на уровне товара.

Primary key (составной):

posting_number + product_id (или posting_number + sku) + line_idx

Поля (MVP):
- posting_number (string, required)
- product_id (string/int, required) - какой товар.
- sku (string/int, optional)
- quantity (int, required) - сколько штук.
- price (numeric, optional) - текущая цена
- old_price (numeric, optional) - “старая” цена (часто помогает понять скидки).
- total_discount_value (numeric, optional) - скидка (если Ozon даёт в явном виде).
- total_discount_percent (numeric, optional) - скидка (если Ozon даёт в явном виде).
- commission_amount (numeric, optional) - комиссия Ozon на эту позицию.
- commission_percent (numeric, optional)
- payout (numeric, optional) — ключевая метрика “сколько осталось продавцу по этой позиции”
- item_services_total (numeric, optional) — если можно агрегировать сервисы по позиции
- raw_updated_at (datetime, required)

Источник:
postings list с включённым financial_data

2.5 stg_transaction (финансовые операции / транзакции)
Зачем: добрать платные услуги/корректировки/возвратные логистики и сверять “к выплате”.

Primary key: operation_id

Поля (MVP):
- operation_id (string/int, required) - уникальный номер операции
- operation_date (datetime, required) - дата операции.
- operation_type (string, required) - тип: продажа, услуга, корректировка и т.д.
- operation_type_name (string, optional) - тип: продажа, услуга, корректировка и т.д.
- posting_number (string, optional) - если операция относится к конкретной посылке (не всегда есть).
- accruals_for_sale (numeric, optional) - начислено за продажу (если это операция продажи).
- sale_commission (numeric, optional) - комиссия в рамках операции (если выделена).
- delivery_charge (numeric, optional) логистика продажи.
- return_delivery_charge (numeric, optional) - логистика возврата.
- amount (numeric, optional) — итог по операции (если есть)
- raw_updated_at (datetime, required)

Источник:
Finance transaction list v3

2.6 stg_transaction_item (товары внутри транзакции)
Зачем: связать транзакции с товарами (если Ozon отдаёт items[] со sku).

Primary key (составной):

operation_id + sku + line_idx

Поля:
- operation_id (string/int, required)
- sku (string/int, required)
- item_name (string, optional)
- raw_updated_at (datetime, required)

Источник:
finance transaction list v3 (items[])

2.7 stg_transaction_service (услуги внутри транзакции)
Зачем: разложить платные услуги по типам (хранение/логистика/прочее).

Primary key (составной):

operation_id + service_name + line_idx

Поля:
- operation_id (string/int, required) - какая операция.
- service_name (string, required) - название услуги (по нему группируешь).
- service_price (numeric, required) - сколько списали/начислили.
- raw_updated_at (datetime, required) - контроль.

Источник:
finance transaction list v3 (services[])

2.8 fact_cogs (себестоимость от клиента)
Зачем: без себестоимости нет управленческой прибыли.

Primary key: offer_id (или sku, но лучше offer_id)

Поля:
- offer_id (string, required) - артикул продавца.
- unit_cogs (numeric, required) — себестоимость 1 шт
- packaging_cost (numeric, optional)
- inbound_shipping_cost (numeric, optional)
- other_variable_cost (numeric, optional)
- effective_from (date, optional) - если себестоимость менялась во времени.
- effective_to (date, optional) - если себестоимость менялась во времени.
- updated_at (datetime, required)

Источник:
таблица клиента (шаблон) — Excel/Google Sheet

2.9 fact_ads_spend (реклама)
Зачем: прибыль после рекламы и пороги безубыточности.

Primary key (составной):

date + campaign_id + (offer_id/sku)

Поля (MVP):
- date (date, required)
- campaign_id (string, optional)
- offer_id (string, optional)
- sku (string/int, optional)
- spend (numeric, required)
- impressions (int, optional)
- clicks (int, optional)
- attributed_orders (int, optional)
- attributed_revenue (numeric, optional)
- raw_updated_at (datetime, required)

Источник:
Ozon Performance API / выгрузки рекламы

3) Витрины (mart) — что нужно для Power BI v1
3.1 mart_pnl_daily_by_product

Это сердце продукта. Она отвечает на вопросы:
- “В какие дни магазин реально зарабатывал, а в какие — терял?”
- “Какие товары сегодня/на этой неделе ушли в минус?”
- “Почему прибыль упала: реклама, логистика, комиссия, возвраты, себестоимость?”

Grain (зерно):
1 строка = 1 день × 1 товар (товар = offer_id или sku, лучше offer_id как “человеческий” ключ).

Поля и расшифровка
Идентификаторы
- date — день, к которому мы относим продажи/затраты (важно: это выбранная “политика даты”, обычно дата создания/отгрузки отправления).
- offer_id — артикул продавца (то, что клиент узнаёт).
- sku — ID товара в Ozon (нужен для связки с финансами/транзакциями).
- product_name — название товара (для читаемости в BI).
- category_name (опционально) — категория, чтобы смотреть прибыль по категориям.

Объём продаж
- units_sold — сколько штук этого товара продали в этот день.
- orders_count — сколько заказов/отправлений содержали этот товар в этот день (полезно, если хочешь средний чек/структуру корзины).
- revenue_gross — “грязная” выручка: цена × количество (до учёта некоторых удержаний/корректировок). Иногда это “витринная” цифра, но она нужна для долей и маржи.
- discount_total — суммарные скидки/уменьшения цены по данным Ozon (если доступны на уровне posting financial).

Деньги, которые реально “оставил Ozon”
- payout — ключевое: сколько денег по факту остаётся продавцу по этому товару за этот день по данным финансовой части отправлений (часто это “после комиссии и части логистики” — зависит от конкретных полей Ozon).
- commission_amount — сколько удержано комиссией за этот товар в этот день.
- logistics_amount — сколько удержано логистикой/доставкой (если доступно в financial_data или рассчитано через транзакции).
- services_amount — прочие услуги (упаковка, хранение, платные сервисы), если их можно разложить на товар/день.
- returns_amount — “стоимость возвратов”/корректировки возвратов, если их можно корректно отнести на товар (иногда это отдельные операции в транзакциях).

Себестоимость и реклама
- cogs_amount — себестоимость проданных штук: units_sold × unit_cogs (из таблицы клиента).
- ads_spend — расходы на рекламу, отнесённые к этому товару в этот день (если есть товарный разрез; если нет — будет пусто/0 или распределение по правилу).

Прибыльность (то, ради чего всё)
- profit_before_ads — прибыль до рекламы. Простыми словами: “сколько заработали на товаре, если не считать рекламу”.
- profit_after_ads — прибыль после рекламы. Это главный KPI для управления рекламой.
- margin_after_ads — маржа после рекламы: profit_after_ads / revenue_gross (в процентах). Нужна, чтобы сравнивать товары с разным оборотом.
- profit_per_unit_after_ads — прибыль на 1 штуку после рекламы (очень понятная метрика для селлера).

Как считается (простая логика)
1. Берём продажи/количество из stg_posting_item.
2. Берём финансовую часть по этим же posting’ам из stg_posting_item_financial (payout, комиссии, скидки).
3. Подтягиваем себестоимость по offer_id из fact_cogs.
4. Подтягиваем рекламу по date + offer_id/sku из fact_ads_spend.
5. Если часть услуг/корректировок не лежит в posting financial — “досыпаем” из транзакций (через stg_transaction_service и stg_transaction_item), но только там, где можно честно сопоставить.

Типовые тонкости

payout у Ozon может быть уже “после комиссии” и частично “после логистики”. Поэтому ты всегда хранишь и payout, и отдельные компоненты, чтобы не было двойного вычитания.

Возвраты часто “живут” во времени со сдвигом: продажа была вчера, возврат — через неделю. Поэтому в daily-витрине возвраты могут выглядеть “странно”. Это нормально, если ты честно объясняешь политику учета (и именно поэтому нужна периодная витрина 30d).

3.2 mart_pnl_30d_by_product
Зачем нужна:
Это “таблица, ради которой платят”. В ней клиент открывает и сразу видит:
- топ прибыльных товаров,
- топ убыточных товаров,
- товары с большим оборотом, но низкой прибылью (самый частый инсайт),
- товары, где реклама съела всю маржу.

Grain:
1 строка = 1 товар за период (обычно 30 дней).

Поля (почти те же, но агрегированные)
- period_start, period_end — границы периода (например, последние 30 дней).
- offer_id, sku, product_name, category_name
- units_sold
- orders_count
- revenue_gross
- discount_total
- payout
- commission_amount
- logistics_amount
- services_amount
- returns_amount
- cogs_amount
- ads_spend
- profit_before_ads
- profit_after_ads
- margin_after_ads
- profit_per_unit_after_ads

Как считается
Просто суммирование mart_pnl_daily_by_product за период.

Тонкости:
Эта витрина сглаживает “скачки” из-за возвратов/корректировок, поэтому для управленческих решений обычно важнее, чем daily.

3.3 mart_leakage_summary_30d (витрина “утечки денег” без шума)
Зачем нужна
Это экран для собственника: “куда уходят деньги в целом”.
Она отвечает на вопрос: “Почему прибыль маленькая при хорошем обороте?”

Grain:
1 строка = 1 период (30 дней) × 1 магазин (и опционально — разрез по категории).

Ты можешь сделать две версии:
- без категории (вообще одна строка на магазин)
- с категорией (по строке на каждую категорию)

Поля и расшифровка
- period_start, period_end
- revenue_gross_total — суммарная выручка (по всем товарам).
- payout_total — суммарно “сколько оставил Ozon”.
- commission_total — суммарные комиссии.
- logistics_total — суммарная логистика.
- services_total — суммарные услуги/хранение/штрафы (то, что уходит не в комиссию и не в доставку).
- returns_total — суммарные потери на возвратах/корректировках (если считаем).
- cogs_total — суммарная себестоимость проданного.
- ads_total — суммарные расходы на рекламу.
- profit_before_ads_total
- profit_after_ads_total
- margin_after_ads_total

Поля долей (очень полезно)
- commission_share = commission_total / revenue_gross_total
- logistics_share = logistics_total / revenue_gross_total
- services_share = services_total / revenue_gross_total
- ads_share = ads_total / revenue_gross_total
Это то, что селлер понимает мгновенно: “реклама съедает 18% оборота”.

Как считается:
Агрегация mart_pnl_30d_by_product (сумма всех компонент) и, если нужно, группировка по category.

3.4 mart_ads_daily (витрина рекламы для нормального анализа, отдельно от P&L)
Зачем нужна:
Если ты будешь держать рекламу “внутри общей P&L таблицы”, ты быстро упрёшься:
- слишком много кампаний,
- нужно смотреть клики/показы/CPC,
- нужно сравнивать кампании между собой.

Эта витрина делает рекламу самостоятельной сущностью.

Grain:
В идеале: 1 день × 1 кампания × 1 товар (если Ozon даёт товарный разрез).
Если товарного разреза нет — тогда: 1 день × 1 кампания.

Поля и расшифровка
- date
- campaign_id
- campaign_name (если доступно)
- offer_id / sku (если реклама товарная)
- impressions — показы
- clicks — клики
- spend — расходы
- cpc — цена клика = spend / clicks
- attributed_orders — заказы, которые система атрибутировала рекламе (если доступно)
- attributed_revenue — выручка от атрибутированных заказов (если доступно)
- acos — доля расходов на рекламу = spend / attributed_revenue (если revenue доступна)
- raw_updated_at

Как считается

Почти напрямую из fact_ads_spend, только приводишь к единому зерну и добавляешь вычисляемые метрики (cpc, acos).

Тонкости

Атрибуция может быть неполной/с лагом. Поэтому в v1 ты не обещаешь идеальную атрибуцию, но даёшь качественный контроль расходов и трендов.

3.5 mart_inventory_snapshot (остатки и риск “закончится товар”)
Зачем нужна:
Очень часто прибыль теряют так:
товар закончился → карточка упала в выдаче → потом его “реанимируют” рекламой за деньги.

Эта витрина нужна для контроля “дней до out-of-stock” и рекомендаций по дозаказу (пока без ML).

Grain:
Есть два варианта, выбирай один:

Вариант A (проще и нормально для v1)
1 строка = 1 товар × “текущая дата/последний снимок”
Это snapshot “как сейчас”.

Вариант B (лучше для истории)
1 строка = 1 товар × 1 день
Тогда ты видишь динамику запасов.

Поля и расшифровка
- snapshot_date — дата снимка остатков
- offer_id, sku, product_name
- stock_on_hand — сколько штук сейчас на складе (доступно к продаже)
- stock_reserved (если доступно) — сколько зарезервировано под заказы
- stock_available — доступно = on_hand - reserved (если есть reserved)
- avg_daily_sales_7d — средние продажи в день за последние 7 дней (из mart_pnl_daily_by_product)
- avg_daily_sales_30d — средние продажи в день за 30 дней
- days_of_stock_7d — на сколько дней хватит при текущих продажах (7d) = stock_available / avg_daily_sales_7d
- days_of_stock_30d — аналогично по 30d
- reorder_flag — флажок “пора заказывать” (например, если days_of_stock < 10)

Как считается:
- Остатки берёшь из источника остатков (API/выгрузка).
- Продажи (avg_daily_sales) берёшь из mart_pnl_daily_by_product.
- Делишь остаток на продажи и получаешь “на сколько дней хватит”.

3.6 mart_executive_summary_30d (одна строка для “панели собственника” и бота)
Зачем нужна:
Когда у тебя появится Telegram-бот или просто нужен верхнеуровневый показатель, удобно иметь одну компактную таблицу, где:
- оборот
- прибыль
- реклама
- возвраты
- доли расходов
- топ-3 прибыльных и топ-3 убыточных товара (можно ссылками)

Это резко повышает “продуктовость” и упрощает автоматизацию.

Grain:
1 строка = 30 дней × магазин.

Поля
- period_start, period_end
- revenue_gross_total
- payout_total
- profit_before_ads_total
- ads_total
- profit_after_ads_total
- margin_after_ads_total
- commission_share, logistics_share, services_share, ads_share
- top_profit_products (например JSON/текст: offer_id:profit)
- top_loss_products (аналогично)

Как считается

Агрегируешь mart_leakage_summary_30d + подтягиваешь top-N из mart_pnl_30d_by_product.

4) Политика дат (важно!)
В v1 используем две логики:
“операционная аналитика” — по дате создания/отгрузки posting (выбирается и фиксируется)
“финансовая сверка” — по operation_date транзакций

5) Data Quality Checks v1 (минимум)
- В stg_posting_item нет строк с quantity <= 0
- posting_number уникален в stg_posting
- Для каждого posting_number есть хотя бы один item
- В fact_cogs покрыто хотя бы 80% продаж по offer_id (иначе прибыль неполная)
- payout не отрицательный (если отрицательный — помечаем как anomaly)
- Сверка: сумма payout по postings за период примерно бьётся с суммой релевантных транзакций (с допуском)