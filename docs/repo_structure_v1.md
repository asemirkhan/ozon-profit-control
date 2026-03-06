# Repo Structure v1 — Ozon Profit Control

## 1) Цели структуры
- Понятное разделение: коннекторы API, хранение raw, нормализация, загрузка, витрины, проверки качества.
- Поддержка нескольких клиентов через конфиги.
- Возможность повторного запуска и воспроизводимости.
- Минимизация ошибок через типизацию, валидацию, тесты, логирование.

## 2) Дерево проекта (рекомендуемое)
ozon-profit-control/
├── docs/
│   ├── product_spec_v1.md
│   ├── data_contract_v1.md
│   ├── etl_blueprint_v1.md
│   ├── api_endpoints_ozon_v1.md
│   ├── repo_structure_v1.md
│   ├── dq_checks_v1.md
│   └── glossary.md
├── src/
│   ├── cli.py
│   ├── config/
│   │   ├── config_schema.yaml
│   │   └── clients/
│   │       ├── client_demo.yaml
│   │       └── (client_*.yaml)
│   ├── connectors/
│   │   └── ozon/
│   │       ├── auth.py
│   │       ├── products.py
│   │       ├── postings.py
│   │       ├── transactions.py
│   │       └── ads.py
│   ├── raw_store/
│   │   ├── paths.py
│   │   └── writer.py
│   ├── normalizers/
│   │   └── ozon/
│   │       ├── products_normalizer.py
│   │       ├── postings_normalizer.py
│   │       ├── transactions_normalizer.py
│   │       └── ads_normalizer.py
│   ├── loaders/
│   │   ├── db.py
│   │   ├── ddl.py
│   │   └── upsert.py
│   ├── quality/
│   │   ├── dq_runner.py
│   │   └── checks_v1.py
│   ├── marts/
│   │   ├── build_marts.py
│   │   └── sql/
│   │       ├── mart_pnl_daily_by_product.sql
│   │       ├── mart_pnl_30d_by_product.sql
│   │       ├── mart_leakage_summary_30d.sql
│   │       ├── mart_ads_daily.sql
│   │       ├── mart_inventory_snapshot.sql
│   │       └── mart_executive_summary_30d.sql
│   ├── utils/
│   │   ├── logging.py
│   │   ├── dates.py
│   │   ├── http.py
│   │   └── types.py
│   └── models/
│       ├── raw_models.py
│       └── staging_models.py
├── data/
│   └── raw/                      (НЕ коммитить в git)
├── templates/
│   ├── cogs_template.csv
│   └── report_template.md
├── docker/
│   └── docker-compose.yml
├── .gitignore
├── pyproject.toml
└── README.md

## 3) Главные правила
- В git не попадают: data/raw, ключи, пароли, клиентские файлы.
- Все параметры на клиента лежат в YAML конфиге (client_slug, какие источники включены, период).
- Каждый запуск ETL имеет run_id и пишет логи.
- Raw JSON сохраняется всегда.
- Power BI читает только mart_ таблицы (или представления).