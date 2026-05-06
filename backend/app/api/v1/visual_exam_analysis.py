from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.exam import Exam, ExamQuestion
from app.models.user import User
from app.models.visual_exam import VisualExamAnswer, VisualExamRun, VisualExamRunStatus
from app.services.export.spreadsheet import export_results_xlsx
from app.services.visual_exam_pipeline import analyze_discursive_exam_pdf

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])

_MAX_BYTES = settings.MAX_UPLOAD_MB * 1024 * 1024


class KnownPipelineError(Exception):
    def __init__(self, code: str, user_message: str, detail: str, stage: str):
        super().__init__(detail)
        self.code = code
        self.user_message = user_message
        self.detail = detail
        self.stage = stage


class VisualAnswerUpdate(BaseModel):
    page_number: int
    question_number: int
    answer_transcription: str | None = None
    score: float | None = None
    verdict: str | None = None
    justification: str | None = None
    needs_human_review: bool = False
    review_reason: str | None = None


@router.post("/analyze-discursive-pdf", status_code=status.HTTP_200_OK)
async def analyze_discursive_pdf(
    file: UploadFile = File(...),
    exam_id: str = Form(...),
    vision_model: str | None = Form(default=None),
    text_model: str | None = Form(default=None),
    process_pages: str | None = Form(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    request_id = str(uuid.uuid4())
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Apenas arquivos PDF são aceitos.")
    if file.content_type and file.content_type not in {"application/pdf", "application/x-pdf"}:
        raise HTTPException(status_code=400, detail="O arquivo enviado não parece ser um PDF.")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Arquivo PDF vazio.")
    if len(raw) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"PDF excede {settings.MAX_UPLOAD_MB} MB.")

    try:
        exam = db.query(Exam).filter(Exam.id == exam_id).first()
        if not exam:
            raise KnownPipelineError(
                code="SELECTED_EXAM_NOT_FOUND",
                user_message="A prova selecionada não foi encontrada ou não possui gabarito cadastrado.",
                detail=f"Exam {exam_id} não encontrado.",
                stage="student_detection",
            )
        questions = (
            db.query(ExamQuestion)
            .filter(ExamQuestion.exam_id == exam.id)
            .order_by(ExamQuestion.question_number.asc())
            .all()
        )
        if not questions:
            raise KnownPipelineError(
                code="SELECTED_EXAM_NOT_FOUND",
                user_message="A prova selecionada não foi encontrada ou não possui gabarito cadastrado.",
                detail=f"Exam {exam_id} sem questões.",
                stage="grading",
            )

        rubric_payload = {
            "exam_id": str(exam.id),
            "exam_name": exam.name,
            "is_practical": bool(exam.is_practical),
            "questions": [
                {
                    "number": q.question_number,
                    "prompt": q.question_text,
                    "max_score": q.max_score,
                    "expected_answer": q.expected_answer,
                    "correction_criteria": q.correction_criteria,
                }
                for q in questions
            ],
        }

        run_id = uuid.uuid4()
        run_dir = settings.UPLOAD_DIR.resolve() / "visual_exam_runs" / str(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = run_dir / _safe_pdf_name(file.filename)
        pdf_path.write_bytes(raw)

        run = VisualExamRun(
            id=run_id,
            user_id=current_user.id,
            filename=file.filename,
            status=VisualExamRunStatus.PROCESSING,
            vision_model_used=vision_model or settings.OPENROUTER_VISION_MODEL,
            text_model_used=text_model or settings.OPENROUTER_TEXT_MODEL,
        )
        db.add(run)
        db.commit()

        result = await run_in_threadpool(
            analyze_discursive_exam_pdf,
            str(pdf_path),
            rubric_payload,
            {
                "vision_model": vision_model,
                "text_model": text_model,
                "process_pages": process_pages,
                "run_id": str(run_id),
                "is_practical": bool(exam.is_practical),
            },
        )
        raw_students = result.pop("_raw_students", [])

        run.status = (
            VisualExamRunStatus.SUCCESS if result.get("status") == "success" else VisualExamRunStatus.FAILED
        )
        run.pages_processed = int(result.get("pages_processed") or 0)
        run.vision_model_used = result.get("vision_model_used") or run.vision_model_used
        run.text_model_used = result.get("text_model_used") or run.text_model_used
        if result.get("errors"):
            run.error = " | ".join(str(error) for error in result["errors"])

        _persist_visual_answers(db, run.id, raw_students or result.get("students") or [])
        db.commit()

        if result.get("status") != "success":
            pipeline_errors = result.get("errors") or []
            detail = str(pipeline_errors[0]) if pipeline_errors else "Falha não especificada no pipeline."
            error_code = _infer_error_code(detail)
            stage = _infer_stage(error_code)
            raise KnownPipelineError(
                code=error_code,
                user_message=_error_message_for_code(error_code),
                detail=detail,
                stage=stage,
            )
        result["run_id"] = str(run.id)
        return {
            "ok": True,
            "data": result,
            "warnings": _normalize_warnings(result.get("warnings") or []),
            "request_id": request_id,
        }
    except KnownPipelineError as exc:
        logger.exception(
            "[analyze-discursive-pdf] erro conhecido request_id=%s code=%s stage=%s",
            request_id,
            exc.code,
            exc.stage,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error_code": exc.code,
                "message": exc.user_message,
                "detail": exc.detail[:500],
                "stage": exc.stage,
                "requires_manual_action": True,
                "request_id": request_id,
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        try:
            if "run" in locals():
                run.status = VisualExamRunStatus.FAILED
                run.error = str(exc)
                db.add(run)
                db.commit()
        except Exception:
            db.rollback()
        logger.exception("[analyze-discursive-pdf] erro inesperado request_id=%s", request_id)
        raise HTTPException(
            status_code=500,
            detail={
                "ok": False,
                "error_code": "UNEXPECTED_ERROR",
                "message": "Erro inesperado ao analisar o PDF.",
                "detail": str(exc)[:500],
                "stage": "unknown",
                "requires_manual_action": True,
                "request_id": request_id,
            },
        ) from exc


def _persist_visual_answers(db: Session, run_id: uuid.UUID, students: list[dict]) -> None:
    for page in students:
        student = page.get("student") or {}
        raw_vision = page.get("raw_vision_json") or page
        for question in page.get("questions") or []:
            grade = question.get("grade") or {}
            raw_grade = question.get("raw_grading_json") or grade
            db.add(
                VisualExamAnswer(
                    run_id=run_id,
                    student_name=page.get("detected_student_name") or student.get("name") or None,
                    registration=page.get("detected_registration") or student.get("registration") or None,
                    detected_student_code=page.get("detected_student_code") or student.get("student_code") or None,
                    class_name=student.get("class") or None,
                    page_number=int(
                        page.get("physical_page")
                        or page.get("page")
                        or raw_vision.get("physical_page")
                        or raw_vision.get("page_number")
                        or 0
                    ),
                    question_number=int(question.get("number") or 0),
                    prompt_detected=question.get("prompt_detected") or None,
                    answer_transcription=question.get("extracted_answer") or question.get("answer_transcription") or None,
                    reading_confidence=question.get("reading_confidence") or None,
                    ocr_confidence=(
                        float(question.get("ocr_confidence"))
                        if isinstance(question.get("ocr_confidence"), (int, float))
                        else None
                    ),
                    reading_notes=question.get("reading_notes") or None,
                    image_region=(
                        json.dumps(question.get("image_region"), ensure_ascii=False)
                        if question.get("image_region") is not None
                        else None
                    ),
                    score=grade.get("score") if isinstance(grade.get("score"), (int, float)) else None,
                    max_score=grade.get("max_score") if isinstance(grade.get("max_score"), (int, float)) else None,
                    verdict=grade.get("verdict") or None,
                    justification=grade.get("justification") or None,
                    detected_concepts_json=json.dumps(grade.get("detected_concepts") or [], ensure_ascii=False),
                    missing_concepts_json=json.dumps(grade.get("missing_concepts") or [], ensure_ascii=False),
                    needs_human_review=bool(
                        grade.get("needs_human_review")
                        or question.get("reading_confidence") == "baixa"
                    ),
                    review_reason=grade.get("review_reason") or None,
                    raw_vision_json=json.dumps(raw_vision, ensure_ascii=False),
                    raw_grading_json=json.dumps(raw_grade, ensure_ascii=False),
                )
            )


@router.patch("/runs/{run_id}/answers", status_code=status.HTTP_200_OK)
def update_visual_answer(
    run_id: uuid.UUID,
    payload: VisualAnswerUpdate,
    db: Session = Depends(get_db),
):
    answer = (
        db.query(VisualExamAnswer)
        .filter(
            VisualExamAnswer.run_id == run_id,
            VisualExamAnswer.page_number == payload.page_number,
            VisualExamAnswer.question_number == payload.question_number,
        )
        .first()
    )
    if not answer:
        raise HTTPException(status_code=404, detail="Resposta visual não encontrada.")

    if payload.answer_transcription is not None:
        answer.answer_transcription = payload.answer_transcription
    answer.score = payload.score
    if payload.verdict is not None:
        answer.verdict = payload.verdict
    if payload.justification is not None:
        answer.justification = payload.justification
    answer.needs_human_review = payload.needs_human_review
    answer.review_reason = payload.review_reason or None
    db.commit()
    return {"status": "ok"}


@router.get("/runs/{run_id}/export")
def export_visual_run(
    run_id: uuid.UUID,
    include_details: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    request_id = str(uuid.uuid4())
    try:
        run = db.query(VisualExamRun).filter(VisualExamRun.id == run_id).first()
        if not run:
            raise HTTPException(
                status_code=404,
                detail={
                    "ok": False,
                    "error_code": "RUN_NOT_FOUND",
                    "message": "Execução visual não encontrada.",
                    "detail": f"run_id={run_id}",
                    "stage": "database",
                    "requires_manual_action": True,
                    "request_id": request_id,
                },
            )

        answers = (
            db.query(VisualExamAnswer)
            .filter(VisualExamAnswer.run_id == run_id)
            .order_by(VisualExamAnswer.page_number, VisualExamAnswer.question_number)
            .all()
        )
        if not answers:
            raise HTTPException(
                status_code=404,
                detail={
                    "ok": False,
                    "error_code": "RUN_EXPORT_EMPTY",
                    "message": "Nenhum resultado para exportar.",
                    "detail": f"run_id={run_id} sem respostas persistidas.",
                    "stage": "database",
                    "requires_manual_action": True,
                    "request_id": request_id,
                },
            )

        expected_by_question = {
            int(a.question_number): _expected_answer_from_raw(a.raw_grading_json)
            for a in answers
            if _expected_answer_from_raw(a.raw_grading_json)
        }
        question_numbers = sorted({int(a.question_number) for a in answers})
        questions = [
            {
                "number": qn,
                "text": "",
                "max_score": None,
                "expected_answer": expected_by_question.get(qn, ""),
            }
            for qn in question_numbers
        ]

        grouped: dict[str, dict] = {}
        for a in answers:
            identity_key = (
                (a.detected_student_code or "").strip()
                or (a.registration or "").strip()
                or (a.student_name or "").strip()
                or f"pagina-{a.page_number}"
            )
            if identity_key not in grouped:
                grouped[identity_key] = {
                    "student_name": a.student_name or f"Aluno (pág. {a.page_number})",
                    "registration_number": a.registration or (a.detected_student_code or f"P{a.page_number}"),
                    "curso": "",
                    "turma": a.class_name or "",
                    "scores": {},
                    "total": 0.0,
                    "needs_review": False,
                    "observacoes": [],
                    "warnings": [],
                    "identity_source": "",
                    "question_details": [],
                }

            score_val = float(a.score) if a.score is not None else None
            grouped[identity_key]["scores"][int(a.question_number)] = score_val
            grouped[identity_key]["needs_review"] = (
                grouped[identity_key]["needs_review"] or bool(a.needs_human_review)
            )
            if a.review_reason:
                grouped[identity_key]["observacoes"].append(a.review_reason)
            if a.review_reason:
                grouped[identity_key]["warnings"].append(a.review_reason)
            grouped[identity_key]["question_details"].append(
                {
                    "question_number": int(a.question_number),
                    "score": score_val,
                    "verdict": a.verdict or "",
                    "comment": a.justification or "",
                    "expected_answer": expected_by_question.get(int(a.question_number), ""),
                    "transcription": a.answer_transcription or "",
                    "needs_review": bool(a.needs_human_review),
                    "review_reason": a.review_reason or "",
                    "technical_detail": a.review_reason or "",
                    "physical_page": a.page_number,
                    "transcription_confidence": a.ocr_confidence,
                    "warnings": [a.review_reason] if a.review_reason else [],
                }
            )

        for row in grouped.values():
            row["total"] = float(
                sum(score for score in row["scores"].values() if isinstance(score, (int, float)))
            )
            row["observacoes"] = "; ".join(row["observacoes"][:5])

        xlsx_bytes = export_results_xlsx(
            run.filename,
            questions,
            list(grouped.values()),
            include_details=include_details,
        )
        safe_name = Path(run.filename).stem.replace('"', "").replace("'", "")
        return Response(
            content=xlsx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="notas_manuscrita_{safe_name}.xlsx"'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("[runs-export] erro inesperado request_id=%s run_id=%s", request_id, run_id)
        raise HTTPException(
            status_code=500,
            detail={
                "ok": False,
                "error_code": "DATABASE_ERROR",
                "message": "Erro ao salvar os resultados no banco.",
                "detail": str(exc)[:500],
                "stage": "database",
                "requires_manual_action": True,
                "request_id": request_id,
            },
        ) from exc


def _safe_pdf_name(filename: str) -> str:
    name = Path(filename).name.replace("\\", "_").replace("/", "_")
    return name if name.lower().endswith(".pdf") else f"{name}.pdf"


def _expected_answer_from_raw(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    if not isinstance(parsed, dict):
        return ""
    return str(parsed.get("expected_answer") or parsed.get("rubric_expected_answer") or "")


def _infer_error_code(detail: str) -> str:
    low = (detail or "").lower()
    if "rubrica" in low and "troca" in low:
        return "RUBRIC_MISMATCH"
    if "json" in low and ("schema" in low or "incompleto" in low or "invalid" in low):
        return "JSON_SCHEMA_INVALID"
    if "render" in low or "pdf" in low and "imagem" in low:
        return "PDF_RENDER_FAILED"
    if "qr" in low:
        return "QR_READ_FAILED"
    if "transcri" in low:
        return "TRANSCRIPTION_FAILED"
    if "grading" in low or "corre" in low:
        return "GRADING_FAILED"
    if "database" in low or "sql" in low or "psycopg" in low:
        return "DATABASE_ERROR"
    return "PDF_ANALYSIS_FAILED"


def _infer_stage(error_code: str) -> str:
    mapping = {
        "PDF_RENDER_FAILED": "student_detection",
        "QR_READ_FAILED": "qr_reading",
        "TRANSCRIPTION_FAILED": "transcription",
        "GRADING_FAILED": "grading",
        "JSON_SCHEMA_INVALID": "grading",
        "RUBRIC_MISMATCH": "grading",
        "DATABASE_ERROR": "database",
    }
    return mapping.get(error_code, "unknown")


def _error_message_for_code(error_code: str) -> str:
    mapping = {
        "PDF_RENDER_FAILED": "Não foi possível converter o PDF em imagens.",
        "QR_READ_FAILED": "O QR Code não pôde ser lido em uma ou mais páginas.",
        "STUDENT_LINK_WEAK": "Algumas páginas foram vinculadas por fallback e precisam de revisão.",
        "TRANSCRIPTION_FAILED": "Falha na transcrição visual de uma resposta manuscrita.",
        "GRADING_FAILED": "Falha na correção automática de uma ou mais respostas.",
        "JSON_SCHEMA_INVALID": "A IA retornou uma resposta fora do formato esperado.",
        "DATABASE_ERROR": "Erro ao salvar os resultados no banco.",
        "SELECTED_EXAM_NOT_FOUND": "A prova selecionada não foi encontrada ou não possui gabarito cadastrado.",
        "RUBRIC_MISMATCH": "A rubrica da questão não corresponde à questão detectada.",
        "PDF_ANALYSIS_FAILED": "Não foi possível analisar o PDF.",
    }
    return mapping.get(error_code, "Não foi possível analisar o PDF.")


def _normalize_warnings(raw_warnings: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in raw_warnings:
        text = str(item)
        code = _infer_error_code(text) if "falha" in text.lower() or "json" in text.lower() else "STUDENT_LINK_WEAK"
        stage = _infer_stage(code)
        normalized.append(
            {
                "code": code,
                "message": _error_message_for_code(code) if code != "STUDENT_LINK_WEAK" else text,
                "detail": text,
                "stage": stage,
            }
        )
    return normalized
