#!/usr/bin/env python3
"""Демо-наполнение профиля мужа (Иванов Пётр).

ВНИМАНИЕ: все данные здесь ВЫМЫШЛЕНЫ. Это витрина, чтобы посмотреть,
как хаб выглядит с наполненным профилем, — не медицинская информация.
Каждая созданная строка помечена маркером DEMO_TAG, чтобы её можно было
удалить одной командой и не перепутать с настоящими анализами.

    python3 demo_seed_husband.py          # залить демо
    python3 demo_seed_husband.py --remove # убрать всё демо подчистую
"""

from __future__ import annotations

import sys
from datetime import date

from hubcore.db import get_conn as connect

DEMO_TAG = "[ДЕМО]"
SLUG = "husband"

# (месяц-смещение назад, дата) — четыре точки во времени, чтобы были графики
PANELS = [
    ("2023-11-14", "Чек-ап, биохимия + липиды"),
    ("2024-06-03", "Контроль после смены питания"),
    ("2025-02-19", "Годовой чек-ап"),
    ("2026-05-12", "Контроль печени и витамина D"),
]

# raw_name -> [(значение по датам), единицы, ref_low, ref_high]
# Динамика придумана «правдоподобной»: печень и липиды ползут вниз после
# вмешательства, витамин D растёт на добавках, глюкоза держится у верхней границы.
SERIES = {
    "Холестерин общий":        ([6.4, 6.1, 5.7, 5.4],      "ммоль/л", 3.3, 5.2),
    "Холестерин-ЛПНП":         ([4.3, 4.0, 3.6, 3.3],      "ммоль/л", 1.5, 3.3),
    "Холестерин-ЛПВП":         ([1.02, 1.08, 1.15, 1.21],  "ммоль/л", 1.03, 2.2),
    "Триглицериды":            ([2.35, 2.05, 1.72, 1.48],  "ммоль/л", 0.4, 1.7),
    "АЛТ":                     ([68.0, 61.0, 49.0, 38.0],  "ед/л",    0.0, 41.0),
    "АСТ":                     ([41.0, 38.0, 33.0, 29.0],  "ед/л",    0.0, 37.0),
    "ГГТ":                     ([79.0, 70.0, 55.0, 44.0],  "ед/л",    0.0, 55.0),
    "Глюкоза":                 ([5.9, 6.1, 5.8, 5.6],      "ммоль/л", 4.1, 5.9),
    "Гликированный гемоглобин":([5.8, 5.9, 5.7, 5.5],      "%",       4.0, 6.0),
    "Витамин D, 25-ОН":        ([14.0, 22.0, 31.0, 42.0],  "нг/мл",   30.0, 100.0),
    "Ферритин":                ([310.0, 288.0, 240.0, 205.0], "нг/мл", 20.0, 250.0),
    "Гемоглобин":              ([156.0, 154.0, 151.0, 149.0], "г/л",  130.0, 160.0),
    "ТТГ":                     ([2.1, 2.4, 2.2, 2.0],      "мкМЕ/мл", 0.4, 4.0),
    "Креатинин":               ([94.0, 92.0, 90.0, 89.0],  "мкмоль/л", 62.0, 106.0),
    "Мочевая кислота":         ([432.0, 415.0, 390.0, 368.0], "мкмоль/л", 202.0, 416.0),
    "С-реактивный белок":      ([4.8, 3.9, 2.6, 1.7],      "мг/л",    0.0, 5.0),
}

CONDITIONS = [
    ("Стеатоз печени (жировой гепатоз)", "active", "2023-11-14", "средняя",
     "Выявлен на УЗИ при чек-апе. Динамика на фоне снижения веса — положительная."),
    ("Дефицит витамина D", "resolved", "2023-11-14", "лёгкая",
     "Компенсирован приёмом холекальциферола, с 2026 — поддерживающая доза."),
    ("Дислипидемия", "active", "2023-11-14", "лёгкая",
     "ЛПНП выше цели, коррекция питанием без статинов. Контроль раз в полгода."),
    ("Предиабет (нарушенная гликемия натощак)", "active", "2024-06-03", "лёгкая",
     "Глюкоза натощак у верхней границы, HbA1c 5.7–5.9%. Наблюдение."),
]

