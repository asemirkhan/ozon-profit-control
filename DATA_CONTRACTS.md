# Контракты данных и идентичность

**Область:** зерно, ключи, lineage, нормализация и связи объектов. Реестр активных документов находится в [PROJECT.md](PROJECT.md).

**Document ID:** PC-CONTRACTS · **Status:** ACTIVE · **Domain:** data_contracts
**Owner:** project_owner · **Effective:** 2026-10-30 · **Revision history:** Git only · **Supersedes:** consolidated legacy guidance

## Модель данных и идентичность

Каждый слой сохраняет grain, ключ, бизнес-время, время получения, marketplace, источник и lineage. Raw неизменяемо хранит полученное (с безопасной редактировкой секретов); staging сохраняет структуру источника; нормализованный слой сохраняет типы и явные ключи; marts содержат расчёты с версией правил; API/UI отображают snapshot и его качество.

Текущие основные grains:
- товар — marketplace + source product identity;
- продажа/возврат — строка события/операции источника;
- заказ/отмена — строка или статусное событие заказа;
- финансы — строка финансового отчёта/операция с ID источника;
- реклама — дата × кампания × доступные товарные/placement dimensions;
- остаток — снимок × товар × склад × тип доступности;
- наблюдения карточки/конкурента — объект × фактическое время наблюдения.

Основные WB staging-контракты: `stg_wb_product` — source identity/raw update time; `stg_wb_sale` — sale_date + исходный тип/знак; `stg_wb_order` — order_date, заказ не равен продаже; `stg_wb_finance` — operation_date + rrdid/type/source amount/sign; `stg_wb_ads` — date + campaign и реально полученные товарные dimensions; `stg_wb_stock` — snapshot_date + product/warehouse/availability; `stg_wb_paid_storage` — report_date + detail; `stg_wb_competitor_snapshot`/`stg_wb_product_card_snapshot` — captured_at, точечный снимок; `fact_wb_competitor_observation` — конкурент × свой товар × query × дата; `fact_wb_product_card_observation` — товар × фактический день наблюдения. Точные constraints сверять с DDL и upsert перед изменением.

Эти grains сверять с реальным DDL и API; название таблицы не является доказательством. Канонический товар соединяется только через однозначную bridge-связь marketplace-specific ID. Похожие артикулы сами по себе не ключ. Для каждого join измерять matched/unmatched/ambiguous, потерю/размножение строк и сумму до/после. Не использовать технический placeholder как товар. Unknown остаётся отдельным состоянием.

Контракт денежных фактов и правила их соединения с экономикой определены в [AGGREGATION.md](AGGREGATION.md).
