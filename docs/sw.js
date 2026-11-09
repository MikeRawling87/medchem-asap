// App shell cache-first; data network-first (falls back to last copy when offline).
const V = "mca-shell-v7";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(V).then(c => c.addAll(SHELL))); self.skipWaiting(); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V && k !== "mca-data").map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (u.origin !== location.origin || e.request.method !== "GET") return;
  if (u.pathname.endsWith("/data.json") || u.pathname.endsWith("/archive/index.json")) {
    e.respondWith(fetch(e.request).then(r => {
      const copy = r.clone(); caches.open("mca-data").then(c => c.put(u.pathname, copy)); return r;
    }).catch(() => caches.open("mca-data").then(c => c.match(u.pathname))));
    return;
  }
  if (/\/archive\/\d{4}-\d{2}\.json$/.test(u.pathname)) {   // archive months: instant from cache, refreshed in background
    e.respondWith(caches.open("mca-data").then(async c => {
      const hit = await c.match(e.request);
      const net = fetch(e.request).then(r => { if (r.ok) c.put(e.request, r.clone()); return r; }).catch(() => hit);
      return hit || net;
    }));
    return;
  }
  if (u.pathname.includes("/img/")) {
    e.respondWith(caches.open("mca-data").then(async c => (await c.match(e.request)) ||
      fetch(e.request).then(r => { if (r.ok) c.put(e.request, r.clone()); return r; })));
    return;
  }
  // shell: network first so updates show up, cache when offline
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok) { const copy = r.clone(); caches.open(V).then(c => c.put(e.request, copy)); }
    return r;
  }).catch(() => caches.match(e.request).then(m => m || caches.match("index.html"))));
});
