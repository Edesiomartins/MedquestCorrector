from app.services.vision.sheet_geometry import load_manifest


def _payload():
    return {
        "version": 3,
        "template_repeat": True,
        "template_page_count": 2,
        "pages": [
            {
                "physical_index": 0,
                "exam_id": "e",
                "student_id": "",
                "page_in_student": 1,
                "total_pages_for_student": 2,
                "boxes": [
                    {
                        "question_number": 38,
                        "x_pt": 10,
                        "y_bottom_pt": 20,
                        "width_pt": 100,
                        "height_pt": 50,
                    }
                ],
            },
            {
                "physical_index": 1,
                "exam_id": "e",
                "student_id": "",
                "page_in_student": 2,
                "total_pages_for_student": 2,
                "boxes": [
                    {
                        "question_number": 39,
                        "x_pt": 10,
                        "y_bottom_pt": 20,
                        "width_pt": 100,
                        "height_pt": 50,
                    }
                ],
            },
        ],
    }


def test_template_manifest_repeats_pages_by_modulo():
    manifest = load_manifest(_payload())
    assert manifest is not None
    assert manifest.page(0).box(38) is not None
    assert manifest.page(1).box(39) is not None
    assert manifest.page(2).box(38) is not None
    assert manifest.page(3).box(39) is not None


def test_normal_manifest_does_not_repeat():
    payload = _payload()
    payload.pop("template_repeat")
    payload.pop("template_page_count")
    manifest = load_manifest(payload)
    assert manifest is not None
    assert manifest.page(2) is None
