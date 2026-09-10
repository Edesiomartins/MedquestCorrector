from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.services.json_utils import parse_json_safely

logger = logging.getLogger(__name__)

VISION_EXTRACTION_PROMPT = """
Você é um especialista em leitura visual de provas manuscritas de estudantes de Medicina.

Sua tarefa é analisar a imagem da folha de respostas e extrair fielmente as respostas manuscritas do aluno.

Regras obrigatórias:
1. Extraia apenas o texto manuscrito pelo aluno nas áreas de resposta.
2. Não traduza o texto.
3. Não corrija português.
4. Não melhore a resposta.
5. Não complete lacunas.
6. Não invente termos técnicos.
7. Preserve frases informais, rasuras e anotações relevantes.
8. Se uma palavra estiver duvidosa, use [?].
9. Se um trecho estiver ilegível, use [ilegível].
10. Identifique nome, matrícula, turma e número das questões quando estiverem visíveis.
11. Se houver anotação fora da área principal da resposta, registre em reading_notes.
12. Se o aluno escreveu "não sei", "não faço ideia" ou equivalente, preserve exatamente.
13. Retorne somente JSON válido.
14. Não use markdown.
15. Não inclua explicações fora do JSON.

Classifique reading_confidence assim:
- alta: texto claramente legível;
- media: pequenos trechos duvidosos;
- baixa: muitos trechos ilegíveis.

Formato JSON obrigatório:
{
  "student": {
    "name": "",
    "registration": "",
    "class": "",
    "student_code": ""
  },
  "physical_page": 1,
  "questions": [
    {
      "number": 1,
      "prompt_detected": "",
      "answer_transcription": "",
      "reading_confidence": "alta|media|baixa",
      "ocr_confidence": 0.0,
      "reading_notes": "",
      "has_answer": true,
      "image_region": null
    }
  ]
}
"""


class OpenRouterVisionError(RuntimeError):
    pass


def extract_answers_from_page_image(
    image_path: str,
    page_number: int | None = None,
    context: dict | None = None,
) -> dict:
    """
    Envia a página ou recorte para um modelo com visão via OpenRouter e retorna JSON normalizado.
    """
    if not settings.OPENROUTER_API_KEY:
        raise OpenRouterVisionError("OPENROUTER_API_KEY não configurada.")

    context = dict(context or {})
    if page_number is not None:
        context["page_number"] = page_number
    requested_model = str(context.get("vision_model") or settings.OPENROUTER_VISION_MODEL).strip()
    models = _vision_model_candidates(requested_model)
    data_url = encode_image_to_data_url(image_path)
    prompt = _build_prompt(context)
    errors: list[str] = []

    for index, model in enumerate(models):
        started = time.perf_counter()
        fallback_used = index > 0
        try:
            raw = _call_openrouter_vision(model=model, prompt=prompt, data_url=data_url)
            parsed = _load_json_object(raw)
            if parsed.get("status") == "error":
                raise OpenRouterVisionError(str(parsed.get("error") or "invalid_json"))
            normalized = _normalize_vision_response(parsed, context, raw)
            normalized["model_used"] = model
            normalized["fallback_used"] = fallback_used
            logger.info(
                "OpenRouter vision extraction succeeded",
                extra={
                    "model": model,
                    "page": normalized.get("page_number"),
                    "fallback_used": fallback_used,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                },
            )
            return normalized
        except Exception as exc:
            elapsed = time.perf_counter() - started
            message = f"{model}: {exc}"
            errors.append(message)
            logger.warning(
                "OpenRouter vision extraction failed",
                extra={
                    "model": model,
                    "page": context.get("page_number"),
                    "fallback_used": fallback_used,
                    "elapsed_seconds": round(elapsed, 3),
                    "error": str(exc),
                },
            )

    raise OpenRouterVisionError("Falha em todos os modelos de visão: " + " | ".join(errors))


extract_handwritten_answers_from_image = extract_answers_from_page_image


