"""Correção prática: abreviações anatômicas, núcleo da resposta e semelhança."""

from app.services.exam_grading_client import grade_practical_answer


def _grade(expected: str, answer: str, max_score: float = 1.0) -> dict:
    return grade_practical_answer(
        {"number": 1, "reading_confidence": "alta", "max_score": max_score},
        {"expected_answer": expected, "max_score": max_score},
        answer,
        reading_confidence="alta",
    )


def test_bone_abbreviation_with_extra_words_is_correct():
    """Caso real: 'O.' é osso, e 'do pé' é contexto que o aluno acrescentou."""
    out = _grade("O. Calcaneo D.", "osso calcaneo do pe d")

    assert out["score"] == 1.0
    assert out["verdict"] == "correta"
    assert out["needs_human_review"] is False


def test_filler_words_do_not_block_match():
    out = _grade("Músculo Sóleo Esquerdo", "o musculo soleo do lado esquerdo")

    assert out["score"] == 1.0
    assert out["verdict"] == "correta"


def test_conflicting_structure_class_is_wrong():
    """Artéria femoral e nervo femoral são estruturas diferentes."""
    out = _grade("A. Femoral D.", "n. femoral d")

    assert out["score"] == 0.0
    assert out["verdict"] == "incorreta"
    assert "estrutura" in out["justification"].lower()


def test_omitted_structure_class_is_tolerated():
    out = _grade("A. Femoral D.", "femoral direita")

    assert out["score"] == 1.0
    assert out["verdict"] == "correta"


def test_partial_core_is_pending_review():
    """Falta 'braquial': não zera, fica pendente para o professor decidir."""
    out = _grade("M. Bíceps Braquial D.", "m biceps direito")

    assert out["score"] is None
    assert out["needs_human_review"] is True
    assert out["verdict"] == "revisao_pendente"


def test_unrelated_answer_is_wrong():
    out = _grade("M. Bíceps Braquial D.", "triceps direito")

    assert out["score"] == 0.0
    assert out["verdict"] == "incorreta"
    assert out["needs_human_review"] is False


def test_ocr_typo_in_core_is_pending_review():
    """'bucinafor' está a uma letra de 'bucinador': dúvida, não erro."""
    out = _grade("Músculo Bucinador E", "M. Bucinafor E.")

    assert out["score"] is None
    assert out["needs_human_review"] is True


def test_anterior_and_posterior_are_not_interchangeable():
    """A tolerância a erro de leitura não pode igualar anterior e posterior.

    Antes isto ficava pendente, pelo acerto parcial em 'tibial'. Mas anterior e
    posterior são qualificadores contraditórios do mesmo eixo: são músculos
    diferentes, e não há dúvida para o professor resolver.
    """
    out = _grade("M. Tibial Anterior D.", "m tibial posterior d")

    assert out["score"] == 0.0
    assert out["verdict"] == "incorreta"
    assert out["needs_human_review"] is False


def test_wrong_laterality_still_zeroes():
    out = _grade("Latissimo do dorso direito", "m. latissimo do dorso E.")

    assert out["score"] == 0.0
    assert out["verdict"] == "incorreta"
    assert "lateralidade" in out["justification"].lower()


# ---------------------------------------------------------------------------
# Casos reais de uma prova de 150 respostas, em que 69 foram para revisao com a
# transcricao correta. Cada um destes e uma regressao: a comparacao e por eixos
# (classe, lado, qualificador critico, nucleo), nunca por semelhanca da frase.
# ---------------------------------------------------------------------------


def test_abbreviated_posterior_matches_the_written_form():
    """'Post' e 'posterior' sao a mesma palavra; so uma esta abreviada."""
    out = _grade("Arteria Comunicante Post D", "Artéria comunicante posterior direita")

    assert out["verdict"] == "correta"
    assert out["score"] == 1.0


def test_dotted_artery_abbreviation_with_posterior_matches():
    out = _grade("Arteria Comunicante Post D", "A. COMUNICANTE POSTERIOR DIREITA")

    assert out["verdict"] == "correta"
    assert out["score"] == 1.0


def test_parenthetical_acronym_is_an_alias_not_a_required_word():
    """'(PDA)' e apelido do mesmo vaso: omiti-lo nao torna a resposta incompleta."""
    out = _grade("Artéria interventricular posterior (PDA)", "Artéria interventricular posterior")

    assert out["verdict"] == "correta"
    assert out["score"] == 1.0


def test_extra_anatomical_context_around_the_same_vessel_is_accepted():
    out = _grade(
        "Artéria interventricular posterior (PDA)",
        "Ramo interventricular posterior da Artéria Coronária direita",
    )

    assert out["verdict"] == "correta"
    assert out["score"] == 1.0


def test_plural_gabarito_and_chamber_abbreviation_match_the_written_form():
    """'Musculos papilares ... VE' e 'musculo papilar ... ventriculo esquerdo'."""
    out = _grade("Músculos papilares Anterior — VE", "Músculo papilar anterior do ventrículo esquerdo")

    assert out["verdict"] == "correta"
    assert out["score"] == 1.0


def test_right_ventricle_abbreviation_matches_the_written_form():
    out = _grade("Musculo Papilar Ant. VD", "Músculo papilar anterior do ventrículo direito")

    assert out["verdict"] == "correta"
    assert out["score"] == 1.0


# --- qualificadores criticos: contradicao e erro, nao duvida ------------------


def test_posterior_instead_of_anterior_is_wrong_not_pending():
    """Anterior e posterior sao posicoes contraditorias: nao ha o que revisar."""
    out = _grade("Musculo Papilar Ant. VD", "Músculo papilar posterior do ventrículo direito")

    assert out["verdict"] == "incorreta"
    assert out["score"] == 0.0
    assert out["needs_human_review"] is False


def test_anterior_instead_of_media_is_wrong():
    out = _grade("Artéria Cerebral Média D", "Artéria cerebral anterior direita")

    assert out["verdict"] == "incorreta"
    assert out["score"] == 0.0


def test_a_different_structure_sharing_a_qualifier_is_wrong():
    """'media' bate, mas 'cerebral' nao: qualificador sozinho nao e acerto parcial."""
    out = _grade("Artéria Cerebral Média D", "comissura cerebelar média direita")

    assert out["verdict"] == "incorreta"
    assert out["score"] == 0.0


def test_missing_side_plus_foreign_context_goes_to_review_not_to_full_score():
    """Cordas tendineas nao pertencem a valva semilunar; a regra nao decide sozinha."""
    out = _grade("Cordas tendíneas anteriores D", "Cordas tendíneas anteriores da valva semilunar pulmonar")

    assert out["verdict"] != "correta"
    assert out["score"] != 1.0
    assert out["needs_human_review"] is True
