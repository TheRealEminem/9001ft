// 9001ft Ladder service worker.
//
// Keeps only the app shell below, so an installed app can show a friendly
// offline page. Everything else (articles, Reader pages, the progress stream,
// settings, anything posted) goes straight to the network and is never stored.

const CACHE = "ladder-shell-v1";
const SHELL = [
  "/offline",
  "/banner.webp",
  "/favicon.ico",
  "/manifest.webmanifest",
  "/static/app.js",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/maskable-192.png",
  "/icons/maskable-512.png",
  "/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL.map((url) => new Request(url, { cache: "reload" }))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key.startsWith("ladder-shell-") && key !== CACHE).map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Pages: always from the network. Only when that fails, the offline page.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match("/offline").then((page) => page || Response.error()))
    );
    return;
  }

  // The shell's own files: network first, the kept copy when offline.
  if (url.search === "" && SHELL.includes(url.pathname)) {
    event.respondWith(fetch(request).catch(() => caches.match(url.pathname).then((hit) => hit || Response.error())));
  }
  // Anything else: not touched, not stored.
});
