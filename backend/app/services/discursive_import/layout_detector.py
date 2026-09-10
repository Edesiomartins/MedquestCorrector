from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import pymupdf as fitz
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

QUESTION_RE = re.compile(r"^\s*(?:questao|q)\s*0*(\d{1,4})\b", re.IGNORECASE)
LINE_CHAR_RE = re.compile(r"^[\s_\.\-–—]+$")
MIN_VECTOR_LINE_WIDTH_FRAC = 0.32
PREVIEW_DPI = 110


@dataclass(frozen=True)
class TextLine:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


def _ascii_fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _question_number(text: str) -> int | None:
    match = QUESTION_RE.search(_ascii_fold(text))
    if not match:
        return None
    value = int(match.group(1))
    return value if value > 0 else None


def _text_lines(page: fitz.Page) -> list[TextLine]:
    words = page.get_text("words")
    grouped: dict[tuple[int, int], list[tuple]] = {}
    for word in words:
        key = (int(word[5]), int(word[6]))
        grouped.setdefault(key, []).append(word)

    lines: list[TextLine] = []
    for items in grouped.values():
        items = sorted(items, key=lambda w: (float(w[1]), float(w[0]), int(w[7])))
        text = " ".join(str(w[4]) for w in items).strip()
        if not text:
            continue
        lines.append(
            TextLine(
                text=text,
                x0=min(float(w[0]) for w in items),
                y0=min(float(w[1]) for w in items),
                x1=max(float(w[2]) for w in items),
                y1=max(float(w[3]) for w in items),
            )
        )
    return sorted(lines, key=lambda line: (line.y0, line.x0))


def _looks_like_text_answer_line(line: TextLine) -> bool:
    compact = line.text.replace(" ", "")
    if len(compact) < 10:
        return False
    return bool(LINE_CHAR_RE.fullmatch(compact)) and sum(ch in "_.-–—" for ch in compact) >= 10


def _vector_answer_lines(page: fitz.Page) -> list[TextLine]:
    candidates: list[TextLine] = []
    min_width = float(page.rect.width) * MIN_VECTOR_LINE_WIDTH_FRAC
    for drawing in page.get_drawings():
        for item in drawing.get("items") or []:
            if not item or item[0] != "l":
                continue
            p1, p2 = item[1], item[2]
            x0, y0 = float(p1.x), float(p1.y)
            x1, y1 = float(p2.x), float(p2.y)
            if abs(y1 - y0) > 2.0:
                continue
            left, right = sorted((x0, x1))
            if right - left < min_width:
                continue
            y = (y0 + y1) / 2.0
            candidates.append(TextLine(text="", x0=left, y0=y - 1.0, x1=right, y1=y + 1.0))
    return candidates


def _page_preview_data_url(page: fitz.Page) -> str:
    zoom = PREVIEW_DPI / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    encoded = base64.b64encode(pix.tobytes("png")).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _clean_question_text(label_line: TextLine, content_lines: list[TextLine]) -> str:
    label_folded = _ascii_fold(label_line.text)
    match = QUESTION_RE.search(label_folded)
    inline_tail = ""
    if match:
        inline_tail = label_line.text[match.end():].strip(" :-–—")
        compact_tail = inline_tail.replace(" ", "")
        if compact_tail and LINE_CHAR_RE.fullmatch(compact_tail):
            inline_tail = ""
    parts = [inline_tail] if inline_tail else []
    for line in content_lines:
        if _looks_like_text_answer_line(line):
            continue
        if _question_number(line.text) is not None:
            break
        parts.append(line.text.strip())
    return " ".join(part for part in parts if part).strip()


