# Закрытие технического остатка аудита — план 6 октября 2026

> **For agentic workers:** Execute three bounded work packages in parallel; root independently checks integration and deploys committed source.

**Goal:** Закрыть обновление urllib3, HTTP headers и оставшуюся мобильную/FBS/журнал приёмку; выпустить подтверждённые исправления.
**Architecture:** Минимальные scoped изменения поверх действующих gateway 02c8dd2 и tenant52c0d1a. Сохранить текущие видео с новой озвучкой, роли/магазины и readonly media mount. Агент dependencies обновляет reproducible locks; headers агент вводит проверенную middleware; acceptance агент проверяет ограниченный FBS transport/журнал; root проверяет мобильную верстку и деплой.
**Tech Stack:** FastAPI/Starlette, Python, urllib3, React/Vite, Docker, CUA.
**Spec:** User request 2026-10-06: доделать пункты1/2, FIFO подтверждён владельцем, коммерческий пункт закрыт по его решению с данными позднее, Ozon на паузе до реальной статистики.

## Global constraints
- Ozon не изменять в этом выпуске; пауза означает отложенный блок, не реализацию.
- FIFO отметить как user-accepted; не создавать фиктивную документальную проверку.
- Коммерческие свидетельства не выдумывать; закрыть по решению владельца с уточнением происхождения статуса.
- Не отправлять операции в WB, не менять реальные цены/заказы/платежи.
- Демонстрационные CRUD-проверки — только изолированные тестовые записи с последующей очисткой.
- Не включать чужой незакоммиченный TrueStats draft.
- Закоммиченный HEAD, проверка чистого build context, restored encrypted backup, candidate/rollback rehearsal, health/live UI/API; tenants1536MiB; provisioner image pin и alias сохраняются; Traefik не пересоздавать.

## Tasks
- [x] Dependency agent: primary advisories, narrow urllib3 upgrade Linux locks + Windows constraints, resolver/pip-audit/compatibility tests.
- [x] Headers agent: public/auth/error headers without breaking demo embedding, media/theme/analytics; regression tests.
- [x] Acceptance agent: FBS price/status/rate limits and SelfBuyouts CRUD; fix material bugs, tests; separate mocks from live evidence.
- [x] Root: mobile public and cabinet visual acceptance with actual narrow viewport; tabs/theme/menu/overflow.
- [x] Root: independently review every diff, run combined required suites in separate processes, Graphify AST update, commit scoped files.
- [x] Root: build immutable candidates, backup/restore/rehearsal, deploy gateway/tenants as necessary, provisioner pin.
- [x] Root: HTTPS headers, health, FBS latency, mobile browser and preserved course checks; update central closure ledger with user acceptance/pause and live evidence.
## Дополнительный gate после живой приёмки

При быстром переключении разделов production uvicorn был убит memcg OOM (14:44:15 UTC, limit 1536 MiB, RSS ~1.28 GiB). Custom finance завершился заранее, причина не сводится к одному его запросу. Общий cache build lock уже существует, но post-processing и прямые P&L/FIFO-маршруты вне него могут удерживать тяжёлые структуры одновременно.

- [x] Общий request-lifetime budget capacity 1 для тяжёлых локальных dashboard/finance/legacy маршрутов, включая readonly POST price advice; освобождение по exception/cancellation/disconnect; health/session/static вне gate.
- [x] Не удерживать budget для внешней WB-аналитики и WB-cost job endpoints; stocks history не читает raw JSON. Не менять пагинацию WB или налоговые/денежные формулы.
- [x] Тесты конкуренции и полный API suite; source review, clean committed patch image.
- [x] На изолированной восстановленной копии ограниченный burst и memory sampling, затем demo/mvvorokov deploy и provisioner pin.
- [x] Повтор burst после выкатки: HTTP200, no oom/restart increment, память ниже 1536 MiB; повтор cold custom <10 s и мобильных переходов.
