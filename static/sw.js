// Service worker: caches the app shell so it opens instantly (and offline), keeps the
// last menu responses so the app still shows something without signal, and shows
// push notifications (meal digests) sent by server.py.
const SHELL = "milton-shell-v6-fresh";
const DATA = "milton-data-v1";
const SHELL_FILES = ["./", "./index.html", "./manifest.webmanifest", "./icons/icon-192.png", "./icons/icon-512.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(SHELL_FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => ![SHELL, DATA].includes(k)).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.origin === location.origin && (url.pathname.startsWith("/api/") || url.pathname.startsWith("/data/"))) {
    // network first, fall back to the last copy
    e.respondWith(fetch(e.request).then(res => { const copy = res.clone(); caches.open(DATA).then(c => c.put(e.request, copy)); return res; })
      .catch(() => caches.match(e.request).then(r => r || new Response(JSON.stringify({ error: "offline" }), { status: 503, headers: { "Content-Type": "application/json" } }))));
    return;
  }
  if (url.origin === location.origin) {
    const isPage = e.request.mode === "navigate" || url.pathname === "/" || url.pathname.endsWith("/index.html");
    if (isPage) {
      // the page itself: network first so a deploy shows on the next open; cached copy when offline
      e.respondWith(fetch(e.request).then(res => { if (res.ok) caches.open(SHELL).then(c => c.put(e.request, res.clone())); return res; })
        .catch(() => caches.match(e.request).then(r => r || caches.match("./index.html"))));
      return;
    }
    // icons, manifest, photos: cache first, refresh in the background
    e.respondWith(caches.match(e.request).then(cached => {
      const net = fetch(e.request).then(res => { if (res.ok) caches.open(SHELL).then(c => c.put(e.request, res.clone())); return res; }).catch(() => cached);
      return cached || net;
    }));
    return;
  }
  // fonts and photos: cache as they arrive, keeping the newest few hundred
  e.respondWith(caches.match(e.request).then(cached => cached || fetch(e.request).then(res => { if (res.ok || res.type === "opaque") caches.open(DATA).then(c => c.put(e.request, res.clone()).then(() => trim(c, 320))); return res; })));
});
async function trim(cache, max) {
  const keys = await cache.keys();
  for (const k of keys.slice(0, Math.max(0, keys.length - max))) await cache.delete(k);
}

// ---- push notifications ----
self.addEventListener("push", e => {
  let data = {};
  try { data = e.data ? e.data.json() : {}; } catch { data = { title: "Forbrary", body: e.data ? e.data.text() : "" }; }
  const title = data.title || "Forbrary";
  e.waitUntil(self.registration.showNotification(title, {
    body: data.body || "",
    icon: "./icons/icon-192.png",
    badge: "./icons/icon-192.png",
    tag: data.tag || "milton-menu",
    renotify: !!data.tag,
    data: { url: data.url || "./#today" },
  }));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const target = new URL(e.notification.data?.url || "./#today", self.location.href).href;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(list => {
    const open = list.find(c => c.url.startsWith(self.registration.scope));
    if (open) { open.navigate(target); return open.focus(); }
    return self.clients.openWindow(target);
  }));
});
// Keep the subscription alive if the browser rotates it.
self.addEventListener("pushsubscriptionchange", e => {
  e.waitUntil(self.registration.pushManager.subscribe(e.oldSubscription?.options || { userVisibleOnly: true })
    .then(sub => fetch("/api/push/subscribe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ subscription: sub.toJSON(), renewed: true }) })).catch(() => {}));
});
