"""Проверки входа на уровне HTTP.

Главное здесь — не «страница входа рисуется», а что без входа не отдаётся
ни одна страница с данными. Проверка идёт по списку реальных маршрутов
приложения, а не по паре выбранных вручную: новый маршрут, добавленный
мимо защиты, должен ронять тест.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hubcore import auth


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HUB_AUTH_FILE", str(tmp_path / "auth.json"))
    auth._attempts.clear()
    from hubcore import web

    return TestClient(web.app, follow_redirects=False)


def test_bez_nastroennogo_vhoda_hab_otkryt(client):
    # Домашний режим: на 127.0.0.1 лишний экран с паролем не нужен.
    assert client.get("/").status_code == 200


def test_s_nastroennym_vhodom_glavnaya_zakryta(client):
    auth.save_config("zhenya", "длинный пароль")
    r = client.get("/")
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_zakryty_vse_marshruty_s_dannymi(client):
    """Ни один GET-маршрут, кроме страницы входа, не отдаётся без сессии."""
    from hubcore import web

    auth.save_config("zhenya", "длинный пароль")
    proverено = 0
    for route in web.app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if "GET" not in methods or "{" in path or path.startswith(web.PUBLIC_PATHS):
            continue
        r = client.get(path)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), (
            f"маршрут {path} отдаётся без входа"
        )
        proverено += 1
    assert proverено >= 5, "маршруты не нашлись — тест перестал что-либо проверять"


def test_stranica_vhoda_dostupna(client):
    auth.save_config("zhenya", "длинный пароль")
    r = client.get("/login")
    assert r.status_code == 200
    assert "Пароль" in r.text


def test_stranica_vhoda_ne_vydaet_soderzhimoe(client):
    """Посторонний видит только название хаба.

    Ни имён субъектов, ни намёка на содержимое, ни даже логина —
    страница входа не должна подтверждать, что вы угадали хоть что-то.
    """
    auth.save_config("ivanova", "длинный пароль")
    text = client.get("/login").text
    for utechka in ("Иванова", "Петров", "Барсик", "анализ", "ivanova"):
        assert utechka not in text


def test_vhod_s_vernym_parolem_daet_dostup(client):
    auth.save_config("zhenya", "длинный пароль")
    r = client.post("/login", data={"username": "zhenya", "password": "длинный пароль", "next": "/"})
    assert r.status_code == 303
    assert auth.SESSION_COOKIE in r.cookies
    client.cookies.set(auth.SESSION_COOKIE, r.cookies[auth.SESSION_COOKIE])
    assert client.get("/").status_code == 200


def test_vhod_s_nevernym_parolem_ne_daet(client):
    auth.save_config("zhenya", "длинный пароль")
    r = client.post("/login", data={"username": "zhenya", "password": "не тот", "next": "/"})
    assert r.status_code == 401
    assert auth.SESSION_COOKIE not in r.cookies


def test_soobshchenie_ob_oshibke_ne_razlichaet_login_i_parol(client):
    auth.save_config("zhenya", "длинный пароль")
    a = client.post("/login", data={"username": "zhenya", "password": "не тот"})
    b = client.post("/login", data={"username": "нет такого", "password": "не тот"})
    assert "Неверный логин или пароль" in a.text
    assert a.text == b.text


def test_podmenennaya_cookie_ne_puskaet(client):
    auth.save_config("zhenya", "длинный пароль")
    # Значение только из ASCII: cookie с кириллицей отвергает сам HTTP-клиент,
    # до приложения такой запрос не доходит и ничего не проверяет.
    client.cookies.set(auth.SESSION_COOKIE, "cG9kZGVsbnlq.dG9rZW4")
    r = client.get("/")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_posle_vyhoda_dostup_zakryvaetsya(client):
    auth.save_config("zhenya", "длинный пароль")
    r = client.post("/login", data={"username": "zhenya", "password": "длинный пароль"})
    client.cookies.set(auth.SESSION_COOKIE, r.cookies[auth.SESSION_COOKIE])
    client.post("/logout")
    client.cookies.clear()
    assert client.get("/").status_code == 303


def test_perенаправление_tolko_na_svoi_puti(client):
    """?next=чужой-сайт не должен уводить со страницы входа наружу."""
    auth.save_config("zhenya", "длинный пароль")
    r = client.post(
        "/login",
        data={"username": "zhenya", "password": "длинный пароль", "next": "https://злой-сайт.ru"},
    )
    assert r.headers["location"] == "/"

    r = client.post(
        "/login",
        data={"username": "zhenya", "password": "длинный пароль", "next": "//злой-сайт.ru"},
    )
    assert r.headers["location"] == "/"


def test_posle_serii_oshibok_vhod_priderzhivaetsya(client):
    auth.save_config("zhenya", "длинный пароль")
    for _ in range(auth.MAX_ATTEMPTS):
        client.post("/login", data={"username": "zhenya", "password": "не тот"})
    # Даже с верным паролем — пауза: иначе ограничение обходится тем,
    # что перебор просто продолжают.
    r = client.post("/login", data={"username": "zhenya", "password": "длинный пароль"})
    assert r.status_code == 429


def test_zagolovok_zapreshchaet_indeksaciyu(client):
    auth.save_config("zhenya", "длинный пароль")
    assert "noindex" in client.get("/login").text


def test_ikonka_dlya_telefona_est_i_ona_png(client):
    """iOS берёт иконку домашнего экрана только из PNG.

    С SVG он молча подставляет уменьшенный скриншот страницы — то есть
    кусок медицинской таблицы на домашнем экране телефона.
    """
    from hubcore import web

    auth.save_config("ivanova", "длинный пароль")
    html = client.get("/login").text
    assert 'rel="apple-touch-icon"' in html
    assert ".png" in html.split('rel="apple-touch-icon"')[1].split(">")[0]
    icon = web.BASE_DIR / "static" / "apple-touch-icon.png"
    assert icon.exists() and icon.stat().st_size > 1000
    assert icon.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_v_ishodnike_stranicy_vhoda_net_kommentariev_o_soderzhimom(client):
    """Комментарии в HTML читает любой, кто открыл исходник страницы."""
    auth.save_config("ivanova", "длинный пароль")
    html = client.get("/login").text
    for slovo in ("анализ", "медицин", "паци", "диагноз"):
        assert slovo not in html.lower()


def test_otkryty_tolko_ikonki_i_oformlenie(client):
    """Список открытых путей не должен разрастись до чего-то с данными.

    Проверка буквальная: любой новый пункт в PUBLIC_PATHS обязан быть
    либо страницей входа, либо картинкой, либо стилями.
    """
    from hubcore import web

    razresheno = {"/login", "/favicon.ico", "/apple-touch-icon", "/sw.js", "/push/key"}
    for path in web.PUBLIC_PATHS:
        assert path in razresheno or path.startswith("/static/"), path
        assert not path.rstrip("/").endswith(("/documents", "/inbox", "/s")), path


def test_ikonka_otdaetsya_bez_vhoda(client):
    """Телефон забирает иконку до того, как где-либо войдёт."""
    auth.save_config("ivanova", "длинный пароль")
    r = client.get("/static/apple-touch-icon.png")
    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert client.get("/static/manifest.webmanifest").status_code == 200


def test_kornevye_ikonki_ne_perehvatyvayut_stranicy(client):
    """Маршруты иконок не должны съедать обычные страницы.

    Ловушка настоящая: один маршрут /{name} для всех имён иконок разом
    перехватил /login, и хаб перестал пускать внутрь вообще.
    """
    auth.save_config("ivanova", "длинный пароль")
    assert client.get("/login").status_code == 200
    assert client.get("/apple-touch-icon.png").status_code == 200
    # обычная страница по-прежнему уводит на вход, а не отдаёт картинку
    r = client.get("/documents")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_ios_probuet_neskolko_imen_i_vse_otvechayut(client):
    """Safari перебирает имена сам, не глядя на <link> на странице."""
    auth.save_config("ivanova", "длинный пароль")
    for name in ("/apple-touch-icon.png", "/apple-touch-icon-precomposed.png",
                 "/apple-touch-icon-180x180.png", "/apple-touch-icon-152x152.png",
                 "/apple-touch-icon-120x120.png", "/favicon.ico"):
        r = client.get(name)
        assert r.status_code == 200, name


def test_obrabotchik_uvedomlenij_dostupen_bez_vhoda(client):
    """Браузер тянет sw.js служебным запросом — редирект ломает регистрацию."""
    auth.save_config("ivanova", "длинный пароль")
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert r.headers.get("Service-Worker-Allowed") == "/"


def test_otkrytyj_klyuch_dostupen_a_podpiska_net(client):
    """Ключ VAPID открытый по устройству, а запись подписки — нет."""
    auth.save_config("ivanova", "длинный пароль")
    assert client.get("/push/key").status_code == 200
    r = client.post("/push/subscribe", json={"subscription": {"endpoint": "https://x"}})
    assert r.status_code == 303, "чужой смог бы подписать своё устройство на наши уведомления"


def test_v_obrabotchike_net_lichnyh_dannyh(client):
    """Файл отдаётся кому угодно — в нём не должно быть ни имён, ни адресов."""
    text = client.get("/sw.js").text.lower()
    for slovo in ("зеленов", "берлов", "пряник", "@", "100.109"):
        assert slovo not in text
