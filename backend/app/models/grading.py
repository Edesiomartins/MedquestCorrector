from sqlalchemy import Boolean, Column, String, Float, Integer, ForeignKey, Text, DateTime, Enum as SQLEnum, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
import enum
from .base import Base


class ResultStatus(str, enum.Enum):
    PENDING = "PENDING"
    GRADED = "GRADED"
    AUTO_APPROVED = "AUTO_APPROVED"
    REVIEWED = "REVIEWED"


class StudentResult(Base):
    """Prova lógica de um aluno dentro do lote (pode abranger várias páginas físicas no PDF)."""
    __tablename__ = "student_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id = Column(UUID(as_uuid=True), ForeignKey("upload_batches.id"), nullable=False)
    student_id = Column(UUID(as_uuid=True), ForeignKey("students.id"), nullable=True)
    # qr | header_ocr | manifest_fallback | anonymous
    identity_source = Column(String(40), nullable=True)
    physical_page = Column(Integer, nullable=True)
    detected_student_name = Column(String(255), nullable=True)
    detected_registration = Column(String(100), nullable=True)
    handwritten_name = Column(String(255), nullable=True)
    confirmed_name = Column(String(255), nullable=True)
    warnings_json = Column(JSONB, nullable=False, default=list)
    page_number = Column(Integer, nullable=False)
    total_score = Column(Float, default=0.0)
    status = Column(SQLEnum(ResultStatus), default=ResultStatus.PENDING)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class QuestionScore(Base):
    """Nota de uma questão específica para um aluno."""
    __tablename__ = "question_scores"
    __table_args__ = (
        UniqueConstraint(
            "student_result_id",
            "question_id",
            name="uq_question_scores_result_question",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_result_id = Column(UUID(as_uuid=True), ForeignKey("student_results.id"), nullable=False)
    question_id = Column(UUID(as_uuid=True), ForeignKey("exam_questions.id"), nullable=False)
    ai_score = Column(Float, default=0.0)
    ai_justification = Column(Text, nullable=True)
    final_score = Column(Float, nullable=True)
    professor_comment = Column(Text, nullable=True)

    extracted_answer_text = Column(Text, nullable=True)
    ocr_provider = Column(String, nullable=True)
    ocr_confidence = Column(Float, nullable=True)
    grading_confidence = Column(Float, nullable=True)
    requires_manual_review = Column(Boolean, nullable=False, default=False)
    manual_review_reason = Column(Text, nullable=True)
    criteria_met_json = Column(Text, nullable=True)
    criteria_missing_json = Column(Text, nullable=True)

    source_page_number = Column(Integer, nullable=True)
    source_question_number = Column(Integer, nullable=True)
    crop_box_json = Column(Text, nullable=True)
    answer_crop_path = Column(Text, nullable=True)
    transcription_edited_by_human = Column(Boolean, nullable=False, default=False)
    """Transcrição já conferida por gente: reprocessar o lote não pode sobrescrevê-la."""
    transcription_confidence = Column(Float, nullable=True)
    warnings_json = Column(JSONB, nullable=False, default=list)