def _answer_box_for_section(
    page: fitz.Page,
    label_line: TextLine,
    section_lines: list[TextLine],
    vector_lines: list[TextLine],
    section_bottom: float,
) -> tuple[float, float, float, float, float, str, list[TextLine]]:
    text_answer_lines = [line for line in section_lines if _looks_like_text_answer_line(line)]
    vector_answer_lines = [line for line in vector_lines if label_line.y1 < line.y0 < section_bottom]
    answer_lines = sorted([*text_answer_lines, *vector_answer_lines], key=lambda line: line.y0)

    if answer_lines:
        x0 = max(0.0, min(line.x0 for line in answer_lines) - 4.0)
        x1 = min(float(page.rect.width), max(line.x1 for line in answer_lines) + 4.0)
        y0 = max(label_line.y1 + 2.0, min(line.y0 for line in answer_lines) - 12.0)
        typical_gap = 20.0
        ys = sorted(line.y0 for line in answer_lines)
        if len(ys) > 1:
            diffs = [b - a for a, b in zip(ys, ys[1:]) if 6.0 <= b - a <= 50.0]
            if diffs:
                typical_gap = sum(diffs) / len(diffs)
        y1 = min(section_bottom - 2.0, max(line.y1 for line in answer_lines) + max(12.0, typical_gap * 0.65))
        return x0, y0, x1, y1, 0.95, "answer_lines", answer_lines

    content_lines = [
        line
        for line in section_lines
        if not _looks_like_text_answer_line(line) and _question_number(line.text) is None
    ]
    text_bottom = max((line.y1 for line in content_lines), default=label_line.y1)
    x0 = max(18.0, min((line.x0 for line in content_lines), default=36.0))
    x1 = min(float(page.rect.width) - 18.0, max((line.x1 for line in content_lines), default=float(page.rect.width) - 36.0))
    if x1 - x0 < float(page.rect.width) * 0.55:
        x0, x1 = 36.0, float(page.rect.width) - 36.0
    y0 = text_bottom + 8.0
    y1 = section_bottom - 8.0
    if y1 - y0 < 36.0:
        y0 = max(label_line.y1 + 8.0, section_bottom - 72.0)
    return x0, y0, x1, y1, 0.62, "blank_space", []


def detect_pdf_layout(raw_pdf: bytes) -> dict[str, Any]:
    if not raw_pdf:
        raise ValueError("PDF vazio.")
    doc = fitz.open(stream=raw_pdf, filetype="pdf")
    try:
        pages: list[dict[str, Any]] = []
        questions: list[dict[str, Any]] = []
        warnings: list[str] = []
        seen_numbers: set[int] = set()

        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            width, height = float(page.rect.width), float(page.rect.height)
            pages.append(
                {
                    "page_index": page_index,
                    "width_pt": width,
                    "height_pt": height,
                    "preview_data_url": _page_preview_data_url(page),
                }
            )

            lines = _text_lines(page)
            labels = [(idx, line, _question_number(line.text)) for idx, line in enumerate(lines)]
            labels = [(idx, line, number) for idx, line, number in labels if number is not None]
            vector_lines = _vector_answer_lines(page)

            for pos, (line_index, label, qnum) in enumerate(labels):
                assert qnum is not None
                next_label_y = labels[pos + 1][1].y0 if pos + 1 < len(labels) else height - 18.0
                section_lines = [
                    line
                    for line in lines[line_index + 1 :]
                    if line.y0 < next_label_y and _question_number(line.text) is None
                ]
                x0, y0, x1, y1, confidence, provenance, answer_lines = _answer_box_for_section(
                    page,
                    label,
                    section_lines,
                    vector_lines,
                    next_label_y,
                )
                question_content_lines = [line for line in section_lines if line.y0 < y0]
                question_text = _clean_question_text(label, question_content_lines)
                if not question_text:
                    warnings.append(f"Q{qnum} na página {page_index + 1}: enunciado não pôde ser extraído.")

                if qnum in seen_numbers:
                    warnings.append(f"Número de questão duplicado detectado: Q{qnum}.")
                seen_numbers.add(qnum)

                if y1 <= y0 or x1 <= x0:
                    warnings.append(f"Q{qnum} na página {page_index + 1}: área de resposta precisa de ajuste manual.")
                    y0 = max(label.y1 + 8.0, min(height - 72.0, y0))
                    y1 = min(height - 18.0, max(y0 + 54.0, y1))
                    x0, x1 = 36.0, width - 36.0
                    confidence = min(confidence, 0.35)

                questions.append(
                    {
                        "question_number": int(qnum),
                        "page_index": page_index,
                        "question_text": question_text,
                        "x_pt": round(x0, 3),
                        "y_bottom_pt": round(height - y1, 3),
                        "width_pt": round(x1 - x0, 3),
                        "height_pt": round(y1 - y0, 3),
                        "confidence": round(confidence, 3),
                        "provenance": provenance,
                        "answer_line_count": len(answer_lines),
                    }
                )

        if not questions:
            warnings.append("Nenhuma questão discursiva foi detectada automaticamente.")
        return {
            "source_format": "pdf",
            "pages": pages,
            "questions": questions,
            "warnings": warnings,
        }
    finally:
        doc.close()


