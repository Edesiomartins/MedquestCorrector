"""Item 13 do docs/HTR_PLANO_EXECUCAO.md: o ciclo de rotulagem.

Toda vez que o professor corrige uma transcricao na revisao, ele produz sem
esforco adicional o dado mais caro deste dominio: um par
(recorte, o que o modelo leu, o que esta escrito de fato). Hoje essa informacao
era sobrescrita e sumia.
"""

from uuid import uuid4

import pytest

from app.services.htr_labeling import export_dataset, record_review


class _FakeScore:
    """QuestionScore o bastante para o servico, sem tocar no banco."""

    def __init__(self, *, crop="local:crops/x/p001_q001.png", model_text="actina e miosina"):
        self.id = uuid4()
        self.answer_crop_path = crop
        self.extracted_answer_text = model_text
        self.source_question_number = 1
        self.source_page_number = 3
        self.transcription_confidence = 0.91
        self.ocr_provider = "openrouter"


class _FakeDB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


@pytest.fixture
def db():
    return _FakeDB()


# --- gravacao -----------------------------------------------------------------


def test_correction_is_recorded_as_a_labeled_pair(db):
    label = record_review(
        db, question_score=_FakeScore(model_text="octina e miosino"), human_transcription="actina e miosina"
    )

    assert label is not None
    assert db.added == [label]
    assert label.model_transcription == "octina e miosino"
    assert label.human_transcription == "actina e miosina"
    assert label.was_correct is False
    assert label.character_error_rate > 0


def test_confirmation_is_recorded_too(db):
    """So gravar correcoes enviesaria o conjunto: todo exemplo seria um erro."""
    label = record_review(
        db, question_score=_FakeScore(model_text="actina e miosina"), human_transcription="actina e miosina"
    )

    assert label.was_correct is True
    assert label.character_error_rate == 0.0


def test_confirmation_ignores_whitespace_and_case():
    db = _FakeDB()
    label = record_review(
        db,
        question_score=_FakeScore(model_text="Actina  e\nmiosina"),
        human_transcription="actina e miosina",
    )

    assert label.was_correct is True


def test_confirmed_empty_box_is_a_valid_label(db):
    """E justamente o caso que mede alucinacao."""
    label = record_review(db, question_score=_FakeScore(model_text=""), human_transcription="")

    assert label is not None
    assert label.was_correct is True
    assert label.human_transcription == ""


def test_hallucination_is_recorded_as_a_correction(db):
    """Modelo escreveu onde nao havia nada: o par mais valioso do conjunto."""
    label = record_review(
        db, question_score=_FakeScore(model_text="inventou uma resposta"), human_transcription=""
    )

    assert label.was_correct is False
    assert label.character_error_rate == pytest.approx(1.0)


def test_no_crop_means_no_label(db):
    """Rotulo sem recorte e uma linha de texto sem contexto: nao e auditavel."""
    assert record_review(db, question_score=_FakeScore(crop=None), human_transcription="actina") is None
    assert db.added == []


def test_metadata_is_carried_for_later_analysis(db):
    exam_id, student_id, reviewer_id = uuid4(), uuid4(), uuid4()

    label = record_review(
        db,
        question_score=_FakeScore(),
        human_transcription="actina e miosina",
        exam_id=exam_id,
        student_id=student_id,
        reviewer_id=reviewer_id,
        vision_model="qwen/qwen2.5-vl-72b-instruct",
    )

    assert label.exam_id == exam_id
    assert label.student_id == student_id
    assert label.reviewer_id == reviewer_id
    assert label.vision_model == "qwen/qwen2.5-vl-72b-instruct"
    assert label.question_number == 1
    assert label.page_number == 3


@pytest.mark.parametrize(
    "numeric,expected",
    [(0.95, "alta"), (0.70, "media"), (0.30, "baixa"), (None, None)],
)
def test_numeric_confidence_becomes_a_label(numeric, expected):
    db = _FakeDB()
    score = _FakeScore()
    score.transcription_confidence = numeric

    label = record_review(db, question_score=score, human_transcription="actina")

    assert label.reading_confidence == expected


def test_service_does_not_commit(db):
    """Quem chama decide o limite da transacao, que inclui a nota."""
    record_review(db, question_score=_FakeScore(), human_transcription="actina")

    assert not hasattr(db, "committed")


# --- exportacao ---------------------------------------------------------------


class _Label:
    def __init__(self, human, model="x", correct=False, cer=0.5, question=1):
        self.answer_crop_path = "local:crops/a/p001_q001.png"
        self.human_transcription = human
        self.model_transcription = model
        self.was_correct = correct
        self.character_error_rate = cer
        self.question_number = question


