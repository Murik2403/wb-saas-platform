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
- [ ] Dependency agent: primary advisories, narrow urllib3 upgrade Linux locks + Windows constraints, resolver/pip-audit/compatibility tests.
- [ ] Headers agent: public/auth/error headers without breaking demo embedding, media/theme/analytics; regression tests.
- [ ] Acceptance agent: FBS price/status/rate limits and SelfBuyouts CRUD; fix material bugs, tests; separate mocks from live evidence.
- [ ] Root: mobile public and cabinet visual acceptance with actual narrow viewport; tabs/theme/menu/overflow.
- [ ] Root: independently review every diff, run combined required suites in separate processes, Graphify AST update, commit scoped files.
- [ ] Root: build immutable candidates, backup/restore/rehearsal, deploy gateway/tenants as necessary, provisioner pin.
- [ ] Root: HTTPS headers, health, FBS latency, mobile browser and preserved course checks; update central closure ledger with user acceptance/pause and live evidence.