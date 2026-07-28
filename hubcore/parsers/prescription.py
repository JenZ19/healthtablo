"""Парсер рецептов и врачебных рекомендаций (в т.ч. ветеринарных).

    Рекомендация по лечению №000000003 от 16.07.2026
    Пациент: Барсик, мальтеза   Врач: Петрова А. А.
    По результатам рентгена есть признаки артроза локтевого сустава.
    Рекомендации
    1) Медикаменты:
    • Гапентин (таб. 80 мг)(ветеринарный препарат) - давать по 1 таб. 2 раза в сутки, курс 1 месяц
    • Спазмалгон/Баралгин (таб. 500 мг) - давать по 1/4 табл. 2 раза в сутки, курс 7 дней

Зачем отдельный парсер: generic видит в строках числа с единицами и делает
из них «результаты анализов» — в таблице показателей появляется «Гапентин =
80 мг». Это не измерение, а назначение. Здесь такие строки разбираются в
назначения (`medications`), а результатов документ не даёт вовсе.
"""

from __future__ import annotations

import re

from .base import BaseParser, ParsedDocument, ParsedMedication
from .tabular import find_labeled_date

_MARKERS = (
    "рекомендация по лечению",
    "рекомендации по лечению",
    "рецептурный бланк",
    "рецепт №",
    "рецепт n",
    "назначения:",
)

_DATE_LABELS = ["дата оформления рецепта", "от"]
_DATE_ANY_RE = re.compile(r"\bот\s+(\d{2})\.(\d{2})\.(\d{4})")

# Строка назначения: маркер списка, название, доза в скобках, схема после тире.
_MED_RE = re.compile(
    r"^[•\-\*•]?\s*(?P<name>[^()\n]{3,60}?)\s*"
    r"\((?:таб\.?|табл\.?|капс\.?|амп\.?|р-р|мазь)?\s*(?P<dose>[^)]*?)\)"
    r"(?P<rest>.*)$",
    re.I,
)
_COURSE_RE = re.compile(r"курс\s+([^,.;]+)", re.I)
_SCHEDULE_RE = re.compile(r"давать\s+(.+?)(?:,\s*курс|$)", re.I)


def _join_wrapped(lines: list[str]) -> list[str]:
    """Склеить назначение, разорванное переносом строки.

    В бланке одно назначение занимает две строки:

        • Амантадин или ПК-Перц или Мидантан (таб. 100 мг) - давать по 1/4
        таб. 1 раз в сутки, курс 1 месяц

    Если читать построчно, от схемы остаётся «по 1/4», а длительность курса
    теряется совсем — и лекарство выглядит бессрочным. Продолжением считаем
    строку без маркера списка, идущую сразу за назначением.
    """
    out: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^[•\-\*•]", line):
            out.append(line)
        elif out and re.match(r"^[•\-\*•]", out[-1]) and not re.match(r"^\d+\)", line):
            # продолжение предыдущего назначения, пока оно не закончилось курсом
            if not _COURSE_RE.search(out[-1]):
                out[-1] = f"{out[-1]} {line}"
    return out


# Длительность курса словами → сколько это дней.
_COURSE_UNITS = [
    (r"(\d+)\s*(?:месяц|мес)", 30),
    (r"(\d+)\s*(?:недел|нед)", 7),
    (r"(\d+)\s*(?:дн|день|сут)", 1),
]


def course_days(course: str | None) -> int | None:
    """Перевести «1 месяц», «7 дней», «2 недели» в число дней."""
    if not course:
        return None
    low = course.lower()
    for pattern, mult in _COURSE_UNITS:
        m = re.search(pattern, low)
        if m:
            try:
                return int(m.group(1)) * mult
            except ValueError:
                return None
    return None


class PrescriptionParser(BaseParser):
    name = "prescription"

    def can_parse(self, text: str) -> bool:
        low = text.lower()
        return any(k in low for k in _MARKERS)

    def parse(self, text: str) -> ParsedDocument:
        doc = ParsedDocument()
        doc.results = []  # назначение — не измерение, результатов здесь нет

        d = find_labeled_date(text, _DATE_LABELS)
        if not d:
            m = _DATE_ANY_RE.search(text)
            if m:
                dd, mm, yy = m.groups()
                d = f"{yy}-{mm}-{dd}"
        doc.doc_date = d

        low = text.lower()
        doc.title = (
            "Рецептурный бланк" if "рецептурный бланк" in low else "Рекомендация по лечению"
        )

        m = re.search(r"Пациент\s*:\s*([^,\n|]+)", text, re.I)
        if m:
            doc.patient_name = m.group(1).strip()
        else:
            m = re.search(r"Кличка\s*\|?\s*([^\n|]+)", text, re.I)
            if m:
                doc.patient_name = m.group(1).strip()

        meds = []
        for line in _join_wrapped(text.splitlines()):
            if not line or not re.match(r"^[•\-\*•]", line):
                continue
            m = _MED_RE.match(line)
            if not m:
                continue
            name = m.group("name").strip(" -•*")
            dose = (m.group("dose") or "").strip()
            rest = m.group("rest") or ""
            sched = _SCHEDULE_RE.search(rest)
            course = _COURSE_RE.search(rest)
            meds.append(
                ParsedMedication(
                    name=name,
                    dose=dose or None,
                    schedule=(sched.group(1).strip() if sched else None),
                    course=(course.group(1).strip() if course else None),
                )
            )
        doc.medications = meds

        if meds:
            doc.parse_note = "назначено: " + "; ".join(
                f"{x.name}{' ' + x.dose if x.dose else ''}" for x in meds
            )
        return doc
