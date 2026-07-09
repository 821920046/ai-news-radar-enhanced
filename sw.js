/* AI News Radar — Service Worker（离线快照 + 静态资源缓存，纯前端免费）
 * 数据 JSON：网络优先（保证“实时”），失败回退缓存（离线可看上次快照）
 * 预渲染 HTML / 导航：网络优先（保证每小时构建的首屏与 SEO 内容最新），失败回退缓存
 * 其它静态资源：缓存优先（秒开）
 */
const CACHE = 'ai-news-radar-v2';
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

function networkFirst(event, req, fallbackToShell) {
  event.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
        return res;
      })
      .catch(() =>
        caches.match(req).then((hit) => hit || (fallbackToShell ? caches.match('./index.html') : undefined))
      )
  );
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // 仅缓存同源

  // 导航 / 预渲染 HTML：网络优先，失败回退缓存（离线仍可看上次快照）
  const isHTML =
    req.mode === 'navigate' ||
    url.pathname === '/' ||
    url.pathname.endsWith('/') ||
    url.pathname.endsWith('.html');
  if (isHTML) {
    networkFirst(event, req, true);
    return;
  }

  // 数据 JSON：网络优先，失败回退缓存
  if (url.pathname.endsWith('.json')) {
    networkFirst(event, req, false);
    return;
  }

  // 其它静态资源：缓存优先
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
