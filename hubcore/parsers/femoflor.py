"""Парсер бланков «Фемофлор» — исследование биоценоза урогенитального тракта.

Формат принципиально не такой, как у обычных бланков СМ-Клиники, поэтому
разбирается отдельно:

    Исследование биоценоза урогенитального тракта
    Фемофлор 16
    Дата | 24.07.2026, 17:49:41
    Ф.И.О. пациента | Иванова Анна Петровна
    Контроль взятия материала | 10 5.1 | 1 | 10 | 100
    1 | Общая бактериальная масса | 10 5.8
    НОРМОФЛОРА
    2 | Lactobacillus spp. | 10 5.7 | -0.2 (51-68%)
    ФАКУЛЬТАТИВНО-АНАЭРОБНЫЕ МИКРООРГАНИЗМЫ
    4 | Streptococcus spp. | не выявлено
    14 | Candida spp. * | ниже ПЗ ***

Ключевое: результатом является ТОЛЬКО пронумерованная строка вида
«N | название | значение». Всё остальное — шапка, названия разделов,
сноски и подписи. Раньше этот бланк доставался generic-парсеру, и в
таблицу анализов попадали «Организация», «Примечание», «Номер пробирки»
и «логарифмическая шкала» — то есть служебные поля бланка вместо
микроорганизмов.

Значения микробиологические, а не числовые в привычном смысле: «10 5.8»
означает 10⁵·⁸ КОЕ/мл, а доля от общей бактериальной массы записана в
скобках («-0.2 (51-68%)»). В хаб они кладутся текстом: превращать
логарифм в число и рисовать по нему график динамики было бы враньём.
"""

from __future__ import annotations

import re

from .base import BaseParser, ParsedDocument, ParsedResult, parse_date

LAB_NAME = "СМ-Клиника (ЛДЦ), Фемофлор"

_DATE_RE = re.compile(r"^\s*Дата\s*\|\s*([^\n|]+)", re.I | re.M)
_FIO_RE = re.compile(r"Ф\.?\s*И\.?\s*О\.?\s*пациента\s*\|\s*([^|\n]+)", re.I)

# Строка результата: номер, название, значение (и, возможно, доля в скобках).
_ROW_RE = re.compile(r"^\s*(\d{1,2})\s*\|\s*([^|]+?)\s*\|\s*(.+)$")

# Отдельная строка контроля качества забора — она без номера, но полезна.
_CONTROL_RE = re.compile(r"^\s*Контроль взятия материала\s*\|\s*([^|]+)", re.I)


def _clean_value(raw: str) -> str:
    """Привести значение к читаемому виду: «10 5.8» → «10^5.8»."""
    v = " ".join(raw.split())
    v = re.sub(r"\b10\s+(\d+[.,]\d+)\b", r"10^\1", v)
    return v.strip(" |")


class FemoflorParser(BaseParser):
    name = "femoflor"

    def can_parse(self, text: str) -> bool:
        low = text.lower()
        # Слова «Фемофлор» мало: обычный бланк СМ-Клиники может просто
        # упоминать его в списке заказанных исследований. Нужен признак
        # самой таблицы Фемофлора — общая бактериальная масса и
        # пронумерованные строки микроорганизмов.
        if "показатель | результат" in low or "референсные пределы" in low:
            return False
        if "общая бактериальная масса" in low:
            return True
        numbered = sum(1 for l in text.splitlines() if _ROW_RE.match(l))
        return numbered >= 5 and ("фемофлор" in low or "биоценоз" in low)

    def parse(self, text: str) -> ParsedDocument:
        doc = ParsedDocument(lab_name=LAB_NAME)
        doc.title = "Фемофлор — биоценоз урогенитального тракта"

        m = _DATE_RE.search(text)
        if m:
            doc.doc_date = parse_date(m.group(1))
        m = _FIO_RE.search(text)
        if m:
            doc.patient_name = m.group(1).strip(" .,|")

        results: list[ParsedResult] = []
        section = ""
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            if not line.strip():
                continue

            # Заголовок раздела: строка капслоком без разделителей колонок.
            bare = line.strip()
            if "|" not in bare and bare.isupper() and 4 <= len(bare) <= 60:
                section = bare.capitalize()
                continue

            m = _CONTROL_RE.match(line)
            if m:
                results.append(
                    ParsedResult(
                        raw_name="Контроль взятия материала",
                        value_num=None,
                        value_text=_clean_value(m.group(1)),
                        unit=None,
                        ref_low=None,
                        ref_high=None,
                        ref_text="показатель качества забора, не результат исследования",
                    )
                )
                continue

            m = _ROW_RE.match(line)
            if not m:
                continue
            _num, name, value = m.groups()
            name = name.strip(" *")
            if len(name) < 3:
                continue
            results.append(
                ParsedResult(
                    raw_name=name,
                    value_num=None,
                    value_text=_clean_value(value),
                    unit=None,
                    ref_low=None,
                    ref_high=None,
                    ref_text=section or None,
                )
            )

        doc.results = results
        if not results:
            doc.parsed_ok = False
            doc.parse_note = "бланк Фемофлор распознан, но строк результатов не найдено"
        else:
            doc.parse_note = f"микроорганизмов: {len(results)}"
        return doc
