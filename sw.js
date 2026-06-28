/* AI News Radar — Service Worker（离线快照 + 静态资源缓存，纯前端免费）
 * 数据 JSON：网络优先（保证“实时”），失败回退缓存（离线可看上次快照）
 * 静态资源：缓存优先（秒开）
 */
const CACHE = 'ai-news-radar-v1';
const SHELL = [
  './',
  './index.html',
  './assets/app.js',
  './assets/tailwind.min.css',
  './manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // 仅缓存同源

  // 数据 JSON：网络优先，失败回退缓存
  if (url.pathname.endsWith('.json')) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  // 静态资源：缓存优先
  event.respondWith(
    caches.match(req).then(
      (hit) =>
        hit ||
        fetch(req)
          .then((res) => {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy));
            return res;
          })
          .catch(() => hit)
    )
  );
});
