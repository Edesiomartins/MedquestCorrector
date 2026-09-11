from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.discursive_import.layout_detector import (
    build_template_manifest,
    detect_pdf_layout,
    normalize_docx_to_pdf,
    validate_confirmed_layout,
)


def _pdf_with_lined_question(number=38, second_number=None):
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    c.setFont('Helvetica-Bold', 12)
    c.drawString(42, h - 70, f'Questão {number}')
    c.setFont('Helvetica', 10)
    c.drawString(42, h - 92, 'Explique a diferença morfológica fundamental entre as articulações.')
    y = h - 145
    for _ in range(6):
        c.line(42, y, w - 42, y)
        y -= 24
    if second_number is not None:
        c.setFont('Helvetica-Bold', 12)
        c.drawString(42, y - 12, f'Questão {second_number}')
        c.setFont('Helvetica', 10)
        c.drawString(42, y - 34, 'Defina o conceito solicitado.')
        y2 = y - 70
        for _ in range(4):
            c.line(42, y2, w - 42, y2)
            y2 -= 24
    c.save()
    return buf.getvalue()


def test_detects_question_38_and_lined_answer_area():
    result = detect_pdf_layout(_pdf_with_lined_question())
    assert [q['question_number'] for q in result['questions']] == [38]
    q = result['questions'][0]
    assert q['page_index'] == 0
    assert q['height_pt'] > 80
    assert q['width_pt'] > 400
    assert q['provenance'] == 'answer_lines'
    assert q['confidence'] >= 0.9
    assert 'diferença morfológica' in q['question_text'].lower()
    assert result['pages'][0]['preview_data_url'].startswith('data:image/png;base64,')


def test_detects_non_contiguous_question_numbers():
    result = detect_pdf_layout(_pdf_with_lined_question(38, 41))
    assert [q['question_number'] for q in result['questions']] == [38, 41]


def test_build_template_manifest_repeats_template_pages():
    detected = detect_pdf_layout(_pdf_with_lined_question())
    manifest = build_template_manifest('exam-123', detected['pages'], detected['questions'])
    assert manifest['template_repeat'] is True
    assert manifest['template_page_count'] == 1
    assert manifest['source'] == 'external_discursive'
    assert manifest['pages'][0]['boxes'][0]['question_number'] == 38
    assert manifest['pages'][0]['student_id'] == ''


def test_validation_rejects_duplicate_question_numbers():
    pages = [{'page_index': 0, 'width_pt': 595.0, 'height_pt': 842.0}]
    questions = [
        {'question_number': 38, 'page_index': 0, 'question_text': 'A', 'x_pt': 20, 'y_bottom_pt': 20, 'width_pt': 200, 'height_pt': 100},
        {'question_number': 38, 'page_index': 0, 'question_text': 'B', 'x_pt': 20, 'y_bottom_pt': 140, 'width_pt': 200, 'height_pt': 100},
    ]
    errors, _warnings = validate_confirmed_layout(pages, questions)
    assert any('duplicado' in item.lower() for item in errors)


def test_validation_rejects_box_outside_page():
    pages = [{'page_index': 0, 'width_pt': 595.0, 'height_pt': 842.0}]
    questions = [
        {'question_number': 1, 'page_index': 0, 'question_text': 'A', 'x_pt': 500, 'y_bottom_pt': 20, 'width_pt': 200, 'height_pt': 100},
    ]
    errors, _warnings = validate_confirmed_layout(pages, questions)
    assert any('limites da página' in item.lower() for item in errors)


def test_docx_is_normalized_to_canonical_pdf_with_real_question_number():
    from docx import Document

    doc = Document()
    doc.add_paragraph('Curso de Medicina')
    doc.add_paragraph('Questão 38')
    doc.add_paragraph('O punho é composto por oito ossos. Analise a diferença morfológica.')
    for _ in range(6):
        doc.add_paragraph('_' * 90)
    buf = BytesIO()
    doc.save(buf)

    normalized = normalize_docx_to_pdf(buf.getvalue())
    assert normalized['canonical_pdf'][:4] == b'%PDF'
    assert normalized['warnings']
    detected = detect_pdf_layout(normalized['canonical_pdf'])
    assert [q['question_number'] for q in detected['questions']] == [38]
    assert detected['questions'][0]['provenance'] == 'answer_lines'


def test_blank_space_without_lines_is_proposed_for_manual_confirmation():
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    c.setFont('Helvetica-Bold', 12)
    c.drawString(42, h - 70, 'Q 7')
    c.setFont('Helvetica', 10)
    c.drawString(42, h - 92, 'Descreva o mecanismo fisiológico solicitado.')
    c.save()
    result = detect_pdf_layout(buf.getvalue())
    assert result['questions'][0]['question_number'] == 7
    assert result['questions'][0]['provenance'] == 'blank_space'
    assert result['questions'][0]['height_pt'] > 100


