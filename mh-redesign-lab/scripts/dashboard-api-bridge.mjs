import { createServer } from "node:http";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";

const host = process.env.MH_BRIDGE_HOST ?? "127.0.0.1";
const port = Number(process.env.MH_BRIDGE_PORT ?? 8011);
const tenantApiBase = process.env.MH_TENANT_API_BASE_URL ?? "http://127.0.0.1:8001";
const legacyCacheTtlMs = Number(process.env.MH_LEGACY_CACHE_TTL_MS ?? 30000);
const overviewCacheTtlMs = Number(process.env.MH_OVERVIEW_CACHE_TTL_MS ?? 60000);
const overviewSoftTimeoutMs = Number(process.env.MH_OVERVIEW_SOFT_TIMEOUT_MS ?? 7000);
const actionPlanDir = resolve(process.env.MH_ACTION_PLAN_DIR ?? "./.tmp/action-plans");
const tenantSource = process.env.MH_TENANT_SOURCE ?? "header";
const tenantParentDomain = process.env.MH_TENANT_PARENT_DOMAIN ?? "marketshelper.ru";
const tenantSubdomainPrefix = process.env.MH_TENANT_SUBDOMAIN_PREFIX ?? "app";
const localTenantId = process.env.MH_LOCAL_TENANT_ID ?? "marketshelper-live";
const localTenantName = process.env.MH_LOCAL_TENANT_NAME ?? "Локальная копия аккаунта";
const localUserName = process.env.MH_LOCAL_USER_NAME ?? "Мурад";
const actionPlans = new Map();

let legacyCache = {
  expiresAt: 0,
  payload: null,
  promise: null,
};

let overviewCache = {
  expiresAt: 0,
  payload: null,
  promise: null,
};

const jsonHeaders = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
  "access-control-allow-origin": "*",
  "access-control-allow-methods": "GET, POST, PATCH, OPTIONS",
  "access-control-allow-headers": "content-type, authorization, x-tenant-id, x-user-id",
};

