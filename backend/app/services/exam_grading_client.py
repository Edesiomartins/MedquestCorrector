from __future__ import annotations

import json
import logging
import re
import time
from difflib import SequenceMatcher
from typing import Any

import httpx

from app.core.config import settings
from app.services.grading.practical_match import (
    CORRECT,
    PENDING,
    expand_anatomy_abbreviations as _expand_anatomy_abbreviations,
    match_answer as _match_practical_answer,
    normalize_practical_answer as _normalize_practical_answer,
)

logger = logging.getLogger(__name__)
REQUIRED_GRADING_KEYS = [
    "nota",
    "comentario",
    "criterios_atendidos",
    "criterios_ausentes",
    "revisao_necessaria",
]

GRADING_PROMPT = """
Você é um professor de Medicina avaliando uma questão discursiva.

Você receberá:
1. Enunciado da questão.
2. Padrão de resposta ou rubrica.
3. Resposta transcrita do aluno.
4. Confiança da leitura visual.

Regras obrigatórias:
1. Corrija apenas com base no texto transcrito.
2. Não presuma que o aluno escreveu algo que não aparece na transcrição.
3. Não invente conteúdo.
4. Não penalize ortografia se o conceito estiver correto.
5. Considere equivalentes variações de acento, caixa, pontuação e abreviações anatômicas comuns quando o conceito for o mesmo.
6. Atribua nota proporcional aos conceitos essenciais presentes.
7. Se a resposta não responder ao conteúdo, atribua 0.
8. Se a resposta estiver em branco, classifique como sem_resposta.
9. Se a transcrição estiver ilegível, classifique como ilegivel.
10. Se reading_confidence for baixa, marque needs_human_review como true.
11. Retorne SOMENTE JSON válido.
12. Não use markdown.
13. Não use bloco ```json.
14. Não escreva explicações fora do JSON.
15. Use aspas duplas em todas as chaves.
16. Use ponto decimal, nunca vírgula decimal.
17. A nota deve ser exatamente um destes valores: 0, 0.25, 0.5, 0.75, 1.
18. Comentário curto e objetivo.

Formato JSON obrigatório (use exatamente estas chaves):
{
  "nota": 0.75,
  "comentario": "Comentário curto.",
  "criterios_atendidos": ["..."],
  "criterios_ausentes": ["..."],
  "revisao_necessaria": false
}

Não inclua "analysis".
Não inclua "reasoning".
Não inclua "physical_page".
Não inclua campos extras.
"""


class OpenRouterGradingError(RuntimeError):
    pass


def grade_practical_answer(
    question: dict,
    rubric: dict,
    student_answer: str,
    reading_confidence: str = "media",
) -> dict:
    """Corrige prova prática por comparação direta com o gabarito esperado."""
    qnum = _to_int(question.get("number") or question.get("question_number"), 0)
    max_score = _to_float(question.get("max_score") or rubric.get("max_score") or rubric.get("valor") or 1.0, 1.0)
    expected_raw = str(
        rubric.get("expected_answer")
        or rubric.get("rubric")
        or rubric.get("answer")
        or rubric.get("resposta")
        or ""
    ).strip()
    answer_raw = str(student_answer or question.get("answer_transcription") or "").strip()
    confidence = str(reading_confidence or question.get("reading_confidence") or "media").lower()

    if not expected_raw:
        return {
            "question_number": qnum,
            "score": None,
            "max_score": max_score,
            "verdict": "sem_rubrica",
            "justification": "Gabarito prático não informado para esta questão.",
            "detected_concepts": [],
            "missing_concepts": [],
            "needs_human_review": True,
            "review_reason": "Gabarito prático ausente.",
            "model_used": "practical-rule-based",
        }

    if not answer_raw:
        return {
            "question_number": qnum,
            "score": 0.0,
            "max_score": max_score,
            "verdict": "sem_resposta",
            "justification": f"Resposta em branco. Esperado: {expected_raw}.",
            "detected_concepts": [],
            "missing_concepts": [expected_raw],
            "needs_human_review": confidence == "baixa",
            "review_reason": "Leitura visual com baixa confiança." if confidence == "baixa" else "",
            "model_used": "practical-rule-based",
        }

    match = _match_practical_answer(answer_raw, expected_raw)
    is_correct = match.status == CORRECT
    is_pending = match.status == PENDING

    justification = _PRACTICAL_JUSTIFICATIONS.get(match.reason, "").format(expected=expected_raw)
    if is_pending:
        review_reason = justification
    elif confidence == "baixa":
        review_reason = "Leitura visual com baixa confiança."
    else:
        review_reason = ""

    return {
        "question_number": qnum,
        # Pendente fica sem nota de propósito: zerar uma resposta duvidosa é pior
        # do que devolvê-la ao professor.
        "score": None if is_pending else (max_score if is_correct else 0.0),
        "max_score": max_score,
        "verdict": match.status,
        "justification": justification,
        "detected_concepts": [expected_raw] if is_correct else [],
        "missing_concepts": [] if is_correct else [expected_raw],
        "needs_human_review": is_pending or confidence == "baixa",
        "review_reason": review_reason,
        "model_used": "practical-rule-based",
        "similarity": round(match.similarity, 3),
        "expected_answer": expected_raw,
        "normalized_student_answer": match.normalized_answer,
        "match_reason": match.reason,
        "matched_core": match.matched_core,
        "missing_core": match.missing_core,
    }