def _wrap_text(text: str, font_name: str, font_size: float, max_width: float) -> list[str]:
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _extract_docx_questions(raw_docx: bytes) -> list[dict[str, Any]]:
    doc = Document(BytesIO(raw_docx))
    paragraphs = [p.text.strip() for p in doc.paragraphs]
    questions: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_answer_lines = False
    for text in paragraphs:
        qnum = _question_number(text)
        if qnum is not None:
            if current:
                questions.append(current)
            folded = _ascii_fold(text)
            match = QUESTION_RE.search(folded)
            tail = text[match.end():].strip(" :-–—") if match else ""
            compact_tail = tail.replace(" ", "")
            if compact_tail and LINE_CHAR_RE.fullmatch(compact_tail):
                tail = ""
            current = {"question_number": qnum, "text_parts": [tail] if tail else [], "answer_lines": 0}
            in_answer_lines = False
            continue
        if current is None:
            continue
        compact = text.replace(" ", "")
        if compact and LINE_CHAR_RE.fullmatch(compact) and sum(ch in "_.-–—" for ch in compact) >= 10:
            current["answer_lines"] += 1
            in_answer_lines = True
            continue
        if text and not in_answer_lines:
            current["text_parts"].append(text)
    if current:
        questions.append(current)
    return questions


def normalize_docx_to_pdf(raw_docx: bytes) -> dict[str, Any]:
    if not raw_docx:
        raise ValueError("DOCX vazio.")
    extracted = _extract_docx_questions(raw_docx)
    if not extracted:
        raise ValueError("Nenhuma questão foi encontrada no DOCX.")

    out = BytesIO()
    c = canvas.Canvas(out, pagesize=A4)
    page_width, page_height = A4
    left = 42.0
    right = page_width - 42.0
    max_text_width = right - left

    for idx, item in enumerate(extracted):
        c.setFont("Helvetica-Bold", 12)
        y = page_height - 58.0
        c.drawString(left, y, f"QUESTÃO {item['question_number']}")
        y -= 24.0
        c.setFont("Helvetica", 10)
        text = " ".join(part for part in item["text_parts"] if part).strip()
        for line in _wrap_text(text, "Helvetica", 10.0, max_text_width):
            c.drawString(left, y, line)
            y -= 14.0
        y -= 14.0
        line_count = max(4, min(int(item.get("answer_lines") or 6), 12))
        spacing = 24.0
        available = max(80.0, y - 42.0)
        if line_count * spacing > available:
            spacing = max(16.0, available / line_count)
        for _ in range(line_count):
            c.line(left, y, right, y)
            y -= spacing
        if idx < len(extracted) - 1:
            c.showPage()
    c.save()
    canonical_pdf = out.getvalue()
    return {
        "canonical_pdf": canonical_pdf,
        "warnings": [
            "DOCX normalizado para PDF canônico. Para manter a geometria dos recortes, use este PDF como versão de impressão da prova."
        ],
    }