def test_multiple_pages_preserve_page_indexes():
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    for number in (38, 39):
        c.setFont('Helvetica-Bold', 12)
        c.drawString(42, h - 70, f'QUESTÃO {number}')
        c.setFont('Helvetica', 10)
        c.drawString(42, h - 92, f'Enunciado da questão {number}.')
        y = h - 145
        for _ in range(5):
            c.line(42, y, w - 42, y)
            y -= 24
        if number == 38:
            c.showPage()
    c.save()
    result = detect_pdf_layout(buf.getvalue())
    assert [(q['question_number'], q['page_index']) for q in result['questions']] == [(38, 0), (39, 1)]
    assert len(result['pages']) == 2


def test_question_labels_ignore_header_and_inline_mentions():
    from app.services.discursive_import.layout_detector import _question_number

    assert _question_number("QUESTÕES DISCURSIVAS") is None
    assert _question_number("Enunciado da questão 38.") is None
    assert _question_number("Questão 38") == 38
    assert _question_number("QUESTÃO 39") == 39
    assert _question_number("Q 7") == 7
    assert _question_number("1. Questão 35") == 35


def test_discursive_header_page_does_not_invent_a_question(monkeypatch):
    from app.services.discursive_import import layout_detector as detector

    monkeypatch.setattr(detector, "detect_discursive_page_layout", lambda *a, **k: {"questions": []})
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 12)
    c.drawString(42, h - 70, "QUESTÕES DISCURSIVAS")
    c.setFont("Helvetica", 10)
    c.drawString(42, h - 92, "Nome do aluno(a): ________________")
    c.save()
    result = detect_pdf_layout(buf.getvalue())
    assert result["questions"] == []
    assert any("nenhuma questão discursiva" in item.lower() for item in result["warnings"])


def test_overlap_is_warning_not_error():
    pages = [{'page_index': 0, 'width_pt': 595.0, 'height_pt': 842.0}]
    questions = [
        {'question_number': 1, 'page_index': 0, 'question_text': 'A', 'x_pt': 20, 'y_bottom_pt': 20, 'width_pt': 200, 'height_pt': 100},
        {'question_number': 2, 'page_index': 0, 'question_text': 'B', 'x_pt': 30, 'y_bottom_pt': 30, 'width_pt': 200, 'height_pt': 100},
    ]
    errors, warnings = validate_confirmed_layout(pages, questions)
    assert errors == []
    assert any('sobrepõem' in item for item in warnings)


def test_docx_question_label_with_underscores_does_not_pollute_question_text():
    from docx import Document
    doc = Document()
    doc.add_paragraph('Questão 38 ' + '_' * 50)
    doc.add_paragraph('O punho é composto por oito ossos carpianos.')
    doc.add_paragraph('Analise a diferença morfológica fundamental.')
    for _ in range(6):
        doc.add_paragraph('_' * 90)
    buf = BytesIO(); doc.save(buf)
    normalized = normalize_docx_to_pdf(buf.getvalue())
    detected = detect_pdf_layout(normalized['canonical_pdf'])
    q = detected['questions'][0]
    assert 'punho' in q['question_text'].lower()
    assert not q['question_text'].lstrip().startswith('_')


def _rasterized_pdf(source_pdf: bytes) -> bytes:
    import pymupdf as fitz

    src = fitz.open(stream=source_pdf, filetype="pdf")
    out = fitz.open()
    try:
        for page in src:
            pix = page.get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), alpha=False)
            new_page = out.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, pixmap=pix)
        return out.tobytes()
    finally:
        src.close()
        out.close()


def test_textual_pdf_keeps_structural_detector_and_skips_vision(monkeypatch):
    from app.services.discursive_import import layout_detector as detector

    called = []
    monkeypatch.setattr(
        detector,
        "detect_discursive_page_layout",
        lambda *a, **k: called.append(1) or {"questions": []},
    )
    result = detect_pdf_layout(_pdf_with_lined_question(38))
    assert called == []
    assert [q["question_number"] for q in result["questions"]] == [38]
    assert result["questions"][0]["provenance"] == "answer_lines"


