# Ускорение dashboard под нагрузкой — Implementation Plan

> **For agentic workers:** выполнение по независимым задачам агентами с отдельной проверкой результата.

**Goal:** сократить подтверждённую стоимость quality и dashboard без изменения финансовых результатов и повторить приёмку 20 кабинетов на тех же ресурсах.

**Architecture:** исследуются существующие функции `build_dashboard` и `_cost_coverage_diagnostics`. Сохраняются API, shared work guard, кэши и порядок распределения расходов. Кандидат проверяется в отдельном worktree и приватном Docker; production остаётся на прежнем образе.

**Tech Stack:** Python 3.12, Pandas, SQLite, pytest, Docker/cgroup v2.

**Spec:** `C:/Users/mvvor/Downloads/wbsaasplatform/deliveries/shared-host-night-20261014/DASHBOARD_PERFORMANCE_HANDOFF_09e414a.md` и `density-summary-diagnostic-final.json` в той же папке.

## Global Constraints

- Временное окно до 04:00 UTC / 07:00 МСК 14 октября; прекращение новой нагрузки в 03:49:30 UTC.
- Никакого production deployment, перезапуска рабочих контейнеров, provider calls или аренды.
- 12 GiB / 8 CPU, no swap, memory floor 1 GiB, disk floor 20 GiB; admission reservation 384 MiB / fraction 0.7.
- Semaphore(1), 20 s timeout/SLO, overview TTL 20 s и heavy cache TTL/entry/byte budgets неизменны.
- Operational FIFO guard свежий; неизвестная себестоимость не становится нулём. Исходные финансовые формулы, date semantics и общие allocation weights сохраняются.
- Сборка только после tests/review/commit; Docker context содержит только закоммиченные изменённые Python-модули. UI assets наследуются от неизменного исходного image pin.

## Task 1: Подтвердить hotspot и точную эквивалентность quality

**Files:** `tenant-app/ui_helpers.py`, целевые quality tests в `tenant-app/tests/`. Другие product files этому агенту не принадлежат.

- [ ] Профилировать неизменный `_cost_coverage_diagnostics` на fixture benchmark 365 дней. Сохранить cProfile names/calls/durations, без содержимого клиентских данных.
- [ ] Проверить старый и новый результат на одинаковых DataFrames: catalog → orders → sales precedence, first nonempty, неизвестные/некорректные ID, numeric-only row coercion, absent columns, исторический SKU вне каталога, нулевые/отсутствующие ставки.
- [ ] Заменить только подтверждённую дорогую операцию обхода identity rows, сохранив исходный порядок/типы и формулы. Не ограничивать активные SKU только каталогом.
- [ ] Повторить equality/profile и существующие quality/FIFO/concurrency tests. Пример контракта equality:

```python
assert original_summary == candidate_summary
pd.testing.assert_frame_equal(original_diagnostics, candidate_diagnostics, check_exact=True)
```

## Task 2: Подтвердить hotspot и точную эквивалентность dashboard

**Files:** `tenant-app/calculations.py`, целевые projection/financial tests в `tenant-app/tests/`. `api_server.py` остаётся у основного агента; новый TTL для economics не добавляется.

- [ ] Профилировать `build_dashboard` и `get_management_economics` с точным запросом benchmark и тем же fixture; разделить количество SQL reads, FIFO и повторных DataFrame scans.
- [ ] Выбрать одну измеренную дорогую операцию. Для per-SKU selection допустимы заранее вычисленные позиции строк, если сохраняются raw key equality, исходный порядок, dtype и операции суммирования.
- [ ] Проверить целиком исходный и новый DashboardData, source_row/coverage, technical allocation weights, returns, missing COGS и manual-expense intersections. Изменение только display totals без source context не является эквивалентностью.
- [ ] Повторить cProfile и целевые economics/projection tests; если измеренный эффект мал или контракт нельзя сохранить, оставить этот модуль неизменным и задокументировать вывод.

## Task 3: Независимая проверка и кандидат из commit

- [ ] Основной агент проверяет изменения, запускает существующие полные tenant-app и tenant-api suites:

```powershell
python -m pytest tenant-app/tests -q
python -m pytest mh-redesign-lab/tenant-api/tests -q
```

- [ ] Сопоставить исходный и кандидат на восстановленных копиях данных, если подготовлены аутентифицированные private inputs. Не сохранять суммы или строки в акте: только equality/hashes/counts/timings.
- [ ] Сделать commit после прохождения проверок. Зафиксировать полный SHA и modified-module hashes; собрать overlay образ из immutable исходного tenant image с сохранением UI assets.
- [ ] Выполнить тот же benchmark из `0239f88` на candidate image и тех же 20 кабинетах, сохраняя исходный FAILED и все отрицательные попытки. Все четыре фазы, completed counts, overlap, resource/health/cleanup обязательны.
- [ ] Kernel peak отсутствует: даже при функциональном успехе formal capacity остаётся unproven. 25/30 нельзя запускать в обход admission; отказ не записывается как успешный тест.
- [ ] После остановки подтвердить отсутствие всех own objects/guard units и состояние рабочих сервисов; обновить audit с baseline/candidate отдельно. Production deployment этим планом не выполняется.