_PRACTICAL_JUSTIFICATIONS = {
    "confere": "Resposta prática confere com o gabarito esperado.",
    "lateralidade": "Estrutura compatível, mas lateralidade divergente.",
    "estrutura": "Termo correto, mas a classe da estrutura diverge do gabarito.",
    "nucleo_parcial": "Resposta incompleta em relação ao gabarito. Revisão humana necessária.",
    "qualificador": (
        "Estrutura compatível, mas o qualificador anatômico contradiz o gabarito "
        "(anterior/posterior, medial/lateral, superior/inferior): {expected}."
    ),
    "contexto_extra": (
        "Estrutura compatível, mas o aluno omitiu a lateralidade e acrescentou "
        "contexto anatômico que o gabarito não cita. Revisão humana necessária."
    ),
    "leitura_aproximada": (
        "Resposta compatível com o gabarito, mas a leitura tem ruído. "
        "Revisão humana necessária."
    ),
    "nao_confere": "Resposta prática não confere com o gabarito esperado: {expected}.",
}


def grade_discursive_answer(
    question: dict,
    rubric: dict,
    student_answer: str,
    reading_confidence: str = "media",
) -> dict:
    student_name = str(question.get("student_name") or "").strip()
    question_number = question.get("number") or question.get("question_number")
    correlation_id = str(question.get("correlation_id") or "n/a")
    if not settings.OPENROUTER_API_KEY:
        return _fallback_grade(
            question=question,
            rubric=rubric,
            raw_response="",
            parse_error="OPENROUTER_API_KEY não configurada.",
            force_review=True,
        )

    primary = str(question.get("text_model") or rubric.get("text_model") or settings.OPENROUTER_TEXT_MODEL).strip()
    models = _model_candidates(primary)
    prompt = _build_prompt(question, rubric, student_answer, reading_confidence)
    errors: list[str] = []

    for index, model in enumerate(models):
        started = time.perf_counter()
        fallback_used = index > 0
        try:
            logger.warning(
                "[grading-debug] correlation_id=%s student=%s question=%s model=%s",
                correlation_id,
                student_name or "n/a",
                question_number,
                model,
            )
            raw = _call_openrouter_text(model=model, prompt=prompt)
            logger.warning("[grading-debug] correlation_id=%s raw_response_preview=%s", correlation_id, raw[:700])
            parsed = parse_llm_json_response(raw)
            normalized = _normalize_grading_response(parsed, question, rubric, raw)
            normalized["model_used"] = model
            normalized["fallback_used"] = fallback_used
            logger.warning(
                "[grading-debug] correlation_id=%s parsed_grade=%s revisao=%s schema_valid=%s warnings=%s",
                correlation_id,
                normalized.get("score"),
                normalized.get("needs_human_review"),
                normalized.get("schema_valid"),
                normalized.get("parse_warnings"),
            )
            logger.info(
                "OpenRouter grading succeeded",
                extra={
                    "model": model,
                    "question_number": normalized.get("question_number"),
                    "fallback_used": fallback_used,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                },
            )
            return normalized
        except Exception as exc:
            errors.append(f"{model}: {exc}")
            logger.warning(
                "OpenRouter grading failed",
                extra={
                    "model": model,
                    "question_number": question.get("number"),
                    "fallback_used": fallback_used,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "error": str(exc),
                },
            )

    return _fallback_grade(
        question=question,
        rubric=rubric,
        raw_response="",
        parse_error="Falha em todos os modelos textuais: " + " | ".join(errors),
        force_review=True,
    )


