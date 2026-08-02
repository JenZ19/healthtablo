"""Подключение к sqlite3 и схема базы данных.

Никакого ORM — только stdlib sqlite3. Миграции простые: версия схемы
хранится в таблице meta (ключ schema_version) и применяется по порядку
через список SQL-скриптов в MIGRATIONS.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
# На сервере данные лежат отдельно от кода: код обновляется выкаткой,
# а база и оригиналы документов переживают её и не попадают под rsync
# --delete. Дома переменная не задана и всё остаётся в data/ рядом.
DATA_DIR = Path(os.environ.get("HUB_DATA_DIR") or BASE_DIR / "data")
FILES_DIR = DATA_DIR / "files"
INBOX_DIR = Path(os.environ.get("HUB_INBOX_DIR") or BASE_DIR / "inbox")
DB_PATH = DATA_DIR / "health.db"

SCHEMA_VERSION = 4

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('human','dog')),
    sex TEXT,
    birthdate TEXT,
    species TEXT,
    breed TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NULL REFERENCES subjects(id) ON DELETE SET NULL,
    kind TEXT NOT NULL DEFAULT 'lab' CHECK(kind IN ('lab','imaging','visit','prescription','vaccination','other')),
    title TEXT,
    doc_date TEXT,
    lab_name TEXT,
    source TEXT NOT NULL DEFAULT 'local' CHECK(source IN ('invitro_lk','gmail','icloud','local','manual')),
    source_path TEXT,
    stored_path TEXT,
    sha256 TEXT UNIQUE,
    page_count INTEGER,
    raw_text TEXT,
    parsed_ok INTEGER NOT NULL DEFAULT 0,
    parse_note TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analytes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name_ru TEXT NOT NULL,
    name_en TEXT,
    unit_canonical TEXT,
    category TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS analyte_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    analyte_id INTEGER NOT NULL REFERENCES analytes(id) ON DELETE CASCADE,
    alias TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    subject_id INTEGER NULL REFERENCES subjects(id) ON DELETE SET NULL,
    analyte_id INTEGER NULL REFERENCES analytes(id) ON DELETE SET NULL,
    raw_name TEXT NOT NULL,
    value_num REAL,
    value_text TEXT,
    unit TEXT,
    ref_low REAL,
    ref_high REAL,
    ref_text TEXT,
    flag TEXT NOT NULL DEFAULT 'unknown' CHECK(flag IN ('low','high','normal','unknown')),
    taken_at TEXT
);

CREATE TABLE IF NOT EXISTS conditions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    onset_date TEXT,
    resolved_date TEXT,
    severity TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS medications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    dose TEXT,
    schedule TEXT,
    started TEXT,
    ended TEXT,
    reason TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    details TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NULL REFERENCES subjects(id) ON DELETE CASCADE,
    date TEXT NOT NULL DEFAULT (date('now')),
    title TEXT,
    body TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_subject ON documents(subject_id);
CREATE INDEX IF NOT EXISTS idx_documents_doc_date ON documents(doc_date);
CREATE INDEX IF NOT EXISTS idx_results_subject ON results(subject_id);
CREATE INDEX IF NOT EXISTS idx_results_analyte ON results(analyte_id);
CREATE INDEX IF NOT EXISTS idx_results_taken_at ON results(taken_at);
CREATE INDEX IF NOT EXISTS idx_results_document ON results(document_id);
CREATE INDEX IF NOT EXISTS idx_aliases_alias ON analyte_aliases(alias);
CREATE INDEX IF NOT EXISTS idx_conditions_subject ON conditions(subject_id);
CREATE INDEX IF NOT EXISTS idx_medications_subject ON medications(subject_id);
CREATE INDEX IF NOT EXISTS idx_events_subject ON events(subject_id);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(date);
CREATE INDEX IF NOT EXISTS idx_notes_subject ON notes(subject_id);
"""