def _call_openrouter_vision(model: str, prompt: str, data_url: str, json_mode: bool = True) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": 0,
        "max_tokens": 4096,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    url = f"{settings.OPENROUTER_BASE_URL.rstrip('/')}/chat/completions"
    with httpx.Client(timeout=settings.OPENROUTER_TIMEOUT_SECONDS) as client:
        response = client.post(url, json=payload, headers=_headers())
    logger.info("OpenRouter vision HTTP status", extra={"model": model, "status_code": response.status_code})
    if response.status_code >= 400:
        raise OpenRouterVisionError(f"HTTP {response.status_code}: {response.text[:500]}")
    return _extract_message_content(response.json())


# Chaves que carregam gabarito/criterio de correcao. A etapa de visao e CEGA:
# se qualquer uma destas chegar ao prompt, o modelo passa a completar palavras
# ilegiveis com a resposta esperada (docs/HTR_PLANO_EXECUCAO.md, item P0-A).
ANSWER_KEY_CONTEXT_KEYS = frozenset(
    {
        "answer_key",
        "correct_answer",
        "correction_criteria",
        "criteria",
        "expected_answer",
        "gabarito",
        "grading_criteria",
        "resposta_esperada",
        "rubric",
        "rubric_summary",
        "rubrica",
    }
)

_INTERNAL_CONTEXT_KEYS = frozenset({"vision_model", "image_path"})


def _strip_answer_key(value: Any, removed: list[str]) -> Any:
    """Remove recursivamente qualquer campo de gabarito do contexto."""
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if str(key).strip().lower() in ANSWER_KEY_CONTEXT_KEYS:
                removed.append(str(key))
                continue
            clean[key] = _strip_answer_key(item, removed)
        return clean
    if isinstance(value, list):
        return [_strip_answer_key(item, removed) for item in value]
    return value


def _build_prompt(context: dict) -> str:
    parts = [VISION_EXTRACTION_PROMPT.strip()]
    if context:
        removed: list[str] = []
        safe_context = {
            key: _strip_answer_key(value, removed)
            for key, value in context.items()
            if key not in _INTERNAL_CONTEXT_KEYS
            and str(key).strip().lower() not in ANSWER_KEY_CONTEXT_KEYS
            and value is not None
        }
        for key in context:
            if str(key).strip().lower() in ANSWER_KEY_CONTEXT_KEYS:
                removed.append(str(key))
        if removed:
            logger.warning(
                "Contexto de gabarito descartado antes do prompt de visao: %s",
                sorted(set(removed)),
            )
        safe_context = {key: value for key, value in safe_context.items() if value not in (None, {}, [])}
        if safe_context:
            parts.append(
                "Contexto adicional fornecido pelo sistema:\n"
                + json.dumps(safe_context, ensure_ascii=False, indent=2)
            )
    parts.append(
        "Instrução final: retorne exclusivamente um objeto JSON válido. "
        "Não use markdown, comentários ou texto fora do JSON."
    )
    return "\n\n".join(parts)


def _vision_model_candidates(primary: str, *, allow_fallback: bool = True) -> list[str]:
    if not allow_fallback:
        # Benchmark: o modelo pedido E o objeto da medicao. Cair para o proximo
        # da cadeia transformaria "modelo A falhou" em "modelo B respondeu" --
        # exatamente o resultado que o experimento nao pode registrar.
        model = (primary or "").strip()
        if not model:
            raise OpenRouterVisionError("Modelo de visao explicito exigido quando o fallback esta desligado.")
        return [model]

    text_model = settings.OPENROUTER_TEXT_MODEL.strip()
    fallbacks = _split_csv(settings.OPENROUTER_VISION_FALLBACKS)
    candidates = [primary or settings.OPENROUTER_VISION_MODEL, *fallbacks]
    clean: list[str] = []
    for model in candidates:
        model = model.strip()
        if not model or model in clean:
            continue
        if model == text_model or model == "openai/gpt-oss-120b":
            logger.warning("Modelo textual ignorado na etapa de visão: %s", model)
            continue
        clean.append(model)
    if not clean:
        clean.append("google/gemini-2.5-flash")
    return clean


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


