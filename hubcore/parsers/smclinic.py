"""Парсер бланков лаборатории ГК «СМ-Клиника» (ООО «ЛДЦ»).

Формат после раскладки по колонкам:

    Код пациента: | АМ0000000 | № образца: | FB8H54
    Дата забора образца: 13.01.2024 10:54:35 | Заказчик: | ВДНХ
    Ф.И.О. пациента: | Иванова Анна Петровна | Дата рождения: 05.07.1990
    Биоматериал: | Моча | Пол: | Ж
    Общий анализ мочи (Физико-химический анализ)
    Показатель | Результат | Ед. изм. | Референсные пределы
    Удельный вес | 1020 | 1005 - 1030
    Белок | Не обнаружено | г/л | Не обнаружено
    Лейкоциты | 0 - 2 | в п/зр | 1 - 4

Особенности:
- биоматериал указан в шапке одной строкой и распространяется на весь
  бланк — по нему разводятся одноимённые показатели крови и мочи;
- результат бывает диапазоном («0 - 2» лейкоцитов в поле зрения) — это
  не число и не референс, он уходит в текстовое значение;
- у части строк колонка единиц отсутствует, и референс оказывается
  третьей ячейкой вместо четвёртой;
- перечисление допустимых вариантов («Тёмно-желтый, Желтый, …») может
  занимать несколько строк — эти хвосты не являются показателями.
"""

from __future__ import annotations

import re

from .base import BaseParser, ParsedDocument, parse_date
from .tabular import cells, is_header_row, is_noise, make_result

LAB_NAME = "СМ-Клиника (ЛДЦ)"

_DATE_RE = re.compile(r"Дата забора образца:?\s*\|?\s*(\d{2})\.(\d{2})\.(\d{4})", re.I)
# Бланки «Фемофлор» из той же сети подписывают дату иначе и иногда пишут
# месяц словом: «Дата | 24.07.2026, 17:49:41» либо «Дата | 1 Май 2025, 13:46:51».
_DATE_ALT_RE = re.compile(r"^\s*Дата\s*\|?\s*([^\n|]+)", re.I | re.M)
_FIO_RE = re.compile(r"Ф\.?\s*И\.?\s*О\.?\s*пациента:?\s*\|?\s*([^|\n]+)", re.I)
# Только настоящее поле шапки: «Биоматериал:» или «Биоматериал |» в начале
# строки. Без этих ограничений регулярка ловила слово «биоматериалА» в
# служебном тексте вроде «правила взятия биоматериала.» и объявляла
# биоматериалом букву «А» — после чего все показатели получали префикс «А:».
_MATERIAL_RE = re.compile(r"^\s*Биоматериал\s*(?::|\|)\s*([^|\n]+)", re.I | re.M)

# Допустимые биоматериалы. Всё, что не из списка, — не биоматериал.
_KNOWN_MATERIALS = (
    "кровь", "сыворотк", "плазм", "моча", "кал", "мазок", "соскоб",
    "мокрота", "слюна", "ликвор", "эякулят", "секрет", "выпот", "пунктат",
)

# Значение-диапазон: «0 - 2», «1 - 4». Число в такой ячейке брать нельзя.
_RANGE_VALUE_RE = re.compile(r"^\d+[.,]?\d*\s*[-–]\s*\d+[.,]?\d*$")

_HEAD_PREFIXES = (
    "код пациента", "№ образца", "дата забора", "заказчик", "ф.и.о", "фио",
    "дата рождения", "возраст", "биоматериал", "пол", "используемое оборудование",
    "анализатор", "адрес", "тел", "инн", "лицензия", "ооо", "группа компаний",
    "результат лабораторного исследования", "врач", "исполнитель",
)

# Биоматериал крови считаем базовым — к нему префикс не добавляется.
_BLOOD = ("кровь", "сыворотк", "плазм", "цельная кровь", "капиллярн", "венозн")