# Миграции применяются по порядку, начиная с версии, следующей за текущей в
# meta.schema_version, и каждая доводится до конца отдельным executescript +
# обновлением версии. Все CREATE — с IF NOT EXISTS: миграцию безопасно
# применять поверх боевой базы, старые данные не трогаются.
MIGRATIONS: dict[int, str] = {
    4: """
-- Подписки на пуш-уведомления. Одна строка на устройство: у телефона и
-- ноутбука подписки разные, и отозвать одну, не тронув другую, нужно
-- уметь — например когда телефон потерян.
--
-- endpoint приходит от самого браузера и уникален, поэтому он же ключ:
-- повторная подписка того же устройства обновляет запись, а не плодит
-- дубли, из-за которых одно уведомление приходило бы дважды.
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL,
    last_ok TEXT,
    failures INTEGER NOT NULL DEFAULT 0
);

-- Что уже отправляли. Без этой таблицы одно и то же напоминание
-- приходило бы каждый запуск планировщика, и уведомления быстро
-- научились бы игнорировать.
CREATE TABLE IF NOT EXISTS push_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    UNIQUE(kind, key)
);
""",
    3: """
-- Менструальный календарь. Отмечаются дни менструации с обильностью,
-- из них выводятся длина цикла и длительность. Симптомы хранятся строкой
-- через запятую: набор у каждой свой, а жёсткий справочник заставил бы
-- подгонять ощущения под чужие категории.
CREATE TABLE IF NOT EXISTS cycle_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    flow TEXT,
    symptoms TEXT,
    note TEXT,
    UNIQUE(subject_id, date)
);
CREATE INDEX IF NOT EXISTS idx_cycle_days_subject ON cycle_days(subject_id, date);
""",
    2: """
CREATE TABLE IF NOT EXISTS med_doses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    medication_id INTEGER REFERENCES medications(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    slot INTEGER NOT NULL DEFAULT 1,
    taken INTEGER NOT NULL DEFAULT 0,
    marked_at TEXT,
    UNIQUE(medication_id, date, slot)
);

CREATE TABLE IF NOT EXISTS weights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    kg REAL NOT NULL,
    note TEXT
);

CREATE TABLE IF NOT EXISTS feeding (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    started TEXT,
    food TEXT NOT NULL,
    portion_g REAL,
    times_per_day INTEGER,
    note TEXT
);

CREATE TABLE IF NOT EXISTS prophylaxis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    date TEXT NOT NULL,
    drug TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS mood_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    mood TEXT NOT NULL,
    note TEXT,
    UNIQUE(subject_id, date)
);

CREATE INDEX IF NOT EXISTS idx_med_doses_subject ON med_doses(subject_id);
CREATE INDEX IF NOT EXISTS idx_med_doses_medication ON med_doses(medication_id);
CREATE INDEX IF NOT EXISTS idx_med_doses_date ON med_doses(date);
CREATE INDEX IF NOT EXISTS idx_weights_subject ON weights(subject_id);
CREATE INDEX IF NOT EXISTS idx_weights_date ON weights(date);
CREATE INDEX IF NOT EXISTS idx_feeding_subject ON feeding(subject_id);
CREATE INDEX IF NOT EXISTS idx_prophylaxis_subject ON prophylaxis(subject_id);
CREATE INDEX IF NOT EXISTS idx_prophylaxis_date ON prophylaxis(date);
CREATE INDEX IF NOT EXISTS idx_mood_log_subject ON mood_log(subject_id);
CREATE INDEX IF NOT EXISTS idx_mood_log_date ON mood_log(date);
""",
}


def run_migrations(conn: sqlite3.Connection) -> None:
    current = get_schema_version(conn)
    for version in sorted(v for v in MIGRATIONS if v > current):
        conn.executescript(MIGRATIONS[version])
        conn.execute(
            "UPDATE meta SET value=? WHERE key='schema_version'",
            (str(version),),
        )
        current = version


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    INBOX_DIR.mkdir(parents=True, exist_ok=True)


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn(db_path: Path | str | None = None):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


_SEED_SUBJECTS = [
    # slug, name, kind, sex, species, breed
    ("me", "Я", "human", "female", None, None),
    ("husband", "Муж", "human", "male", None, None),
    ("dog", "Собака", "dog", None, "собака", None),
]


def seed_subjects(conn: sqlite3.Connection) -> None:
    count = conn.execute("SELECT COUNT(*) AS c FROM subjects").fetchone()["c"]
    if count:
        return
    for slug, name, kind, sex, species, breed in _SEED_SUBJECTS:
        conn.execute(
            "INSERT INTO subjects(slug, name, kind, sex, species, breed) VALUES (?,?,?,?,?,?)",
            (slug, name, kind, sex, species, breed),
        )


def init_db(db_path: Path | str | None = None) -> None:
    """Создать схему БД (идемпотентно), засеять справочники и записать версию схемы."""
    ensure_dirs()
    from . import analytes as analytes_module  # локальный импорт — без циклов на уровне модуля

    with get_conn(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            # Свежая база: SCHEMA_SQL уже создал таблицы уровня версии 1,
            # дальше миграции 2+ доводят её до SCHEMA_VERSION.
            conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
        run_migrations(conn)
        seed_subjects(conn)
        analytes_module.sync_to_db(conn)


def get_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row["value"]) if row else 0
