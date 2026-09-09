"""Benchmark de modelos VLM (scripts/benchmark_htr_models.py).

Um benchmark que mente e pior que nenhum: ele produz um numero, o numero vira
decisao, e a decisao fica errada por meses. As invariantes abaixo sao o que
separa "medimos modelos" de "medimos qualquer outra coisa":

1. todos os modelos veem EXATAMENTE os mesmos recortes;
2. o modelo pedido e o modelo chamado;
3. nao ha fallback silencioso -- modelo que falha entra na tabela como falha;
4. a falha de um modelo nao derruba o resto da bateria;
5. a referencia humana NUNCA chega ao prompt (transcricao cega).
"""

import csv
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import benchmark_htr_models as bench  # noqa: E402
from app.services import openrouter_vision_client as vc  # noqa: E402


@pytest.fixture
def dataset(tmp_path):
    """Tres recortes: dois com resposta, um em branco (o que mede alucinacao)."""
    crops = tmp_path / "crops"
    crops.mkdir()
    rows = [
        {"crop": "a.png", "reference": "actina e miosina", "strata": ["bastao"], "question": 1},
        {"crop": "b.png", "reference": "miosina", "strata": ["cursiva_ligada"], "question": 2},
        {"crop": "c.png", "reference": "", "strata": ["vazia"], "question": 3},
    ]
    for row in rows:
        Image.new("RGB", (400, 120), (240, 240, 240)).save(crops / row["crop"])
    labels = tmp_path / "labels.jsonl"
    labels.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    return labels, crops


def _reader(answers: dict, *, fails=frozenset()):
    """Transcritor falso: mapeia nome do recorte -> transcricao devolvida."""
    seen: list[tuple[str, str]] = []

    def transcriber(crop_path, question_number, model):
        seen.append((model, Path(crop_path).name))
        if model in fails:
            raise RuntimeError("503 do provedor")
        return {
            "answer_transcription": answers.get(Path(crop_path).name, ""),
            "reading_confidence": "alta",
            "model_used": model,
        }

    transcriber.seen = seen
    return transcriber


# --- 1. mesmos recortes para todos os modelos ---------------------------------


def test_every_model_sees_exactly_the_same_crops(dataset, tmp_path):
    labels, crops = dataset
    reader = _reader({"a.png": "actina e miosina", "b.png": "miosina"})

    bench.run_benchmark(
        tasks=bench.load_tasks(labels, crops),
        models=["modelo/a", "modelo/b"],
        results_dir=tmp_path / "results",
        transcriber=reader,
    )

    by_model: dict[str, list[str]] = {}
    for model, crop in reader.seen:
        by_model.setdefault(model, []).append(crop)
    assert by_model["modelo/a"] == by_model["modelo/b"] == ["a.png", "b.png", "c.png"]


def test_empty_boxes_reach_the_model_so_hallucination_can_be_measured(dataset, tmp_path):
    """Curto-circuitar caixa vazia pelo detector de tinta zeraria a metrica n. 1."""
    labels, crops = dataset
    reader = _reader({})

    bench.run_benchmark(
        tasks=bench.load_tasks(labels, crops),
        models=["modelo/a"],
        results_dir=tmp_path / "results",
        transcriber=reader,
    )

    assert ("modelo/a", "c.png") in reader.seen


# --- 2 e 3. o modelo pedido e o modelo chamado, sem fallback ------------------


def test_the_requested_model_is_the_one_actually_called(dataset, tmp_path, monkeypatch):
    """Sem transcritor falso: exercita o caminho real ate a camada HTTP."""
    labels, crops = dataset
    models_called: list[str] = []
    monkeypatch.setattr(vc.settings, "OPENROUTER_API_KEY", "test-key")

    def fake_http(model, prompt, data_url, json_mode=True):
        models_called.append(model)
        return "<TRANSCRICAO>actina</TRANSCRICAO><CONFIANCA>alta</CONFIANCA>"

    monkeypatch.setattr(vc, "_call_openrouter_vision", fake_http)

    bench.run_benchmark(
        tasks=bench.load_tasks(labels, crops),
        models=["qwen/qwen3-vl-32b-instruct"],
        results_dir=tmp_path / "results",
    )

    assert set(models_called) == {"qwen/qwen3-vl-32b-instruct"}