def test_scanned_pdf_without_text_uses_visual_fallback(monkeypatch):
    from app.services.discursive_import import layout_detector as detector

    calls = []

    def fake_layout(image_path, vision_model=None):
        calls.append({"image_path": image_path, "vision_model": vision_model})
        return {
            "questions": [
                {
                    "number": 35,
                    "question_text": "Explique a diferença morfológica fundamental entre as articulações.",
                    "x": 0.07,
                    "y": 0.22,
                    "width": 0.86,
                    "height": 0.45,
                }
            ]
        }

    monkeypatch.setattr(detector, "detect_discursive_page_layout", fake_layout)
    raster = _rasterized_pdf(_pdf_with_lined_question(35))
    result = detect_pdf_layout(raster)

    assert calls, "PDF escaneado deve acionar o fallback visual"
    assert [q["question_number"] for q in result["questions"]] == [35]
    q = result["questions"][0]
    assert q["provenance"] == "vision_scan"
    assert q["page_index"] == 0
    assert "diferença morfológica" in q["question_text"].lower()
    assert q["width_pt"] > 400
    assert q["height_pt"] > 200
    assert q["x_pt"] >= 0
    assert q["y_bottom_pt"] >= 0
    assert q["x_pt"] + q["width_pt"] <= 600
    assert q["y_bottom_pt"] + q["height_pt"] <= 850


def test_vision_question_without_box_still_gets_a_default_area(monkeypatch):
    from app.services.discursive_import import layout_detector as detector

    monkeypatch.setattr(
        detector,
        "detect_discursive_page_layout",
        lambda *a, **k: {
            "questions": [
                {
                    "question_text": "Questão 35 Explique a diferença morfológica fundamental.",
                }
            ]
        },
    )
    result = detect_pdf_layout(_rasterized_pdf(_pdf_with_lined_question(35)))
    assert [q["question_number"] for q in result["questions"]] == [35]
    q = result["questions"][0]
    assert q["provenance"] == "vision_scan"
    assert q["width_pt"] > 80
    assert q["height_pt"] > 80
    assert "diferença morfológica" in q["question_text"].lower()


def test_layout_vision_prompt_is_blind_to_answer_key():
    from app.services.openrouter_vision_client import LAYOUT_DETECTION_PROMPT

    folded = LAYOUT_DETECTION_PROMPT.casefold()
    assert "não transcreva a resposta manuscrita" in folded
    assert "não invente gabarito" in folded
    assert "expected_answer" not in folded
    assert "correction_criteria" not in folded


def test_layout_detection_reuses_existing_openrouter_client(monkeypatch, tmp_path):
    from app.services import openrouter_vision_client as client

    image = tmp_path / "page.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    captured = {}
    monkeypatch.setattr(client.settings, "OPENROUTER_API_KEY", "test-key")

    def fake_call(**kwargs):
        captured.update(kwargs)
        return (
            '{"questions":[{"number":35,"question_text":"enunciado da questão 35","x":0.1,"y":0.2,"width":0.8,"height":0.4}]}',
            "vision-mock",
            False,
        )

    monkeypatch.setattr(client, "_call_with_fallbacks", fake_call)
    out = client.detect_discursive_page_layout(str(image))

    assert captured["prompt"] is client.LAYOUT_DETECTION_PROMPT
    assert captured["json_mode"] is True
    assert captured["what"] == "detecção de layout discursivo"
    assert "expected_answer" not in captured
    assert "correction_criteria" not in captured
    assert out["questions"][0]["number"] == 35
    assert out["model_used"] == "vision-mock"


def _pdf_pages_with_questions(numbers: list[int]) -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    for index, number in enumerate(numbers):
        c.setFont("Helvetica-Bold", 12)
        c.drawString(42, h - 70, f"Questão {number}")
        c.setFont("Helvetica", 10)
        c.drawString(42, h - 92, f"Enunciado da questão {number}.")
        y = h - 145
        for _ in range(5):
            c.line(42, y, w - 42, y)
            y -= 24
        if index < len(numbers) - 1:
            c.showPage()
    c.save()
    return buf.getvalue()


def _rasterized_repeated_first_page(source_pdf: bytes, n_pages: int) -> bytes:
    import pymupdf as fitz

    src = fitz.open(stream=source_pdf, filetype="pdf")
    out = fitz.open()
    try:
        pix = src[0].get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), alpha=False)
        for _ in range(n_pages):
            new_page = out.new_page(width=src[0].rect.width, height=src[0].rect.height)
            new_page.insert_image(new_page.rect, pixmap=pix)
        return out.tobytes()
    finally:
        src.close()
        out.close()


def test_scanned_repeated_q35_copies_collapse_to_one_logical_question(monkeypatch):
    from app.services.discursive_import import layout_detector as detector

    calls = []

    def fake_layout(*a, **k):
        calls.append(1)
        return {
            "questions": [
                {
                    "number": 35,
                    "question_text": "Explique a diferença morfológica.",
                    "x": 0.07,
                    "y": 0.22,
                    "width": 0.86,
                    "height": 0.45,
                }
            ]
        }

    monkeypatch.setattr(detector, "detect_discursive_page_layout", fake_layout)
    result = detect_pdf_layout(_rasterized_repeated_first_page(_pdf_with_lined_question(35), 5))

    assert [q["question_number"] for q in result["questions"]] == [35]
    assert len(result["pages"]) == 1
    assert result["template_repeat"] is True
    assert result["template_page_count"] == 1
    assert result["source_page_count"] == 5
    assert result["detected_copy_count"] == 5
    assert result["questions"][0]["occurrence_count"] == 5
    assert result["questions"][0]["page_index"] == 0
    errors, _warnings = validate_confirmed_layout(result["pages"], result["questions"])
    assert errors == []
    manifest = build_template_manifest("exam-35", result["pages"], result["questions"])
    assert manifest["template_repeat"] is True
    assert manifest["template_page_count"] == 1


