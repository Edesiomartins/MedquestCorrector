"""HTR V2: uma chamada multimodal cega por pagina, com schema validado."""

import json

import pytest

from app.services import openrouter_vision_client as vc
from app.services.openrouter_vision_client import transcribe_answer_batch


FORBIDDEN_PROMPT_TERMS = (
    "expected_answer",
    "gabarito",
    "resposta esperada",
    "correction_criteria",
    "grading_criteria",
    "correct_answer",
    "answer_key",
    "rubric",
    "rubrica",
    "verdict",
)


@pytest.fixture
def image_paths(tmp_path):
    from PIL import Image

    paths = []
    for name in ("page.png", "sheet1.png", "sheet2.png"):
        path = tmp_path / name
        Image.new("RGB", (80, 60), (230, 230, 230)).save(path)
        paths.append(str(path))
    return paths


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setattr(vc.settings, "OPENROUTER_API_KEY", "test-key")


def _question(number: int, text: str = "texto") -> dict:
    return {
        "number": number,
        "answer_transcription": text,
        "reading_confidence": "alta",
        "has_answer": True,
        "reading_notes": "",
    }


def _capture(monkeypatch, payload: dict, usage=None):
    seen: dict = {"call_count": 0, "calls": []}

    def fake(model, prompt, image_paths, json_mode=True):
        seen["call_count"] += 1
        seen["model"] = model
        seen["prompt"] = prompt
        seen["image_paths"] = list(image_paths)
        seen["json_mode"] = json_mode
        seen["calls"].append({"model": model, "prompt": prompt, "image_paths": list(image_paths)})
        return json.dumps(payload), usage

    monkeypatch.setattr(vc, "_call_openrouter_vision_multi", fake)
    return seen


def test_batch_uses_one_multimodal_call_for_all_images(monkeypatch, image_paths):
    seen = _capture(
        monkeypatch,
        {"questions": [_question(1, "a"), _question(2, "b"), _question(3, "c")]},
    )

    result = transcribe_answer_batch(
        context_image_paths=[image_paths[0]],
        contact_sheet_paths=image_paths[1:],
        expected_question_numbers=[1, 2, 3],
    )

    assert [q["number"] for q in result["questions"]] == [1, 2, 3]
    assert seen["call_count"] == 1
    assert seen["image_paths"] == image_paths
    assert seen["json_mode"] is True


