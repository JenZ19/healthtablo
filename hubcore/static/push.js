// Подписка на уведомления. Вешается на кнопку в настройках.
//
// Разрешение запрашивается только по нажатию: браузеры давно блокируют
// автоматический запрос, а iOS вдобавок требует, чтобы страница была
// добавлена на домашний экран.

(function () {
  const btn = document.getElementById('push-toggle');
  if (!btn) return;
  const status = document.getElementById('push-status');

  const say = (text, kind) => {
    if (!status) return;
    status.textContent = text;
    status.className = 'push-status' + (kind ? ' ' + kind : '');
  };

  const supported = 'serviceWorker' in navigator && 'PushManager' in window;
  const secure = window.isSecureContext;
  const standalone = window.navigator.standalone === true ||
                     window.matchMedia('(display-mode: standalone)').matches;
  const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);

  if (!secure) {
    btn.disabled = true;
    say('Нужен защищённый адрес (https). По обычному http браузер уведомления не разрешает.', 'warn');
    return;
  }
  if (!supported) {
    btn.disabled = true;
    say('Этот браузер не умеет пуш-уведомления.', 'warn');
    return;
  }
  if (isIOS && !standalone) {
    btn.disabled = true;
    say('На айфоне сначала добавьте хаб на домашний экран: «Поделиться» → «На экран «Домой»», и откройте оттуда.', 'warn');
    return;
  }

  const b64ToBytes = (b64) => {
    const pad = '='.repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  };

  const refresh = async () => {
    const reg = await navigator.serviceWorker.getRegistration('/');
    const sub = reg && (await reg.pushManager.getSubscription());
    if (sub) {
      btn.textContent = 'Отключить уведомления';
      btn.dataset.on = '1';
      say('Уведомления включены на этом устройстве.', 'ok');
    } else {
      btn.textContent = 'Включить уведомления';
      delete btn.dataset.on;
      say('Уведомления на этом устройстве выключены.');
    }
  };

  const enable = async () => {
    say('Спрашиваю разрешение…');
    const perm = await Notification.requestPermission();
    if (perm !== 'granted') {
      say('Разрешение не выдано. Включить можно в настройках браузера.', 'warn');
      return;
    }
    const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    await navigator.serviceWorker.ready;
    const key = await (await fetch('/push/key')).text();
    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: b64ToBytes(key.trim()),
    });
    await fetch('/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subscription: sub, label: navigator.userAgent.slice(0, 80) }),
    });
    await refresh();
  };

  const disable = async () => {
    const reg = await navigator.serviceWorker.getRegistration('/');
    const sub = reg && (await reg.pushManager.getSubscription());
    if (sub) {
      await fetch('/push/unsubscribe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ endpoint: sub.endpoint }),
      });
      await sub.unsubscribe();
    }
    await refresh();
  };

  btn.addEventListener('click', async () => {
    btn.disabled = true;
    try {
      await (btn.dataset.on ? disable() : enable());
    } catch (e) {
      say('Не получилось: ' + e.message, 'warn');
    } finally {
      btn.disabled = false;
    }
  });

  navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(refresh).catch(() => refresh());
})();
