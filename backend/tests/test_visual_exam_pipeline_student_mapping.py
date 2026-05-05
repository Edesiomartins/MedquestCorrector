from app.services.visual_exam_pipeline import analyze_discursive_exam_pdf


def test_student_mapping_uses_detected_header_not_page_order(monkeypatch, tmp_path):
    pdf_path = tmp_path / "prova.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "app.services.visual_exam_pipeline.render_pdf_to_images",
        lambda *_args, **_kwargs: [f"page-{idx}" for idx in range(1, 9)],
    )
    monkeypatch.setattr(
        "app.services.visual_exam_pipeline.normalize_page_image",
        lambda image: image,
    )
    monkeypatch.setattr(
        "app.services.visual_exam_pipeline.maybe_crop_answer_regions",
        lambda _image: {"regions": []},
    )
    monkeypatch.setattr(
        "app.services.visual_exam_pipeline.grade_discursive_answer",
        lambda question, _rubric, _answer, reading_confidence="media": {
            "question_number": int(question.get("number") or 0),
            "score": 0.0,
            "max_score": 1.0,
            "verdict": "parcial",
            "justification": "ok",
            "detected_concepts": [],
            "missing_concepts": [],
            "needs_human_review": reading_confidence == "baixa",
            "review_reason": "",
            "model_used": "mock-model",
            "fallback_used": False,
        },
    )

    def fake_extract(_image_path, page_number=None, context=None):
        if page_number == 1:
            return {
                "student": {
                    "name": "ALUNO 09",
                    "registration": "24102MED009",
                    "class": "T1",
                    "student_code": "009",
                },
                "physical_page": 1,
                "questions": [
                    {
                        "number": 1,
                        "prompt_detected": "Q1",
                        "answer_transcription": "Resposta do aluno 09",
                        "reading_confidence": "alta",
                        "ocr_confidence": 0.93,
                        "reading_notes": "",
                        "has_answer": True,
                        "image_region": {"x": 1, "y": 2, "w": 3, "h": 4},
                    }
                ],
                "model_used": "vision-mock",
                "fallback_used": False,
            }
        if page_number == 8:
            return {
                "student": {
                    "name": "ALUNO 01",
                    "registration": "24102MED001",
                    "class": "T1",
                    "student_code": "001",
                },
                "physical_page": 8,
                "questions": [
                    {
                        "number": 1,
                        "prompt_detected": "Q1",
                        "answer_transcription": "Eita, essa me pegou...",
                        "reading_confidence": "media",
                        "ocr_confidence": 0.88,
                        "reading_notes": "",
                        "has_answer": True,
                        "image_region": {"q": 1},
                    },
                    {
                        "number": 2,
                        "prompt_detected": "Q2",
                        "answer_transcription": "Não faço ideia...",
                        "reading_confidence": "alta",
                        "ocr_confidence": 0.95,
                        "reading_notes": "",
                        "has_answer": True,
                        "image_region": {"q": 2},
                    },
                    {
                        "number": 3,
                        "prompt_detected": "Q3",
                        "answer_transcription": "Tem ácido lático e sensação de queimação muscular.",
                        "reading_confidence": "alta",
                        "ocr_confidence": 0.96,
                        "reading_notes": "",
                        "has_answer": True,
                        "image_region": {"q": 3},
                    },
                ],
                "model_used": "vision-mock",
                "fallback_used": False,
            }
        return {
            "student": {
                "name": f"ALUNO X{page_number}",
                "registration": f"REG{page_number:03d}",
                "class": "T1",
                "student_code": f"{page_number:03d}",
            },
            "physical_page": page_number,
            "questions": [],
            "model_used": "vision-mock",
            "fallback_used": False,
        }

    monkeypatch.setattr(
        "app.services.visual_exam_pipeline.extract_answers_from_page_image",
        fake_extract,
    )

    result = analyze_discursive_exam_pdf(str(pdf_path), rubric={"questions": []}, options={})
    assert result["status"] == "success"

    by_page = {entry["physical_page"]: entry for entry in result["students"]}
    assert by_page[1]["detected_student_code"] == "009"
    assert by_page[1]["detected_student_name"] == "ALUNO 09"
    assert by_page[8]["detected_student_code"] == "001"
    assert by_page[8]["detected_registration"] == "24102MED001"

    aluno_01 = by_page[8]
    answers = {q["question_number"]: q["extracted_answer"] for q in aluno_01["questions"]}
    assert answers[1].startswith("Eita, essa me pegou")
    assert answers[2].startswith("Não faço ideia")
    assert "ácido lático" in answers[3]
    assert "queimação" in answers[3]

    aluno_09_answers = {q["question_number"]: q["extracted_answer"] for q in by_page[1]["questions"]}
    assert answers[1] != aluno_09_answers[1]


