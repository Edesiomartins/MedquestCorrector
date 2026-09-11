import json

from app.services.exam_grading_client import (
    GRADING_PROMPT,
    REQUIRED_GRADING_KEYS,
    _build_prompt,
    _looks_like_question_copy,
    _normalize_grading_response,
    grade_discursive_answer,
)

WILLIS_QUESTION = {
    "number": 35,
    "prompt": (
        "Um aneurisma sacular foi identificado na artéria comunicante anterior. "
        "Descreva a localização dessa artéria."
    ),
    "reading_confidence": "alta",
}
WILLIS_RUBRIC = {
    "max_score": 1.0,
    "expected_answer": (
        "A artéria comunicante anterior localiza-se entre as artérias cerebrais anteriores, "
        "fazendo parte do polígono de Willis."
    ),
}
CORE_ANSWER = "entre as artérias cerebrais anteriores no polígono de Willis"
PARTIAL_ANSWER = "localizada no polígono de Willis"
PARTIAL_WITH_ERROR = (
    "localizada no polígono de Willis, entre as artérias cerebrais médias"
)
INCOMPATIBLE_ANSWER = "é um músculo da parede anterior do abdome"


def _parsed(nota: float, comentario: str) -> dict:
    return {
        "nota": nota,
        "comentario": comentario,
        "criterios_atendidos": ["conceito pertinente"],
        "criterios_ausentes": [],
        "revisao_necessaria": False,
    }


def test_grading_prompt_keeps_current_scale_and_json_keys():
    folded = GRADING_PROMPT.casefold()
    for value in ("0", "0.25", "0.5", "0.75", "1.0"):
        assert value in GRADING_PROMPT
    for key in REQUIRED_GRADING_KEYS:
        assert key in GRADING_PROMPT
    assert "não use markdown" in folded
    assert "não inclua \"analysis\"" in folded


def test_grading_prompt_requires_conceptual_evaluation_order():
    folded = GRADING_PROMPT.casefold()
    assert "núcleo da pergunta" in folded
    assert "conceitos corretos" in folded
    assert "só depois identifique omissões e erros" in folded
    assert "não use similaridade lexical" in folded
    assert "não é um texto que o aluno precise reproduzir" in folded
    assert "erro parcial não apaga automaticamente" in folded
    assert "ortografia" in folded
    assert "comentário deve explicar" in folded


def test_grading_prompt_defines_semantic_score_bands():
    folded = GRADING_PROMPT.casefold()
    assert "núcleo conceitual necessário para responder à pergunta está correto" in folded
    assert "não exigir reprodução literal do gabarito" in folded
    assert "falta um detalhe importante" in folded
    assert "conhecimento substancial pertinente" in folded
    assert "insuficiente para responder adequadamente" in folded
    assert "conceitualmente incompatível" in folded


def test_grading_prompt_calibrates_willis_equivalent_cases():
    folded = GRADING_PROMPT.casefold()
    assert "entre as artérias cerebrais anteriores no polígono de willis" in folded
    assert "nunca 0 nem 0.25" in folded
    assert "localizada no polígono de willis" in folded
    assert "crédito parcial" in folded
    assert "não zere automaticamente" in folded
    assert "totalmente incompatível" in folded


def test_user_prompt_tells_model_not_to_use_lexical_similarity():
    prompt = _build_prompt(WILLIS_QUESTION, WILLIS_RUBRIC, CORE_ANSWER, "alta")
    folded = prompt.casefold()
    assert "não por similaridade lexical" in folded
    assert CORE_ANSWER in prompt
    assert WILLIS_RUBRIC["expected_answer"] in prompt or "cerebrais anteriores" in prompt


def test_willis_answers_are_not_treated_as_copies_of_the_question():
    for answer in (CORE_ANSWER, PARTIAL_ANSWER, PARTIAL_WITH_ERROR):
        question = {**WILLIS_QUESTION, "answer_transcription": answer}
        assert _looks_like_question_copy(question, WILLIS_RUBRIC, answer) is False


