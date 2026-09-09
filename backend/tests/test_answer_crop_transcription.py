"""Item 4 + P2 do docs/HTR_PLANO_EXECUCAO.md: uma questao por chamada, prompt curto e cego.

O prompt antigo pedia cinco coisas de uma vez -- identidade, numeros de questao,
enunciado, transcricao, notas, autoconfianca e JSON valido. Objetivos multiplos
degradam cada um. Aqui a transcricao fica sozinha, sobre UM recorte, com saida em
texto puro delimitado: JSON aninhado gasta atencao que devia ir para os tracos.
"""

import pytest

from app.services import openrouter_vision_client as vc
from app.services.openrouter_vision_client import (
    ANSWER_TRANSCRIPTION_PROMPT,
    parse_transcription_response,
    read_sheet_header,
    transcribe_answer_crop,
)


@pytest.fixture
def crop(tmp_path):
    from PIL import Image

    path = tmp_path / "q1.png"
    Image.new("RGB", (600, 180), (235, 235, 235)).save(path)
    return str(path)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setattr(vc.settings, "OPENROUTER_API_KEY", "test-key")


def _capture(monkeypatch, reply: str) -> dict:
    """Substitui a chamada HTTP e devolve o que foi enviado ao modelo."""
    seen: dict = {}

    def fake_call(model, prompt, data_url, json_mode=True):
        seen.update(model=model, prompt=prompt, data_url=data_url, json_mode=json_mode)
        return reply

    monkeypatch.setattr(vc, "_call_openrouter_vision", fake_call)
    return seen


# --- parsing da saida delimitada ---------------------------------------------


def test_parse_extracts_delimited_fields():
    parsed = parse_transcription_response(
        "<TRANSCRICAO>\nactina e miosina\n</TRANSCRICAO>\n"
        "<CONFIANCA>media</CONFIANCA>\n"
        "<NOTAS>rasura no fim</NOTAS>"
    )

    assert parsed["answer_transcription"] == "actina e miosina"
    assert parsed["reading_confidence"] == "media"
    assert parsed["reading_notes"] == "rasura no fim"
    assert parsed["has_answer"] is True


def test_parse_preserves_internal_line_breaks():
    parsed = parse_transcription_response("<TRANSCRICAO>linha um\nlinha dois</TRANSCRICAO>")

    assert parsed["answer_transcription"] == "linha um\nlinha dois"


def test_parse_marks_empty_box_as_no_answer():
    parsed = parse_transcription_response("<TRANSCRICAO></TRANSCRICAO><CONFIANCA>alta</CONFIANCA>")

    assert parsed["answer_transcription"] == ""
    assert parsed["has_answer"] is False


@pytest.mark.parametrize("raw", ["ALTA", " Media ", "baixa"])
def test_parse_normalizes_confidence(raw):
    parsed = parse_transcription_response(f"<TRANSCRICAO>x</TRANSCRICAO><CONFIANCA>{raw}</CONFIANCA>")

    assert parsed["reading_confidence"] in {"alta", "media", "baixa"}


def test_parse_falls_back_to_raw_text_without_delimiters():
    """Modelo que ignora o formato ainda entrega algo aproveitavel."""
    parsed = parse_transcription_response("fibras tipo I sao lentas")

    assert "fibras tipo I" in parsed["answer_transcription"]
    assert parsed["reading_confidence"] == "baixa"


def test_parse_unknown_confidence_degrades_to_baixa():
    parsed = parse_transcription_response("<TRANSCRICAO>x</TRANSCRICAO><CONFIANCA>excelente</CONFIANCA>")

    assert parsed["reading_confidence"] == "baixa"


# --- prompt -------------------------------------------------------------------


def test_transcription_prompt_is_blind(crop, monkeypatch):
    seen = _capture(monkeypatch, "<TRANSCRICAO>x</TRANSCRICAO>")

    transcribe_answer_crop(crop, question_number=1)

    lowered = seen["prompt"].lower()
    assert "expected_answer" not in lowered
    assert "gabarito" not in lowered
    assert "resposta esperada" not in lowered


def test_transcription_prompt_covers_cursive_failure_modes():
    """Rasura, insercao, continuacao e abreviacao medica precisam estar no prompt."""
    lowered = ANSWER_TRANSCRIPTION_PROMPT.lower()

    for topic in ("risc", "seta", "asterisco", "abrevia"):
        assert topic in lowered, topic


