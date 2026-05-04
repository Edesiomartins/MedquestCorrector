from sqlalchemy import Column, String, Float, Integer, ForeignKey, Text, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from .base import Base


class Exam(Base):
    __tablename__ = "exams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    class_id = Column(UUID(as_uuid=True), ForeignKey("classes.id"), nullable=True)
    name = Column(String, nullable=False)
    is_practical = Column(Boolean, nullable=False, default=False)
    layout_manifest_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ExamQuestion(Base):
    __tablename__ = "exam_questions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exam_id = Column(UUID(as_uuid=True), ForeignKey("exams.id"), nullable=False)
    question_number = Column(Integer, nullable=False)
    question_text = Column(Text, nullable=False)
    expected_answer = Column(Text, nullable=False)
    correction_criteria = Column(Text, nullable=True)
    max_score = Column(Float, nullable=False, default=1.0)
    page_number = Column(Integer, nullable=True)
    box_x = Column(Float, nullable=True)
    box_y = Column(Float, nullable=True)
    box_w = Column(Float, nullable=True)
    box_h = Column(Float, nullable=True)
