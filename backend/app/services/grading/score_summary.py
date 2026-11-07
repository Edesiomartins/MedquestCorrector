"""Totais de prova: pontos confirmados, pendências e nota final só quando fechada."""

from __future__ import annotations

from typing import Any


def summarize_scores(
    items: list[dict[str, Any]],
    *,
    scale_max: float | None = None,
    round_to_quarter: bool = False,
) -> dict[str, Any]:
    """Soma pontos. Não divide pelo número de questões.

    Valor pendente não entra como zero. Com pendência, a nota final fica aberta
    e o status é provisório. Conversão de escala só ocorre com nota concluída
    e `scale_max` explícito.
    """
    confirmed = 0.0
    graded = 0
    pending = 0
    maximum = 0.0
    for item in items:
        max_score = _as_float(item.get("max_score"))
        if max_score is not None:
            maximum += max_score
        pending_item = bool(item.get("pending")) or (
            item.get("score") is None and bool(item.get("needs_review", True))
        )
        score = _as_float(item.get("score"))
        if pending_item or score is None:
            pending += 1
            continue
        confirmed += score
        graded += 1

    concluded = pending == 0 and bool(items)
    final_score = confirmed if concluded else None
    if concluded and round_to_quarter and final_score is not None:
        final_score = round(final_score * 4) / 4

    proportional = None
    if concluded and scale_max is not None and maximum > 0 and final_score is not None:
        proportional = final_score / maximum * float(scale_max)
        if round_to_quarter:
            proportional = round(proportional * 4) / 4

    return {
        "confirmed_points": confirmed,
        "graded_count": graded,
        "pending_count": pending,
        "max_points": maximum,
        "final_score": final_score,
        "proportional_score": proportional,
        "status": "concluida" if concluded else "provisoria",
    }


def points_from_fraction(raw_score: Any, max_score: float) -> float | None:
    """Converte a fração 0, 0,25, 0,5, 0,75 ou 1 no valor em pontos da questão."""
    if raw_score is None or max_score < 0:
        return None
    try:
        fraction = float(str(raw_score).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if fraction < 0:
        return None
    # A régua do modelo é fração da questão. 1 em questão de 2 pontos vale 2.
    if fraction <= 1.0:
        return round(fraction * max_score, 4)
    if max_score > 0 and fraction <= max_score + 1e-6:
        return round(fraction, 4)
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
