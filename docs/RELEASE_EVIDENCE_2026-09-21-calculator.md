# Выпуск расширенного калькулятора — 2026-09-21

Коммит `34b7a5796f9d2353c892a5ba8f0edc622fb4d680` собран в immutable gateway image с тем же SHA в OCI revision:

- image: `sha256:bd2da2f7e4950276ddec0a1a085c7aa68c87619b272be29da3ef85eece07c992`;
- контейнер: `wb-saas-gateway-1`;
- health: `healthy`;
- rollback tag старого gateway сохранён как `mh-gateway:rollback-34b7a57`;
- перед переключением создан SQLite backup `/app/data/gateway-pre-release-34b7a57.sqlite3`, `PRAGMA integrity_check` вернул `ok`.

Проверки после переключения:

- `https://marketshelper.ru/healthz` — HTTP 200, `{"status":"ok"}`;
- `https://marketshelper.ru/calculator` — HTTP 200;
- живая страница содержит заголовок калькулятора по девяти статьям и блок покрытия статей;
- tenant-контейнер не переключался.

Локальные проверки перед выпуском: browser calculator test passed; 35 Python tests passed, 138 subtests passed; `git diff --check` passed; gateway image build passed.
