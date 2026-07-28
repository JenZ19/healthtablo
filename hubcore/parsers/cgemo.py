"""Парсер протокола ФБУЗ «Центр гигиены и эпидемиологии» (cgemo.ru).

Это не лабораторный бланк, а официальный протокол испытаний, где результат
спрятан в одной широкой таблице, размазанной по нескольким строкам:

    1. Заявитель (наименование предприятия, организации, ЧЛ): Иванова Анна Петровна
    4. Дата и время отбора материала: 03.04.2021
    Иванова | РНК | Биологический
    1 | Евгения | 05.07.1990 | 03.04.2021 | 03.04.2021 | коронавируса | материал | Отрицательный
    Андреевна | 2019-nCoV | мазки

Разбирать эту таблицу по колонкам бессмысленно — она собирается из
обрывков. Поэтому берём то, что здесь действительно ценно и надёжно:
кто, когда и какой результат.
"""

from __future__ import annotations

import re

from .base import BaseParser, ParsedDocument, ParsedResult

LAB_NAME = "ФБУЗ ЦГиЭ в Московской области"

_APPLICANT_RE = re.compile(r"Заявитель[^:]*:\s*([^|\n]+)", re.I)
_SAMPLED_RE = re.compile(r"Дата и время отбора материала\s*:\s*(\d{2})\.(\d{2})\.(\d{4})", re.I)
_VERDICT_RE = re.compile(r"\b(Отрицательн\w*|Положительн\w*|Не обнаружен\w*|Обнаружен\w*)\b", re.I)


class CgemoParser(BaseParser):
    name = "cgemo"

    def can_parse(self, text: str) -> bool:
        low = text.lower()
        return "cgemo.ru" in low or "центр гигиены и эпидемиологии" in low

    def parse(self, text: str) -> ParsedDocument:
        doc = ParsedDocument(lab_name=LAB_NAME)

        m = _APPLICANT_RE.search(text)
        if m:
            doc.patient_name = m.group(1).strip(" .,")

        m = _SAMPLED_RE.search(text)
        if m:
            d, mth, y = m.groups()
            doc.doc_date = f"{y}-{mth}-{d}"

        # Название исследования — из пункта «вид исследования», результат —
        # единственное словесное заключение в таблице.
        name = "РНК коронавируса SARS-CoV-2 (2019-nCoV), ПЦР"
        m = re.search(r"вид исследования\)\s*:\s*([^|\n]+)", text, re.I)
        if m and len(m.group(1).strip()) > 5:
            name = m.group(1).strip(" .,")

        verdict = None
        for line in text.splitlines():
            if "коронавирус" in line.lower() or re.match(r"^\d+\s*\|", line.strip()):
                mv = _VERDICT_RE.search(line)
                if mv:
                    verdict = mv.group(1)
                    break
        if verdict is None:
            mv = _VERDICT_RE.search(text)
            verdict = mv.group(1) if mv else None

        if verdict:
            doc.results = [
                ParsedResult(
                    raw_name=name,
                    value_num=None,
                    value_text=verdict,
                    unit=None,
                    ref_low=None,
                    ref_high=None,
                    ref_text=None,
                )
            ]
        else:
            doc.parsed_ok = False
            doc.parse_note = "протокол ЦГиЭ распознан, но результат исследования не найден"
        return doc
