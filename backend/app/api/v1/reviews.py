import json
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.storage import path_from_local_url
from app.services.htr_dataset_bundle import build_bundle
from app.services.htr_labeling import export_dataset, record_review
from app.models.exam import Exam, ExamQuestion
from app.models.grading import QuestionScore, ResultStatus, StudentResult
from app.models.pipeline import BatchStatus, UploadBatch
from app.models.student import Student
from app.models.user import Class
from app.schemas.review import (
    BatchResultsResponse,
    BatchResultsStats,
    QuestionScoreDetail,
    StudentResultDetail,
    UpdateScore,
)
from app.services.export.spreadsheet import export_results_xlsx

router = APIRouter(dependencies=[Depends(get_current_user)])
logger = logging.getLogger(__name__)


def _latest_review_batch_id(db: Session) -> UUID | None:
    """Retorna o lote mais recente para manter a exportação após o fim da fila."""
    row = (
        db.query(UploadBatch.id)
        .order_by(UploadBatch.created_at.desc())
        .first()
    )
    return row[0] if row else None


def _structured_http_error(*, status_code: int, code: str, message: str, detail: str, stage: str):
    request_id = str(uuid4())
    logger.exception(
        "[reviews-api] request_id=%s code=%s stage=%s detail=%s",
        request_id,
        code,
        stage,
        detail,
    )
    raise HTTPException(
        status_code=status_code,
        detail={
            "ok": False,
            "error_code": code,
            "message": message,
            "detail": detail[:500],
            "stage": stage,
            "requires_manual_action": True,
            "request_id": request_id,
        },
    )


def _effective_question_score(s: QuestionScore) -> float:
    if s.final_score is not None:
        return float(s.final_score)
    return float(s.ai_score or 0)


def _exam_id_from_student_result(db: Session, sr: StudentResult | None) -> UUID | None:
    """exam_id vem do lote persistido, nunca do payload da revisão."""
    if sr is None:
        return None
    batch = db.query(UploadBatch).filter(UploadBatch.id == sr.batch_id).first()
    if batch is None:
        logger.warning(
            "StudentResult %s sem UploadBatch %s; exam_id indisponível para rotulagem HTR.",
            sr.id,
            sr.batch_id,
        )
        return None
    return batch.exam_id


def _batch_completion_recheck(db: Session, batch_id: UUID) -> None:
    pending = (
        db.query(QuestionScore)
        .join(StudentResult)
        .filter(
            StudentResult.batch_id == batch_id,
            QuestionScore.requires_manual_review.is_(True),
        )
        .first()
    )
    batch = db.query(UploadBatch).filter(UploadBatch.id == batch_id).first()
    if not batch:
        return
    batch.status = BatchStatus.REVIEW_PENDING if pending else BatchStatus.DONE
    db.commit()


@router.get("/batch/{batch_id}", response_model=BatchResultsResponse)
def list_batch_results(batch_id: UUID, db: Session = Depends(get_db)):
    """Lista resultados do lote com totais por categoria."""
    results = (
        db.query(StudentResult)
        .filter(StudentResult.batch_id == batch_id)
        .order_by(StudentResult.page_number)
        .all()
    )
    details = [_build_detail(db, r) for r in results]

    auto_ap = sum(1 for r in results if r.status == ResultStatus.AUTO_APPROVED)
    reviewed = sum(1 for r in results if r.status == ResultStatus.REVIEWED)
    pending_review = (
        db.query(StudentResult.id)
        .filter(StudentResult.batch_id == batch_id)
        .join(QuestionScore)
        .filter(QuestionScore.requires_manual_review.is_(True))
        .distinct()
        .count()
    )

    stats = BatchResultsStats(
        total=len(results),
        auto_approved=auto_ap,
        pending_review=pending_review,
        reviewed=reviewed,
    )
    return BatchResultsResponse(results=details, stats=stats)