def grade_page_answers(extracted_page: dict, rubric: dict) -> dict:
    graded_questions = []
    for question in extracted_page.get("questions") or []:
        qnum = int(question.get("number") or 0)
        question_rubric = _rubric_for_question(rubric, qnum)
        if not question_rubric:
            graded_questions.append({**question, "grade": _missing_rubric_grade(question)})
            continue
        grade = grade_discursive_answer(
            question,
            question_rubric,
            question.get("answer_transcription") or "",
            reading_confidence=question.get("reading_confidence") or "media",
        )
        graded_questions.append({**question, "grade": grade})
    return {**extracted_page, "questions": graded_questions}


def _call_openrouter_text(model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": GRADING_PROMPT.strip()},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
    }
    url = f"{settings.OPENROUTER_BASE_URL.rstrip('/')}/chat/completions"
    with httpx.Client(timeout=settings.OPENROUTER_TIMEOUT_SECONDS) as client:
        response = client.post(url, json=payload, headers=_headers())
    logger.info("OpenRouter grading HTTP status", extra={"model": model, "status_code": response.status_code})
    if response.status_code >= 400:
        raise OpenRouterGradingError(f"HTTP {response.status_code}: {response.text[:500]}")
    return _extract_message_content(response.json())


def _build_prompt(question: dict, rubric: dict, student_answer: str, reading_confidence: str) -> str:
    prompt_text = str(
        question.get("prompt")
        or question.get("prompt_detected")
        or question.get("question_prompt")
        or ""
    )
    answer_text = student_answer or ""
    rubric_text = (
        str(rubric.get("expected_answer") or "")
        or str(rubric.get("rubric") or "")
        or str(rubric.get("answer") or "")
    )
    payload = {
        "question_number": question.get("number") or question.get("question_number"),
        "question_prompt": prompt_text,
        "question_prompt_expanded": _expand_anatomy_abbreviations(prompt_text),
        "reading_confidence": reading_confidence or question.get("reading_confidence") or "media",
        "student_answer": answer_text,
        "student_answer_expanded": _expand_anatomy_abbreviations(answer_text),
        "student_answer_normalized": _normalize_practical_answer(answer_text),
        "rubric_expected_answer_expanded": _expand_anatomy_abbreviations(rubric_text),
        "rubric_expected_answer_normalized": _normalize_practical_answer(rubric_text),
        "rubric": rubric or {},
    }
    return (
        "Corrija a resposta abaixo e retorne SOMENTE o JSON solicitado, curto e válido.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _normalize_grading_response(parsed: dict, question: dict, rubric: dict, raw: str) -> dict:
    schema_valid, schema_warnings = _validate_grading_schema(parsed)
    qnum = _to_int(question.get("number") or question.get("question_number"), 0)
    max_score = _to_float(
        parsed.get("max_score")
        or question.get("max_score")
        or rubric.get("max_score")
        or rubric.get("valor")
        or 1.0,
        1.0,
    )
    raw_score = parsed.get("score", parsed.get("nota"))
    score, invalid_grade = clamp_grade(raw_score)
    score = max(0.0, min(score, max_score))
    confidence = str(question.get("reading_confidence") or "").lower()
    answer = str(question.get("answer_transcription") or "").strip()
    revisao_value, revisao_value_invalid = _coerce_revisao_necessaria(parsed.get("revisao_necessaria"))
    if revisao_value_invalid:
        schema_warnings.append("Campo revisao_necessaria com tipo/valor inválido.")

    needs_review = (
        bool(parsed.get("needs_human_review", revisao_value))
        or confidence == "baixa"
        or answer.count("[ilegível]") + answer.count("[ilegivel]") >= 2
        or invalid_grade
        or (not schema_valid)
        or revisao_value_invalid
    )
    verdict = _normalize_verdict(parsed.get("verdict"), answer, confidence, score)
    copied_statement = _looks_like_question_copy(question, rubric, answer)
    if copied_statement:
        score = 0.0
        verdict = "incorreta"
        needs_review = True

    return {
        "question_number": qnum,
        "score": score,
        "max_score": max_score,
        "verdict": verdict,
        "justification": str(parsed.get("justification", parsed.get("comentario", "")) or ""),
        "missing_concepts": _list_of_strings(parsed.get("missing_concepts", parsed.get("criterios_ausentes"))),
        "detected_concepts": _list_of_strings(parsed.get("detected_concepts", parsed.get("criterios_atendidos"))),
        "needs_human_review": needs_review,
        "schema_valid": schema_valid,
        "parse_warnings": schema_warnings,
        "review_reason": str(
            parsed.get("review_reason")
            or parsed.get("erro_parse")
            or ("; ".join(schema_warnings) if schema_warnings else "")
            or ("Resposta parece cópia do enunciado da questão." if copied_statement else "")
            or ("Nota fora do formato esperado; revisão manual necessária." if invalid_grade else "")
            or ("Leitura visual com baixa confiança." if confidence == "baixa" else "")
        ),
        "raw_model_output": raw,
    }


def _normalize_verdict(value: Any, answer: str, confidence: str, score: float) -> str:
    text = str(value or "").strip().lower()
    allowed = {"correta", "parcial", "incorreta", "sem_resposta", "ilegivel"}
    if text in allowed:
        return text
    if not answer:
        return "sem_resposta"
    if confidence == "baixa" and ("[ilegível]" in answer or "[ilegivel]" in answer):
        return "ilegivel"
    if score >= 0.99:
        return "correta"
    if score > 0:
        return "parcial"
    return "incorreta"


def _model_candidates(primary: str) -> list[str]:
    candidates = [primary or settings.OPENROUTER_TEXT_MODEL, *_split_csv(settings.OPENROUTER_TEXT_FALLBACKS)]
    clean: list[str] = []
    for model in candidates:
        model = model.strip()
        if model and model not in clean:
            clean.append(model)
    return clean or [settings.OPENROUTER_TEXT_MODEL]


def _headers() -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    if settings.OPENROUTER_HTTP_REFERER:
        headers["HTTP-Referer"] = settings.OPENROUTER_HTTP_REFERER
    if settings.OPENROUTER_APP_TITLE:
        headers["X-OpenRouter-Title"] = settings.OPENROUTER_APP_TITLE
        headers["X-Title"] = settings.OPENROUTER_APP_TITLE
    return headers


def _extract_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise OpenRouterGradingError("Resposta sem choices.")
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str):
        return _strip_markdown_json(content)
    if isinstance(content, list):
        return _strip_markdown_json("\n".join(item.get("text", "") for item in content if isinstance(item, dict)))
    raise OpenRouterGradingError("Resposta sem conteúdo textual.")


