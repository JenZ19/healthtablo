"""Парсеры ветеринарных бланков: «Ветсоюз» и «ВЕТЛАБ» (Митрохина).

Оба бланка относятся к животному, а не к человеку, поэтому вместо ФИО
пациента они отдают кличку — ingest сопоставляет её с субъектом-собакой.

--- Ветсоюз (vetunion.ru): ПЦР кала, качественные результаты ---

    Представитель | ИВАНОВА М С
    Вид | Собака
    Кличка | БАРСИК
    Дата взятия образца: | 23.04.2025
    Исследование | Результат | Единицы | Референсные значения
    Криптоспоридии | не обнар
    (Cryptosporidium spp.)
    Клостридиальный | обнаруж
    энтеротоксин (Clostridium
    perfringens)

Грабли: латинское название возбудителя переносится на следующие строки уже
ПОСЛЕ строки со значением, поэтому хвост надо приклеивать к предыдущему
результату, а не к следующему.

--- ВЕТЛАБ (mitrokhina.ru): биохимия и посевы ---

    РЕЗУЛЬТАТ ИССЛЕДОВАНИЯ № 000000001 от 23.04.2025
    Владелец:Иванова М. С. | Порода:Мальтийская болонка
    Кличка:Барсик
    Показатель | Результат | Единица | Норма | Отклонение
    АЛТ (ALT) | 84,7 | ед./л | 10,0 - 58,0 | ▲46%

Грабли: двоеточие без пробела после подписи; референс иногда разрывается
на две строки («300,0 -» / «1500,0»); колонка «Отклонение» содержит либо
слово «норма», либо стрелку с процентом — её используем только для сверки.
"""

from __future__ import annotations

import re

from .base import BaseParser, ParsedDocument, compute_flag
from .tabular import cells, is_header_row, is_noise, make_result

# ---------------------------------------------------------------------------
# Ветсоюз
# ---------------------------------------------------------------------------

VETUNION_LAB = "Ветсоюз"

_QUALITATIVE = ("обнаруж", "не обнар", "выявлен", "не выявлен", "положит", "отрицат")

# Качественный результат, прилипший к названию в конце строки.
_TRAILING_QUAL_RE = re.compile(
    r"(?:не\s+)?(?:обнаруж\w*|обнар|выявл\w*|положит\w*|отрицат\w*)\s*$", re.I
)


class VetunionParser(BaseParser):
    name = "vetunion"

    def can_parse(self, text: str) -> bool:
        return "vetunion.ru" in text.lower()

    def parse(self, text: str) -> ParsedDocument:
        doc = ParsedDocument(lab_name=VETUNION_LAB)

        m = re.search(r"Кличка\s*\|?\s*([^\|\n]+)", text, re.I)
        if m:
            doc.patient_name = m.group(1).strip()

        m = re.search(r"Дата взятия образца:?\s*\|?\s*(\d{2})\.(\d{2})\.(\d{4})", text, re.I)
        if m:
            d, mth, y = m.groups()
            doc.doc_date = f"{y}-{mth}-{d}"

        results = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or is_noise(line):
                continue
            parts = cells(line)

            if len(parts) == 1:
                token = parts[0]
                # Колонка результата иногда стоит вплотную к названию, и
                # раскладка не успевает поставить границу:
                #   "Кампилобактер (Campylobacter обнаруж"
                m = _TRAILING_QUAL_RE.search(token)
                if m and m.start() > 3:
                    r = make_result(token[: m.start()], m.group(0))
                    if r:
                        results.append(r)
                        continue
                # иначе это хвост латинского названия, приехавший ниже значения
                if results and re.search(r"[A-Za-z]", token) and len(token) < 60:
                    results[-1].raw_name = f"{results[-1].raw_name} {token}".strip()
                continue

            if is_header_row(parts):
                continue
            head = parts[0].lower().rstrip(": ")
            if head.startswith(("представитель", "вид", "кличка", "инз", "дата",
                                "врач", "направивший", "ип ", "ростов")):
                continue

            value = parts[1]
            if not any(q in value.lower() for q in _QUALITATIVE):
                continue
            r = make_result(parts[0], value)
            if r:
                results.append(r)

        doc.results = results
        # Название бланка Ветсоюза — перечисление самих исследований: в один
        # заказ входит по одному-двум возбудителям, и «Ветсоюз, 23.04.2025»
        # на трёх бланках подряд не даёт различить, где какой анализ.
        if results:
            names = [r.raw_name.split("(")[0].strip() for r in results]
            doc.title = "ПЦР: " + ", ".join(names)[:90]
        else:
            doc.parsed_ok = False
            doc.parse_note = "бланк Ветсоюза распознан, но результатов не найдено"
        return doc


# ---------------------------------------------------------------------------
# ВЕТЛАБ
# ---------------------------------------------------------------------------

VETLAB_LAB = "ВЕТЛАБ (Митрохина)"

_DEVIATION_RE = re.compile(r"^(норма|[▲▼↑↓]\s*\d+\s*%?)$", re.I)
_KV_RE = re.compile(r"([А-Яа-яЁё\s\.]+?)\s*:\s*([^|]*)")