@router.get("/next", response_model=StudentResultDetail)
def get_next_pending(db: Session = Depends(get_db)):
    """Próximo aluno com ao menos uma questão ainda marcada para revisão manual."""
    row = (
        db.query(StudentResult)
        .join(QuestionScore)
        .join(UploadBatch, StudentResult.batch_id == UploadBatch.id)
        .filter(
            QuestionScore.requires_manual_review.is_(True),
            StudentResult.status != ResultStatus.REVIEWED,
            StudentResult.status != ResultStatus.AUTO_APPROVED,
        )
        .order_by(UploadBatch.created_at.desc(), StudentResult.page_number)
        .first()
    )
    if not row:
        latest_batch_id = _latest_review_batch_id(db)
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error_code": "NO_PENDING_REVIEW",
                "message": "Nenhuma correção pendente de revisão.",
                "detail": "Fila de revisão vazia.",
                "stage": "review",
                "requires_manual_action": False,
                "request_id": str(uuid4()),
                "latest_batch_id": str(latest_batch_id) if latest_batch_id else None,
            },
        )
    return _build_detail(db, row)


@router.post("/scores/{score_id}", status_code=status.HTTP_200_OK)
def update_score(
    score_id: UUID,
    payload: UpdateScore,
    db: Session = Depends(get_db),
):
    qs = db.query(QuestionScore).filter(QuestionScore.id == score_id).first()
    if not qs:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error_code": "REVIEW_SCORE_NOT_FOUND",
                "message": "Score não encontrado.",
                "detail": f"score_id={score_id}",
                "stage": "review",
                "requires_manual_action": True,
                "request_id": str(uuid4()),
            },
        )

    sr = db.query(StudentResult).filter(StudentResult.id == qs.student_result_id).first()
    exam_id = _exam_id_from_student_result(db, sr)

    # A revisão da transcrição vira dado rotulado antes de a leitura antiga ser
    # sobrescrita — é o par (recorte, leitura do modelo, leitura humana), o dado
    # mais caro deste domínio, colhido sem esforço adicional.
    # Ver docs/HTR_PLANO_EXECUCAO.md, item 13.
    if payload.extracted_answer_text is not None:
        if exam_id is not None:
            record_review(
                db,
                question_score=qs,
                human_transcription=payload.extracted_answer_text,
                student_id=sr.student_id if sr else None,
                exam_id=exam_id,
            )
        else:
            logger.error(
                "Rótulo HTR ignorado por ausência de exam_id (question_score=%s, "
                "student_result_id=%s). A revisão da nota segue normalmente.",
                qs.id,
                sr.id if sr else qs.student_result_id,
            )
        qs.extracted_answer_text = payload.extracted_answer_text or None
        qs.transcription_edited_by_human = True

    qs.final_score = payload.final_score
    qs.professor_comment = payload.professor_comment
    qs.requires_manual_review = False
    qs.manual_review_reason = None
    db.commit()

    if sr:
        _recalc_total(db, sr)
        _batch_completion_recheck(db, sr.batch_id)
    return {"status": "ok"}


@router.get("/scores/{score_id}/crop")
def get_score_crop(score_id: UUID, db: Session = Depends(get_db)):
    """Serve o recorte da caixa de resposta desta questão.

    Existe para que a tela de revisão mostre a imagem ao lado da transcrição.
    Conferir quatro palavras de cursiva olhando o recorte leva três segundos; sem
    a imagem o revisor não tem como revisar — ele aprova.
    """
    qs = db.query(QuestionScore).filter(QuestionScore.id == score_id).first()
    if not qs or not qs.answer_crop_path:
        raise HTTPException(status_code=404, detail="Recorte não disponível para esta questão.")

    try:
        path = path_from_local_url(qs.answer_crop_path)
    except ValueError:
        # Inclui os registros antigos, cujo `answer_crop_path` era a string
        # `batch=.../page=.../q=...` e não uma URL `local:`.
        logger.info("answer_crop_path sem arquivo associado: %r", qs.answer_crop_path)
        raise HTTPException(status_code=404, detail="Recorte não disponível para esta questão.")

    if not path.is_file():
        raise HTTPException(status_code=404, detail="Arquivo do recorte não encontrado.")

    return Response(
        content=path.read_bytes(),
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/htr-dataset")
def export_htr_dataset(
    exam_id: UUID | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=20000),
    db: Session = Depends(get_db),
):
    """Exporta os pares rotulados no formato de `scripts/eval_htr.py`.

    Cada correção de transcrição feita na revisão entra aqui. É o conjunto de
    avaliação do item 3 crescendo sozinho, com dados de prova real em vez de
    fixtures — e, em alguns milhares de exemplos, a base para um ajuste fino.
    """
    rows = export_dataset(db, exam_id=exam_id, limit=limit)
    body = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    return Response(
        content=(body + "\n") if body else "",
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="labels.jsonl"'},
    )


