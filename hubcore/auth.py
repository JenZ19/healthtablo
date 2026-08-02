"""Вход по логину и паролю — для случая, когда хаб стоит не на своём ноутбуке.

Устройство сделано под одно требование: не тащить в проект ни одной внешней
зависимости ради авторизации. Всё собрано из стандартной библиотеки —
scrypt для пароля, hmac для подписи cookie.

Три вещи, которые здесь важнее удобства:

1. **Пароль не хранится.** В файле лежит только scrypt-хеш с солью. Даже
   получив файл настроек, пароль из него не достать — придётся перебирать,
   а scrypt специально сделан медленным и жадным до памяти.

2. **Cookie подписана, а не «угадана».** Внутри — имя пользователя, срок
   и HMAC-подпись серверным секретом. Подделать её, не зная секрета,
   нельзя; подобрать — тоже.

3. **Без пароля хаб не выходит наружу.** Если хаб просят слушать не
   127.0.0.1, а внешний адрес, и при этом пароль не задан — запуск
   прерывается. Это единственная защита от «поставил на сервер, а
   авторизацию включу потом».

Локально, на 127.0.0.1, вход по умолчанию выключен: хаб как был домашним
приложением, так и остался, лишний экран с паролем ему ни к чему.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

# Параметры scrypt. Взяты с запасом: проверка пароля занимает ~0,1 с, что
# незаметно при входе раз в сутки, но делает перебор дорогим.
_SCRYPT_N = 2 ** 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32
# OpenSSL по умолчанию не даёт scrypt взять больше 32 МБ, а при N=2^15
# нужно ровно столько плюс накладные — без явного лимита падает с
# «memory limit exceeded».
_MAXMEM = 96 * 1024 * 1024

SESSION_COOKIE = "hub_session"
SESSION_TTL = 30 * 24 * 3600  # месяц: хаб семейный, каждый день логиниться незачем

# Защита от подбора: после стольких неудач с одного адреса — пауза.
MAX_ATTEMPTS = 7
LOCKOUT_SECONDS = 15 * 60


def config_path() -> Path:
    """Файл с настройками входа. Рядом с базой, не в коде и не в репозитории."""
    env = os.environ.get("HUB_AUTH_FILE")
    if env:
        return Path(env)
    from . import db as db_module

    return Path(db_module.DB_PATH).resolve().parent / "auth.json"


# ---------------------------------------------------------------------------
# Пароль
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    key = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
        dklen=_KEY_LEN, maxmem=_MAXMEM,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, key_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        key = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt_b64),
            n=int(n), r=int(r), p=int(p), dklen=len(_unb64(key_b64)), maxmem=_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    # Сравнение с постоянным временем: обычное == подсказывает длину
    # совпавшего префикса тому, кто умеет измерять задержки.
    return hmac.compare_digest(key, _unb64(key_b64))


def _norm_user(name: object) -> bytes:
    return str(name or "").strip().lower().encode("utf-8")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

def load_config() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(username: str, password: str) -> Path:
    """Записать логин и хеш пароля. Файл доступен только владельцу."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    cfg["username"] = username
    cfg["password_hash"] = hash_password(password)
    cfg.setdefault("secret", secrets.token_urlsafe(48))
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    path.chmod(0o600)
    return path


def enabled() -> bool:
    cfg = load_config()
    return bool(cfg.get("username") and cfg.get("password_hash"))


def check_credentials(username: str, password: str) -> bool:
    cfg = load_config()
    if not cfg.get("username") or not cfg.get("password_hash"):
        return False
    # Имя тоже сравниваем в постоянном времени и не сообщаем, что именно
    # не совпало: иначе перебор сначала находит логин, потом пароль.
    # Сравнение именно в байтах: compare_digest на строках падает, если в
    # них есть хоть один не-ASCII знак, — кириллический логин ронял вход.
    ok_user = hmac.compare_digest(_norm_user(username), _norm_user(cfg["username"]))
    ok_pass = verify_password(password, cfg["password_hash"])
    return ok_user and ok_pass


# ---------------------------------------------------------------------------
# Сессия
# ---------------------------------------------------------------------------

def issue_token(username: str) -> str:
    cfg = load_config()
    secret = cfg.get("secret", "")
    payload = f"{username}|{int(time.time()) + SESSION_TTL}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{_b64(payload.encode())}.{_b64(sig)}"


def valid_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    cfg = load_config()
    secret = cfg.get("secret", "")
    if not secret:
        return False
    payload_b64, sig_b64 = token.rsplit(".", 1)
    try:
        payload = _unb64(payload_b64)
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig_b64)):
            return False
        username, expires = payload.decode().rsplit("|", 1)
    except (ValueError, TypeError, UnicodeDecodeError):
        return False
    if int(expires) < time.time():
        return False
    return hmac.compare_digest(_norm_user(username), _norm_user(cfg.get("username", "")))


# ---------------------------------------------------------------------------
# Защита от подбора
# ---------------------------------------------------------------------------

_attempts: dict[str, list[float]] = {}


def locked_out(ip: str) -> int:
    """Сколько секунд осталось ждать этому адресу. Ноль — можно пробовать."""
    now = time.time()
    tries = [t for t in _attempts.get(ip, []) if now - t < LOCKOUT_SECONDS]
    _attempts[ip] = tries
    if len(tries) < MAX_ATTEMPTS:
        return 0
    return int(LOCKOUT_SECONDS - (now - tries[0])) + 1


def note_failure(ip: str) -> None:
    _attempts.setdefault(ip, []).append(time.time())


def note_success(ip: str) -> None:
    _attempts.pop(ip, None)


# ---------------------------------------------------------------------------
# Проверка перед выходом наружу
# ---------------------------------------------------------------------------

def guard_public_bind(host: str) -> None:
    """Не дать поднять хаб на внешнем адресе без пароля.

    Это не паранойя, а самая вероятная ошибка при переезде на сервер:
    сначала «просто чтобы открылось», потом руки не доходят. В базе лежат
    анализы с ФИО и датами рождения, поэтому шаг сделан обязательным.
    """
    local = host in ("127.0.0.1", "localhost", "::1")
    if local or enabled():
        return
    raise SystemExit(
        f"Отказ: хаб просят слушать {host}, но вход не настроен.\n"
        f"В базе медицинские данные — наружу без пароля нельзя.\n"
        f"Сначала выполните:  python3 hub.py setpassword\n"
    )
