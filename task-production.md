# Task: migrate the "Производство" (production) page from Streamlit to Next.js

Continuing the MARKETSHELPER frontend migration. Streamlit source of truth (DO NOT modify):
`tenant-app/pages/production.py` (largest page, ~2277 lines — go section by section, not one
blind pass). Context: `memory/project_marketshelper_frontend_migration.md`,
`C:\Users\mvvor\Downloads\handoff20260831.md`.

Study the 7 done pages for the 4-part pattern:
`tenant-app/<name>_service.py` + routes in `tenant-app/api_server.py` +
`frontend/src/lib/<name>.ts` + `frontend/src/app/<route>/page.tsx`.
Period-scoped (uses `ctx['start']`/`ctx['end']`/`ctx['today_msk']`/`data`) like
`overview_service.py` — copy the period contract from the `/api/overview` route.
Mutations: copy `reports` page pattern.

## Produce
1. `tenant-app/production_service.py` — pure read model. Structure of production.py:
   "План производства" (KPIs + plan table), action tabs [Изготовить, Отгрузить / FBS, Реклама],
   execution tabs [Сменное задание, Отгрузки, Печатная форма, Журнал движений,
   Себестоимость партий, НЗП / заготовки], and inside НЗП the sub-tabs
   [Выпуск заготовок, Остатки НЗП, Комплектация, Журнал НЗП].
   All Russian strings in COPY. `json_safe`.
2. `tenant-app/api_server.py` routes — one per `st.button` in production.py (there are ~15;
   enumerate them). Groups: start/close production shift, dispatch status save + create + edit,
   printable form (return HTML/PDF like the reports download), movement-journal undo, WIP:
   release blanks, close WIP batch, manual WIP packaging post, undo WIP movement.
   Reuse production.py's imports from `db`/`db.wip`/`db.production`/`ui_helpers` — do not
   reimplement FIFO/WIP logic.
3. `frontend/src/lib/production.ts` — mirror `lib/overview.ts` + `lib/reports.ts`.
4. `frontend/src/app/production/page.tsx` — mirror `app/overview/page.tsx` (period + KPIs) and
   `app/reports/page.tsx` (forms/flash). Nested tab switchers. Tables like `app/products/page.tsx`.
   Confirm dialogs for every close-batch / post / undo action. `/production` already linked in
   `app-shell.tsx`.

## Constraints
Faithful port, same Russian copy verbatim, no redesign. This is the biggest page — prioritise the
plan table + Изготовить + Отгрузки + Сменное задание + Журнал движений; НЗП and Печатная форма
can be simpler but must work. Do NOT touch `pages/*.py`, `app.py`, `mh-redesign-lab/`, `gateway/`,
`docs/superpowers/`. Tests in `tenant-app/tests/` where practical.

## Verify before done
- `cd tenant-app && .\.venv\Scripts\python.exe -c "import production_service, api_server"` clean.
- `cd frontend && npx tsc --noEmit` clean.
- Restart both servers, `curl "http://127.0.0.1:8020/api/production?period=30d"`,
  open `http://localhost:3001/production`, browser console clean.
- Report what you did and any gaps.
