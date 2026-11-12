const CACHE_NAME = 'renaven-cache-v1';
const urlsToCache = [
  '/',
  '/static/css/style.css' // Si tienes un css principal, agrégalo aquí
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(urlsToCache))
  );
});

self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request)
      .then(response => response || fetch(event.request))
  );
});