def parse_llm_json_response(raw: str) -> dict:
    text = _strip_markdown_json(raw)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]

    attempts = [text]
    fixed_quotes = (
        text.replace("“", '"')
        .replace("”", '"')
        .replace("’", "'")
        .replace("‘", "'")
    )
    attempts.append(fixed_quotes)
    attempts.append(re.sub(r",\s*([}\]])", r"\1", fixed_quotes))
    attempts.append(re.sub(r"(\d),(\d)", r"\1.\2", attempts[-1]))

    last_error = ""
    for candidate in attempts:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
            last_error = "json_root_not_object"
        except json.JSONDecodeError as exc:
            last_error = str(exc)

    return {
        "nota": 0.0,
        "comentario": "Resposta da IA em formato inválido. Revisão manual necessária.",
        "criterios_atendidos": [],
        "criterios_ausentes": [],
        "revisao_necessaria": True,
        "erro_parse": last_error or "invalid_json",
        "raw_response_preview": (raw or "")[:500],
    }


def _validate_grading_schema(parsed: dict) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    missing = [k for k in REQUIRED_GRADING_KEYS if k not in parsed]
    if missing:
        warnings.append(f"JSON recuperado, mas schema incompleto: chaves ausentes: {missing}")
    # Chaves parecidas, porém inválidas, não são aceitas.
    suspicious = [k for k in parsed.keys() if isinstance(k, str) and _is_suspicious_typo_key(k)]
    if suspicious:
        warnings.append(f"Chaves suspeitas/ignoradas: {suspicious}")
    if "analysis" in parsed or "reasoning" in parsed:
        warnings.append("Campos proibidos detectados: analysis/reasoning.")

    # Tipagem esperada
    if "comentario" in parsed and not isinstance(parsed.get("comentario"), str):
        warnings.append("Campo comentario com tipo inválido.")
    if "criterios_atendidos" in parsed and not isinstance(parsed.get("criterios_atendidos"), list):
        warnings.append("Campo criterios_atendidos com tipo inválido.")
    if "criterios_ausentes" in parsed and not isinstance(parsed.get("criterios_ausentes"), list):
        warnings.append("Campo criterios_ausentes com tipo inválido.")
    if "nota" in parsed and not isinstance(parsed.get("nota"), (int, float, str)):
        warnings.append("Campo nota com tipo inválido.")

    return len(warnings) == 0, warnings


