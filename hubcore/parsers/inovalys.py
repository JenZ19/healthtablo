"""Парсер отчётов французской ветеринарной лаборатории Inovalys.

Откалиброван на отчёте о титровании антител к бешенству (FAVN-тест) —
именно его требуют для ввоза животного в ЕС и обратно:

    ANALYTICAL REPORT N° | D000000000
    Inovalys site du Mans
    Object: SN RAGE - BARSIK
    # Sampled 03/03/2025
    # Species | DOG
    # Identification chip | 643090000000000
    # Name of the Pet | BARSIK
    # Date of birth | 01/10/2018
    # Date of last rabies vaccination | 30/01/2025
    RABIES (FAVN TEST résult) | … | Seroneutralization POSITIVE | 07/03/25
    RABIES (FAVN TEST antibody titer) | … | >=7,92 | Ul/ml 07/03/25

Особенности:
- бланк двуязычный, английский с французскими вставками, даты в формате
  ДД/ММ/ГГГГ — европейском, а не американском;
- решётка «#» помечает данные со слов владельца, а не измерения
  лаборатории, — это не мусор, но и не результаты анализа;
- порог для поездок по ЕС — 0,5 МЕ/мл, он и подставляется как нижняя
  граница нормы: без него титр «>=7,92» ни о чём не говорит;
- OCR путает «UI/ml» с «Ul/ml», а десятичный разделитель — запятая.
"""

from __future__ import annotations

import re

from .base import BaseParser, ParsedDocument, ParsedResult

LAB_NAME = "Inovalys (Франция)"

# Порог защитного титра антител к бешенству для ввоза в ЕС, МЕ/мл.
EU_RABIES_THRESHOLD = 0.5

_SAMPLED_RE = re.compile(r"Sampled\s+(\d{2})/(\d{2})/(\d{4})", re.I)
_REGISTERED_RE = re.compile(r"Registration date.*?(\d{2})/(\d{2})/(\d{4})", re.I | re.S)
_PET_RE = re.compile(r"Name of the Pet\s*\|?\s*([A-ZА-Я][\w'-]*)", re.I)
_OBJECT_PET_RE = re.compile(r"Object:\s*SN\s+RAGE\s*-\s*([\w'-]+)", re.I)
_CHIP_RE = re.compile(r"Identification chip\s*\|?\s*(\d{9,20})", re.I)
_DOB_RE = re.compile(r"Date of birth\s*\|?\s*(\d{2})/(\d{2})/(\d{4})", re.I)
_VACC_RE = re.compile(r"Date of last rabies vaccination\s*\|?\s*(\d{2})/(\d{2})/(\d{4})", re.I)

_TITER_RE = re.compile(r"antibody titer\).*?\|\s*(>?=?\s*[\d,.]+)\s*\|?\s*U[IL]/ml", re.I)
_VERDICT_RE = re.compile(r"Seroneutralization\s+(POSITIVE|NEGATIVE|POSITIF|NEGATIF)", re.I)


def _iso(day: str, month: str, year: str) -> str:
    return f"{year}-{month}-{day}"


class InovalysParser(BaseParser):
    name = "inovalys"

    def can_parse(self, text: str) -> bool:
        low = text.lower()
        return "inovalys" in low or "favn test" in low

    def parse(self, text: str) -> ParsedDocument:
        doc = ParsedDocument(lab_name=LAB_NAME)

        m = _SAMPLED_RE.search(text) or _REGISTERED_RE.search(text)
        if m:
            doc.doc_date = _iso(*m.groups())

        m = _PET_RE.search(text) or _OBJECT_PET_RE.search(text)
        if m:
            doc.patient_name = m.group(1).strip().title()

        doc.title = "Титр антител к бешенству (FAVN-тест)"

        results: list[ParsedResult] = []

        m = _VERDICT_RE.search(text)
        if m:
            verdict = m.group(1).upper()
            results.append(
                ParsedResult(
                    raw_name="Бешенство, серонейтрализация (FAVN)",
                    value_num=None,
                    value_text="POSITIVE" if verdict.startswith("POSITI") else "NEGATIVE",
                    unit=None,
                    ref_low=None,
                    ref_high=None,
                    ref_text="положительный результат означает наличие антител",
                )
            )

        m = _TITER_RE.search(text)
        if m:
            raw = m.group(1).replace(" ", "")
            num = None
            n = re.search(r"[\d,.]+", raw)
            if n:
                try:
                    num = float(n.group(0).replace(",", "."))
                except ValueError:
                    num = None
            results.append(
                ParsedResult(
                    raw_name="Бешенство, титр антител (FAVN)",
                    value_num=num,
                    value_text=raw if num is None else None,
                    unit="МЕ/мл",
                    ref_low=EU_RABIES_THRESHOLD,
                    ref_high=None,
                    ref_text=f"для поездок по ЕС требуется не менее {EU_RABIES_THRESHOLD} МЕ/мл",
                )
            )

        doc.results = results

        # Паспортные данные животного и дата прививки — не результаты анализа,
        # но терять их нельзя: по ним сверяется срок действия титра.
        notes = []
        m = _CHIP_RE.search(text)
        if m:
            notes.append(f"чип {m.group(1)}")
        m = _DOB_RE.search(text)
        if m:
            notes.append(f"дата рождения {_iso(*m.groups())}")
        m = _VACC_RE.search(text)
        if m:
            notes.append(f"последняя прививка от бешенства {_iso(*m.groups())}")
        if notes:
            doc.parse_note = "; ".join(notes)

        if not results:
            doc.parsed_ok = False
            doc.parse_note = "отчёт Inovalys распознан, но результатов не найдено"
        return doc
