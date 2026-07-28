"""FastAPI веб-интерфейс семейного хаба здоровья. Полностью локальный,
без внешних запросов, CDN и телеметрии. Запуск: hub.py serve (localhost:8765).
"""

from __future__ import annotations

import calendar as calendar_module
import datetime as dt
import re
import sqlite3
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db as db_module
from . import ingest as ingest_module
from .analytes import categories

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Семейный хаб здоровья")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.on_event("startup")
def _startup() -> None:
    db_module.init_db()


def asset_version(name: str) -> str:
    """Версия статики по времени изменения файла.

    Без неё браузер продолжает показывать закэшированные CSS и JS после
    правок — при локальной разработке это регулярно выглядит как «стили
    не применились», хотя на диске всё на месте.
    """
    try:
        return str(int((BASE_DIR / "static" / name).stat().st_mtime))
    except OSError:
        return "0"


def get_conn() -> sqlite3.Connection:
    return db_module.connect()


# ---------------------------------------------------------------------------
# Jinja-фильтры
# ---------------------------------------------------------------------------

def fmt_date(value: str | None) -> str:
    if not value:
        return "—"
    try:
        d = dt.date.fromisoformat(value[:10])
        months = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
        return f"{d.day} {months[d.month - 1]} {d.year}"
    except ValueError:
        return value


