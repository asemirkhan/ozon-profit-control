# Profit Control — обзор и реестр знаний

**Document ID:** PC-PROJECT · **Status:** ACTIVE · **Domain:** overview
**Owner:** project_owner · **Effective:** 2026-10-30 · **Revision history:** Git only · **Supersedes:** consolidated legacy guidance

**Обновлено:** 2026-10-30. **Владелец продуктовых и бизнес-решений:** пользователь проекта.

Этот файл отвечает за назначение, архитектуру, реестр нормативных инструкций и общие решения проекта. Подробные процессы живут в профильных файлах ниже: каждому процессу соответствует один активный документ. Не копируй его правила в `PROJECT.md` или другие инструкции.

## Назначение

Profit Control собирает данные магазинов на Wildberries и Ozon, сохраняет происхождение фактов, сверяет продажи и деньги, вычисляет проверяемые показатели и показывает продавцу подтверждённые сигналы и безопасные действия. В проекте есть Python ingestion/normalization/loaders, PostgreSQL, SQL-витрины, аналитические сборщики и Next.js-кабинет.

Система не выдаёт корреляцию за причину, оценку за факт, отсутствие данных за ноль, незакрытый источник за полный период или синтетический пример за результат магазина. Если данные, методика или связь не подтверждены — вывод ограничивается фактом либо блокируется.

## Приоритет источников истины

1. Последнее явное решение владельца проекта.
2. Один активный профильный документ из реестра ниже.
3. Код и тесты описывают фактическое поведение, но сами по себе не утверждают бизнес-методику.
4. Архивы, экспорты, старые отчёты и имена `v1/final/canonical` не являются источниками инструкций.

Статусы правил: **УТВЕРЖДЕНО**, **ОБЩИЙ КОНТРОЛЬ**, **ОТКРЫТО**, **ТЕКУЩИЙ ДЕФЕКТ**, **НЕ ПРОВЕРЕНО**. При конфликте двух активных источников или отсутствии правила блокируй только зависимую часть; укажи пробел и продолжай независимую работу.

## Реестр активных инструкций

| Document ID | Файл | Домен | Статус | Владелец нормы |
|---|---|---|---|---|
| PC-PROJECT | [PROJECT.md](PROJECT.md) | overview | ACTIVE | project_owner |
| PC-AGENTS | [AGENTS.md](AGENTS.md) | agents | ACTIVE | project_owner |
| PC-INGESTION | [INGESTION.md](INGESTION.md) | ingestion | ACTIVE | project_owner |
| PC-CONTRACTS | [DATA_CONTRACTS.md](DATA_CONTRACTS.md) | data_contracts | ACTIVE | project_owner |
| PC-AGGREGATION | [AGGREGATION.md](AGGREGATION.md) | aggregation | ACTIVE | project_owner |
| PC-ANALYSIS | [ANALYSIS.md](ANALYSIS.md) | analysis | ACTIVE | project_owner |
| PC-SIGNALS | [SIGNALS_RECOMMENDATIONS.md](SIGNALS_RECOMMENDATIONS.md) | signals | ACTIVE | project_owner |
| PC-OPERATIONS | [OPERATIONS_SECURITY.md](OPERATIONS_SECURITY.md) | operations | ACTIVE | project_owner |
| PC-WORKFLOWS | [WORKFLOWS.md](WORKFLOWS.md) | workflows | ACTIVE | project_owner |
Каждому домену соответствует ровно один документ со статусом `ACTIVE`. Валидатор проверяет уникальный ID, активный статус, владельца, домен и физическое наличие файла. При замене документа сначала одним изменением обновляются этот реестр и ссылки; предыдущая норма удаляется из рабочей системы. История изменений живёт только в Git, ручные версии не ведутся.

## Карта продукта и кода

Основной путь: API площадки → сохранённый raw-ответ и audit → нормализованные факты → PostgreSQL → marts/сверки → аналитические сигналы → рекомендации → клиентские данные → кабинет/API.

- Runtime: `scripts/run_profit_control_canonical_runtime_v1.py`.
- Общий ingestion: `src/ingestion/`; адаптеры WB/Ozon: `src/connectors/`; преобразование: `src/normalizers/`; загрузка: `src/loaders/`.
- DDL: `src/loaders/ddl.sql`; расчёт витрин: `src/marts/`; API: `src/api/main.py`.
- Движок рекомендаций: `src/services/recommendation_engine_v2.py`; сбор клиентских наборов: scripts-раннеры.
- Кабинет: `cabinet/`; публичный сайт: `web/`.
- Кабинет использует несколько JSON/CSV наборов; единая атомарная публикация всех наборов под общим snapshot пока не доказана.

Landing в `web/`: `npm ci`, `npm run dev`; production build: `npm run build`. Vercel Root Directory — `web`. Публичные переменные `NEXT_PUBLIC_*` не предназначены для секретов.

## Текущий статус и открытые решения

- Действующего WB-токена сейчас нет. Замер live catch-up и скорость API — **BLOCKED** до появления credentials; токены не отправлять в чат и не включать в Git.
- Финансовые знаки, кросс-marketplace глоссарий, некоторые категории/атрибуции и пороги — **ОТКРЫТО/НЕ ПРОВЕРЕНО**. См. [AGGREGATION.md](AGGREGATION.md).
- Аутентификация write API не подтверждена; до проверки endpoint нельзя публиковать во внешнюю сеть. См. [OPERATIONS_SECURITY.md](OPERATIONS_SECURITY.md).
- Полнота рекомендаций и общий client snapshot не подтверждены; текущие builder-флаги сами по себе не являются доказательством. См. [SIGNALS_RECOMMENDATIONS.md](SIGNALS_RECOMMENDATIONS.md).

Очередь работ и критерии снятия блокировок находятся только в [WORKFLOWS.md](WORKFLOWS.md). Git — единственная история версий. Секреты, реальные выгрузки и локальные данные не включаются в коммиты.
