# API Contracts For The Redesign Lab

Этот документ описывает минимальный контракт для подключения нового интерфейса к реальным данным. Текущий Streamlit остается fallback-версией до тех пор, пока эти контракты не будут закрыты тестами и не появится feature flag для переключения.

## Общие правила

- Base URL целевого backend: `http://127.0.0.1:8001`.
- В redesign lab есть bridge `http://127.0.0.1:8011` для старых staging-развёртываний. Он сначала проксирует `/api/v1/...` в актуальный tenant API, а для старого backend использует адаптацию `/api/today` и `/api/overview?period=30d`.
- Для Vite dev frontend ходит через proxy `/tenant-api` на bridge, поэтому CORS в текущем backend менять не нужно.
- Все даты в пользовательских запросах идут в часовом поясе `Europe/Moscow`.
- Денежные значения передаются целыми копейками: `amount_kop`.
- Проценты передаются в basis points: `980` означает `9.8%`, `3160` означает `31.6%`.
- Клиент не передает tenant id вручную: backend берет tenant из сессии/gateway-контекста.
- Ответы должны быть read-only на первом этапе. Кнопки действий можно подключать позже через отдельный action-plan endpoint.

## GET `/api/v1/dashboard/today`

Query:

```http
GET /api/v1/dashboard/today?date=2026-08-30
```

Response:

```json
{
  "generated_at": "2026-08-30T14:20:00+03:00",
  "sync": {
    "state": "fresh",
    "source": "wb-api",
    "updated_at": "2026-08-30T14:09:00+03:00",
    "lag_minutes": 11
  },
  "metrics": [
    {
      "id": "revenue",
      "label": "Выручка",
      "value": { "kind": "money", "amount_kop": 184000000, "currency": "RUB" },
      "delta_bps": 1800,
      "comparison_label": "к среде прошлой недели",
      "tone": "good"
    }
  ],
  "hero_action": {
    "id": "stockout-wb-493-22",
    "sku": "WB-493-22",
    "title": "Закупить 80 шт.",
    "description": "Остаток на 1,8 дня, карточка держит темп",
    "impact_kop": 7300000,
    "due_at": "2026-08-30T16:00:00+03:00",
    "tone": "danger"
  },
  "actions": [
    {
      "id": "stockout-wb-493-22",
      "type": "stock_reorder",
      "sku": "WB-493-22",
      "title": "Костюм спортивный графит",
      "issue": "Остаток на 1,8 дня, карточка держит темп",
      "recommended_action": "Закупить 80 шт.",
      "impact_kop": 7300000,
      "due_at": "2026-08-30T16:00:00+03:00",
      "tone": "danger"
    }
  ],
  "revenue_trend": [
    { "time": "10:00", "revenue_kop": 42000000, "plan_bps": 4200 },
    { "time": "11:00", "revenue_kop": 57000000, "plan_bps": 5700 }
  ],
  "production": [
    { "stage": "Крой", "status": "done", "value": "готово" },
    { "stage": "Пошив", "status": "active", "value": "68%" }
  ],
  "inventory_watch": [
    {
      "sku": "493-22",
      "name": "Костюм спортивный графит",
      "stock_days": 1.8,
      "margin_bps": 3400,
      "signal": "поставка",
      "tone": "danger"
    }
  ]
}
```

## GET `/api/v1/dashboard/stock`

Query:

```http
GET /api/v1/dashboard/stock?date=2026-08-30
```

Response shape:

```json
{
  "generated_at": "2026-08-30T14:20:00+03:00",
  "sync": { "state": "fresh", "source": "wb-api", "updated_at": "2026-08-30T14:09:00+03:00", "lag_minutes": 11 },
  "metrics": [],
  "hero": {
    "primary": "5 SKU уходят в ноль быстрее прогноза",
    "secondary": "1 280 шт. в пути",
    "tone": "danger"
  },
  "reorder_plan": [
    {
      "sku": "493-22",
      "name": "Костюм спортивный графит",
      "stock_days": 1.8,
      "recommended_qty": 80,
      "warehouse": "Коледино",
      "risk_label": "потеря 73 000 ₽",
      "tone": "danger"
    }
  ],
  "coverage": [
    {
      "label": "≤ 7 дн.",
      "detail": "критический запас",
      "value": "24 SKU",
      "width": 63,
      "tone": "danger"
    }
  ],
  "warehouses": [
    { "name": "Коледино", "coverage_bps": 3700, "tone": "danger" },
    { "name": "Электросталь", "coverage_bps": 5800, "tone": "warn" },
    { "name": "Казань", "coverage_bps": 8600, "tone": "good" }
  ],
  "rules": [
    {
      "id": "auto-reorder-under-5-days",
      "title": "Заказывать при запасе меньше 5 дней",
      "description": "Товары с маржей ниже 20% отправлять в ручное решение."
    }
  ],
  "inventory_watch": []
}
```

