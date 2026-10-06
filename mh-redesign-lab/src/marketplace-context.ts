/** Server-authorized marketplace context. Backend independently checks every grant. */
import { clearShopContext, scopedFetch } from "./selected-shop";

export type MarketplaceSession = { marketplace: "wb" | "ozon"; role: string; can_write: boolean; is_demo: boolean; authorized: boolean; shared_routes: Array<{ method: string; path: string }> };
const roles = new Set(["owner", "manager", "viewer", "technologist", "cutter", "seamstress", "warehouse", "integration_reader"]);
const sharedPrefixes = ["/api/v1/manufacturing-costs", "/api/v1/cashflow", "/api/v1/cash-accounts", "/api/v1/production-workspace/workcenters"];
const ozonPrefixes = ["/api/v1/ozon-accounting", "/api/v1/ozon-cabinet", "/api/v1/ozon-insights", "/api/v1/ozon-prices"];
const neutral = new Set(["/api/v1/session", "/api/v1/health", "/healthz", "/_stcore/health"]);
let current: MarketplaceSession | null = null;
let epoch = 0;
const controllers = new Set<AbortController>();
const retries = new Map<string, string>();
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const prefix = (path: string, base: string) => path === base || path.startsWith(base + "/");
export const isOzonPath = (path: string) => ozonPrefixes.some(base => prefix(path, base)) || path === "/api/v1/settings/ozon";
function routeMatches(pattern: string, path: string) {
  const expected = pattern.split("/"), actual = path.split("/");
  return expected.length === actual.length && expected.every((part, index) => /^\{[a-z_]+\}$/.test(part) ? /^[a-zA-Z0-9_-]+$/.test(actual[index]) : part === actual[index]);
}

export function validateMarketplaceSession(raw: unknown): MarketplaceSession {
  if (!raw || typeof raw !== "object") throw new Error("Контекст маркетплейса не подтверждён");
  const value = raw as MarketplaceSession;
  if (!["wb", "ozon"].includes(value.marketplace) || !roles.has(value.role) || typeof value.can_write !== "boolean" || typeof value.is_demo !== "boolean" || value.authorized !== true || !Array.isArray(value.shared_routes)
      || value.can_write && (value.is_demo || !["owner", "manager"].includes(value.role)) || value.marketplace === "ozon" && value.role === "integration_reader") throw new Error("Контекст маркетплейса не подтверждён");
  const seen = new Set<string>();
  for (const route of value.shared_routes) {
    if (!route || typeof route.path !== "string" || !["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"].includes(route.method) || !sharedPrefixes.some(base => prefix(route.path, base)) || !/^\/api\/v1\/[a-z0-9/_{}-]+$/.test(route.path) || value.is_demo && !["GET", "HEAD", "OPTIONS"].includes(route.method)) throw new Error("Общие маршруты не подтверждены");
    const key = route.method + " " + route.path;
    if (seen.has(key)) throw new Error("Общие маршруты не подтверждены");
    seen.add(key);
  }
  return value;
}

export function currentMarketplace() { return current; }
export function clearMarketplaceContext() {
  for (const controller of controllers) controller.abort();
  controllers.clear(); retries.clear(); epoch++; current = null;
  clearShopContext();
}
export function selectMarketplace(session: MarketplaceSession) {
  const checked = validateMarketplaceSession(session);
  clearMarketplaceContext();
  const shared = checked.shared_routes.map(route => Object.freeze({ ...route })); Object.freeze(shared);
  current = Object.freeze({ ...checked, shared_routes: shared });
  return current;
}
function stillCurrent(version: number) { if (epoch !== version) throw new DOMException("Маркетплейс изменён", "AbortError"); }

