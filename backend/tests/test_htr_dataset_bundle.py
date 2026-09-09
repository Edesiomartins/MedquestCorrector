"""Bundle ZIP do dataset HTR: labels.jsonl + os PNGs, portatil.

O `GET /reviews/htr-dataset` exporta so o labels.jsonl, e os `crop` apontam para
caminhos internos do servidor -- o dataset nao sai da maquina. O bundle resolve
isso, e por isso mesmo e o ponto onde dado de aluno pode vazar para fora: o
arquivo e feito para ser baixado, copiado e mandado para outra maquina.

As invariantes abaixo sao o que separa "dataset portatil" de "vazamento":

1. nada de identificacao pessoal entra no ZIP (lista BRANCA de campos);
2. nenhum caminho absoluto do servidor entra no ZIP;
3. path traversal nao le arquivo fora da area de upload;
4. um recorte sumido nao derruba a exportacao inteira;
5. exam_id e obrigatorio -- nao existe exportacao global;
6. a rota continua exigindo autenticacao.
"""

import io
import json
import zipfile
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.storage import upload_root
from app.services.htr_dataset_bundle import build_bundle


@pytest.fixture
def crops(tmp_path, monkeypatch):
    """Area de upload isolada, com dois recortes reais gravados."""
    from app.core import storage

    monkeypatch.setattr(storage.settings, "UPLOAD_DIR", tmp_path)
    batch = uuid4()
    written = []
    for page, question in ((1, 1), (1, 2)):
        rel = f"crops/{batch}/p{page:03d}_q{question:03d}.png"
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\x89PNG\r\n\x1a\n fake")
        written.append(f"local:{rel}")
    return written


def _row(crop, **overrides):
    row = {
        "crop": crop,
        "reference": "actina e miosina",
        "strata": ["confirmada"],
        "question": 1,
        "model_transcription": "octina e miosino",
        "cer_at_review": 0.12,
    }
    row.update(overrides)
    return row


def _open(payload: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(payload))


def _labels(payload: bytes) -> list[dict]:
    with _open(payload) as archive:
        text = archive.read("labels.jsonl").decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# --- estrutura do ZIP ----------------------------------------------------------


def test_bundle_contains_labels_and_every_crop(crops):
    exam_id = uuid4()

    payload, manifest = build_bundle([_row(crops[0]), _row(crops[1])], exam_id=exam_id)

    with _open(payload) as archive:
        names = archive.namelist()
    assert "labels.jsonl" in names
    assert "manifest.json" in names
    assert "crops/crop_000001.png" in names
    assert "crops/crop_000002.png" in names
    assert manifest["samples_exported"] == 2


def test_crop_paths_are_relative_and_portable(crops):
    payload, _ = build_bundle([_row(crops[0]), _row(crops[1])], exam_id=uuid4())

    assert [row["crop"] for row in _labels(payload)] == [
        "crops/crop_000001.png",
        "crops/crop_000002.png",
    ]


def test_no_server_path_survives_into_the_bundle(crops):
    payload, _ = build_bundle([_row(crops[0])], exam_id=uuid4())

    with _open(payload) as archive:
        text = archive.read("labels.jsonl").decode("utf-8")
    assert "local:" not in text
    assert str(upload_root()) not in text
    assert "\\" not in text  # nenhum separador do Windows vazou


def test_the_human_reference_is_never_used_as_a_file_name(crops):
    """Nome de arquivo derivado da resposta vazaria o texto do aluno na listagem do ZIP."""
    payload, _ = build_bundle(
        [_row(crops[0], reference="paciente Maria Silva com HAS")], exam_id=uuid4()
    )

    with _open(payload) as archive:
        names = archive.namelist()
    assert names == ["labels.jsonl", "manifest.json", "crops/crop_000001.png"] or set(names) == {
        "labels.jsonl",
        "manifest.json",
        "crops/crop_000001.png",
    }
    assert not any("maria" in name.lower() for name in names)


# --- dados pessoais ------------------------------------------------------------


def test_personal_fields_never_reach_the_bundle(crops):
    """Lista BRANCA: campo novo no export nao vaza sozinho amanha."""
    payload, _ = build_bundle(
        [
            _row(
                crops[0],
                student_id=str(uuid4()),
                student_name="Maria Silva",
                registration="24102MED009",
                email="maria@exemplo.com",
                expected_answer="deslizamento dos filamentos",
                correction_criteria="cita actina e miosina",
                rubric="2 pontos",
            )
        ],
        exam_id=uuid4(),
    )

    with _open(payload) as archive:
        text = archive.read("labels.jsonl").decode("utf-8")
        manifest = archive.read("manifest.json").decode("utf-8")

    for leak in ("student_id", "Maria Silva", "24102MED009", "maria@exemplo.com",
                 "expected_answer", "correction_criteria", "rubric", "deslizamento"):
        assert leak not in text, leak
        assert leak not in manifest, leak

    assert set(_labels(payload)[0]) == {
        "crop", "reference", "question", "strata", "model_transcription", "cer_at_review",
    }


# --- robustez e seguranca ------------------------------------------------------


def test_a_missing_crop_is_skipped_and_counted_not_fatal(crops):
    payload, manifest = build_bundle(
        [_row(crops[0]), _row("local:crops/sumiu/p001_q001.png"), _row(crops[1])],
        exam_id=uuid4(),
    )

    assert manifest["samples_exported"] == 2
    assert manifest["missing_crops"] == 1
    assert len(_labels(payload)) == 2


