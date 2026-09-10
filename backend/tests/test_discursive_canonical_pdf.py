from io import BytesIO

import pymupdf as fitz
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.discursive_import.canonical_pdf import add_student_identity_header


def _two_page_pdf() -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.drawString(42, 700, "QUESTÃO 38")
    c.showPage()
    c.drawString(42, 700, "QUESTÃO 39")
    c.save()
    return buf.getvalue()


def test_identity_header_is_added_to_every_page_without_changing_page_size():
    raw = _two_page_pdf()
    stamped = add_student_identity_header(raw)
    doc = fitz.open(stream=stamped, filetype="pdf")
    try:
        assert doc.page_count == 2
        for page in doc:
            text = page.get_text()
            assert "Nome:" in text
            assert "Matrícula:" in text
            assert round(page.rect.width) == round(A4[0])
            assert round(page.rect.height) == round(A4[1])
    finally:
        doc.close()
