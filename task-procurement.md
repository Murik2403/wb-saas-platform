# Task: migrate the "Закупки" (procurement) page from Streamlit to Next.js

You are continuing the MARKETSHELPER frontend migration. Streamlit source of truth:
`tenant-app/pages/procurement.py` (do NOT modify it, it stays live). Full context:
`memory/project_marketshelper_frontend_migration.md` and `handoff20260831.md` in repo root
(the latter is at `C:\Users\mvvor\Downloads\handoff20260831.md`).

## What to produce (same 4-part pattern as the 7 already-done pages)

1. `tenant-app/procurement_service.py` — pure computation layer. Mirror the read model that
   `pages/procurement.py::render()` builds: the 6 KPI cards, and the 8 tabs
   (Рекомендации, Создать закупку, Заявки, Приёмка, Платежи, Поставщики, Журнал, Сводный план).
   Follow the structure/þdocstring style of `tenant-app/reports_service.py` and
   `tenant-app/overview_service.py`. Keep every Russian UI string here (COPY dict), not in TSX.
   Use `from today_service import json_safe` to serialize. Reuse the exact same functions
   `pages/procurement.py` imports from `db` and `ui_helpers` — do not reimplement business logic.
   `ctx` is built like `api_server._build_ctx()` (needs `data` from `build_dashboard`).

2. `tenant-app/api_server.py` — add routes. Read: `GET /api/procurement`. Mutations, mirroring
   the Streamlit buttons (study each `st.button` in procurement.py):
   - `POST /api/procurement/orders` (create, incl. the two "из рекомендаций" auto-create flows)
   - `PATCH /api/procurement/orders/{id}` (update header + items + refresh_auto_costs)
   - `DELETE /api/procurement/orders/{id}`
   - `POST /api/procurement/orders/{id}/receipt`
   - `POST /api/procurement/orders/{id}/payments`  + `POST /api/procurement/payments/{id}/undo`
   - `POST /api/procurement/suppliers` (create/update via save_supplier)
   - `POST /api/procurement/movements/{id}/undo`
   - `POST /api/procurement/consolidated-orders` (create draft orders from the plan)
   - `GET /api/procurement/consolidated-plan.csv` (download_button equivalent)
   - budget save can fold into the suppliers/settings pattern: `POST /api/procurement/budget`
   Add PATCH to `allow_methods` in the CORSMiddleware list if missing.

3. `frontend/src/lib/procurement.ts` — types + fetch helpers. Copy the style of
   `frontend/src/lib/reports.ts` exactly (same `API_URL`, `unwrap<T>` helper, one fn per route).

4. `frontend/src/app/procurement/page.tsx` — the page. Copy the visual/interaction pattern of
   `frontend/src/app/reports/page.tsx` (Card, Pill, Skeleton, flash messages, `animate-content-in`,
   the `px-4 py-5 sm:px-6 md:px-10` wrapper, CSS var colors). Tabs: a simple tab switcher.
   Tables: plain `<table>` styled with `var(--border)` like nothing fancy — match products/page.tsx
   for the table styling (read `frontend/src/app/products/page.tsx`). Confirmation dialogs for every
   destructive / money-moving / stock-moving action (`confirm(...)` is fine, reports page uses it).
   Also add `/procurement` is ALREADY in `app-shell.tsx` NAV — no shell change needed.

## Constraints
- Match the finished pages' altitude: faithful port, not a redesign. Same Russian copy verbatim.
- Do NOT touch `pages/*.py`, `app.py`, `mh-redesign-lab/`, `gateway/`, `docs/superpowers/`.
- The data editors in procurement.py (st.data_editor) become editable HTML tables or simple
  add-row forms — keep it functional, not pixel-perfect. Prioritise: Рекомендации, Заявки list,
  Приёмка, Платежи, Поставщики, Журнал. "Создать закупку" and "Сводный план" can be simpler.
- Write tests where practical (`tenant-app/tests/` if it exists — check).

## Verify before declaring done
- `cd tenant-app && .\.venv\Scripts\python.exe -c "import procurement_service, api_server"` — no errors.
- `cd frontend && npx tsc --noEmit` (or `npm run build`) — no type errors.
- Restart both servers (commands in handoff), `curl http://127.0.0.1:8020/api/procurement`,
  open `http://localhost:3001/procurement`, check browser console clean.
- Report exactly what you did and any gaps.
