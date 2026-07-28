import datetime as dt

import pytest

from hubcore import db as db_module
from hubcore import web as web_module


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    db_path = data_dir / "health.db"
    monkeypatch.setattr(db_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_module, "FILES_DIR", data_dir / "files")
    monkeypatch.setattr(db_module, "INBOX_DIR", tmp_path / "inbox")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    db_module.init_db(db_path)
    return db_path


# --- разбор числа приёмов в сутки из текста схемы --------------------------

def test_doses_per_day_two_times():
    assert web_module.doses_per_day("по 1 таб. 2 раза в сутки") == 2


def test_doses_per_day_one_time():
    assert web_module.doses_per_day("1 раз в сутки") == 1


def test_doses_per_day_unrecognized_defaults_to_one():
    assert web_module.doses_per_day("по 1/4") == 1


def test_doses_per_day_partial_sentence():
    assert web_module.doses_per_day("по 1/4 табл. 2 раза в") == 2


def test_doses_per_day_empty():
    assert web_module.doses_per_day("") == 1
    assert web_module.doses_per_day(None) == 1


# --- переключение отметки о приёме ------------------------------------------

def test_toggle_dose_marks_and_unmarks(isolated_db):
    with db_module.get_conn(isolated_db) as conn:
        subject_id = conn.execute("SELECT id FROM subjects WHERE slug='dog'").fetchone()["id"]
        conn.execute(
            "INSERT INTO medications(subject_id, name, schedule, started) VALUES (?,?,?,?)",
            (subject_id, "Гапентин", "по 1 таб. 2 раза в сутки", "2026-07-16"),
        )
        med_id = conn.execute("SELECT id FROM medications WHERE name='Гапентин'").fetchone()["id"]

        taken = web_module.toggle_dose(conn, subject_id, med_id, "2026-07-20", 1)
        assert taken is True
        row = conn.execute(
            "SELECT * FROM med_doses WHERE medication_id=? AND date=? AND slot=1", (med_id, "2026-07-20")
        ).fetchone()
        assert row["taken"] == 1
        assert row["marked_at"] is not None

        taken_again = web_module.toggle_dose(conn, subject_id, med_id, "2026-07-20", 1)
        assert taken_again is False
        row2 = conn.execute(
            "SELECT * FROM med_doses WHERE medication_id=? AND date=? AND slot=1", (med_id, "2026-07-20")
        ).fetchone()
        assert row2["taken"] == 0
        # разные слоты не должны пересекаться
        taken_slot2 = web_module.toggle_dose(conn, subject_id, med_id, "2026-07-20", 2)
        assert taken_slot2 is True


# --- сроки профилактики с учётом ручных записей -----------------------------

def test_vet_due_uses_latest_of_document_and_manual_record(isolated_db):
    with db_module.get_conn(isolated_db) as conn:
        subject_id = conn.execute("SELECT id FROM subjects WHERE slug='dog'").fetchone()["id"]
        today = dt.date.today()
        old_doc_date = (today - dt.timedelta(days=200)).isoformat()
        conn.execute(
            "INSERT INTO documents(subject_id, kind, title, doc_date, source) VALUES (?,?,?,?,?)",
            (subject_id, "vaccination", "Вакцинация от бешенства", old_doc_date, "manual"),
        )

        due_before_manual = web_module.vet_due(conn, subject_id)
        entry = next(d for d in due_before_manual if d["label"] == "вакцинация от бешенства")
        assert entry["last"] == old_doc_date
        assert entry["passed"] == 200

        # более свежая ручная запись должна перекрыть дату из документа
        recent_manual_date = (today - dt.timedelta(days=10)).isoformat()
        conn.execute(
            "INSERT INTO prophylaxis(subject_id, kind, date, drug) VALUES (?,?,?,?)",
            (subject_id, "вакцинация", recent_manual_date, "Нобивак"),
        )

        due_after_manual = web_module.vet_due(conn, subject_id)
        entry2 = next(d for d in due_after_manual if d["label"] == "вакцинация от бешенства")
        assert entry2["last"] == recent_manual_date
        assert entry2["passed"] == 10
        assert entry2["overdue"] is False


def test_vet_due_manual_only_record(isolated_db):
    with db_module.get_conn(isolated_db) as conn:
        subject_id = conn.execute("SELECT id FROM subjects WHERE slug='dog'").fetchone()["id"]
        today = dt.date.today()
        old_date = (today - dt.timedelta(days=120)).isoformat()
        conn.execute(
            "INSERT INTO prophylaxis(subject_id, kind, date, drug) VALUES (?,?,?,?)",
            (subject_id, "глисты", old_date, "Мильбемакс"),
        )
        due = web_module.vet_due(conn, subject_id)
        entry = next(d for d in due if d["label"] == "обработка от глистов")
        assert entry["last"] == old_date
        assert entry["overdue"] is True  # период 90 дней, прошло 120


# --- определение превышения веса относительно породного коридора -----------

def test_weight_over_breed_range_within_range():
    result = web_module.weight_over_breed_range(3.5, "Мальтийская болонка (мальтезе)")
    assert result == {"low": 3.0, "high": 4.0, "over": False}


def test_weight_over_breed_range_over():
    result = web_module.weight_over_breed_range(5.4, "Мальтийская болонка (мальтезе)")
    assert result["over"] is True
    assert result["pct"] == 35  # (5.4 - 4.0) / 4.0 * 100 = 35%
    assert result["over_kg"] == pytest.approx(1.4)


def test_weight_over_breed_range_unknown_breed_returns_none():
    assert web_module.weight_over_breed_range(10.0, "неизвестная порода") is None
    assert web_module.weight_over_breed_range(10.0, None) is None