def encode_image_to_data_url(image_path: str) -> str:
    path = Path(image_path)
    if not path.is_file():
        raise FileNotFoundError(f"Imagem não encontrada: {path}")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    if mime not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        mime = "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _extract_message_content(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise OpenRouterVisionError("Resposta sem choices.")
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str):
        return _strip_markdown_json(content)
    if isinstance(content, list):
        text_parts = [item.get("text", "") for item in content if isinstance(item, dict)]
        return _strip_markdown_json("\n".join(text_parts))
    raise OpenRouterVisionError("Resposta sem conteúdo textual.")


def _load_json_object(raw: str) -> dict:
    return parse_json_safely(raw)


def _normalize_vision_response(parsed: dict, context: dict, raw: str) -> dict:
    student = parsed.get("student") if isinstance(parsed.get("student"), dict) else {}
    questions = parsed.get("questions") if isinstance(parsed.get("questions"), list) else []
    normalized_questions = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        normalized_questions.append(
            {
                "number": _to_int(item.get("number"), default=len(normalized_questions) + 1),
                "prompt_detected": str(item.get("prompt_detected") or ""),
                "answer_transcription": str(item.get("answer_transcription") or ""),
                "reading_confidence": _normalize_confidence(item.get("reading_confidence")),
                "ocr_confidence": _to_float_or_none(item.get("ocr_confidence")),
                "reading_notes": str(item.get("reading_notes") or ""),
                "has_answer": bool(item.get("has_answer", bool(item.get("answer_transcription")))),
                "image_region": item.get("image_region") if isinstance(item.get("image_region"), (dict, list, str)) else None,
            }
        )

    student_name = str(student.get("name") or "")
    registration = str(student.get("registration") or "")
    student_code = str(student.get("student_code") or "").strip() or _infer_student_code(
        student_name,
        registration,
    )
    return {
        "student": {
            "name": student_name,
            "registration": registration,
            "class": str(student.get("class") or ""),
            "student_code": student_code,
        },
        "physical_page": _to_int(
            parsed.get("physical_page") or parsed.get("page_number"),
            default=_to_int(context.get("page_number") or context.get("page_index"), 1),
        ),
        "questions": normalized_questions,
        "raw_model_output": raw,
    }


def _normalize_confidence(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"alta", "media", "baixa"}:
        return text
    if text in {"média", "medio", "médio"}:
        return "media"
    return "baixa"


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0.0:
        return 0.0
    if parsed > 1.0:
        return 1.0
    return parsed


def _infer_student_code(name: str, registration: str) -> str:
    for source in (name, registration):
        text = str(source or "").strip()
        if not text:
            continue
        match = re.search(r"(?i)aluno\D*(\d{1,4})", text)
        if match:
            return f"{int(match.group(1)):03d}"
        reg_match = re.search(r"(\d{2,4})\s*$", text)
        if reg_match:
            return f"{int(reg_match.group(1)):03d}"
    return ""


