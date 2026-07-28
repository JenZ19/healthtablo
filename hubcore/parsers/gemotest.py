"""Парсер бланков лаборатории «Гемотест».

Встречаются два поколения бланка:

Старый (2018–2019):
    № направления: | 10000001 | дата: | 26.08.2018 | Фамилия: | Иванова
    ЛПУ: | 5030 "Доктор к Вам" (Таганская) | Имя: | Евгения Андреевна
    Наименование исследования | Результат | Ед. изм. | Нормальные значения
    Прогестерон | 0.09 | нг/мл | Смотри текст

Новый (2020–2024):
    № заказа | 10000003 | Фамилия пациента | Иванова
    Дата регистрации заказа | 08.03.2021 | Имя пациента | Евгения Андреевна
    Исследование | Значение | Ед. изм. | Нормальные значения
    Общий белок | 78.8 | г/л | 64 - 83
    Дата исследования: 08.03.2021;

Ключевые грабли, из-за которых наивный разбор врёт:
- в документе несколько дат, и «Дата рождения пациента» идёт раньше даты
  забора — поэтому дата ищется строго по подписи;
- ФИО разнесено на две строки (Фамилия / Имя);
- название исследования переносится на 2–3 строки, а значение остаётся
  на первой из них;
- под строкой результата идут строки-расшифровки референса
  («Фолликулярная фаза 0.20-1.5»), которые не являются результатами.
"""

from __future__ import annotations

import re

from .base import BaseParser, ParsedDocument
from .tabular import cells, find_labeled_date, is_header_row, is_noise, looks_like_name, make_result

LAB_NAME = "Лаборатория Гемотест"

# Подписи дат в порядке приоритета: сначала дата собственно исследования.
DATE_LABELS = [
    "дата исследования",
    "дата регистрации заказа",
    "дата взятия",
]

# Строки-расшифровки референса под результатом: "Фолликулярная фаза 0.20-1.5"
_REF_EXPLAIN_RE = re.compile(
    r"^(фолликулярн|овуляторн|лютеинов|постменопауз|нормальн|умеренно|высок|"
    r"оптимальн|погранично|беременност|i+ триместр|дети|мужчин|женщин)",
    re.I,
)

_HEAD_PREFIXES = (
    "№", "лпу", "адрес", "врач", "отделение", "пол", "дата", "номер истории",
    "диагноз", "палата", "фамилия", "имя", "лицензия", "ло-", "тел", "инн",
)


def _extract_person(text: str) -> str | None:
    """Собрать ФИО из бланка.

    Три варианта записи: разнесённые ячейки «Фамилия»/«Имя» (бланк
    результатов) и цельное «ФИО:» или «Выдана:» (справка о тесте).
    """
    surname = given = None
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        for i, p in enumerate(parts[:-1]):
            key = p.lower().rstrip(": ").strip()
            val = parts[i + 1].strip()
            if not val or val == "-":
                continue
            if key in ("фамилия", "фамилия пациента") and surname is None:
                surname = val
            elif key in ("имя", "имя пациента") and given is None:
                given = val
            elif key in ("фио", "выдана", "выдан") and surname is None and given is None:
                if re.match(r"^[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+", val):
                    return val.strip(" ,")
    if surname and given:
        return f"{surname} {given}"
    return surname or given


def _find_date(text: str) -> str | None:
    d = find_labeled_date(text, DATE_LABELS)
    if d:
        return d
    # старый бланк: "№ направления: | 10000001 | дата: | 26.08.2018"
    m = re.search(r"дата:\s*\|?\s*(\d{2})\.(\d{2})\.(\d{4})", text, re.I)
    if m:
        dd, mm, yy = m.groups()
        return f"{yy}-{mm}-{dd}"
    return None


def _material_of(line: str) -> str | None:
    """Если строка — заголовок раздела бланка, вернуть биоматериал.

    «ОБЩЕКЛИНИЧЕСКИЕ ИССЛЕДОВАНИЯ МОЧИ» → «моча»,
    «БИОХИМИЧЕСКИЕ ИССЛЕДОВАНИЯ КРОВИ» → «кровь».
    Для не-заголовков возвращает None, и текущий биоматериал не меняется.
    """
    t = line.strip().strip("|").strip()
    if "|" in line or len(t) < 8 or len(t) > 70:
        return None
    letters = [ch for ch in t if ch.isalpha()]
    if not letters or sum(1 for ch in letters if ch.isupper()) / len(letters) < 0.8:
        return None
    low = t.lower()
    if "мочи" in low or "моча" in low:
        return "моча"
    if "кала" in low or "фекал" in low:
        return "кал"
    if "крови" in low or "кровь" in low or "гематолог" in low:
        return "кровь"
    return None


def _looks_unfinished(token: str) -> bool:
    """Похоже ли, что название показателя оборвано переносом строки.

    Признаки: висящая запятая или дефис в конце, либо незакрытая скобка.
    Всё остальное — заголовок раздела, и приклеивать его к следующему
    показателю нельзя, иначе в базу попадают имена вида
    «Оценка функции щитовидной железы Т3 свободный».
    """
    t = token.strip()
    if not t:
        return False
    if t.endswith((",", "-", "–", "—", "/")):
        return True
    return t.count("(") > t.count(")")


