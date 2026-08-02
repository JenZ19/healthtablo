"""Проверки входа.

Смотреть глазами тут нечего: «страница открылась» ничего не доказывает,
а ошибка в проверке пароля выглядит ровно так же, как её отсутствие.
"""

from __future__ import annotations

import json
import time

import pytest

from hubcore import auth


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HUB_AUTH_FILE", str(tmp_path / "auth.json"))
    auth._attempts.clear()
    yield


# --- пароль ---------------------------------------------------------------

def test_pravilnyj_parol_prohodit():
    stored = auth.hash_password("очень длинный пароль")
    assert auth.verify_password("очень длинный пароль", stored)


def test_nepravilnyj_parol_ne_prohodit():
    stored = auth.hash_password("очень длинный пароль")
    assert not auth.verify_password("очень длинный паролЬ", stored)
    assert not auth.verify_password("", stored)


def test_parol_ne_hranitsya_v_otkrytom_vide(tmp_path):
    auth.save_config("zhenya", "секретный пароль 123")
    raw = auth.config_path().read_text("utf-8")
    assert "секретный пароль 123" not in raw
    assert json.loads(raw)["password_hash"].startswith("scrypt$")


def test_odinakovye_paroli_dayut_raznye_hesi():
    # Соль случайная: одинаковый хеш у двух пользователей выдавал бы,
    # что пароли совпадают.
    assert auth.hash_password("одно и то же") != auth.hash_password("одно и то же")


def test_bitiy_hesh_ne_padaet_a_otklonyaet():
    assert not auth.verify_password("что угодно", "мусор")
    assert not auth.verify_password("что угодно", "scrypt$не$число$1$aa$bb")


# --- настройка ------------------------------------------------------------

def test_bez_nastrojki_vhod_vyklyuchen():
    assert not auth.enabled()
    assert not auth.check_credentials("zhenya", "пароль")


def test_posle_nastrojki_vhod_vklyuchen():
    auth.save_config("zhenya", "длинный пароль")
    assert auth.enabled()
    assert auth.check_credentials("zhenya", "длинный пароль")
    assert auth.check_credentials("ZHENYA", "длинный пароль")  # логин без учёта регистра
    assert not auth.check_credentials("andrey", "длинный пароль")
    assert not auth.check_credentials("zhenya", "другой пароль")


def test_fajl_nastroek_zakryt_ot_chuzhih():
    path = auth.save_config("zhenya", "длинный пароль")
    assert oct(path.stat().st_mode)[-3:] == "600"


# --- сессия ---------------------------------------------------------------

def test_svoj_token_prinimaetsya():
    auth.save_config("zhenya", "длинный пароль")
    assert auth.valid_token(auth.issue_token("zhenya"))


def test_chuzhoj_i_isporchennyj_token_otklonyayutsya():
    auth.save_config("zhenya", "длинный пароль")
    token = auth.issue_token("zhenya")
    assert not auth.valid_token(None)
    assert not auth.valid_token("")
    assert not auth.valid_token("совсем не токен")
    assert not auth.valid_token(token[:-4] + "AAAA")   # подмена подписи
    payload, sig = token.rsplit(".", 1)
    assert not auth.valid_token(payload + ".")          # подпись убрана


def test_token_drugogo_polzovatelya_ne_prohodit():
    auth.save_config("zhenya", "длинный пароль")
    chuzhoj = auth.issue_token("andrey")
    assert not auth.valid_token(chuzhoj)


def test_prosrochennyj_token_ne_prohodit(monkeypatch):
    auth.save_config("zhenya", "длинный пароль")
    token = auth.issue_token("zhenya")
    monkeypatch.setattr(time, "time", lambda: time.__dict__["time"] and 1e12)
    assert not auth.valid_token(token)


def test_smena_parolya_ne_sbrasyvaet_sekret():
    # Секрет подписи переживает смену пароля: иначе каждая смена
    # разлогинивала бы телефон и планшет заодно.
    auth.save_config("zhenya", "первый пароль")
    secret1 = auth.load_config()["secret"]
    auth.save_config("zhenya", "второй пароль")
    assert auth.load_config()["secret"] == secret1


# --- подбор ---------------------------------------------------------------

def test_posle_serii_oshibok_adres_blokiruetsya():
    ip = "203.0.113.7"
    assert auth.locked_out(ip) == 0
    for _ in range(auth.MAX_ATTEMPTS):
        auth.note_failure(ip)
    assert auth.locked_out(ip) > 0


def test_uspeshnyj_vhod_sbrasyvaet_schetchik():
    ip = "203.0.113.8"
    for _ in range(auth.MAX_ATTEMPTS - 1):
        auth.note_failure(ip)
    auth.note_success(ip)
    assert auth.locked_out(ip) == 0


def test_blokirovka_ne_zadevaet_drugie_adresa():
    for _ in range(auth.MAX_ATTEMPTS):
        auth.note_failure("203.0.113.9")
    assert auth.locked_out("203.0.113.10") == 0


# --- защита от выхода наружу без пароля -----------------------------------

def test_lokalno_bez_parolya_mozhno():
    auth.guard_public_bind("127.0.0.1")  # не должно бросать


def test_naruzhu_bez_parolya_nelzya():
    with pytest.raises(SystemExit):
        auth.guard_public_bind("0.0.0.0")


def test_naruzhu_s_parolem_mozhno():
    auth.save_config("zhenya", "длинный пароль")
    auth.guard_public_bind("0.0.0.0")


def test_kirillicheskij_login_ne_ronyaet_vhod():
    """Логин русскими буквами должен просто не подходить, а не падать.

    hmac.compare_digest на строках отказывается работать с не-ASCII —
    непроверенное место валило вход в ошибку 500 вместо отказа.
    """
    auth.save_config("zhenya", "длинный пароль")
    assert auth.check_credentials("Женя", "длинный пароль") is False
    assert auth.check_credentials("zhenya", "длинный пароль") is True


def test_kirillicheskij_login_mozhno_zadat():
    auth.save_config("Женя", "длинный пароль")
    assert auth.check_credentials("женя", "длинный пароль")
    assert auth.valid_token(auth.issue_token("Женя"))
