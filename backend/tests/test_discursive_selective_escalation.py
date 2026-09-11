from app.services import visual_exam_pipeline as vep
from app.services.openrouter_vision_client import ANSWER_TRANSCRIPTION_PROMPT
from app.services.vision.discursive_escalation import should_escalate_discursive_reading


def _question(**overrides) -> dict:
    base = {
        "number": 1,
        "answer_transcription": "articulação plana",
        "reading_confidence": "alta",
        "reading_notes": "",
        "has_answer": True,
        "has_erasure": False,
        "erased_text": "",
        "model_used": "qwen-mock",
        "answer_crop_path": "/tmp/crop.png",
        "alternative_readings": [],
    }
    base.update(overrides)
    return base


def test_low_confidence_escalates():
    assert should_escalate_discursive_reading(_question(reading_confidence="baixa")) is True


def test_illegible_marker_escalates():
    assert should_escalate_discursive_reading(_question(answer_transcription="veia [ilegível]")) is True
    assert should_escalate_discursive_reading(_question(answer_transcription="veia [ilegivel]")) is True


def test_uncertain_marker_escalates():
    assert should_escalate_discursive_reading(_question(answer_transcription="miosina [?]")) is True


def test_high_confidence_does_not_escalate():
    assert should_escalate_discursive_reading(_question()) is False


def test_erasure_alone_does_not_escalate():
    assert (
        should_escalate_discursive_reading(
            _question(has_erasure=True, erased_text="artéria comunicante posterior")
        )
        is False
    )


def test_blank_ink_box_does_not_escalate():
    assert should_escalate_discursive_reading(_question(has_answer=False, answer_transcription="")) is False


def test_short_answer_does_not_escalate():
    assert should_escalate_discursive_reading(_question(answer_transcription="HAS")) is False


def test_doubtful_notes_escalate():
    assert should_escalate_discursive_reading(_question(reading_notes="há dúvida na leitura")) is True


def test_vision_prompt_does_not_include_expected_answer_or_rubric():
    folded = ANSWER_TRANSCRIPTION_PROMPT.casefold()
    assert "gabarito" not in folded
    assert "resposta esperada" not in folded
    assert "critérios" not in folded
    assert "criterios" not in folded
    assert "rubrica" not in folded


def test_max_three_escalations_per_page(monkeypatch):
    calls = []

    def fake_transcribe(crop_path, question_number=None, vision_model=None, allow_fallback=True):
        calls.append(
            {
                "crop_path": crop_path,
                "question_number": question_number,
                "vision_model": vision_model,
                "allow_fallback": allow_fallback,
            }
        )
        return {
            "answer_transcription": f"sol {question_number}",
            "reading_confidence": "alta",
            "reading_notes": "",
            "has_erasure": False,
            "erased_text": "",
            "has_answer": True,
            "model_used": vision_model,
        }

    monkeypatch.setattr(vep, "transcribe_answer_crop", fake_transcribe)
    questions = [
        _question(number=n, reading_confidence="baixa", answer_crop_path=f"/tmp/q{n}.png")
        for n in range(1, 6)
    ]
    warnings = []
    out = vep._maybe_selective_discursive_escalation(
        questions,
        options={
            "htr_discursive_selective_escalation_enabled": True,
            "htr_discursive_escalation_max_per_page": 3,
            "htr_discursive_escalation_model": "openai/gpt-5.6-sol",
        },
        warnings=warnings,
        physical_page_number=1,
    )
    assert len(calls) == 3
    assert [item["question_number"] for item in calls] == [1, 2, 3]
    assert all(item["allow_fallback"] is False for item in calls)
    assert all("expected_answer" not in item and "correction_criteria" not in item for item in calls)
    assert [q["answer_transcription"] for q in out[:3]] == ["sol 1", "sol 2", "sol 3"]
    assert out[3]["answer_transcription"] == "articulação plana"
    assert out[0]["original_reading"]["model_used"] == "qwen-mock"
    assert out[0]["escalated"] is True


def test_sol_failure_keeps_qwen_reading_and_flags_review(monkeypatch):
    def fail_transcribe(*_args, **_kwargs):
        raise RuntimeError("sol offline")

    monkeypatch.setattr(vep, "transcribe_answer_crop", fail_transcribe)
    warnings = []
    out = vep._maybe_selective_discursive_escalation(
        [_question(reading_confidence="baixa")],
        options={
            "htr_discursive_selective_escalation_enabled": True,
            "htr_discursive_escalation_model": "openai/gpt-5.6-sol",
        },
        warnings=warnings,
        physical_page_number=2,
    )
    assert out[0]["answer_transcription"] == "articulação plana"
    assert out[0]["escalation_failed"] is True
    assert out[0]["original_reading"]["answer_transcription"] == "articulação plana"
    assert out[0]["model_used"] == "qwen-mock"
    assert warnings

    grade = vep._apply_discursive_review_flags(
        {"score": 1.0, "verdict": "parcial", "needs_human_review": False, "review_reason": ""},
        out[0],
        is_practical=False,
    )
    assert grade["needs_human_review"] is True
    assert grade["score"] == 1.0


def test_sol_success_replaces_transcription_and_keeps_erasure(monkeypatch):
    def fake_transcribe(*_args, **_kwargs):
        return {
            "answer_transcription": "veia pulmonar inferior esquerda",
            "reading_confidence": "alta",
            "reading_notes": "",
            "has_erasure": True,
            "erased_text": "artéria comunicante",
            "has_answer": True,
            "model_used": "openai/gpt-5.6-sol",
        }

    monkeypatch.setattr(vep, "transcribe_answer_crop", fake_transcribe)
    out = vep._maybe_selective_discursive_escalation(
        [_question(reading_confidence="baixa", has_erasure=False)],
        options={"htr_discursive_selective_escalation_enabled": True},
        warnings=[],
        physical_page_number=1,
    )
    assert out[0]["answer_transcription"] == "veia pulmonar inferior esquerda"
    assert out[0]["has_erasure"] is True
    assert out[0]["erased_text"] == "artéria comunicante"
    assert out[0]["escalation_failed"] is False


def test_disabled_flag_does_not_call_sol(monkeypatch):
    calls = []
    monkeypatch.setattr(vep, "transcribe_answer_crop", lambda *a, **k: calls.append(1))
    vep._maybe_selective_discursive_escalation(
        [_question(reading_confidence="baixa")],
        options={"htr_discursive_selective_escalation_enabled": False},
        warnings=[],
        physical_page_number=1,
    )
    assert calls == []


def test_practical_exam_does_not_escalate(monkeypatch):
    calls = []
    monkeypatch.setattr(vep, "transcribe_answer_crop", lambda *a, **k: calls.append(1))
    vep._maybe_selective_discursive_escalation(
        [_question(reading_confidence="baixa")],
        options={"htr_discursive_selective_escalation_enabled": True, "is_practical": True},
        warnings=[],
        physical_page_number=1,
    )
    assert calls == []