def _material(text: str) -> str | None:
    m = _MATERIAL_RE.search(text)
    if not m:
        return None
    val = m.group(1).strip().strip(".,").lower()
    if not val or not any(k in val for k in _KNOWN_MATERIALS):
        return None
    if any(b in val for b in _BLOOD):
        return None
    # «Моча», «Кал», «Мазок из цервикального канала» → короткое слово
    return val.split(",")[0].split(" ")[0].capitalize()


# Обрывки шапки таблицы и названия приборов, которые стоят там же, где
# заголовок раздела, но исследованием не являются.
_NOT_TITLE = (
    "референсн", "показател", "результат", "ед. изм", "единиц", "норма",
    "амплификатор", "анализатор", "микроскоп", "оборудован", "аппарат",
    "система", "реагент", "метод ", "заказанные", "ооо ", "группа компаний", "адрес",
    "фактический адрес", "юридический адрес", "лицензия", "тел.", "www",
)


def _is_not_a_title(low: str) -> bool:
    return any(low.startswith(k) for k in _NOT_TITLE)


def _study_title(text: str) -> str | None:
    """Название исследования — строка-заголовок раздела перед таблицей.

    В бланке она идёт без разделителей колонок сразу перед строкой
    «Показатель | Результат | …»: «Общий анализ мочи (Физико-химический
    анализ)», «Клинический анализ крови». Если бланк состоит из нескольких
    разделов, берётся первый — он же и есть назначенное исследование.
    """
    lines = [l.strip() for l in text.splitlines()]
    for i, line in enumerate(lines):
        if not line.lower().startswith("показатель"):
            continue
        for j in range(i - 1, max(-1, i - 5), -1):
            cand = lines[j]
            if not cand or "|" in cand:
                continue
            low = cand.lower()
            if low.startswith(_HEAD_PREFIXES) or is_noise(cand) or _is_not_a_title(low):
                continue
            if 6 <= len(cand) <= 90:
                return cand.rstrip(".")
        break
    # бланки без таблицы (Фемофлор) — заголовок в первых строках
    for line in lines[:4]:
        low = line.lower()
        if not line or "|" in line or not (6 <= len(line) <= 90):
            continue
        if low.startswith(_HEAD_PREFIXES) or _is_not_a_title(low) or is_noise(line):
            continue
        return line.rstrip(".")
    return None


class SmClinicParser(BaseParser):
    name = "smclinic"

    def can_parse(self, text: str) -> bool:
        low = text.lower()
        return "см-клиника" in low or "smclinic.ru" in low or "лабораторно-диагностический центр" in low

    def parse(self, text: str) -> ParsedDocument:
        doc = ParsedDocument(lab_name=LAB_NAME)

        m = _DATE_RE.search(text)
        if m:
            d, mth, y = m.groups()
            doc.doc_date = f"{y}-{mth}-{d}"
        else:
            m = _DATE_ALT_RE.search(text)
            if m:
                doc.doc_date = parse_date(m.group(1))

        m = _FIO_RE.search(text)
        if m:
            doc.patient_name = m.group(1).strip(" .,")

        material = _material(text)
        doc.title = _study_title(text)

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

            name, value = parts[0], parts[1]
            rest = parts[2:]
            unit = rest[0] if rest else None
            ref = " ".join(rest[1:]) if len(rest) > 1 else None
            # единиц нет — третья ячейка на самом деле референс
            if unit and ref is None and (
                re.search(r"\d\s*[-–]\s*\d|[<>]", unit) or unit.lower().startswith(("не обнаруж", "прозрачн", "единично"))
            ):
                ref, unit = unit, None

            r = make_result(name, value, unit, ref)
            if not r:
                continue
            # «0 - 2» — это диапазон, а не измеренное число
            if _RANGE_VALUE_RE.match(value.strip()):
                r.value_num, r.value_text = None, value.strip()
            if material:
                r.raw_name = f"{material}: {r.raw_name}"
            results.append(r)

        doc.results = results
        if not results:
            doc.parsed_ok = False
            doc.parse_note = "бланк СМ-Клиники распознан, но строк результатов не найдено"
        return doc