def test_physical_page_is_global_and_sequential(monkeypatch, tmp_path):
    pdf_path = tmp_path / "prova.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "app.services.visual_exam_pipeline.render_pdf_to_images",
        lambda *_args, **_kwargs: [f"page-{idx}" for idx in range(1, 11)],
    )
    monkeypatch.setattr("app.services.visual_exam_pipeline.normalize_page_image", lambda image: image)
    monkeypatch.setattr("app.services.visual_exam_pipeline.maybe_crop_answer_regions", lambda _image: {"regions": []})
    monkeypatch.setattr(
        "app.services.visual_exam_pipeline.extract_answers_from_page_image",
        lambda _image_path, page_number=None, context=None: {
            "student": {"name": f"ALUNO {page_number:02d}", "registration": f"REG{page_number:03d}", "class": "T1"},
            "physical_page": 1,  # simula retorno inconsistente do modelo
            "questions": [],
            "model_used": "vision-mock",
            "fallback_used": False,
        },
    )

    result = analyze_discursive_exam_pdf(str(pdf_path), rubric={"questions": []}, options={})
    assert result["status"] == "success"
    pages = [entry["physical_page"] for entry in result["students"]]
    assert pages == list(range(1, 11))


def test_rubric_mapping_uses_question_number_not_array_index(monkeypatch, tmp_path):
    pdf_path = tmp_path / "prova.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(
        "app.services.visual_exam_pipeline.render_pdf_to_images",
        lambda *_args, **_kwargs: ["page-1"],
    )
    monkeypatch.setattr("app.services.visual_exam_pipeline.normalize_page_image", lambda image: image)
    monkeypatch.setattr("app.services.visual_exam_pipeline.maybe_crop_answer_regions", lambda _image: {"regions": []})
    monkeypatch.setattr(
        "app.services.visual_exam_pipeline.extract_answers_from_page_image",
        lambda *_args, **_kwargs: {
            "student": {"name": "ALUNO 06", "registration": "24102MED006", "class": "T1"},
            "physical_page": 1,
            "questions": [
                {"number": 1, "answer_transcription": "Salve o Corinthians...", "reading_confidence": "alta"},
                {"number": 2, "answer_transcription": "Fibras tipo I resistem mais.", "reading_confidence": "alta"},
                {"number": 3, "answer_transcription": "Lactato e metabolismo anaeróbico.", "reading_confidence": "alta"},
            ],
            "model_used": "vision-mock",
            "fallback_used": False,
        },
    )

    def fake_grade(question, rubric, _answer, reading_confidence="media"):
        expected = str(rubric.get("expected_answer") or "").lower()
        if int(question.get("number") or 0) == 2:
            assert "fibras tipo i" in expected or "fibras tipo ii" in expected
            assert "filamentos" not in expected
        if int(question.get("number") or 0) == 3:
            assert "lactato" in expected or "anaeróbico" in expected
        return {
            "question_number": int(question.get("number") or 0),
            "score": 0.5,
            "max_score": 1.0,
            "verdict": "parcial",
            "justification": "ok",
            "detected_concepts": [],
            "missing_concepts": [],
            "needs_human_review": False,
            "review_reason": "",
            "model_used": "mock-model",
            "fallback_used": False,
        }

    monkeypatch.setattr("app.services.visual_exam_pipeline.grade_discursive_answer", fake_grade)
    result = analyze_discursive_exam_pdf(
        str(pdf_path),
        rubric={
            "questions": [
                {"number": 1, "prompt": "Q1", "expected_answer": "filamentos deslizantes actina e miosina", "max_score": 1.0},
                {"number": 2, "prompt": "Q2", "expected_answer": "fibras tipo I e II, maratonista e velocista", "max_score": 1.0},
                {"number": 3, "prompt": "Q3", "expected_answer": "metabolismo anaeróbico, lactato e queimação", "max_score": 1.0},
            ]
        },
        options={},
    )
    assert result["status"] == "success"


def test_practical_exam_uses_expected_answer_without_discursive_llm(monkeypatch, tmp_path):
    pdf_path = tmp_path / "prova_pratica.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr("app.services.visual_exam_pipeline.render_pdf_to_images", lambda *_args, **_kwargs: ["page-1"])
    monkeypatch.setattr("app.services.visual_exam_pipeline.normalize_page_image", lambda image: image)
    monkeypatch.setattr("app.services.visual_exam_pipeline.maybe_crop_answer_regions", lambda _image: {"regions": []})
    monkeypatch.setattr(
        "app.services.visual_exam_pipeline.extract_answers_from_page_image",
        lambda *_args, **_kwargs: {
            "student": {"name": "ALUNO 01", "registration": "1", "class": "P1"},
            "physical_page": 1,
            "questions": [
                {"number": 1, "answer_transcription": "m. PeiToral maioR E.", "reading_confidence": "alta"},
                {"number": 2, "answer_transcription": "m. orbicular da boca", "reading_confidence": "alta"},
            ],
            "model_used": "vision-mock",
            "fallback_used": False,
        },
    )

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("corretor discursivo não deve ser chamado em prova prática")

    monkeypatch.setattr("app.services.visual_exam_pipeline.grade_discursive_answer", fail_if_called)

    result = analyze_discursive_exam_pdf(
        str(pdf_path),
        rubric={
            "is_practical": True,
            "questions": [
                {"number": 1, "prompt": "Identifique", "expected_answer": "Peitoral maior esquerdo", "max_score": 1.0},
                {"number": 2, "prompt": "Identifique", "expected_answer": "Orbicular da boca", "max_score": 1.0},
            ],
        },
        options={"is_practical": True},
    )

    grades = {
        q["question_number"]: q["grade"]
        for q in result["students"][0]["questions"]
    }
    assert grades[1]["score"] == 1.0
    assert grades[2]["score"] == 1.0
    assert grades[1]["model_used"] == "practical-rule-based"
