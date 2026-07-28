from pathlib import Path

import fitz  # PyMuPDF
import pytest

from hubcore import db as db_module
from hubcore import ingest as ingest_module


CYRILLIC_FONT = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"


def _make_lab_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page()
    text = (
        "ИНВИТРО\n"
        "Дата взятия: 05.03.2024\n"
        "Пациент: Иванова Анна\n"
        "Наименование | Результат | Единицы | Референс\n"
        "Гемоглобин | 140 | г/л | 130 - 160\n"
        "Глюкоза | 5.5 | ммоль/л | 3.9 - 6.1\n"
        "ТТГ | 8.5 | мЕд/л | 0.4 - 4.0\n"
    )
    page.insert_text((36, 72), text, fontsize=11, fontfile=CYRILLIC_FONT, fontname="F0")
    doc.save(str(path))
    doc.close()


@pytest.fixture()
def isolated_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    files_dir = data_dir / "files"
    inbox_dir = tmp_path / "inbox"
    db_path = data_dir / "health.db"

    monkeypatch.setattr(db_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_module, "FILES_DIR", files_dir)
    monkeypatch.setattr(db_module, "INBOX_DIR", inbox_dir)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)

    db_module.init_db(db_path)
    return {"db_path": db_path, "files_dir": files_dir, "inbox_dir": inbox_dir}


def test_ingest_pdf_extracts_results_and_assigns_subject(tmp_path, isolated_env):
    pdf_path = tmp_path / "lab.pdf"
    _make_lab_pdf(pdf_path)

    with db_module.get_conn(isolated_env["db_path"]) as conn:
        conn.execute("UPDATE subjects SET notes=? WHERE slug='me'", ("Иванова Анна",))

    result = ingest_module.ingest_path(pdf_path, db_path=isolated_env["db_path"])

    assert result.status == "ingested"
    assert result.subject_slug == "me"
    assert result.results_count == 3
    assert result.matched_count == 3
    assert result.doc_date == "2024-03-05"


def test_ingest_is_idempotent_by_sha256(tmp_path, isolated_env):
    pdf_path = tmp_path / "lab2.pdf"
    _make_lab_pdf(pdf_path)

    first = ingest_module.ingest_path(pdf_path, db_path=isolated_env["db_path"])
    second = ingest_module.ingest_path(pdf_path, db_path=isolated_env["db_path"])

    assert first.status in ("ingested", "needs_review")
    assert second.status == "duplicate"
    assert second.document_id == first.document_id

    with db_module.get_conn(isolated_env["db_path"]) as conn:
        count = conn.execute("SELECT COUNT(*) c FROM documents WHERE sha256=?",
                              (conn.execute("SELECT sha256 FROM documents WHERE id=?", (first.document_id,)).fetchone()["sha256"],)
                              ).fetchone()["c"]
    assert count == 1


def test_ingest_dry_run_does_not_write_to_db(tmp_path, isolated_env):
    pdf_path = tmp_path / "lab3.pdf"
    _make_lab_pdf(pdf_path)

    result = ingest_module.ingest_path(pdf_path, db_path=isolated_env["db_path"], dry_run=True)
    assert result.status == "dry_run"
    assert result.results_count == 3

    with db_module.get_conn(isolated_env["db_path"]) as conn:
        count = conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
    assert count == 0


def test_ingest_missing_file_reports_error(isolated_env):
    result = ingest_module.ingest_path("/no/such/file.pdf", db_path=isolated_env["db_path"])
    assert result.status == "error"


def test_ingest_unmatched_subject_needs_review(tmp_path, isolated_env):
    pdf_path = tmp_path / "lab4.pdf"
    _make_lab_pdf(pdf_path)
    # никто из субъектов не совпадает с "Иванова Анна" -> needs_review
    result = ingest_module.ingest_path(pdf_path, db_path=isolated_env["db_path"])
    assert result.status == "needs_review"
    assert result.subject_slug is None
