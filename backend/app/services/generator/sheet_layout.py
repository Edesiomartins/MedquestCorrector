"""
Layout da folha-resposta (coordenadas em pontos PDF, origem inferior esquerda).

Deve permanecer alinhado com `answer_sheet._draw_sheet`: qualquer mudança visual no PDF
deve ser espelhada aqui para crops/OCR e para o manifesto JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.pdfbase.pdfmetrics import stringWidth


# Mesmos valores que em answer_sheet._draw_sheet
MARGIN = 2 * cm
FIDUCIAL_MM = 4 * mm
FIDUCIAL_OUTER_GAP = 2 * mm
QR_SIZE = 18 * mm
# Recuo do primeiro texto útil abaixo do topo.
PAGE_TOP_CONTENT_INSET = 6 * mm
# Espaço entre a linha "(cont.)" e o baseline de "Questão N"; inclui o QR no topo.
CONTINUATION_GAP_BELOW_HEADER = QR_SIZE + 10 * mm
QUESTION_TEXT_MAX_CHARS = 95
QUESTION_TEXT_FONT_NAME = "Helvetica"
QUESTION_TEXT_FONT_SIZE = 8
QUESTION_TITLE_GAP = 5 * mm
QUESTION_TEXT_LINE_GAP = 4 * mm
QUESTION_TEXT_BOTTOM_GAP = 2 * mm
# Mantido para compatibilidade com imports antigos; o cálculo atual usa o texto real.
QUESTION_BLOCK_OVERHEAD = QUESTION_TITLE_GAP + (2 * QUESTION_TEXT_LINE_GAP) + QUESTION_TEXT_BOTTOM_GAP
DEFAULT_RESPONSE_LINES = 5
PRACTICAL_RESPONSE_LINES = 2


@dataclass
class FiducialBox:
    x_pt: float
    y_pt: float
    w_pt: float
    h_pt: float


@dataclass
class AnswerBoxPlacement:
    question_number: int
    """Retângulo da área cinza de resposta (ReportLab rect)."""
    x_pt: float
    y_bottom_pt: float
    width_pt: float
    height_pt: float


@dataclass
class ManifestPage:
    physical_index: int
    exam_id: str
    student_id: str
    page_in_student: int
    total_pages_for_student: int
    boxes: list[AnswerBoxPlacement] = field(default_factory=list)
    fiducials: list[FiducialBox] = field(default_factory=list)


def fiducials_for_page(
    width_pt: float,
    height_pt: float,
    question_area_top_pt: float | None = None,
) -> list[FiducialBox]:
    """Marcadores laterais delimitando a área útil das questões."""
    s = float(FIDUCIAL_MM)
    m = float(MARGIN)
    gap = float(FIDUCIAL_OUTER_GAP)
    left_x = max(0.0, m - s - gap)
    right_x = min(width_pt - s, width_pt - m + gap)
    top_y = (
        height_pt - m - s
        if question_area_top_pt is None
        else float(question_area_top_pt) - s
    )
    top_y = min(top_y, height_pt - m - s)
    top_y = max(top_y, m + (2 * s))
    return [
        FiducialBox(x_pt=left_x, y_pt=m, w_pt=s, h_pt=s),
        FiducialBox(x_pt=right_x, y_pt=m, w_pt=s, h_pt=s),
        FiducialBox(x_pt=left_x, y_pt=top_y, w_pt=s, h_pt=s),
        FiducialBox(x_pt=right_x, y_pt=top_y, w_pt=s, h_pt=s),
    ]


def wrap_question_text(
    text: str,
    max_width_pt: float | None = None,
    *,
    font_name: str = QUESTION_TEXT_FONT_NAME,
    font_size: float = QUESTION_TEXT_FONT_SIZE,
    max_chars: int = QUESTION_TEXT_MAX_CHARS,
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""

    if max_width_pt is not None:
        for word in words:
            candidate = f"{cur} {word}".strip()
            if cur and stringWidth(candidate, font_name, font_size) > max_width_pt:
                lines.append(cur)
                cur = word
            else:
                cur = candidate
        if cur:
            lines.append(cur)
        return lines

    for word in words:
        if len(cur) + len(word) + 1 > max_chars:
            if cur:
                lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines


def question_block_height(
    text: str,
    answer_area_h: float,
    spacing: float,
    question_text_width: float | None = None,
) -> float:
    """Altura real ocupada por uma questão antes de avançar para a próxima."""
    text_lines = wrap_question_text(text, question_text_width)
    return (
        QUESTION_TITLE_GAP
        + (len(text_lines) * QUESTION_TEXT_LINE_GAP)
        + QUESTION_TEXT_BOTTOM_GAP
        + answer_area_h
        + spacing
    )


def compute_answer_sheet_pages(
    exam_id: UUID,
    questions: list[Any],  # QuestionSlot-like: number, text, max_score
    student_id: UUID,
    *,
    logo_bottom_y_after: float | None = None,
    response_lines: int = DEFAULT_RESPONSE_LINES,
    compact_header: bool = True,
) -> tuple[list[ManifestPage], int]:
    """
    Simula a paginação de `_draw_sheet` e retorna páginas com boxes de resposta.

    `logo_bottom_y_after`: após desenhar a logo, equivale a `logo_y - 6*mm` no gerador.
    Se None, folha sem logo (`y = h - margin - PAGE_TOP_CONTENT_INSET` antes do cabeçalho).
    """
    w, h = A4
    margin = MARGIN
    usable_w = w - 2 * margin
    top_inset = PAGE_TOP_CONTENT_INSET
    cont_gap = CONTINUATION_GAP_BELOW_HEADER

    normalized_response_lines = max(1, response_lines)
    response_line_gap = 5 * mm
    first_response_line_offset = 10 * mm
    response_bottom_padding = 3 * mm
    answer_area_h = (
        first_response_line_offset
        + (normalized_response_lines - 1) * response_line_gap
        + response_bottom_padding
    )
    spacing = 4 * mm

    if logo_bottom_y_after is not None:
        y = logo_bottom_y_after
    else:
        y = h - margin - top_inset

    # --- Cabeçalho (espelho exato de _draw_sheet) ---
    if compact_header:
        title_gap = 6 * mm
        subtitle_gap = 8 * mm
        box_h = 18 * mm
        box_bottom_gap = 5 * mm
        divider_gap = 4 * mm
    else:
        title_gap = 8 * mm
        subtitle_gap = 12 * mm
        box_h = 22 * mm
        box_bottom_gap = 8 * mm
        divider_gap = 6 * mm

    y -= title_gap
    y -= subtitle_gap
    y -= box_h + box_bottom_gap

    current_question_area_top_y = y
    y -= divider_gap

    pages: list[ManifestPage] = []

    page_in_student = 0

    def new_manifest_page() -> ManifestPage:
        nonlocal page_in_student
        page_in_student += 1
        return ManifestPage(
            physical_index=0,
            exam_id=str(exam_id),
            student_id=str(student_id),
            page_in_student=page_in_student,
            total_pages_for_student=0,
            fiducials=fiducials_for_page(w, h),
        )

    current = new_manifest_page()
    current.fiducials = fiducials_for_page(w, h, current_question_area_top_y)

    for q in questions:
        needed = question_block_height(q.text, answer_area_h, spacing, usable_w)
        if y - needed < margin:
            pages.append(current)
            y = h - margin - top_inset
            current = new_manifest_page()
            # Página de continuação: linha "(cont.)" em `y`, depois `y -= cont_gap` até o baseline de "Questão N"
            y -= cont_gap
            current.fiducials = fiducials_for_page(w, h, y + 6 * mm)

        # Baseline do "Questão N"; em seguida o PDF faz `y -= 5 mm`.
        y -= QUESTION_TITLE_GAP

        text_lines = wrap_question_text(q.text, usable_w)
        for _line in text_lines:
            y -= QUESTION_TEXT_LINE_GAP

        y -= QUESTION_TEXT_BOTTOM_GAP

        box_x = margin
        box_y_bottom = y - answer_area_h
        current.boxes.append(
            AnswerBoxPlacement(
                question_number=q.number,
                x_pt=box_x,
                y_bottom_pt=box_y_bottom,
                width_pt=usable_w,
                height_pt=answer_area_h,
            )
        )

        y -= answer_area_h + spacing

    pages.append(current)

    total_pages = len(pages)
    for p in pages:
        p.total_pages_for_student = total_pages

    return pages, total_pages


def manifest_to_jsonable(pages: list[ManifestPage]) -> dict[str, Any]:
    """Serializa o manifesto para gravar em Exam.layout_manifest_json."""

    def box_dict(b: AnswerBoxPlacement) -> dict[str, Any]:
        return {
            "question_number": b.question_number,
            "x_pt": b.x_pt,
            "y_bottom_pt": b.y_bottom_pt,
            "width_pt": b.width_pt,
            "height_pt": b.height_pt,
        }

    def fid_dict(f: FiducialBox) -> dict[str, Any]:
        return {"x_pt": f.x_pt, "y_pt": f.y_pt, "width_pt": f.w_pt, "height_pt": f.h_pt}

    return {
        "version": 1,
        "pages": [
            {
                "physical_index": p.physical_index,
                "exam_id": p.exam_id,
                "student_id": p.student_id,
                "page_in_student": p.page_in_student,
                "total_pages_for_student": p.total_pages_for_student,
                "boxes": [box_dict(b) for b in p.boxes],
                "fiducials": [fid_dict(f) for f in p.fiducials],
            }
            for p in pages
        ],
    }


def dumps_manifest(pages: list[ManifestPage]) -> str:
    return json.dumps(manifest_to_jsonable(pages), ensure_ascii=False)


def pdf_answer_box_to_pil_pixels(
    x_pt: float,
    y_bottom_pt: float,
    width_pt: float,
    height_pt: float,
    page_height_pt: float,
    dpi: float,
) -> tuple[int, int, int, int]:
    """
    Converte retângulo PDF (origem inferior esquerda) para crop PIL (origem superior esquerda).

    Retorna (left, upper, right, lower) em pixels.
    """
    scale = dpi / 72.0
    y_top_pt = y_bottom_pt + height_pt
    left = int(x_pt * scale)
    right = int((x_pt + width_pt) * scale)
    upper = int((page_height_pt - y_top_pt) * scale)
    lower = int((page_height_pt - y_bottom_pt) * scale)
    return left, upper, right, lower


def merge_student_manifest_pages(all_pages: list[ManifestPage]) -> list[ManifestPage]:
    """Renumera physical_index globalmente após concatenar páginas de vários alunos."""
    out: list[ManifestPage] = []
    for i, p in enumerate(all_pages):
        np = ManifestPage(
            physical_index=i,
            exam_id=p.exam_id,
            student_id=p.student_id,
            page_in_student=p.page_in_student,
            total_pages_for_student=p.total_pages_for_student,
            boxes=list(p.boxes),
            fiducials=list(p.fiducials),
        )
        out.append(np)
    return out
