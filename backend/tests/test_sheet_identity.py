"""Regressões de identidade de folha, agrupamento e autorização."""

from app.services.identity.sheet_identity import (
    display_name,
    group_sheet_rows,
    is_generic_student_name,
    lexical_registration_order,
    resolve_sheet_identity,
    same_account,
)


STUDENT_09 = "00000000-0000-0000-0000-000000000009"
STUDENT_10 = "00000000-0000-0000-0000-000000000010"
STUDENT_10_OTHER_CLASS = "00000000-0000-0000-0000-000000000110"


def _roster():
    return {
        STUDENT_09: {"name": "ALUNO 09", "registration": "9", "class": "P1"},
        STUDENT_10: {"name": "ALUNO 10", "registration": "10", "class": "P1"},
        STUDENT_10_OTHER_CLASS: {"name": "ALUNO 10", "registration": "10", "class": "P2"},
    }


def test_generic_registered_name_does_not_hide_handwritten_name():
    assert is_generic_student_name("ALUNO 09")
    assert is_generic_student_name("ALUNO 10")
    assert not is_generic_student_name("Eduardo Pires")

    identity = resolve_sheet_identity(
        physical_page=2,
        qr_exam_id="exam-1",
        qr_student_id=STUDENT_09,
        expected_exam_id="exam-1",
        roster=_roster(),
        header={"handwritten_name": "Eduardo Pires", "registration": "9", "printed_label": "ALUNO 09"},
    )

    assert identity.student_id == STUDENT_09
    assert identity.registered_name == "ALUNO 09"
    assert identity.handwritten_name == "Eduardo Pires"
    assert identity.display_name == "Eduardo Pires"
    assert display_name(confirmed_name="Nome conferido", handwritten_name="Eduardo Pires") == "Nome conferido"


def test_lexical_registration_order_puts_ten_on_generated_page_two():
    """ORDER BY registration_number em texto coloca a matrícula 10 na segunda folha.

    Para matrículas "1".."16", o índice 1 do manifesto é o aluno 10 e o índice 15
    é o aluno 9. Uma digitalização fora dessa ordem não pode herdar esse índice.
    """
    ordered = lexical_registration_order([str(number) for number in range(1, 17)])
    assert ordered[0] == "1"
    assert ordered[1] == "10"
    assert ordered[15] == "9"


def test_shuffled_page_does_not_inherit_manifest_student_ten():
    """Página 2 impressa como ALUNO 09 não vira o aluno 10 do manifesto.

    Essa é a causa reproduzível da mistura: sem QR, o manifesto da posição 2
    (índice 1) aponta para a matrícula "10" por ordem lexicográfica. A folha
    física continua sem vínculo automático.
    """
    identity = resolve_sheet_identity(
        physical_page=2,
        qr_student_id=None,
        expected_exam_id="exam-1",
        roster=_roster(),
        manifest_student_id=STUDENT_10,
        header={
            "printed_label": "ALUNO 09",
            "registration": "9",
            "handwritten_name": "Eduardo Pires",
        },
    )

    assert identity.student_id is None
    assert identity.manifest_student_id == STUDENT_10
    assert identity.printed_label == "ALUNO 09"
    assert identity.handwritten_name == "Eduardo Pires"
    assert identity.display_name == "Eduardo Pires"
    assert identity.uncertain is True
    assert any("não foi adotado" in item for item in identity.conflicts)


def test_qr_exam_mismatch_and_header_disagreement_stay_on_the_sheet():
    identity = resolve_sheet_identity(
        physical_page=4,
        qr_exam_id="outra-prova",
        qr_student_id=STUDENT_10,
        expected_exam_id="exam-1",
        roster=_roster(),
        manifest_student_id=STUDENT_09,
        header={"handwritten_name": "Outra Pessoa", "registration": "44", "printed_label": "ALUNO 04"},
    )
    assert identity.student_id is None
    assert any("outra prova" in item for item in identity.conflicts)
    assert identity.display_name == "Outra Pessoa"