@router.get("/htr-dataset-bundle")
def export_htr_dataset_bundle(
    exam_id: UUID = Query(..., description="Prova a exportar. Obrigatorio: nao ha exportacao global."),
    limit: int = Query(default=1000, ge=1, le=20000),
    db: Session = Depends(get_db),
):
    """Exporta o dataset HTR de UMA prova como ZIP portatil.

    Diferente de `/htr-dataset`, que devolve so o `labels.jsonl` com caminhos
    internos do servidor, aqui vao as imagens junto e os `crop` viram caminhos
    relativos (`crops/crop_000001.png`) — o bundle roda direto no
    `scripts/benchmark_htr_models.py` em qualquer maquina.

    `exam_id` e obrigatorio de proposito: exportacao global de todos os recortes
    do sistema num arquivo so e uma superficie de vazamento que esta etapa nao
    abre. O conteudo passa por lista branca de campos — nenhum dado de aluno,
    gabarito ou criterio de correcao entra no arquivo.
    """
    rows = export_dataset(db, exam_id=exam_id, limit=limit)
    payload, manifest = build_bundle(rows, exam_id=exam_id)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="medquest_htr_dataset_{exam_id}.zip"',
            "X-Htr-Samples-Exported": str(manifest["samples_exported"]),
            "X-Htr-Missing-Crops": str(manifest["missing_crops"]),
        },
    )


@router.post("/results/{result_id}/approve", status_code=status.HTTP_200_OK)
def approve_result(result_id: UUID, db: Session = Depends(get_db)):
    """Finaliza revisão das questões pendentes deste aluno."""
    sr = db.query(StudentResult).filter(StudentResult.id == result_id).first()
    if not sr:
        raise HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error_code": "REVIEW_RESULT_NOT_FOUND",
                "message": "Resultado não encontrado.",
                "detail": f"result_id={result_id}",
                "stage": "review",
                "requires_manual_action": True,
                "request_id": str(uuid4()),
            },
        )

    scores = db.query(QuestionScore).filter(QuestionScore.student_result_id == sr.id).all()
    for s in scores:
        if not s.requires_manual_review:
            continue
        if s.final_score is None:
            s.final_score = s.ai_score
        s.requires_manual_review = False
        s.manual_review_reason = None

    sr.status = ResultStatus.REVIEWED
    _recalc_total(db, sr)

    remaining = (
        db.query(StudentResult)
        .join(QuestionScore)
        .filter(QuestionScore.requires_manual_review.is_(True))
        .distinct()
        .count()
    )
    _batch_completion_recheck(db, sr.batch_id)

    return {"status": "ok", "remaining": remaining}


