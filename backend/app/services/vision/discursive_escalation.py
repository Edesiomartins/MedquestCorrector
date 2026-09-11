"""Decisão pura de escalonamento seletivo no HTR V2 de discursivas."""

from __future__ import annotations

from typing import Any

_DOUBT_NOTE_MARKERS = (
    "dúvida",
    "duvida",
    "ambig",
    "ilegív",
    "ilegiv",
    "incerto",
    "incerteza",
)


def should_escalate_discursive_reading(question: dict[str, Any] | None) -> bool:
    """Escalona só recortes duvidosos. Rasura isolada e caixa vazia não entram."""
    question = question or {}
    if not question.get("has_answer"):
        return False

    transcription = str(question.get("answer_transcription") or "")
    folded = transcription.casefold()
    confidence = str(question.get("reading_confidence") or "").strip().lower()
    if confidence == "baixa":
        return True
    if "[ilegível]" in folded or "[ilegivel]" in folded or "[?]" in folded:
        return True

    notes = str(question.get("reading_notes") or "").casefold()
    return any(marker in notes for marker in _DOUBT_NOTE_MARKERS)