def test_transcription_asks_for_plain_text_not_json(crop, monkeypatch):
    seen = _capture(monkeypatch, "<TRANSCRICAO>x</TRANSCRICAO>")

    transcribe_answer_crop(crop, question_number=3)

    assert seen["json_mode"] is False
    assert "<TRANSCRICAO>" in seen["prompt"]


def test_transcription_tells_the_model_which_question_it_is(crop, monkeypatch):
    seen = _capture(monkeypatch, "<TRANSCRICAO>x</TRANSCRICAO>")

    transcribe_answer_crop(crop, question_number=7)

    assert "7" in seen["prompt"]


# --- chamada ------------------------------------------------------------------


def test_transcribe_returns_normalized_payload(crop, monkeypatch):
    _capture(monkeypatch, "<TRANSCRICAO>actina</TRANSCRICAO><CONFIANCA>alta</CONFIANCA>")

    result = transcribe_answer_crop(crop, question_number=1)

    assert result["answer_transcription"] == "actina"
    assert result["number"] == 1
    assert result["model_used"]
    assert result["fallback_used"] is False


def test_transcribe_falls_back_to_the_next_model(crop, monkeypatch):
    calls: list[str] = []

    def flaky(model, prompt, data_url, json_mode=True):
        calls.append(model)
        if len(calls) == 1:
            raise RuntimeError("503")
        return "<TRANSCRICAO>ok</TRANSCRICAO>"

    monkeypatch.setattr(vc, "_call_openrouter_vision", flaky)

    result = transcribe_answer_crop(crop, question_number=1)

    assert result["fallback_used"] is True
    assert len(calls) == 2


def test_transcribe_raises_when_every_model_fails(crop, monkeypatch):
    monkeypatch.setattr(
        vc,
        "_call_openrouter_vision",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(vc.OpenRouterVisionError):
        transcribe_answer_crop(crop, question_number=1)


# --- identidade separada da transcricao ---------------------------------------


def test_read_sheet_header_returns_identity_fields(crop, monkeypatch):
    _capture(monkeypatch, '{"name": "Maria Silva", "registration": "24102MED009", "class": "T1"}')

    header = read_sheet_header(crop)

    assert header["name"] == "Maria Silva"
    assert header["registration"] == "24102MED009"
    assert header["student_code"] == "009"


def test_read_sheet_header_does_not_ask_for_answers(crop, monkeypatch):
    seen = _capture(monkeypatch, '{"name": "x"}')

    read_sheet_header(crop)

    assert "transcri" not in seen["prompt"].lower()


# --- benchmark: modelo explicito, sem fallback --------------------------------
#
# Num benchmark o modelo pedido E o objeto da medicao. Cair para o proximo da
# cadeia transformaria "modelo A falhou" em "modelo B respondeu" -- exatamente o
# resultado que o experimento nao pode registrar.


def test_explicit_model_without_fallback_calls_only_that_model(crop, monkeypatch):
    seen = _capture(monkeypatch, "<TRANSCRICAO>x</TRANSCRICAO>")

    result = transcribe_answer_crop(crop, question_number=1, vision_model="qwen/qwen3-vl-32b-instruct", allow_fallback=False)

    assert seen["model"] == "qwen/qwen3-vl-32b-instruct"
    assert result["model_used"] == "qwen/qwen3-vl-32b-instruct"
    assert result["fallback_used"] is False


def test_explicit_model_failure_is_not_masked_by_the_fallback_chain(crop, monkeypatch):
    calls: list[str] = []

    def flaky(model, prompt, data_url, json_mode=True):
        calls.append(model)
        raise RuntimeError("503")

    monkeypatch.setattr(vc, "_call_openrouter_vision", flaky)

    with pytest.raises(vc.OpenRouterVisionError):
        transcribe_answer_crop(crop, question_number=1, vision_model="modelo/que-falha", allow_fallback=False)

    assert calls == ["modelo/que-falha"]


def test_production_path_keeps_the_fallback_chain(crop, monkeypatch):
    """O default nao muda: producao continua caindo para o proximo modelo."""
    calls: list[str] = []

    def flaky(model, prompt, data_url, json_mode=True):
        calls.append(model)
        if len(calls) == 1:
            raise RuntimeError("503")
        return "<TRANSCRICAO>ok</TRANSCRICAO>"

    monkeypatch.setattr(vc, "_call_openrouter_vision", flaky)

    assert transcribe_answer_crop(crop, question_number=1)["fallback_used"] is True
