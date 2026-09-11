from app.services.openrouter_vision_client import (
    _normalize_batch_questions,
    parse_transcription_response,
)


def test_crop_transcription_preserves_erasure_metadata():
    raw = """<TRANSCRICAO>veia pulmonar inferior esquerda</TRANSCRICAO>
<CONFIANCA>alta</CONFIANCA>
<RASURA>sim</RASURA>
<TEXTO_RISCADO>artéria comunicante posterior direita</TEXTO_RISCADO>
<NOTAS>resposta anterior riscada</NOTAS>"""
    parsed = parse_transcription_response(raw)
    assert parsed["answer_transcription"] == "veia pulmonar inferior esquerda"
    assert parsed["has_erasure"] is True
    assert parsed["erased_text"] == "artéria comunicante posterior direita"


def test_batch_transcription_preserves_erasure_metadata():
    parsed = {
        "questions": [
            {
                "number": 38,
                "answer_transcription": "articulação plana",
                "reading_confidence": "alta",
                "has_answer": True,
                "has_erasure": True,
                "erased_text": "gínglimo",
                "reading_notes": "rasura visível",
            }
        ]
    }
    result = _normalize_batch_questions(parsed, [38], model="vision-test", fallback_used=False)
    assert result[0]["has_erasure"] is True
    assert result[0]["erased_text"] == "gínglimo"


def test_batch_defaults_to_no_erasure_when_model_omits_fields():
    parsed = {
        "questions": [
            {
                "number": 1,
                "answer_transcription": "texto",
                "reading_confidence": "alta",
                "has_answer": True,
            }
        ]
    }
    result = _normalize_batch_questions(parsed, [1], model="vision-test", fallback_used=False)
    assert result[0]["has_erasure"] is False
    assert result[0]["erased_text"] == ""


def test_discursive_erasure_keeps_score_and_requires_human_review():
    from app.services.visual_exam_pipeline import (
        DISCURSIVE_ERASURE_REVIEW_REASON,
        _apply_discursive_review_flags,
    )

    grade = {
        "score": 1.5,
        "max_score": 2.0,
        "verdict": "parcial",
        "needs_human_review": False,
        "review_reason": "",
    }
    out = _apply_discursive_review_flags(
        dict(grade),
        {"has_erasure": True, "erased_text": "artéria comunicante"},
        is_practical=False,
    )
    assert out["score"] == 1.5
    assert out["verdict"] == "parcial"
    assert out["needs_human_review"] is True
    assert DISCURSIVE_ERASURE_REVIEW_REASON in out["review_reason"]


def test_practical_erasure_does_not_change_grade_via_discursive_rule():
    from app.services.visual_exam_pipeline import _apply_discursive_review_flags

    grade = {
        "score": 2.0,
        "max_score": 2.0,
        "verdict": "correta",
        "needs_human_review": False,
        "review_reason": "",
    }
    out = _apply_discursive_review_flags(
        dict(grade),
        {"has_erasure": True, "erased_text": "gínglimo"},
        is_practical=True,
    )
    assert out == grade
