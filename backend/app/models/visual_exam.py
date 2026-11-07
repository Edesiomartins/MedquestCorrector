import enum
import uuid

from sqlalchemy import Boolean, Column, DateTime, Enum as SQLEnum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from .base import Base


class VisualExamRunStatus(str, enum.Enum):
    PROCESSING = "PROCESSING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class VisualExamRun(Base):
    __tablename__ = "visual_exam_runs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    filename = Column(String, nullable=False)
    status = Column(
        SQLEnum(VisualExamRunStatus),
        nullable=False,
        default=VisualExamRunStatus.PROCESSING,
    )
    pages_processed = Column(Integer, nullable=False, default=0)
    vision_model_used = Column(String, nullable=True)
    text_model_used = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    error = Column(Text, nullable=True)


class VisualExamAnswer(Base):
    __tablename__ = "visual_exam_answers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("visual_exam_runs.id"), nullable=False)
    student_name = Column(String, nullable=True)
    registration = Column(String, nullable=True)
    detected_student_code = Column(String, nullable=True)
    class_name = Column(String, nullable=True)
    page_number = Column(Integer, nullable=False)
    question_number = Column(Integer, nullable=False)
    prompt_detected = Column(Text, nullable=True)
    answer_transcription = Column(Text, nullable=True)
    original_transcription = Column(Text, nullable=True)
    handwritten_name = Column(String, nullable=True)
    registered_name = Column(String, nullable=True)
    confirmed_name = Column(String, nullable=True)
    identity_source = Column(String, nullable=True)
    reading_confidence = Column(String, nullable=True)
    ocr_confidence = Column(Float, nullable=True)
    reading_notes = Column(Text, nullable=True)
    image_region = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    max_score = Column(Float, nullable=True)
    verdict = Column(String, nullable=True)
    justification = Column(Text, nullable=True)
    detected_concepts_json = Column(Text, nullable=True)
    missing_concepts_json = Column(Text, nullable=True)
    needs_human_review = Column(Boolean, nullable=False, default=False)
    review_reason = Column(Text, nullable=True)
    raw_vision_json = Column(Text, nullable=True)
    raw_grading_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
