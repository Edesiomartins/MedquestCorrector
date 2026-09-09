"""Item 4 no pipeline visual: quando ha manifesto, le por recorte de caixa.

Antes o pipeline visual mandava a PAGINA INTEIRA ao modelo e ignorava
`Exam.layout_manifest_json`, que o worker Celery ja usava. Agora os dois passam
pelo mesmo servico de geometria. Sem manifesto, o caminho de pagina inteira
continua valendo -- provas antigas nao podem parar de funcionar.
"""

import pymupdf as fitz
import pytest
from reportlab.lib.pagesizes import A4

from app.services import visual_exam_pipeline as vep
from app.services.visual_exam_pipeline import analyze_discursive_exam_pdf


PAGE_W_PT, PAGE_H_PT = A4

MANIFEST = {
    "version": 1,
    "pages": [
        {
            "physical_index": 0,
            "exam_id": "exam-1",
            "student_id": "student-1",
            "page_in_student": 1,
            "total_pages_for_student": 1,
            "boxes": [
                {"question_number": 1, "x_pt": 56.7, "y_bottom_pt": 600.0, "width_pt": 481.9, "height_pt": 90.0},
                {"question_number": 2, "x_pt": 56.7, "y_bottom_pt": 460.0, "width_pt": 481.9, "height_pt": 90.0},
            ],
            "fiducials": [],
        }
    ],
}

RUBRIC = {
    "questions": [
        {"number": 1, "prompt": "Q1", "max_score": 2.0, "expected_answer": "actina e miosina"},
        {"number": 2, "prompt": "Q2", "max_score": 2.0, "expected_answer": "fibras tipo I"},
    ]
}


@pytest.fixture
def sheet_pdf(tmp_path):
    """Folha com a caixa da Q1 preenchida (tinta) e a Q2 vazia."""
    path = tmp_path / "folha.pdf"
    doc = fitz.open()
    page = doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
    box = MANIFEST["pages"][0]["boxes"][0]
    top = PAGE_H_PT - (box["y_bottom_pt"] + box["height_pt"])
    for row in range(3):
        y = top + 20 + row * 22
        page.draw_line(
            fitz.Point(box["x_pt"] + 10, y),
            fitz.Point(box["x_pt"] + box["width_pt"] - 10, y),
            color=(0.1, 0.15, 0.5),
            width=1.6,
        )
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def stub_grading(monkeypatch):
    monkeypatch.setattr(
        vep,
        "grade_discursive_answer",
        lambda question, _rubric, answer, reading_confidence="media": {
            "question_number": int(question.get("number") or 0),
            "score": 1.0 if answer else 0.0,
            "max_score": 2.0,
            "verdict": "parcial",
            "justification": "ok",
            "detected_concepts": [],
            "missing_concepts": [],
            "needs_human_review": False,
            "review_reason": "",
            "model_used": "text-mock",
        },
    )


@pytest.fixture
def spy_transcribe(monkeypatch):
    calls: list[dict] = []

    def fake(image_path, question_number=None, vision_model=None):
        calls.append({"image_path": image_path, "question_number": question_number})
        return {
            "number": int(question_number or 0),
            "prompt_detected": "",
            "answer_transcription": f"resposta manuscrita da questao {question_number}",
            "reading_confidence": "alta",
            "ocr_confidence": None,
            "reading_notes": "",
            "has_answer": True,
            "image_region": None,
            "model_used": "vision-mock",
            "fallback_used": False,
        }

    monkeypatch.setattr(vep, "transcribe_answer_crop", fake)
    monkeypatch.setattr(
        vep,
        "read_sheet_header",
        lambda *a, **k: {
            "name": "ALUNO 09",
            "registration": "24102MED009",
            "class": "T1",
            "student_code": "009",
            "model_used": "vision-mock",
            "fallback_used": False,
        },
    )
    return calls


def _options(**extra):
    return {"layout_manifest": MANIFEST, "run_id": "test", **extra}


def test_manifest_triggers_one_call_per_answered_question(sheet_pdf, spy_transcribe, stub_grading):
    result = analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, _options())

    assert result["status"] == "success"
    # Q1 tem tinta e vai ao modelo; Q2 esta vazia e nao gasta chamada nenhuma.
    assert [call["question_number"] for call in spy_transcribe] == [1]


