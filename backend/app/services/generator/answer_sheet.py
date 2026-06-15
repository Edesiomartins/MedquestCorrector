"""Gera folhas-resposta em PDF personalizadas por aluno usando ReportLab."""

import io
from dataclasses import dataclass
from uuid import UUID

import qrcode
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app.services.generator.sheet_layout import (
    CONTINUATION_GAP_BELOW_HEADER,
    DEFAULT_RESPONSE_LINES,
    HEADER_DIVIDER_BELOW_GAP_COMPACT,
    HEADER_DIVIDER_BELOW_GAP_FULL,
    HEADER_STUDENT_BOX_H_COMPACT,
    HEADER_STUDENT_BOX_H_FULL,
    PAGE_TOP_CONTENT_INSET,
    QR_TOP_PADDING_COMPACT,
    QR_TOP_PADDING_FULL,
    QUESTION_TEXT_FONT_NAME,
    QUESTION_TEXT_FONT_SIZE,
    QUESTION_TEXT_BOTTOM_GAP,
    QUESTION_TEXT_LINE_GAP,
    QUESTION_TITLE_GAP,
    QR_SIZE,
    compute_answer_sheet_pages,
    fiducials_for_page,
    merge_student_manifest_pages,
    manifest_to_jsonable,
    question_block_height,
    wrap_question_text,
)
from app.services.vision.qr_decode import format_qr_payload


@dataclass
class StudentInfo:
    name: str
    registration_number: str
    curso: str
    turma: str


@dataclass
class QuestionSlot:
    number: int
    text: str
    max_score: float


@dataclass
class LogoSpec:
    image: ImageReader
    width: float
    height: float


