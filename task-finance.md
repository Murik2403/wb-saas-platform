# Task: migrate the "Финансы" (finance) page from Streamlit to Next.js

Continuing the MARKETSHELPER frontend migration. Streamlit source of truth (DO NOT modify):
`tenant-app/pages/finance.py`. Context: `memory/project_marketshelper_frontend_migration.md`,
`C:\Users\mvvor\Downloads\handoff20260831.md`.

Study the 7 done pages for the 4-part pattern:
`tenant-app/<name>_service.py` + routes in `tenant-app/api_server.py` +
`frontend/src/lib/<name>.ts` + `frontend/src/app/<route>/page.tsx`.
This page is **period-scoped** (uses `ctx['start']`/`ctx['end']`/`data`) exactly like
`overview_service.py` / `app/overview/page.tsx` / the `/api/overview` route — copy that
period=today|7d|30d|90d|custom contract verbatim (see `overview_service.resolve_window`).
For the mutations copy `reports` page pattern.

## Produce
1. `tenant-app/finance_service.py` — pure read model. Sections in finance.py:
   "Финансовый результат" (KPI rows from `data.financial`), the margin block with tabs
   [Сводка, Юнит-экономика, Действия, Центр решений], and the ranking block with tabs
   [По общей прибыли, По прибыли на единицу]. All Russian strings in COPY. `json_safe`.
2. `tenant-app/api_server.py` routes:
   `GET /api/finance?period=&start=&end=` (read model),
   `POST /api/finance/decision-parameters` (the "Сохранить параметры центра решений" button —
   save_settings), `POST /api/finance/purchase-rules` (the "Сохранить MOQ и сроки" button).
   Reuse finance.py's imports from `config`/`db`/`ui_helpers` — do not reimplement the
   decision-center / consolidated logic.
3. `frontend/src/lib/finance.ts` — mirror `lib/overview.ts` (period param) + `lib/reports.ts`
   (POST helpers).
4. `frontend/src/app/finance/page.tsx` — mirror `app/overview/page.tsx` for the period selector +
   KPI cards, and `app/reports/page.tsx` for forms/flash. Tables like `app/products/page.tsx`.
   `/finance` already linked in `app-shell.tsx`.

## Constraints
Faithful port, same Russian copy verbatim, no redesign. Charts in finance.py (plotly) →
simple HTML/CSS bars or a small inline SVG, or just the table — keep it functional. Do NOT touch
`pages/*.py`, `app.py`, `mh-redesign-lab/`, `gateway/`, `docs/superpowers/`. Tests in
`tenant-app/tests/` where practical.

## Verify before done
- `cd tenant-app && .\.venv\Scripts\python.exe -c "import finance_service, api_server"` clean.
- `cd frontend && npx tsc --noEmit` clean.
- Restart both servers, `curl "http://127.0.0.1:8020/api/finance?period=30d"`,
  open `http://localhost:3001/finance`, browser console clean.
- Report what you did and any gaps.
