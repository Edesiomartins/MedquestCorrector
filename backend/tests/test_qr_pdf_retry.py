"""QR ilegível na rasterização de trabalho ainda pode ser lido no PDF."""

import io
from pathlib import Path

import pymupdf as fitz
import qrcode
from PIL import Image

from app.services import visual_exam_pipeline as vep
from app.services.vision.qr_decode import decode_sheet_qr_from_pdf


def _pdf_with_qr(path: Path, payload: str) -> None:
    image = qrcode.make(payload)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(fitz.Rect(36, 36, 150, 150), stream=buffer.getvalue())
    doc.save(str(path))
    doc.close()


def test_pdf_retry_reads_the_qr_when_the_page_image_does_not(tmp_path, monkeypatch):
    pdf = tmp_path / "folha.pdf"
    _pdf_with_qr(pdf, "MQPC|exam-1|student-09|1|1")
    page_image = tmp_path / "page.png"
    Image.new("RGB", (8, 8), "white").save(page_image)

    real = vep.decode_sheet_qr

    def fail_the_work_image(image):
        if image.size[0] < 50:
            return None
        return real(image)

    monkeypatch.setattr(vep, "decode_sheet_qr", fail_the_work_image)

    class Manifest:
        student_id = "student-10"

    identity = vep._identify_page(
        page_image=str(page_image),
        manifest_page=Manifest(),
        physical_page_number=2,
        options={"students_by_id": {}, "read_header": False},
        crop_dir=tmp_path,
        warnings=[],
        pdf_path=str(pdf),
        page_index=0,
    )

    assert identity["qr_student_id"] == "student-09"
    assert identity["student_id"] == "student-09"
    assert identity["student_id"] != "student-10"


def test_decode_sheet_qr_from_pdf_reads_payload(tmp_path):
    pdf = tmp_path / "folha.pdf"
    _pdf_with_qr(pdf, "MQPC|exam-1|student-09|1|1")
    payload = decode_sheet_qr_from_pdf(str(pdf), 0, dpi=220)
    assert payload is not None
    assert payload.student_id == "student-09"
