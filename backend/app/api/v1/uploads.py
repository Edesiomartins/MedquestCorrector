import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from uuid import UUID

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.storage import write_batch_pdf
from app.services.batch_results_cleanup import clear_batch_grading_results
from app.models.exam import Exam
from app.models.pipeline import UploadBatch, BatchStatus
from app.models.user import User
from app.schemas.upload import BatchResponse, BatchStatusResponse

router = APIRouter(dependencies=[Depends(get_current_user)])

logger = logging.getLogger(__name__)

_MAX_BYTES = settings.MAX_UPLOAD_MB * 1024 * 1024


@router.post("/upload", response_model=BatchResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_batch(
    exam_id: UUID = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Apenas arquivos PDF são aceitos.")

    exam = db.query(Exam).filter(Exam.id == exam_id).first()
    if not exam:
        raise HTTPException(status_code=404, detail="Prova não encontrada.")

    raw = await file.read()
    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="Arquivo PDF vazio.")
    if len(raw) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF excede o tamanho máximo de {settings.MAX_UPLOAD_MB} MB.",
        )

    batch_id = uuid.uuid4()
    try:
        file_url = write_batch_pdf(batch_id, raw)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar upload: {e}") from e

    new_batch = UploadBatch(
        id=batch_id,
        exam_id=exam_id,
        user_id=current_user.id,
        file_url=file_url,
        status=BatchStatus.PENDING,
    )
    db.add(new_batch)
    db.commit()
    db.refresh(new_batch)

    from app.workers.pipeline import process_upload_batch
    process_upload_batch.delay(str(new_batch.id))

    return BatchResponse(batch_id=new_batch.id, status=new_batch.status.value)


@router.post("/{batch_id}/reprocess", response_model=BatchResponse, status_code=status.HTTP_202_ACCEPTED)
def reprocess_batch(batch_id: UUID, db: Session = Depends(get_db)):
    """
    Reprocessa o **mesmo** PDF já armazenado para este `batch_id`
    (nova vinculação aluno-página, OCR e correção), sem novo escaneamento.

    Remove resultados anteriores do lote antes de enfileirar o worker.
    """
    batch = db.query(UploadBatch).filter(UploadBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    if batch.status == BatchStatus.PROCESSING:
        raise HTTPException(
            status_code=409,
            detail="Lote já está em processamento. Aguarde concluir.",
        )

    logger.warning("[reprocess] invalidando resultados antigos do batch=%s", batch_id)
    removed = clear_batch_grading_results(db, batch_id)
    logger.info("[reprocess] removidos %s student_results do batch=%s", removed, batch_id)

    batch.status = BatchStatus.PENDING
    batch.total_pages = 0
    db.commit()
    db.refresh(batch)

    logger.warning("[reprocess] iniciando novo processamento para o mesmo PDF batch=%s", batch_id)

    from app.workers.pipeline import process_upload_batch

    process_upload_batch.delay(str(batch_id))

    return BatchResponse(batch_id=batch.id, status=batch.status.value)


@router.post("/{batch_id}/reupload", response_model=BatchResponse, status_code=status.HTTP_202_ACCEPTED)
async def reupload_batch_pdf(
    batch_id: UUID,
    exam_id: UUID = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Substitui o arquivo PDF do lote e reprocessa (mesmo `batch_id`, novo arquivo no disco).

    Útil para reenviar o scan sem criar outro lote.
    """
    batch = db.query(UploadBatch).filter(UploadBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")
    if batch.exam_id != exam_id:
        raise HTTPException(status_code=400, detail="exam_id não corresponde ao lote.")
    if batch.status == BatchStatus.PROCESSING:
        raise HTTPException(
            status_code=409,
            detail="Lote já está em processamento. Aguarde concluir.",
        )

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Apenas arquivos PDF são aceitos.")

    raw = await file.read()
    if len(raw) == 0:
        raise HTTPException(status_code=400, detail="Arquivo PDF vazio.")
    if len(raw) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF excede o tamanho máximo de {settings.MAX_UPLOAD_MB} MB.",
        )

    try:
        file_url = write_batch_pdf(batch_id, raw)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar upload: {e}") from e

    batch.file_url = file_url

    logger.warning("[reprocess] invalidando resultados antigos do batch=%s (substituição de PDF)", batch_id)
    removed = clear_batch_grading_results(db, batch_id)
    logger.info("[reprocess] removidos %s student_results do batch=%s após reupload", removed, batch_id)

    batch.status = BatchStatus.PENDING
    batch.total_pages = 0
    db.commit()
    db.refresh(batch)

    logger.warning("[reprocess] iniciando novo processamento após reupload batch=%s", batch_id)

    from app.workers.pipeline import process_upload_batch

    process_upload_batch.delay(str(batch_id))

    return BatchResponse(batch_id=batch.id, status=batch.status.value)


@router.get("/{batch_id}/status", response_model=BatchStatusResponse)
def get_batch_status(batch_id: UUID, db: Session = Depends(get_db)):
    batch = db.query(UploadBatch).filter(UploadBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")

    return BatchStatusResponse(
        batch_id=batch.id,
        status=batch.status.value,
        total_pages=batch.total_pages or 0,
    )


@router.post("/{batch_id}/process-now", response_model=BatchStatusResponse)
def process_batch_now(batch_id: UUID, db: Session = Depends(get_db)):
    batch = db.query(UploadBatch).filter(UploadBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Lote não encontrado.")

    if batch.status == BatchStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="Lote já está em processamento.")
    if batch.status in {BatchStatus.REVIEW_PENDING, BatchStatus.DONE}:
        return BatchStatusResponse(
            batch_id=batch.id,
            status=batch.status.value,
            total_pages=batch.total_pages or 0,
        )

    from app.workers.pipeline import process_upload_batch

    process_upload_batch.apply(args=[str(batch.id)])
    db.refresh(batch)
    return BatchStatusResponse(
        batch_id=batch.id,
        status=batch.status.value,
        total_pages=batch.total_pages or 0,
    )