def test_core_willis_answer_keeps_high_semantic_score():
    question = {**WILLIS_QUESTION, "answer_transcription": CORE_ANSWER}
    out = _normalize_grading_response(
        _parsed(1.0, "Núcleo conceitual da localização está correto."),
        question,
        WILLIS_RUBRIC,
        "{}",
    )
    assert out["score"] == 1.0
    assert out["score"] not in {0.0, 0.25}
    assert "núcleo conceitual" in out["justification"].lower()


def test_partial_willis_location_keeps_partial_credit_not_zero():
    question = {**WILLIS_QUESTION, "answer_transcription": PARTIAL_ANSWER}
    out = _normalize_grading_response(
        _parsed(0.5, "Acerta o território geral, mas omite o detalhe essencial."),
        question,
        WILLIS_RUBRIC,
        "{}",
    )
    assert out["score"] == 0.5
    assert out["score"] > 0
    assert out["verdict"] == "parcial"


def test_partial_concept_plus_anatomical_error_is_not_auto_zeroed():
    question = {**WILLIS_QUESTION, "answer_transcription": PARTIAL_WITH_ERROR}
    out = _normalize_grading_response(
        _parsed(0.5, "Conceito pertinente presente, com erro anatômico relevante."),
        question,
        WILLIS_RUBRIC,
        "{}",
    )
    assert out["score"] == 0.5
    assert out["score"] not in {0.0, 0.25}


def test_incompatible_answer_remains_zero():
    question = {**WILLIS_QUESTION, "answer_transcription": INCOMPATIBLE_ANSWER}
    out = _normalize_grading_response(
        _parsed(0.0, "Resposta conceitualmente incompatível com a pergunta."),
        question,
        WILLIS_RUBRIC,
        "{}",
    )
    assert out["score"] == 0.0
    assert out["verdict"] == "incorreta"


def test_grade_discursive_answer_preserves_semantic_scores_from_model(monkeypatch):
    from app.services import exam_grading_client as client

    monkeypatch.setattr(client.settings, "OPENROUTER_API_KEY", "test-key")

    cases = [
        (CORE_ANSWER, 1.0, "Núcleo conceitual correto, sem exigir o texto do gabarito."),
        (PARTIAL_ANSWER, 0.5, "Território geral correto; detalhe essencial ausente."),
        (PARTIAL_WITH_ERROR, 0.5, "Conceito parcial com erro anatômico; crédito proporcional."),
        (INCOMPATIBLE_ANSWER, 0.0, "Resposta incompatível com a pergunta."),
    ]

    for answer, nota, comentario in cases:
        monkeypatch.setattr(
            client,
            "_call_openrouter_text",
            lambda model, prompt, nota=nota, comentario=comentario: json.dumps(
                {
                    "nota": nota,
                    "comentario": comentario,
                    "criterios_atendidos": ["conceito pertinente"] if nota else [],
                    "criterios_ausentes": [] if nota else ["núcleo da pergunta"],
                    "revisao_necessaria": False,
                },
                ensure_ascii=False,
            ),
        )
        out = grade_discursive_answer(
            {**WILLIS_QUESTION, "answer_transcription": answer},
            WILLIS_RUBRIC,
            answer,
            reading_confidence="alta",
        )
        assert out["score"] == nota
        if answer == CORE_ANSWER:
            assert out["score"] >= 0.75
        if answer == PARTIAL_ANSWER:
            assert out["score"] > 0
        if answer == INCOMPATIBLE_ANSWER:
            assert out["score"] == 0.0
        assert out["justification"] == comentario
        assert set(out.keys()) >= {
            "question_number",
            "score",
            "max_score",
            "verdict",
            "justification",
            "detected_concepts",
            "missing_concepts",
            "needs_human_review",
        }