MEDICATIONS = [
    ("Холекальциферол (витамин D3)", "5000 МЕ", "ежедневно утром", "2023-11-20", "2025-03-01",
     "Дефицит витамина D", "Насыщающая доза."),
    ("Холекальциферол (витамин D3)", "2000 МЕ", "ежедневно утром", "2025-03-01", None,
     "Поддержание уровня витамина D", "Поддерживающая доза."),
    ("Омега-3 (ЭПК+ДГК)", "1000 мг", "ежедневно с едой", "2024-01-10", None,
     "Триглицериды", None),
    ("Урсодезоксихолевая кислота", "500 мг", "на ночь", "2023-12-01", "2024-09-01",
     "Стеатоз печени", "Курс 9 месяцев, отменён после нормализации АЛТ."),
]

EVENTS = [
    ("2023-11-14", "визит", "Терапевт, первичный чек-ап",
     "Жалобы на утомляемость. Назначены биохимия, липидный профиль, УЗИ брюшной полости."),
    ("2023-11-16", "исследование", "УЗИ органов брюшной полости",
     "Диффузные изменения печени по типу жирового гепатоза. Желчный пузырь без конкрементов."),
    ("2024-06-03", "визит", "Терапевт, контроль",
     "Вес −7 кг за полгода. АЛТ снижается. Продолжить питание и омега-3."),
    ("2025-02-19", "визит", "Терапевт, годовой чек-ап",
     "Липиды улучшились, витамин D в норме. Обсуждён предиабет, статины пока не показаны."),
    ("2025-09-08", "симптом", "Эпизод изжоги после нагрузок",
     "Три эпизода за месяц, связаны с поздним ужином. ФГДС не проводилась."),
    ("2026-05-12", "визит", "Терапевт, контроль печени",
     "АЛТ впервые в референсе. Рекомендован контроль через 12 месяцев."),
    ("2026-05-14", "исследование", "УЗИ органов брюшной полости, контроль",
     "Признаки стеатоза сохраняются, но выражены слабее, чем в 2023 году."),
]


def find_analyte(cur, raw_name: str):
    key = raw_name.lower().replace("ё", "е")
    cur.execute("SELECT id FROM analytes WHERE lower(name_ru) = ?", (key,))
    row = cur.fetchone()
    if row:
        return row[0]
    norm = "".join(ch for ch in key if ch.isalnum())
    cur.execute("SELECT analyte_id, alias FROM analyte_aliases")
    for aid, alias in cur.fetchall():
        if "".join(ch for ch in alias.lower().replace("ё", "е") if ch.isalnum()) == norm:
            return aid
    return None


def flag_for(value, low, high):
    if low is not None and value < low:
        return "low"
    if high is not None and value > high:
        return "high"
    return "normal"


def remove(conn):
    cur = conn.cursor()
    cur.execute("SELECT id FROM subjects WHERE slug = ?", (SLUG,))
    row = cur.fetchone()
    if not row:
        print("Профиль мужа не найден — нечего убирать.")
        return
    sid = row[0]
    cur.execute("SELECT id FROM documents WHERE subject_id = ? AND parse_note = ?", (sid, DEMO_TAG))
    doc_ids = [r[0] for r in cur.fetchall()]
    for did in doc_ids:
        cur.execute("DELETE FROM results WHERE document_id = ?", (did,))
    cur.execute("DELETE FROM documents WHERE subject_id = ? AND parse_note = ?", (sid, DEMO_TAG))
    for table, col in (("conditions", "notes"), ("medications", "notes"), ("events", "details")):
        cur.execute(
            f"DELETE FROM {table} WHERE subject_id = ? AND {col} LIKE ?",
            (sid, f"{DEMO_TAG}%"),
        )
    cur.execute("UPDATE subjects SET notes = NULL WHERE id = ?", (sid,))
    conn.commit()
    print(f"Демо убрано: документов {len(doc_ids)}, вместе с результатами, состояниями, лекарствами и событиями.")


