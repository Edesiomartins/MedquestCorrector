"""HTR V2 no pipeline de manifesto: uma chamada por pagina, com fallback V1."""

from pathlib import Path

import pytest
from PIL import Image

from app.services import visual_exam_pipeline as vep
from app.services.openrouter_vision_client import OpenRouterVisionError
from app.services.visual_exam_pipeline import HTRBatchError
from app.services.vision.ink import InkStats
from app.services.vision.sheet_geometry import AnswerBox, ManifestPageGeometry, SheetManifest


def _page_image(tmp_path: Path) -> str:
    path = tmp_path / "page.png"
    Image.new("RGB", (120, 80), (250, 250, 250)).save(path)
    return str(path)


def _manifest(numbers: list[int]) -> SheetManifest:
    page = ManifestPageGeometry(
        physical_index=0,
        exam_id="exam-1",
        student_id="student-1",
        page_in_student=1,
        total_pages_for_student=1,
        boxes=[
            AnswerBox(question_number=number, x_pt=10, y_bottom_pt=100, width_pt=200, height_pt=40)
            for number in numbers
        ],
    )
    return SheetManifest(version=1, pages={0: page})


def _ink(has_ink: bool) -> InkStats:
    return InkStats(
        has_ink=has_ink,
        ink_ratio=0.02 if has_ink else 0.0,
        ink_pixels=12 if has_ink else 0,
        components=1 if has_ink else 0,
        reason="ink" if has_ink else "empty",
        is_marginal=False,
    )


def _v1_page(physical_page_number: int = 1) -> dict:
    return {
        "student": {"name": "V1", "registration": "", "class": "", "student_code": "001"},
        "physical_page": physical_page_number,
        "questions": [
            {
                "number": 1,
                "prompt_detected": "",
                "answer_transcription": "from v1",
                "reading_confidence": "alta",
                "ocr_confidence": None,
                "reading_notes": "",
                "has_answer": True,
            }
        ],
        "model_used": "v1-model",
        "fallback_used": False,
        "read_strategy": "manifest_crops",
    }