const server = createServer(async (request, response) => {
  if (!request.url) {
    sendJson(response, 400, { error: { code: "bad_request", message: "Missing URL" } });
    return;
  }

  const url = new URL(request.url, `http://${request.headers.host ?? `${host}:${port}`}`);
  const tenantId = tenantFromRequest(request);

  if (request.method === "OPTIONS") {
    response.writeHead(204, jsonHeaders);
    response.end();
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/v1/action-plan/items") {
    const tenantPlan = await getActionPlan(tenantId);
    const item = await readJsonBody(request);
    if (!item?.title || !item?.sku) {
      sendJson(response, 422, { error: { code: "invalid_action_plan_item", message: "title and sku are required" } });
      return;
    }

    const existing = item.source_action_id
      ? tenantPlan.find((entry) => entry.source_action_id === item.source_action_id && entry.status !== "cancelled")
      : null;
    if (existing) {
      sendJson(response, 409, { error: { code: "action_plan_item_exists", message: "Action is already in the plan", item: existing } });
      return;
    }

    const created = {
      id: `plan-${Date.now()}-${tenantPlan.length + 1}`,
      tenant_id: tenantId,
      created_by: request.headers["x-user-id"] ?? "demo-user",
      source: item.source ?? "dashboard",
      source_action_id: item.source_action_id ?? null,
      title: String(item.title),
      sku: String(item.sku),
      due_at: item.due_at ?? null,
      assignee: item.assignee ?? null,
      priority: item.priority ?? "medium",
      status: "created",
      created_at: new Date().toISOString(),
      history: [{ status: "created", at: new Date().toISOString() }],
    };
    tenantPlan.unshift(created);
    await saveActionPlan(tenantId, tenantPlan);
    sendJson(response, 201, created);
    return;
  }

  if (request.method === "GET" && url.pathname === "/api/v1/action-plan/items") {
    sendJson(response, 200, { items: await getActionPlan(tenantId) });
    return;
  }

  if (request.method === "GET" && url.pathname === "/api/v1/session") {
    sendJson(response, 200, {
      user: { id: request.headers["x-user-id"] ?? "live-user", name: localUserName, role: "owner" },
      tenant: { id: tenantId, name: tenantId === localTenantId ? localTenantName : tenantId },
      mode: "live-local",
    });
    return;
  }

  const statusMatch = url.pathname.match(/^\/api\/v1\/action-plan\/items\/([^/]+)$/);
  if (request.method === "PATCH" && statusMatch) {
    const tenantPlan = await getActionPlan(tenantId);
    const item = tenantPlan.find((entry) => entry.id === statusMatch[1]);
    if (!item) {
      sendJson(response, 404, { error: { code: "action_plan_item_not_found", message: "Action plan item not found" } });
      return;
    }

    const body = await readJsonBody(request);
    const statuses = new Set(["created", "in_progress", "done", "cancelled"]);
    const priorities = new Set(["low", "medium", "high", "critical"]);
    if (body?.status !== undefined && !statuses.has(body.status)) {
      sendJson(response, 422, { error: { code: "invalid_action_plan_status", message: "Unsupported status" } });
      return;
    }
    if (body?.priority !== undefined && !priorities.has(body.priority)) {
      sendJson(response, 422, { error: { code: "invalid_action_plan_priority", message: "Unsupported priority" } });
      return;
    }

    if (body?.status && body.status !== item.status) {
      item.status = body.status;
      item.history = [...(Array.isArray(item.history) ? item.history : []), { status: body.status, at: new Date().toISOString() }];
    }
    if (body?.assignee !== undefined) item.assignee = body.assignee || null;
    if (body?.due_at !== undefined) item.due_at = body.due_at || null;
    if (body?.priority !== undefined) item.priority = body.priority;
    item.updated_at = new Date().toISOString();
    await saveActionPlan(tenantId, tenantPlan);
    sendJson(response, 200, item);
    return;
  }

  if (request.method !== "GET") {
    sendJson(response, 405, { error: { code: "method_not_allowed", message: "Only GET, POST and PATCH are supported" } });
    return;
  }

  try {
    if (url.pathname === "/api/health") {
      await fetchLegacyToday();
      sendJson(response, 200, {
        status: "ok",
        bridge: "dashboard-api-bridge",
        tenant_api_base: tenantApiBase,
      });
      return;
    }

    if (url.pathname === "/api/today") {
      const legacy = await fetchLegacyToday();
      sendJson(response, 200, legacy);
      return;
    }

    if (url.pathname === "/api/overview") {
      const canonical = await fetchTenantJson(url.pathname, url.search, request);
      if (canonical) {
        sendJson(response, 200, canonical);
        return;
      }
      const overview = await fetchOverview(url.searchParams.get("period") ?? "30d");
      sendJson(response, 200, overview);
      return;
    }

    if (url.pathname === "/api/v1/dashboard/today") {
      const canonical = await fetchTenantJson(url.pathname, url.search, request);
      if (canonical) {
        sendJson(response, 200, canonical);
        return;
      }
      const [legacy, overview] = await Promise.all([fetchLegacyToday(), fetchOverviewSoft("30d")]);
      sendJson(response, 200, toTodayV1(legacy, overview));
      return;
    }

    if (url.pathname === "/api/v1/dashboard/stock") {
      const canonical = await fetchTenantJson(url.pathname, url.search, request);
      if (canonical) {
        sendJson(response, 200, canonical);
        return;
      }
      const [legacy, overview] = await Promise.all([fetchLegacyToday(), fetchOverviewSoft("30d")]);
      sendJson(response, 200, toStockV1(legacy, overview));
      return;
    }

    if (url.pathname === "/api/v1/dashboard/ads") {
      const canonical = await fetchTenantJson(url.pathname, url.search, request);
      if (canonical) {
        sendJson(response, 200, canonical);
        return;
      }
      const [legacy, overview] = await Promise.all([fetchLegacyToday(), fetchOverviewSoft("30d")]);
      sendJson(response, 200, toAdsV1(legacy, overview));
      return;
    }

    if (url.pathname.startsWith("/api/v1/")) {
      const canonical = await fetchTenantJson(url.pathname, url.search, request);
      if (canonical) {
        sendJson(response, 200, canonical);
        return;
      }
    }

    sendJson(response, 404, {
      error: {
        code: "not_found",
        message: `No bridge route for ${url.pathname}`,
      },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unknown bridge error";
    sendJson(response, 502, {
      error: {
        code: "tenant_api_unavailable",
        message,
      },
    });
  }
});

server.listen(port, host, () => {
  console.log(`Dashboard API bridge listening on http://${host}:${port}/`);
  console.log(`Tenant API source: ${tenantApiBase}`);
});

async function fetchLegacyToday() {
  const now = Date.now();

  if (legacyCache.payload && legacyCache.expiresAt > now) {
    return legacyCache.payload;
  }

  if (legacyCache.payload) {
    if (!legacyCache.promise) {
      legacyCache.promise = requestLegacyToday();
      void legacyCache.promise.catch(() => undefined);
    }
    return legacyCache.payload;
  }

  if (legacyCache.promise) {
    return legacyCache.promise;
  }

  legacyCache.promise = requestLegacyToday();
  return legacyCache.promise;
}

function requestLegacyToday() {
  return fetch(`${tenantApiBase}/api/today`, {
    headers: { accept: "application/json" },
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text();
        throw new Error(`/api/today returned HTTP ${response.status}: ${text.slice(0, 240)}`);
      }

      return response.json();
    })
    .then((payload) => {
      legacyCache = {
        expiresAt: Date.now() + legacyCacheTtlMs,
        payload,
        promise: null,
      };

      return payload;
    })
    .catch((error) => {
      legacyCache.promise = null;
      throw error;
    });
}

async function fetchTenantJson(pathname, search, request) {
  const headers = { accept: "application/json" };
  for (const name of ["authorization", "x-tenant-id", "x-user-id", "x-forwarded-host", "x-forwarded-proto"]) {
    const value = request.headers[name];
    if (typeof value === "string" && value) {
      headers[name] = value;
    }
  }

  const response = await fetch(`${tenantApiBase}${pathname}${search ?? ""}`, { headers });
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${pathname} returned HTTP ${response.status}: ${text.slice(0, 240)}`);
  }
  return response.json();
}

async function fetchOverview(period) {
  const now = Date.now();

  if (overviewCache.payload && overviewCache.expiresAt > now && overviewCache.payload.period === period) {
    return overviewCache.payload;
  }

  if (overviewCache.payload && overviewCache.payload.period === period) {
    if (!overviewCache.promise) {
      overviewCache.promise = requestOverview(period);
      void overviewCache.promise.catch(() => undefined);
    }
    return overviewCache.payload;
  }

  if (overviewCache.promise) {
    return overviewCache.promise;
  }

  overviewCache.promise = requestOverview(period);
  return overviewCache.promise;
}

function requestOverview(period) {
  const params = new URLSearchParams({ period });
  return fetch(`${tenantApiBase}/api/overview?${params.toString()}`, {
    headers: { accept: "application/json" },
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text();
        throw new Error(`/api/overview returned HTTP ${response.status}: ${text.slice(0, 240)}`);
      }

      return response.json();
    })
    .then((payload) => {
      overviewCache = {
        expiresAt: Date.now() + overviewCacheTtlMs,
        payload,
        promise: null,
      };

      return payload;
    })
    .catch((error) => {
      overviewCache.promise = null;
      throw error;
    });
}

async function fetchOverviewSoft(period) {
  try {
    return await Promise.race([
      fetchOverview(period),
      delay(overviewSoftTimeoutMs).then(() => null),
    ]);
  } catch {
    return null;
  }
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function toTodayV1(legacy, overview) {
  const signals = array(legacy.signals);
  const heroAction = toAction(signals[0], 0);
  const overviewTrend = toRevenueTrend(overview);
  const trend = overviewTrend.length ? overviewTrend : toLegacyTrend(legacy.revenue_trend ?? legacy.revenue_trend_day ?? legacy.tabs?.revenue_trend);

  return {
    generated_at: new Date().toISOString(),
    sync: syncFromLegacy(legacy),
    metrics: array(legacy.kpis).slice(0, 4).map(toMetric),
    hero_action: heroAction,
    actions: signals.map(toAction),
    revenue_trend: trend,
    revenue_trend_day: trend,
    revenue_trend_week: trend,
    production: toProduction(legacy),
    inventory_watch: toInventoryWatch(legacy, overview),
    tabs: legacy.tabs ?? {},
  };
}

function toStockV1(legacy, overview) {
  const emergencyDays = number(legacy.tabs?.stock?.emergency_days) || 7;
  const products = overviewProducts(overview);
  const stockRows = products.length
    ? products
        .slice()
        .sort((a, b) => a.days_of_stock - b.days_of_stock)
        .slice(0, 12)
        .map(productToInventoryRow)
    : array(legacy.tabs?.stock?.rows).map(toInventoryRow);
  const reorderPlan = products.length
    ? products
        .filter((product) => product.stock > 0 || product.sales > 0)
        .filter((product) => product.days_of_stock <= emergencyDays * 2 || product.stock <= product.sales)
        .sort((a, b) => a.days_of_stock - b.days_of_stock)
        .slice(0, 8)
        .map((product) => productToReorderItem(product, overviewPeriodDays(overview), emergencyDays))
    : array(legacy.tabs?.procurement?.products_to_buy).map(toReorderItem);
  const activeOrders = array(legacy.tabs?.procurement?.active_orders);
  const pipeline = array(legacy.kpis).find((metric) => metric.key === "pipeline");
  const criticalCount = products.length
    ? products.filter((product) => product.days_of_stock <= emergencyDays).length
    : stockRows.length;
  const totalStock = products.length ? sum(products, (product) => product.stock) : parseDisplayNumber(overviewKpi(overview, "stock")?.value);
  const recommendedQty = sum(reorderPlan, (item) => item.recommended_qty ?? 0);

  return {
    generated_at: new Date().toISOString(),
    sync: syncFromLegacy(legacy),
    hero: {
      primary: criticalCount
        ? `${criticalCount} SKU ниже ${formatNumber(emergencyDays)} дней запаса`
        : `Критичного дефицита ниже ${formatNumber(emergencyDays)} дней нет`,
      primaryNote: products.length
        ? `План собран из ${products.length} товаров за ${overview.period_label ?? "30 дней"}.`
        : "В текущем /api/today нет строк критического склада.",
      secondary: `${formatNumber(totalStock)} шт.`,
      secondaryNote: "Фактический остаток на складах WB из обзорного API.",
      tone: criticalCount > 0 ? "danger" : "good",
    },
    metrics: [
      {
        id: "critical_stock",
        label: "Дефицит",
        value: `${criticalCount} SKU`,
        note: `порог ${formatNumber(emergencyDays)} дней`,
        comparison_label: "остатки WB",
        tone: criticalCount > 0 ? "danger" : "good",
      },
      {
        id: "reorder_qty",
        label: "К отгрузке",
        value: `${formatNumber(recommendedQty)} шт.`,
        note: `${reorderPlan.length} позиций`,
        comparison_label: "план пополнения",
        tone: reorderPlan.length > 0 ? "warn" : "good",
      },
      {
        id: "pipeline",
        label: "Готово / в пути",
        value: pipeline?.value ?? "0 / 0",
        note: pipeline?.note ?? "комплектов",
        comparison_label: "производственный контур",
        tone: "info",
      },
      {
        id: "stock_total",
        label: "Остаток WB",
        value: `${formatNumber(totalStock)} шт.`,
        note: activeOrders.length ? `${activeOrders.length} активных закупок` : "активных закупок нет",
        comparison_label: "фактический склад",
        tone: criticalCount > 0 ? "warn" : "good",
      },
    ],
    reorder_plan: reorderPlan,
    coverage: stockCoverage(products, emergencyDays),
    rules: [
      {
        id: "auto-reorder-under-threshold",
        title: `Пополнять при запасе меньше ${formatNumber(emergencyDays)} дней`,
        description: "Количество считается от средней продажи за период и целевого покрытия 21 день.",
      },
      {
        id: "ads-stock-guard",
        title: "Не масштабировать рекламу при коротком запасе",
        description: `SKU ниже ${formatNumber(emergencyDays)} дней сначала идут в поставку, потом в рост ставок.`,
      },
    ],
    inventory_watch: stockRows,
  };
}

function toAdsV1(legacy, overview) {
  const adsKpi = overviewKpi(overview, "ad_spend") ?? array(legacy.kpis).find((metric) => metric.key === "ads");
  const revenueKpi = overviewKpi(overview, "revenue");
  const products = overviewProducts(overview);
  const adProducts = products
    .filter((product) => product.drr > 0 || product.revenue > 0)
    .slice()
    .sort((a, b) => b.drr - a.drr || b.revenue - a.revenue);
  const campaigns = adProducts.slice(0, 8).map(productToCampaign);
  const problemCount = campaigns.filter((campaign) => campaign.tone !== "good").length;
  const spendKop = parseMoneyToKop(adsKpi?.value) || sum(campaigns, (campaign) => campaign.spend_kop ?? 0);
  const revenueKop = parseMoneyToKop(revenueKpi?.value) || sum(campaigns, (campaign) => campaign.revenue_kop ?? 0);
  const acosBps = revenueKop > 0 ? Math.round((spendKop / revenueKop) * 10000) : 0;
  const roas = spendKop > 0 ? revenueKop / spendKop : 0;
  const adSignals = array(legacy.signals).filter((signal) =>
    `${signal.type ?? ""} ${signal.signal ?? ""} ${signal.action ?? ""}`.toLocaleLowerCase("ru-RU").includes("реклам"),
  );

  return {
    generated_at: new Date().toISOString(),
    sync: syncFromLegacy(legacy),
    hero: {
      title: problemCount
        ? `${problemCount} SKU требуют настройки рекламы`
        : "Реклама в рабочей зоне по ДРР",
      description: products.length
        ? `Смотрю товарные ДРР, выручку и запас за ${overview.period_label ?? "30 дней"}; короткий запас блокирует масштабирование.`
        : "Нужен отдельный WB ads endpoint для кампаний, пока беру операционные сигналы.",
      tone: problemCount > 0 ? "warn" : "good",
    },
    metrics: [
      {
        id: "ad_spend",
        label: "Расход",
        value: adsKpi?.value ?? "0 ₽",
        note: overviewDeltaNote(adsKpi),
        comparison_label: adsKpi?.note ?? "рекламные расходы",
        tone: problemCount > 0 ? "warn" : "good",
      },
      {
        id: "acos",
        label: "ДРР",
        value: formatPercent(acosBps),
        note: "цель до 11%",
        comparison_label: "расход / выручка",
        tone: acosBps > 1100 ? "warn" : "good",
      },
      {
        id: "roas",
        label: "ROAS",
        value: roas > 0 ? `${formatNumber(roas)}x` : "0x",
        note: "выручка / расход",
        comparison_label: "по периоду",
        tone: roas >= 8 ? "good" : roas >= 4 ? "info" : "warn",
      },
      {
        id: "ad_alerts",
        label: "Сигналы",
        value: `${Math.max(problemCount, adSignals.length)}`,
        note: products.length ? "из товарной аналитики" : "из операционной очереди",
        comparison_label: "требуют проверки",
        tone: Math.max(problemCount, adSignals.length) > 0 ? "warn" : "good",
      },
    ],
    campaigns: campaigns.length
      ? campaigns
      : adSignals.map((signal, index) => ({
          id: slug(signal.id ?? `${signal.type ?? "ad"}-${index}`),
          name: signal.article ? `${signal.type} / ${signal.article}` : signal.type ?? `Рекламный сигнал ${index + 1}`,
          spend_kop: 0,
          revenue_kop: 0,
          acos_bps: 0,
          signal: signal.signal ?? "проверить рекламу",
          recommended_action: signal.action ?? "Проверить",
          tone: toneFromPriority(signal.priority),
        })),
    funnel: overviewFunnel(overview),
    creative_tests: creativeSignalsFromProducts(adProducts),
  };
}

function toMetric(metric) {
  return {
    id: metric.key ?? slug(metric.label ?? "metric"),
    label: metric.label ?? metric.key ?? "Метрика",
    value: metric.value ?? "0",
    note: metricNote(metric),
    comparison_label: metricDetail(metric.key),
    tone: normalizeTone(metric.tone, metric.key === "attention" && Number(metric.value) > 0 ? "warn" : "info"),
  };
}

function metricNote(metric) {
  if (!metric.note) {
    return "";
  }

  if (metric.key === "orders" && metric.note.includes("₽")) {
    return `выручка ${metric.note}`;
  }

  if (metric.key === "sales" && metric.note.includes("₽")) {
    return `сумма ${metric.note}`;
  }

  return metric.note;
}

function overviewProducts(overview) {
  return array(overview?.products)
    .map((row) => ({
      nm_id: row.nm_id ?? "",
      article: row.article ?? "",
      orders: number(row.orders),
      sales: number(row.sales),
      revenue: number(row.revenue),
      drr: number(row.drr),
      stock: number(row.stock),
      days_of_stock: number(row.days_of_stock),
    }))
    .filter((product) => product.article || product.nm_id)
    .map((product) => ({
      ...product,
      label: product.article || String(product.nm_id),
      days_of_stock: Number.isFinite(product.days_of_stock) ? product.days_of_stock : 999,
    }));
}

function overviewKpi(overview, key) {
  return array(overview?.kpis).find((metric) => metric.key === key);
}

function overviewPeriodDays(overview) {
  const start = Date.parse(overview?.start ?? "");
  const end = Date.parse(overview?.end ?? "");
  if (Number.isFinite(start) && Number.isFinite(end) && end >= start) {
    return Math.round((end - start) / 86400000) + 1;
  }

  return 30;
}

function productToInventoryRow(product) {
  return {
    sku: product.article || String(product.nm_id),
    name: product.article || `WB ${product.nm_id}`,
    stock_days: product.days_of_stock,
    stock: product.stock,
    margin_bps: null,
    signal: stockSignal(product),
    tone: stockTone(product.days_of_stock),
  };
}

function productToReorderItem(product, periodDays, emergencyDays) {
  const targetDays = 21;
  const dailySales = periodDays > 0 ? product.sales / periodDays : 0;
  const recommendedQty = Math.max(0, Math.ceil(dailySales * targetDays - product.stock));

  return {
    sku: product.article || String(product.nm_id),
    name: product.article || `WB ${product.nm_id}`,
    stock_days: product.days_of_stock,
    recommended_qty: recommendedQty,
    warehouse: "WB",
    risk_label:
      product.days_of_stock <= emergencyDays
        ? `запас ${formatNumber(product.days_of_stock)} дня`
        : product.drr > 10
          ? `ДРР ${formatNumber(product.drr)}%`
          : "плановое пополнение",
    tone: stockTone(product.days_of_stock),
  };
}

function stockCoverage(products, emergencyDays) {
  if (!products.length) {
    return [];
  }

  const total = products.length;
  const watchedMax = Math.max(14, emergencyDays * 2);
  const buckets = [
    {
      label: `≤ ${formatNumber(emergencyDays)} дн.`,
      detail: "критический запас",
      count: products.filter((product) => product.days_of_stock <= emergencyDays).length,
      tone: "danger",
    },
    {
      label: `${formatNumber(emergencyDays)}-${formatNumber(watchedMax)} дн.`,
      detail: "под наблюдением",
      count: products.filter((product) => product.days_of_stock > emergencyDays && product.days_of_stock <= watchedMax).length,
      tone: "warn",
    },
    {
      label: `> ${formatNumber(watchedMax)} дн.`,
      detail: "можно масштабировать",
      count: products.filter((product) => product.days_of_stock > watchedMax).length,
      tone: "good",
    },
  ];

  return buckets.map((bucket) => ({
    label: bucket.label,
    detail: bucket.detail,
    value: `${bucket.count} SKU`,
    width: Math.max(4, Math.round((bucket.count / total) * 100)),
    tone: bucket.count > 0 ? bucket.tone : "info",
  }));
}

function stockSignal(product) {
  if (product.days_of_stock <= 3) {
    return "срочно";
  }
  if (product.days_of_stock <= 7) {
    return "поставка";
  }
  if (product.drr > 10) {
    return "реклама";
  }
  return "контроль";
}

function stockTone(daysOfStock) {
  if (daysOfStock <= 3) {
    return "danger";
  }
  if (daysOfStock <= 7) {
    return "warn";
  }
  return "info";
}

function productToCampaign(product) {
  const spendKop = Math.round(product.revenue * (product.drr / 100) * 100);
  const revenueKop = Math.round(product.revenue * 100);
  const acosBps = Math.round(product.drr * 100);
  const signal = adSignal(product);

  return {
    id: slug(`sku-${product.article || product.nm_id}`),
    name: `SKU ${product.article || product.nm_id}`,
    spend_kop: spendKop,
    revenue_kop: revenueKop,
    acos_bps: acosBps,
    signal: signal.label,
    recommended_action: signal.action,
    tone: signal.tone,
  };
}

function adSignal(product) {
  if (product.days_of_stock <= 3) {
    return { label: "запас блокирует рост", action: "Сначала пополнить", tone: "danger" };
  }
  if (product.days_of_stock <= 7) {
    return { label: "короткий запас", action: "Не масштабировать", tone: "warn" };
  }
  if (product.drr >= 11) {
    return { label: "ДРР выше цели", action: "Снизить ставки", tone: product.drr >= 18 ? "danger" : "warn" };
  }
  if (product.drr > 0 && product.drr <= 5 && product.days_of_stock > 14) {
    return { label: "есть запас для роста", action: "Добавить бюджет", tone: "good" };
  }
  return { label: "контроль эффективности", action: "Проверить", tone: "info" };
}

function overviewFunnel(overview) {
  return array(overview?.funnel).map((stage) => ({
    label: stage.label ?? "Шаг",
    value_text: stage.value_text ?? formatNumber(number(stage.value)),
    width_pct: number(stage.width_pct),
  }));
}

function creativeSignalsFromProducts(products) {
  return products
    .slice()
    .sort((a, b) => a.drr - b.drr || b.revenue - a.revenue)
    .slice(0, 3)
    .map((product) => ({
      label: product.article || `WB ${product.nm_id}`,
      value: `ДРР ${formatNumber(product.drr)}%`,
      winner: product.drr > 0 && product.drr <= 5 && product.days_of_stock > 14,
    }));
}

function toRevenueTrend(overview) {
  return array(overview?.chart?.rows)
    .slice(-12)
    .map((row) => ({
      time: row.x,
      revenue_kop: Math.round(number(row.revenue) * 100),
    }));
}

function overviewDeltaNote(metric) {
  if (typeof metric?.delta === "number") {
    const sign = metric.delta > 0 ? "+" : "";
    return `${sign}${formatNumber(metric.delta)}% к прошлому периоду`;
  }

  return metric?.note ?? "";
}

function toAction(signal, index) {
  if (!signal) {
    return {
      id: "no-signals",
      type: "summary",
      sku: "",
      title: "Нет критичных сигналов",
      issue: "Операционная очередь пуста",
      recommended_action: "Открыть обзор",
      impact_kop: 0,
      priority: "Средний",
      tone: "good",
    };
  }

  return {
    id: slug(signal.id ?? `${signal.priority ?? "signal"}-${signal.type ?? "type"}-${signal.article ?? index}`),
    type: signal.type ?? "signal",
    sku: signal.article ?? "",
    title: signal.article || signal.type || "Сигнал",
    issue: signal.signal ?? "Проверьте операционный сигнал",
    recommended_action: signal.action ?? "Добавить в план",
    impact_kop: null,
    priority: signal.priority,
    tone: toneFromPriority(signal.priority),
  };
}

function toInventoryWatch(legacy, overview) {
  const stockRows = array(legacy.inventory_watch).map(toInventoryRow);
  if (stockRows.length) {
    return stockRows;
  }

  const tabStockRows = array(legacy.tabs?.stock?.rows).map(toInventoryRow);
  if (tabStockRows.length) {
    return tabStockRows;
  }

  const overviewRows = overviewProducts(overview).map(productToInventoryRow);
  if (overviewRows.length) {
    return overviewRows;
  }

  return array(legacy.tabs?.procurement?.products_to_buy).map((item) => {
    const stockRaw = item.stock_days ?? item.days_of_stock;
    const stockDays = stockRaw === undefined || stockRaw === null || stockRaw === "" ? null : number(stockRaw);
    return {
      sku: item.sku ?? item.article ?? "SKU",
      name: item.name ?? item.product ?? "Товар",
      stock_days: stockDays,
      margin_bps: null,
      signal: item.action ?? "пополнение",
      tone: stockDays !== null && stockDays <= 3 ? "danger" : "warn",
    };
  });
}

function toInventoryRow(row) {
  const stockRaw = row.stock_days ?? row.days_of_stock ?? row["Запас, дней"] ?? row["Запас WB, дней"];
  const stockDays = stockRaw === undefined || stockRaw === null || stockRaw === "" ? null : number(stockRaw);
  const rawMargin = row.margin_bps ?? row["Маржа, %"];
  const marginValue = number(rawMargin);

  return {
    sku: row.sku ?? row.article ?? row["Артикул продавца"] ?? row.supplier_article ?? "SKU",
    name: row.name ?? row.product ?? row["Товар"] ?? row.product_name ?? "Товар",
    stock_days: stockDays,
    stock: row.stock === undefined || row.stock === null || row.stock === ""
      ? (row["Остаток"] === undefined || row["Остаток"] === null || row["Остаток"] === "" ? undefined : number(row["Остаток"]))
      : number(row.stock),
    margin_bps: row.margin_bps === undefined ? marginValue * 100 || null : marginValue || null,
    signal: row.signal ?? row["Сигнал"] ?? (stockDays === null ? "Нет данных от WB" : stockDays <= 3 ? "поставка" : "контроль"),
    tone: stockDays === null ? "info" : stockDays <= 3 ? "danger" : stockDays <= 7 ? "warn" : "good",
  };
}

function toLegacyTrend(input) {
  return array(input).map((row, index) => ({
    time: row.time ?? row.x ?? row.date ?? row.day ?? row.hour ?? index,
    revenue_rub: number(row.revenue_rub ?? row.revenue ?? row.value),
  }));
}

function toReorderItem(item) {
  const stockDays = number(item.stock_days ?? item.days_of_stock);
  const qty = number(item.recommended_qty ?? item.order_qty);

  return {
    sku: item.sku ?? item.article ?? "SKU",
    name: item.name ?? item.product ?? "Товар",
    stock_days: stockDays,
    recommended_qty: qty,
    warehouse: item.warehouse ?? "WB",
    risk_label: item.action ?? (stockDays <= 3 ? "риск дефицита" : "плановое пополнение"),
    tone: stockDays <= 3 ? "danger" : stockDays <= 7 ? "warn" : "info",
  };
}

function toProduction(legacy) {
  const shift = legacy.tabs?.shift;
  return [
    { stage: "Смена", status: shift?.planned > 0 ? "active" : "pending", value: `${shift?.material_covered ?? 0}/${shift?.planned ?? 0}` },
    { stage: "Задания", status: array(shift?.tasks).length ? "active" : "pending", value: `${array(shift?.tasks).length}` },
    { stage: "Блокеры", status: array(shift?.blocked_by_material).length ? "blocked" : "done", value: `${array(shift?.blocked_by_material).length}` },
  ];
}

function syncFromLegacy(legacy) {
  return {
    state: legacy.sync_stale ? "stale" : "fresh",
    source: "wb-api",
    updated_at: new Date().toISOString(),
    lag_minutes: minutesFromAttentionNote(legacy) ?? 0,
  };
}

function minutesFromAttentionNote(legacy) {
  const note = array(legacy.kpis).find((metric) => metric.key === "attention")?.note;
  const match = typeof note === "string" ? note.match(/WB:\s*(\d+)\s*мин/) : null;
  return match ? Number(match[1]) : null;
}

function metricDetail(key) {
  if (key === "orders") {
    return "заказы за сегодня";
  }
  if (key === "sales") {
    return "выкупы за сегодня";
  }
  if (key === "ads") {
    return "рекламные расходы";
  }
  if (key === "shift") {
    return "план и факт смены";
  }
  return "legacy /api/today";
}

function toneFromPriority(priority) {
  if (priority === "Критично") {
    return "danger";
  }
  if (priority === "Высокий" || priority === "Средний") {
    return "warn";
  }
  return "info";
}

function normalizeTone(tone, fallback) {
  return tone === "good" || tone === "warn" || tone === "danger" || tone === "info" ? tone : fallback;
}

function number(value) {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : 0;
  }

  if (typeof value !== "string") {
    return 0;
  }

  const normalized = value
    .replace(/\s+/g, "")
    .replace(",", ".")
    .replace(/[^0-9.-]/g, "");
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : 0;
}

function parseDisplayNumber(value) {
  return number(value);
}

function parseMoneyToKop(value) {
  return Math.round(parseDisplayNumber(value) * 100);
}

function formatNumber(value) {
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 }).format(value);
}

function formatPercent(valueBps) {
  return `${formatNumber(valueBps / 100)}%`;
}

function array(value) {
  return Array.isArray(value) ? value : [];
}

function sum(items, pick) {
  return items.reduce((total, item) => total + pick(item), 0);
}

function slug(value) {
  return String(value)
    .toLocaleLowerCase("ru-RU")
    .replace(/[^a-zа-я0-9]+/giu, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

function sendJson(response, status, payload) {
  response.writeHead(status, jsonHeaders);
  response.end(JSON.stringify(payload));
}

function readJsonBody(request) {
  return new Promise((resolve, reject) => {
    let body = "";
    request.setEncoding("utf8");
    request.on("data", (chunk) => {
      body += chunk;
      if (body.length > 100_000) {
        reject(new Error("Request body is too large"));
        request.destroy();
      }
    });
    request.on("end", () => {
      try {
        resolve(body ? JSON.parse(body) : null);
      } catch {
        reject(new Error("Invalid JSON body"));
      }
    });
    request.on("error", reject);
  });
}

function tenantFromRequest(request) {
  if (tenantSource === "host") {
    const forwardedHost = String(request.headers["x-forwarded-host"] ?? request.headers.host ?? "").split(":", 1)[0];
    const suffix = `.${tenantSubdomainPrefix}.${tenantParentDomain}`;
    const slug = forwardedHost.endsWith(suffix) ? forwardedHost.slice(0, -suffix.length) : "";
    return /^[a-zA-Z0-9-]{1,80}$/.test(slug) ? slug : "unknown-tenant";
  }

  const requested = request.headers["x-tenant-id"];
  return typeof requested === "string" && /^[a-zA-Z0-9_-]{1,80}$/.test(requested) ? requested : localTenantId;
}

function tenantFile(tenantId) {
  return resolve(actionPlanDir, `${tenantId}.json`);
}

async function getActionPlan(tenantId) {
  if (!actionPlans.has(tenantId)) {
    actionPlans.set(tenantId, await loadActionPlan(tenantFile(tenantId)));
  }
  return actionPlans.get(tenantId);
}

async function loadActionPlan(filePath) {
  try {
    const stored = JSON.parse(await readFile(filePath, "utf8"));
    return Array.isArray(stored) ? stored : [];
  } catch {
    return [];
  }
}

async function saveActionPlan(tenantId, items) {
  const filePath = tenantFile(tenantId);
  await mkdir(dirname(filePath), { recursive: true });
  const temporaryFile = `${filePath}.tmp`;
  await writeFile(temporaryFile, JSON.stringify(items, null, 2), "utf8");
  await rename(temporaryFile, filePath);
}
