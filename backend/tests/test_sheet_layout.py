from uuid import uuid4

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

from app.services.generator.answer_sheet import (
    QuestionSlot,
    StudentInfo,
    generate_answer_sheets,
    practical_answer_sheet_options,
)
from app.services.generator.sheet_layout import (
    FIDUCIAL_OUTER_GAP,
    MARGIN,
    PAGE_TOP_CONTENT_INSET,
    pdf_answer_box_to_pil_pixels,
    QR_SIZE,
    compute_answer_sheet_pages,
)
from app.services.vision.pdf_parser import PDFParserService
from app.services.vision.qr_decode import decode_sheet_qr


def _questions(count: int) -> list[QuestionSlot]:
    return [
        QuestionSlot(
            number=i,
            text="Explique a estrutura anatomica indicada e sua relacao funcional.",
            max_score=1.0,
        )
        for i in range(1, count + 1)
    ]


def test_fiducials_bracket_question_area_not_header():
    pages, _ = compute_answer_sheet_pages(uuid4(), _questions(3), uuid4())
    page = pages[0]
    _, page_h = A4

    top_fiducial_y = max(f.y_pt for f in page.fiducials)
    first_box_top = page.boxes[0].y_bottom_pt + page.boxes[0].height_pt

    assert top_fiducial_y < page_h - MARGIN - QR_SIZE
    assert top_fiducial_y > first_box_top


def test_fiducials_stay_outside_answer_box_columns():
    pages, _ = compute_answer_sheet_pages(uuid4(), _questions(3), uuid4())
    page = pages[0]
    page_w, _ = A4
    left_fiducials = [f for f in page.fiducials if f.x_pt < page_w / 2]
    right_fiducials = [f for f in page.fiducials if f.x_pt > page_w / 2]

    epsilon = 0.01
    assert all(f.x_pt + f.w_pt <= MARGIN - FIDUCIAL_OUTER_GAP + epsilon for f in left_fiducials)
    assert all(f.x_pt >= page_w - MARGIN + FIDUCIAL_OUTER_GAP - epsilon for f in right_fiducials)


def test_continuation_pages_reserve_top_space_for_qr():
    pages, _ = compute_answer_sheet_pages(uuid4(), _questions(8), uuid4())

    assert len(pages) > 1
    first_box_top = pages[1].boxes[0].y_bottom_pt + pages[1].boxes[0].height_pt
    _, page_h = A4
    continuation_qr_bottom = page_h - MARGIN - PAGE_TOP_CONTENT_INSET - QR_SIZE

    assert first_box_top < continuation_qr_bottom - 5 * mm


def test_generated_sheet_qr_and_manifest_crops_match_new_layout():
    exam_id = uuid4()
    student_id = uuid4()
    pdf_bytes, manifest = generate_answer_sheets(
        exam_id=exam_id,
        exam_name="ANATOMIA II",
        questions=_questions(8),
        students=[
            (
                student_id,
                StudentInfo(
                    name="Aluno Teste",
                    registration_number="2026001",
                    curso="Medicina",
                    turma="Turma A",
                ),
            )
        ],
    )

    images = PDFParserService.extract_pages_as_images(pdf_bytes, dpi=200)
    assert len(images) == len(manifest["pages"]) == 2

    for page_idx, (image, page_manifest) in enumerate(zip(images, manifest["pages"], strict=True)):
        qr = decode_sheet_qr(image)
        assert qr is not None
        assert qr.exam_id == str(exam_id)
        assert qr.student_id == str(student_id)
        assert qr.page_in_student == page_idx + 1

        assert page_manifest["boxes"]
        for box in page_manifest["boxes"]:
            left, upper, right, lower = pdf_answer_box_to_pil_pixels(
                box["x_pt"],
                box["y_bottom_pt"],
                box["width_pt"],
                box["height_pt"],
                A4[1],
                200,
            )
            assert 0 <= left < right <= image.width
            assert 0 <= upper < lower <= image.height

            crop = image.crop((left, upper, right, lower))
            assert crop.width > 0
            assert crop.height > 0


def test_generated_sheet_does_not_truncate_long_question_text():
    final_phrase = "incluindo metabolismo oxidativo e resistencia a fadiga."
    long_question = (
        "Um maratonista e um velocista possuem composicoes musculares distintas. "
        "Diferencie as fibras musculares tipo I das fibras tipo II quanto a velocidade "
        "de contracao, vascularizacao, quantidade de mioglobina, fonte principal de ATP, "
        f"{final_phrase}"
    )
    pdf_bytes, _manifest = generate_answer_sheets(
        exam_id=uuid4(),
        exam_name="ANATOMIA II",
        questions=[QuestionSlot(number=1, text=long_question, max_score=1.0)],
        students=[
            (
                uuid4(),
                StudentInfo(
                    name="Aluno Teste",
                    registration_number="2026001",
                    curso="Medicina",
                    turma="Turma A",
                ),
            )
        ],
    )

    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        extracted_text = "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()

    normalized = " ".join(extracted_text.split())
    assert final_phrase in normalized


def test_practical_sheet_fits_twelve_short_questions_with_compact_logo_space():
    options = practical_answer_sheet_options()
    logo_bottom_y_after = (
        A4[1]
        - MARGIN
        - PAGE_TOP_CONTENT_INSET
        - options["logo_max_height"]
        - options["logo_bottom_gap"]
    )

    pages, _ = compute_answer_sheet_pages(
        uuid4(),
        _questions(12),
        uuid4(),
        logo_bottom_y_after=logo_bottom_y_after,
        **options,
    )

    assert len(pages) == 1
    assert len(pages[0].boxes) == 12
