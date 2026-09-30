# Актуальные источники и границы возможностей Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. В данной сессии исполняет назначенный агент; дополнительные агенты не создаются.

**Goal:** Исправить три устаревших ограничения страницы возможностей по уже выпущенному коду.

**Architecture:** Только серверный HTML шаблон capabilities. ДДС описывает счета и канонический CSV с предпросмотром; общие складские операции требуют явных соответствий магазина; Ozon разделяет raw API и ручной документальный FIFO. Формулы, разрешения, API и данные не меняются.

**Tech Stack:** FastAPI/Jinja, Python unittest existing gateway suites.

**Spec:** docs/marketing/AUDIT_REQUIREMENT_RECONCILIATION_2026-09-30.md (F163), tenant-app/db/cash_statement.py, shared_warehouse.py, ozon_pnl.py.

## Global Constraints

- Изменяются только gateway/templates/capabilities.html и этот план.
- Не заявлять прямую банковскую синхронизацию, автоматический общий склад или полную денежную нормализацию Ozon API.
- Не возвращать нежелательные конкурентные сравнения.
- Не менять данные/роли клиента. Production выпускает root после независимого review; этот агент не деплоит.

### Task 1: Источники ДДС, общего склада и Ozon

**Files:** Modify gateway/templates/capabilities.html; verify existing gateway/tests/test_marketing_routes.py and test_public_seo_audit.py.

**Interfaces:** Jinja template structure/route/title/schema unchanged; rendered statements match existing source contracts.

- [x] Прочитать canonical cash CSV columns+preview/dedup/link, explicit warehouse mappings/reservations, manual Ozon projection/null guards.
- [x] Исправить три строки таблицы, сохранив источник и действия клиента.
- [x] Выполнить существующие page/SEO suites, осмотреть diff и отсутствие прежних трёх устаревших ограничений. Новые тесты-перепечатки текста не добавлять.
- [x] Commit только двух принадлежащих агенту файлов. Передать root результаты для review и точного Gateway release.
- [ ] Root: exact image/backup/restore/rollback/deploy/live page. Пока не выполнено, считать изменение подготовленным, не опубликованным.

Проверено: existing marketing_routes + public_seo_audit — 10 passed, 14 subtests passed, 2 existing deprecation warnings. Diff ограничен тремя строками таблицы, без изменения HTML structure/API. Независимая review OzonUI4660736: 9 frontend tests passed; explicit collection flag не заменяется legacy source_complete, денежная нормализация и NULL profit разделены. Это не утверждение о выпуске4660736.
