// 離線快取：App 外殼先存起來；資料（data.json）有網路就抓最新的，沒網路就用上次存的
const CACHE = 'radar-v4';  // 改了圖示或網頁外殼就加 1，已安裝的 App 才會換新
const SHELL = ['./', 'index.html', 'manifest.webmanifest', 'icons/icon-192.png', 'icons/icon-512.png', 'icons/maskable-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  // 網路優先：拿到就更新快取，失敗才用快取（data.json 帶的 ?時間戳 不算在快取鍵裡）
  e.respondWith(
    fetch(e.request).then(res => {
      if (res.ok) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(url.pathname, copy));
      }
      return res;
    }).catch(() => caches.match(url.pathname).then(r => r || caches.match('./')))
  );
});
