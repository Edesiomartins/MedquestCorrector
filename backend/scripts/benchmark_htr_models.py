#!/usr/bin/env python
"""Compara modelos de visao (VLM) na leitura de manuscrito, no MESMO conjunto.

Complementa `scripts/eval_htr.py`: aquele mede UMA configuracao, este compara
VARIAS. Toda a metrica vem de `app/services/vision/htr_metrics.py` e a leitura
do dataset vem de `eval_htr`, para que os numeros do benchmark e os da avaliacao
de rotina sejam a mesma coisa.

Tres decisoes de projeto sustentam a validade do experimento:

1. **Sem fallback.** `transcribe_answer_crop(..., allow_fallback=False)`. Se o
   modelo A falhar e a cadeia cair para o B, a tabela registraria o resultado do
   B na linha do A -- o unico erro que torna o benchmark pior que nenhum.
2. **Sem detector de tinta, por padrao.** Em producao `detect_ink()` resolve
   caixa vazia antes de gastar chamada. Aqui isso zeraria a alucinacao de TODOS
   os modelos, que e justamente o criterio de desempate numero um. As caixas
   vazias vao para o modelo de proposito. `--use-ink-detector` reproduz o
   caminho de producao quando o que se quer medir e o pipeline, nao o modelo.
3. **Mesmo prompt para todos.** `ANSWER_TRANSCRIPTION_PROMPT` nao e tocado.
   Mudar modelo e prompt ao mesmo tempo nao permite atribuir a diferenca a
   nenhum dos dois.

A transcricao continua CEGA: a referencia humana vai para o arquivo de
resultado, nunca para o prompt.

Uso
---
    python scripts/benchmark_htr_models.py \\
        --labels eval/labels.jsonl \\
        --crops eval/crops \\
        --models "google/gemma-4-31b-it:free" "qwen/qwen3-vl-32b-instruct" \\
                 "google/gemini-2.5-flash"

    # so mostra quantas chamadas seriam feitas, sem gastar nada
    python scripts/benchmark_htr_models.py --labels eval/labels.jsonl --models a b --dry-run

Saidas em `eval/results/`: um `.jsonl` por modelo, mais `benchmark_summary.json`
e `benchmark_summary.csv`.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.services.vision.htr_metrics import Report, Sample, evaluate  # noqa: E402
from eval_htr import read_jsonl  # noqa: E402

# Ordem das colunas do CSV. Documentada em docs/HTR_MODEL_BENCHMARK.md; quem
# consumir a planilha depende dela, entao nao reordene sem avisar.
CSV_COLUMNS = [
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
    "confidence_cer_correlation",
]


@dataclass(frozen=True)
class CropTask:
    """Um recorte do conjunto. Resolvido UMA vez e reusado por todos os modelos."""

    crop: str
    path: Path
    reference: str
    strata: tuple[str, ...] = ()
    question: int | None = None


def load_tasks(labels_path: Path, crops_dir: Path | None = None, limit: int | None = None) -> list[CropTask]:
    """Le o labels.jsonl e resolve o caminho de cada recorte.

    Falha alto se faltar arquivo: conjunto parcial nao e comparavel com execucao
    anterior, e um benchmark que compara conjuntos diferentes nao compara nada.
    """
    labels_path = Path(labels_path)
    crops_dir = Path(crops_dir) if crops_dir else labels_path.parent
    rows = read_jsonl(labels_path)
    if not rows:
        raise SystemExit(f"{labels_path} esta vazio.")

    tasks: list[CropTask] = []
    missing: list[str] = []
    for row in rows:
        crop = str(row.get("crop") or "")
        if not crop:
            continue
        path = Path(crop)
        if not path.is_absolute():
            path = crops_dir / crop
        if not path.is_file():
            missing.append(crop)
            continue
        tasks.append(
            CropTask(
                crop=crop,
                path=path,
                reference=str(row.get("reference") or ""),
                strata=tuple(row.get("strata") or ()),
                question=row.get("question"),
            )
        )

    if missing:
        raise SystemExit(
            f"{len(missing)} recorte(s) do {labels_path.name} nao encontrados em {crops_dir}: "
            + ", ".join(missing[:5])
            + (" ..." if len(missing) > 5 else "")
        )
    return tasks[:limit] if limit else tasks


def model_slug(model: str) -> str:
    """Nome de arquivo para um id do OpenRouter: `google/gemma-4-31b-it:free` -> `gemma-4-31b-it`."""
    text = str(model or "").strip().lower()
    name = text.rsplit("/", 1)[-1]
    if name.endswith(":free"):
        name = name[: -len(":free")]
    name = re.sub(r"[^a-z0-9._-]+", "-", name).strip("-.")
    return name or "modelo"


def unique_slugs(models: list[str]) -> dict[str, str]:
    """Slugs distintos para modelos distintos — dois arquivos nao podem colidir."""
    slugs: dict[str, str] = {}
    used: set[str] = set()
    for model in models:
        base = model_slug(model)
        candidate = base
        if candidate in used:
            candidate = re.sub(r"[^a-z0-9._-]+", "-", str(model).strip().lower()).strip("-.")
        suffix = 2
        while candidate in used:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used.add(candidate)
        slugs[model] = candidate
    return slugs


def default_transcriber(crop_path: str, question_number: int | None, model: str) -> dict:
    """Caminho real de producao, com o modelo forcado e a cadeia de fallback desligada."""
    from app.services.openrouter_vision_client import transcribe_answer_crop

    return transcribe_answer_crop(
        crop_path,
        question_number=question_number,
        vision_model=model,
        allow_fallback=False,
    )


def run_model(
    tasks: list[CropTask],
    model: str,
    output_path: Path | None = None,
    *,
    transcriber=default_transcriber,
    use_ink_detector: bool = False,
    progress: bool = True,
) -> list[dict]:
    """Roda UM modelo sobre a lista de recortes ja resolvida.

    Nenhum recorte derruba a execucao: falha vira linha com `error` preenchido,
    hipotese vazia (que a metrica conta como resposta nao lida) e a bateria segue.
    """
    rows: list[dict] = []
    for index, task in enumerate(tasks, start=1):
        row = {
            "crop": task.crop,
            "reference": task.reference,
            "hypothesis": "",
            "confidence": None,
            "model": model,
            "elapsed_seconds": 0.0,
            "error": None,
        }

        if use_ink_detector and _is_blank(task.path):
            row.update(confidence="alta", source="ink_detector")
            rows.append(row)
            if progress:
                print(f"  [{index}/{len(tasks)}] {task.crop}: caixa vazia (detector de tinta)")
            continue

        started = time.perf_counter()
        try:
            read = transcriber(str(task.path), task.question, model)
            row["hypothesis"] = str(read.get("answer_transcription") or "")
            row["confidence"] = read.get("reading_confidence")
        except Exception as exc:  # noqa: BLE001 — a falha e o resultado, nao uma excecao a propagar
            row["error"] = f"{type(exc).__name__}: {exc}"
        row["elapsed_seconds"] = round(time.perf_counter() - started, 3)

        rows.append(row)
        if progress:
            status = row["error"] or repr(row["hypothesis"][:60])
            print(f"  [{index}/{len(tasks)}] {task.crop}: {status}")

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )
    return rows


def _is_blank(path: Path) -> bool:
    from PIL import Image

    from app.services.vision.ink import detect_ink

    with Image.open(path) as image:
        return not detect_ink(image).has_ink


def build_report(tasks: list[CropTask], rows: list[dict]) -> Report:
    """Usa as MESMAS metricas da avaliacao de rotina (htr_metrics.evaluate)."""
    by_crop = {row["crop"]: row for row in rows}
    samples = [
        Sample(
            reference=task.reference,
            hypothesis=str((by_crop.get(task.crop) or {}).get("hypothesis") or ""),
            confidence=(by_crop.get(task.crop) or {}).get("confidence"),
            strata=task.strata,
            crop_id=task.crop,
        )
        for task in tasks
    ]
    return evaluate(samples)


def summarize(model: str, tasks: list[CropTask], rows: list[dict], slug: str = "") -> dict:
    report = build_report(tasks, rows)
    timed = [row for row in rows if row.get("source") != "ink_detector"]
    failures = [row for row in rows if row.get("error")]
    answered = report.samples - report.empty_boxes

    summary = report.as_dict()
    summary.pop("by_stratum", None)
    summary.update(
        model=model,
        slug=slug or model_slug(model),
        # Leitura perfeita so existe onde havia resposta: caixa vazia nao entra
        # no denominador, senao um conjunto com muitas vazias inflaria a taxa.
        perfect_read_rate=round(report.perfect_reads / answered, 4) if answered else 0.0,
        avg_latency_seconds=(
            round(sum(row["elapsed_seconds"] for row in timed) / len(timed), 3) if timed else 0.0
        ),
        api_failures=len(failures),
        by_stratum={name: stratum.as_dict() for name, stratum in report.by_stratum.items()},
    )
    return summary


def rank(summaries: list[dict]) -> list[dict]:
    """Ordena por: alucinacao, CER, leituras perfeitas, respostas nao lidas.

    Velocidade NAO entra: um modelo rapido que inventa resposta em caixa vazia
    custa mais caro que um lento que le direito — a nota errada chega ao aluno.
    """
    return sorted(
        summaries,
        key=lambda s: (
            float(s.get("hallucination_rate") or 0.0),
            float(s.get("cer") or 0.0),
            -float(s.get("perfect_read_rate") or 0.0),
            int(s.get("missed_answers") or 0),
        ),
    )


def run_benchmark(
    *,
    tasks: list[CropTask],
    models: list[str],
    results_dir: Path,
    transcriber=default_transcriber,
    use_ink_detector: bool = False,
    progress: bool = True,
) -> list[dict]:
    """Roda todos os modelos sobre a MESMA lista de recortes.

    `tasks` e resolvida uma vez pelo chamador e passada intacta a cada modelo:
    e essa lista compartilhada que garante a comparabilidade.
    """
    results_dir = Path(results_dir)
    slugs = unique_slugs(models)
    summaries: list[dict] = []

    for model in models:
        if progress:
            print(f"\n>>> {model}")
        output_path = results_dir / f"{slugs[model]}.jsonl"
        rows = run_model(
            tasks,
            model,
            output_path,
            transcriber=transcriber,
            use_ink_detector=use_ink_detector,
            progress=progress,
        )
        summary = summarize(model, tasks, rows, slug=slugs[model])
        summary["results_file"] = str(output_path)
        summaries.append(summary)

    return summaries


def write_summary(summaries: list[dict], results_dir: Path, dataset_info: dict | None = None) -> tuple[Path, Path]:
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    ranking = rank(summaries)

    json_path = results_dir / "benchmark_summary.json"
    json_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "dataset": dataset_info or {},
                "ranking_criteria": [
                    "menor hallucination_rate",
                    "menor cer",
                    "maior perfect_read_rate",
                    "menor missed_answers",
                ],
                "ranking": [
                    {
                        "position": position,
                        "model": summary["model"],
                        "hallucination_rate": summary["hallucination_rate"],
                        "cer": summary["cer"],
                        "perfect_read_rate": summary["perfect_read_rate"],
                        "missed_answers": summary["missed_answers"],
                        "api_failures": summary["api_failures"],
                    }
                    for position, summary in enumerate(ranking, start=1)
                ],
                "models": summaries,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    csv_path = results_dir / "benchmark_summary.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for summary in ranking:
            writer.writerow({column: summary.get(column) for column in CSV_COLUMNS})

    return json_path, csv_path


def print_ranking(summaries: list[dict]) -> None:
    ranking = rank(summaries)
    width = max(len(s["model"]) for s in ranking)
    header = (
        f"{'#':<3}{'modelo':<{width}}  {'n':>4}  {'aluc':>6}  {'CER':>7}  {'CER-ac':>7}  "
        f"{'WER':>7}  {'perf':>6}  {'naolid':>6}  {'falhas':>6}  {'s/rec':>6}"
    )
    print("\n" + header)
    print("-" * len(header))
    for position, summary in enumerate(ranking, start=1):
        print(
            f"{position:<3}{summary['model']:<{width}}  {summary['samples']:>4}  "
            f"{summary['hallucination_rate']:>6.1%}  {summary['cer']:>7.4f}  "
            f"{summary['cer_no_accents']:>7.4f}  {summary['wer']:>7.4f}  "
            f"{summary['perfect_read_rate']:>6.1%}  {summary['missed_answers']:>6}  "
            f"{summary['api_failures']:>6}  {summary['avg_latency_seconds']:>6.2f}"
        )
    print(
        "\nordem: menor alucinacao > menor CER > mais leituras perfeitas > menos nao lidas."
        "\nvelocidade (s/rec) e informativa e NAO entra na ordenacao."
    )


def print_strata(summaries: list[dict]) -> None:
    """A media global esconde os boloes: lapis fraco, cursiva ligada, foto de celular."""
    strata = sorted({name for summary in summaries for name in summary.get("by_stratum", {})})
    if not strata:
        return
    print("\npor estrato (CER / alucinacao)")
    print("=" * 30)
    width = max(len(s["model"]) for s in summaries)
    for stratum in strata:
        print(f"\n  {stratum}")
        rows = [
            (summary["model"], summary["by_stratum"][stratum])
            for summary in summaries
            if stratum in summary.get("by_stratum", {})
        ]
        for model, data in sorted(rows, key=lambda item: item[1]["cer"]):
            print(
                f"    {model:<{width}}  n={data['samples']:<4} CER={data['cer']:.4f} "
                f"alucinacao={data['hallucination_rate']:.1%}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--labels", type=Path, required=True, help="labels.jsonl com as referencias humanas")
    parser.add_argument("--crops", type=Path, help="diretorio dos recortes (default: ao lado de --labels)")
    parser.add_argument("--models", nargs="+", required=True, help="ids de modelo do OpenRouter")
    parser.add_argument("--results-dir", type=Path, help="saida (default: <dir do labels>/results)")
    parser.add_argument("--limit", type=int, help="usa apenas os N primeiros recortes (ensaio barato)")
    parser.add_argument("--dry-run", action="store_true", help="mostra o custo em chamadas e sai")
    parser.add_argument(
        "--use-ink-detector",
        action="store_true",
        help="resolve caixa vazia sem chamar o modelo (mede o PIPELINE, nao o modelo: zera a alucinacao)",
    )
    parser.add_argument("--yes", action="store_true", help="nao pede confirmacao antes de gastar chamadas")
    args = parser.parse_args()

    if not args.labels.is_file():
        raise SystemExit(f"Arquivo de referencia nao encontrado: {args.labels}")

    models = list(dict.fromkeys(model.strip() for model in args.models if model.strip()))
    if not models:
        raise SystemExit("Informe ao menos um modelo em --models.")

    tasks = load_tasks(args.labels, args.crops, limit=args.limit)
    results_dir = args.results_dir or args.labels.parent / "results"
    calls = len(tasks) * len(models)

    print(f"conjunto     {len(tasks)} recortes ({args.labels})")
    print(f"modelos      {len(models)}: {', '.join(models)}")
    print(f"chamadas     {calls} chamadas de LLM ({len(tasks)} por modelo)")
    print(f"saida        {results_dir}")
    if args.use_ink_detector:
        print("aviso        --use-ink-detector ligado: caixas vazias nao vao ao modelo e a")
        print("             taxa de alucinacao medida sera a do PIPELINE, nao a do modelo.")

    if args.dry_run:
        print("\n--dry-run: nada foi chamado.")
        return 0

    if not args.yes and sys.stdin.isatty():
        if input(f"\nGastar {calls} chamadas de LLM? [s/N] ").strip().lower() not in {"s", "sim", "y", "yes"}:
            print("cancelado.")
            return 1

    summaries = run_benchmark(
        tasks=tasks,
        models=models,
        results_dir=results_dir,
        use_ink_detector=args.use_ink_detector,
    )

    print_ranking(summaries)
    print_strata(summaries)

    json_path, csv_path = write_summary(
        summaries,
        results_dir,
        dataset_info={
            "labels": str(args.labels),
            "crops": str(args.crops or args.labels.parent),
            "samples": len(tasks),
            "ink_detector": args.use_ink_detector,
            "fallback": False,
        },
    )
    print(f"\nresumo JSON  {json_path}")
    print(f"resumo CSV   {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
