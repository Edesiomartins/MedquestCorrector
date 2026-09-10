from __future__ import annotations

from statistics import median

import pymupdf as fitz

MIN_ANSWER_LINES = 6
MIN_LINE_WIDTH_FRAC = 0.55


def _long_horizontal_lines(page: fitz.Page) -> list[tuple[float, float, float]]:
    """Retorna (x0, x1, y) das linhas horizontais longas da área de resposta."""
    width = float(page.rect.width)
    found: list[tuple[float, float, float]] = []
    for drawing in page.get_drawings():
        for item in drawing.get("items") or []:
            if not item or item[0] != "l":
                continue
            p1, p2 = item[1], item[2]
            x0, x1 = sorted((float(p1.x), float(p2.x)))
            y0, y1 = float(p1.y), float(p2.y)
            if abs(y1 - y0) > 2.0 or x1 - x0 < width * MIN_LINE_WIDTH_FRAC:
                continue
            found.append((x0, x1, (y0 + y1) / 2.0))
    return sorted(found, key=lambda value: value[2])


def _ensure_minimum_writing_lines(page: fitz.Page) -> None:
    """Garante pelo menos seis linhas no PDF canônico quando houver espaço."""
    lines = _long_horizontal_lines(page)
    if not lines or len(lines) >= MIN_ANSWER_LINES:
        return

    ys = [line[2] for line in lines]
    gaps = [b - a for a, b in zip(ys, ys[1:]) if 10.0 <= b - a <= 45.0]
    spacing = float(median(gaps)) if gaps else 24.0
    x0 = min(line[0] for line in lines)
    x1 = max(line[1] for line in lines)
    next_y = max(ys) + spacing
    bottom_limit = float(page.rect.height) - 42.0

    while len(lines) < MIN_ANSWER_LINES and next_y <= bottom_limit:
        page.draw_line(
            fitz.Point(x0, next_y),
            fitz.Point(x1, next_y),
            color=(0, 0, 0),
            width=0.7,
        )
        lines.append((x0, x1, next_y))
        next_y += spacing


def add_student_identity_header(raw_pdf: bytes) -> bytes:
    """Prepara o PDF canônico para aplicação e posterior leitura dos scans.

    Adiciona Nome/Matrícula em todas as páginas e garante espaço de escrita
    suficiente sem deslocar o enunciado ou as caixas já existentes.
    """
    if not raw_pdf:
        raise ValueError("PDF canônico vazio.")

    doc = fitz.open(stream=raw_pdf, filetype="pdf")
    try:
        for page in doc:
            width = float(page.rect.width)
            page.insert_text(
                fitz.Point(42.0, 24.0),
                "Nome: ________________________________________________",
                fontsize=9.0,
                fontname="helv",
            )
            page.insert_text(
                fitz.Point(max(42.0, width - 210.0), 24.0),
                "Matrícula: __________________",
                fontsize=9.0,
                fontname="helv",
            )
            _ensure_minimum_writing_lines(page)
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