def test_empty_box_is_reported_without_calling_the_model(sheet_pdf, spy_transcribe, stub_grading):
    result = analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, _options())

    by_number = {q["number"]: q for q in result["students"][0]["questions"]}
    assert by_number[2]["has_answer"] is False
    assert by_number[2]["answer_transcription"] == ""


def test_answered_question_carries_the_transcription(sheet_pdf, spy_transcribe, stub_grading):
    result = analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, _options())

    by_number = {q["number"]: q for q in result["students"][0]["questions"]}
    assert "resposta manuscrita da questao 1" in by_number[1]["answer_transcription"]
    assert by_number[1]["has_answer"] is True


def test_crop_is_persisted_so_the_review_screen_can_show_it(sheet_pdf, spy_transcribe, stub_grading, monkeypatch):
    """Sem a imagem, o revisor nao revisa: ele aceita."""
    monkeypatch.setenv("DEBUG", "1")

    result = analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, _options())

    from pathlib import Path

    path = result["students"][0]["questions"][0]["answer_crop_path"]
    assert path and Path(path).is_file()


def test_crop_sent_to_the_model_is_higher_resolution_than_the_whole_page(
    sheet_pdf, spy_transcribe, stub_grading, monkeypatch
):
    """O ponto do item 4: mais pixels por milimetro de papel na area que importa."""
    monkeypatch.setenv("DEBUG", "1")
    from PIL import Image

    analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, _options())

    box = MANIFEST["pages"][0]["boxes"][0]
    with Image.open(spy_transcribe[0]["image_path"]) as crop:
        px_per_pt = crop.height / box["height_pt"]

    assert px_per_pt > 220 / 72.0


def test_without_manifest_falls_back_to_whole_page(sheet_pdf, stub_grading, monkeypatch):
    """Provas antigas, sem manifesto, continuam funcionando."""
    called: list[str] = []

    def fake_whole_page(image_path, page_number=None, context=None):
        called.append(image_path)
        return {
            "student": {"name": "ALUNO 09", "registration": "", "class": "", "student_code": "009"},
            "physical_page": page_number,
            "questions": [
                {
                    "number": 1,
                    "prompt_detected": "",
                    "answer_transcription": "leitura de pagina inteira",
                    "reading_confidence": "media",
                    "ocr_confidence": None,
                    "reading_notes": "",
                    "has_answer": True,
                    "image_region": None,
                }
            ],
            "model_used": "vision-mock",
            "fallback_used": False,
        }

    monkeypatch.setattr(vep, "extract_answers_from_page_image", fake_whole_page)

    result = analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, {"run_id": "test"})

    assert result["status"] == "success"
    assert len(called) == 1
    assert result["students"][0]["questions"][0]["answer_transcription"] == "leitura de pagina inteira"


def test_malformed_manifest_falls_back_instead_of_failing(sheet_pdf, stub_grading, monkeypatch):
    monkeypatch.setattr(
        vep,
        "extract_answers_from_page_image",
        lambda *a, **k: {"student": {}, "physical_page": 1, "questions": [], "model_used": "m", "fallback_used": False},
    )

    result = analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, _options(layout_manifest="{lixo"))

    assert result["status"] == "success"


def test_transcription_failure_on_one_question_does_not_lose_the_page(
    sheet_pdf, spy_transcribe, stub_grading, monkeypatch
):
    monkeypatch.setattr(
        vep,
        "transcribe_answer_crop",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("modelo fora do ar")),
    )

    result = analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, _options())

    assert result["status"] == "success"
    assert any("questão 1" in w or "questao 1" in w for w in result["warnings"])
    # A questão vazia continua sendo reportada mesmo com a outra falhando.
    assert {q["number"] for q in result["students"][0]["questions"]} == {1, 2}


def test_failed_reading_is_flagged_for_review_not_silently_zeroed(
    sheet_pdf, spy_transcribe, stub_grading, monkeypatch
):
    """Falha de infraestrutura nao pode virar nota zero com cara de correcao."""
    monkeypatch.setattr(
        vep,
        "transcribe_answer_crop",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("modelo fora do ar")),
    )

    result = analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, _options())

    q1 = next(q for q in result["students"][0]["questions"] if q["number"] == 1)
    assert q1["grade"]["needs_human_review"] is True
    assert q1["has_answer"] is True


