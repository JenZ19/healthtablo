"""Общий интерфейс парсера бланков + живучий generic-парсер как fallback.

Любой парсер должен уметь:
- can_parse(text) -> bool         — быстрая эвристика "похоже ли это на мой формат"
- parse(text) -> ParsedDocument   — извлечь дату, лабораторию, ФИО, результаты

Generic-парсер ничего не знает про конкретную лабораторию и просто ищет
табличные строки вида "название ... значение ... единица ... референс".
Используется как последний парсер в цепочке (PARSER_CHAIN), если ни один
специализированный парсер не подошёл, и как единственный источник данных
для любых незнакомых бланков.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Вспомогательные структуры
# ---------------------------------------------------------------------------


@dataclass
class ParsedResult:
    raw_name: str
    value_num: float | None
    value_text: str | None
    unit: str | None
    ref_low: float | None
    ref_high: float | None
    ref_text: str | None


@dataclass
class ParsedMedication:
    """Назначение из рецепта или рекомендации врача.

    Отдельно от ParsedResult: доза «80 мг» — это не измерение, и в таблице
    показателей ей не место.
    """

    name: str
    dose: str | None = None
    schedule: str | None = None
    course: str | None = None


@dataclass
class ParsedDocument:
    doc_date: str | None = None
    lab_name: str | None = None
    patient_name: str | None = None
    # Название самого исследования из бланка («Общий анализ мочи»,
    # «Клинический анализ крови»). Без него в ленте стоят одинаковые
    # строки «СМ-Клиника (ЛДЦ)», по которым ничего не найти.
    title: str | None = None
    results: list[ParsedResult] = field(default_factory=list)
    medications: list[ParsedMedication] = field(default_factory=list)
    parsed_ok: bool = True
    parse_note: str = ""


class BaseParser:
    name = "base"

    def can_parse(self, text: str) -> bool:  # pragma: no cover - интерфейс
        raise NotImplementedError

    def parse(self, text: str) -> ParsedDocument:  # pragma: no cover - интерфейс
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Даты
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")
_DATE_RE_YMD = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

# Русские месяцы в любом падеже: ключ — достаточный префикс основы.
# Порядок важен: более длинные префиксы проверяются первыми (март/марта vs ма/мая).
RU_MONTH_PREFIXES: list[tuple[str, int]] = [
    ("январ", 1), ("янв", 1),
    ("феврал", 2), ("фев", 2),
    ("март", 3), ("мар", 3),
    ("апрел", 4), ("апр", 4),
    ("мая", 5), ("май", 5),
    ("июн", 6),
    ("июл", 7),
    ("август", 8), ("авг", 8),
    ("сентябр", 9), ("сен", 9),
    ("октябр", 10), ("окт", 10),
    ("ноябр", 11), ("ноя", 11),
    ("декабр", 12), ("дек", 12),
]

# "23 Марта 2021", "24 августа 2017", "26. Август 2018"
_DATE_RE_RU = re.compile(r"\b(\d{1,2})\.?\s+([А-Яа-яЁё]{3,10})\.?\s+(\d{4})\b")


def _ru_month_number(word: str) -> int | None:
    w = word.strip().lower().replace("ё", "е")
    for prefix, num in RU_MONTH_PREFIXES:
        if w.startswith(prefix):
            return num
    return None


def parse_date_ru(text: str) -> str | None:
    """Распарсить русскую дату словом: «23 Марта 2021» → «2021-03-23»."""
    if not text:
        return None
    for m in _DATE_RE_RU.finditer(text):
        day, month_word, year = m.groups()
        month = _ru_month_number(month_word)
        if month is None:
            continue
        try:
            day_i = int(day)
        except ValueError:
            continue
        if not 1 <= day_i <= 31:
            continue
        return f"{year}-{month:02d}-{day_i:02d}"
    return None


def parse_date(text: str) -> str | None:
    """Найти первую дату в тексте и вернуть в формате ISO (YYYY-MM-DD).

    Поддерживает ДД.ММ.ГГГГ, ГГГГ-ММ-ДД и русские даты словом («23 Марта 2021»).
    """
    if not text:
        return None
    m = _DATE_RE_YMD.search(text)
    if m:
        return m.group(0)
    m = _DATE_RE.search(text)
    if m:
        day, month, year = m.groups()
        try:
            day_i, month_i = int(day), int(month)
            if 1 <= day_i <= 31 and 1 <= month_i <= 12:
                return f"{year}-{month_i:02d}-{day_i:02d}"
        except ValueError:
            return None
    return parse_date_ru(text)


# ---------------------------------------------------------------------------
# Числа и референсы
# ---------------------------------------------------------------------------

_NUM_RE = r"-?\d+[.,]?\d*"


def parse_value(text: str) -> float | None:
    """Распарсить число, поддерживая запятую как десятичный разделитель."""
    if text is None:
        return None
    t = text.strip().replace(" ", "")
    t = t.replace(",", ".")
    m = re.match(r"^[<>]?=?(-?\d+\.?\d*)$", t)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


# Маркеры «вне нормы», которые лаборатории дописывают прямо в значение:
#   Гемотест — "14.3--", "1.1++", "97-", "1.000--"
#   ВЕТЛАБ   — стрелки в отдельной колонке, но на всякий случай тоже режем
_VALUE_FLAG_SUFFIX_RE = re.compile(r"[+\-↑↓▲▼*]+$")
_CMP_PREFIX_RE = re.compile(r"^[<>≤≥]")


def strip_value_flags(raw: str) -> tuple[str, str | None]:
    """Отрезать хвостовые маркеры отклонения от значения.

    Возвращает (значение_без_маркера, маркер_или_None).
    «14.3--» → («14.3», «--»); «5,0» → («5,0», None).
    """
    t = (raw or "").strip()
    if not t:
        return "", None
    m = _VALUE_FLAG_SUFFIX_RE.search(t)
    if not m:
        return t, None
    core = t[: m.start()].strip()
    # "-" целиком (пустая ячейка) или отрицательное число — не трогаем
    if not core or not re.search(r"\d", core):
        return t, None
    return core, m.group(0)


def split_value(raw: str) -> tuple[float | None, str | None]:
    """Разобрать ячейку «Результат» на (число, текст).

    Числовым считается только «чистое» число (с запятой или точкой как
    десятичным разделителем и пробелами как разделителями тысяч).
    Всё остальное — качественный результат: «не обнаружено», «отрицательный»,
    «0-2», «<34», «РНК не обнаружена» — уходит в value_text с value_num=None.
    """
    t = (raw or "").strip()
    if not t:
        return None, None
    core, _flag = strip_value_flags(t)
    if _CMP_PREFIX_RE.match(core):
        return None, t
    num = parse_value(core)
    if num is None:
        return None, t
    return num, None


def parse_reference(text: str) -> tuple[float | None, float | None, str | None]:
    """Распознать референсный диапазон из текста бланка.

    Поддерживает: "10.0 - 20.0", "10-20", "10,0-20,0", "<5.0", "< 5", ">1.2",
    "до 5,0", "не более 5", "не менее 1.2", "отрицательно" (текстовый, без
    числового диапазона).
    Возвращает (ref_low, ref_high, ref_text_исходный).
    """
    if not text:
        return None, None, None
    raw = text.strip()
    if not raw:
        return None, None, None
    t = raw.lower()

    # "10.0 - 20.0" / "10-20" / "10,0-20,0"
    m = re.search(rf"({_NUM_RE})\s*[-–—]\s*({_NUM_RE})", t)
    if m:
        low = parse_value(m.group(1))
        high = parse_value(m.group(2))
        return low, high, raw

    # "< 5.0", "менее 5", "до 5,0", "не более 5", "≤ 5"
    m = re.search(rf"(?:[<≤]|\bменее\b|\bдо\b|\bне более\b)\s*({_NUM_RE})", t)
    if m:
        return None, parse_value(m.group(1)), raw

    # "> 1.2", "более 1.2", "не менее 1.2", "≥ 1.2"
    m = re.search(rf"(?:[>≥]|\bболее\b|\bне менее\b|\bот\b)\s*({_NUM_RE})", t)
    if m:
        return parse_value(m.group(1)), None, raw

    # чисто текстовый референс типа "отрицательно" / "норма"
    return None, None, raw


_FLAG_WORDS = {"отрицательно", "отр", "норма", "в норме", "негативно", "не обнаружено"}


def compute_flag(value_num: float | None, ref_low: float | None, ref_high: float | None) -> str:
    if value_num is None:
        return "unknown"
    if ref_low is not None and value_num < ref_low:
        return "low"
    if ref_high is not None and value_num > ref_high:
        return "high"
    if ref_low is not None or ref_high is not None:
        return "normal"
    return "unknown"


# ---------------------------------------------------------------------------
# Generic-парсер
# ---------------------------------------------------------------------------

_VALUE_TOKEN_RE = re.compile(rf"^[<>]?=?\s*{_NUM_RE}$")
_SPLIT_COLS_RE = re.compile(r"\t+| {2,}|\s*\|\s*")


def _split_columns(line: str) -> list[str]:
    parts = [p.strip() for p in _SPLIT_COLS_RE.split(line.strip()) if p.strip()]
    return parts


def _looks_like_value(tok: str) -> bool:
    tok = tok.strip()
    return bool(_VALUE_TOKEN_RE.match(tok))


class GenericParser(BaseParser):
    """Живучий fallback: ищет строки вида "название  значение  единица  референс".

    Не привязан к конкретной лаборатории — работает по общей эвристике
    расположения колонок, поэтому годится как последний парсер в цепочке.
    """

    name = "generic"

    def can_parse(self, text: str) -> bool:
        # Generic всегда "подходит" — это fallback последней инстанции.
        return True

    def parse(self, text: str) -> ParsedDocument:
        doc = ParsedDocument()
        if not text:
            doc.parsed_ok = False
            doc.parse_note = "нет текста для разбора"
            return doc

        doc.doc_date = parse_date(text)

        results: list[ParsedResult] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or len(line) < 4:
                continue
            cols = _split_columns(line)
            if len(cols) < 2:
                # попробовать разложить строку по одиночным пробелам как
                # последний резерв: "Имя показателя 12.3 г/л 10-20"
                tokens = line.split()
                if len(tokens) < 2:
                    continue
                value_idx = None
                for i, tok in enumerate(tokens):
                    if _looks_like_value(tok) and i > 0:
                        value_idx = i
                        break
                if value_idx is None:
                    continue
                name = " ".join(tokens[:value_idx])
                value_tok = tokens[value_idx]
                rest = tokens[value_idx + 1:]
                unit = rest[0] if rest and not re.search(r"\d", rest[0]) else None
                ref_text = " ".join(rest[1:] if unit else rest)
            else:
                name = cols[0]
                value_idx = None
                for i in range(1, len(cols)):
                    if _looks_like_value(cols[i]):
                        value_idx = i
                        break
                if value_idx is None:
                    continue
                value_tok = cols[value_idx]
                rest_cols = cols[value_idx + 1:]
                unit = rest_cols[0] if rest_cols else None
                ref_text = " ".join(rest_cols[1:]) if len(rest_cols) > 1 else (rest_cols[0] if False else None)
                if unit and re.search(r"[-–—<>]|\d.*\d", unit) and re.search(_NUM_RE, unit):
                    # похоже, что это уже референс, а не единица — колонка единиц пропущена
                    ref_text = unit
                    unit = None

            if not name or len(name) < 2:
                continue
            # похоже на заголовок / мусор
            if re.match(r"^(результат|показатель|анализ|референс|единиц)", name.strip().lower()):
                continue

            value_num = parse_value(value_tok)
            value_text = value_tok if value_num is None else None
            ref_low = ref_high = None
            if ref_text:
                ref_low, ref_high, ref_text = parse_reference(ref_text)

            results.append(
                ParsedResult(
                    raw_name=name.strip(" :.-"),
                    value_num=value_num,
                    value_text=value_text,
                    unit=unit.strip() if unit else None,
                    ref_low=ref_low,
                    ref_high=ref_high,
                    ref_text=ref_text,
                )
            )

        doc.results = results
        if not results:
            doc.parsed_ok = False
            doc.parse_note = "generic-парсер не нашёл табличных строк"
        return doc
