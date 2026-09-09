"""Empacota o dataset HTR — labels.jsonl + os PNGs — num ZIP portátil.

`GET /reviews/htr-dataset` exporta só o `labels.jsonl`, e os `crop` apontam para
caminhos internos do servidor: para rodar `scripts/benchmark_htr_models.py` fora
da máquina, o dataset precisa levar as imagens junto e falar de si mesmo em
caminhos relativos.

Duas regras carregam este módulo, e as duas existem porque o arquivo gerado é
feito para **sair do servidor**:

1. **Lista branca de campos.** O bundle copia apenas `BUNDLE_FIELDS`. Uma lista
   negra protegeria contra os vazamentos que hoje conhecemos; a branca protege
   também contra o campo que alguém acrescentar ao `export_dataset` amanhã sem
   lembrar que ele desemboca aqui. Nome, matrícula, e-mail, `student_id`,
   gabarito e critério de correção não têm por onde entrar.
2. **Um recorte por vez, resolvido pelo caminho seguro.** Todo `crop` passa por
   `path_from_local_url`, que confina a leitura à raiz de upload. Caminho que
   não resolve — traversal, absoluto, formato antigo — é descartado e contado,
   nunca lido. E recorte que sumiu do disco não derruba a exportação inteira:
   um dataset de 200 imagens não pode se perder porque uma delas foi apagada.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from app.core.storage import path_from_local_url
from app.services.vision.htr_metrics import normalize_text

logger = logging.getLogger(__name__)

# Lista BRANCA: só isto entra no labels.jsonl do bundle. Ver o módulo acima.
BUNDLE_FIELDS = ("reference", "question", "strata", "model_transcription", "cer_at_review")

CROP_ARCNAME = "crops/crop_{index:06d}.png"


def build_bundle(rows: Iterable[dict], *, exam_id: UUID | str) -> tuple[bytes, dict]:
    """Monta o ZIP a partir das linhas de `htr_labeling.export_dataset`.

    Devolve `(bytes do zip, manifest)`. O manifest também vai dentro do ZIP; sai
    junto para que quem chama possa logar o resultado sem reabrir o arquivo.
    """
    entries: list[dict] = []
    crops: list[tuple[str, bytes]] = []
    missing = 0
    rejected = 0
    empty_references = 0

    for row in rows:
        raw_crop = str(row.get("crop") or "")
        try:
            path = path_from_local_url(raw_crop)
        except (ValueError, OSError):
            # Caminho fora da área de upload, absoluto, ou registro antigo no
            # formato `batch=.../page=...`. Não é lido: é contado e descartado.
            logger.warning("Recorte com caminho não exportável descartado: %r", raw_crop)
            rejected += 1
            missing += 1
            continue

        if not path.is_file():
            logger.info("Recorte ausente no disco, ignorado na exportação: %r", raw_crop)
            missing += 1
            continue

        arcname = CROP_ARCNAME.format(index=len(entries) + 1)
        # O nome do arquivo é sequencial de propósito: derivá-lo da resposta ou
        # do aluno colocaria o texto do aluno na listagem do ZIP.
        crops.append((arcname, path.read_bytes()))

        entry = {"crop": arcname}
        entry.update({field: row.get(field) for field in BUNDLE_FIELDS if field in row})
        entries.append(entry)

        if not normalize_text(str(row.get("reference") or "")):
            empty_references += 1

    manifest = {
        "exam_id": str(exam_id),
        "samples_exported": len(entries),
        "missing_crops": missing,
        "rejected_paths": rejected,
        "empty_references": empty_references,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        labels = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)
        archive.writestr("labels.jsonl", labels)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        for arcname, data in crops:
            archive.writestr(arcname, data)

    logger.info(
        "Bundle HTR gerado: exame %s, %d recortes, %d ignorados (%d caminhos rejeitados).",
        exam_id,
        len(entries),
        missing,
        rejected,
    )
    return buffer.getvalue(), manifest
