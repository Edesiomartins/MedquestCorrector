"""Guarda de gabarito para provas discursivas externas."""

from __future__ import annotations

import json
from typing import Any, Iterable

from app.services.vision.sheet_geometry import load_manifest

EXPECTED_ANSWER_PLACEHOLDER = "Resposta esperada não informada."
MISSING_GABARITO_MESSAGE = (
    "Esta prova discursiva externa possui questões sem resposta esperada. "
    "Complete o gabarito antes de iniciar a correção."
)


class ExternalDiscursiveNotReadyError(ValueError):
    """Prova externa incompleta: não iniciar HTR/correção."""


def is_blank_expected_answer(value: Any) -> bool:
    text = str(value or "").strip()
    return not text or text == EXPECTED_ANSWER_PLACEHOLDER


def _raw_manifest(exam: Any) -> dict[str, Any] | None:
    raw = getattr(exam, "layout_manifest_json", None)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None
    return None


def is_external_discursive_exam(exam: Any) -> bool:
    data = _raw_manifest(exam)
    if data is not None and str(data.get("source") or "") == "external_discursive":
        return True
    manifest = load_manifest(getattr(exam, "layout_manifest_json", None))
    return bool(manifest and manifest.is_external_discursive)


def require_external_discursive_ready(exam: Any, questions: Iterable[Any]) -> None:
    if not is_external_discursive_exam(exam):
        return
    if any(is_blank_expected_answer(getattr(question, "expected_answer", None)) for question in questions):
        raise ExternalDiscursiveNotReadyError(MISSING_GABARITO_MESSAGE)