def generate_answer_sheets(
    exam_id: UUID,
    exam_name: str,
    questions: list[QuestionSlot],
    students: list[tuple[UUID, StudentInfo]],
    logo_bytes: bytes | None = None,
    response_lines: int = DEFAULT_RESPONSE_LINES,
    compact_header: bool = True,
    *,
    question_spacing: float | None = None,
    question_title_gap: float | None = None,
    question_text_bottom_gap: float | None = None,
    first_response_line_offset: float | None = None,
    response_bottom_padding: float | None = None,
    logo_max_height: float | None = None,
    logo_bottom_gap: float | None = None,
    header_title_gap: float | None = None,
    header_subtitle_gap: float | None = None,
    header_box_h: float | None = None,
    header_box_bottom_gap: float | None = None,
    header_divider_below_gap: float | None = None,
    header_title_font_size: float | None = None,
    header_subtitle_font_size: float | None = None,
    inline_question_prompt: bool = False,
) -> tuple[bytes, dict]:
    """
    Gera PDF com folhas-resposta e o manifesto de layout (coordenadas dos boxes).

    Retorna `(pdf_bytes, manifest_dict)`.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    logo = _load_logo(logo_bytes)

    spacing_eff = question_spacing if question_spacing is not None else 4 * mm
    title_gap_eff = question_title_gap if question_title_gap is not None else QUESTION_TITLE_GAP
    text_bottom_eff = (
        question_text_bottom_gap if question_text_bottom_gap is not None else QUESTION_TEXT_BOTTOM_GAP
    )
    first_line_eff = (
        first_response_line_offset if first_response_line_offset is not None else 10 * mm
    )
    resp_pad_eff = response_bottom_padding if response_bottom_padding is not None else 3 * mm
    logo_max_h_eff = logo_max_height if logo_max_height is not None else 24 * mm
    logo_bottom_gap_eff = logo_bottom_gap if logo_bottom_gap is not None else 6 * mm

    all_manifest_pages = []

    for student_id, student in students:
        logo_y_after: float | None = None
        if logo:
            margin = 2 * cm
            y = height - margin - PAGE_TOP_CONTENT_INSET
            max_logo_w = width - (2 * margin)
            max_logo_h = logo_max_h_eff
            scale = min(max_logo_w / logo.width, max_logo_h / logo.height)
            draw_h = logo.height * scale
            logo_y = y - draw_h
            logo_y_after = logo_y - logo_bottom_gap_eff

        pages_sim, _total = compute_answer_sheet_pages(
            exam_id,
            questions,
            student_id,
            logo_bottom_y_after=logo_y_after,
            response_lines=response_lines,
            compact_header=compact_header,
            question_spacing=question_spacing,
            question_title_gap=question_title_gap,
            question_text_bottom_gap=question_text_bottom_gap,
            first_response_line_offset=first_response_line_offset,
            response_bottom_padding=response_bottom_padding,
            logo_max_height=logo_max_height,
            logo_bottom_gap=logo_bottom_gap,
            header_title_gap=header_title_gap,
            header_subtitle_gap=header_subtitle_gap,
            header_box_h=header_box_h,
            header_box_bottom_gap=header_box_bottom_gap,
            header_divider_below_gap=header_divider_below_gap,
            header_title_font_size=header_title_font_size,
            header_subtitle_font_size=header_subtitle_font_size,
            inline_question_prompt=inline_question_prompt,
        )
        all_manifest_pages.extend(pages_sim)

        _draw_sheet(
            c,
            width,
            height,
            exam_name,
            questions,
            student,
            logo,
            exam_id=exam_id,
            student_id=student_id,
            total_pages_for_student=len(pages_sim),
            response_lines=response_lines,
            compact_header=compact_header,
            question_spacing=spacing_eff,
            question_title_gap=title_gap_eff,
            question_text_bottom_gap=text_bottom_eff,
            first_response_line_offset=first_line_eff,
            response_bottom_padding=resp_pad_eff,
            logo_max_height=logo_max_h_eff,
            logo_bottom_gap=logo_bottom_gap_eff,
            header_title_gap=header_title_gap,
            header_subtitle_gap=header_subtitle_gap,
            header_box_h=header_box_h,
            header_box_bottom_gap=header_box_bottom_gap,
            header_divider_below_gap=header_divider_below_gap,
            header_title_font_size=header_title_font_size,
            header_subtitle_font_size=header_subtitle_font_size,
            inline_question_prompt=inline_question_prompt,
        )
        c.showPage()

    merged = merge_student_manifest_pages(all_manifest_pages)
    manifest_dict = manifest_to_jsonable(merged)

    c.save()
    buf.seek(0)
    return buf.read(), manifest_dict


def _draw_qr(c: canvas.Canvas, payload: str, x: float, y: float, size: float) -> None:
    qr = qrcode.QRCode(version=None, box_size=2, border=0)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    c.drawImage(ImageReader(bio), x, y, width=size, height=size, mask="auto")


def _draw_fiducials(c: canvas.Canvas, w: float, h: float, question_area_top_y: float) -> None:
    c.saveState()
    c.setStrokeColor(colors.black)
    c.setFillColor(colors.black)
    for f in fiducials_for_page(w, h, question_area_top_y):
        c.rect(f.x_pt, f.y_pt, f.w_pt, f.h_pt, stroke=0, fill=1)
    c.restoreState()


def _draw_justified_line(
    c: canvas.Canvas,
    text: str,
    x: float,
    y: float,
    width: float,
    *,
    font_name: str,
    font_size: float,
) -> None:
    words = text.split()
    if len(words) <= 1:
        c.drawString(x, y, text)
        return

    words_width = sum(c.stringWidth(word, font_name, font_size) for word in words)
    gap = (width - words_width) / (len(words) - 1)
    if gap <= 0:
        c.drawString(x, y, text)
        return

    cursor_x = x
    for word in words[:-1]:
        c.drawString(cursor_x, y, word)
        cursor_x += c.stringWidth(word, font_name, font_size) + gap
    c.drawString(cursor_x, y, words[-1])


def _draw_question_text(c: canvas.Canvas, lines: list[str], x: float, y: float, width: float) -> float:
    c.setFont(QUESTION_TEXT_FONT_NAME, QUESTION_TEXT_FONT_SIZE)
    for idx, line in enumerate(lines):
        is_last_line = idx == len(lines) - 1
        if is_last_line:
            c.drawString(x, y, line)
        else:
            _draw_justified_line(
                c,
                line,
                x,
                y,
                width,
                font_name=QUESTION_TEXT_FONT_NAME,
                font_size=QUESTION_TEXT_FONT_SIZE,
            )
        y -= QUESTION_TEXT_LINE_GAP
    return y


def _draw_sheet(
    c: canvas.Canvas,
    w: float,
    h: float,
    exam_name: str,
    questions: list[QuestionSlot],
    student: StudentInfo,
    logo: LogoSpec | None,
    *,
    exam_id: UUID,
    student_id: UUID,
    total_pages_for_student: int,
    response_lines: int = DEFAULT_RESPONSE_LINES,
    compact_header: bool = True,
    question_spacing: float,
    question_title_gap: float,
    question_text_bottom_gap: float,
    first_response_line_offset: float,
    response_bottom_padding: float,
    logo_max_height: float,
    logo_bottom_gap: float,
    header_title_gap: float | None,
    header_subtitle_gap: float | None,
    header_box_h: float | None,
    header_box_bottom_gap: float | None,
    header_divider_below_gap: float | None,
    header_title_font_size: float | None,
    header_subtitle_font_size: float | None,
    inline_question_prompt: bool,
):
    margin = 2 * cm
    page_in_student = 0

    def begin_physical_page() -> str:
        nonlocal page_in_student
        page_in_student += 1
        return format_qr_payload(
            str(exam_id),
            str(student_id),
            page_in_student,
            total_pages_for_student,
        )

    current_qr_payload = begin_physical_page()

    # Primeira linha de texto abaixo do topo; os marcadores entram abaixo do cabeçalho.
    y = h - margin - PAGE_TOP_CONTENT_INSET

    if logo:
        max_logo_w = w - (2 * margin)
        max_logo_h = logo_max_height
        scale = min(max_logo_w / logo.width, max_logo_h / logo.height)
        draw_w = logo.width * scale
        draw_h = logo.height * scale
        logo_x = (w - draw_w) / 2
        logo_y = y - draw_h
        c.drawImage(
            logo.image,
            logo_x,
            logo_y,
            width=draw_w,
            height=draw_h,
            preserveAspectRatio=True,
            mask="auto",
        )
        y = logo_y - logo_bottom_gap

    c.setFont(
        "Helvetica-Bold",
        header_title_font_size
        if header_title_font_size is not None
        else (14 if compact_header else 16),
    )
    c.drawCentredString(w / 2, y, exam_name)
    y -= header_title_gap if header_title_gap is not None else (6 if compact_header else 8) * mm

    c.setFont(
        "Helvetica",
        header_subtitle_font_size
        if header_subtitle_font_size is not None
        else 9,
    )
    c.drawCentredString(w / 2, y, "FOLHA DE RESPOSTAS — Preencha com letra legível")
    y -= header_subtitle_gap if header_subtitle_gap is not None else (8 if compact_header else 12) * mm

    c.setFont("Helvetica-Bold", 10)
    if compact_header:
        box_h = float(header_box_h if header_box_h is not None else HEADER_STUDENT_BOX_H_COMPACT)
        qr_top_pad = QR_TOP_PADDING_COMPACT
        divider_below_gap = (
            header_divider_below_gap
            if header_divider_below_gap is not None
            else HEADER_DIVIDER_BELOW_GAP_COMPACT
        )
        box_bottom_gap = header_box_bottom_gap if header_box_bottom_gap is not None else 5 * mm
    else:
        box_h = float(header_box_h if header_box_h is not None else HEADER_STUDENT_BOX_H_FULL)
        qr_top_pad = QR_TOP_PADDING_FULL
        divider_below_gap = (
            header_divider_below_gap
            if header_divider_below_gap is not None
            else HEADER_DIVIDER_BELOW_GAP_FULL
        )
        box_bottom_gap = header_box_bottom_gap if header_box_bottom_gap is not None else 8 * mm

    c.setStrokeColor(colors.grey)
    c.setLineWidth(0.5)
    # Retângulo com altura exata da caixa (sem +2 mm) para o QR caber dentro dos limites.
    c.rect(margin, y - box_h, w - 2 * margin, box_h, stroke=1, fill=0)

    left = margin + 4 * mm
    qr_size = QR_SIZE
    qr_x = left
    qr_y = y - qr_top_pad - qr_size
    _draw_qr(c, current_qr_payload, qr_x, qr_y, qr_size)

    text_left = qr_x + qr_size + 6 * mm
    c.setFont("Helvetica", 9)
    if compact_header:
        c.drawString(text_left, y - 5 * mm, f"Nome: {student.name}")
        c.drawString(text_left, y - 10 * mm, f"Turma: {student.turma}")
        c.drawString(text_left, y - 15 * mm, f"Matrícula: {student.registration_number}")
        c.drawString(text_left + 70 * mm, y - 15 * mm, f"Curso: {student.curso}")
    else:
        c.drawString(text_left, y - 5 * mm, f"Nome: {student.name}")
        c.drawString(text_left, y - 11 * mm, f"Turma: {student.turma}")
        c.drawString(text_left, y - 17 * mm, f"Matrícula: {student.registration_number}")
        c.drawString(text_left + 70 * mm, y - 17 * mm, f"Curso: {student.curso}")

    y -= box_h + box_bottom_gap

    c.setStrokeColor(colors.black)
    c.setLineWidth(0.3)
    c.line(margin, y, w - margin, y)
    _draw_fiducials(c, w, h, y)
    y -= divider_below_gap

    usable_w = w - 2 * margin
    effective_response_lines = max(1, response_lines)
    response_line_gap = 5 * mm
    response_label_offset = 4 * mm
    answer_area_h = (
        first_response_line_offset
        + (effective_response_lines - 1) * response_line_gap
        + response_bottom_padding
    )
    spacing = question_spacing

    for q in questions:
        needed = question_block_height(
            q.text,
            answer_area_h,
            spacing,
            usable_w,
            title_gap=question_title_gap,
            text_bottom_gap=question_text_bottom_gap,
            question_prefix=f"Questão {q.number} - " if inline_question_prompt else "",
        )
        if y - needed < margin:
            c.showPage()
            current_qr_payload = begin_physical_page()
            y = h - margin - PAGE_TOP_CONTENT_INSET

            _draw_qr(c, current_qr_payload, margin, y - QR_SIZE, QR_SIZE)
            c.setFont("Helvetica-Bold", 9)
            c.drawString(
                margin + QR_SIZE + 6 * mm,
                y - 5 * mm,
                f"{exam_name} — {student.name} (cont.)",
            )
            y -= CONTINUATION_GAP_BELOW_HEADER
            _draw_fiducials(c, w, h, y + 6 * mm)

        if inline_question_prompt:
            text_lines = wrap_question_text(f"Questão {q.number} - {q.text}", usable_w)
        else:
            c.setFont("Helvetica-Bold", 10)
            c.drawString(margin, y, f"Questão {q.number}")

            c.setFont("Helvetica", 8)
            c.drawRightString(w - margin, y, f"(vale {q.max_score} pts)")

            y -= question_title_gap

            text_lines = wrap_question_text(q.text, usable_w)
        y = _draw_question_text(c, text_lines, margin, y, usable_w)

        y -= question_text_bottom_gap

        c.setStrokeColor(colors.Color(0.8, 0.8, 0.8))
        c.setFillColor(colors.Color(0.97, 0.97, 0.97))
        c.setLineWidth(0.4)
        c.rect(margin, y - answer_area_h, usable_w, answer_area_h, stroke=1, fill=1)

        c.setFillColor(colors.Color(0.7, 0.7, 0.7))
        c.setFont("Helvetica-Oblique", 7)
        c.drawString(margin + 3 * mm, y - response_label_offset, "Resposta:")

        c.setStrokeColor(colors.Color(0.85, 0.85, 0.85))
        for i in range(effective_response_lines):
            line_y = y - first_response_line_offset - (i * response_line_gap)
            c.line(margin + 2 * mm, line_y, w - margin - 2 * mm, line_y)

        c.setFillColor(colors.black)
        c.setStrokeColor(colors.black)

        y -= answer_area_h + spacing

    c.setFont("Helvetica", 7)
    c.setFillColor(colors.grey)
    c.drawCentredString(w / 2, margin - 6 * mm, "medquestcorrector — Folha gerada automaticamente")
    c.setFillColor(colors.black)
def _load_logo(logo_bytes: bytes | None) -> LogoSpec | None:
    if not logo_bytes:
        return None

    try:
        image = ImageReader(io.BytesIO(logo_bytes))
        img_w, img_h = image.getSize()
    except Exception as exc:
        raise ValueError("Não foi possível ler o arquivo de logo enviado.") from exc

    if img_w <= 0 or img_h <= 0:
        raise ValueError("A imagem da logo é inválida.")
    return LogoSpec(image=image, width=float(img_w), height=float(img_h))


def practical_answer_sheet_options() -> dict[str, int | bool | float]:
    """
    Parâmetros base da folha prática: 1 linha de resposta e cabeçalho compacto.

    Os valores de caixa/espaçamento aqui são apenas o fallback; quando o
    autoajuste está ativo (`autofit_practical_options`), eles são recalculados
    para encher cada página com 10–12 questões.
    """
    return {
        "response_lines": 1,
        "compact_header": True,
        # Espaço ACIMA do enunciado (entre a caixa de resposta anterior e o
        # próximo enunciado). Deve ser grande o suficiente para separar
        # visualmente o enunciado da caixa anterior e colá-lo na sua própria
        # caixa de resposta.
        "question_spacing": 6 * mm,
        "question_title_gap": 0,
        "question_text_bottom_gap": 0.5 * mm,
        # Caixa de resposta (fallback sem autoajuste).
        "first_response_line_offset": 8 * mm,
        "response_bottom_padding": 2 * mm,
        # Logo do cabeçalho um pouco maior.
        "logo_max_height": 14 * mm,
        "logo_bottom_gap": 5 * mm,
        "header_title_gap": 4 * mm,
        "header_subtitle_gap": 4 * mm,
        "header_box_h": 22 * mm,
        "header_box_bottom_gap": 2 * mm,
        "header_divider_below_gap": 3 * mm,
        "header_title_font_size": 12,
        "header_subtitle_font_size": 8,
        "inline_question_prompt": True,
    }
