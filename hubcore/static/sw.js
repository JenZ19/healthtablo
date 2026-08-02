// Обработчик пуш-уведомлений.
//
// Живёт отдельным файлом в корне сайта, потому что service worker может
// управлять только теми страницами, что лежат не выше него: положить его
// в /static значило бы, что подписаться сможет только /static.

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let data = { title: 'Хаб здоровья', body: '', url: '/' };
  try {
    if (event.data) data = Object.assign(data, event.data.json());
  } catch (e) {
    if (event.data) data.body = event.data.text();
  }
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/apple-touch-icon.png',
      badge: '/apple-touch-icon.png',
      data: { url: data.url || '/' },
      // Одна метка на вид напоминания: второе уведомление про те же
      // таблетки заменяет первое, а не копится стопкой на экране.
      tag: data.title,
      renotify: false,
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((list) => {
      // Если хаб уже открыт — переводим на нужную страницу, а не плодим
      // ещё одно окно поверх существующего.
      for (const client of list) {
        if ('focus' in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    })
  );
});