def _strip_markdown_json(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


# ---------------------------------------------------------------------------
# Transcrição por recorte: uma questão por chamada, prompt curto e cego.
#
# O prompt de página inteira acima pede sete coisas de uma vez — identidade,
# números de questão, enunciado detectado, transcrição, notas, autoconfiança e
# JSON válido. Objetivos múltiplos degradam cada um deles. Aqui a transcrição
# fica sozinha, sobre UM recorte, e a saída é texto puro delimitado: JSON
# aninhado gasta atenção que devia ir para os traços.
# Ver docs/HTR_PLANO_EXECUCAO.md, itens 4 e P2.
# ---------------------------------------------------------------------------

ANSWER_TRANSCRIPTION_PROMPT = """
Transcreva EXATAMENTE o texto manuscrito nesta imagem. É a resposta de um aluno
de Medicina a uma questão de prova.

Você não sabe qual é a resposta certa e não deve tentar adivinhá-la. Transcreva
o que está escrito, mesmo que pareça errado, incompleto ou sem sentido.

Regras:
- Não traduza, não corrija português, não complete palavras, não invente termos.
- Texto RISCADO pelo aluno foi apagado por ele: omita da transcrição.
- Seta de inserção (^ ou →) indica onde encaixar um trecho: transcreva na posição indicada.
- Asterisco (*) costuma indicar continuação em outro lugar da folha: registre em NOTAS.
- Abreviações médicas (HAS, DM2, IAM, ICC, AVC) devem ficar como o aluno escreveu.
- Palavra duvidosa: escreva sua melhor leitura seguida de [?].
- Trecho realmente ilegível: use [ilegível].
- Se o aluno escreveu "não sei" ou equivalente, preserve exatamente.
- Se não houver nada escrito, devolva a transcrição vazia.

Confusões frequentes em manuscrito brasileiro — olhe duas vezes antes de decidir:
a/o, n/u, r/n, m/nn, ç/c, i/e no fim de palavra, e acentos que o aluno não escreveu.

Responda EXATAMENTE neste formato, sem markdown e sem comentários:

<TRANSCRICAO>
(o texto do aluno, preservando as quebras de linha)
</TRANSCRICAO>
<CONFIANCA>alta|media|baixa</CONFIANCA>
<NOTAS>(observações sobre rasuras, setas, continuações; vazio se não houver)</NOTAS>

CONFIANCA: alta = leu tudo com clareza; media = poucos trechos duvidosos;
baixa = muitos trechos duvidosos ou ilegíveis.
""".strip()

HEADER_IDENTIFICATION_PROMPT = """
Esta imagem é o cabeçalho de uma folha de respostas.

Extraia apenas os dados de identificação impressos ou escritos ali.
Não descreva mais nada da imagem.

Retorne somente JSON válido, sem markdown:
{"name": "", "registration": "", "class": ""}

Campo ausente ou ilegível: deixe string vazia.
""".strip()

_TAG_PATTERN = "<{tag}>(.*?)</{tag}>"


def _extract_tag(raw: str, tag: str) -> str | None:
    match = re.search(_TAG_PATTERN.format(tag=tag), raw, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else None


def parse_transcription_response(raw: str) -> dict:
    """Lê a saída delimitada da transcrição por recorte.

    Modelo que ignora o formato ainda entrega algo aproveitável: o texto cru vira
    a transcrição, com confiança rebaixada para `baixa` — não dá para confiar na
    autoavaliação de quem já desobedeceu ao formato pedido.
    """
    text = _strip_markdown_json(str(raw or ""))
    transcription = _extract_tag(text, "TRANSCRICAO")
    followed_format = transcription is not None

    if not followed_format:
        transcription = text.strip()

    confidence_raw = _extract_tag(text, "CONFIANCA") if followed_format else None
    notes = _extract_tag(text, "NOTAS") if followed_format else ""

    return {
        "answer_transcription": transcription or "",
        "reading_confidence": _normalize_confidence(confidence_raw) if followed_format else "baixa",
        "reading_notes": notes or "",
        "has_answer": bool((transcription or "").strip()),
        "format_followed": followed_format,
    }


def transcribe_answer_crop(
    image_path: str,
    question_number: int | None = None,
    vision_model: str | None = None,
    allow_fallback: bool = True,
) -> dict:
    """Transcreve UM recorte de resposta, sem saber o gabarito.

    Devolve o mesmo formato de questão que o pipeline visual já consome, para que
    o caminho de recorte e o caminho de página inteira convirjam a jusante.
    """
    if not settings.OPENROUTER_API_KEY:
        raise OpenRouterVisionError("OPENROUTER_API_KEY não configurada.")

    prompt = ANSWER_TRANSCRIPTION_PROMPT
    if question_number:
        prompt = f"{prompt}\n\nEsta imagem é a resposta da questão {question_number}."

    raw_output, model, fallback_used = _call_with_fallbacks(
        image_path=image_path,
        prompt=prompt,
        vision_model=vision_model,
        json_mode=False,
        what=f"transcrição da questão {question_number}",
        allow_fallback=allow_fallback,
    )

    parsed = parse_transcription_response(raw_output)
    return {
        "number": int(question_number or 0),
        "prompt_detected": "",
        "answer_transcription": parsed["answer_transcription"],
        "reading_confidence": parsed["reading_confidence"],
        # `ocr_confidence` fica None de propósito: o float que o modelo inventava
        # não era calibrado e servia de gate para revisão manual sem significar
        # nada (docs/HTR_PLANO_EXECUCAO.md, seção de confiança).
        "ocr_confidence": None,
        "reading_notes": parsed["reading_notes"],
        "has_answer": parsed["has_answer"],
        "image_region": None,
        "model_used": model,
        "fallback_used": fallback_used,
        "raw_model_output": raw_output,
    }


def read_sheet_header(image_path: str, vision_model: str | None = None) -> dict:
    """Lê nome/matrícula/turma do cabeçalho, isolado da transcrição.

    Identidade e transcrição são tarefas diferentes que competiam pela mesma
    chamada. Quando o QR da página é legível, esta função nem precisa rodar.
    """
    if not settings.OPENROUTER_API_KEY:
        raise OpenRouterVisionError("OPENROUTER_API_KEY não configurada.")

    raw_output, model, fallback_used = _call_with_fallbacks(
        image_path=image_path,
        prompt=HEADER_IDENTIFICATION_PROMPT,
        vision_model=vision_model,
        json_mode=True,
        what="leitura do cabeçalho",
    )

    parsed = _load_json_object(raw_output)
    if not isinstance(parsed, dict) or parsed.get("status") == "error":
        parsed = {}

    name = str(parsed.get("name") or "")
    registration = str(parsed.get("registration") or "")
    return {
        "name": name,
        "registration": registration,
        "class": str(parsed.get("class") or ""),
        "student_code": str(parsed.get("student_code") or "").strip()
        or _infer_student_code(name, registration),
        "model_used": model,
        "fallback_used": fallback_used,
    }


def _call_with_fallbacks(
    *,
    image_path: str,
    prompt: str,
    vision_model: str | None,
    json_mode: bool,
    what: str,
    allow_fallback: bool = True,
) -> tuple[str, str, bool]:
    """Percorre a cadeia de modelos até um responder. Retorna (saída, modelo, houve_fallback).

    Com `allow_fallback=False` a cadeia tem um elo só: o modelo pedido responde ou
    a chamada falha. É o que o benchmark precisa — ver `_vision_model_candidates`.
    """
    models = _vision_model_candidates(
        str(vision_model or settings.OPENROUTER_VISION_MODEL).strip(),
        allow_fallback=allow_fallback,
    )
    data_url = encode_image_to_data_url(image_path)
    errors: list[str] = []

    for index, model in enumerate(models):
        started = time.perf_counter()
        try:
            raw = _call_openrouter_vision(
                model=model,
                prompt=prompt,
                data_url=data_url,
                json_mode=json_mode,
            )
            logger.info(
                "OpenRouter vision call succeeded",
                extra={
                    "model": model,
                    "task": what,
                    "fallback_used": index > 0,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                },
            )
            return raw, model, index > 0
        except Exception as exc:
            errors.append(f"{model}: {exc}")
            logger.warning(
                "OpenRouter vision call failed",
                extra={"model": model, "task": what, "error": str(exc)},
            )

    raise OpenRouterVisionError(f"Falha em todos os modelos de visão ({what}): " + " | ".join(errors))


# ---------------------------------------------------------------------------
# HTR V2: uma chamada multimodal por aluno/pagina (pagina + contact sheets).
# ---------------------------------------------------------------------------

BATCH_TRANSCRIPTION_PROMPT = """
Transcreva EXATAMENTE o texto manuscrito das respostas do aluno.

Você receberá, nesta ordem:
1. Imagem(ns) da página da prova — apenas CONTEXTO visual da folha.
2. Uma ou duas contact sheets com recortes em alta resolução. Estas imagens são a EVIDÊNCIA PRINCIPAL da transcrição.

Cada recorte nas contact sheets tem uma etiqueta de sistema Q<n> (por exemplo Q1, Q3, Q11) colocada FORA da escrita do aluno. Use a etiqueta para identificar a questão. Não renumere as questões e não invente números.

Você não sabe qual é a resposta certa e não deve tentar adivinhá-la. Transcreva o que está escrito, mesmo que pareça errado, incompleto ou sem sentido.

Regras:
- Não traduza, não corrija português, não complete palavras, não invente termos.
- Texto RISCADO pelo aluno foi apagado por ele: omita da transcrição.
- Palavra duvidosa: escreva sua melhor leitura seguida de [?].
- Trecho realmente ilegível: use [ilegível].
- Se o aluno escreveu "não sei" ou equivalente, preserve exatamente.

Devolva somente JSON válido, sem markdown e sem texto fora do JSON, com exatamente as questões pedidas, uma vez cada:
{
  "questions": [
    {
      "number": 1,
      "answer_transcription": "",
      "reading_confidence": "alta|media|baixa",
      "has_answer": true,
      "reading_notes": ""
    }
  ]
}

reading_confidence: alta = leu tudo com clareza; media = poucos trechos duvidosos; baixa = muitos trechos duvidosos ou ilegíveis.
""".strip()


def transcribe_answer_batch(
    *,
    context_image_paths: list[str],
    contact_sheet_paths: list[str],
    expected_question_numbers: list[int],
    vision_model: str | None = None,
    allow_fallback: bool = True,
) -> dict:
    """Transcreve um lote de respostas em UMA chamada multimodal, sem gabarito."""
    if not settings.OPENROUTER_API_KEY:
        raise OpenRouterVisionError("OPENROUTER_API_KEY não configurada.")

    expected = [int(number) for number in expected_question_numbers]
    if not expected:
        raise OpenRouterVisionError("HTR V2 exige ao menos uma questão esperada.")

    image_paths = [str(path) for path in list(context_image_paths or []) + list(contact_sheet_paths or [])]
    if not image_paths:
        raise OpenRouterVisionError("HTR V2 exige ao menos uma imagem.")

    prompt = _build_batch_prompt(expected)
    raw_output, model, fallback_used, usage = _call_with_fallbacks_multi(
        image_paths=image_paths,
        prompt=prompt,
        vision_model=vision_model,
        json_mode=True,
        what=f"transcrição em lote das questões {expected}",
        allow_fallback=allow_fallback,
    )

    parsed = _load_json_object(raw_output)
    if not isinstance(parsed, dict) or parsed.get("status") == "error":
        raise OpenRouterVisionError("JSON inválido na transcrição em lote.")

    questions = _normalize_batch_questions(parsed, expected, model=model, fallback_used=fallback_used)
    return {
        "questions": questions,
        "model_used": model,
        "fallback_used": fallback_used,
        "usage": usage,
        "raw_model_output": raw_output,
    }


def _build_batch_prompt(expected_question_numbers: list[int]) -> str:
    labels = ", ".join(f"Q{number}" for number in expected_question_numbers)
    numbers = ", ".join(str(number) for number in expected_question_numbers)
    return (
        f"{BATCH_TRANSCRIPTION_PROMPT}\n\n"
        f"Transcreva exatamente estas questões, uma vez cada, com os números reais: {numbers}.\n"
        f"Etiquetas de sistema nas contact sheets: {labels}."
    )


def _normalize_batch_questions(
    parsed: dict,
    expected_question_numbers: list[int],
    *,
    model: str,
    fallback_used: bool,
) -> list[dict]:
    raw_questions = parsed.get("questions")
    if not isinstance(raw_questions, list):
        raise OpenRouterVisionError("Resposta de HTR V2 sem lista de questões.")

    normalized: list[dict] = []
    seen_numbers: list[int] = []
    for item in raw_questions:
        if not isinstance(item, dict):
            raise OpenRouterVisionError("Questão de HTR V2 com schema inválido.")
        number = _to_int(item.get("number"), default=0)
        if number <= 0:
            raise OpenRouterVisionError("Questão de HTR V2 sem número válido.")
        seen_numbers.append(number)
        transcription = str(item.get("answer_transcription") or "")
        normalized.append(
            {
                "number": number,
                "prompt_detected": str(item.get("prompt_detected") or ""),
                "answer_transcription": transcription,
                "reading_confidence": _normalize_confidence(item.get("reading_confidence")),
                "ocr_confidence": None,
                "reading_notes": str(item.get("reading_notes") or ""),
                "has_answer": bool(item.get("has_answer", bool(transcription.strip()))),
                "image_region": item.get("image_region")
                if isinstance(item.get("image_region"), (dict, list, str))
                else None,
                "model_used": model,
                "fallback_used": fallback_used,
            }
        )

    _assert_exact_question_numbers(seen_numbers, expected_question_numbers)

    by_number = {item["number"]: item for item in normalized}
    return [by_number[number] for number in expected_question_numbers]


def _assert_exact_question_numbers(actual: list[int], expected: list[int]) -> None:
    expected_set = set(expected)
    actual_set = set(actual)
    if len(actual) != len(actual_set):
        raise OpenRouterVisionError("HTR V2 devolveu questão duplicada.")
    missing = [number for number in expected if number not in actual_set]
    unexpected = [number for number in actual if number not in expected_set]
    if missing or unexpected:
        raise OpenRouterVisionError(
            "HTR V2 desalinhou a numeração das questões: "
            f"faltando={missing} inesperadas={unexpected}."
        )


def _normalize_usage(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    prompt_tokens = _optional_int(value.get("prompt_tokens"))
    completion_tokens = _optional_int(value.get("completion_tokens"))
    total_tokens = _optional_int(value.get("total_tokens"))
    if prompt_tokens is None and completion_tokens is None and total_tokens is None:
        return None
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _call_openrouter_vision_multi(
    model: str,
    prompt: str,
    image_paths: list[str],
    json_mode: bool = True,
) -> tuple[str, dict | None]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image_path in image_paths:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": encode_image_to_data_url(image_path)},
            }
        )
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "max_tokens": 8192,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    url = f"{settings.OPENROUTER_BASE_URL.rstrip('/')}/chat/completions"
    with httpx.Client(timeout=settings.OPENROUTER_TIMEOUT_SECONDS) as client:
        response = client.post(url, json=payload, headers=_headers())
    logger.info(
        "OpenRouter vision HTTP status",
        extra={
            "model": model,
            "status_code": response.status_code,
            "image_count": len(image_paths),
        },
    )
    if response.status_code >= 400:
        raise OpenRouterVisionError(f"HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    return _extract_message_content(data), _normalize_usage(data.get("usage"))


def _call_with_fallbacks_multi(
    *,
    image_paths: list[str],
    prompt: str,
    vision_model: str | None,
    json_mode: bool,
    what: str,
    allow_fallback: bool = True,
) -> tuple[str, str, bool, dict | None]:
    models = _vision_model_candidates(
        str(vision_model or settings.OPENROUTER_VISION_MODEL).strip(),
        allow_fallback=allow_fallback,
    )
    errors: list[str] = []

    for index, model in enumerate(models):
        started = time.perf_counter()
        try:
            raw, usage = _call_openrouter_vision_multi(
                model=model,
                prompt=prompt,
                image_paths=image_paths,
                json_mode=json_mode,
            )
            logger.info(
                "OpenRouter vision batch call succeeded",
                extra={
                    "model": model,
                    "task": what,
                    "fallback_used": index > 0,
                    "image_count": len(image_paths),
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                    "prompt_tokens": (usage or {}).get("prompt_tokens"),
                    "completion_tokens": (usage or {}).get("completion_tokens"),
                    "total_tokens": (usage or {}).get("total_tokens"),
                },
            )
            return raw, model, index > 0, usage
        except Exception as exc:
            errors.append(f"{model}: {exc}")
            logger.warning(
                "OpenRouter vision batch call failed",
                extra={"model": model, "task": what, "error": str(exc)},
            )

    raise OpenRouterVisionError(f"Falha em todos os modelos de visão ({what}): " + " | ".join(errors))
