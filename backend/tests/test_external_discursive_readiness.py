import json
from types import SimpleNamespace

import pytest

from app.services.discursive_import.readiness import (
    MISSING_GABARITO_MESSAGE,
    ExternalDiscursiveNotReadyError,
    require_external_discursive_ready,
)


EXTERNAL_MANIFEST = {
    "version": 1,
    "source": "external_discursive",
    "pages": [
        {
            "physical_index": 0,
            "exam_id": "exam-1",
            "student_id": "",
            "page_in_student": 1,
            "total_pages_for_student": 1,
            "boxes": [
                {"question_number": 38, "x_pt": 42.0, "y_bottom_pt": 120.0, "width_pt": 500.0, "height_pt": 180.0}
            ],
        }
    ],
}


def _exam(manifest=None):
    return SimpleNamespace(layout_manifest_json=json.dumps(manifest if manifest is not None else EXTERNAL_MANIFEST))


def _question(expected_answer: str):
    return SimpleNamespace(expected_answer=expected_answer)


def test_external_discursive_with_empty_expected_answer_is_blocked():
    with pytest.raises(ExternalDiscursiveNotReadyError, match="gabarito"):
        require_external_discursive_ready(_exam(), [_question("")])


def test_placeholder_expected_answer_is_treated_as_blank():
    with pytest.raises(ExternalDiscursiveNotReadyError) as exc:
        require_external_discursive_ready(
            _exam(),
            [_question("Resposta esperada não informada.")],
        )
    assert str(exc.value) == MISSING_GABARITO_MESSAGE


def test_complete_external_discursive_is_allowed():
    require_external_discursive_ready(_exam(), [_question("articulação plana")])


def test_legacy_exam_without_external_source_is_not_blocked():
    require_external_discursive_ready(
        _exam({"version": 1, "pages": EXTERNAL_MANIFEST["pages"]}),
        [_question("")],
    )


def test_practical_or_empty_manifest_is_not_blocked():
    require_external_discursive_ready(SimpleNamespace(layout_manifest_json=None), [_question("")])
    require_external_discursive_ready(SimpleNamespace(layout_manifest_json=""), [_question("")])