def test_empty_box_is_graded_not_sent_to_review(sheet_pdf, spy_transcribe, stub_grading):
    """Caixa comprovadamente vazia e nota zero legitima, nao trabalho para o revisor."""
    result = analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, _options())

    q2 = next(q for q in result["students"][0]["questions"] if q["number"] == 2)
    assert q2["grade"]["needs_human_review"] is False
    assert q2["ink_ratio"] is not None


def test_identity_prefers_the_qr_over_regex_on_the_name(sheet_pdf, spy_transcribe, stub_grading, monkeypatch):
    class _Payload:
        exam_id = "exam-1"
        student_id = "uuid-do-aluno"
        page_in_student = 1
        total_pages_for_student = 1

    monkeypatch.setattr(vep, "decode_sheet_qr", lambda _img: _Payload())

    result = analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, _options())

    student = result["students"][0]["student"]
    assert student["qr_student_id"] == "uuid-do-aluno"
    assert student["identity_source"] == "qr"


def test_identity_falls_back_to_manifest_student_when_qr_unreadable(
    sheet_pdf, spy_transcribe, stub_grading, monkeypatch
):
    monkeypatch.setattr(vep, "decode_sheet_qr", lambda _img: None)

    result = analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, _options())

    assert result["students"][0]["student"]["qr_student_id"] == "student-1"


def test_crops_survive_the_temp_dir_cleanup(sheet_pdf, spy_transcribe, stub_grading, tmp_path):
    """Sem DEBUG o tempdir e apagado; o recorte tem de estar fora dele."""
    from pathlib import Path

    durable = tmp_path / "run" / "crops"
    result = analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, _options(crop_dir=str(durable)))

    for question in result["students"][0]["questions"]:
        path = question.get("answer_crop_path")
        assert path, f"questao {question['number']} sem recorte"
        assert Path(path).is_file(), f"recorte de {question['number']} nao sobreviveu"


def test_qr_identity_skips_the_header_model_call(sheet_pdf, spy_transcribe, stub_grading, monkeypatch):
    """Economia: se o QR resolve o aluno, nao ha por que gastar uma chamada
    pedindo ao modelo que leia o cabecalho."""
    header_calls: list[str] = []

    def spy_header(*_args, **_kwargs):
        header_calls.append("chamou")
        return {"name": "", "registration": "", "class": "", "student_code": ""}

    monkeypatch.setattr(vep, "read_sheet_header", spy_header)

    class _Payload:
        exam_id = "exam-1"
        student_id = "student-1"
        page_in_student = 1
        total_pages_for_student = 1

    monkeypatch.setattr(vep, "decode_sheet_qr", lambda _img: _Payload())

    result = analyze_discursive_exam_pdf(
        str(sheet_pdf),
        RUBRIC,
        _options(
            students_by_id={
                "student-1": {
                    "name": "Maria Silva",
                    "registration": "24102MED009",
                    "class": "T1",
                }
            }
        ),
    )

    assert header_calls == []
    student = result["students"][0]["student"]
    assert student["name"] == "Maria Silva"
    assert student["identity_source"] == "qr"


def test_header_is_read_when_the_qr_student_is_unknown(sheet_pdf, spy_transcribe, stub_grading, monkeypatch):
    """QR ilegivel ou aluno fora da lista: ai sim vale gastar a chamada."""
    monkeypatch.setattr(vep, "decode_sheet_qr", lambda _img: None)

    result = analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, _options(students_by_id={}))

    assert result["students"][0]["student"]["name"] == "ALUNO 09"


def _low_confidence_reader(texts_by_call: list[str]):
    """Leitor que devolve confianca baixa e um texto diferente a cada chamada."""
    calls: list[dict] = []

    def read(image_path, question_number=None, vision_model=None):
        index = min(len(calls), len(texts_by_call) - 1)
        calls.append({"image_path": image_path, "vision_model": vision_model})
        return {
            "number": int(question_number or 0),
            "prompt_detected": "",
            "answer_transcription": texts_by_call[index],
            "reading_confidence": "baixa",
            "ocr_confidence": None,
            "reading_notes": "",
            "has_answer": True,
            "image_region": None,
            "model_used": vision_model or "vision-mock",
            "fallback_used": False,
        }

    return read, calls