def test_batch_http_payload_has_text_then_all_images(monkeypatch, image_paths):
    seen = {"call_count": 0, "payloads": []}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "id": "gen-1",
                "model": "openai/gpt-5.6-sol",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"questions": [_question(1, "a"), _question(2, "b")]}
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            }

    class FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, json=None, headers=None):
            seen["call_count"] += 1
            seen["url"] = url
            seen["payloads"].append(json)
            seen["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(vc.httpx, "Client", FakeClient)

    result = transcribe_answer_batch(
        context_image_paths=[image_paths[0]],
        contact_sheet_paths=image_paths[1:],
        expected_question_numbers=[1, 2],
        allow_fallback=False,
        vision_model="openai/gpt-5.6-sol",
    )

    assert seen["call_count"] == 1
    content = seen["payloads"][0]["messages"][0]["content"]
    assert content[0]["type"] == "text"
    image_parts = [part for part in content if part["type"] == "image_url"]
    assert len(image_parts) == 3
    assert all(part["image_url"]["url"].startswith("data:image/") for part in image_parts)
    assert result["usage"]["prompt_tokens"] == 11
    assert result["usage"]["completion_tokens"] == 7
    assert result["usage"]["total_tokens"] == 18
    assert "sk-" not in json.dumps(seen["payloads"])
    assert "Authorization" not in json.dumps(result)


def test_batch_prompt_is_blind(monkeypatch, image_paths):
    seen = _capture(monkeypatch, {"questions": [_question(1), _question(2)]})

    transcribe_answer_batch(
        context_image_paths=[image_paths[0]],
        contact_sheet_paths=[image_paths[1]],
        expected_question_numbers=[1, 2],
    )

    lowered = seen["prompt"].lower()
    for forbidden in FORBIDDEN_PROMPT_TERMS:
        assert forbidden not in lowered, forbidden
    assert "1" in seen["prompt"]
    assert "2" in seen["prompt"]


def test_batch_prompt_describes_contact_sheets_as_primary_evidence(monkeypatch, image_paths):
    seen = _capture(monkeypatch, {"questions": [_question(1)]})

    transcribe_answer_batch(
        context_image_paths=[image_paths[0]],
        contact_sheet_paths=[image_paths[1]],
        expected_question_numbers=[1],
    )

    lowered = seen["prompt"].lower()
    assert "contact" in lowered or "folha" in lowered
    assert "contexto" in lowered or "context" in lowered
    assert "q1" in lowered or "questão 1" in lowered or "questao 1" in lowered


def test_valid_non_contiguous_numbers_are_preserved(monkeypatch, image_paths):
    seen = _capture(
        monkeypatch,
        {"questions": [_question(7, "c"), _question(1, "a"), _question(3, "b")]},
    )

    result = transcribe_answer_batch(
        context_image_paths=[image_paths[0]],
        contact_sheet_paths=[image_paths[1]],
        expected_question_numbers=[1, 3, 7],
    )

    assert [q["number"] for q in result["questions"]] == [1, 3, 7]
    assert result["questions"][0]["answer_transcription"] == "a"
    assert result["questions"][1]["answer_transcription"] == "b"
    assert result["questions"][2]["answer_transcription"] == "c"
    assert result["questions"][0]["ocr_confidence"] is None
    assert seen["call_count"] == 1


def test_missing_question_raises_before_grading(monkeypatch, image_paths):
    _capture(monkeypatch, {"questions": [_question(1), _question(3)]})

    with pytest.raises(vc.OpenRouterVisionError):
        transcribe_answer_batch(
            context_image_paths=[image_paths[0]],
            contact_sheet_paths=[image_paths[1]],
            expected_question_numbers=[1, 2, 3],
        )


def test_duplicate_question_raises_before_grading(monkeypatch, image_paths):
    _capture(monkeypatch, {"questions": [_question(1), _question(2), _question(2)]})

    with pytest.raises(vc.OpenRouterVisionError):
        transcribe_answer_batch(
            context_image_paths=[image_paths[0]],
            contact_sheet_paths=[image_paths[1]],
            expected_question_numbers=[1, 2],
        )


def test_unknown_question_raises_before_grading(monkeypatch, image_paths):
    _capture(monkeypatch, {"questions": [_question(1), _question(2), _question(99)]})

    with pytest.raises(vc.OpenRouterVisionError):
        transcribe_answer_batch(
            context_image_paths=[image_paths[0]],
            contact_sheet_paths=[image_paths[1]],
            expected_question_numbers=[1, 2],
        )


def test_invalid_json_raises(monkeypatch, image_paths):
    def fake(*a, **k):
        return "isto nao e json", None

    monkeypatch.setattr(vc, "_call_openrouter_vision_multi", fake)

    with pytest.raises(vc.OpenRouterVisionError):
        transcribe_answer_batch(
            context_image_paths=[image_paths[0]],
            contact_sheet_paths=[image_paths[1]],
            expected_question_numbers=[1],
        )


def test_missing_usage_does_not_fail(monkeypatch, image_paths):
    _capture(monkeypatch, {"questions": [_question(1)]}, usage=None)

    result = transcribe_answer_batch(
        context_image_paths=[image_paths[0]],
        contact_sheet_paths=[image_paths[1]],
        expected_question_numbers=[1],
    )

    assert result["usage"] in (None, {})
    assert result["questions"][0]["number"] == 1


def test_http_error_raises_vision_error(monkeypatch, image_paths):
    def fake(*a, **k):
        raise vc.OpenRouterVisionError("HTTP 503: unavailable")

    monkeypatch.setattr(vc, "_call_openrouter_vision_multi", fake)

    with pytest.raises(vc.OpenRouterVisionError):
        transcribe_answer_batch(
            context_image_paths=[image_paths[0]],
            contact_sheet_paths=[image_paths[1]],
            expected_question_numbers=[1],
            allow_fallback=False,
        )