def test_same_printed_code_in_different_classes_does_not_share_identity():
    first = resolve_sheet_identity(
        physical_page=1,
        qr_exam_id="exam-1",
        qr_student_id=STUDENT_10,
        expected_exam_id="exam-1",
        roster=_roster(),
        header={"printed_label": "ALUNO 10", "registration": "10"},
    )
    second = resolve_sheet_identity(
        physical_page=1,
        qr_exam_id="exam-2",
        qr_student_id=STUDENT_10_OTHER_CLASS,
        expected_exam_id="exam-2",
        roster=_roster(),
        header={"printed_label": "ALUNO 10", "registration": "10"},
    )
    assert first.student_id != second.student_id
    assert first.class_name == "P1"
    assert second.class_name == "P2"


def test_registrations_with_the_same_suffix_do_not_match():
    identity = resolve_sheet_identity(
        physical_page=3,
        qr_exam_id="exam-1",
        qr_student_id=STUDENT_10,
        expected_exam_id="exam-1",
        roster={STUDENT_10: {"name": "ALUNO 10", "registration": "110", "class": "P1"}},
        header={"registration": "10", "printed_label": "ALUNO 10", "handwritten_name": "Ana"},
    )
    assert identity.student_id is None
    assert identity.identity_source == "conflict"
    assert any("Matrícula" in item for item in identity.conflicts)


def test_repeated_questions_do_not_merge_eduardo_and_analy():
    """As páginas 2 e 16 saíram na mesma aba porque compartilhavam o código 10.

    Mesmo com esse rótulo repetido e as mesmas questões 1 a 10, a exportação
    mantém uma folha para cada página física.
    """
    rows = []
    for page, name in ((2, "Eduardo Pires"), (16, "Analy Rodrigues")):
        for question in range(1, 11):
            rows.append(
                {
                    "physical_page": page,
                    "question_number": question,
                    "student_name": "ALUNO 10",
                    "registered_name": "ALUNO 10",
                    "handwritten_name": name,
                    "registration_number": "10",
                    "detected_student_code": "010",
                    "identity_source": "manifest",
                    "uncertain": True,
                    "score": 1,
                }
            )

    groups = group_sheet_rows(rows)
    assert len(groups) == 2
    by_page = {group["physical_page"]: group for group in groups}
    assert len(by_page[2]["answers"]) == 10
    assert len(by_page[16]["answers"]) == 10
    assert by_page[2]["display_name"] == "Eduardo Pires"
    assert by_page[16]["display_name"] == "Analy Rodrigues"


def test_qr_continuation_merges_only_without_repeated_questions():
    rows = [
        {
            "physical_page": 1,
            "sheet_key": "page:1",
            "question_number": 1,
            "student_id": STUDENT_09,
            "identity_source": "qr",
            "uncertain": False,
            "handwritten_name": "Eduardo Pires",
            "registered_name": "ALUNO 09",
        },
        {
            "physical_page": 2,
            "sheet_key": "page:2",
            "question_number": 2,
            "student_id": STUDENT_09,
            "identity_source": "qr",
            "uncertain": False,
            "handwritten_name": "Eduardo Pires",
            "registered_name": "ALUNO 09",
        },
        {
            "physical_page": 3,
            "sheet_key": "page:3",
            "question_number": 1,
            "student_id": STUDENT_09,
            "identity_source": "qr",
            "uncertain": False,
            "handwritten_name": "Eduardo Pires",
        },
    ]
    groups = group_sheet_rows(rows)
    keys = {tuple(sorted(group["pages"])) for group in groups}
    assert (1, 2) in keys
    assert any(3 in group["pages"] and group["pages"] == [3] for group in groups)


def test_reprocessing_group_is_deterministic():
    rows = [
        {"physical_page": 2, "question_number": 1, "handwritten_name": "Bruno", "uncertain": True},
        {"physical_page": 1, "question_number": 1, "handwritten_name": "Ana", "uncertain": True},
    ]
    first = [group["display_name"] for group in group_sheet_rows(rows)]
    second = [group["display_name"] for group in group_sheet_rows(list(reversed(rows)))]
    assert first == second == ["Ana", "Bruno"]


def test_account_isolation():
    assert same_account(None, "user-a") is True
    assert same_account("user-a", "user-a") is True
    assert same_account("user-a", "user-b") is False