def _vetlab_field(text: str, key: str) -> str | None:
    """Достать значение поля вида «Кличка:Барсик» (двоеточие без пробела)."""
    m = re.search(rf"{key}\s*:\s*([^|\n]+)", text, re.I)
    return m.group(1).strip() if m else None


# Название исследования в бланке ВЕТЛАБ стоит отдельной строкой после шапки
# и до таблицы: «Биохимия крови "Расширенная"», «Комплексное
# микробиологическое исследование». Без него все бланки лаборатории
# назывались одинаково — «ВЕТЛАБ (Митрохина)» плюс дата, и отличить посев
# от биохимии в ленте было нельзя.
_VETLAB_HEAD_FIELDS = (
    "владелец", "название клиники", "лечащий", "животное", "порода",
    "возраст", "кличка", "пол", "дата", "результат исследования", "сеть",
    "материал", "информация", "микробиологический профиль", "www", "+7",
)

_VETLAB_STUDY_HINTS = (
    "исследование", "биохимия", "анализ", "профиль", "посев", "гормон",
    "коагул", "цитолог", "соскоб", "смыв",
)


def _vetlab_study_title(text: str) -> str | None:
    parts: list[str] = []
    for raw in text.splitlines():
        line = raw.strip().strip("|").strip()
        low = line.lower()
        if not line or "|" in raw or len(line) < 6 or len(line) > 90:
            continue
        if low.startswith(_VETLAB_HEAD_FIELDS) or ":" in line[:22]:
            continue
        if any(k in low for k in _VETLAB_STUDY_HINTS):
            parts.append(line)
            # название бывает из двух строк: «Комплексное микробиологическое
            # исследование» + «с определением чувствительности к…»
            if len(parts) == 2:
                break
    if not parts:
        return None
    title = " ".join(parts)
    return title[:110].strip(" .,")


class VetlabParser(BaseParser):
    name = "vetlab"

    def can_parse(self, text: str) -> bool:
        low = text.lower()
        return "mitrokhina.ru" in low or "ветеринарных лабораторий ветлаб" in low

    def parse(self, text: str) -> ParsedDocument:
        doc = ParsedDocument(lab_name=VETLAB_LAB)
        doc.patient_name = _vetlab_field(text, "Кличка")
        doc.title = _vetlab_study_title(text)

        m = re.search(r"РЕЗУЛЬТАТ ИССЛЕДОВАНИЯ.*?от\s+(\d{2})\.(\d{2})\.(\d{4})", text, re.I)
        if not m:
            m = re.search(r"Дата проведения анализа\s*:\s*(\d{2})\.(\d{2})\.(\d{4})", text, re.I)
        if m:
            d, mth, y = m.groups()
            doc.doc_date = f"{y}-{mth}-{d}"

        results = []
        # Широкий референс не помещается в ячейку и разрывается на три строки:
        #     300,0 -
        #     а-Амилаза (AMY) | 582,8 | ед./л | норма
        #     1500,0
        # То есть начало приезжает ДО строки показателя, а хвост — ПОСЛЕ.
        ref_head = ""          # накопленное начало референса
        awaiting: list = []    # результат, которому этот референс принадлежит

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or is_noise(line):
                continue
            parts = cells(line)

            # обрывок референса без собственного показателя
            if len(parts) == 1 and re.fullmatch(r"[\d\s,\.\-–—]+", parts[0]):
                fragment = parts[0]
                if awaiting and ref_head:
                    from .base import parse_reference

                    low_, high_, txt_ = parse_reference(f"{ref_head} {fragment}")
                    r_ = awaiting[0]
                    r_.ref_low, r_.ref_high, r_.ref_text = low_, high_, txt_
                    ref_head, awaiting = "", []
                else:
                    ref_head = fragment
                continue

            if len(parts) < 2 or is_header_row(parts):
                continue
            head = parts[0].lower().rstrip(": ")
            if head.startswith(("владелец", "название клиники", "лечащий", "животное",
                                "порода", "возраст", "кличка", "пол", "дата",
                                "результат исследования", "материал", "сеть",
                                "микробиологический", "чувствительность", "выделенные")):
                continue

            name, value = parts[0], parts[1]
            rest = parts[2:]
            # отбросить колонку «Отклонение», она не единица и не референс
            rest = [c for c in rest if not _DEVIATION_RE.match(c.strip())]
            unit = rest[0] if rest else None
            ref = " ".join(rest[1:]) if len(rest) > 1 else None
            if unit and ref is None and re.search(r"\d\s*-\s*\d|[<>]", unit):
                ref, unit = unit, None

            r = make_result(name, value, unit, ref)
            if r:
                results.append(r)
                # референса в строке нет, но перед ней было начало диапазона —
                # значит хвост приедет следующей строкой
                if r.ref_text is None and ref_head:
                    awaiting = [r]
                else:
                    ref_head, awaiting = "", []

        doc.results = results
        if not results:
            doc.parsed_ok = False
            doc.parse_note = "бланк ВЕТЛАБ распознан, но строк результатов не найдено"
        return doc