@router.get("/batch/{batch_id}/export")
def export_batch(
    batch_id: UUID,
    include_details: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    try:
        batch = db.query(UploadBatch).filter(UploadBatch.id == batch_id).first()
        if not batch:
            raise HTTPException(
                status_code=404,
                detail={
                    "ok": False,
                    "error_code": "BATCH_NOT_FOUND",
                    "message": "Lote não encontrado.",
                    "detail": f"batch_id={batch_id}",
                    "stage": "database",
                    "requires_manual_action": True,
                    "request_id": str(uuid4()),
                },
            )

        exam = db.query(Exam).filter(Exam.id == batch.exam_id).first()
        if not exam:
            raise HTTPException(
                status_code=404,
                detail={
                    "ok": False,
                    "error_code": "SELECTED_EXAM_NOT_FOUND",
                    "message": "A prova selecionada não foi encontrada ou não possui gabarito cadastrado.",
                    "detail": f"exam_id={batch.exam_id}",
                    "stage": "database",
                    "requires_manual_action": True,
                    "request_id": str(uuid4()),
                },
            )
        questions = (
            db.query(ExamQuestion)
            .filter(ExamQuestion.exam_id == exam.id)
            .order_by(ExamQuestion.question_number)
            .all()
        )

        turma_name = "—"
        if exam.class_id:
            turma = db.query(Class).filter(Class.id == exam.class_id).first()
            if turma:
                turma_name = turma.name

        student_results = (
            db.query(StudentResult)
            .filter(StudentResult.batch_id == batch_id)
            .order_by(StudentResult.page_number)
            .all()
        )

        q_dicts = [
            {
                "number": q.question_number,
                "text": q.question_text,
                "max_score": q.max_score,
                "expected_answer": q.expected_answer,
            }
            for q in questions
        ]

        rows = []
        for sr in student_results:
            student = db.query(Student).filter(Student.id == sr.student_id).first() if sr.student_id else None
            scores_db = db.query(QuestionScore).filter(QuestionScore.student_result_id == sr.id).all()

            score_map = {}
            question_details = []
            for s in scores_db:
                q = db.query(ExamQuestion).filter(ExamQuestion.id == s.question_id).first()
                if q:
                    score_map[q.question_number] = _effective_question_score(s)
                    question_details.append(
                        {
                            "question_number": q.question_number,
                            "score": _effective_question_score(s),
                            "verdict": "",
                            "comment": s.ai_justification or "",
                            "expected_answer": q.expected_answer,
                            "transcription": s.extracted_answer_text or "",
                            "needs_review": bool(s.requires_manual_review),
                            "review_reason": s.manual_review_reason or "",
                            "technical_detail": s.manual_review_reason or "",
                            "physical_page": s.source_page_number,
                            "transcription_confidence": s.transcription_confidence or s.ocr_confidence,
                            "warnings": s.warnings_json or [],
                        }
                    )

            rows.append({
                "student_name": student.name if student else f"Aluno (pág. {sr.page_number})",
                "registration_number": student.registration_number if student else f"P{sr.page_number}",
                "curso": student.curso if student else "",
                "turma": turma_name,
                "scores": score_map,
                "total": sr.total_score,
                "needs_review": any(s.requires_manual_review for s in scores_db),
                "observacoes": "; ".join(sr.warnings_json or []),
                "warnings": sr.warnings_json or [],
                "identity_source": sr.identity_source,
                "question_details": question_details,
            })

        xlsx_bytes = export_results_xlsx(
            exam.name,
            q_dicts,
            rows,
            include_details=include_details,
        )

        return Response(
            content=xlsx_bytes,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="notas_{exam.name}.xlsx"'},
        )
    except HTTPException:
        raise
    except Exception as exc:
        _structured_http_error(
            status_code=500,
            code="DATABASE_ERROR",
            message="Erro ao salvar os resultados no banco.",
            detail=str(exc),
            stage="database",
        )


def _build_detail(db: Session, sr: StudentResult) -> StudentResultDetail:
    student = db.query(Student).filter(Student.id == sr.student_id).first() if sr.student_id else None
    scores = db.query(QuestionScore).filter(QuestionScore.student_result_id == sr.id).all()

    details = []
    for s in scores:
        q = db.query(ExamQuestion).filter(ExamQuestion.id == s.question_id).first()
        if q:
            details.append(
                QuestionScoreDetail(
                    id=s.id,
                    question_number=q.question_number,
                    question_text=q.question_text,
                    max_score=q.max_score,
                    ai_score=s.ai_score,
                    ai_justification=s.ai_justification,
                    final_score=s.final_score,
                    professor_comment=s.professor_comment,
                    extracted_answer_text=s.extracted_answer_text,
                    ocr_provider=s.ocr_provider,
                    ocr_confidence=s.ocr_confidence,
                    grading_confidence=s.grading_confidence,
                    requires_manual_review=s.requires_manual_review,
                    manual_review_reason=s.manual_review_reason,
                    criteria_met_json=s.criteria_met_json,
                    criteria_missing_json=s.criteria_missing_json,
                    source_page_number=s.source_page_number,
                    crop_box_json=s.crop_box_json,
                    answer_crop_path=s.answer_crop_path,
                    transcription_edited_by_human=bool(
                        getattr(s, "transcription_edited_by_human", False)
                    ),
                    transcription_confidence=s.transcription_confidence,
                    warnings_json=s.warnings_json or [],
                )
            )
    details.sort(key=lambda x: x.question_number)

    return StudentResultDetail(
        id=sr.id,
        batch_id=sr.batch_id,
        student_name=student.name if student else None,
        registration_number=student.registration_number if student else None,
        page_number=sr.page_number,
        physical_page=sr.physical_page,
        identity_source=sr.identity_source,
        detected_student_name=sr.detected_student_name,
        detected_registration=sr.detected_registration,
        warnings_json=sr.warnings_json or [],
        total_score=sr.total_score,
        status=sr.status.value,
        scores=details,
    )


def _recalc_total(db: Session, sr: StudentResult) -> None:
    scores = db.query(QuestionScore).filter(QuestionScore.student_result_id == sr.id).all()
    sr.total_score = sum(_effective_question_score(s) for s in scores)
    db.commit()