def _coerce_revisao_necessaria(value: Any) -> tuple[bool, bool]:
    if isinstance(value, bool):
        return value, False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True, False
        if normalized == "false":
            return False, False
        return True, True
    if value is None:
        return True, True
    return bool(value), True


def _is_suspicious_typo_key(key: str) -> bool:
    normalized = key.strip().lower()
    if normalized in REQUIRED_GRADING_KEYS:
        return False
    if "revisao_necess" in normalized:
        return True
    if normalized.startswith("criterios_") and normalized not in {"criterios_atendidos", "criterios_ausentes"}:
        return True
    return False


def _rubric_for_question(rubric: dict, number: int) -> dict | None:
    if not rubric:
        return None
    if "free_text_rubric" in rubric:
        return rubric
    if isinstance(rubric.get("questions"), list):
        for item in rubric["questions"]:
            if isinstance(item, dict) and int(item.get("number") or item.get("question_number") or 0) == number:
                return item
    value = rubric.get(str(number)) or rubric.get(number)
    return value if isinstance(value, dict) else None


def _missing_rubric_grade(question: dict) -> dict:
    confidence = str(question.get("reading_confidence") or "media")
    return {
        "question_number": int(question.get("number") or 0),
        "score": 0.0,
        "max_score": None,
        "verdict": "sem_rubrica",
        "justification": "Rubrica não fornecida para esta questão.",
        "detected_concepts": [],
        "missing_concepts": [],
        "needs_human_review": confidence == "baixa",
        "review_reason": "Leitura visual com baixa confiança." if confidence == "baixa" else "",
    }


def _strip_markdown_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp_grade(value: Any) -> tuple[float, bool]:
    allowed = [0.0, 0.25, 0.5, 0.75, 1.0]
    try:
        numeric = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0, True
    nearest = min(allowed, key=lambda x: abs(x - numeric))
    invalid = nearest != numeric
    return nearest, invalid


def _fallback_grade(
    *,
    question: dict,
    rubric: dict,
    raw_response: str,
    parse_error: str,
    force_review: bool,
) -> dict:
    qnum = _to_int(question.get("number") or question.get("question_number"), 0)
    max_score = _to_float(
        question.get("max_score") or rubric.get("max_score") or rubric.get("valor") or 1.0,
        1.0,
    )
    return {
        "question_number": qnum,
        "score": 0.0,
        "max_score": max_score,
        "verdict": "incorreta",
        "justification": "Resposta da IA em formato inválido. Revisão manual necessária.",
        "missing_concepts": [],
        "detected_concepts": [],
        "needs_human_review": force_review,
        "review_reason": parse_error[:500],
        "raw_model_output": raw_response[:1000],
    }


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _looks_like_question_copy(question: dict, rubric: dict, answer: str) -> bool:
    answer_norm = _normalize_text(answer)
    if not answer_norm or len(answer_norm) < 20:
        return False
    prompt = (
        question.get("prompt")
        or question.get("prompt_detected")
        or question.get("question_prompt")
        or rubric.get("prompt")
        or ""
    )
    prompt_norm = _normalize_text(str(prompt))
    if not prompt_norm or len(prompt_norm) < 20:
        return False

    ratio = SequenceMatcher(None, answer_norm, prompt_norm).ratio()
    answer_tokens = set(answer_norm.split())
    prompt_tokens = set(prompt_norm.split())
    overlap = len(answer_tokens & prompt_tokens) / max(1, len(answer_tokens))
    return ratio >= 0.86 or overlap >= 0.85


def _normalize_text(value: str) -> str:
    lowered = str(value or "").lower()
    lowered = re.sub(r"[^a-z0-9áàâãéèêíïóôõöúçñ\s]", " ", lowered)
    lowered = re.sub(r"\s+", " ", lowered).strip()
    return lowered

