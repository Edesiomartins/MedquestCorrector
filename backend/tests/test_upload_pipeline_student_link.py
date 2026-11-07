from uuid import UUID, uuid4

from app.services.vision.qr_decode import PageQrPayload
from app.workers.pipeline import (
    IDENTITY_SOURCE_QR,
    IDENTITY_SOURCE_ANONYMOUS,
    IDENTITY_SOURCE_HEADER_OCR,
    _pick_student_identity,
)


def test_pick_student_prefers_qr_over_manifest_when_exam_matches():
    exam_id = str(uuid4())
    uuid_01 = str(uuid4())
    uuid_09 = str(uuid4())
    qr = PageQrPayload(
        exam_id=exam_id,
        student_id=uuid_09,
        page_in_student=1,
        total_pages_for_student=1,
    )
    sid, src, warnings = _pick_student_identity(qr, None, uuid_01, exam_id)
    assert sid == UUID(uuid_09)
    assert src == IDENTITY_SOURCE_QR
    assert warnings


def test_pick_student_does_not_use_manifest_when_qr_is_missing():
    exam_id = str(uuid4())
    uuid_01 = str(uuid4())
    sid, src, warnings = _pick_student_identity(None, None, uuid_01, exam_id)
    assert sid is None
    assert src == IDENTITY_SOURCE_ANONYMOUS
    assert any("Manifesto ignorado" in w for w in warnings)


def test_qr_exam_mismatch_does_not_fall_back_to_manifest():
    exam_id = str(uuid4())
    uuid_01 = str(uuid4())
    uuid_09 = str(uuid4())
    qr = PageQrPayload(
        exam_id="outro-exam-id",
        student_id=uuid_09,
        page_in_student=1,
        total_pages_for_student=1,
    )
    sid, src, warnings = _pick_student_identity(qr, None, uuid_01, exam_id)
    assert sid is None
    assert src == IDENTITY_SOURCE_ANONYMOUS
    assert any("exam_id divergente" in w for w in warnings)
    assert any("Manifesto ignorado" in w for w in warnings)


def test_pick_student_uses_header_when_qr_invalid_and_header_available():
    exam_id = str(uuid4())
    header_uuid = UUID(str(uuid4()))
    qr = PageQrPayload(
        exam_id="outro-exam-id",
        student_id=str(uuid4()),
        page_in_student=1,
        total_pages_for_student=1,
    )
    sid, src, _warnings = _pick_student_identity(qr, header_uuid, None, exam_id)
    assert sid == header_uuid
    assert src == IDENTITY_SOURCE_HEADER_OCR