_CERT_MARK = "о результатах лабораторного теста"
_DATETIME_CELL = re.compile(r"^\d{2}\.\d{2}\.\d{4}(\s+\d{2}:\d{2}:\d{2})?$")


def _is_covid_certificate(text: str) -> bool:
    return _CERT_MARK in text.lower()


def _parse_covid_certificate(text: str, doc: ParsedDocument) -> ParsedDocument:
    """Разобрать справку о тесте на COVID-19.

    Таблица там устроена иначе, чем в обычном бланке: значение стоит в
    ПОСЛЕДНЕЙ колонке, а между названием и значением — две даты.

        Коронавирус SARS-CoV-2, | 08.03.2021 10:51:18 | 08.03.2021 19:44:19 | положительный
        антитела IgG (п/кол.)

    Хвост названия («антитела IgG (п/кол.)») приезжает строкой НИЖЕ, уже
    после значения, поэтому его приклеиваем к предыдущему результату.
    """
    results = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or is_noise(line):
            continue
        parts = cells(line)

        if len(parts) == 1:
            tail = parts[0]
            if results and len(tail) < 60 and not tail.lower().startswith(
                ("информируем", "отсканируйте", "качественное", "фио", "справка")
            ):
                results[-1].raw_name = f"{results[-1].raw_name} {tail}".strip()
            continue

        date_cells = [i for i, c in enumerate(parts) if _DATETIME_CELL.match(c)]
        if not date_cells or len(parts) < 3:
            continue

        name = parts[0]
        value = parts[-1]
        if _DATETIME_CELL.match(value):
            continue
        r = make_result(name, value)
        if r:
            results.append(r)
            if doc.doc_date is None:
                doc.doc_date = _iso(parts[date_cells[0]])

    doc.results = results
    if not results:
        doc.parsed_ok = False
        doc.parse_note = "справка Гемотеста распознана, но результатов не найдено"
    return doc


def _iso(cell: str) -> str | None:
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})", cell)
    if not m:
        return None
    d, mth, y = m.groups()
    return f"{y}-{mth}-{d}"


class GemotestParser(BaseParser):
    name = "gemotest"

    def can_parse(self, text: str) -> bool:
        low = text.lower()
        if "гемотест" in low or "gemotest.ru" in low:
            return True
        # На части бланков логотип — картинка, слова «Гемотест» в тексте нет.
        # Тогда опознаём по характерной шапке или по форме справки о тесте.
        if ("фамилия пациента" in low and "№ заказа" in low) or "№ направления" in low:
            return True
        return _CERT_MARK in low

    def parse(self, text: str) -> ParsedDocument:
        doc = ParsedDocument(lab_name=LAB_NAME)
        doc.doc_date = _find_date(text)
        doc.patient_name = _extract_person(text)

        if _is_covid_certificate(text):
            return _parse_covid_certificate(text, doc)

        results = []
        pending_name = ""  # накопитель для названий, переехавших на пару строк
        material = ""      # текущий биоматериал: кровь / моча / кал

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                pending_name = ""
                continue

            # Заголовок раздела бланка задаёт биоматериал. Без этого «Глюкоза»
            # из общего анализа мочи и «Глюкоза» из биохимии крови сливаются
            # в один маркер, и в динамике 0,0 ммоль/л мочи встаёт рядом с
            # 4,7 ммоль/л крови — цифры верные, вывод из них ложный.
            mat = _material_of(line)
            if mat is not None:
                material = mat
                pending_name = ""
                continue
            if is_noise(line) or _REF_EXPLAIN_RE.match(line):
                pending_name = ""
                continue

            parts = cells(line)
            if is_header_row(parts):
                pending_name = ""
                continue

            # Строка без колонок — либо заголовок раздела («Биохимия 8
            # показателей», «Оценка функции щитовидной железы»), либо начало
            # названия, разорванного переносом. Отличаем по незавершённости:
            # у переноса остаётся висящая запятая, дефис или незакрытая скобка.
            if len(parts) == 1:
                token = parts[0]
                if _looks_unfinished(token) and looks_like_name(token) and len(token) < 90:
                    pending_name = (pending_name + " " + token).strip()
                else:
                    pending_name = ""
                continue

            head = parts[0].lower().rstrip(": ")
            if head.startswith(_HEAD_PREFIXES):
                pending_name = ""
                continue

            name = (pending_name + " " + parts[0]).strip() if pending_name else parts[0]
            pending_name = ""

            value = parts[1]
            unit = parts[2] if len(parts) > 2 else None
            ref = " ".join(parts[3:]) if len(parts) > 3 else None
            # колонка «единицы» на деле оказалась референсом (единиц в бланке нет)
            if unit and ref is None and re.search(r"[<>–—]|\d\s*-\s*\d", unit):
                ref, unit = unit, None

            r = make_result(name, value, unit, ref)
            if r:
                if material and material != "кровь":
                    r.raw_name = f"{material.capitalize()}: {r.raw_name}"
                results.append(r)

        doc.results = results
        if not results:
            doc.parsed_ok = False
            doc.parse_note = "бланк Гемотеста распознан, но строк результатов не найдено"
        return doc
