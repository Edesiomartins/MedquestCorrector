from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.exam import Exam
from app.models.grading import QuestionScore, StudentResult
from app.models.pipeline import UploadBatch
from app.models.user import User
from app.models.visual_exam import VisualExamAnswer, VisualExamRun

router = APIRouter()


@router.get("/corrections")
def list_correction_history(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    items = [
        *_standard_batch_history(db, current_user.id),
        *_visual_run_history(db, current_user.id),
    ]
    items.sort(key=lambda item: item["created_at"] or "", reverse=True)
    return {"items": items[:limit]}


def _standard_batch_history(db: Session, user_id: UUID) -> list[dict]:
    batches = (
        db.query(UploadBatch, Exam)
        .join(Exam, Exam.id == UploadBatch.exam_id)
        .filter(UploadBatch.user_id == user_id)
        .order_by(UploadBatch.created_at.desc())
        .all()
    )

    rows: list[dict] = []
    for batch, exam in batches:
        student_count = (
            db.query(StudentResult.id)
            .filter(StudentResult.batch_id == batch.id)
            .count()
        )
        question_count = (
            db.query(QuestionScore.id)
            .join(StudentResult, StudentResult.id == QuestionScore.student_result_id)
            .filter(StudentResult.batch_id == batch.id)
            .count()
        )
        pending_review = (
            db.query(QuestionScore.id)
            .join(StudentResult, StudentResult.id == QuestionScore.student_result_id)
            .filter(
                StudentResult.batch_id == batch.id,
                QuestionScore.requires_manual_review.is_(True),
            )
            .count()
        )
        rows.append(
            {
                "id": str(batch.id),
                "kind": "batch",
                "exam_name": exam.name,
                "is_practical": bool(exam.is_practical),
                "status": batch.status.value if hasattr(batch.status, "value") else str(batch.status),
                "created_at": batch.created_at.isoformat() if batch.created_at else None,
                "filename": batch.file_url,
                "students_count": student_count,
                "questions_count": question_count,
                "pending_review_count": pending_review,
                "export_path": f"/reviews/batch/{batch.id}/export",
            }
        )
    return rows


def _visual_run_history(db: Session, user_id: UUID) -> list[dict]:
    runs = (
        db.query(VisualExamRun)
        .filter(VisualExamRun.user_id == user_id)
        .order_by(VisualExamRun.created_at.desc())
        .all()
    )

    rows: list[dict] = []
    for run in runs:
        answers = db.query(VisualExamAnswer).filter(VisualExamAnswer.run_id == run.id)
        distinct_students = {
            (
                (answer.detected_student_code or "").strip()
                or (answer.registration or "").strip()
                or (answer.student_name or "").strip()
                or f"pagina-{answer.page_number}"
            )
            for answer in answers.all()
        }
        pending_review = answers.filter(VisualExamAnswer.needs_human_review.is_(True)).count()
        rows.append(
            {
                "id": str(run.id),
                "kind": "visual",
                "exam_name": run.filename,
                "is_practical": False,
                "status": run.status.value if hasattr(run.status, "value") else str(run.status),
                "created_at": run.created_at.isoformat() if run.created_at else None,
                "filename": run.filename,
                "students_count": len(distinct_students),
                "questions_count": answers.count(),
                "pending_review_count": pending_review,
                "export_path": f"/runs/{run.id}/export",
            }
        )
    return rows