def test_escalation_is_off_by_default(sheet_pdf, spy_transcribe, stub_grading, monkeypatch):
    """Ligar por padrao multiplicaria o custo de toda prova."""
    read, calls = _low_confidence_reader(["leitura duvidosa"])
    monkeypatch.setattr(vep, "transcribe_answer_crop", read)

    result = analyze_discursive_exam_pdf(str(sheet_pdf), RUBRIC, _options())

    # Uma unica chamada para a unica questao com tinta: nada foi escalado.
    assert len(calls) == 1
    q1 = next(q for q in result["students"][0]["questions"] if q["number"] == 1)
    assert q1.get("escalated") is not True


def test_low_confidence_escalates_when_enabled(sheet_pdf, spy_transcribe, stub_grading, monkeypatch):
    read, calls = _low_confidence_reader(["actina e miosina"] * 5)
    monkeypatch.setattr(vep, "transcribe_answer_crop", read)

    result = analyze_discursive_exam_pdf(
        str(sheet_pdf), RUBRIC, _options(escalate_low_confidence=True, tta_variants=2)
    )

    assert len(calls) > 1
    q1 = next(q for q in result["students"][0]["questions"] if q["number"] == 1)
    assert q1["escalated"] is True
    # Leituras convergentes: a confianca sobe, agora ancorada em evidencia.
    assert q1["reading_confidence"] == "alta"
    assert q1["agreement_cer"] == 0.0


def test_second_opinion_uses_a_different_model_family(sheet_pdf, spy_transcribe, stub_grading, monkeypatch):
    read, calls = _low_confidence_reader(["actina"] * 5)
    monkeypatch.setattr(vep, "transcribe_answer_crop", read)

    analyze_discursive_exam_pdf(
        str(sheet_pdf),
        RUBRIC,
        _options(escalate_low_confidence=True, tta_variants=1, consensus_model="outra/familia"),
    )

    assert any(call["vision_model"] == "outra/familia" for call in calls)


def test_diverging_readings_go_to_review_with_the_alternatives(
    sheet_pdf, spy_transcribe, stub_grading, monkeypatch
):
    read, _ = _low_confidence_reader(
        ["actina e miosina", "xxxxx yyyyy zzzz", "nada parecido com aquilo"]
    )
    monkeypatch.setattr(vep, "transcribe_answer_crop", read)

    result = analyze_discursive_exam_pdf(
        str(sheet_pdf), RUBRIC, _options(escalate_low_confidence=True, tta_variants=2)
    )

    q1 = next(q for q in result["students"][0]["questions"] if q["number"] == 1)
    assert q1["reading_confidence"] == "baixa"
    assert q1["alternative_readings"]
    assert any("questão 1" in w or "questao 1" in w for w in result["warnings"])


def test_escalation_survives_a_failing_variant(sheet_pdf, spy_transcribe, stub_grading, monkeypatch):
    """Variante que falha so nao vota; nao pode derrubar a leitura."""
    calls: list[str] = []

    def flaky(image_path, question_number=None, vision_model=None):
        calls.append(image_path)
        if len(calls) > 1:
            raise RuntimeError("modelo fora do ar")
        return {
            "number": int(question_number or 0),
            "prompt_detected": "",
            "answer_transcription": "actina e miosina",
            "reading_confidence": "baixa",
            "ocr_confidence": None,
            "reading_notes": "",
            "has_answer": True,
            "image_region": None,
            "model_used": "vision-mock",
            "fallback_used": False,
        }

    monkeypatch.setattr(vep, "transcribe_answer_crop", flaky)

    result = analyze_discursive_exam_pdf(
        str(sheet_pdf), RUBRIC, _options(escalate_low_confidence=True, tta_variants=2)
    )

    assert result["status"] == "success"
    q1 = next(q for q in result["students"][0]["questions"] if q["number"] == 1)
    assert q1["answer_transcription"] == "actina e miosina"
