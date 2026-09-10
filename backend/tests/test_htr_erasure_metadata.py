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