def validate_confirmed_layout(
    pages: list[dict[str, Any]], questions: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    page_map = {int(page.get("page_index", -1)): page for page in pages}
    seen: set[int] = set()

    for item in questions:
        try:
            qnum = int(item.get("question_number"))
        except (TypeError, ValueError):
            errors.append("Questão com número inválido.")
            continue
        if qnum <= 0:
            errors.append(f"Número de questão inválido: {qnum}.")
            continue
        if qnum in seen:
            errors.append(f"Número de questão duplicado: Q{qnum}.")
        seen.add(qnum)

        try:
            page_index = int(item.get("page_index"))
        except (TypeError, ValueError):
            errors.append(f"Q{qnum}: página inválida.")
            continue
        page = page_map.get(page_index)
        if page is None:
            errors.append(f"Q{qnum}: página {page_index + 1} não existe.")
            continue
        try:
            x = float(item.get("x_pt"))
            y = float(item.get("y_bottom_pt"))
            w = float(item.get("width_pt"))
            h = float(item.get("height_pt"))
            pw = float(page.get("width_pt"))
            ph = float(page.get("height_pt"))
        except (TypeError, ValueError):
            errors.append(f"Q{qnum}: geometria inválida.")
            continue
        if w <= 0 or h <= 0:
            errors.append(f"Q{qnum}: área de resposta vazia ou inválida.")
            continue
        tolerance = 0.5
        if x < -tolerance or y < -tolerance or x + w > pw + tolerance or y + h > ph + tolerance:
            errors.append(f"Q{qnum}: área de resposta fora dos limites da página.")
        if not str(item.get("question_text") or "").strip():
            warnings.append(f"Q{qnum}: enunciado vazio; complete antes de corrigir a prova.")

    for i, first in enumerate(questions):
        for second in questions[i + 1 :]:
            if int(first.get("page_index", -1)) != int(second.get("page_index", -1)):
                continue
            try:
                ax0 = float(first["x_pt"])
                ay0 = float(first["y_bottom_pt"])
                ax1 = ax0 + float(first["width_pt"])
                ay1 = ay0 + float(first["height_pt"])
                bx0 = float(second["x_pt"])
                by0 = float(second["y_bottom_pt"])
                bx1 = bx0 + float(second["width_pt"])
                by1 = by0 + float(second["height_pt"])
            except (KeyError, TypeError, ValueError):
                continue
            overlap_w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
            overlap_h = max(0.0, min(ay1, by1) - max(ay0, by0))
            overlap = overlap_w * overlap_h
            smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
            if smaller > 0 and overlap / smaller > 0.15:
                warnings.append(
                    f"Áreas de Q{first.get('question_number')} e Q{second.get('question_number')} se sobrepõem; revise antes de salvar."
                )
    return errors, warnings


def build_template_manifest(
    exam_id: str,
    pages: list[dict[str, Any]],
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    errors, warnings = validate_confirmed_layout(pages, questions)
    if errors:
        raise ValueError(" | ".join(errors))
    page_count = len(pages)
    manifest_pages: list[dict[str, Any]] = []
    for page in sorted(pages, key=lambda value: int(value["page_index"])):
        page_index = int(page["page_index"])
        boxes = []
        for item in sorted(
            (question for question in questions if int(question["page_index"]) == page_index),
            key=lambda value: int(value["question_number"]),
        ):
            boxes.append(
                {
                    "question_number": int(item["question_number"]),
                    "x_pt": float(item["x_pt"]),
                    "y_bottom_pt": float(item["y_bottom_pt"]),
                    "width_pt": float(item["width_pt"]),
                    "height_pt": float(item["height_pt"]),
                    "source_confidence": item.get("confidence"),
                    "detector_provenance": item.get("provenance") or "manual",
                }
            )
        manifest_pages.append(
            {
                "physical_index": page_index,
                "exam_id": str(exam_id),
                "student_id": "",
                "page_in_student": page_index + 1,
                "total_pages_for_student": page_count,
                "page_width_pt": float(page["width_pt"]),
                "page_height_pt": float(page["height_pt"]),
                "fiducials": [],
                "fiducial_style": "none",
                "boxes": boxes,
            }
        )
    return {
        "version": 3,
        "source": "external_discursive",
        "template_repeat": True,
        "template_page_count": page_count,
        "warnings": warnings,
        "pages": manifest_pages,
    }