def test_no_silent_fallback_a_failing_model_is_recorded_as_failing(dataset, tmp_path, monkeypatch):
    labels, crops = dataset
    models_called: list[str] = []
    monkeypatch.setattr(vc.settings, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(vc.settings, "OPENROUTER_VISION_FALLBACKS", "google/gemini-2.5-flash")

    def always_down(model, prompt, data_url, json_mode=True):
        models_called.append(model)
        raise RuntimeError("503")

    monkeypatch.setattr(vc, "_call_openrouter_vision", always_down)

    summaries = bench.run_benchmark(
        tasks=bench.load_tasks(labels, crops),
        models=["modelo/quebrado"],
        results_dir=tmp_path / "results",
    )

    assert set(models_called) == {"modelo/quebrado"}
    assert summaries[0]["api_failures"] == 3


# --- 4. a falha de um modelo nao derruba a bateria -----------------------------


def test_a_broken_model_does_not_stop_the_others(dataset, tmp_path):
    labels, crops = dataset
    reader = _reader({"a.png": "actina e miosina", "b.png": "miosina"}, fails={"modelo/quebrado"})

    summaries = bench.run_benchmark(
        tasks=bench.load_tasks(labels, crops),
        models=["modelo/quebrado", "modelo/bom"],
        results_dir=tmp_path / "results",
        transcriber=reader,
    )

    by_model = {s["model"]: s for s in summaries}
    assert by_model["modelo/quebrado"]["api_failures"] == 3
    assert by_model["modelo/bom"]["api_failures"] == 0
    assert by_model["modelo/bom"]["perfect_reads"] == 2


def test_a_single_broken_crop_does_not_stop_the_remaining_crops(dataset, tmp_path):
    labels, crops = dataset

    def transcriber(crop_path, question_number, model):
        if Path(crop_path).name == "a.png":
            raise RuntimeError("timeout")
        return {
            "answer_transcription": "miosina",
            "reading_confidence": "alta",
            "model_used": model,
        }

    summaries = bench.run_benchmark(
        tasks=bench.load_tasks(labels, crops),
        models=["modelo/a"],
        results_dir=tmp_path / "results",
        transcriber=transcriber,
    )

    assert summaries[0]["api_failures"] == 1
    assert summaries[0]["samples"] == 3


# --- 5. transcricao cega -------------------------------------------------------


def test_the_human_reference_never_reaches_the_prompt(dataset, tmp_path, monkeypatch):
    labels, crops = dataset
    prompts: list[str] = []
    monkeypatch.setattr(vc.settings, "OPENROUTER_API_KEY", "test-key")

    def fake_http(model, prompt, data_url, json_mode=True):
        prompts.append(prompt)
        return "<TRANSCRICAO>x</TRANSCRICAO>"

    monkeypatch.setattr(vc, "_call_openrouter_vision", fake_http)

    bench.run_benchmark(
        tasks=bench.load_tasks(labels, crops),
        models=["modelo/a"],
        results_dir=tmp_path / "results",
    )

    assert prompts
    for prompt in prompts:
        lowered = prompt.lower()
        assert "actina e miosina" not in lowered
        assert "gabarito" not in lowered
        assert "resposta esperada" not in lowered


# --- metricas e saidas ---------------------------------------------------------


def test_metrics_match_the_known_dataset(dataset, tmp_path):
    labels, crops = dataset
    # a.png perfeito, b.png nao lido, c.png (vazia) alucinada.
    reader = _reader({"a.png": "actina e miosina", "b.png": "", "c.png": "inventou"})

    summaries = bench.run_benchmark(
        tasks=bench.load_tasks(labels, crops),
        models=["modelo/a"],
        results_dir=tmp_path / "results",
        transcriber=reader,
    )
    summary = summaries[0]

    assert summary["samples"] == 3
    assert summary["perfect_reads"] == 1
    assert summary["perfect_read_rate"] == pytest.approx(0.5)
    assert summary["missed_answers"] == 1
    assert summary["empty_boxes"] == 1
    assert summary["hallucinated_empty"] == 1
    assert summary["hallucination_rate"] == pytest.approx(1.0)
    # `Report.as_dict()` arredonda em 4 casas — mesma convencao do eval_htr.
    assert summary["cer"] == pytest.approx((0.0 + 1.0 + 1.0) / 3, abs=1e-4)


def test_per_model_jsonl_records_one_auditable_line_per_crop(dataset, tmp_path):
    labels, crops = dataset
    reader = _reader({"a.png": "actina e miosina", "b.png": "miosina"})

    bench.run_benchmark(
        tasks=bench.load_tasks(labels, crops),
        models=["google/gemma-4-31b-it:free"],
        results_dir=tmp_path / "results",
        transcriber=reader,
    )

    path = tmp_path / "results" / "gemma-4-31b-it.jsonl"
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [r["crop"] for r in rows] == ["a.png", "b.png", "c.png"]
    assert rows[0]["reference"] == "actina e miosina"
    assert rows[0]["hypothesis"] == "actina e miosina"
    assert rows[0]["confidence"] == "alta"
    assert rows[0]["model"] == "google/gemma-4-31b-it:free"
    assert rows[0]["elapsed_seconds"] >= 0
    assert rows[0]["error"] is None


def test_ranking_puts_hallucination_before_cer():
    """Velocidade nao decide. Alucinacao decide, depois CER."""
    summaries = [
        {
            "model": "rapido", "hallucination_rate": 0.5, "cer": 0.05,
            "perfect_read_rate": 0.9, "missed_answers": 0, "avg_latency_seconds": 0.1,
        },
        {
            "model": "honesto", "hallucination_rate": 0.0, "cer": 0.20,
            "perfect_read_rate": 0.4, "missed_answers": 2, "avg_latency_seconds": 9.0,
        },
    ]

    assert [s["model"] for s in bench.rank(summaries)] == ["honesto", "rapido"]


def test_ranking_tiebreaks_by_cer_then_perfect_reads_then_missed():
    summaries = [
        {
            "model": "c", "hallucination_rate": 0.0, "cer": 0.1,
            "perfect_read_rate": 0.5, "missed_answers": 3, "avg_latency_seconds": 1.0,
        },
        {
            "model": "b", "hallucination_rate": 0.0, "cer": 0.1,
            "perfect_read_rate": 0.5, "missed_answers": 1, "avg_latency_seconds": 1.0,
        },
        {
            "model": "a", "hallucination_rate": 0.0, "cer": 0.1,
            "perfect_read_rate": 0.8, "missed_answers": 9, "avg_latency_seconds": 1.0,
        },
    ]

    assert [s["model"] for s in bench.rank(summaries)] == ["a", "b", "c"]


def test_summary_files_are_written_with_the_documented_csv_columns(dataset, tmp_path):
    labels, crops = dataset
    reader = _reader({"a.png": "actina e miosina", "b.png": "miosina"})
    results = tmp_path / "results"

    summaries = bench.run_benchmark(
        tasks=bench.load_tasks(labels, crops),
        models=["modelo/a"],
        results_dir=results,
        transcriber=reader,
    )
    bench.write_summary(summaries, results, dataset_info={"labels": str(labels), "samples": 3})

    payload = json.loads((results / "benchmark_summary.json").read_text(encoding="utf-8"))
    assert payload["ranking"][0]["model"] == "modelo/a"
    assert payload["models"][0]["by_stratum"]["bastao"]["samples"] == 1

    with (results / "benchmark_summary.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert list(rows[0])[:13] == [
        "model",
        "samples",
        "cer",
        "cer_no_accents",
        "wer",
        "perfect_reads",
        "perfect_read_rate",
        "missed_answers",
        "empty_boxes",
        "hallucinated_empty",
        "hallucination_rate",
        "avg_latency_seconds",
        "api_failures",
    ]


def test_model_slug_keeps_distinct_models_distinct():
    assert bench.model_slug("google/gemma-4-31b-it:free") == "gemma-4-31b-it"
    slugs = bench.unique_slugs(["google/gemma-4-31b-it:free", "other/gemma-4-31b-it"])
    assert len(set(slugs.values())) == 2