@pytest.mark.parametrize(
    "hostile",
    [
        "local:../../../../etc/passwd",
        "local:/etc/passwd",
        "local:C:/Windows/win.ini",
        "batch=abc/page=1/q=1",  # formato antigo, nem e URL local
        "file:///etc/passwd",
    ],
)
def test_path_traversal_and_foreign_paths_are_rejected(crops, hostile):
    payload, manifest = build_bundle([_row(hostile), _row(crops[0])], exam_id=uuid4())

    with _open(payload) as archive:
        names = archive.namelist()
    assert names.count("crops/crop_000001.png") == 1
    assert len([n for n in names if n.startswith("crops/")]) == 1
    assert manifest["samples_exported"] == 1
    assert manifest["missing_crops"] == 1


def test_manifest_reports_the_shape_of_the_dataset(crops):
    exam_id = uuid4()

    _, manifest = build_bundle(
        [_row(crops[0], reference=""), _row(crops[1])], exam_id=exam_id
    )

    assert manifest["exam_id"] == str(exam_id)
    assert manifest["samples_exported"] == 2
    assert manifest["empty_references"] == 1
    assert manifest["missing_crops"] == 0
    assert manifest["created_at"]


# --- o bundle alimenta o benchmark sem adaptacao -------------------------------


def test_the_extracted_bundle_feeds_the_benchmark_unchanged(crops, tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import benchmark_htr_models as bench

    payload, _ = build_bundle([_row(crops[0]), _row(crops[1])], exam_id=uuid4())
    extracted = tmp_path / "bundle"
    with _open(payload) as archive:
        archive.extractall(extracted)

    tasks = bench.load_tasks(extracted / "labels.jsonl")

    assert [task.crop for task in tasks] == ["crops/crop_000001.png", "crops/crop_000002.png"]
    assert all(task.path.is_file() for task in tasks)


# --- isolamento entre provas ---------------------------------------------------


class _Label:
    def __init__(self, exam_id, human, crop="local:crops/a/p001_q001.png"):
        self.exam_id = exam_id
        self.answer_crop_path = crop
        self.human_transcription = human
        self.model_transcription = "x"
        self.was_correct = True
        self.character_error_rate = 0.0
        self.question_number = 1


class _FilteringDB:
    """Banco falso que APLICA o criterio do filtro, em vez de engoli-lo.

    O `_QueryDB` de test_htr_labeling ignora o criterio, entao nada ali prova
    que `export_dataset` isola por prova — e o bundle depende disso.
    """

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


def test_export_dataset_really_filters_by_exam(crops):
    from app.services.htr_labeling import export_dataset

    wanted, other = uuid4(), uuid4()
    db = _FilteringDB([_Label(wanted, "da prova pedida"), _Label(other, "da OUTRA prova")])

    rows = export_dataset(db, exam_id=wanted)

    assert [row["reference"] for row in rows] == ["da prova pedida"]


# --- endpoint ------------------------------------------------------------------


@pytest.fixture
def api(crops):
    """App minimo com o router de reviews e um banco falso em memoria."""
    from app.api.v1 import reviews

    rows_by_exam: dict = {}

    def fake_export_dataset(db, *, exam_id=None, limit=None):
        assert exam_id is not None, "exportacao global nao pode acontecer"
        return list(rows_by_exam.get(str(exam_id), []))

    app = FastAPI()
    app.include_router(reviews.router, prefix="/reviews")
    app.dependency_overrides[get_db] = lambda: None
    client = TestClient(app)
    return client, app, rows_by_exam, fake_export_dataset, reviews


def test_bundle_endpoint_requires_authentication(api):
    client, _, _, _, _ = api

    assert client.get("/reviews/htr-dataset-bundle", params={"exam_id": str(uuid4())}).status_code == 401


def test_bundle_endpoint_requires_exam_id(api, monkeypatch):
    client, app, _, fake_export, reviews = api
    app.dependency_overrides[get_current_user] = lambda: object()
    monkeypatch.setattr(reviews, "export_dataset", fake_export)

    assert client.get("/reviews/htr-dataset-bundle").status_code == 422


def test_bundle_endpoint_returns_only_the_requested_exam(api, monkeypatch, crops):
    client, app, rows_by_exam, fake_export, reviews = api
    app.dependency_overrides[get_current_user] = lambda: object()
    monkeypatch.setattr(reviews, "export_dataset", fake_export)
    wanted, other = uuid4(), uuid4()
    rows_by_exam[str(wanted)] = [_row(crops[0], reference="da prova pedida")]
    rows_by_exam[str(other)] = [_row(crops[1], reference="da OUTRA prova")]

    response = client.get("/reviews/htr-dataset-bundle", params={"exam_id": str(wanted)})

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert f"medquest_htr_dataset_{wanted}.zip" in response.headers["content-disposition"]
    text = _open(response.content).read("labels.jsonl").decode("utf-8")
    assert "da prova pedida" in text
    assert "da OUTRA prova" not in text


def test_the_existing_labels_endpoint_is_untouched(api, monkeypatch):
    """Regressao: a rota antiga continua devolvendo ndjson, nao ZIP."""
    client, app, _, _, reviews = api
    app.dependency_overrides[get_current_user] = lambda: object()
    monkeypatch.setattr(reviews, "export_dataset", lambda db, **kw: [{"crop": "local:x.png"}])

    response = client.get("/reviews/htr-dataset")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