export async function marketplaceFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const session = current;
  if (!session) throw new Error("Контекст маркетплейса не подтверждён");
  const original = input instanceof Request ? input : null;
  const url = new URL(original?.url ?? String(input), window.location.origin);
  if (url.origin !== window.location.origin) throw new Error("Недопустимый адрес кабинета");
  const path = url.pathname.replace(/^\/tenant-api(?=\/|$)/, "");
  const method = (init.method ?? original?.method ?? "GET").toUpperCase();
  if (session.marketplace === "wb") {
    if (isOzonPath(path)) throw new Error("Выберите кабинет Ozon");
    return scopedFetch(input, init);
  }
  const shared = session.shared_routes.some(route => route.method === method && routeMatches(route.path, path));
  const access = path === "/api/v1/marketplace-context/ozon/access" && session.role === "owner" && ["GET", "PUT"].includes(method);
  if (!isOzonPath(path) && !shared && !access && !(neutral.has(path) && ["GET", "HEAD", "OPTIONS"].includes(method))) throw new Error("Маршрут не относится к кабинету Ozon");
  const write = !["GET", "HEAD", "OPTIONS"].includes(method);
  if (write && (session.is_demo || !session.can_write && !shared)) throw new Error("Кабинет Ozon доступен только для просмотра");
  const headers = new Headers(init.headers ?? original?.headers);
  if (headers.has("X-MH-Marketplace") && headers.get("X-MH-Marketplace") !== "ozon") throw new Error("Маркетплейс запроса не совпадает с кабинетом");
  if (headers.has("X-MH-Shop-ID") && headers.get("X-MH-Shop-ID") !== "0" || headers.has("X-MH-Shop-Revision")) throw new Error("Ozon требует основной контекст кабинета");
  const queryShop = url.searchParams.getAll("shop_id"), queryMarketplace = url.searchParams.getAll("marketplace");
  if (queryShop.length > 1 || queryShop.length === 1 && queryShop[0] !== "0" || queryMarketplace.length > 1 || queryMarketplace.length === 1 && queryMarketplace[0] !== "ozon") throw new Error("Контекст URL не совпадает с кабинетом Ozon");
  if (typeof init.body === "string") {
    try {
      const body = JSON.parse(init.body);
      if (body && typeof body === "object" && ("marketplace" in body && body.marketplace !== "ozon" || "shop_id" in body && (typeof body.shop_id !== "number" || body.shop_id !== 0))) throw new Error("Контекст данных не совпадает с кабинетом Ozon");
    } catch (error) { if (!(error instanceof SyntaxError)) throw error; }
  }
  headers.set("X-MH-Marketplace", "ozon"); headers.set("X-MH-Shop-ID", "0");
  let replayIdentity: string | null = null;
  if (write) {
    replayIdentity = typeof init.body === "string" || init.body == null && !original ? JSON.stringify([session.marketplace, session.role, method, path, url.search, init.body ?? null]) : null;
    const key = headers.get("X-MH-Request-Key") ?? (replayIdentity ? retries.get(replayIdentity) : null) ?? crypto.randomUUID();
    if (!uuid.test(key)) throw new Error("Некорректный ключ действия");
    if (replayIdentity) retries.set(replayIdentity, key);
    headers.set("X-MH-Request-Key", key);
  }
  const version = epoch, controller = new AbortController(); controllers.add(controller);
  const callerSignal = init.signal ?? original?.signal;
  const abort = () => controller.abort(); callerSignal?.addEventListener("abort", abort, { once: true });
  if (callerSignal?.aborted) controller.abort();
  const finish = () => { controllers.delete(controller); callerSignal?.removeEventListener("abort", abort); };
  try {
    const response = await globalThis.fetch(input, { ...init, headers, signal: controller.signal }); stillCurrent(version);
    if (response.ok && (response.headers.get("X-MH-Marketplace") !== "ozon" || response.headers.has("X-MH-Shop-ID") && response.headers.get("X-MH-Shop-ID") !== "0")) throw new Error("Источник ответа не соответствует кабинету Ozon");
    if (response.body === null) finish();
    const guard = (source: Response): Response => new Proxy(source, { get(target, property) {
      const value = Reflect.get(target, property, target);
      if (property === "clone") return () => { stillCurrent(version); return guard(target.clone()); };
      if (["json", "text", "blob", "arrayBuffer", "formData"].includes(String(property))) return async () => {
        try { stillCurrent(version); const body = await value.call(target); stillCurrent(version); if (response.ok && replayIdentity) retries.delete(replayIdentity); return body; }
        finally { finish(); }
      };
      return typeof value === "function" ? value.bind(target) : value;
    } });
    return guard(response);
  } catch (error) { finish(); throw error; }
}