def test_scanned_q35_q36_cycle_collapses_to_two_logical_questions(monkeypatch):
    from app.services.discursive_import import layout_detector as detector

    calls = []
    sequence = [35, 36, 35, 36, 35, 36]

    def fake_layout(*a, **k):
        number = sequence[len(calls)]
        calls.append(number)
        return {
            "questions": [
                {
                    "number": number,
                    "question_text": f"Enunciado da questão {number}.",
                    "x": 0.07,
                    "y": 0.22,
                    "width": 0.86,
                    "height": 0.45,
                }
            ]
        }

    monkeypatch.setattr(detector, "detect_discursive_page_layout", fake_layout)
    raster = _rasterized_pdf(_pdf_pages_with_questions(sequence))
    result = detect_pdf_layout(raster)

    assert [q["question_number"] for q in result["questions"]] == [35, 36]
    assert [q["page_index"] for q in result["questions"]] == [0, 1]
    assert result["template_page_count"] == 2
    assert result["template_repeat"] is True
    assert result["source_page_count"] == 6
    assert result["detected_copy_count"] == 3
    assert {q["question_number"]: q["occurrence_count"] for q in result["questions"]} == {35: 3, 36: 3}
    errors, _warnings = validate_confirmed_layout(result["pages"], result["questions"])
    assert errors == []


def test_vision_stops_after_repeated_template_is_identified(monkeypatch):
    from app.services.discursive_import import layout_detector as detector

    calls = []
    monkeypatch.setattr(
        detector,
        "detect_discursive_page_layout",
        lambda *a, **k: calls.append(1)
        or {
            "questions": [
                {
                    "number": 35,
                    "question_text": "Q35",
                    "x": 0.1,
                    "y": 0.2,
                    "width": 0.8,
                    "height": 0.4,
                }
            ]
        },
    )
    result = detect_pdf_layout(_rasterized_repeated_first_page(_pdf_with_lined_question(35), 105))
    assert result["template_page_count"] == 1
    assert result["detected_copy_count"] == 105
    assert [q["question_number"] for q in result["questions"]] == [35]
    assert len(calls) == 2


def test_confirm_payload_with_repeated_copies_is_consolidated_before_validation():
    from app.services.discursive_import.layout_detector import consolidate_repeated_template

    pages = [{"page_index": index, "width_pt": 595.0, "height_pt": 842.0} for index in range(5)]
    questions = [
        {
            "question_number": 35,
            "page_index": index,
            "question_text": "Explique.",
            "x_pt": 40,
            "y_bottom_pt": 40,
            "width_pt": 500,
            "height_pt": 200,
            "expected_answer": "resposta única" if index == 0 else "",
            "correction_criteria": "critério único" if index == 0 else "",
            "max_score": 2.0 if index == 0 else 1.0,
        }
        for index in range(5)
    ]
    pages, questions, meta = consolidate_repeated_template(pages, questions, source_page_count=5)
    assert meta["template_page_count"] == 1
    assert meta["detected_copy_count"] == 5
    assert len(questions) == 1
    assert questions[0]["question_number"] == 35
    assert questions[0]["expected_answer"] == "resposta única"
    assert questions[0]["correction_criteria"] == "critério único"
    assert questions[0]["max_score"] == 2.0
    errors, _warnings = validate_confirmed_layout(pages, questions)
    assert errors == []


def test_real_duplicate_inside_template_is_still_rejected():
    pages = [
        {"page_index": 0, "width_pt": 595.0, "height_pt": 842.0},
        {"page_index": 1, "width_pt": 595.0, "height_pt": 842.0},
    ]
    questions = [
        {
            "question_number": 35,
            "page_index": 0,
            "question_text": "A",
            "x_pt": 20,
            "y_bottom_pt": 400,
            "width_pt": 200,
            "height_pt": 100,
        },
        {
            "question_number": 35,
            "page_index": 1,
            "question_text": "B",
            "x_pt": 20,
            "y_bottom_pt": 40,
            "width_pt": 200,
            "height_pt": 100,
        },
    ]
    errors, _warnings = validate_confirmed_layout(pages, questions)
    assert any("duplicado" in item.lower() for item in errors)
