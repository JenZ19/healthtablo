"""Общие кирпичики для парсеров бланков, разложенных по колонкам.

После `ingest.layout_text` строка бланка выглядит так:

    Общий белок | 78.8 | г/л | 64 - 83

То есть колонки разделены " | ". Здесь собрано всё, что нужно всем
лабораторным парсерам: разбиение строки на ячейки, склейка названий
показателей, переехавших на несколько строк, и сборка ParsedResult
из набора ячеек.
"""

from __future__ import annotations

import re

from .base import ParsedResult, compute_flag, parse_reference, split_value

COL_SEP = re.compile(r"\s*\|\s*")

# Строки, которые никогда не являются результатом исследования.
NOISE_PREFIXES = (
    "дата исследования",
    "дата выполенния",
    "дата выполнения",
    "комментарии",
    "локализация",
    "внимание!",
    "результаты исследований не являются",
    "результат лабораторных исследований",
    "качество исследований",
    "за интерпретацией",
    "получая данный результат",
    "электронная подпись",
    "исполнитель",
    "печать:",
    "страница",
    "стр.",
    "перейти на исходный",
    "документ результатов",
    "лабораторного тестирования",
    "www.",
    "материалы:",
    "информация",
    "биоматериал",
)

# Заголовки таблицы — тоже не результаты.
HEADER_WORDS = {
    "исследование", "наименование исследования", "показатель", "название",
    "результат", "значение", "ед. изм.", "ед.изм.", "единица", "единицы",
    "норма", "нормальные значения", "референсные значения", "отклонение",
}


def cells(line: str) -> list[str]:
    """Разбить строку на ячейки по разделителю колонок."""
    return [c.strip() for c in COL_SEP.split(line.strip()) if c.strip()]


def is_noise(line: str) -> bool:
    low = line.strip().lower()
    if not low or len(low) < 2:
        return True
    return any(low.startswith(p) for p in NOISE_PREFIXES)


def is_header_row(parts: list[str]) -> bool:
    if not parts:
        return True
    low = [p.strip().lower().rstrip(":") for p in parts]
    return sum(1 for p in low if p in HEADER_WORDS) >= 2 or low[0] in HEADER_WORDS


def looks_like_name(text: str) -> bool:
    """Похоже ли на название показателя, а не на число/единицу/референс."""
    t = text.strip()
    if len(t) < 2:
        return False
    letters = sum(1 for ch in t if ch.isalpha())
    return letters >= 2


def make_result(
    name: str,
    value_raw: str,
    unit: str | None = None,
    ref_raw: str | None = None,
) -> ParsedResult | None:
    """Собрать ParsedResult из сырых ячеек бланка."""
    name = (name or "").strip(" :.- ")
    if not looks_like_name(name):
        return None
    value_num, value_text = split_value(value_raw)
    if value_num is None and not value_text:
        return None
    ref_low, ref_high, ref_text = parse_reference(ref_raw) if ref_raw else (None, None, None)
    return ParsedResult(
        raw_name=name,
        value_num=value_num,
        value_text=value_text,
        unit=(unit or "").strip() or None,
        ref_low=ref_low,
        ref_high=ref_high,
        ref_text=ref_text,
    )


def flag_of(r: ParsedResult) -> str:
    return compute_flag(r.value_num, r.ref_low, r.ref_high)


_DATE = re.compile(r"\b(\d{2})\.(\d{2})\.(\d{4})\b")


def find_labeled_date(text: str, labels: list[str]) -> str | None:
    """Найти дату рядом с одной из подписей, в порядке приоритета подписей.

    Нужно, чтобы не хватать «Дата рождения» вместо даты забора — самая
    частая ошибка наивного «первая дата в документе».
    """
    lines = text.splitlines()
    for label in labels:
        lab = label.lower()
        for line in lines:
            low = line.lower()
            pos = low.find(lab)
            if pos == -1:
                continue
            m = _DATE.search(line, pos)
            if m:
                d, mth, y = m.groups()
                return f"{y}-{mth}-{d}"
    return None
