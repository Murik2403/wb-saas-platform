# Проверенная миграция control-plane — 13 октября 2026

Исправлен оставшийся пункт старого аудита DeepSeek: перенос SQLite в PostgreSQL
раньше выводил число строк источника, а содержимое назначения не проверял.
`ON CONFLICT DO NOTHING` скрывал несовпадающие строки; таблицы без PK при
повторе дублировались. Текущая PostgreSQL-схема с `GENERATED ALWAYS` также
отказывала в переносе явных ID. Это операторский скрипт, не переключение
рабочей базы или изменение результатов кабинетов.

## Реализация

- Источник открывается `mode=ro` и читается в одной snapshot-транзакции.
  Отсутствующий файл не создаётся. Циклические зависимости отклоняются.
- Все таблицы назначения блокируются до окончания транзакции; lock timeout
  ограничен 10 секундами. Проводить cutover следует при остановленных writers.
- Количество и содержимое проверяются как мультимножество всех строк,
  включая multiplicity дубликатов и NULL/BLOB. Лишние и изменённые строки
  откатывают весь перенос, вместо исправления/удаления данных по предположению.
- Без PK непустая таблица принимается только при точном совпадении;
  идентичный повтор не создаёт дубликаты.
- Явные ID сохраняются через `OVERRIDING SYSTEM VALUE`; sequence продвигаются
  транзакционным `ALTER SEQUENCE RESTART`, без отката ранее занятой позиции.
- После всех INSERT и ALTER SEQUENCE выполняется `SET CONSTRAINTS ALL IMMEDIATE`,
  затем повторная сверка всех таблиц. Так последующий или отложенный trigger
  не может незаметно изменить уже проверенную таблицу до commit.
- CLI скрывает содержимое driver exceptions: секреты соединения и значения
  строк не выводятся. Сообщение об успехе появляется только после commit.

## Проверки

На настоящем одноразовом локальном PostgreSQL 16:

- До исправления: **9 failed / 1 passed**, включая ложный успех при конфликте,
  тихое изменение/пропуск строк trigger, дублирование keyless-таблицы,
  identity import и нестабильный source при конкурентной записи.
- После независимого review: **15 passed**, 11,34 s. Проверена полная текущая
  control-plane схема, точный повтор, FK/составной PK/quoted identifiers,
  BLOB/NULL, первый новый ID, неизменность исходного файла и атомарный rollback.
- Review отдельно воспроизвёл ложный успех deferred constraint trigger и
  sequence DDL event trigger. После изменения порядка операций:
  **2 independent regressions passed**, 5,45 s.
- Проверка CLI с подставным exception, содержащим секрет, подтвердила отсутствие
  значения в stdout/stderr и безопасное сообщение об отказе.

Команда: `py -3.12 -m pytest scripts/tests/test_migrate_sqlite_to_postgres.py -q`.
Нужны gateway locked dependencies и локальный Docker. Скрипт не использовал
рабочий PostgreSQL, аккаунты клиентов или внешние провайдеры.

`DEPLOY.md` исправлен: сначала offline schema/backup/transfer/verification,
затем переключение публичного gateway. После новых PostgreSQL-записей простой
откат к старому SQLite не считается безопасным без сверки новых данных.

Официальный контракт:
[INSERT / identity](https://www.postgresql.org/docs/16/sql-insert.html),
[transactional sequence restart](https://www.postgresql.org/docs/16/sql-altersequence.html).
Production остаётся на прежнем backend: реальная миграция сейчас не запускалась.

Evidence: `deliveries/audit-gates-20261013/migration-{red,reviewed-final}.log`
и `deliveries/restart-recovery-20261013/MIGRATION_REVIEW.md`.