def _batch_result(numbers: list[int], usage=None) -> dict:
    return {
        "questions": [
            {
                "number": number,
                "prompt_detected": "",
                "answer_transcription": f"resp {number}",
                "reading_confidence": "alta",
                "ocr_confidence": None,
                "reading_notes": "",
                "has_answer": True,
                "image_region": None,
                "model_used": "vision-mock",
                "fallback_used": False,
            }
            for number in numbers
        ],
        "model_used": "vision-mock",
        "fallback_used": False,
        "usage": usage or {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
    }


def _install_crop_prep(monkeypatch, blank_numbers: set[int] | None = None):
    blank_numbers = blank_numbers or set()
    current = {"number": None}

    def fake_render(pdf_path, page_index, box, dpi=None):
        current["number"] = box.question_number
        return Image.new("RGB", (80, 40), (30, 30, 30))

    def fake_ink(_image):
        return _ink(has_ink=current["number"] not in blank_numbers)

    monkeypatch.setattr(vep, "render_pdf_box", fake_render)
    monkeypatch.setattr(vep, "detect_ink", fake_ink)
    monkeypatch.setattr(vep, "normalize_for_reading", lambda crop, upscale_below_px=700: crop)
    monkeypatch.setattr(
        vep,
        "_identify_page",
        lambda **kwargs: {
            "name": "ALUNO",
            "registration": "",
            "class": "",
            "student_code": "009",
            "qr_student_id": "",
            "identity_source": "test",
        },
    )


def _read(tmp_path: Path, numbers: list[int], options: dict | None = None, warnings: list[str] | None = None):
    warnings = warnings if warnings is not None else []
    page_image = _page_image(tmp_path)
    return vep._read_page(
        pdf_path=str(tmp_path / "sheet.pdf"),
        page_image=page_image,
        page_index=0,
        physical_page_number=1,
        manifest=_manifest(numbers),
        options=options or {},
        rubric=None,
        work_dir=tmp_path,
        crop_dir=tmp_path / "crops",
        warnings=warnings,
    ), warnings


def test_feature_flag_off_preserves_v1(monkeypatch, tmp_path):
    v1_calls = []
    v2_calls = []
    monkeypatch.setattr(vep.settings, "HTR_BATCH_PAGE_ENABLED", False)
    monkeypatch.setattr(
        vep,
        "_read_page_by_crops",
        lambda **kwargs: v1_calls.append(kwargs) or _v1_page(kwargs["physical_page_number"]),
    )
    monkeypatch.setattr(
        vep,
        "_read_page_by_batch",
        lambda **kwargs: v2_calls.append(kwargs) or _v1_page(kwargs["physical_page_number"]),
    )

    result, _warnings = _read(tmp_path, [1, 2, 3])

    assert len(v1_calls) == 1
    assert v2_calls == []
    assert result["read_strategy"] == "manifest_crops"


def test_v2_makes_one_batch_call_for_manifest_page(monkeypatch, tmp_path):
    numbers = [1, 3, 4, 6, 8, 9, 11, 12, 14, 15]
    batch_calls = []
    crop_calls = []
    _install_crop_prep(monkeypatch)

    def fake_batch(**kwargs):
        batch_calls.append(kwargs)
        return _batch_result(list(kwargs["expected_question_numbers"]))

    monkeypatch.setattr(vep, "transcribe_answer_batch", fake_batch)
    monkeypatch.setattr(
        vep,
        "transcribe_answer_crop",
        lambda *a, **k: crop_calls.append(1) or {"number": 0, "answer_transcription": "v1"},
    )

    result, _warnings = _read(tmp_path, numbers, {"htr_batch_page_enabled": True, "vision_model": "vision-mock"})

    assert len(batch_calls) == 1
    assert crop_calls == []
    assert batch_calls[0]["expected_question_numbers"] == numbers
    assert len(batch_calls[0]["context_image_paths"]) == 1
    assert 1 <= len(batch_calls[0]["contact_sheet_paths"]) <= 2
    assert result["read_strategy"] == "manifest_batch_v2"
    assert [q["number"] for q in result["questions"]] == numbers


def test_blank_box_is_not_sent_to_the_model_and_is_merged_back(monkeypatch, tmp_path):
    batch_calls = []
    _install_crop_prep(monkeypatch, blank_numbers={2})

    def fake_batch(**kwargs):
        batch_calls.append(kwargs)
        return _batch_result(list(kwargs["expected_question_numbers"]))

    monkeypatch.setattr(vep, "transcribe_answer_batch", fake_batch)

    result, _warnings = _read(tmp_path, [1, 2, 3], {"htr_batch_page_enabled": True})

    assert len(batch_calls) == 1
    assert batch_calls[0]["expected_question_numbers"] == [1, 3]
    by_number = {q["number"]: q for q in result["questions"]}
    assert list(by_number) == [1, 2, 3]
    assert by_number[2]["has_answer"] is False
    assert by_number[2]["answer_transcription"] == ""
    assert "Sem resposta" in by_number[2]["reading_notes"]
    assert by_number[1]["answer_transcription"] == "resp 1"
    assert by_number[3]["answer_transcription"] == "resp 3"


def test_v2_http_error_falls_back_to_v1(monkeypatch, tmp_path):
    batch_calls = []
    v1_calls = []
    _install_crop_prep(monkeypatch)

    def fail_batch(**kwargs):
        batch_calls.append(kwargs)
        raise OpenRouterVisionError("HTTP 503: unavailable")

    monkeypatch.setattr(vep, "transcribe_answer_batch", fail_batch)
    monkeypatch.setattr(
        vep,
        "_read_page_by_crops",
        lambda **kwargs: v1_calls.append(kwargs) or _v1_page(kwargs["physical_page_number"]),
    )

    warnings: list[str] = []
    result, warnings = _read(tmp_path, [1, 2], {"htr_batch_page_enabled": True}, warnings)

    assert len(batch_calls) == 1
    assert len(v1_calls) == 1
    assert result["read_strategy"] == "manifest_crops"
    assert result["questions"][0]["answer_transcription"] == "from v1"
    assert any("V1" in item or "recorte" in item.lower() for item in warnings)


def test_v2_schema_mismatch_falls_back_to_v1(monkeypatch, tmp_path):
    batch_calls = []
    v1_calls = []
    _install_crop_prep(monkeypatch)

    def bad_schema(**kwargs):
        batch_calls.append(kwargs)
        raise OpenRouterVisionError("HTR V2 devolveu questão duplicada.")

    monkeypatch.setattr(vep, "transcribe_answer_batch", bad_schema)
    monkeypatch.setattr(
        vep,
        "_read_page_by_crops",
        lambda **kwargs: v1_calls.append(kwargs) or _v1_page(kwargs["physical_page_number"]),
    )

    warnings: list[str] = []
    result, warnings = _read(tmp_path, [1, 2], {"htr_batch_page_enabled": True}, warnings)

    assert len(batch_calls) == 1
    assert len(v1_calls) == 1
    assert result["read_strategy"] == "manifest_crops"
    assert any("duplicada" in item or "V1" in item or "recorte" in item.lower() for item in warnings)


def test_htr_batch_error_falls_back_to_v1(monkeypatch, tmp_path):
    v1_calls = []
    _install_crop_prep(monkeypatch)

    def fail_batch(**kwargs):
        raise HTRBatchError("JSON inválido na transcrição em lote.")

    monkeypatch.setattr(vep, "transcribe_answer_batch", fail_batch)
    monkeypatch.setattr(
        vep,
        "_read_page_by_crops",
        lambda **kwargs: v1_calls.append(kwargs) or _v1_page(kwargs["physical_page_number"]),
    )

    warnings: list[str] = []
    result, warnings = _read(tmp_path, [1, 2], {"htr_batch_page_enabled": True}, warnings)

    assert len(v1_calls) == 1
    assert result["read_strategy"] == "manifest_crops"
    assert result["questions"][0]["answer_transcription"] == "from v1"
    assert any("V1" in item or "recorte" in item.lower() for item in warnings)


def test_unexpected_error_is_not_swallowed_by_v1_fallback(monkeypatch, tmp_path):
    v1_calls = []
    _install_crop_prep(monkeypatch)

    def boom(**kwargs):
        raise RuntimeError("bug de programacao no HTR V2")

    monkeypatch.setattr(vep, "transcribe_answer_batch", boom)
    monkeypatch.setattr(
        vep,
        "_read_page_by_crops",
        lambda **kwargs: v1_calls.append(kwargs) or _v1_page(kwargs["physical_page_number"]),
    )

    with pytest.raises(RuntimeError, match="bug de programacao no HTR V2"):
        _read(tmp_path, [1, 2], {"htr_batch_page_enabled": True})

    assert v1_calls == []


def test_more_than_fifteen_questions_skips_v2_and_uses_v1(monkeypatch, tmp_path):
    numbers = list(range(1, 17))
    v2_calls = []
    v1_calls = []
    batch_calls = []

    monkeypatch.setattr(
        vep,
        "_read_page_by_batch",
        lambda **kwargs: v2_calls.append(kwargs) or _v1_page(kwargs["physical_page_number"]),
    )
    monkeypatch.setattr(
        vep,
        "_read_page_by_crops",
        lambda **kwargs: v1_calls.append(kwargs) or _v1_page(kwargs["physical_page_number"]),
    )
    monkeypatch.setattr(vep, "transcribe_answer_batch", lambda **kwargs: batch_calls.append(kwargs))

    warnings: list[str] = []
    result, warnings = _read(tmp_path, numbers, {"htr_batch_page_enabled": True}, warnings)

    assert v2_calls == []
    assert batch_calls == []
    assert len(v1_calls) == 1
    assert result["read_strategy"] == "manifest_crops"
    assert any("15" in item for item in warnings)
