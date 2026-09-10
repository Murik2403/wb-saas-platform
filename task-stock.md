# Task: migrate the "Остатки" (stock) page from Streamlit to Next.js

Continuing the MARKETSHELPER frontend migration. Streamlit source of truth (DO NOT modify):
`tenant-app/pages/stock.py`. Context: `memory/project_marketshelper_frontend_migration.md`,
`C:\Users\mvvor\Downloads\handoff20260831.md`.

Study the 7 done pages for the 4-part pattern:
`tenant-app/<name>_service.py` + routes in `tenant-app/api_server.py` +
`frontend/src/lib/<name>.ts` + `frontend/src/app/<route>/page.tsx`.
Not period-scoped — reflects current-moment state, ctx like `api_server._build_ctx()`, same as
`control_service.py`. Closest refs: `control_service.py` / `app/control/page.tsx` (KPIs + actions +
CSV) and `reports` page (mutations/flash).

## Produce
1. `tenant-app/stock_service.py` — pure read model for the 6 tabs:
   По складам WB, Стоимостная оценка (sub-tabs Слои / Движения стоимости),
   FIFO готовой продукции, FIFO продаж, Сверка FIFO, Инциденты WB.
   All Russian strings in COPY. `json_safe`.
2. `tenant-app/api_server.py` routes — one per `st.button` in stock.py. Notably:
   `GET /api/stock`, `POST /api/stock/fifo-recheck` (the "Полная перепроверка" button — same as
   `control_service.run_fifo_recheck` if applicable, otherwise stock.py's own),
   the FBW-API verify buttons (`POST /api/stock/wb/verify-*`), and the full WB-incidents CRUD:
   create case, update case, add compensation, reverse line, delete compensation record.
   Reuse stock.py's imports from `db`/`wb_incidents`/`ui_helpers` — do not reimplement.
   Live WB API calls (verify-through-FBW) must NOT be fired during automated testing — GET only.
3. `frontend/src/lib/stock.ts` — mirror `lib/control.ts` + `lib/reports.ts`.
4. `frontend/src/app/stock/page.tsx` — mirror `app/control/page.tsx` + `app/reports/page.tsx`.
   Tab switcher, tables like `app/products/page.tsx`, confirm dialogs for every reverse/delete/
   compensation action. `/stock` already linked in `app-shell.tsx`.

## Constraints
Faithful port, same Russian copy verbatim, no redesign. Plotly donut → small inline SVG or omit.
Do NOT touch `pages/*.py`, `app.py`, `mh-redesign-lab/`, `gateway/`, `docs/superpowers/`.
Tests in `tenant-app/tests/` where practical.

## Verify before done
- `cd tenant-app && .\.venv\Scripts\python.exe -c "import stock_service, api_server"` clean.
- `cd frontend && npx tsc --noEmit` clean.
- Restart both servers, `curl http://127.0.0.1:8020/api/stock`,
  open `http://localhost:3001/stock`, browser console clean.
- Report what you did and any gaps.
