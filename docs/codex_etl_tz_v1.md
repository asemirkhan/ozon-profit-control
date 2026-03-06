# ТЗ для Codex — ETL каркас v1 (Ozon Profit Control)

## 1) Цель
Сгенерировать Python-проект, который:
- забирает данные из Ozon Seller API,
- сохраняет raw JSON,
- нормализует в staging таблицы Postgres,
- выполняет data quality checks,
- строит витрины (mart) SQL-скриптами,
- поддерживает несколько клиентов через YAML конфиги.

## 2) Стек и требования
- Python 3.11+
- requests или httpx
- pydantic для валидации/типов
- pandas + pyarrow (для промежуточной работы) допускается
- psycopg / sqlalchemy для Postgres
- структурное логирование (logging)
- конфиги: YAML (pydantic-settings или ruamel/yaml)

## 3) Команды CLI (обязательно)
Реализовать `src/cli.py` с командами:
- `ingest --client <slug> --from YYYY-MM-DD --to YYYY-MM-DD`
- `normalize --client <slug> --from YYYY-MM-DD --to YYYY-MM-DD`
- `load --client <slug> --from YYYY-MM-DD --to YYYY-MM-DD`
- `dq --client <slug> --from YYYY-MM-DD --to YYYY-MM-DD`
- `marts --client <slug> --from YYYY-MM-DD --to YYYY-MM-DD`
- `run --client <slug> --from YYYY-MM-DD --to YYYY-MM-DD` (выполняет всё по порядку)

## 4) Хранение raw JSON
- Путь: `data/raw/<client_slug>/<source>/<YYYY-MM-DD>/<run_id>_<page>.json`
- Для каждого сохранённого файла рядом писать `*.meta.json` с:
  - endpoint name
  - request params
  - timestamp
  - http status
  - pagination info

## 5) Коннекторы Ozon (минимум)
Реализовать в `src/connectors/ozon/`:
- products: list
- postings: fbs list (+ переключатель fbo)
- transactions: finance transaction list
- ads: заглушка интерфейса (реализация может быть позже)

## 6) Normalizers (raw → staging)
Реализовать в `src/normalizers/ozon/` функции, которые принимают raw JSON и возвращают DataFrame/список dict для вставки:
- products → dim_product
- postings → stg_posting
- postings.products[] → stg_posting_item
- postings.financial_data.products[] → stg_posting_item_financial (если присутствует)
- transactions → stg_transaction
- transactions.services[] → stg_transaction_service
- transactions.items[] → stg_transaction_item

## 7) Загрузка в Postgres
- Создать DDL (таблицы) в `src/loaders/ddl.py`
- Использовать upsert по ключам:
  - dim_product: offer_id
  - stg_posting: posting_number
  - stg_transaction: operation_id
  - остальные: составные ключи (описать в DDL и upsert логике)
- Все таблицы должны иметь `loaded_at` timestamp.

## 8) Data Quality
- Реализовать runner + checks по документу `docs/dq_checks_v1.md`
- Выводить результат в таблицу `dq_report`:
  - run_id, client_slug, period_start, period_end, check_id, severity, message, metric_value, threshold, created_at
- Если есть ERROR, команда `run` должна завершаться с кодом != 0.

## 9) Marts
- В `src/marts/sql/` положить SQL файлы витрин:
  - mart_pnl_daily_by_product.sql
  - mart_pnl_30d_by_product.sql
  - mart_leakage_summary_30d.sql
  - mart_ads_daily.sql
  - mart_inventory_snapshot.sql
  - mart_executive_summary_30d.sql
- `build_marts.py` должен последовательно выполнять SQL, создавая таблицы/представления в схеме клиента.

## 10) Конфиги клиентов
- `src/config/clients/<client_slug>.yaml` содержит:
  - client_slug
  - ozon: client_id, api_key (ссылки на env vars допускаются)
  - enabled_sources: postings_fbs, postings_fbo, transactions, products, ads, inventory
  - default_period_days: 30
  - date_policy: created_at | shipped_at (фиксируется)
  - db_schema: <client_slug>

## 11) Критерии готовности
- Проект собирается и запускается.
- `run` сохраняет raw, пишет staging, запускает dq, строит marts.
- Код документирован короткими docstring.
- Есть минимум 5 unit-тестов на нормализацию (explode массивов, типы, ключи).