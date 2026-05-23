from app.services.exam_grading_client import (
    _build_prompt,
    _normalize_grading_response,
    clamp_grade,
    grade_practical_answer,
    parse_llm_json_response,
)


def test_parse_llm_json_response_invalid_json_returns_safe_fallback():
    raw = """{
      "nota": 0.75
      "comentario": "faltou ATP"
    }"""
    parsed = parse_llm_json_response(raw)
    assert parsed["revisao_necessaria"] is True
    assert parsed["nota"] == 0.0
    assert "erro_parse" in parsed


def test_clamp_grade_marks_review_when_outside_scale():
    value, needs_review = clamp_grade(0.6)
    assert value in {0.5, 0.75}
    assert needs_review is True


def test_clamp_grade_accepts_valid_scale_without_review():
    value, needs_review = clamp_grade(0.75)
    assert value == 0.75
    assert needs_review is False


def test_grading_flags_when_answer_copies_question_statement():
    parsed = {
        "nota": 1,
        "comentario": "ok",
        "criterios_atendidos": [],
        "criterios_ausentes": [],
        "revisao_necessaria": False,
    }
    question = {
        "number": 1,
        "prompt": "Explique o mecanismo de contração muscular envolvendo actina, miosina e ATP.",
        "answer_transcription": "Explique o mecanismo de contração muscular envolvendo actina, miosina e ATP.",
        "reading_confidence": "alta",
    }
    rubric = {"max_score": 1.0}
    out = _normalize_grading_response(parsed, question, rubric, "{}")
    assert out["score"] == 0.0
    assert out["needs_human_review"] is True
    assert "cópia do enunciado" in (out["review_reason"] or "")


def test_grading_marks_review_when_required_key_is_misspelled():
    parsed = {
        "nota": 0.5,
        "comentario": "ok",
        "criterios_atendidos": ["x"],
        "criterios_austeis": [],
        "revisao_necessosa": " ",
    }
    question = {"number": 1, "prompt": "Q", "answer_transcription": "Resposta", "reading_confidence": "alta"}
    rubric = {"max_score": 1.0}
    out = _normalize_grading_response(parsed, question, rubric, "{}")
    assert out["score"] == 0.5
    assert out["needs_human_review"] is True
    assert out["schema_valid"] is False
    assert any("chaves ausentes" in w.lower() for w in out["parse_warnings"])


def test_grading_string_boolean_only_accepts_true_false_literals():
    parsed = {
        "nota": 0.5,
        "comentario": "ok",
        "criterios_atendidos": ["x"],
        "criterios_ausentes": [],
        "revisao_necessaria": "t",
    }
    question = {"number": 1, "prompt": "Q", "answer_transcription": "Resposta", "reading_confidence": "alta"}
    rubric = {"max_score": 1.0}
    out = _normalize_grading_response(parsed, question, rubric, "{}")
    assert out["needs_human_review"] is True
    assert any("revisao_necessaria" in w for w in out["parse_warnings"])


def test_analysis_field_with_missing_required_keys_forces_review():
    parsed = {"analysis": "We need to produce JSON...", "nota": 0.5}
    question = {"number": 2, "prompt": "Q2", "answer_transcription": "Resposta", "reading_confidence": "alta"}
    rubric = {"max_score": 1.0}
    out = _normalize_grading_response(parsed, question, rubric, "{}")
    assert out["score"] == 0.5
    assert out["schema_valid"] is False
    assert out["needs_human_review"] is True
    assert any("analysis" in w.lower() for w in out["parse_warnings"])


def test_practical_grading_matches_expected_muscle_with_laterality_abbreviation():
    out = grade_practical_answer(
        {"number": 1, "reading_confidence": "alta"},
        {"expected_answer": "Peitoral maior esquerdo", "max_score": 1.0},
        "m. PeiToral maioR E.",
        reading_confidence="alta",
    )

    assert out["score"] == 1.0
    assert out["verdict"] == "correta"
    assert out["needs_human_review"] is False


def test_practical_grading_accepts_accents_and_compact_muscle_laterality_abbreviations():
    cases = [
        "M. Soleo E",
        "M.Soleo E.",
        "musc. sóleo esq.",
        "Sóleo esquerdo (E)",
    ]

    for answer in cases:
        out = grade_practical_answer(
            {"number": 1, "reading_confidence": "alta"},
            {"expected_answer": "Músculo Sóleo Esquerdo", "max_score": 1.0},
            answer,
            reading_confidence="alta",
        )

        assert out["score"] == 1.0
        assert out["verdict"] == "correta"
        assert out["needs_human_review"] is False


def test_practical_grading_rejects_wrong_laterality():
    out = grade_practical_answer(
        {"number": 1, "reading_confidence": "alta"},
        {"expected_answer": "Latissimo do dorso direito", "max_score": 1.0},
        "m. latissimo do dorso E.",
        reading_confidence="alta",
    )

    assert out["score"] == 0.0
    assert out["verdict"] == "incorreta"
    assert "lateralidade" in out["justification"].lower()


def test_practical_grading_accepts_synonym_grande_dorsal_vs_latissimo():
    out = grade_practical_answer(
        {"number": 3, "reading_confidence": "alta"},
        {"expected_answer": "Músculo Grande dorsal E", "max_score": 1.0},
        "m. latíssimo do dorso E.",
        reading_confidence="alta",
    )

    assert out["score"] == 1.0
    assert out["verdict"] == "correta"


def test_practical_grading_accepts_trailing_laterality_abbreviation_only_at_end():
    out = grade_practical_answer(
        {"number": 9, "reading_confidence": "alta"},
        {"expected_answer": "Músculo Redondo menor E", "max_score": 1.0},
        "m redondo menor e",
        reading_confidence="alta",
    )

    assert out["score"] == 1.0
    assert out["verdict"] == "correta"


def test_practical_grading_expands_muscle_abbreviations_m_and_mm():
    out = grade_practical_answer(
        {"number": 1, "reading_confidence": "alta"},
        {"expected_answer": "Músculos peitoral maior e redondo menor esquerdo", "max_score": 1.0},
        "Mm. peitoral maior e redondo menor E.",
        reading_confidence="alta",
    )

    assert out["score"] == 1.0
    assert out["verdict"] == "correta"


def test_practical_grading_expands_artery_abbreviation_a():
    out = grade_practical_answer(
        {"number": 1, "reading_confidence": "alta"},
        {"expected_answer": "Artéria braquial direita", "max_score": 1.0},
        "A. braquial D.",
        reading_confidence="alta",
    )

    assert out["score"] == 1.0
    assert out["verdict"] == "correta"


def test_practical_grading_near_match_sets_human_review():
    out = grade_practical_answer(
        {"number": 1, "reading_confidence": "alta"},
        {"expected_answer": "Músculo Bucinador E", "max_score": 1.0},
        "M. Bucinafor E.",
        reading_confidence="alta",
    )

    assert out["score"] == 0.0
    assert out["needs_human_review"] is True
    assert "ocr/abreviação" in out["review_reason"].lower()


def test_discursive_prompt_includes_expanded_anatomy_abbreviations():
    prompt = _build_prompt(
        {
            "number": 1,
            "prompt": "Descreva Mm. e A. relacionadas ao caso.",
        },
        {
            "expected_answer": "Mm. flexores e A. braquial",
            "max_score": 1.0,
        },
        "Mm. flexores do antebraço e A. braquial",
        "alta",
    )

    assert "student_answer_expanded" in prompt
    assert "musculos flexores" in prompt.lower()
    assert "arteria braquial" in prompt.lower()
    assert "rubric_expected_answer_expanded" in prompt
