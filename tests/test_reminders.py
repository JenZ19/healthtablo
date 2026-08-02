"""Проверки напоминаний.

Главный риск здесь — не «не пришло», а «пришло не то и по десять раз».
Уведомление, которое повторяется каждый день, читать перестают, и тогда
пропускается то единственное, ради которого всё затевалось.
"""

from __future__ import annotations

import datetime as dt

import pytest

from hubcore import db as db_module
from hubcore import push, reminders


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "test.db")
    db_module.init_db()
    c = db_module.connect(tmp_path / "test.db")
    # init_db заводит субъектов сам — берём их, а не создаём своих:
    # slug у них уникальный, и повторная вставка просто падает.
    c.execute("UPDATE subjects SET name='Барсик' WHERE slug='dog'")
    c.execute("UPDATE subjects SET name='Иванова Мария', sex='female' WHERE slug='me'")
    c.commit()
    yield c
    c.close()


def _sid(conn, slug: str) -> int:
    return conn.execute("SELECT id FROM subjects WHERE slug=?", (slug,)).fetchone()["id"]


TODAY = dt.date(2026, 8, 2)


# --- таблетки -------------------------------------------------------------

def test_neotmechennaya_doza_daet_napominanie(conn):
    conn.execute("INSERT INTO medications (subject_id, name, started, ended) "
                 "VALUES (?, 'Гапентин', '2026-07-01', '2026-09-01')", (_sid(conn, "dog"),))
    conn.commit()
    items = reminders.pending_doses(conn, TODAY)
    assert len(items) == 1
    assert "Гапентин" in items[0]["body"]


def test_otmechennaya_doza_ne_daet(conn):
    conn.execute("INSERT INTO medications (subject_id, name, started, ended) "
                 "VALUES (?, 'Гапентин', '2026-07-01', '2026-09-01')", (_sid(conn, "dog"),))
    conn.execute("INSERT INTO med_doses (subject_id, medication_id, date, slot, taken) "
                 "VALUES (?, (SELECT id FROM medications WHERE name='Гапентин'), ?, 1, 1)",
                 (_sid(conn, "dog"), TODAY.isoformat()))
    conn.commit()
    assert reminders.pending_doses(conn, TODAY) == []


def test_zakonchennyj_kurs_ne_napominaet(conn):
    # Курс кончился в июле — в августе про него молчим.
    conn.execute("INSERT INTO medications (subject_id, name, started, ended) "
                 "VALUES (?, 'Амантадин', '2026-06-01', '2026-07-01')", (_sid(conn, "dog"),))
    conn.commit()
    assert reminders.pending_doses(conn, TODAY) == []


def test_eshche_ne_nachavshijsya_kurs_ne_napominaet(conn):
    conn.execute("INSERT INTO medications (subject_id, name, started, ended) "
                 "VALUES (?, 'Будущее', '2026-09-01', '2026-10-01')", (_sid(conn, "dog"),))
    conn.commit()
    assert reminders.pending_doses(conn, TODAY) == []


# --- профилактика ---------------------------------------------------------

def test_prosrochennaya_obrabotka_napominaet(conn):
    conn.execute("INSERT INTO prophylaxis (subject_id, kind, date) VALUES (?, 'глисты', '2026-01-01')", (_sid(conn, 'dog'),))
    conn.commit()
    items = reminders.prophylaxis_due(conn, TODAY)
    assert len(items) == 1
    assert "просрочено" in items[0]["body"]


def test_svezhaya_obrabotka_ne_napominaet(conn):
    conn.execute("INSERT INTO prophylaxis (subject_id, kind, date) VALUES (?, 'глисты', ?)",
                 (_sid(conn, 'dog'), (TODAY - dt.timedelta(days=10)).isoformat()))
    conn.commit()
    assert reminders.prophylaxis_due(conn, TODAY) == []


def test_srok_v_blizhajshuyu_nedelyu_napominaet(conn):
    last = TODAY - dt.timedelta(days=reminders.PROPHYLAXIS_INTERVALS["глисты"] - 3)
    conn.execute("INSERT INTO prophylaxis (subject_id, kind, date) VALUES (?, 'глисты', ?)",
                 (_sid(conn, 'dog'), last.isoformat()))
    conn.commit()
    items = reminders.prophylaxis_due(conn, TODAY)
    assert len(items) == 1 and "через 3 дн." in items[0]["body"]


def test_uchityvaetsya_poslednyaya_obrabotka_a_ne_pervaya(conn):
    conn.execute("INSERT INTO prophylaxis (subject_id, kind, date) VALUES (?, 'глисты', '2024-01-01')", (_sid(conn, 'dog'),))
    conn.execute("INSERT INTO prophylaxis (subject_id, kind, date) VALUES (?, 'глисты', ?)",
                 (_sid(conn, 'dog'), (TODAY - dt.timedelta(days=5)).isoformat()))
    conn.commit()
    assert reminders.prophylaxis_due(conn, TODAY) == []


def test_klyuch_privyazan_k_sroku_a_ne_k_segodnya(conn):
    """Иначе просроченная обработка напоминала бы о себе каждый день."""
    conn.execute("INSERT INTO prophylaxis (subject_id, kind, date) VALUES (?, 'глисты', '2026-01-01')", (_sid(conn, 'dog'),))
    conn.commit()
    a = reminders.prophylaxis_due(conn, TODAY)[0]["key"]
    b = reminders.prophylaxis_due(conn, TODAY + dt.timedelta(days=5))[0]["key"]
    assert a == b


# --- цикл -----------------------------------------------------------------