class _QueryDB:
    def __init__(self, rows):
        self.rows = rows

    def query(self, _model):
        return self

    def filter(self, *_a, **_k):
        return self

    def order_by(self, *_a, **_k):
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    def all(self):
        return self.rows


def test_export_uses_the_eval_harness_format():
    db = _QueryDB([_Label("actina e miosina deslizam")])

    rows = export_dataset(db)

    assert set(rows[0]) >= {"crop", "reference", "strata"}
    assert rows[0]["reference"] == "actina e miosina deslizam"


def test_export_marks_corrected_and_confirmed_separately():
    db = _QueryDB([_Label("actina", correct=True), _Label("miosina", correct=False)])

    rows = export_dataset(db)

    assert "confirmada" in rows[0]["strata"]
    assert "corrigida" in rows[1]["strata"]


def test_export_infers_the_strata_it_can():
    db = _QueryDB([_Label(""), _Label("nao sei"), _Label("uma resposta bem mais longa que tres palavras")])

    rows = export_dataset(db)

    assert "vazia" in rows[0]["strata"]
    assert "curta" in rows[1]["strata"]
    assert "curta" not in rows[2]["strata"]


def test_export_respects_the_limit():
    db = _QueryDB([_Label(f"resposta {i}") for i in range(10)])

    assert len(export_dataset(db, limit=3)) == 3


class _ExamLabel:
    def __init__(self, exam_id, human, crop="local:crops/a/p001_q001.png"):
        self.exam_id = exam_id
        self.answer_crop_path = crop
        self.human_transcription = human
        self.model_transcription = "x"
        self.was_correct = True
        self.character_error_rate = 0.0
        self.question_number = 1


class _FilteringDB:
    """Aplica o filtro de exam_id; o `_QueryDB` acima ignora o critério."""

    def __init__(self, labels):
        self.labels = list(labels)

    def query(self, _model):
        return self

    def filter(self, criterion):
        wanted = criterion.right.value
        field = criterion.left.key
        self.labels = [label for label in self.labels if getattr(label, field) == wanted]
        return self

    def order_by(self, *_a, **_k):
        return self

    def limit(self, n):
        self.labels = self.labels[:n]
        return self

    def all(self):
        return self.labels


def test_export_dataset_returns_only_labels_of_the_requested_exam():
    wanted, other = uuid4(), uuid4()
    db = _FilteringDB(
        [
            _ExamLabel(wanted, "da prova X"),
            _ExamLabel(other, "da prova Y"),
            _ExamLabel(None, "sem prova"),
        ]
    )

    rows = export_dataset(db, exam_id=wanted)

    assert [row["reference"] for row in rows] == ["da prova X"]


def test_export_dataset_does_not_leak_labels_from_another_exam():
    exam_x, exam_y = uuid4(), uuid4()
    db = _FilteringDB([_ExamLabel(exam_x, "rótulo X"), _ExamLabel(exam_y, "rótulo Y")])

    rows = export_dataset(db, exam_id=exam_y)

    assert [row["reference"] for row in rows] == ["rótulo Y"]
    assert all("rótulo X" not in row["reference"] for row in rows)


# --- update_score persiste exam_id do lote ------------------------------------


class _First:
    def __init__(self, value):
        self.value = value

    def filter(self, *_a, **_k):
        return self

    def first(self):
        return self.value


class _ReviewSession:
    def __init__(self, mapping):
        self.mapping = mapping
        self.added = []
        self.commits = 0

    def query(self, model):
        return _First(self.mapping[model])

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


def _score(*, model_text="octina e miosino", crop="local:crops/x/p001_q001.png"):
    score = _FakeScore(crop=crop, model_text=model_text)
    score.student_result_id = uuid4()
    score.ai_score = 0.0
    score.final_score = None
    score.professor_comment = None
    score.requires_manual_review = True
    score.manual_review_reason = "baixa confiança"
    return score


def _student_result(*, exam_batch_id, student_id=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=uuid4(),
        batch_id=exam_batch_id,
        student_id=student_id or uuid4(),
        total_score=0.0,
    )


def _batch(*, exam_id, batch_id=None):
    from types import SimpleNamespace

    return SimpleNamespace(id=batch_id or uuid4(), exam_id=exam_id)


def _call_update_score(monkeypatch, session, score, payload):
    from app.api.v1 import reviews
    from app.models.grading import QuestionScore
    from app.schemas.review import UpdateScore

    monkeypatch.setattr(reviews, "_recalc_total", lambda *_a, **_k: None)
    monkeypatch.setattr(reviews, "_batch_completion_recheck", lambda *_a, **_k: None)
    session.mapping[QuestionScore] = score
    return reviews.update_score(
        score_id=score.id,
        payload=payload if isinstance(payload, UpdateScore) else UpdateScore(**payload),
        db=session,
    )


