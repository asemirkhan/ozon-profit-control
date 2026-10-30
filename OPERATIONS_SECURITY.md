# Эксплуатация и безопасность

**Область:** API, доступы, TLS, deployment, резервное копирование и восстановление. Последовательность релизных ворот — в [WORKFLOWS.md](WORKFLOWS.md).

**Document ID:** PC-OPERATIONS · **Status:** ACTIVE · **Domain:** operations
**Owner:** project_owner · **Effective:** 2026-10-30 · **Revision history:** Git only · **Supersedes:** consolidated legacy guidance

## API, эксплуатация и безопасность

API/страницы — по фактическим роутам `src/api/main.py` и `cabinet/app/api/`; перед изменением сверить реальный контракт в коде. Внешние ключи доступны только серверной среде. Все write-операции обязаны проверять аутентификацию/авторизацию, принадлежность store, marketplace, сущность и даты; audit actor берётся из проверенной identity, а не из payload.

**ТЕКУЩИЙ ДЕФЕКТ:** API изменения не подтверждает пользователя, `updated_by` доверяет клиенту. Не публиковать такие операции в сети/production до авторизации. TLS verification включён всегда; токены/PII не печатать и не коммитить. До staging просмотреть каждый путь и staged diff. GitHub считать публичным, пока приватность не проверена.

Ежедневный поток проходит bootstrap → источники → marts → analytics → snapshot/publish. Работающий контейнер не гарантирует свежесть и полноту. Проверять run/source state, покрытие, cursor, reconciliation, возраст каждого набора, число blocked рекомендаций и атомарность client snapshot.

Повтор должен быть идемпотентным. Не запускать параллельную копию runtime; проверять lock/process. При ошибке не затирать последний хороший snapshot. Backup PostgreSQL считается проверенным только после restore rehearsal. Диагностический режим не подменяет production публикацию.
