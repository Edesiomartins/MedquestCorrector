from __future__ import annotations

import pymupdf as fitz


def add_student_identity_header(raw_pdf: bytes) -> bytes:
    """Adiciona Nome/Matrícula a cada página do PDF canônico sem mover o layout.

    A geometria das questões permanece intacta: o texto é inserido somente na
    margem superior já reservada pelo normalizador de DOCX.
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
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
