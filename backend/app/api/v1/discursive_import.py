from __future__ import annotations

import base64
import json
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.exam import Exam, ExamQuestion
from app.services.discursive_import.canonical_pdf import add_student_identity_header
from app.services.discursive_import.layout_detector import (
    build_template_manifest,
    detect_pdf_layout,
    normalize_docx_to_pdf,
    validate_confirmed_layout,
)

router = APIRouter(dependencies=[Depends(get_current_user)])
MAX_IMPORT_PAGES = 20


class LayoutPageIn(BaseModel):
    page_index: int = Field(ge=0)
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)


class LayoutQuestionIn(BaseModel):
    question_number: int = Field(gt=0)
    page_index: int = Field(ge=0)
    question_text: str = ""
    x_pt: float
    y_bottom_pt: float
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    provenance: str | None = None
    expected_answer: str = ""
    correction_criteria: str | None = None
    max_score: float = Field(default=1.0, gt=0)


class ConfirmLayoutIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    class_id: UUID | None = None
    pages: list[LayoutPageIn]
    questions: list[LayoutQuestionIn]


def _ensure_enabled() -> None:
    if not settings.DISCURSIVE_UNIVERSAL_IMPORT_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "ok": False,
                "message": "Importação universal de discursivas está desativada.",
                "stage": "discursive_import",
            },
        )


def _safe_title(filename: str) -> str:
    stem = Path(filename or "Prova discursiva externa").stem.replace("_", " ").strip()
    return stem[:200] or "Prova discursiva externa"


@router.post("/detect")
async def detect_external_discursive_layout(file: UploadFile = File(...)):
    _ensure_enabled()
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "message": "Formato não suportado.",
                "detail": "Envie uma prova discursiva em PDF ou DOCX.",
                "stage": "discursive_import",
            },
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")
    max_bytes = int(settings.MAX_UPLOAD_MB) * 1024 * 1024
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Arquivo acima do limite de {settings.MAX_UPLOAD_MB} MB.",
        )

    try:
        canonical_pdf: bytes | None = None
        extra_warnings: list[str] = []
        if suffix == ".docx":
            normalized = normalize_docx_to_pdf(raw)
            canonical_pdf = add_student_identity_header(normalized["canonical_pdf"])
            extra_warnings.extend(normalized.get("warnings") or [])
            extra_warnings.append(
                "O PDF canônico inclui campos de Nome e Matrícula em todas as páginas para identificação dos scans."
            )
            detected = detect_pdf_layout(canonical_pdf)
            detected["source_format"] = "docx"
        else:
            detected = detect_pdf_layout(raw)

        if len(detected.get("pages") or []) > MAX_IMPORT_PAGES:
            raise ValueError(f"A importação aceita até {MAX_IMPORT_PAGES} páginas por prova discursiva.")

        warnings = [*extra_warnings, *(detected.get("warnings") or [])]
        response = {
            "ok": True,
            "source_format": detected.get("source_format") or suffix.lstrip("."),
            "suggested_title": _safe_title(filename),
            "pages": detected.get("pages") or [],
            "questions": detected.get("questions") or [],
            "warnings": warnings,
            "requires_confirmation": True,
        }
        if canonical_pdf is not None:
            response["canonical_pdf_data_url"] = (
                "data:application/pdf;base64," + base64.b64encode(canonical_pdf).decode("ascii")
            )
        return response
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "message": "Não foi possível detectar o layout da prova discursiva.",
                "detail": str(exc)[:500],
                "stage": "discursive_layout_detection",
            },
        ) from exc


@router.post("/confirm", status_code=status.HTTP_201_CREATED)
def confirm_external_discursive_layout(payload: ConfirmLayoutIn, db: Session = Depends(get_db)):
    _ensure_enabled()
    if not payload.pages:
        raise HTTPException(status_code=422, detail="A prova precisa ter ao menos uma página.")
    if not payload.questions:
        raise HTTPException(status_code=422, detail="Adicione ao menos uma questão antes de salvar.")

    pages = [page.model_dump() for page in payload.pages]
    questions = [question.model_dump() for question in payload.questions]
    errors, warnings = validate_confirmed_layout(pages, questions)
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "message": "O layout precisa de ajustes antes de ser salvo.",
                "errors": errors,
                "warnings": warnings,
                "stage": "discursive_layout_confirmation",
            },
        )

    exam = Exam(
        name=payload.title.strip(),
        class_id=payload.class_id,
        is_practical=False,
    )
    try:
        db.add(exam)
        db.flush()

        for item in sorted(questions, key=lambda value: int(value["question_number"])):
            db.add(
                ExamQuestion(
                    exam_id=exam.id,
                    question_number=int(item["question_number"]),
                    question_text=str(item.get("question_text") or "").strip()
                    or f"Questão {int(item['question_number'])}",
                    expected_answer=str(item.get("expected_answer") or "").strip()
                    or "Resposta esperada não informada.",
                    correction_criteria=(str(item.get("correction_criteria") or "").strip() or None),
                    max_score=float(item.get("max_score") or 1.0),
                    page_number=int(item["page_index"]) + 1,
                    box_x=float(item["x_pt"]),
                    box_y=float(item["y_bottom_pt"]),
                    box_w=float(item["width_pt"]),
                    box_h=float(item["height_pt"]),
                )
            )

        manifest = build_template_manifest(str(exam.id), pages, questions)
        exam.layout_manifest_json = json.dumps(manifest, ensure_ascii=False)
        db.commit()
        db.refresh(exam)
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={
                "ok": False,
                "message": "Não foi possível salvar a prova discursiva externa.",
                "detail": str(exc)[:500],
                "stage": "discursive_layout_save",
            },
        ) from exc

    return {
        "ok": True,
        "exam_id": str(exam.id),
        "title": exam.name,
        "questions_created": len(questions),
        "template_page_count": len(pages),
        "warnings": warnings,
        "next_step": "Revise a resposta esperada, critérios e valor de cada questão antes de corrigir os scans.",
    }