def _mark_cycle(conn, start: dt.date, days: int = 4):
    for i in range(days):
        conn.execute("INSERT INTO cycle_days (subject_id, date, flow) VALUES (?, ?, 'умеренные')",
                     (_sid(conn, "me"), (start + dt.timedelta(days=i)).isoformat()))
    conn.commit()


def test_bez_otmetok_pro_cikl_molchim(conn):
    assert reminders.cycle_due(conn, TODAY) == []


def test_odin_epizod_ne_daet_prognoza(conn):
    # По одной менструации длину цикла не вычислить — и выдумывать её нельзя.
    _mark_cycle(conn, TODAY - dt.timedelta(days=20))
    assert reminders.cycle_due(conn, TODAY) == []


def test_ozhidaemaya_data_napominaet_zaranee(conn):
    _mark_cycle(conn, TODAY - dt.timedelta(days=56))
    _mark_cycle(conn, TODAY - dt.timedelta(days=28))
    items = reminders.cycle_due(conn, TODAY)
    assert len(items) == 1 and "ожидается" in items[0]["body"]


def test_zaderzhka_napominaet(conn):
    _mark_cycle(conn, TODAY - dt.timedelta(days=61))
    _mark_cycle(conn, TODAY - dt.timedelta(days=33))
    items = reminders.cycle_due(conn, TODAY)
    assert len(items) == 1 and "задержка" in items[0]["body"]


def test_v_seredine_cikla_molchim(conn):
    _mark_cycle(conn, TODAY - dt.timedelta(days=42))
    _mark_cycle(conn, TODAY - dt.timedelta(days=14))
    assert reminders.cycle_due(conn, TODAY) == []


# --- отправка -------------------------------------------------------------

def test_odno_i_to_zhe_ne_uhodit_dvazhdy(conn, monkeypatch):
    conn.execute("INSERT INTO medications (subject_id, name, started, ended) "
                 "VALUES (?, 'Гапентин', '2026-07-01', '2026-09-01')", (_sid(conn, "dog"),))
    conn.commit()
    monkeypatch.setattr(push, "send_to_all", lambda *a, **k: (1, 0))

    first = reminders.run(conn, TODAY)
    second = reminders.run(conn, TODAY)
    assert len(first) == 1
    assert second == [], "повторный запуск отправил то же самое ещё раз"


def test_za_odin_zahod_ne_bolshe_treh(conn, monkeypatch):
    for i in range(6):
        conn.execute("INSERT INTO medications (subject_id, name, started, ended) "
                     "VALUES (?, ?, '2026-07-01', '2026-09-01')",
                     (_sid(conn, "dog"), f"Лекарство {i}"))
    conn.commit()
    monkeypatch.setattr(push, "send_to_all", lambda *a, **k: (1, 0))
    assert len(reminders.run(conn, TODAY)) == reminders.MAX_PER_RUN


def test_bez_podpisannyh_ustrojstv_nichego_ne_pomechaetsya(conn, monkeypatch):
    """Если отправить некому — повод должен остаться на потом.

    Иначе первый же запуск на сервере без подписок «съел» бы все
    напоминания, и после подписки телефона не пришло бы ничего.
    """
    conn.execute("INSERT INTO medications (subject_id, name, started, ended) "
                 "VALUES (?, 'Гапентин', '2026-07-01', '2026-09-01')", (_sid(conn, "dog"),))
    conn.commit()
    monkeypatch.setattr(push, "send_to_all", lambda *a, **k: (0, 0))

    assert reminders.run(conn, TODAY) == []
    monkeypatch.setattr(push, "send_to_all", lambda *a, **k: (1, 0))
    assert len(reminders.run(conn, TODAY)) == 1


def test_probnyj_progon_nichego_ne_otpravlyaet(conn, monkeypatch):
    conn.execute("INSERT INTO medications (subject_id, name, started, ended) "
                 "VALUES (?, 'Гапентин', '2026-07-01', '2026-09-01')", (_sid(conn, "dog"),))
    conn.commit()
    otpravleno = []
    monkeypatch.setattr(push, "send_to_all",
                        lambda *a, **k: (otpravleno.append(a), (1, 0))[1])
    reminders.run(conn, TODAY, dry_run=True)
    assert otpravleno == []


def test_v_tekste_net_diagnozov_i_znachenij(conn):
    """Уведомление видно на заблокированном экране."""
    conn.execute("INSERT INTO medications (subject_id, name, reason, started, ended) "
                 "VALUES (?, 'Гапентин', 'грыжа диска L2-L3', '2026-07-01', '2026-09-01')",
                 (_sid(conn, "dog"),))
    conn.execute("INSERT INTO prophylaxis (subject_id, kind, date) VALUES (?, 'глисты', '2026-01-01')", (_sid(conn, 'dog'),))
    conn.commit()
    for item in reminders.collect(conn, TODAY):
        text = (item["title"] + " " + item["body"]).lower()
        for slovo in ("грыжа", "диагноз", "ммоль", "диска"):
            assert slovo not in text, item


# --- ключи VAPID ----------------------------------------------------------

def test_klyuchi_sozdayutsya_odin_raz(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DATA_DIR", tmp_path)
    first = push.ensure_keys()
    second = push.ensure_keys()
    assert first["public"] == second["public"]
    assert first["private"] == second["private"]


def test_zakrytyj_klyuch_zakryt_ot_chuzhih(tmp_path, monkeypatch):
    monkeypatch.setattr(db_module, "DATA_DIR", tmp_path)
    push.ensure_keys()
    assert oct(push.keys_path().stat().st_mode)[-3:] == "600"