def test_update_score_records_htr_label_with_exam_id_from_the_batch(monkeypatch):
    from app.models.grading import StudentResult
    from app.models.pipeline import UploadBatch

    exam_id = uuid4()
    batch = _batch(exam_id=exam_id)
    score = _score()
    sr = _student_result(exam_batch_id=batch.id)
    session = _ReviewSession({StudentResult: sr, UploadBatch: batch})

    result = _call_update_score(
        monkeypatch,
        session,
        score,
        {"final_score": 1.5, "extracted_answer_text": "actina e miosina"},
    )

    assert result == {"status": "ok"}
    assert session.commits >= 1
    assert len(session.added) == 1
    label = session.added[0]
    assert label.exam_id == exam_id
    assert label.student_id == sr.student_id
    assert label.human_transcription == "actina e miosina"
    assert label.model_transcription == "octina e miosino"
    assert label.was_correct is False


def test_update_score_empty_box_still_creates_a_label_with_exam_id(monkeypatch):
    from app.models.grading import StudentResult
    from app.models.pipeline import UploadBatch

    exam_id = uuid4()
    batch = _batch(exam_id=exam_id)
    score = _score(model_text="")
    sr = _student_result(exam_batch_id=batch.id)
    session = _ReviewSession({StudentResult: sr, UploadBatch: batch})

    _call_update_score(
        monkeypatch,
        session,
        score,
        {"final_score": 0.0, "extracted_answer_text": ""},
    )

    label = session.added[0]
    assert label.exam_id == exam_id
    assert label.human_transcription == ""
    assert label.was_correct is True


def test_update_score_does_not_take_exam_id_from_the_payload(monkeypatch):
    from app.models.grading import StudentResult
    from app.models.pipeline import UploadBatch
    from app.schemas.review import UpdateScore

    exam_id = uuid4()
    batch = _batch(exam_id=exam_id)
    score = _score()
    sr = _student_result(exam_batch_id=batch.id)
    session = _ReviewSession({StudentResult: sr, UploadBatch: batch})
    payload = UpdateScore.model_validate(
        {
            "final_score": 1.0,
            "extracted_answer_text": "actina",
            "exam_id": str(uuid4()),
        }
    )

    _call_update_score(monkeypatch, session, score, payload)

    assert session.added[0].exam_id == exam_id
    assert not hasattr(payload, "exam_id") or payload.model_dump().get("exam_id") is None


def test_labels_recorded_on_exam_x_are_not_exported_for_exam_y(monkeypatch):
    from app.models.grading import StudentResult
    from app.models.pipeline import UploadBatch

    exam_x, exam_y = uuid4(), uuid4()
    batch = _batch(exam_id=exam_x)
    score = _score()
    sr = _student_result(exam_batch_id=batch.id)
    session = _ReviewSession({StudentResult: sr, UploadBatch: batch})

    _call_update_score(
        monkeypatch,
        session,
        score,
        {"final_score": 1.0, "extracted_answer_text": "actina e miosina"},
    )
    recorded = session.added[0]
    db = _FilteringDB(
        [
            _ExamLabel(recorded.exam_id, recorded.human_transcription),
            _ExamLabel(exam_y, "rótulo da prova Y"),
        ]
    )

    rows_x = export_dataset(db, exam_id=exam_x)
    db_y = _FilteringDB(
        [
            _ExamLabel(recorded.exam_id, recorded.human_transcription),
            _ExamLabel(exam_y, "rótulo da prova Y"),
        ]
    )
    rows_y = export_dataset(db_y, exam_id=exam_y)

    assert [row["reference"] for row in rows_x] == ["actina e miosina"]
    assert [row["reference"] for row in rows_y] == ["rótulo da prova Y"]


def test_update_score_does_not_create_htr_label_without_exam_id(monkeypatch, caplog):
    import logging

    from app.models.grading import StudentResult
    from app.models.pipeline import UploadBatch

    score = _score()
    sr = _student_result(exam_batch_id=uuid4())
    session = _ReviewSession({StudentResult: sr, UploadBatch: None})
    caplog.set_level(logging.ERROR, logger="app.api.v1.reviews")

    result = _call_update_score(
        monkeypatch,
        session,
        score,
        {"final_score": 1.5, "extracted_answer_text": "actina e miosina"},
    )

    assert result == {"status": "ok"}
    assert session.added == []
    assert score.final_score == 1.5
    assert score.extracted_answer_text == "actina e miosina"
    assert score.transcription_edited_by_human is True
    assert score.requires_manual_review is False
    assert any("ausência de exam_id" in record.getMessage() for record in caplog.records)
