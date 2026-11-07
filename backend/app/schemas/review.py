from pydantic import BaseModel
from uuid import UUID
from typing import Optional


class QuestionScoreDetail(BaseModel):
    id: UUID
    question_number: int
    question_text: str
    max_score: float
    ai_score: float
    ai_justification: Optional[str] = None
    final_score: Optional[float] = None
    professor_comment: Optional[str] = None

    extracted_answer_text: Optional[str] = None
    ocr_provider: Optional[str] = None
    ocr_confidence: Optional[float] = None
    grading_confidence: Optional[float] = None
    requires_manual_review: bool = False
    manual_review_reason: Optional[str] = None
    criteria_met_json: Optional[str] = None
    criteria_missing_json: Optional[str] = None
    source_page_number: Optional[int] = None
    crop_box_json: Optional[str] = None
    answer_crop_path: Optional[str] = None
    transcription_edited_by_human: bool = False
    transcription_confidence: Optional[float] = None
    warnings_json: Optional[list[str]] = None


class StudentResultDetail(BaseModel):
    id: UUID
    batch_id: UUID
    student_name: Optional[str] = None
    registration_number: Optional[str] = None
    page_number: int
    physical_page: Optional[int] = None
    identity_source: Optional[str] = None
    detected_student_name: Optional[str] = None
    detected_registration: Optional[str] = None
    handwritten_name: Optional[str] = None
    confirmed_name: Optional[str] = None
    warnings_json: Optional[list[str]] = None
    total_score: float
    status: str
    scores: list[QuestionScoreDetail]


class BatchResultsStats(BaseModel):
    total: int
    auto_approved: int
    pending_review: int
    reviewed: int


class BatchResultsResponse(BaseModel):
    results: list[StudentResultDetail]
    stats: BatchResultsStats


class UpdateScore(BaseModel):
    final_score: float
    professor_comment: Optional[str] = None
    # Transcrição conferida pelo professor. `None` significa "não mexi nela";
    # string vazia significa "a caixa está em branco", que é informação diferente
    # e é justamente o caso que mede alucinação.
    extracted_answer_text: Optional[str] = None
    grade_confirmed: bool = False
