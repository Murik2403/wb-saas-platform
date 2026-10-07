const CACHE_PREFIX = "marketshelper-shell-";
const CACHE_NAME = `${CACHE_PREFIX}v3`;
const MAX_ASSETS = 64;
function cacheable(response) {
  return response.ok && response.type === "basic" && !response.redirected
    && !/private|no-store/i.test(response.headers?.get("cache-control") || "");
}
const SHELL = ["/offline.html", "/manifest.webmanifest", "/icons/marketshelper.svg", "/icons/marketshelper-192.png", "/icons/marketshelper-512.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then(async (cache) => {
    for (const path of SHELL) {
      const response = await fetch(path, { cache: "reload" });
      if (cacheable(response)) await cache.put(path, response);
    }
  }));
});
// Existing tabs retain their input; activation is requested by an explicit
// update button. First installations activate normally without reloading.
self.addEventListener("message", (event) => {
  if (event.data?.type === "MH_ACTIVATE_UPDATE") self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then(async (keys) => {
    const previous = keys.filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME);
    // Keep one previous shell for open older tabs, bounded by MAX_ASSETS.
    await Promise.all(previous.slice(0, -1).map((key) => caches.delete(key)));
    await self.clients.claim();
  }));
});

async function cachedStatic(request) {
  const keys = (await caches.keys()).filter((key) => key.startsWith(CACHE_PREFIX));
  for (const key of keys.reverse()) {
    const hit = await (await caches.open(key)).match(request);
    if (hit) return hit;
  }
}
async function storeStatic(request, response) {
  const cache = await caches.open(CACHE_NAME);
  await cache.put(request, response);
  const keys = await cache.keys();
  const disposable = keys.filter((key) => !SHELL.includes(new URL(key.url).pathname));
  await Promise.all(disposable.slice(0, Math.max(0, keys.length - MAX_ASSETS)).map((key) => cache.delete(key)));
}
self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(async () => (await caches.open(CACHE_NAME)).match("/offline.html")));
    return;
  }
  // An extension alone is not proof a URL is public: private downloads may
  // end in .js/.png. Only known public bootstrap and bundled asset paths qualify.
  const isStatic = SHELL.includes(url.pathname) || url.pathname.startsWith("/icons/")
    || (url.pathname.startsWith("/assets/") && /\.(?:css|js|mjs|svg|png|webp|woff2?)$/i.test(url.pathname));
  if (!isStatic || url.search) return;
  event.respondWith(fetch(request).then(async (response) => {
    if (cacheable(response)) {
      event.waitUntil(storeStatic(request, response.clone()).catch(() => {}));
      return response;
    }
    return (await cachedStatic(request)) || response;
  }).catch(() => cachedStatic(request)));
});
