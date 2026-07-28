"""Парсер бланков ИНВИТРО.

Откалиброван на реальном бланке — фотографии, распознанной через OCR:

    INVITRO
    ИВАНОВ ПЁТР СЕРГЕЕВИЧ | ООО «ИНВИТРО-ГОРОД»
    Пол: | Муж
    Дата рождения: | 14.03.1970
    ИНЗ: | 868890874
    Дата взятия образца: | 22.10.2025 07:50
    Дата печати результата: | 26.10.2025
    Исследование | Результат | Единицы | Референсные значения
    М-градиент | 0.6* | г/л | не обнаруж.
    сыворотки (кол) Общий белок | 72.8 | г/л | 64 - 83
    Альбумин | 38.50 | г/л | 34.56 - 54.78

Грабли:
- «Дата рождения» стоит в бланке ВЫШЕ даты забора, поэтому дата ищется
  строго по подписи «Дата взятия образца», а не как первая в документе;
- ФИО набрано капслоком отдельной строкой, без подписи «Пациент»;
- звёздочка в значении («0.6*») — пометка лаборатории «вне референса»,
  а не часть числа;
- длинное название переносится на следующую строку и при распознавании
  склеивается с началом следующего показателя («сыворотки (кол) Общий
  белок») — такой хвост отрезается.
"""

from __future__ import annotations

import re

from .base import BaseParser, ParsedDocument
from .tabular import cells, find_labeled_date, is_header_row, is_noise, make_result

LAB_NAME = "ИНВИТРО"

DATE_LABELS = ["дата взятия образца", "дата взятия", "дата поступления образца"]

_HEAD_PREFIXES = (
    "пол", "дата", "возраст", "инз", "врач", "исполнитель", "ооо", "тел",
    "адрес", "комментарий", "исследование", "результат", "единиц",
    "референсные", "лицензия", "8-800", "invitro",
)

# ФИО капслоком: «ИВАНОВ ПЁТР СЕРГЕЕВИЧ»
_FIO_CAPS_RE = re.compile(r"^([А-ЯЁ]{3,}(?:\s+[А-ЯЁ]{3,}){1,2})\s*$")

# Хвост перенесённого названия, приклеившийся спереди к следующему:
# «сыворотки (кол) Общий белок» → «Общий белок»
_GLUED_TAIL_RE = re.compile(r"^[а-яё\s(),.\-]{4,40}?\)\s+(?=[А-ЯЁA-Z])")


# Часть бланков подписывает ФИО явно: «Пациент: Иванова Анна», «Ф.И.О.: …»
_FIO_LABELED_RE = re.compile(
    r"(?:пациент|ф\.?\s*и\.?\s*о\.?)\s*:?\s*\|?\s*([А-ЯЁ][А-Яа-яЁё\- ]{4,60})", re.I
)


def _patient(text: str) -> str | None:
    m = _FIO_LABELED_RE.search(text)
    if m:
        return m.group(1).strip(" .,|")
    for line in text.splitlines():
        for part in line.split("|"):
            m = _FIO_CAPS_RE.match(part.strip())
            if m and "ИНВИТРО" not in m.group(1):
                return m.group(1).title()
    return None


def _clean_name(name: str) -> str:
    return _GLUED_TAIL_RE.sub("", name).strip()


class InvitroParser(BaseParser):
    name = "invitro"

    def can_parse(self, text: str) -> bool:
        low = text.lower()
        return "invitro" in low or "инвитро" in low

    def parse(self, text: str) -> ParsedDocument:
        doc = ParsedDocument(lab_name=LAB_NAME)
        doc.doc_date = find_labeled_date(text, DATE_LABELS)
        doc.patient_name = _patient(text)

        results = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or is_noise(line):
                continue
            parts = cells(line)
            if len(parts) < 2 or is_header_row(parts):
                continue
            head = parts[0].lower().rstrip(": ")
            if head.startswith(_HEAD_PREFIXES):
                continue
            # строка с ФИО пациента стоит в той же таблице, но показателем не является
            if _FIO_CAPS_RE.match(parts[0].strip()):
                continue

            name = _clean_name(parts[0])
            value = parts[1]
            unit = parts[2] if len(parts) > 2 else None
            ref = " ".join(parts[3:]) if len(parts) > 3 else None
            if unit and ref is None and re.search(r"\d\s*[-–]\s*\d|[<>]|обнаруж", unit, re.I):
                ref, unit = unit, None

            r = make_result(name, value, unit, ref)
            if r:
                # в распознанном тексте единицы гуляют регистром: «г/Л» → «г/л»
                if r.unit:
                    r.unit = r.unit.replace("/Л", "/л")
                results.append(r)

        doc.results = results
        if not results:
            doc.parsed_ok = False
            doc.parse_note = "бланк ИНВИТРО распознан, но строк результатов не найдено"
        return doc