def fmt_num(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return f"{value:g}"
    return str(value)


templates.env.filters["fmt_date"] = fmt_date
templates.env.filters["fmt_num"] = fmt_num
templates.env.globals["asset_version"] = asset_version


MONTHS_RU = [
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]


def month_label(year: int, month: int) -> str:
    return f"{MONTHS_RU[month].capitalize()} {year}"


def month_bounds(year: int, month: int):
    """Дни выбранного месяца + координаты соседних месяцев для навигации."""
    first_day = dt.date(year, month, 1)
    days_in_month = calendar_module.monthrange(year, month)[1]
    days = [first_day + dt.timedelta(days=i) for i in range(days_in_month)]
    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    return days, prev_year, prev_month, next_year, next_month


def compute_age(birthdate: str | None) -> str | None:
    if not birthdate:
        return None
    try:
        b = dt.date.fromisoformat(birthdate[:10])
    except ValueError:
        return None
    today = dt.date.today()
    years = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
    return f"{years} лет" if years != 1 else "1 год"


def get_subject_or_404(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM subjects WHERE slug=?", (slug,)).fetchone()


# ---------------------------------------------------------------------------
# Главная
# ---------------------------------------------------------------------------

def attention_items(conn) -> list[dict]:
    """Собрать сводку «за чем следить» — главный экран хаба.

    Хаб бесполезен, если в нём просто лежит полторы тысячи цифр. Смысл
    появляется, когда видно три вещи: что не в порядке прямо сейчас, что
    ухудшается, и что пора пересдать. Именно это здесь и считается.

    Правила намеренно консервативные: показывается только то, что видно из
    самих данных (последнее значение вне референса, ухудшение относительно
    предыдущего, давность измерения). Никаких трактовок и диагнозов — хаб
    показывает, куда посмотреть, а не что это значит.
    """
    items: list[dict] = []

    subjects = conn.execute("SELECT * FROM subjects ORDER BY id").fetchall()
    for s in subjects:
        sid, slug, sname = s["id"], s["slug"], s["name"]

        # --- по каждому маркеру берём два последних числовых значения ---
        rows = conn.execute(
            """SELECT r.analyte_id, a.name_ru, a.code, r.value_num, r.unit,
                      r.ref_low, r.ref_high, r.flag, r.taken_at
                 FROM results r JOIN analytes a ON a.id = r.analyte_id
                WHERE r.subject_id = ? AND r.value_num IS NOT NULL AND r.taken_at IS NOT NULL
                ORDER BY r.analyte_id, r.taken_at DESC""",
            (sid,),
        ).fetchall()

        by_marker: dict[int, list] = {}
        for r in rows:
            by_marker.setdefault(r["analyte_id"], []).append(r)

        today = dt.date.today()
        for aid, hist in by_marker.items():
            # схлопнуть дубли одной даты (один заказ приходил двумя письмами)
            seen, uniq = set(), []
            for r in hist:
                key = (r["taken_at"], r["value_num"])
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(r)
            latest = uniq[0]
            prev = uniq[1] if len(uniq) > 1 else None
            try:
                age_days = (today - dt.date.fromisoformat(latest["taken_at"][:10])).days
            except ValueError:
                age_days = 0

            if latest["flag"] in ("low", "high"):
                worse = None
                if prev is not None and prev["value_num"] is not None:
                    if latest["flag"] == "high":
                        worse = latest["value_num"] > prev["value_num"]
                    else:
                        worse = latest["value_num"] < prev["value_num"]
                dev = deviation(latest["value_num"], latest["ref_low"], latest["ref_high"])
                items.append(
                    {
                        "dev_pct": dev[0] if dev else None,
                        "dev_dir": dev[1] if dev else None,
                        "level": "alert" if age_days <= 400 else "stale",
                        "subject": sname,
                        "slug": slug,
                        "title": latest["name_ru"],
                        "value": f'{fmt_num(latest["value_num"])} {latest["unit"] or ""}'.strip(),
                        "ref": ref_text_of(latest),
                        "date": latest["taken_at"],
                        "age_days": age_days,
                        "trend": ("ухудшается" if worse else ("улучшается" if worse is False else None)),
                        "link": f"/s/{slug}/marker/{latest['code']}",
                        "why": (
                            "вне нормы в последнем анализе"
                            if age_days <= 400
                            else f"вне нормы, и с тех пор не пересдавали — {age_days // 30} мес."
                        ),
                    }
                )

        # --- давно ли вообще сдавались анализы ---
        last_lab = conn.execute(
            """SELECT MAX(taken_at) d FROM results WHERE subject_id=? AND taken_at IS NOT NULL""",
            (sid,),
        ).fetchone()["d"]
        if last_lab:
            try:
                gap = (today - dt.date.fromisoformat(last_lab[:10])).days
            except ValueError:
                gap = 0
            if gap > 365:
                items.append(
                    {
                        "level": "stale",
                        "subject": sname,
                        "slug": slug,
                        "title": "Анализы не сдавались",
                        "value": f"{gap // 30} мес. назад",
                        "ref": "",
                        "date": last_lab,
                        "age_days": gap,
                        "trend": None,
                        "link": f"/s/{slug}/labs",
                        "why": "последние измерения устарели",
                    }
                )

    # Сначала свежие отклонения, внутри них — по величине выхода за границу:
    # 205 при верхней границе 70 должно стоять выше, чем 8,4 при 8,0.
    order = {"alert": 0, "stale": 1}
    items.sort(
        key=lambda x: (
            order.get(x["level"], 2),
            -(x.get("dev_pct") or 0),
            x["age_days"],
        )
    )
    return items


# Периодичность ветеринарной профилактики. Интервалы — общепринятая практика
# для собак; конкретную схему всё равно назначает врач, поэтому хаб только
# считает, сколько прошло, и не притворяется протоколом лечения.
VET_SCHEDULE = [
    ("вакцинация от бешенства", 365, ("бешенств", "antirabique", "rabies")),
    ("комплексная вакцинация", 365, ("вакцинац", "vaccination")),
    ("обработка от глистов", 90, ("эхинококк", "echinococc", "глист", "дегельминт")),
    ("обработка от паразитов", 90, ("антипаразит", "antiparasit", "паразит")),
]


# Ручные записи профилактики заводятся с одним из трёх крупных типов
# (проглистовка/антипаразитарная обработка/вакцинация — так проще заполнять
# форму), а в VET_SCHEDULE вакцинация разбита на бешенство и комплексную
# отдельно, потому что у них разные последствия просрочки. Ручная запись
# «вакцинация» без уточнения засчитывается в обе — она не может быть точнее
# того, что выбрал человек в форме.
PROPHYLAXIS_KIND_BY_LABEL = {
    "вакцинация от бешенства": "вакцинация",
    "комплексная вакцинация": "вакцинация",
    "обработка от глистов": "глисты",
    "обработка от паразитов": "паразиты",
}


def vet_due(conn, subject_id: int) -> list[dict]:
    """Когда пора повторить прививки и обработки.

    Даты берутся из ветпаспорта, который уже разобран в документы, и из
    ручных записей на странице субъекта («Профилактика»). Смысл в том, что
    просроченная прививка от бешенства — это не только про здоровье собаки:
    без действующей вакцинации не выпустят из страны и не примут в клинике.
    Хаб просто считает, сколько прошло с последнего раза — по каждому типу
    берётся самая свежая дата из обоих источников.
    """
    docs = conn.execute(
        """SELECT doc_date, title, kind FROM documents
            WHERE subject_id=? AND doc_date IS NOT NULL
            ORDER BY doc_date DESC""",
        (subject_id,),
    ).fetchall()
    manual = conn.execute(
        """SELECT kind, date FROM prophylaxis WHERE subject_id=? ORDER BY date DESC""",
        (subject_id,),
    ).fetchall()
    today = dt.date.today()
    out = []
    for label, period, keys in VET_SCHEDULE:
        last = None
        for d in docs:
            hay = f"{d['title'] or ''} {d['kind'] or ''}".lower()
            if any(k in hay for k in keys):
                last = d["doc_date"]
                break

        manual_kind = PROPHYLAXIS_KIND_BY_LABEL.get(label)
        for m in manual:
            if m["kind"] == manual_kind:
                if last is None or m["date"] > last:
                    last = m["date"]
                break  # manual уже отсортирован по дате убыв. — первый совпавший самый свежий

        if not last:
            continue
        try:
            passed = (today - dt.date.fromisoformat(last[:10])).days
        except ValueError:
            continue
        left = period - passed
        out.append(
            {
                "label": label,
                "last": last,
                "passed": passed,
                "days_left": left,
                "overdue": left < 0,
                "soon": 0 <= left <= 45,
            }
        )
    # просроченное первым, дальше — по близости срока
    out.sort(key=lambda x: x["days_left"])
    return out


# ---------------------------------------------------------------------------
# Календарь таблеток
# ---------------------------------------------------------------------------

def doses_per_day(schedule: str | None) -> int:
    """Сколько раз в сутки принимать — из свободного текста схемы приёма.

    Схема вводится произвольным текстом («по 1 таб. 2 раза в сутки», «1 раз
    в сутки», «по 1/4»), парсера на неё нет и не будет — вместо этого просто
    ищем число перед словом «раз». Не нашли — считаем разумным дефолтом один
    приём в день.
    """
    if not schedule:
        return 1
    m = re.search(r"(\d+)\s*раз", schedule, re.IGNORECASE)
    if not m:
        return 1
    n = int(m.group(1))
    return n if n > 0 else 1


def toggle_dose(conn: sqlite3.Connection, subject_id: int, medication_id: int, date: str, slot: int) -> bool:
    """Переключить отметку о приёме. Возвращает новое состояние (принято/нет)."""
    row = conn.execute(
        "SELECT * FROM med_doses WHERE medication_id=? AND date=? AND slot=?",
        (medication_id, date, slot),
    ).fetchone()
    now = dt.datetime.now().isoformat(timespec="seconds")
    if row is None:
        conn.execute(
            """INSERT INTO med_doses(subject_id, medication_id, date, slot, taken, marked_at)
               VALUES (?,?,?,?,1,?)""",
            (subject_id, medication_id, date, slot, now),
        )
        return True
    new_taken = 0 if row["taken"] else 1
    conn.execute(
        "UPDATE med_doses SET taken=?, marked_at=? WHERE id=?",
        (new_taken, now if new_taken else None, row["id"]),
    )
    return bool(new_taken)


# ---------------------------------------------------------------------------
# Вес и породный коридор
# ---------------------------------------------------------------------------

# Целевой диапазон веса по породе — справочно, для собак. Конкретную схему
# снижения/набора веса в любом случае определяет ветеринар; хаб только
# сравнивает текущий вес со стандартом породы.
BREED_WEIGHT: dict[str, tuple[float, float]] = {
    "мальтийская болонка": (3.0, 4.0),
}


def breed_weight_range(breed: str | None) -> tuple[float, float] | None:
    if not breed:
        return None
    key = breed.strip().lower()
    for name, rng in BREED_WEIGHT.items():
        if name in key:
            return rng
    return None


def weight_over_breed_range(kg: float | None, breed: str | None) -> dict | None:
    """Насколько текущий вес выше верхней границы породного коридора.

    Тон нейтральный: только факт (на сколько % и кг выше верхней границы).
    Никаких рекомендаций по лечению — это дело ветеринара.
    """
    if kg is None:
        return None
    rng = breed_weight_range(breed)
    if not rng:
        return None
    low, high = rng
    if kg <= high:
        return {"low": low, "high": high, "over": False}
    pct = round((kg - high) / high * 100)
    over_kg = round(kg - high, 2)
    return {"low": low, "high": high, "over": True, "pct": pct, "over_kg": over_kg}


def subject_summary(conn, subject) -> dict:
    """Сводка по субъекту: что видно из данных и что с этим делать.

    Жёсткая граница: хаб не ставит диагнозов и не назначает лечение. Здесь
    только два источника.

    Первый — арифметика по собственным данным: какие показатели вне
    референса и насколько, что давно не пересдавали, просрочена ли
    профилактика, идёт ли курс лекарств, укладывается ли вес в породный
    коридор. Это факты, а не трактовки.

    Второй — рекомендации, которые уже написал врач в загруженных
    документах. Они цитируются как есть, со ссылкой на документ. Хаб их
    не сочиняет и не дополняет: придуманный совет в медицинском
    интерфейсе опаснее его отсутствия.
    """
    sid = subject["id"]
    today = dt.date.today()
    facts: list[dict] = []
    actions: list[dict] = []

    # --- показатели вне референса ---------------------------------------
    rows = conn.execute(
        """SELECT r.analyte_id, a.name_ru, a.code, r.value_num, r.unit,
                  r.ref_low, r.ref_high, r.flag, r.taken_at
             FROM results r JOIN analytes a ON a.id = r.analyte_id
            WHERE r.subject_id = ? AND r.value_num IS NOT NULL AND r.taken_at IS NOT NULL
            ORDER BY r.analyte_id, r.taken_at DESC""",
        (sid,),
    ).fetchall()
    latest_by_marker: dict[int, sqlite3.Row] = {}
    for r in rows:
        latest_by_marker.setdefault(r["analyte_id"], r)

    off = []
    for r in latest_by_marker.values():
        if r["flag"] not in ("low", "high"):
            continue
        dev = deviation(r["value_num"], r["ref_low"], r["ref_high"])
        try:
            age = (today - dt.date.fromisoformat(r["taken_at"][:10])).days
        except ValueError:
            age = 0
        off.append(
            {
                "name": r["name_ru"], "code": r["code"], "flag": r["flag"],
                "value": f'{fmt_num(r["value_num"])} {r["unit"] or ""}'.strip(),
                "pct": dev[0] if dev else None, "dir": dev[1] if dev else None,
                "date": r["taken_at"], "age_days": age,
            }
        )
    off.sort(key=lambda x: -(x["pct"] or 0))

    if off:
        worst = off[0]
        headline = f"{len(off)} показател{_plural(len(off), 'ь', 'я', 'ей')} вне референса"
        if worst["pct"]:
            headline += f", сильнее всего — {worst['name'].lower()} {'↑' if worst['dir'] == 'выше' else '↓'}{worst['pct']}%"
        facts.append({
            # ключ намеренно не "items": в Jinja `f.items` разрешится в метод
            # словаря dict.items, а не в список, и шаблон молча сломается
            "kind": "off",
            "text": headline,
            "chips": off[:6],
        })
        stale = [o for o in off if o["age_days"] > 365]
        if stale:
            actions.append({
                "text": f"Пересдать то, что вне нормы и не проверялось больше года — {len(stale)} показател{_plural(len(stale), 'ь', 'я', 'ей')}",
                "why": "хаб не знает, изменилось ли значение с тех пор",
            })
    else:
        facts.append({"kind": "ok", "text": "Показателей вне референса в последних анализах нет", "chips": []})

    # --- давность последних анализов ------------------------------------
    last_lab = conn.execute(
        "SELECT MAX(doc_date) d FROM documents WHERE subject_id=? AND kind='lab'", (sid,)
    ).fetchone()["d"]
    if last_lab:
        try:
            months = (today - dt.date.fromisoformat(last_lab[:10])).days // 30
        except ValueError:
            months = 0
        if months >= 12:
            actions.append({
                "text": f"Последние анализы сдавались {months} мес. назад",
                "why": "обычная периодичность контроля — раз в год",
            })

    # --- активные курсы лекарств ----------------------------------------
    active_meds = conn.execute(
        """SELECT name, dose, schedule, started, ended FROM medications
            WHERE subject_id=? AND (ended IS NULL OR ended >= ?)
              AND (started IS NULL OR started <= ?)""",
        (sid, today.isoformat(), today.isoformat()),
    ).fetchall()
    if active_meds:
        names = ", ".join(m["name"].split("(")[0].strip() for m in active_meds)
        ends = [m["ended"] for m in active_meds if m["ended"]]
        tail = f", ближайший курс до {fmt_date(min(ends))}" if ends else ""
        facts.append({"kind": "meds", "text": f"Идёт курс: {names[:90]}{tail}", "chips": []})

    # --- профилактика и вес: только для собаки --------------------------
    if subject["kind"] == "dog":
        for v in vet_due(conn, sid):
            if v["overdue"]:
                actions.append({
                    "text": f"Просрочена {v['label']} — на {-v['days_left']} дн.",
                    "why": f"последний раз {fmt_date(v['last'])}",
                })
        w = conn.execute(
            "SELECT kg, date FROM weights WHERE subject_id=? ORDER BY date DESC LIMIT 1", (sid,)
        ).fetchone()
        rng = breed_weight_range(subject["breed"])
        if w and rng and w["kg"] > rng[1]:
            over = round((w["kg"] - rng[1]) / rng[1] * 100)
            facts.append({
                "kind": "weight",
                "text": f"Вес {fmt_num(w['kg'])} кг — на {over}% выше породного коридора {fmt_num(rng[0])}–{fmt_num(rng[1])} кг",
                "chips": [],
            })

    # --- что уже сказали врачи ------------------------------------------
    doctor_notes = []
    for d in conn.execute(
        """SELECT id, title, doc_date, raw_text FROM documents
            WHERE subject_id=? AND raw_text IS NOT NULL
              AND (raw_text LIKE '%Рекомендаци%' OR raw_text LIKE '%РЕКОМЕНДАЦИИ%')
            ORDER BY doc_date DESC LIMIT 3""",
        (sid,),
    ).fetchall():
        items = extract_recommendations(d["raw_text"] or "")
        if items:
            doctor_notes.append({
                "doc_id": d["id"], "title": d["title"], "date": d["doc_date"],
                "lines": items,
            })

    return {"facts": facts, "actions": actions, "doctor_notes": doctor_notes}


# Юридические оговорки, которыми заканчивается почти каждый бланк. Это не
# рекомендации врача, и показывать их вместо назначений — худшее, что может
# сделать сводка.
_DISCLAIMER_MARKERS = (
    "не является", "требует интерпрет", "требует интерприт", "не заменяет",
    "только врачом", "консультация специалиста", "окончательным диагнозом",
    "запись на прием", "запись на приём",
)

# Строка настоящей рекомендации: пункт нумерованного списка или маркер.
_RECO_ITEM_RE = re.compile(r"^\s*(?:\d{1,2}[).]|[•\-–—*])\s*(?P<body>.{8,300})$")

_RECO_START_RE = re.compile(r"рекомендаци", re.I)
_RECO_STOP_RE = re.compile(
    r"(подпись|исполнитель|врач\s*:|дата печати|лицензи|страница|©|препараты\s*:|номенклат)", re.I
)


def extract_recommendations(text: str, limit: int = 6) -> list[str]:
    """Вытащить пункты рекомендаций врача из текста документа.

    Берутся только пункты списка после слова «Рекомендации» — то, что врач
    действительно назначил. Юридические оговорки и шапка отбрасываются:
    в сводке они выглядят как совет, хотя советом не являются.
    """
    lines = text.splitlines()
    # Слово «рекомендация» встречается и в названии документа («Рекомендация
    # по лечению №000000003»), и в шапке. Поэтому пробуем каждое вхождение и
    # берём первое, из которого действительно вышел список пунктов.
    starts = [i + 1 for i, line in enumerate(lines) if _RECO_START_RE.search(line)]
    for start in starts:
        items = _collect_items(lines, start, limit)
        if items:
            return items
    return []


def _collect_items(lines: list[str], start: int, limit: int) -> list[str]:
    out: list[str] = []
    for line in lines[start : start + 40]:
        s = line.strip()
        if not s:
            continue
        if _RECO_STOP_RE.search(s):
            break
        low = s.lower()
        if any(k in low for k in _DISCLAIMER_MARKERS):
            continue
        m = _RECO_ITEM_RE.match(s)
        if m:
            out.append(m.group("body").strip())
        elif out and len(s) < 120 and not re.match(r"^[А-ЯЁ][а-яё]+\s*:", s):
            # продолжение предыдущего пункта, разорванного переносом
            out[-1] = f"{out[-1]} {s}"
        if len(out) >= limit:
            break
    return [o for o in out if len(o) > 8]


def _plural(n: int, one: str, few: str, many: str) -> str:
    if 11 <= n % 100 <= 14:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def care_rhythm(conn) -> dict:
    """Карта обследований по месяцам — по клетке на месяц, по строке на человека.

    Первая версия была столбиками по годам с общей шкалой, и это оказалось
    плохо: у собаки и мужа по одному-двум документам в год, рядом с пиком
    Жени в 56 их столбики схлопывались в ниточку, а полпанели занимала
    пустота. Здесь разреженность работает на график, а не против: пустая
    клетка так же информативна, как заполненная, и ряд с редкими событиями
    читается не хуже плотного.

    Насыщенность клетки — четыре ступени, а не непрерывная шкала: точное
    число документов за месяц ничего не значит (один заказ анализов может
    прийти пятью бланками), значение имеет сам факт и примерная плотность.
    """
    rows = conn.execute(
        """SELECT s.id, s.slug, s.name, s.kind, substr(d.doc_date, 1, 7) AS ym, COUNT(*) n
             FROM documents d JOIN subjects s ON s.id = d.subject_id
            WHERE d.doc_date IS NOT NULL AND length(d.doc_date) >= 7
            GROUP BY s.id, ym"""
    ).fetchall()
    if not rows:
        return {"months": [], "subjects": []}

    all_ym = sorted({r["ym"] for r in rows})
    start_y, start_m = int(all_ym[0][:4]), int(all_ym[0][5:7])
    today = dt.date.today()

    months = []
    y, m = start_y, start_m
    while (y, m) <= (today.year, today.month):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1

    by_subject: dict[int, dict] = {}
    for r in rows:
        s = by_subject.setdefault(
            r["id"], {"slug": r["slug"], "name": r["name"], "kind": r["kind"], "counts": {}}
        )
        s["counts"][r["ym"]] = r["n"]

    def level(n: int) -> int:
        if not n:
            return 0
        if n == 1:
            return 1
        if n <= 3:
            return 2
        if n <= 7:
            return 3
        return 4

    subjects = []
    for s in by_subject.values():
        cells = []
        for ym in months:
            n = s["counts"].get(ym, 0)
            mm = int(ym[5:7])
            cells.append(
                {
                    "ym": ym,
                    "n": n,
                    "level": level(n),
                    "label": f"{MONTHS_RU[mm]} {ym[:4]}",
                    # январь помечаем, чтобы годы читались без отдельной оси
                    "year_start": mm == 1,
                    "year": ym[:4],
                }
            )
        subjects.append(
            {
                **s,
                "cells": cells,
                "total": sum(s["counts"].values()),
                "months_covered": len(s["counts"]),
            }
        )

    # подписи лет — по одной на январь, плюс самый первый месяц
    year_marks = []
    for i, ym in enumerate(months):
        if ym[5:7] == "01" or i == 0:
            year_marks.append({"index": i, "year": ym[:4]})

    return {"months": months, "subjects": subjects, "year_marks": year_marks}


def meds_progress(conn) -> list[dict]:
    """Насколько выполнен курс лекарств в текущем месяце — по каждому субъекту."""
    today = dt.date.today()
    first = today.replace(day=1)
    last_day = calendar_module.monthrange(today.year, today.month)[1]
    last = today.replace(day=last_day)

    out = []
    for s in conn.execute("SELECT * FROM subjects ORDER BY id").fetchall():
        meds = conn.execute(
            """SELECT * FROM medications
                WHERE subject_id=? AND (ended IS NULL OR ended >= ?)
                  AND (started IS NULL OR started <= ?)""",
            (s["id"], first.isoformat(), last.isoformat()),
        ).fetchall()
        if not meds:
            continue
        total = 0
        for m in meds:
            per_day = doses_per_day(m["schedule"])
            try:
                start = max(first, dt.date.fromisoformat(m["started"][:10])) if m["started"] else first
            except ValueError:
                start = first
            finish = min(last, today)
            if finish < start:
                continue
            total += per_day * ((finish - start).days + 1)
        taken = conn.execute(
            """SELECT COUNT(*) c FROM med_doses
                WHERE subject_id=? AND taken=1 AND date BETWEEN ? AND ?""",
            (s["id"], first.isoformat(), last.isoformat()),
        ).fetchone()["c"]
        if total <= 0:
            continue
        out.append(
            {
                "slug": s["slug"],
                "name": s["name"],
                "taken": taken,
                "total": total,
                "pct": min(100, round(100 * taken / total)),
                "meds": [m["name"] for m in meds],
            }
        )
    return out


def deviation(value, ref_low, ref_high) -> tuple[int, str] | None:
    """На сколько процентов значение вышло за границу референса.

    Бинарное «вне нормы» не различает 8,4 при верхней границе 8,0 и 205 при
    верхней 70 — а разница между ними принципиальная. Считается отклонение
    от ближайшей границы диапазона, как это делает ВЕТЛАБ в своих бланках
    («▲46%»). Проценты берутся от самой границы, а не от ширины диапазона:
    так число не зависит от того, насколько широкой лаборатория объявила
    норму, и остаётся сопоставимым между лабораториями.
    """
    if value is None:
        return None
    if ref_high is not None and value > ref_high and ref_high > 0:
        return round((value - ref_high) / ref_high * 100), "выше"
    if ref_low is not None and value < ref_low and ref_low > 0:
        return round((ref_low - value) / ref_low * 100), "ниже"
    return None


def ref_text_of(row) -> str:
    low, high = row["ref_low"], row["ref_high"]
    if low is not None and high is not None:
        return f"норма {fmt_num(low)}–{fmt_num(high)}"
    if high is not None:
        return f"норма до {fmt_num(high)}"
    if low is not None:
        return f"норма от {fmt_num(low)}"
    return ""


@app.get("/")
def index(request: Request):
    conn = get_conn()
    try:
        subjects = conn.execute("SELECT * FROM subjects ORDER BY id").fetchall()
        subject_cards = []
        for s in subjects:
            doc_count = conn.execute(
                "SELECT COUNT(*) c FROM documents WHERE subject_id=?", (s["id"],)
            ).fetchone()["c"]
            last_doc = conn.execute(
                "SELECT * FROM documents WHERE subject_id=? ORDER BY doc_date DESC, added_at DESC LIMIT 1",
                (s["id"],),
            ).fetchone()
            abnormal = conn.execute(
                """SELECT COUNT(*) c FROM results
                   WHERE subject_id=? AND flag IN ('low','high')
                   AND taken_at >= date('now','-1 year')""",
                (s["id"],),
            ).fetchone()["c"]
            subject_cards.append(
                {
                    "row": s,
                    "age": compute_age(s["birthdate"]),
                    "doc_count": doc_count,
                    "last_doc": last_doc,
                    "abnormal": abnormal,
                }
            )

        recent_docs = conn.execute(
            """SELECT documents.*, subjects.name AS subject_name, subjects.slug AS subject_slug
               FROM documents LEFT JOIN subjects ON subjects.id = documents.subject_id
               ORDER BY doc_date DESC, added_at DESC LIMIT 10"""
        ).fetchall()

        out_of_range = conn.execute(
            """SELECT results.*, subjects.name AS subject_name, subjects.slug AS subject_slug,
                      analytes.name_ru AS analyte_name, analytes.code AS analyte_code
               FROM results
               JOIN subjects ON subjects.id = results.subject_id
               LEFT JOIN analytes ON analytes.id = results.analyte_id
               WHERE results.flag IN ('low','high') AND results.taken_at >= date('now','-1 year')
               ORDER BY results.taken_at DESC LIMIT 30"""
        ).fetchall()

        needs_review = conn.execute("SELECT COUNT(*) c FROM documents WHERE subject_id IS NULL").fetchone()["c"]

        attention = attention_items(conn)

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "subject_cards": subject_cards,
                "recent_docs": recent_docs,
                "out_of_range": out_of_range,
                "needs_review": needs_review,
                "attention": attention,
                "attention_now": [a for a in attention if a["level"] == "alert"],
                # просроченного обычно много и оно однотипное — на главной
                # показываем верхушку, остальное живёт на страницах анализов
                "attention_stale": [a for a in attention if a["level"] == "stale"][:8],
                "attention_stale_more": max(0, len([a for a in attention if a["level"] == "stale"]) - 8),
                "rhythm": care_rhythm(conn),
                "meds_progress": meds_progress(conn),
            },
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Профиль субъекта
# ---------------------------------------------------------------------------

@app.get("/s/{slug}")
def subject_profile(request: Request, slug: str):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if not subject:
            return RedirectResponse("/", status_code=302)

        conditions = conn.execute(
            "SELECT * FROM conditions WHERE subject_id=? ORDER BY (status='active') DESC, onset_date DESC",
            (subject["id"],),
        ).fetchall()
        medications = conn.execute(
            "SELECT * FROM medications WHERE subject_id=? ORDER BY (ended IS NULL) DESC, started DESC",
            (subject["id"],),
        ).fetchall()
        # Список последних анализов — по документам, а не по отдельным
        # показателям. Строка на каждый показатель превращала блок в простыню
        # из пятнадцати строк одного и того же бланка, и добраться до самого
        # бланка было нельзя. Здесь строка — это сданный анализ целиком, с
        # переходом на документ и его оригинал.
        recent_lab_docs = conn.execute(
            """SELECT d.id, d.title, d.doc_date, d.lab_name, d.kind, d.stored_path,
                      COUNT(r.id) AS n_results,
                      SUM(CASE WHEN r.flag IN ('low','high') THEN 1 ELSE 0 END) AS n_abnormal
                 FROM documents d LEFT JOIN results r ON r.document_id = d.id
                WHERE d.subject_id = ?
                GROUP BY d.id
                ORDER BY d.doc_date DESC, d.id DESC
                LIMIT 12""",
            (subject["id"],),
        ).fetchall()

        # плитка ключевых маркеров: последнее значение + динамика по каждому
        # аналиту, по которому есть хотя бы один результат
        marker_rows = conn.execute(
            """SELECT DISTINCT analyte_id FROM results
               WHERE subject_id=? AND analyte_id IS NOT NULL""",
            (subject["id"],),
        ).fetchall()
        tiles = []
        for mr in marker_rows:
            history = conn.execute(
                """SELECT * FROM results WHERE subject_id=? AND analyte_id=?
                   ORDER BY taken_at DESC LIMIT 2""",
                (subject["id"], mr["analyte_id"]),
            ).fetchall()
            if not history:
                continue
            analyte = get_analyte_row(conn, mr["analyte_id"])
            latest = history[0]
            prev = history[1] if len(history) > 1 else None
            trend = "→"
            if prev and prev["value_num"] is not None and latest["value_num"] is not None:
                if latest["value_num"] > prev["value_num"]:
                    trend = "↑"
                elif latest["value_num"] < prev["value_num"]:
                    trend = "↓"
            tiles.append({"analyte": analyte, "latest": latest, "trend": trend})
        tiles.sort(key=lambda t: t["latest"]["taken_at"] or "", reverse=True)

        events = conn.execute(
            "SELECT * FROM events WHERE subject_id=? ORDER BY date DESC LIMIT 10",
            (subject["id"],),
        ).fetchall()

        sparklines = important_markers(conn, subject["id"])

        prophylaxis_records = (
            conn.execute(
                "SELECT * FROM prophylaxis WHERE subject_id=? ORDER BY date DESC LIMIT 20",
                (subject["id"],),
            ).fetchall()
            if subject["kind"] == "dog"
            else []
        )

        weight_rows = conn.execute(
            "SELECT * FROM weights WHERE subject_id=? ORDER BY date DESC, id DESC LIMIT 20",
            (subject["id"],),
        ).fetchall()
        weight_chart_rows = conn.execute(
            "SELECT * FROM weights WHERE subject_id=? ORDER BY date ASC, id ASC",
            (subject["id"],),
        ).fetchall()
        weight_svg = build_weight_svg(weight_chart_rows, breed=subject["breed"]) if weight_chart_rows else ""
        latest_weight = weight_rows[0] if weight_rows else None
        weight_alert = (
            weight_over_breed_range(latest_weight["kg"], subject["breed"]) if latest_weight else None
        )

        feeding_rows = conn.execute(
            """SELECT * FROM feeding WHERE subject_id=?
               ORDER BY (started IS NULL), started DESC, id DESC""",
            (subject["id"],),
        ).fetchall()
        current_feeding = feeding_rows[0] if feeding_rows else None

        return templates.TemplateResponse(
            request,
            "subject.html",
            {
                "request": request,
                "subject": subject,
                "age": compute_age(subject["birthdate"]),
                "conditions": conditions,
                "medications": medications,
                "recent_lab_docs": recent_lab_docs,
                "tiles": tiles,
                "sparklines": sparklines,
                "events": events,
                "vet_due": vet_due(conn, subject["id"]) if subject["kind"] == "dog" else [],
                "summary": subject_summary(conn, subject),
                "prophylaxis_records": prophylaxis_records,
                "weight_rows": weight_rows,
                "weight_svg": weight_svg,
                "weight_alert": weight_alert,
                "feeding_rows": feeding_rows,
                "current_feeding": current_feeding,
            },
        )
    finally:
        conn.close()


def get_analyte_row(conn: sqlite3.Connection, analyte_id: int | None):
    if analyte_id is None:
        return None
    return conn.execute("SELECT * FROM analytes WHERE id=?", (analyte_id,)).fetchone()


@app.post("/s/{slug}/edit")
def subject_edit(
    slug: str,
    name: str = Form(...),
    sex: str = Form(""),
    birthdate: str = Form(""),
    species: str = Form(""),
    breed: str = Form(""),
    notes: str = Form(""),
):
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE subjects SET name=?, sex=?, birthdate=?, species=?, breed=?, notes=? WHERE slug=?",
            (name.strip(), sex.strip() or None, birthdate.strip() or None, species.strip() or None,
             breed.strip() or None, notes.strip() or None, slug),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/s/{slug}", status_code=303)


# --- формы добавления (состояние / лекарство / событие / заметка) ---------

@app.post("/s/{slug}/conditions/add")
def add_condition(
    slug: str,
    name: str = Form(...),
    status: str = Form("active"),
    onset_date: str = Form(""),
    severity: str = Form(""),
    notes: str = Form(""),
):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if subject:
            conn.execute(
                "INSERT INTO conditions(subject_id, name, status, onset_date, severity, notes) VALUES (?,?,?,?,?,?)",
                (subject["id"], name.strip(), status, onset_date or None, severity or None, notes or None),
            )
            conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/s/{slug}", status_code=303)


@app.post("/s/{slug}/medications/add")
def add_medication(
    slug: str,
    name: str = Form(...),
    dose: str = Form(""),
    schedule: str = Form(""),
    started: str = Form(""),
    ended: str = Form(""),
    reason: str = Form(""),
    notes: str = Form(""),
):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if subject:
            conn.execute(
                """INSERT INTO medications(subject_id, name, dose, schedule, started, ended, reason, notes)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (subject["id"], name.strip(), dose or None, schedule or None, started or None,
                 ended or None, reason or None, notes or None),
            )
            conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/s/{slug}", status_code=303)


@app.post("/s/{slug}/events/add")
def add_event(
    slug: str,
    date: str = Form(...),
    type: str = Form("визит"),
    title: str = Form(...),
    details: str = Form(""),
):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if subject:
            conn.execute(
                "INSERT INTO events(subject_id, date, type, title, details) VALUES (?,?,?,?,?)",
                (subject["id"], date, type, title.strip(), details or None),
            )
            conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/s/{slug}/timeline", status_code=303)


@app.post("/s/{slug}/notes/add")
def add_note(slug: str, title: str = Form(""), body: str = Form(...), date: str = Form("")):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        sid = subject["id"] if subject else None
        conn.execute(
            "INSERT INTO notes(subject_id, date, title, body) VALUES (?,?,?,?)",
            (sid, date or dt.date.today().isoformat(), title or None, body.strip()),
        )
        conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/s/{slug}", status_code=303)


@app.post("/s/{slug}/prophylaxis")
def add_prophylaxis(
    slug: str,
    kind: str = Form(...),
    date: str = Form(...),
    drug: str = Form(""),
    note: str = Form(""),
):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if subject:
            conn.execute(
                "INSERT INTO prophylaxis(subject_id, kind, date, drug, note) VALUES (?,?,?,?,?)",
                (subject["id"], kind.strip(), date, drug.strip() or None, note.strip() or None),
            )
            conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/s/{slug}", status_code=303)


@app.post("/s/{slug}/prophylaxis/{pid}/delete")
def delete_prophylaxis(slug: str, pid: int):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if subject:
            conn.execute("DELETE FROM prophylaxis WHERE id=? AND subject_id=?", (pid, subject["id"]))
            conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/s/{slug}", status_code=303)


@app.post("/s/{slug}/weights/add")
def add_weight(slug: str, date: str = Form(...), kg: str = Form(...), note: str = Form("")):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if subject:
            try:
                kg_val = float(kg.strip().replace(",", "."))
            except ValueError:
                kg_val = None
            if kg_val is not None:
                conn.execute(
                    "INSERT INTO weights(subject_id, date, kg, note) VALUES (?,?,?,?)",
                    (subject["id"], date, kg_val, note.strip() or None),
                )
                conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/s/{slug}", status_code=303)


@app.post("/s/{slug}/feeding")
def add_feeding(
    slug: str,
    food: str = Form(...),
    portion_g: str = Form(""),
    times_per_day: str = Form(""),
    started: str = Form(""),
    note: str = Form(""),
):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if subject:
            portion = None
            if portion_g.strip():
                try:
                    portion = float(portion_g.strip().replace(",", "."))
                except ValueError:
                    portion = None
            times = None
            if times_per_day.strip():
                try:
                    times = int(times_per_day.strip())
                except ValueError:
                    times = None
            conn.execute(
                """INSERT INTO feeding(subject_id, started, food, portion_g, times_per_day, note)
                   VALUES (?,?,?,?,?,?)""",
                (subject["id"], started or None, food.strip(), portion, times, note.strip() or None),
            )
            conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/s/{slug}", status_code=303)


@app.post("/s/{slug}/feeding/{fid}/delete")
def delete_feeding(slug: str, fid: int):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if subject:
            conn.execute("DELETE FROM feeding WHERE id=? AND subject_id=?", (fid, subject["id"]))
            conn.commit()
    finally:
        conn.close()
    return RedirectResponse(f"/s/{slug}", status_code=303)


# ---------------------------------------------------------------------------
# Календарь таблеток
# ---------------------------------------------------------------------------

@app.get("/s/{slug}/meds")
def meds_calendar(request: Request, slug: str, year: int | None = None, month: int | None = None):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if not subject:
            return RedirectResponse("/", status_code=302)

        today = dt.date.today()
        y, m = year or today.year, month or today.month
        days, prev_year, prev_month, next_year, next_month = month_bounds(y, m)

        meds = conn.execute(
            """SELECT * FROM medications WHERE subject_id=? AND (ended IS NULL OR ended >= ?)
               ORDER BY (ended IS NULL) DESC, started""",
            (subject["id"], today.isoformat()),
        ).fetchall()

        doses_by_med_day: dict[tuple[int, str], dict[int, bool]] = {}
        if meds:
            med_ids = [med["id"] for med in meds]
            placeholders = ",".join("?" for _ in med_ids)
            rows = conn.execute(
                f"""SELECT * FROM med_doses WHERE medication_id IN ({placeholders})
                    AND date BETWEEN ? AND ?""",
                (*med_ids, days[0].isoformat(), days[-1].isoformat()),
            ).fetchall()
            for r in rows:
                doses_by_med_day.setdefault((r["medication_id"], r["date"]), {})[r["slot"]] = bool(r["taken"])

        med_calendars = []
        total_taken = total_slots = 0
        for med in meds:
            n_doses = doses_per_day(med["schedule"])
            started = dt.date.fromisoformat(med["started"][:10]) if med["started"] else None
            ended = dt.date.fromisoformat(med["ended"][:10]) if med["ended"] else None
            day_cells = []
            for d in days:
                active = (started is None or d >= started) and (ended is None or d <= ended)
                slots = []
                for slot in range(1, n_doses + 1):
                    taken = doses_by_med_day.get((med["id"], d.isoformat()), {}).get(slot, False)
                    if active:
                        total_slots += 1
                        if taken:
                            total_taken += 1
                    slots.append({"slot": slot, "taken": taken})
                day_cells.append({"date": d, "active": active, "slots": slots})
            med_calendars.append({"med": med, "doses": n_doses, "days": day_cells})

        return templates.TemplateResponse(
            request,
            "meds.html",
            {
                "request": request,
                "subject": subject,
                "days": days,
                "today": today,
                "year": y,
                "month": m,
                "month_label": month_label(y, m),
                "prev_year": prev_year,
                "prev_month": prev_month,
                "next_year": next_year,
                "next_month": next_month,
                "med_calendars": med_calendars,
                "total_taken": total_taken,
                "total_slots": total_slots,
            },
        )
    finally:
        conn.close()


@app.post("/s/{slug}/meds/toggle")
def meds_toggle(
    slug: str,
    medication_id: int = Form(...),
    date: str = Form(...),
    slot: int = Form(1),
    year: str = Form(""),
    month: str = Form(""),
):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if subject:
            toggle_dose(conn, subject["id"], medication_id, date, slot)
            conn.commit()
    finally:
        conn.close()
    qs = f"?year={year}&month={month}" if year and month else ""
    return RedirectResponse(f"/s/{slug}/meds{qs}", status_code=303)


# ---------------------------------------------------------------------------
# Календарь настроения
# ---------------------------------------------------------------------------

# Наборы состояний разные для людей и для собаки. У человека самочувствие
# различается тоньше и его полезно разделять — «устал» и «подавлен» это не
# одно и то же, и увидеть, чего в месяце было больше, имеет смысл. У собаки
# наблюдаемых состояний по сути пять: владелец видит поведение, а не
# самоощущение, и длинный список только заставит гадать.
#
# Группа задаёт цвет: хорошее — зелёное, нейтральное — песочное, тяжёлое —
# терракотовое. Терракотовый здесь тот же, что у отклонений в анализах, и
# это намеренно: плохие дни должны читаться так же тревожно.
MOOD_GROUPS = {
    "good": "хорошее",
    "neutral": "нейтральное",
    "low": "тяжёлое",
}

HUMAN_MOODS = [
    ("отличное", "good"),
    ("хорошее", "good"),
    ("бодрое", "good"),
    ("спокойное", "good"),
    ("обычное", "neutral"),
    ("сонное", "neutral"),
    ("рассеянное", "neutral"),
    ("усталость", "low"),
    ("раздражение", "low"),
    ("тревога", "low"),
    ("грусть", "low"),
    ("подавленность", "low"),
    ("боль", "low"),
    ("болею", "low"),
]

DOG_MOODS = [
    ("весёлый", "good"),
    ("игривый", "good"),
    ("спокойный", "neutral"),
    ("сонный", "neutral"),
    ("вялый", "low"),
    ("больной", "low"),
]


def moods_for(subject) -> list[tuple[str, str]]:
    return DOG_MOODS if subject["kind"] == "dog" else HUMAN_MOODS


# Полный список для валидации: настроение могло быть записано, когда набор
# был другим, и такие записи нельзя терять.
MOODS = [m for m, _ in HUMAN_MOODS + DOG_MOODS] + [
    "весёлый", "игривый", "спокойный", "сонный", "вялый", "грустный", "тревожный", "больной",
]


@app.get("/s/{slug}/mood")
def mood_calendar(request: Request, slug: str, year: int | None = None, month: int | None = None):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if not subject:
            return RedirectResponse("/", status_code=302)

        today = dt.date.today()
        y, m = year or today.year, month or today.month
        days, prev_year, prev_month, next_year, next_month = month_bounds(y, m)

        rows = conn.execute(
            "SELECT * FROM mood_log WHERE subject_id=? AND date BETWEEN ? AND ?",
            (subject["id"], days[0].isoformat(), days[-1].isoformat()),
        ).fetchall()
        by_date = {r["date"]: r for r in rows}
        palette = moods_for(subject)
        group_of = {name: grp for name, grp in palette}
        counts = {name: 0 for name, _ in palette}
        for r in rows:
            # состояние из старого набора не теряем — показываем как есть
            counts[r["mood"]] = counts.get(r["mood"], 0) + 1
            group_of.setdefault(r["mood"], "neutral")

        return templates.TemplateResponse(
            request,
            "mood.html",
            {
                "request": request,
                "subject": subject,
                "days": days,
                "today": today,
                "by_date": by_date,
                "moods": palette,
                "group_of": group_of,
                "counts": counts,
                "total_marked": len(rows),
                "year": y,
                "month": m,
                "month_label": month_label(y, m),
                "prev_year": prev_year,
                "prev_month": prev_month,
                "next_year": next_year,
                "next_month": next_month,
            },
        )
    finally:
        conn.close()


@app.post("/s/{slug}/mood/set")
def mood_set(
    slug: str,
    date: str = Form(...),
    mood: str = Form(...),
    note: str = Form(""),
    year: str = Form(""),
    month: str = Form(""),
):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if subject and mood in MOODS:
            conn.execute(
                """INSERT INTO mood_log(subject_id, date, mood, note) VALUES (?,?,?,?)
                   ON CONFLICT(subject_id, date) DO UPDATE SET mood=excluded.mood, note=excluded.note""",
                (subject["id"], date, mood, note.strip() or None),
            )
            conn.commit()
    finally:
        conn.close()
    qs = f"?year={year}&month={month}" if year and month else ""
    return RedirectResponse(f"/s/{slug}/mood{qs}", status_code=303)


# ---------------------------------------------------------------------------
# Лента (timeline)
# ---------------------------------------------------------------------------

@app.get("/s/{slug}/timeline")
def timeline(request: Request, slug: str):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if not subject:
            return RedirectResponse("/", status_code=302)

        items = []
        for doc in conn.execute(
            "SELECT * FROM documents WHERE subject_id=? ORDER BY doc_date DESC", (subject["id"],)
        ):
            items.append({"date": doc["doc_date"] or doc["added_at"][:10], "type": "документ",
                          "title": doc["title"] or "Документ", "ref": doc, "kind": doc["kind"]})
        for ev in conn.execute("SELECT * FROM events WHERE subject_id=? ORDER BY date DESC", (subject["id"],)):
            items.append({"date": ev["date"], "type": ev["type"], "title": ev["title"], "ref": ev})
        for med in conn.execute(
            "SELECT * FROM medications WHERE subject_id=? AND started IS NOT NULL ORDER BY started DESC",
            (subject["id"],),
        ):
            items.append({"date": med["started"], "type": "лекарство", "title": f"Начало: {med['name']}", "ref": med})
        for cond in conn.execute(
            "SELECT * FROM conditions WHERE subject_id=? AND onset_date IS NOT NULL ORDER BY onset_date DESC",
            (subject["id"],),
        ):
            items.append({"date": cond["onset_date"], "type": "состояние", "title": cond["name"], "ref": cond})

        items = [i for i in items if i["date"]]
        items.sort(key=lambda i: i["date"], reverse=True)

        return templates.TemplateResponse(
            request,
            "timeline.html", {"request": request, "subject": subject, "items": items}
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# История одного маркера + SVG-график
# ---------------------------------------------------------------------------

@app.get("/s/{slug}/marker/{code}")
def marker_history(request: Request, slug: str, code: str):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if not subject:
            return RedirectResponse("/", status_code=302)
        analyte = conn.execute("SELECT * FROM analytes WHERE code=?", (code,)).fetchone()
        if not analyte:
            return RedirectResponse(f"/s/{slug}", status_code=302)

        rows = conn.execute(
            """SELECT * FROM results WHERE subject_id=? AND analyte_id=?
               ORDER BY taken_at ASC""",
            (subject["id"], analyte["id"]),
        ).fetchall()

        svg = build_marker_svg(rows)

        return templates.TemplateResponse(
            request,
            "marker.html",
            {"request": request, "subject": subject, "analyte": analyte, "rows": list(reversed(rows)), "svg": svg},
        )
    finally:
        conn.close()


def render_series_svg(
    series: list[tuple[str, float, bool]],
    ref_low: float | None = None,
    ref_high: float | None = None,
    width: int = 720,
    height: int = 260,
    compact: bool = False,
) -> str:
    """Инлайновый SVG-график динамики: линия, точки, коридор референса.

    Общий рисовальщик для графика маркера (`build_marker_svg`) и графика
    веса (`build_weight_svg`) — обе функции просто готовят `series` в своём
    формате (подпись оси X, значение, признак отклонения) и зовут сюда.
    `compact=True` — уменьшенный вариант для спарклайнов: без подписей по
    каждой точке, кроме значения у последней.
    """
    if not series:
        return ""
    pad_l, pad_r, pad_t, pad_b = (28, 12, 10, 18) if compact else (50, 20, 20, 40)
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    values = [v for _, v, _ in series]
    all_vals = values + ([ref_low] if ref_low is not None else []) + ([ref_high] if ref_high is not None else [])
    vmin, vmax = min(all_vals), max(all_vals)
    if vmin == vmax:
        vmin -= 1
        vmax += 1
    margin = (vmax - vmin) * 0.1
    vmin -= margin
    vmax += margin

    n = len(series)

    def x_at(i: int) -> float:
        if n == 1:
            return pad_l + plot_w / 2
        return pad_l + plot_w * i / (n - 1)

    def y_at(v: float) -> float:
        return pad_t + plot_h * (1 - (v - vmin) / (vmax - vmin))

    parts = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" class="marker-chart">']

    if ref_low is not None and ref_high is not None:
        y1, y2 = y_at(ref_high), y_at(ref_low)
        parts.append(f'<rect x="{pad_l}" y="{y1:.1f}" width="{plot_w}" height="{(y2 - y1):.1f}" class="ref-band" />')

    parts.append(f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t + plot_h}" class="axis" />')
    parts.append(f'<line x1="{pad_l}" y1="{pad_t + plot_h}" x2="{pad_l + plot_w}" y2="{pad_t + plot_h}" class="axis" />')

    points = " ".join(f"{x_at(i):.1f},{y_at(v):.1f}" for i, (_, v, _) in enumerate(series))
    parts.append(f'<polyline points="{points}" class="chart-line" fill="none" />')

    # Сколько подписей дат влезает по ширине: на дату формата «02.12.25»
    # нужно около 62 px вместе с зазором.
    max_labels = max(2, int(plot_w // 62))
    if n <= max_labels:
        label_slots = set(range(n))
    else:
        step = (n - 1) / (max_labels - 1)
        label_slots = {round(k * step) for k in range(max_labels)}
        label_slots |= {0, n - 1}

    for i, (label, v, abnormal) in enumerate(series):
        x, y = x_at(i), y_at(v)
        cls = "point-abnormal" if abnormal else "point-normal"
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" class="{cls}" />')
        val_cls = "value-abnormal" if abnormal else "value-label"
        if not compact and i in label_slots:
            # Подписи ставятся не под каждой точкой: при двух десятках
            # измерений даты налезают друг на друга и превращаются в сплошную
            # полосу. Показываем столько, сколько помещается по ширине,
            # равномерно по ряду, обязательно включая первую и последнюю.
            parts.append(
                f'<text x="{x:.1f}" y="{pad_t + plot_h + 16}" class="axis-label" text-anchor="middle">{label}</text>'
            )
            parts.append(
                f'<text x="{x:.1f}" y="{y - 8:.1f}" class="{val_cls}" text-anchor="middle">{fmt_num(v)}</text>'
            )
        elif i == n - 1:
            parts.append(
                f'<text x="{x:.1f}" y="{y - 8:.1f}" class="{val_cls}" text-anchor="middle">{fmt_num(v)}</text>'
            )

    if not compact:
        parts.append(f'<text x="{pad_l - 6}" y="{pad_t + 4}" class="axis-label" text-anchor="end">{vmax:.2f}</text>')
        parts.append(f'<text x="{pad_l - 6}" y="{pad_t + plot_h}" class="axis-label" text-anchor="end">{vmin:.2f}</text>')

    parts.append("</svg>")
    return "".join(parts)


def _date_label(value: str | None) -> str:
    return f"{value[8:10]}.{value[5:7]}.{value[2:4]}" if value and len(value) >= 10 else ""


def build_marker_svg(rows: list[sqlite3.Row], width: int = 720, height: int = 260, compact: bool = False) -> str:
    # Один и тот же заказ нередко приходит двумя письмами (полный бланк и
    # дубликат), и в базе честно лежат два документа с одинаковыми цифрами.
    # На графике это давало две точки в одной дате — их схлопываем, а в
    # таблице ниже обе записи остаются со ссылками на свои документы.
    numeric, seen = [], set()
    for r in rows:
        if r["value_num"] is None:
            continue
        key = (r["taken_at"], r["value_num"], r["unit"])
        if key in seen:
            continue
        seen.add(key)
        numeric.append(r)
    if not numeric:
        return ""

    # референсный коридор (берём последний известный диапазон)
    ref_low = next((r["ref_low"] for r in reversed(numeric) if r["ref_low"] is not None), None)
    ref_high = next((r["ref_high"] for r in reversed(numeric) if r["ref_high"] is not None), None)

    series = [
        (_date_label(r["taken_at"]), r["value_num"], r["flag"] in ("low", "high"))
        for r in numeric
    ]
    return render_series_svg(series, ref_low=ref_low, ref_high=ref_high, width=width, height=height, compact=compact)


def build_weight_svg(rows: list[sqlite3.Row], breed: str | None = None, width: int = 720, height: int = 220) -> str:
    if not rows:
        return ""
    rng = breed_weight_range(breed)
    ref_low, ref_high = rng if rng else (None, None)
    series = [
        (_date_label(r["date"]), r["kg"], bool(ref_high is not None and r["kg"] > ref_high))
        for r in rows
    ]
    return render_series_svg(series, ref_low=ref_low, ref_high=ref_high, width=width, height=height)


def important_markers(conn: sqlite3.Connection, subject_id: int, limit: int = 6) -> list[dict]:
    """Маркеры для блока «Динамика важного» на профиле.

    Приоритет — тем, у кого больше всего измерений и хотя бы раз было
    отклонение за всю историю (это самые «весомые» показатели). Если таких
    меньше четырёх, недостающее место занимают просто самые частые маркеры,
    чтобы блок не выглядел пустым.
    """
    marker_rows = conn.execute(
        """SELECT analyte_id, COUNT(*) AS cnt,
                  SUM(CASE WHEN flag IN ('low','high') THEN 1 ELSE 0 END) AS abnormal_cnt
             FROM results
            WHERE subject_id=? AND analyte_id IS NOT NULL AND value_num IS NOT NULL
            GROUP BY analyte_id""",
        (subject_id,),
    ).fetchall()
    if not marker_rows:
        return []

    with_dev = sorted((m for m in marker_rows if m["abnormal_cnt"] > 0), key=lambda m: -m["cnt"])
    without_dev = sorted((m for m in marker_rows if m["abnormal_cnt"] == 0), key=lambda m: -m["cnt"])

    chosen = with_dev[:limit]
    if len(chosen) < 4:
        chosen = chosen + without_dev[: 4 - len(chosen)]
    chosen = chosen[:limit]

    out = []
    for m in chosen:
        rows = conn.execute(
            "SELECT * FROM results WHERE subject_id=? AND analyte_id=? ORDER BY taken_at ASC",
            (subject_id, m["analyte_id"]),
        ).fetchall()
        if not rows:
            continue
        analyte = get_analyte_row(conn, m["analyte_id"])
        out.append({"analyte": analyte, "svg": build_marker_svg(rows, width=260, height=90, compact=True)})
    return out


# ---------------------------------------------------------------------------
# Все анализы субъекта
# ---------------------------------------------------------------------------

@app.get("/s/{slug}/labs")
def labs(request: Request, slug: str, category: str = "", only_abnormal: str = ""):
    conn = get_conn()
    try:
        subject = get_subject_or_404(conn, slug)
        if not subject:
            return RedirectResponse("/", status_code=302)

        query = """SELECT results.*, analytes.name_ru AS analyte_name, analytes.code AS analyte_code,
                          analytes.category AS category
                   FROM results LEFT JOIN analytes ON analytes.id = results.analyte_id
                   WHERE results.subject_id=?"""
        params: list = [subject["id"]]
        if category:
            query += " AND analytes.category=?"
            params.append(category)
        if only_abnormal:
            query += " AND results.flag IN ('low','high')"
        query += " ORDER BY results.taken_at DESC"
        rows = conn.execute(query, params).fetchall()

        return templates.TemplateResponse(
            request,
            "labs.html",
            {
                "request": request,
                "subject": subject,
                "rows": rows,
                "categories": categories(),
                "selected_category": category,
                "only_abnormal": only_abnormal,
            },
        )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Документы
# ---------------------------------------------------------------------------

@app.get("/documents")
def documents(request: Request, subject: str = "", kind: str = "", source: str = ""):
    conn = get_conn()
    try:
        query = """SELECT documents.*, subjects.name AS subject_name, subjects.slug AS subject_slug
                   FROM documents LEFT JOIN subjects ON subjects.id = documents.subject_id WHERE 1=1"""
        params: list = []
        if subject:
            query += " AND subjects.slug=?"
            params.append(subject)
        if kind:
            query += " AND documents.kind=?"
            params.append(kind)
        if source:
            query += " AND documents.source=?"
            params.append(source)
        query += " ORDER BY documents.doc_date DESC, documents.added_at DESC"
        rows = conn.execute(query, params).fetchall()
        subjects = conn.execute("SELECT * FROM subjects ORDER BY id").fetchall()

        return templates.TemplateResponse(
            request,
            "documents.html",
            {
                "request": request,
                "rows": rows,
                "subjects": subjects,
                "selected_subject": subject,
                "selected_kind": kind,
                "selected_source": source,
                "kinds": ["lab", "imaging", "visit", "prescription", "vaccination", "other"],
                "sources": ["invitro_lk", "gmail", "icloud", "local", "manual"],
            },
        )
    finally:
        conn.close()


@app.get("/file/{doc_id}")
def get_file(doc_id: int):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    finally:
        conn.close()
    if not row or not row["stored_path"] or not Path(row["stored_path"]).exists():
        return RedirectResponse("/documents", status_code=302)
    return FileResponse(row["stored_path"])


PREVIEW_DIR = db_module.DATA_DIR / "previews"
BROWSER_IMAGES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def preview_path(stored: Path) -> Path | None:
    """Вернуть путь к версии файла, которую браузер умеет показать.

    PDF и обычные картинки отдаются как есть. HEIC — формат камеры iPhone,
    браузеры его не открывают, поэтому он один раз конвертируется в JPEG
    штатным macOS-овским `sips` и кладётся в кэш: повторная конвертация при
    каждом открытии страницы была бы заметно медленной.
    """
    ext = stored.suffix.lower()
    if ext == ".pdf" or ext in BROWSER_IMAGES:
        return stored
    if ext not in {".heic", ".heif", ".tif", ".tiff"}:
        return None

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    cached = PREVIEW_DIR / (stored.stem + ".jpg")
    if cached.exists() and cached.stat().st_mtime >= stored.stat().st_mtime:
        return cached

    import subprocess

    try:
        subprocess.run(
            ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "80",
             str(stored), "--out", str(cached)],
            check=True, capture_output=True, timeout=60,
        )
    except Exception:
        return None
    return cached if cached.exists() else None


@app.get("/preview/{doc_id}")
def get_preview(doc_id: int):
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    finally:
        conn.close()
    if not row or not row["stored_path"]:
        return RedirectResponse("/documents", status_code=302)
    stored = Path(row["stored_path"])
    if not stored.exists():
        return RedirectResponse("/documents", status_code=302)
    p = preview_path(stored)
    if p is None:
        return FileResponse(stored)
    media = "application/pdf" if p.suffix.lower() == ".pdf" else None
    return FileResponse(p, media_type=media) if media else FileResponse(p)


@app.get("/document/{doc_id}")
def document_page(request: Request, doc_id: int):
    conn = get_conn()
    try:
        doc = conn.execute(
            """SELECT d.*, s.name AS subject_name, s.slug AS subject_slug
                 FROM documents d LEFT JOIN subjects s ON s.id = d.subject_id
                WHERE d.id=?""",
            (doc_id,),
        ).fetchone()
        if not doc:
            return RedirectResponse("/documents", status_code=302)
        results = conn.execute(
            """SELECT r.*, a.name_ru AS analyte_name, a.code AS analyte_code
                 FROM results r LEFT JOIN analytes a ON a.id = r.analyte_id
                WHERE r.document_id=? ORDER BY r.id""",
            (doc_id,),
        ).fetchall()
    finally:
        conn.close()

    stored = Path(doc["stored_path"]) if doc["stored_path"] else None
    ext = stored.suffix.lower() if stored else ""
    viewer = "pdf" if ext == ".pdf" else ("image" if ext in BROWSER_IMAGES | {".heic", ".heif", ".tif", ".tiff"} else None)

    return templates.TemplateResponse(
        request,
        "document.html",
        {"request": request, "doc": doc, "results": results, "viewer": viewer},
    )


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

@app.get("/inbox")
def inbox(request: Request):
    db_module.ensure_dirs()
    conn = get_conn()
    try:
        known_sha = {row["sha256"] for row in conn.execute("SELECT sha256 FROM documents")}
        pending = []
        for p in sorted(db_module.INBOX_DIR.iterdir()):
            if p.is_file() and not p.name.startswith("."):
                try:
                    sha = ingest_module.sha256_file(p)
                except Exception:
                    sha = None
                if sha not in known_sha:
                    pending.append(p.name)

        needs_review = conn.execute(
            "SELECT * FROM documents WHERE subject_id IS NULL ORDER BY added_at DESC"
        ).fetchall()
        subjects = conn.execute("SELECT * FROM subjects ORDER BY id").fetchall()

        return templates.TemplateResponse(
            request,
            "inbox.html",
            {"request": request, "pending": pending, "needs_review": needs_review, "subjects": subjects},
        )
    finally:
        conn.close()


@app.post("/inbox/ingest-all")
def inbox_ingest_all():
    ingest_module.ingest_inbox()
    return RedirectResponse("/inbox", status_code=303)


@app.post("/documents/{doc_id}/assign")
def document_assign(doc_id: int, subject: str = Form(""), doc_date: str = Form("")):
    conn = get_conn()
    try:
        subject_id = None
        if subject:
            row = conn.execute("SELECT id FROM subjects WHERE slug=?", (subject,)).fetchone()
            subject_id = row["id"] if row else None
        updates = []
        params: list = []
        if subject_id is not None:
            updates.append("subject_id=?")
            params.append(subject_id)
        if doc_date:
            updates.append("doc_date=?")
            params.append(doc_date)
        if updates:
            params.append(doc_id)
            conn.execute(f"UPDATE documents SET {', '.join(updates)} WHERE id=?", params)
            if subject_id is not None:
                conn.execute("UPDATE results SET subject_id=? WHERE document_id=?", (subject_id, doc_id))
            conn.commit()
    finally:
        conn.close()
    return RedirectResponse("/inbox", status_code=303)


# ---------------------------------------------------------------------------
# Поиск
# ---------------------------------------------------------------------------

@app.get("/search")
def search(request: Request, q: str = ""):
    conn = get_conn()
    try:
        docs = notes_rows = results_rows = []
        if q.strip():
            like = f"%{q.strip()}%"
            docs = conn.execute(
                """SELECT documents.*, subjects.name AS subject_name, subjects.slug AS subject_slug
                   FROM documents LEFT JOIN subjects ON subjects.id = documents.subject_id
                   WHERE documents.title LIKE ? OR documents.raw_text LIKE ? OR documents.lab_name LIKE ?
                   ORDER BY documents.added_at DESC LIMIT 50""",
                (like, like, like),
            ).fetchall()
            results_rows = conn.execute(
                """SELECT results.*, analytes.name_ru AS analyte_name, subjects.name AS subject_name,
                          subjects.slug AS subject_slug
                   FROM results
                   LEFT JOIN analytes ON analytes.id = results.analyte_id
                   LEFT JOIN subjects ON subjects.id = results.subject_id
                   WHERE results.raw_name LIKE ? OR analytes.name_ru LIKE ?
                   ORDER BY results.taken_at DESC LIMIT 50""",
                (like, like),
            ).fetchall()
            notes_rows = conn.execute(
                """SELECT notes.*, subjects.name AS subject_name, subjects.slug AS subject_slug
                   FROM notes LEFT JOIN subjects ON subjects.id = notes.subject_id
                   WHERE notes.title LIKE ? OR notes.body LIKE ?
                   ORDER BY notes.date DESC LIMIT 50""",
                (like, like),
            ).fetchall()

        return templates.TemplateResponse(
            request,
            "search.html",
            {"request": request, "q": q, "docs": docs, "results_rows": results_rows, "notes_rows": notes_rows},
        )
    finally:
        conn.close()
