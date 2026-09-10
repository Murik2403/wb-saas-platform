# Task: migrate the "Настройки" (settings) page from Streamlit to Next.js

Continuing the MARKETSHELPER frontend migration. Streamlit source of truth (DO NOT modify):
`tenant-app/pages/settings_page.py`. Context: `memory/project_marketshelper_frontend_migration.md`,
`C:\Users\mvvor\Downloads\handoff20260831.md`.

Study the 7 already-done pages for the exact pattern — each is 4 parts:
`tenant-app/<name>_service.py` + route(s) in `tenant-app/api_server.py` +
`frontend/src/lib/<name>.ts` + `frontend/src/app/<route>/page.tsx`.
Closest references to copy: `reports_service.py` / `lib/reports.ts` / `app/reports/page.tsx`
(mutation-heavy page) and `control_service.py` (actions + connection checks).

## Produce
1. `tenant-app/settings_service.py` — pure read model for all 8 tabs:
   Подключение (WB token + Ozon keys status, never return secret values — only booleans/masked),
   Обновление данных, Себестоимость, Производство, Остатки сырья, Готовая продукция, Мощность,
   Резервные копии. Keep all Russian strings in a COPY dict. `json_safe` from today_service.
   ctx built like `api_server._build_ctx()`.
2. `tenant-app/api_server.py` routes — one per `st.button` in settings_page.py. Key ones:
   `POST /api/settings/wb-token` (save), `POST /api/settings/wb-token/check`, `DELETE /api/settings/wb-token`,
   same trio for `/api/settings/ozon-keys`, `POST /api/settings/sync`, `POST /api/settings/financial-report`
   (multipart upload), `POST /api/settings/costs`, `POST /api/settings/forecast-components`,
   `POST /api/settings/forecast/apply`, `POST /api/settings/production-params`,
   `POST /api/settings/raw-stock`, FIFO init endpoints (materials / finished-goods / sales-fifo
   init+process+retry), `POST /api/settings/finished-goods`, `POST /api/settings/capacity`,
   `POST /api/settings/backup`, demo-data load/delete. Reuse the exact functions settings_page.py
   imports from `config` / `db` / `backup_tools` — do not reimplement.
   **Never expose the raw WB token or Ozon api-key in any response.**
3. `frontend/src/lib/settings.ts` — mirror `lib/reports.ts` style.
4. `frontend/src/app/settings/page.tsx` — mirror `app/reports/page.tsx` visual pattern (Card, Pill,
   Skeleton, flash, tab switcher, `animate-content-in`, `px-4 py-5 sm:px-6 md:px-10` wrapper, CSS
   vars). `/settings` already linked in `app-shell.tsx`. Password inputs `type="password"`.
   Confirm dialogs for delete-token, delete Ozon keys, backup, FIFO re-init, demo-data delete.

## Constraints
Faithful port, same Russian copy verbatim, no redesign. Do NOT touch `pages/*.py`, `app.py`,
`mh-redesign-lab/`, `gateway/`, `docs/superpowers/`. Add tests in `tenant-app/tests/` where practical.

## Verify before done
- `cd tenant-app && .\.venv\Scripts\python.exe -c "import settings_service, api_server"` clean.
- `cd frontend && npx tsc --noEmit` clean.
- Restart both servers (handoff cmds), `curl http://127.0.0.1:8020/api/settings`,
  open `http://localhost:3001/settings`, browser console clean.
- Report what you did and any gaps.