def seed(conn):
    cur = conn.cursor()
    # Имя не трогаем, если профиль уже назван по-человечески: демо — витрина,
    # а не способ переименовать живого члена семьи.
    row = cur.execute("SELECT name FROM subjects WHERE slug = ?", (SLUG,)).fetchone()
    current = row[0] if row else ""
    name = current if current and current not in ("Муж", "") else "Иванов Пётр Сергеевич"
    cur.execute(
        """UPDATE subjects
              SET name = ?, kind = 'human', sex = 'male', birthdate = ?, notes = ?
            WHERE slug = ?""",
        (
            name,
            "1990-01-01",
            f"{DEMO_TAG} Анализы и назначения в профиле вымышленные — витрина интерфейса, "
            f"не медицинские данные. Дата рождения тоже условная. "
            f"Убрать: python3 demo_seed_husband.py --remove",
            SLUG,
        ),
    )
    cur.execute("SELECT id FROM subjects WHERE slug = ?", (SLUG,))
    sid = cur.fetchone()[0]

    doc_ids = []
    for i, (d, title) in enumerate(PANELS):
        cur.execute(
            """INSERT INTO documents
                 (subject_id, kind, title, doc_date, lab_name, source,
                  stored_path, sha256, page_count, raw_text, parsed_ok, parse_note)
               VALUES (?, 'lab', ?, ?, ?, 'manual', NULL, ?, 1, ?, 1, ?)""",
            (
                sid,
                f"{DEMO_TAG} {title}",
                d,
                "Демо-лаборатория",
                f"demo-husband-{i}",
                f"{DEMO_TAG} вымышленный бланк, создан demo_seed_husband.py",
                DEMO_TAG,
            ),
        )
        doc_ids.append(cur.lastrowid)

    n_res = 0
    for raw_name, (values, unit, low, high) in SERIES.items():
        aid = find_analyte(cur, raw_name)
        for i, val in enumerate(values):
            cur.execute(
                """INSERT INTO results
                     (document_id, subject_id, analyte_id, raw_name, value_num,
                      value_text, unit, ref_low, ref_high, ref_text, flag, taken_at)
                   VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)""",
                (doc_ids[i], sid, aid, raw_name, val, unit, low, high,
                 f"{low} - {high}", flag_for(val, low, high), PANELS[i][0]),
            )
            n_res += 1

    for name, status, onset, severity, notes in CONDITIONS:
        cur.execute(
            """INSERT INTO conditions (subject_id, name, status, onset_date, severity, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (sid, name, status, onset, severity, f"{DEMO_TAG} {notes}"),
        )

    for name, dose, sched, started, ended, reason, notes in MEDICATIONS:
        cur.execute(
            """INSERT INTO medications
                 (subject_id, name, dose, schedule, started, ended, reason, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (sid, name, dose, sched, started, ended, reason,
             f"{DEMO_TAG} {notes or ''}".strip()),
        )

    for d, typ, title, details in EVENTS:
        cur.execute(
            """INSERT INTO events (subject_id, date, type, title, details)
               VALUES (?, ?, ?, ?, ?)""",
            (sid, d, typ, title, f"{DEMO_TAG} {details}"),
        )

    conn.commit()
    print(f"Демо залито: {len(doc_ids)} бланка, {n_res} результатов по {len(SERIES)} маркерам, "
          f"{len(CONDITIONS)} состояния, {len(MEDICATIONS)} назначения, {len(EVENTS)} событий.")
    print("Всё помечено «[ДЕМО]». Убрать: python3 demo_seed_husband.py --remove")


def main():
    with connect() as conn:
        if "--remove" in sys.argv:
            remove(conn)
        else:
            remove(conn)  # идемпотентность: сначала чистим прошлое демо
            seed(conn)


if __name__ == "__main__":
    main()
