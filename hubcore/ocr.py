"""Распознавание текста на сканах через встроенный в macOS Vision.

Часть документов приходит картинкой без текстового слоя: протоколы КТ,
сфотографированные бланки. Vision выбран вместо Tesseract сознательно:
он уже есть в системе (не нужен ни brew, ни языковые пакеты), хорошо
читает русский и печатные бланки, и работает офлайн — а для медданных
это обязательное условие, наружу ничего уходить не должно.

Точка входа — `ocr_pdf(path)`. Возвращает распознанный текст или None,
если Vision недоступен либо ничего не разобрал.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger("health_hub.ocr")

# Языки в порядке приоритета. Латиница нужна для латинских терминов и
# обозначений в бланках («Th12», «L5-S1», «CT»).
LANGUAGES = ["ru-RU", "en-US"]

# Во сколько раз увеличивать страницу перед распознаванием. На 300 dpi
# мелкий шрифт бланков читается заметно надёжнее, чем на исходных 72.
RENDER_SCALE = 300 / 72


def available() -> bool:
    try:
        import Quartz  # noqa: F401
        import Vision  # noqa: F401
    except Exception:
        return False
    return True


def _recognize(png_bytes: bytes) -> str:
    import Quartz
    import Vision
    from Foundation import NSData

    data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
    src = Quartz.CGImageSourceCreateWithData(data, None)
    if src is None or Quartz.CGImageSourceGetCount(src) == 0:
        return ""
    image = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if image is None:
        return ""

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    try:
        request.setRecognitionLanguages_(LANGUAGES)
    except Exception:
        pass

    ok, err = handler.performRequests_error_([request], None)
    if not ok:
        logger.warning("Vision не смог обработать страницу: %s", err)
        return ""

    # Vision отдаёт распознанные фрагменты в порядке чтения, из-за чего
    # колонки бланка рассыпаются: сначала все названия показателей, потом
    # все значения, потом все единицы. Парсеры на таком тексте бессильны.
    # Поэтому собираем строки заново по координатам рамок, ровно так же,
    # как это делает layout_text для PDF с текстовым слоем.
    boxes = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if not candidates or not len(candidates):
            continue
        text = str(candidates[0].string())
        if not text.strip():
            continue
        b = observation.boundingBox()
        # координаты нормированы (0..1), начало отсчёта — левый нижний угол
        x0 = float(b.origin.x)
        x1 = float(b.origin.x + b.size.width)
        yc = 1.0 - float(b.origin.y + b.size.height / 2)
        boxes.append((yc, x0, x1, text))

    return _boxes_to_lines(boxes)


def _boxes_to_lines(boxes, row_tol: float = 0.010, col_gap: float = 0.022) -> str:
    """Собрать рамки Vision в строки с разделителем колонок " | "."""
    if not boxes:
        return ""
    boxes.sort()
    rows, current, row_y = [], [], None
    for yc, x0, x1, text in boxes:
        if row_y is None or abs(yc - row_y) <= row_tol:
            current.append((x0, x1, text))
            row_y = yc if row_y is None else (row_y * (len(current) - 1) + yc) / len(current)
        else:
            rows.append(current)
            current, row_y = [(x0, x1, text)], yc
    if current:
        rows.append(current)

    lines = []
    for row in rows:
        row.sort(key=lambda t: t[0])
        line, prev_x1 = "", None
        for x0, x1, text in row:
            if prev_x1 is None:
                line = text
            elif x0 - prev_x1 > col_gap:
                line += " | " + text
            else:
                line += " " + text
            prev_x1 = x1
        lines.append(line)
    return "\n".join(lines)


def ocr_pdf(path: str | Path, max_pages: int = 20) -> str | None:
    """Распознать текст в PDF-скане постранично."""
    if not available():
        logger.info("Vision недоступен — OCR пропущен")
        return None
    try:
        import fitz
    except ImportError:
        return None

    path = Path(path)
    try:
        doc = fitz.open(str(path))
    except Exception as e:
        logger.warning("Не удалось открыть %s: %s", path, e)
        return None

    parts = []
    try:
        matrix = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
        for i, page in enumerate(doc):
            if i >= max_pages:
                parts.append(f"[OCR остановлен: страниц больше {max_pages}]")
                break
            try:
                png = page.get_pixmap(matrix=matrix).tobytes("png")
                text = _recognize(png)
            except Exception as e:
                logger.warning("Ошибка OCR страницы %s в %s: %s", i + 1, path.name, e)
                continue
            if text.strip():
                parts.append(text)
    finally:
        doc.close()

    result = "\n".join(parts).strip()
    return result or None


def ocr_image(path: str | Path) -> str | None:
    """Распознать текст на фотографии или скриншоте бланка."""
    if not available():
        return None
    try:
        data = Path(path).read_bytes()
    except Exception as e:
        logger.warning("Не удалось прочитать %s: %s", path, e)
        return None
    try:
        text = _recognize(data)
    except Exception as e:
        logger.warning("Ошибка OCR %s: %s", path, e)
        return None
    return text.strip() or None
