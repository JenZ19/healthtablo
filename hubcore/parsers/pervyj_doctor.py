"""Парсер бланков клиники «Первый Доктор» (1doctor.ru).

Формат после раскладки по колонкам:

    БИОХИМИЯ КРОВИ
    ФИО: Иванова Анна Петровна | Возр: 05.07.1990 Пол: Жен. | амб. карта: 00000/1
    Дата получ. мат-ла: 23 Марта 2021
    Название | Результат | Ед.Изм. | Норма
    Витамины, жирные кислоты
    Витамин В6 (пиридоксин) | 187,00 | нмоль/л | 14,00 - 320,00
    Дата выполенния: 26 Марта 2021

Особенности:
- месяц в датах написан словом по-русски («23 Марта 2021»);
- в бланке опечатка «Дата выполенния» — она в оригинале, не исправлять;
- «Возр:» на самом деле содержит дату рождения, а не возраст, и её нельзя
  принимать за дату исследования;
- строки без колонок — заголовки категорий («Витамины, жирные кислоты»).
"""

from __future__ import annotations

import re

from .base import BaseParser, ParsedDocument, parse_date_ru
from .tabular import cells, is_header_row, is_noise, make_result

LAB_NAME = "Клиника «Первый Доктор»"

_FIO_RE = re.compile(r"ФИО\s*:\s*([^|]+)", re.I)
_TAKEN_RE = re.compile(r"Дата\s+получ\.?\s*мат-?ла\s*:\s*([^|]+)", re.I)
_DONE_RE = re.compile(r"Дата\s+выпол[а-я]*\s*:\s*([^|]+)", re.I)

# Заголовки разделов внутри таблицы — не результаты.
_SECTION_RE = re.compile(
    r"^(витамин|микроэлемент|макроэлемент|жирные кислоты|гормон|биохими|"
    r"метаболит|липид|обмен|аминокислот|тяжел)", re.I
)


def _clean_fio(raw: str) -> str | None:
    # "Иванова Анна Петровна Возр: 05.07.1990 Пол: Жен."
    s = re.split(r"\bВозр\b|\bПол\b|\bамб\b", raw, maxsplit=1)[0]
    s = s.strip(" :.,")
    return s or None


class PervyjDoctorParser(BaseParser):
    name = "pervyj_doctor"

    def can_parse(self, text: str) -> bool:
        low = text.lower()
        return "1doctor.ru" in low or "первый доктор" in low

    def parse(self, text: str) -> ParsedDocument:
        doc = ParsedDocument(lab_name=LAB_NAME)

        m = _FIO_RE.search(text)
        if m:
            doc.patient_name = _clean_fio(m.group(1))

        # дата забора приоритетнее даты выдачи результата
        m = _TAKEN_RE.search(text)
        if m:
            doc.doc_date = parse_date_ru(m.group(1))
        if not doc.doc_date:
            m = _DONE_RE.search(text)
            if m:
                doc.doc_date = parse_date_ru(m.group(1))

        results = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or is_noise(line):
                continue
            low = line.lower()
            if low.startswith(("фио", "дата получ", "дата выпол", "возр", "амб",
                               "г. москва", "киевская", "северный", "тел")):
                continue

            parts = cells(line)
            if len(parts) < 2 or is_header_row(parts):
                continue
            if _SECTION_RE.match(parts[0]) and len(parts) == 1:
                continue

            name = parts[0]
            value = parts[1]
            unit = parts[2] if len(parts) > 2 else None
            ref = " ".join(parts[3:]) if len(parts) > 3 else None
            if unit and ref is None and re.search(r"[<>–—]|\d\s*-\s*\d", unit):
                ref, unit = unit, None

            r = make_result(name, value, unit, ref)
            if r:
                results.append(r)

        doc.results = results
        if not results:
            doc.parsed_ok = False
            doc.parse_note = "бланк «Первого Доктора» распознан, но строк результатов не найдено"
        return doc