## GET `/api/v1/dashboard/ads`

Query:

```http
GET /api/v1/dashboard/ads?date=2026-08-30
```

Response shape:

```json
{
  "generated_at": "2026-08-30T14:20:00+03:00",
  "sync": { "state": "fresh", "source": "wb-api", "updated_at": "2026-08-30T14:09:00+03:00", "lag_minutes": 11 },
  "metrics": [],
  "hero": {
    "title": "Бюджет переносим из перерасхода в масштаб",
    "description": "Снижаем ставки в поиске по костюмам и добавляем бюджет на пижамы.",
    "tone": "info"
  },
  "campaigns": [
    {
      "id": "search-suits-auto",
      "name": "Поиск / костюмы / авто",
      "spend_kop": 6320000,
      "revenue_kop": 42700000,
      "acos_bps": 1480,
      "signal": "выше лимита",
      "recommended_action": "Снизить ставки",
      "tone": "warn"
    }
  ],
  "funnel": [
    { "label": "Заказано", "value": 1030, "value_text": "1 030", "width_pct": 81.6 },
    { "label": "Выкуплено", "value": 1262, "value_text": "1 262", "width_pct": 100 },
    { "label": "Возврат", "value": 50, "value_text": "50", "width_pct": 4 }
  ],
  "creative_tests": [
    { "label": "cremovple4", "value": "ДРР 4,8%", "winner": true },
    { "label": "newformbez4", "value": "ДРР 2,7%", "winner": true }
  ]
}
```

## POST `/api/v1/action-plan/items`

В локальной redesign-версии endpoint уже подключён через dashboard API bridge. В production tenant API его нужно перенести на постоянное хранилище с авторизацией и tenant isolation.

Request:

```json
{
  "source": "dashboard_today",
  "source_action_id": "stockout-wb-493-22",
  "title": "Закупить 80 шт.",
  "sku": "WB-493-22",
  "due_at": "2026-08-30T16:00:00+03:00"
}
```

Response:

```json
{
  "id": "plan-2026-08-30-001",
  "status": "created"
}
```

## GET `/api/v1/action-plan/items`

Возвращает задачи из локального persistent-store bridge. Action plan-файлы разделены по tenant и хранятся в `.tmp/action-plans/{tenant-id}.json`.

Response:

```json
{
  "items": [
    {
      "id": "plan-2026-08-30-001",
      "title": "Закупить 80 шт.",
      "sku": "WB-493-22",
      "status": "created"
    }
  ]
}
```

## GET `/api/v1/session`

Локальный dev-контракт текущей сессии. В production заголовки `X-Tenant-Id` и `X-User-Id` должны формироваться после проверки JWT/OIDC, а не приниматься напрямую от клиента.

```json
{
  "user": { "id": "demo-user", "name": "Мурад", "role": "owner" },
  "tenant": { "id": "demo-tenant", "name": "WB SaaS Demo" },
  "mode": "local-dev"
}
```

Все action plan-файлы в local-dev разделены по tenant: `.tmp/action-plans/{tenant-id}.json`.

## PATCH `/api/v1/action-plan/items/{id}`

Меняет статус задачи. Допустимые значения: `created`, `in_progress`, `done`, `cancelled`.

Request:

```json
{ "status": "done" }
```

## Ошибки

```json
{
  "error": {
    "code": "no_data_yet",
    "message": "Данные за выбранный период еще не загружены",
    "retry_after_seconds": 300
  }
}
```

Ожидаемые коды:

- `401`: нет активной сессии.
- `403`: tenant не имеет доступа к модулю.
- `409`: данных за период еще нет.
- `422`: неверные query-параметры.
- `503`: WB API или локальный sync недоступен.

## План подключения

1. Оставить Streamlit как основной рабочий путь.
2. Поднять API рядом с текущими расчетами, без изменения Streamlit-страниц.
3. В redesign lab bridge временно собирает v1-модель из `/api/today` и `/api/overview?period=30d`.
4. В новом фронтенде использовать `src/data-source.ts`: он уже пробует новый API, затем legacy `/api/today`, затем mock fallback.
5. Добавить feature flag `REDESIGN_ENABLED=false` и включать новую версию только локально или для тестового tenant.
6. После совпадения цифр по `Сегодня`, `Склад`, `Реклама` переключать разделы по одному.
