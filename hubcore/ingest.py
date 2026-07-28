"""Разбор входящих файлов: копирование в архив, извлечение текста, парсинг,
сопоставление субъекта и маркеров, запись в БД.

Основная точка входа — ingest_path(). Идемпотентна по sha256 файла.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import db as db_module
from .analytes import match_analyte, normalize_unit
from .parsers import PARSER_CHAIN
from .parsers.prescription import course_days as prescription_course_days
from .parsers.base import ParsedDocument, compute_flag

logger = logging.getLogger("health_hub.ingest")

PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff"}
TEXT_EXTS = {".txt"}


@dataclass
class IngestResult:
    status: str  # 'ingested' | 'duplicate' | 'error' | 'dry_run' | 'needs_review'
    path: str
    document_id: int | None = None
    subject_slug: str | None = None
    doc_date: str | None = None
    lab_name: str | None = None
    results_count: int = 0
    matched_count: int = 0
    parsed_ok: bool = False
    parse_note: str = ""
    message: str = ""
    parsed: ParsedDocument | None = field(default=None, repr=False)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def ocr_hook(path: Path) -> str | None:
    """Распознать текст на скане или фотографии бланка.

    Работает через встроенный в macOS Vision (hubcore/ocr.py): офлайн,
    без внешних зависимостей. Текст возвращается уже разложенным по
    колонкам, поэтому обычные парсеры разбирают его так же, как PDF
    с текстовым слоем.
    """
    from .ocr import ocr_image, ocr_pdf

    try:
        if path.suffix.lower() in PDF_EXTS:
            return ocr_pdf(path)
        return ocr_image(path)
    except Exception as e:
        logger.warning("OCR не сработал для %s: %s", path.name, e)
        return None


def layout_text(page, col_gap: float = 8.0, row_tol: float = 3.5) -> str:
    """Собрать текст страницы с сохранением колонок таблицы.

    Обычный `page.get_text()` идёт по внутреннему порядку блоков и склеивает
    ячейки соседних колонок в кашу: название показателя, значение и референс
    оказываются на разных строках или в одной строке без границ. Здесь текст
    восстанавливается по координатам слов: слова группируются в строки по
    вертикали, внутри строки сортируются по горизонтали, а заметный
    горизонтальный разрыв между словами превращается в разделитель колонок
    " | ". На выходе строка вида "Общий белок | 78.8 | г/л | 64 - 83",
    которую парсеры разбирают уже тривиально.

    col_gap — минимальный зазор (в пунктах), считающийся границей колонки.
    row_tol — допуск по вертикали, в пределах которого слова считаются одной
    строкой (нужен, т.к. базовые линии в ячейках гуляют на доли пункта).
    """
    words = page.get_text("words")
    if not words:
        return page.get_text()

    # (центр по вертикали,x0, x1, слово)
    items = sorted(((w[1] + w[3]) / 2, w[0], w[2], w[4]) for w in words)

    rows: list[list[tuple[float, float, str]]] = []
    current: list[tuple[float, float, str]] = []
    row_y: float | None = None
    for yc, x0, x1, txt in items:
        if row_y is None or abs(yc - row_y) <= row_tol:
            current.append((x0, x1, txt))
            row_y = yc if row_y is None else (row_y * (len(current) - 1) + yc) / len(current)
        else:
            rows.append(current)
            current, row_y = [(x0, x1, txt)], yc
    if current:
        rows.append(current)

    lines = []
    for row in rows:
        row.sort(key=lambda t: t[0])
        line, prev_x1 = "", None
        for x0, x1, txt in row:
            if prev_x1 is None:
                line = txt
            elif x0 - prev_x1 > col_gap:
                line += " | " + txt
            else:
                line += " " + txt
            prev_x1 = x1
        lines.append(line)
    return "\n".join(lines)


def extract_text(path: Path) -> tuple[str, int, bool, str]:
    """Вернуть (text, page_count, parsed_ok, note)."""
    ext = path.suffix.lower()
    if ext in PDF_EXTS:
        try:
            import fitz  # PyMuPDF
        except ImportError as e:  # pragma: no cover
            return "", 0, False, f"PyMuPDF не установлен: {e}"
        try:
            doc = fitz.open(str(path))
        except Exception as e:
            return "", 0, False, f"ошибка открытия PDF: {e}"
        try:
            parts = [layout_text(page) for page in doc]
            page_count = doc.page_count
        finally:
            doc.close()
        text = "\n".join(parts)
        if not text.strip():
            ocr_text = ocr_hook(path)
            if ocr_text:
                return ocr_text, page_count, True, ""
            return "", page_count, False, "скан, нужен OCR"
        return text, page_count, True, ""
    elif ext in IMAGE_EXTS:
        ocr_text = ocr_hook(path)
        if ocr_text:
            return ocr_text, 1, True, ""
        return "", 1, False, "изображение без текстового слоя, нужен OCR"
    elif ext in TEXT_EXTS:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return "", 0, False, f"ошибка чтения файла: {e}"
        return text, 1, bool(text.strip()), "" if text.strip() else "пустой текстовый файл"
    else:
        return "", 0, False, f"неподдерживаемый формат: {ext or '(без расширения)'}"


def slugify(s: str, maxlen: int = 40) -> str:
    if not s:
        return "doc"
    s = s.strip().lower()
    s = re.sub(r"[^\w\-]+", "-", s, flags=re.UNICODE)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:maxlen] or "doc"


def run_parsers(text: str) -> ParsedDocument:
    for parser in PARSER_CHAIN:
        try:
            if parser.can_parse(text):
                parsed = parser.parse(text)
                parsed.parse_note = f"[{parser.name}] {parsed.parse_note}".strip()
                return parsed
        except Exception as e:  # парсер не должен ронять весь ingest
            logger.warning("Парсер %s упал: %s", getattr(parser, "name", "?"), e)
            continue
    doc = ParsedDocument(parsed_ok=False, parse_note="ни один парсер не подошёл")
    return doc


def _norm_word(w: str) -> str:
    return re.sub(r"[^\w]", "", w.lower().replace("ё", "е"), flags=re.UNICODE)


def person_key(name: str) -> tuple[str, tuple[str, ...]] | None:
    """Свести ФИО к паре (фамилия, инициалы) для устойчивого сравнения.

    «Иванова Анна Петровна», «ИВАНОВА, Анна Петровна» и
    «Иванова А. П.» дают один и тот же ключ ('иванова', ('е','а')).
    А «Иванова М. С.» — другой, и это принципиально: в ветеринарных
    бланках владелец собаки записан именно так, и его нельзя молча
    приклеить к профилю другого человека с той же фамилией.
    """
    if not name:
        return None
    words = [_norm_word(w) for w in re.split(r"[\s,]+", name) if _norm_word(w)]
    if not words:
        return None
    surname, rest = words[0], words[1:]
    initials = tuple(w[0] for w in rest if w)
    return surname, initials


def nickname_key(name: str) -> str:
    return _norm_word(name)


def subject_aliases(row) -> list[str]:
    """Имя субъекта плюс альтернативные имена из поля «Заметки».

    В заметках алиасы пишутся строкой вида «также: Барсик, Барсик».
    """
    out = [row["name"] or ""]
    notes = row["notes"] or ""
    m = re.search(r"(?:также|алиас(?:ы)?|aka)\s*:\s*([^\n]+)", notes, re.I)
    if m:
        out.extend(p.strip() for p in m.group(1).split(","))
    else:
        # заметка без явного маркера — считаем её списком альтернативных имён
        out.extend(p.strip() for p in re.split(r"[,\n;]+", notes))
    # имя вида «Барсик» — учесть и то, что в скобках
    for extra in re.findall(r"\(([^)]+)\)", row["name"] or ""):
        out.append(extra)
    out = [o.strip() for o in out if o and o.strip()]
    return out


def match_subject_by_name(conn, patient_name: str | None):
    """Сопоставить имя из бланка с субъектом хаба.

    Для людей сравниваются фамилия и инициалы, для животных — кличка.
    Если совпадения нет, возвращается None, и документ уходит в «Требует
    разбора» — это лучше, чем привязать чужой анализ не к тому человеку.
    """
    if not patient_name or not patient_name.strip():
        return None

    rows = list(conn.execute("SELECT id, name, kind, notes FROM subjects"))
    target_person = person_key(patient_name)
    target_nick = nickname_key(patient_name)

    # животные — по кличке
    for row in rows:
        if row["kind"] == "human":
            continue
        for alias in subject_aliases(row):
            if nickname_key(alias) == target_nick:
                return row["id"]

    # люди — по фамилии и инициалам
    if target_person:
        for row in rows:
            if row["kind"] != "human":
                continue
            for alias in subject_aliases(row):
                key = person_key(alias)
                if not key:
                    continue
                if key[0] != target_person[0]:
                    continue
                # фамилия совпала: инициалы должны совпасть тоже,
                # но если в одном из имён их просто нет — считаем совпадением
                if not key[1] or not target_person[1] or key[1] == target_person[1]:
                    return row["id"]
    return None


def get_subject_slug(conn, subject_id: int | None) -> str | None:
    if subject_id is None:
        return None
    row = conn.execute("SELECT slug FROM subjects WHERE id=?", (subject_id,)).fetchone()
    return row["slug"] if row else None


def resolve_subject_id(conn, subject: str | None) -> int | None:
    if not subject:
        return None
    row = conn.execute("SELECT id FROM subjects WHERE slug=?", (subject,)).fetchone()
    if row:
        return row["id"]
    row = conn.execute("SELECT id FROM subjects WHERE lower(name)=lower(?)", (subject,)).fetchone()
    return row["id"] if row else None


def store_original(path: Path, subject_slug: str | None, doc_date: str | None, title_hint: str, sha8: str) -> Path:
    subdir = subject_slug or "unsorted"
    date_part = doc_date or "без-даты"
    name_part = slugify(title_hint)
    ext = path.suffix.lower() or ""
    dest_dir = db_module.FILES_DIR / subdir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{date_part}__{name_part}__{sha8}{ext}"
    shutil.copy2(path, dest)
    return dest


MATERIAL_PREFIX_RE = re.compile(r"^(моча|кал|мокрота|слюна)\s*:\s*", re.I)


def split_material(raw_name: str) -> tuple[str | None, str]:
    """Отделить биоматериал от названия показателя.

    «Моча: Глюкоза» → («моча», «Глюкоза»). Для крови префикса нет.
    """
    m = MATERIAL_PREFIX_RE.match(raw_name or "")
    if not m:
        return None, raw_name
    return m.group(1).lower(), raw_name[m.end():].strip()


# Показатели лейкоформулы лаборатории дают дважды: в процентах и в
# абсолютных числах. Это две разные величины с разными единицами и разными
# референсами, и попадать в один график они не должны — иначе ряд выглядит
# как пила: 9,4 % → 0,41 ×10⁹/л → 8,7 % → 0,43 ×10⁹/л, и кажется, будто
# показатель обваливается в ноль через раз.
_ABS_FORM_RE = re.compile(r"\bабс\w*\b|\babs\b", re.I)
_PCT_FORM_RE = re.compile(r"%")


# Единицы концентрации: если показатель лейкоформулы измерен в них, это
# абсолютное число, даже когда в названии про это ни слова. Часть бланков
# пишет просто «Моноциты» и ставит «x10*9/л» — по названию не отличить.
_ABS_UNIT_RE = re.compile(r"10\s*\*?\^?\s*9|тыс/мкл|кл/мкл|10\^9|г/л\b(?!)", re.I)

# Показатели, которые лаборатории дают и в процентах, и в абсолютных числах.
_DUAL_FORM_MARKERS = (
    "нейтрофил", "лимфоцит", "моноцит", "эозинофил", "базофил",
)


def split_form(raw_name: str, unit: str | None = None) -> tuple[str | None, str]:
    """Отделить форму показателя (процент или абсолютное число) от названия.

    Возвращает ('абс', базовое_имя) для абсолютных чисел и (None, имя) для
    процентов и всего остального. Форма определяется сначала по названию,
    а если там не сказано — по единицам измерения.
    """
    name = raw_name or ""
    if _ABS_FORM_RE.search(name):
        return "абс", _ABS_FORM_RE.sub("", name).strip(" ,.()")

    low = name.lower()
    is_dual = any(k in low for k in _DUAL_FORM_MARKERS)
    u = (unit or "").strip()

    if is_dual and u and u != "%" and _ABS_UNIT_RE.search(u):
        return "абс", name.strip(" ,.()")

    if _PCT_FORM_RE.search(name) or u == "%":
        return None, name.replace("%", "").strip(" ,.()") or name
    return None, name


def resolve_analyte(conn, raw_name: str, unit: str | None = None) -> int | None:
    """Подобрать аналит под название из бланка, разводя биоматериалы.

    Глюкоза крови и глюкоза мочи — разные показатели с разными единицами и
    референсами, и класть их в один график нельзя: 0,0 ммоль/л в моче рядом
    с 4,7 ммоль/л в крови выглядит как обвал сахара, которого не было.
    Поэтому для небазового биоматериала заводится отдельный аналит.
    """
    material, base_name = split_material(raw_name)
    form, base_name = split_form(base_name, unit)
    code = match_analyte(base_name)
    if code is None:
        return None
    if material is None and form is None:
        return _analyte_id_by_code(conn, code)

    suffix_map = {'моча': 'urine', 'кал': 'stool', 'мокрота': 'sputum', 'слюна': 'saliva'}
    parts = []
    if material:
        parts.append(suffix_map[material])
    if form:
        parts.append('abs')
    derived = code + '__' + '_'.join(parts)
    row = conn.execute("SELECT id FROM analytes WHERE code=?", (derived,)).fetchone()
    if row:
        return row["id"]
    base = conn.execute(
        "SELECT name_ru, name_en, category FROM analytes WHERE code=?", (code,)
    ).fetchone()
    qualifier = ", ".join(q for q in (material, "абс." if form else None) if q)
    name_ru = f"{base['name_ru'] if base else base_name} ({qualifier})"
    cur = conn.execute(
        "INSERT INTO analytes (code, name_ru, name_en, unit_canonical, category, description) "
        "VALUES (?, ?, ?, NULL, ?, ?)",
        (derived, name_ru, base["name_en"] if base else None,
         f"{material or 'кровь'} — {base['category'] if base else 'прочее'}",
         f"Отдельный аналит: {qualifier}. Единицы и референсы отличаются от "
         f"одноимённого показателя, в одном графике они несопоставимы."),
    )
    return cur.lastrowid


def rescale_specific_gravity(raw_name: str, value, ref_low, ref_high):
    """Привести удельный вес мочи к единой шкале 1.000–1.040.

    Одни лаборатории печатают «1.024», другие — «1020». Числа означают одно
    и то же, но на общем графике выглядят как разница в тысячу раз. Величина
    безразмерная и физиологически всегда лежит около единицы, поэтому деление
    на 1000 для «больших» записей однозначно и не может исказить смысл.
    """
    if "дельный вес" not in (raw_name or "").lower():
        return value, ref_low, ref_high
    scale = lambda v: (v / 1000.0 if v is not None and v > 100 else v)
    return scale(value), scale(ref_low), scale(ref_high)


def _analyte_id_by_code(conn, code: str | None) -> int | None:
    if not code:
        return None
    row = conn.execute("SELECT id FROM analytes WHERE code=?", (code,)).fetchone()
    return row["id"] if row else None


def ingest_path(
    path: str | Path,
    subject: str | None = None,
    source: str = "local",
    db_path: str | Path | None = None,
    dry_run: bool = False,
) -> IngestResult:
    path = Path(path)
    if not path.exists() or not path.is_file():
        return IngestResult(status="error", path=str(path), message="файл не найден")

    try:
        sha = sha256_file(path)
    except Exception as e:
        return IngestResult(status="error", path=str(path), message=f"не удалось прочитать файл: {e}")
    sha8 = sha[:8]

    with db_module.get_conn(db_path) as conn:
        existing = conn.execute("SELECT id FROM documents WHERE sha256=?", (sha,)).fetchone()
        if existing and not dry_run:
            return IngestResult(
                status="duplicate",
                path=str(path),
                document_id=existing["id"],
                message="документ с таким содержимым уже есть в базе",
            )

        try:
            text, page_count, parsed_ok, note = extract_text(path)
        except Exception as e:
            logger.exception("Ошибка извлечения текста из %s", path)
            text, page_count, parsed_ok, note = "", 0, False, f"ошибка извлечения текста: {e}"

        parsed = run_parsers(text) if text else ParsedDocument(parsed_ok=False, parse_note=note or "нет текста")
        if not text:
            parsed.parsed_ok = False
            parsed.parse_note = note

        subject_id = resolve_subject_id(conn, subject)
        if subject_id is None and parsed.patient_name:
            subject_id = match_subject_by_name(conn, parsed.patient_name)

        subject_slug = get_subject_slug(conn, subject_id)
        title_hint = parsed.lab_name or path.stem
        title = parsed.title or f"{parsed.lab_name or 'Документ'} {parsed.doc_date or ''}".strip()

        if dry_run:
            return IngestResult(
                status="dry_run",
                path=str(path),
                subject_slug=subject_slug,
                doc_date=parsed.doc_date,
                lab_name=parsed.lab_name,
                results_count=len(parsed.results),
                matched_count=sum(1 for r in parsed.results if match_analyte(split_material(r.raw_name)[1])),
                parsed_ok=parsed.parsed_ok,
                parse_note=parsed.parse_note,
                message="сухой прогон — в БД ничего не записано",
                parsed=parsed,
            )

        try:
            stored_path = store_original(path, subject_slug, parsed.doc_date, title_hint, sha8)
        except Exception as e:
            logger.exception("Не удалось скопировать файл %s", path)
            return IngestResult(status="error", path=str(path), message=f"не удалось скопировать файл: {e}")

        cur = conn.execute(
            """INSERT INTO documents
               (subject_id, kind, title, doc_date, lab_name, source, source_path, stored_path,
                sha256, page_count, raw_text, parsed_ok, parse_note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                subject_id,
                "lab",
                title or path.stem,
                parsed.doc_date,
                parsed.lab_name,
                source,
                str(path),
                str(stored_path),
                sha,
                page_count,
                text,
                1 if parsed.parsed_ok else 0,
                parsed.parse_note,
            ),
        )
        document_id = cur.lastrowid

        matched_count = 0
        for r in parsed.results:
            analyte_id = resolve_analyte(conn, r.raw_name, r.unit)
            # единицы приводим по базовому названию без префикса биоматериала
            code = match_analyte(split_material(r.raw_name)[1])
            value_num, unit = normalize_unit(r.value_num, r.unit, code)
            value_num, r.ref_low, r.ref_high = rescale_specific_gravity(
                r.raw_name, value_num, r.ref_low, r.ref_high
            )
            flag = compute_flag(value_num, r.ref_low, r.ref_high)
            if analyte_id:
                matched_count += 1
            conn.execute(
                """INSERT INTO results
                   (document_id, subject_id, analyte_id, raw_name, value_num, value_text, unit,
                    ref_low, ref_high, ref_text, flag, taken_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    document_id,
                    subject_id,
                    analyte_id,
                    r.raw_name,
                    value_num,
                    r.value_text,
                    unit,
                    r.ref_low,
                    r.ref_high,
                    r.ref_text,
                    flag,
                    parsed.doc_date,
                ),
            )

        # Назначения из рецептов и рекомендаций — в лекарства, а не в анализы.
        for med in parsed.medications:
            if subject_id is None:
                break
            already = conn.execute(
                "SELECT id FROM medications WHERE subject_id=? AND name=? AND started IS ?",
                (subject_id, med.name, parsed.doc_date),
            ).fetchone()
            if already:
                continue
            # Курс с длительностью превращаем в дату окончания: без неё
            # назначение висит в хабе как бессрочное, и календарь таблеток
            # продолжает требовать приёмов после конца курса.
            ended = None
            days = prescription_course_days(med.course)
            if days and parsed.doc_date:
                try:
                    start = dt.date.fromisoformat(parsed.doc_date[:10])
                    ended = (start + dt.timedelta(days=days - 1)).isoformat()
                except ValueError:
                    ended = None

            conn.execute(
                """INSERT INTO medications
                     (subject_id, name, dose, schedule, started, ended, reason, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    subject_id,
                    med.name,
                    med.dose,
                    med.schedule,
                    parsed.doc_date,
                    ended,
                    parsed.title,
                    f"курс: {med.course}" if med.course else None,
                ),
            )

        status = "ingested" if subject_id is not None else "needs_review"
        return IngestResult(
            status=status,
            path=str(path),
            document_id=document_id,
            subject_slug=subject_slug,
            doc_date=parsed.doc_date,
            lab_name=parsed.lab_name,
            results_count=len(parsed.results),
            matched_count=matched_count,
            parsed_ok=parsed.parsed_ok,
            parse_note=parsed.parse_note,
            message="ок" if subject_id is not None else "субъект не определён — требует разбора",
        )


def ingest_inbox(
    subject: str | None = None,
    source: str = "local",
    db_path: str | Path | None = None,
    dry_run: bool = False,
) -> list[IngestResult]:
    db_module.ensure_dirs()
    results: list[IngestResult] = []
    for p in sorted(db_module.INBOX_DIR.iterdir()):
        if p.is_file() and not p.name.startswith("."):
            try:
                results.append(ingest_path(p, subject=subject, source=source, db_path=db_path, dry_run=dry_run))
            except Exception as e:
                logger.exception("Не удалось разобрать %s", p)
                results.append(IngestResult(status="error", path=str(p), message=str(e)))
    return results
