"""Пуш-уведомления: напоминания о таблетках, профилактике и пересдаче.

Как это устроено в вебе, коротко. Браузер выдаёт странице «подписку» —
адрес на сервере Apple или Google плюс два ключа. Сервер шифрует
сообщение этими ключами и отправляет по адресу; посредник передаёт его
устройству, не имея возможности прочитать. Подписывается отправка парой
ключей VAPID, чтобы посредник знал, что письмо от нас.

Что из этого следует практически:

- **Нужен HTTPS.** Без него браузер вообще не даёт подписаться: пуши
  живут только в защищённом соединении. Исключение — localhost.
- **На айфоне нужна установка на домашний экран.** Safari в обычной
  вкладке подписаться не даст, это ограничение самой iOS.
- **Разрешение спрашивается один раз** и по нажатию, а не само.

Тексты уведомлений намеренно скупые: «Пряник — пора дать таблетку»
вместо диагнозов и показателей. Уведомление видно на заблокированном
экране, через плечо и в чужих руках.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Кому «от кого» приходит уведомление. Нужен любой контакт владельца
# ключа — сервисы Apple и Google требуют его в подписи VAPID.
VAPID_SUBJECT = "mailto:hub@localhost"


def keys_path() -> Path:
    from . import db as db_module

    return Path(db_module.DATA_DIR) / "push_keys.json"


def load_keys() -> dict:
    path = keys_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def ensure_keys() -> dict:
    """Создать пару ключей VAPID, если её ещё нет.

    Ключи создаются один раз и переживают перезапуски: со сменой ключа
    все выданные подписки становятся недействительными, и каждому
    устройству пришлось бы подписываться заново.
    """
    keys = load_keys()
    if keys.get("private") and keys.get("public"):
        return keys

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    raw_public = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    import base64

    public_b64 = base64.urlsafe_b64encode(raw_public).decode().rstrip("=")

    keys = {"private": private_pem, "public": public_b64}
    path = keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keys, indent=2), "utf-8")
    path.chmod(0o600)
    logger.info("созданы ключи VAPID: %s", path)
    return keys


def public_key() -> str:
    return ensure_keys()["public"]


# ---------------------------------------------------------------------------
# Подписки
# ---------------------------------------------------------------------------

def save_subscription(conn, sub: dict, label: str | None = None) -> None:
    keys = sub.get("keys") or {}
    conn.execute(
        """INSERT INTO push_subscriptions (endpoint, p256dh, auth, label, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(endpoint) DO UPDATE
             SET p256dh=excluded.p256dh, auth=excluded.auth, failures=0""",
        (sub["endpoint"], keys.get("p256dh", ""), keys.get("auth", ""),
         label, dt.datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def forget_subscription(conn, endpoint: str) -> None:
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
    conn.commit()


def subscriptions(conn) -> list:
    return conn.execute("SELECT * FROM push_subscriptions ORDER BY id").fetchall()


# ---------------------------------------------------------------------------
# Отправка
# ---------------------------------------------------------------------------

def send_to_all(conn, title: str, body: str, url: str = "/") -> tuple[int, int]:
    """Разослать уведомление на все подписанные устройства.

    Возвращает (доставлено, отвалилось). Подписка, на которую сервис
    ответил «такой больше нет», удаляется сразу: устройство сбросили или
    приложение удалили с экрана, и хранить её незачем.
    """
    from pywebpush import WebPushException, webpush

    keys = ensure_keys()
    payload = json.dumps({"title": title, "body": body, "url": url}, ensure_ascii=False)
    ok = failed = 0

    for sub in subscriptions(conn):
        info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        try:
            webpush(
                subscription_info=info,
                data=payload,
                vapid_private_key=keys["private"],
                vapid_claims={"sub": VAPID_SUBJECT},
                timeout=15,
            )
            conn.execute(
                "UPDATE push_subscriptions SET last_ok=?, failures=0 WHERE id=?",
                (dt.datetime.now().isoformat(timespec="seconds"), sub["id"]),
            )
            ok += 1
        except WebPushException as exc:
            code = getattr(exc.response, "status_code", None)
            if code in (404, 410):
                conn.execute("DELETE FROM push_subscriptions WHERE id=?", (sub["id"],))
                logger.info("подписка %s больше не существует, удалена", sub["id"])
            else:
                conn.execute(
                    "UPDATE push_subscriptions SET failures=failures+1 WHERE id=?", (sub["id"],)
                )
                logger.warning("не отправилось на подписку %s: %s", sub["id"], exc)
            failed += 1
        except Exception as exc:  # сеть, таймаут — не роняем весь обход
            logger.warning("сбой отправки на подписку %s: %s", sub["id"], exc)
            failed += 1

    conn.commit()
    return ok, failed


def already_sent(conn, kind: str, key: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM push_sent WHERE kind=? AND key=?", (kind, key)
    ).fetchone() is not None


def note_sent(conn, kind: str, key: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO push_sent (kind, key, sent_at) VALUES (?, ?, ?)",
        (kind, key, dt.datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def forget_old_sent(conn, days: int = 120) -> None:
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    conn.execute("DELETE FROM push_sent WHERE sent_at < ?", (cutoff,))
    conn.commit()
