"""Pipeline de OCR + correção por questão com revisão apenas para exceções."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from reportlab.lib.pagesizes import A4

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.storage import path_from_local_url, write_answer_crop
from app.models.exam import Exam, ExamQuestion
from app.models.grading import QuestionScore, ResultStatus, StudentResult
from app.models.pipeline import BatchStatus, UploadBatch
from app.services.batch_results_cleanup import clear_batch_grading_results
from app.services.vision.ink import InkStats, detect_ink
from app.services.discursive_import.readiness import (
    ExternalDiscursiveNotReadyError,
    require_external_discursive_ready,
)
from app.services.grading.manual_review_decision import decide_manual_review
from app.services.llm.grading import (
    QuestionSpec,
    SingleQuestionGrade,
    TextGradingService,
    VisionGradingService,
)
from app.services.vision.ocr import get_ocr_provider, image_to_png_bytes
from app.services.vision.sheet_geometry import crop_box_from_image, load_manifest
from app.services.vision.page_align import align_page_with_manifest
from app.services.vision.pdf_parser import PDFParserService
from app.services.vision.qr_decode import PageQrPayload, decode_sheet_qr

logger = logging.getLogger(__name__)

PAGE_W_PT, PAGE_H_PT = A4
PIPELINE_DPI = 200


def _fail(db, batch: UploadBatch, msg: str) -> None:
    logger.error(msg)
    batch.status = BatchStatus.FAILED
    db.commit()


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _effective_total(scores: list[QuestionScore]) -> float:
    total = 0.0
    for s in scores:
        if s.final_score is not None:
            total += float(s.final_score)
        elif not s.requires_manual_review:
            total += float(s.ai_score or 0)
    return total


def _annotate_json(criteria: list[str] | None) -> str | None:
    if not criteria:
        return None
    return json.dumps(criteria, ensure_ascii=False)


def _blank_answer_score(
    *,
    db: Any,
    sr: Any,
    eq: Any,
    ink: InkStats,
    physical_page_one_based: int,
    crop_box_json: str | None,
    answer_crop_path: str | None,
    question_warnings: list[str] | None,
) -> QuestionScore:
    """Caixa sem tinta: nota 0 sem gastar OCR nem LLM.

    A faixa marginal (`ink.is_marginal`) vai para revisão humana em vez de zerar
    em silêncio — pouca tinta pode ser resposta curta ou lápis fraco, e apostar
    contra o aluno nesse caso sai caro. Ver docs/HTR_PLANO_EXECUCAO.md, item 6.
    """
    qs = QuestionScore(
        student_result_id=sr.id,
        question_id=eq.id,
        ai_score=0.0,
        ai_justification=f"Sem resposta detectada na caixa ({ink.reason}).",
        final_score=(None if ink.is_marginal else 0.0),
        extracted_answer_text=None,
        ocr_provider="ink_detector",
        ocr_confidence=None,
        transcription_confidence=None,
        grading_confidence=1.0,
        requires_manual_review=ink.is_marginal,
        manual_review_reason=(
            "Densidade de tinta na faixa marginal; confirmar se a caixa está mesmo vazia."
            if ink.is_marginal
            else None
        ),
        criteria_met_json=_annotate_json([]),
        criteria_missing_json=_annotate_json([]),
        source_page_number=physical_page_one_based,
        source_question_number=eq.question_number,
        crop_box_json=crop_box_json,
        answer_crop_path=answer_crop_path,
        warnings_json=question_warnings or [],
    )
    db.add(qs)
    return qs


IDENTITY_SOURCE_QR = "qr"
IDENTITY_SOURCE_HEADER_OCR = "header_ocr"
IDENTITY_SOURCE_MANIFEST_FALLBACK = "manifest_fallback"
IDENTITY_SOURCE_ANONYMOUS = "anonymous"


def _exam_policy(exam: Any) -> dict[str, Any]:
    raw = getattr(exam, "grading_policy_json", None)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _try_header_student_uuid(_aligned_page_image: Any) -> UUID | None:
    """O cabeçalho manuscrito não produz UUID. A leitura do nome fica em `_read_header_fields`."""
    return None


def _read_header_fields(aligned_page_image: Any, *, allow_remote: bool = False) -> dict[str, str]:
    """Lê o cabeçalho quando a chamada remota está explicitamente ligada.

    Sem a flag, não há chamada paga. O retorno vazio deixa a folha sem vínculo
    automático em vez de copiar o aluno do manifesto.
    """
    if not allow_remote or not settings.OPENROUTER_API_KEY:
        return {"name": "", "registration": "", "class": ""}
    from app.services.openrouter_vision_client import read_sheet_header
    from app.services.visual_exam_pipeline import _save_header_crop

    import tempfile

    try:
        with tempfile.TemporaryDirectory(prefix="header_") as temp_dir:
            image_path = Path(temp_dir) / "page.png"
            aligned_page_image.save(image_path, format="PNG")
            header_path = _save_header_crop(str(image_path), Path(temp_dir), 1)
            if not header_path:
                return {"name": "", "registration": "", "class": ""}
            header = read_sheet_header(header_path)
            return {
                "name": str(header.get("name") or ""),
                "registration": str(header.get("registration") or ""),
                "class": str(header.get("class") or ""),
            }
    except Exception as exc:
        logger.warning("Leitura remota de cabeçalho indisponível: %s", exc)
        return {"name": "", "registration": "", "class": ""}


def _safe_uuid(raw: str | UUID | None) -> UUID | None:
    if raw is None:
        return None
    if isinstance(raw, UUID):
        return raw
    try:
        return UUID(str(raw).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _pick_student_identity(
    qr: PageQrPayload | None,
    header_uuid: UUID | None,
    manifest_student: str | None,
    exam_id_str: str,
) -> tuple[UUID | None, str, list[str]]:
    """
    Identidade do aluno nesta página física do PDF enviado.

    O manifesto não escolhe o aluno: a ordem de geração não é a ordem do scan.
    QR só vale se o exam_id conferir. Sem QR, a folha fica sem vínculo automático.
    """
    warnings: list[str] = []
    manifest_uuid = _safe_uuid(manifest_student)
    qr_uuid = _safe_uuid(qr.student_id if qr else None)
    qr_exam = qr.exam_id if qr else None
    qr_exam_matches = str(qr_exam) == str(exam_id_str) if qr_exam else False

    if manifest_uuid is not None:
        warnings.append(
            "Manifesto ignorado como identidade: a posição da página digitalizada "
            f"não define o aluno (manifest_student={manifest_student})."
        )

    if qr_uuid is not None and qr_exam_matches:
        if manifest_uuid is not None and manifest_uuid != qr_uuid:
            warnings.append("QR e manifesto discordam. O vínculo usa o QR; o manifesto fica só como evidência.")
            logger.warning(
                "[student-link] QR e manifesto discordam — usando QR (manifest=%s, QR=%s).",
                manifest_uuid,
                qr_uuid,
            )
        if header_uuid is not None and header_uuid != qr_uuid:
            warnings.append("QR e cabeçalho discordam. Usando QR e mantendo a folha em revisão.")
        return qr_uuid, IDENTITY_SOURCE_QR, warnings

    if qr_uuid is not None and not qr_exam_matches:
        warnings.append("QR ignorado por exam_id divergente.")
        logger.warning(
            "[student-link] QR ignorado por exam_id divergente (qr_exam=%s, expected=%s, qr_student=%s).",
            qr_exam,
            exam_id_str,
            qr_uuid,
        )

    if header_uuid is not None:
        return header_uuid, IDENTITY_SOURCE_HEADER_OCR, warnings

    warnings.append("Nenhuma identidade conferida. A folha permanece sem vínculo automático.")
    return None, IDENTITY_SOURCE_ANONYMOUS, warnings


@celery_app.task(bind=True, max_retries=0)
def process_upload_batch(self, batch_id: str):
    db = SessionLocal()
    try:
        batch = db.query(UploadBatch).filter(UploadBatch.id == batch_id).first()
        if not batch:
            logger.warning("Batch %s não encontrado.", batch_id)
            return

        exam = db.query(Exam).filter(Exam.id == batch.exam_id).first()
        if not exam:
            _fail(db, batch, f"[batch={batch_id}] Prova não encontrada.")
            return

        questions = (
            db.query(ExamQuestion)
            .filter(ExamQuestion.exam_id == exam.id)
            .order_by(ExamQuestion.question_number)
            .all()
        )
        if not questions:
            _fail(db, batch, f"[batch={batch_id}] Prova sem questões.")
            return

        try:
            require_external_discursive_ready(exam, questions)
        except ExternalDiscursiveNotReadyError as exc:
            _fail(db, batch, str(exc))
            return

        question_by_number = {q.question_number: q for q in questions}
        question_specs = {
            q.question_number: QuestionSpec(
                question_number=q.question_number,
                question_text=q.question_text,
                expected_answer=q.expected_answer,
                max_score=q.max_score,
            )
            for q in questions
        }

        manifest = load_manifest(exam.layout_manifest_json)

        clear_batch_grading_results(db, batch.id)
        batch.status = BatchStatus.PROCESSING
        db.commit()

        try:
            pdf_path = path_from_local_url(batch.file_url)
        except ValueError:
            _fail(db, batch, f"[batch={batch_id}] URL inválida: {batch.file_url!r}")
            return

        if not pdf_path.is_file():
            _fail(db, batch, f"[batch={batch_id}] Arquivo não encontrado: {pdf_path}")
            return

        pdf_bytes = pdf_path.read_bytes()
        try:
            page_images = PDFParserService.extract_pages_as_images(pdf_bytes, dpi=PIPELINE_DPI)
            batch.total_pages = len(page_images)
            db.commit()
        except Exception as exc:
            _fail(db, batch, f"[batch={batch_id}] Falha ao ler PDF: {exc}")
            return

        logger.info("[batch=%s] %d páginas extraídas.", batch_id, len(page_images))

        ocr_provider = get_ocr_provider()
        exam_id_str = str(exam.id)

        student_results_by_id: dict[UUID, StudentResult] = {}
        anonymous_results_by_page: dict[int, StudentResult] = {}

        def get_or_create_sr(
            student_pk: UUID | None,
            page_idx: int,
            identity_src: str,
            identity_warnings: list[str],
            detected_student_name: str | None,
            detected_registration: str | None,
        ) -> StudentResult:
            page_one_based = page_idx + 1
            if student_pk:
                if student_pk in student_results_by_id:
                    sr = student_results_by_id[student_pk]
                    sr.page_number = min(sr.page_number, page_one_based)
                    sr.physical_page = page_one_based
                    sr.detected_student_name = detected_student_name or sr.detected_student_name
                    sr.detected_registration = detected_registration or sr.detected_registration
                    if sr.identity_source is None:
                        sr.identity_source = identity_src
                    elif (
                        identity_src == IDENTITY_SOURCE_QR
                        and sr.identity_source == IDENTITY_SOURCE_MANIFEST_FALLBACK
                    ):
                        sr.identity_source = IDENTITY_SOURCE_QR
                    if identity_warnings:
                        previous = sr.warnings_json or []
                        sr.warnings_json = list(dict.fromkeys([*previous, *identity_warnings]))
                    return sr
                sr = StudentResult(
                    batch_id=batch.id,
                    student_id=student_pk,
                    page_number=page_one_based,
                    physical_page=page_one_based,
                    status=ResultStatus.PENDING,
                    identity_source=identity_src,
                    detected_student_name=detected_student_name,
                    detected_registration=detected_registration,
                    warnings_json=identity_warnings or [],
                )
                db.add(sr)
                db.flush()
                student_results_by_id[student_pk] = sr
                return sr

            if page_idx in anonymous_results_by_page:
                return anonymous_results_by_page[page_idx]

            sr = StudentResult(
                batch_id=batch.id,
                student_id=None,
                page_number=page_one_based,
                physical_page=page_one_based,
                status=ResultStatus.PENDING,
                identity_source=identity_src,
                detected_student_name=detected_student_name,
                detected_registration=detected_registration,
                warnings_json=identity_warnings or [],
            )
            db.add(sr)
            db.flush()
            anonymous_results_by_page[page_idx] = sr
            return sr

        def process_crop_for_question(
            *,
            sr: StudentResult,
            eq: ExamQuestion,
            crop_bytes: bytes,
            physical_page_one_based: int,
            alignment_failed: bool,
            fallback_visual_ok: bool,
            crop_box_json: str | None = None,
            answer_crop_path: str | None = None,
            identity_source: str | None = None,
            question_warnings: list[str] | None = None,
            ink: InkStats | None = None,
        ) -> QuestionScore:
            existing = (
                db.query(QuestionScore)
                .filter(
                    QuestionScore.student_result_id == sr.id,
                    QuestionScore.question_id == eq.id,
                )
                .first()
            )
            if existing:
                logger.warning(
                    "[batch=%s] Questão duplicada ignorada: result=%s question=%s page=%s.",
                    batch_id,
                    sr.id,
                    eq.question_number,
                    physical_page_one_based,
                )
                return existing

            if ink is not None and not ink.has_ink:
                # Caixa vazia sai aqui, sem OCR e sem LLM: e o unico jeito de nao
                # atribuir nota a uma resposta que nao existe -- o modelo de visao
                # reporta confianca "alta" quando alucina texto em caixa vazia.
                return _blank_answer_score(
                    db=db,
                    sr=sr,
                    eq=eq,
                    ink=ink,
                    physical_page_one_based=physical_page_one_based,
                    crop_box_json=crop_box_json,
                    answer_crop_path=answer_crop_path,
                    question_warnings=question_warnings,
                )

            ocr_result = _run_async(ocr_provider.extract_handwriting(crop_bytes))
            spec = question_specs[eq.question_number]
            used_visual_fallback = False
            if ocr_result.error_message:
                grade = SingleQuestionGrade(
                    question_number=eq.question_number,
                    score=0.0,
                    justification="",
                    grading_confidence=1.0,
                    manual_review_required=True,
                    manual_review_reason=ocr_result.error_message,
                )
                parse_ok = True
            else:
                if getattr(exam, "is_practical", False):
                    from app.services.exam_grading_client import grade_practical_answer

                    policy = _exam_policy(exam)
                    graded = grade_practical_answer(
                        {
                            "number": eq.question_number,
                            "max_score": eq.max_score,
                            "laterality_mode": policy.get("laterality_mode"),
                            "reading_confidence": "media",
                        },
                        {
                            "expected_answer": eq.expected_answer,
                            "max_score": eq.max_score,
                            "laterality_mode": policy.get("laterality_mode"),
                        },
                        ocr_result.text or "",
                    )
                    pending_grade = graded.get("score") is None
                    grade = SingleQuestionGrade(
                        question_number=eq.question_number,
                        score=0.0 if pending_grade else float(graded.get("score") or 0),
                        justification=str(graded.get("justification") or ""),
                        grading_confidence=0.0 if pending_grade else 1.0,
                        manual_review_required=bool(graded.get("needs_human_review") or pending_grade),
                        manual_review_reason=graded.get("review_reason") or None,
                    )
                    parse_ok = True
                else:
                    grade_tuple = _run_async(TextGradingService.grade_single_question(ocr_result.text, spec))
                    grade = grade_tuple[0]
                    parse_ok = grade_tuple[1]

            should_try_visual = (
                ocr_result.error_message is not None
                or ocr_result.needs_fallback
                or not parse_ok
                or grade.grading_confidence < 0.70
                or grade.manual_review_required
            )
            if should_try_visual:
                visual_grade, visual_parse_ok = _run_async(
                    VisionGradingService.grade_single_question_image(
                        crop_bytes,
                        spec,
                        ocr_text=ocr_result.text,
                    )
                )
                if visual_parse_ok:
                    grade = visual_grade
                    parse_ok = True
                    used_visual_fallback = True

            review, reason = decide_manual_review(
                ocr_result,
                grade,
                ocr_result.text,
                eq.max_score,
                json_parse_failed=not parse_ok,
                fallback_visual_ok=(
                    fallback_visual_ok
                    or used_visual_fallback
                    or (not ocr_result.needs_fallback)
                ),
                alignment_failed=alignment_failed,
                score_parse_invalid=not parse_ok,
            )

            criteria_met = grade.criteria_met or []
            criteria_miss = grade.criteria_missing or []

            qs = QuestionScore(
                student_result_id=sr.id,
                question_id=eq.id,
                ai_score=grade.score,
                ai_justification=grade.justification or None,
                final_score=(grade.score if not review else None),
                extracted_answer_text=ocr_result.text or None,
                ocr_provider=ocr_result.provider,
                ocr_confidence=ocr_result.confidence_avg,
                transcription_confidence=ocr_result.confidence_avg,
                grading_confidence=grade.grading_confidence,
                requires_manual_review=review,
                manual_review_reason=(reason if review else None),
                criteria_met_json=_annotate_json(criteria_met),
                criteria_missing_json=_annotate_json(criteria_miss),
                source_page_number=physical_page_one_based,
                source_question_number=eq.question_number,
                crop_box_json=crop_box_json,
                answer_crop_path=answer_crop_path,
                warnings_json=question_warnings or [],
            )
            if identity_source and identity_source != IDENTITY_SOURCE_QR:
                qs.requires_manual_review = True
                qs.final_score = None
                qs.manual_review_reason = (
                    qs.manual_review_reason
                    or f"Vinculação sem QR confiável ({identity_source}). Conferir aluno/página."
                )
            db.add(qs)
            return qs

        # --- Processamento por página física ---
        for page_idx, page_img in enumerate(page_images):
            global_page_index = page_idx
            physical_page_number = global_page_index + 1
            # O manifesto vem primeiro: sem os fiduciais dele não há como
            # alinhar, e sem alinhar os recortes por caixa caem deslocados numa
            # página torta (docs/HTR_PLANO_EXECUCAO.md, item 7).
            manifest_page = manifest.page(page_idx) if manifest else None
            alignment = align_page_with_manifest(page_img, manifest_page, dpi=PIPELINE_DPI)
            aligned_img = alignment.image
            align_ok = alignment.ok
            align_reason = alignment.reason
            alignment_failed = not align_ok
            if alignment.markers_found:
                logger.info(
                    "[batch=%s] Página %d alinhada por %s: %d marcadores, "
                    "desalinhamento %.1f px, perspectiva %.1f px, rotação %.2f graus.",
                    batch_id,
                    physical_page_number,
                    alignment.method,
                    alignment.markers_found,
                    alignment.misalignment_px or 0.0,
                    alignment.perspective_residual_px or 0.0,
                    alignment.rotation_deg or 0.0,
                )

            qr_payload = decode_sheet_qr(aligned_img)
            manifest_student = manifest_page.student_id if manifest_page else None
            header_uuid = _try_header_student_uuid(aligned_img)
            header_fields = _read_header_fields(
                aligned_img,
                allow_remote=bool(settings.HEADER_REMOTE_READ_ENABLED),
            )
            detected_student_name = header_fields.get("name") or None
            detected_registration = header_fields.get("registration") or None

            student_uuid, identity_source, identity_warnings = _pick_student_identity(
                qr_payload,
                header_uuid,
                manifest_student,
                exam_id_str,
            )
            if detected_student_name or detected_registration:
                identity_warnings.append("Nome ou matrícula lidos no cabeçalho da folha.")

            logger.warning("=== DEBUG STUDENT PAGE MAP ===")
            logger.warning("physical_page: %s", physical_page_number)
            logger.warning("detected_student_name: %s", detected_student_name)
            logger.warning("detected_registration: %s", detected_registration)
            logger.warning("qr_payload=%s", qr_payload)
            logger.warning("manifest_student_id=%s", manifest_student)
            logger.warning("chosen_student_uuid=%s", student_uuid)
            logger.warning("used_identity_source=%s", identity_source)
            logger.warning("questions_found=%s", [q.question_number for q in questions])
            logger.warning("==============================")

            sr = get_or_create_sr(
                student_uuid,
                page_idx,
                identity_source,
                identity_warnings,
                detected_student_name,
                detected_registration,
            )

            logger.warning(
                "[student-link-final] physical_page=%s identity_source=%s chosen_student=%s "
                "detected_student_name=%s detected_registration=%s batch=%s",
                physical_page_number,
                identity_source,
                str(student_uuid) if student_uuid else "anonymous",
                detected_student_name,
                detected_registration,
                batch_id,
            )

            use_manifest = manifest_page is not None and manifest_page.has_boxes

            if alignment_failed:
                logger.warning("[batch=%s] Alinhamento da página %d falhou.", batch_id, physical_page_number)

            if use_manifest:
                boxes = manifest_page.boxes
                if not qr_payload:
                    logger.info(
                        "[batch=%s] Página %d sem QR legível — crops pelo manifest; identidade=%s.",
                        batch_id,
                        physical_page_number,
                        identity_source,
                    )

                scores_this_page: list[QuestionScore] = []

                for box in boxes:
                    qnum = box.question_number
                    eq = question_by_number.get(qnum)
                    if not eq:
                        continue
                    if (
                        db.query(QuestionScore.id)
                        .filter(
                            QuestionScore.student_result_id == sr.id,
                            QuestionScore.question_id == eq.id,
                        )
                        .first()
                    ):
                        logger.warning(
                            "[batch=%s] Página %d repetiu a questão %d. "
                            "A resposta anterior foi preservada numa folha separada.",
                            batch_id,
                            physical_page_number,
                            qnum,
                        )
                        conflict = StudentResult(
                            batch_id=batch.id,
                            student_id=None,
                            page_number=physical_page_number,
                            physical_page=physical_page_number,
                            status=ResultStatus.PENDING,
                            identity_source="conflict",
                            detected_student_name=detected_student_name,
                            detected_registration=detected_registration,
                            warnings_json=[
                                f"Questão {qnum} repetida para o mesmo vínculo. Registros originais preservados."
                            ],
                        )
                        db.add(conflict)
                        db.flush()
                        target_result = conflict
                    else:
                        target_result = sr
                    crop = crop_box_from_image(aligned_img, box, PAGE_H_PT, PIPELINE_DPI)
                    crop_bytes = image_to_png_bytes(crop)
                    # Grava o recorte de verdade. Antes daqui `answer_crop_path`
                    # era só a string `batch=.../page=.../q=...` — uma referência
                    # para um arquivo que nunca existiu, e a tela de revisão
                    # exibia essa string ao professor.
                    try:
                        crop_ref = write_answer_crop(batch.id, physical_page_number, qnum, crop_bytes)
                    except OSError as exc:
                        logger.warning(
                            "[batch=%s] Falha ao gravar recorte da questão %s: %s",
                            batch_id,
                            qnum,
                            exc,
                        )
                        crop_ref = None
                    ink = detect_ink(
                        crop,
                        suppress_horizontal_guides=bool(
                            manifest and getattr(manifest, "is_external_discursive", False)
                        ),
                    )
                    logger.warning(
                        "[question-crop] question_number=%s crop_box=%s crop_path=%s "
                        "crop_size=%sx%s ink=%s ratio=%.5f",
                        qnum,
                        box.as_dict(),
                        crop_ref,
                        crop.width,
                        crop.height,
                        ink.has_ink,
                        ink.ink_ratio,
                    )

                    qs = process_crop_for_question(
                        sr=target_result,
                        eq=eq,
                        crop_bytes=crop_bytes,
                        physical_page_one_based=physical_page_number,
                        alignment_failed=alignment_failed,
                        fallback_visual_ok=False,
                        crop_box_json=json.dumps(box.as_dict(), ensure_ascii=False),
                        answer_crop_path=crop_ref,
                        identity_source=identity_source,
                        question_warnings=identity_warnings,
                        ink=ink,
                    )
                    scores_this_page.append(qs)

                if alignment_failed:
                    for qs in scores_this_page:
                        qs.requires_manual_review = True
                        qs.manual_review_reason = qs.manual_review_reason or (
                            align_reason or "Falha no alinhamento/crop da página."
                        )
                        qs.final_score = None
                db.commit()

            else:
                # --- Legado: OCR da página inteira + mesma transcrição por questão ---
                logger.warning(
                    "[batch=%s] Manifest ausente ou página %d não mapeada — modo legado.",
                    batch_id,
                    physical_page_number,
                )

                img_bytes = image_to_png_bytes(aligned_img)
                ocr_full = _run_async(ocr_provider.extract_handwriting(img_bytes))

                for eq in questions:
                    if (
                        db.query(QuestionScore.id)
                        .filter(
                            QuestionScore.student_result_id == sr.id,
                            QuestionScore.question_id == eq.id,
                        )
                        .first()
                    ):
                        logger.warning(
                            "[batch=%s] Modo legado repetiu a questão %d para o mesmo aluno; ignorando duplicata.",
                            batch_id,
                            eq.question_number,
                        )
                        continue
                    spec = question_specs[eq.question_number]
                    grade_tuple = _run_async(
                        TextGradingService.grade_single_question(ocr_full.text, spec)
                    )
                    grade = grade_tuple[0]
                    parse_ok = grade_tuple[1]

                    review, reason = decide_manual_review(
                        ocr_full,
                        grade,
                        ocr_full.text,
                        eq.max_score,
                        json_parse_failed=not parse_ok,
                        fallback_visual_ok=False,
                        alignment_failed=alignment_failed,
                        score_parse_invalid=not parse_ok,
                    )

                    mr_reason = None
                    if review:
                        mr_reason = reason or "Revisão manual necessária."
                        if not exam.layout_manifest_json:
                            mr_reason = (
                                f"{mr_reason} Compatibilidade sem manifesto salvo "
                                "(OCR da página inteira para todas as questões)."
                            )

                    qs = QuestionScore(
                        student_result_id=sr.id,
                        question_id=eq.id,
                        ai_score=grade.score,
                        ai_justification=grade.justification or None,
                        final_score=(None if review else grade.score),
                        extracted_answer_text=ocr_full.text or None,
                        ocr_provider=ocr_full.provider,
                        ocr_confidence=ocr_full.confidence_avg,
                        grading_confidence=grade.grading_confidence,
                        requires_manual_review=review,
                        manual_review_reason=mr_reason,
                        criteria_met_json=_annotate_json(grade.criteria_met),
                        criteria_missing_json=_annotate_json(grade.criteria_missing),
                        source_page_number=page_idx + 1,
                        transcription_confidence=ocr_full.confidence_avg,
                        source_question_number=eq.question_number,
                        warnings_json=identity_warnings or [],
                    )
                    if identity_source != IDENTITY_SOURCE_QR:
                        qs.requires_manual_review = True
                        qs.manual_review_reason = qs.manual_review_reason or (
                            f"Vinculação sem QR confiável ({identity_source})."
                        )
                        qs.final_score = None
                    db.add(qs)

                db.commit()

        # --- Consolidar status por StudentResult ---
        sr_rows = db.query(StudentResult).filter(StudentResult.batch_id == batch.id).all()
        sr_ids = [sr.id for sr in sr_rows]

        for sr_id in sr_ids:
            sr_row = db.query(StudentResult).filter(StudentResult.id == sr_id).first()
            if not sr_row:
                continue
            scores_list = db.query(QuestionScore).filter(QuestionScore.student_result_id == sr_id).all()

            sr_row.total_score = _effective_total(scores_list)
            any_manual = any(s.requires_manual_review for s in scores_list)
            if any_manual:
                sr_row.status = ResultStatus.GRADED
            else:
                sr_row.status = ResultStatus.AUTO_APPROVED
                for s in scores_list:
                    if s.final_score is None and not s.requires_manual_review:
                        s.final_score = s.ai_score
            db.commit()

        any_batch_manual = (
            db.query(QuestionScore)
            .join(StudentResult)
            .filter(
                StudentResult.batch_id == batch.id,
                QuestionScore.requires_manual_review.is_(True),
            )
            .first()
            is not None
        )

        batch.status = BatchStatus.REVIEW_PENDING if any_batch_manual else BatchStatus.DONE
        db.commit()

        logger.info("[batch=%s] Pipeline concluído.", batch_id)

    except Exception:
        db.rollback()
        try:
            batch = db.query(UploadBatch).filter(UploadBatch.id == batch_id).first()
            if batch:
                batch.status = BatchStatus.FAILED
                db.commit()
        except Exception:
            db.rollback()
        logger.exception("[batch=%s] Erro inesperado.", batch_id)
    finally:
        db.close()
