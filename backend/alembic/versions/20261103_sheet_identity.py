"""Nomes confirmados, política de correção e transcrição original.

Revision ID: 20261103_sheet_identity
Revises: 20260821_htr_labels
Create Date: 2026-11-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20261103_sheet_identity"
down_revision = "20260821_htr_labels"
branch_labels = None
depends_on = None


def _add(table: str, column: sa.Column) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        return
    names = {col["name"] for col in inspector.get_columns(table)}
    if column.name not in names:
        op.add_column(table, column)


def upgrade() -> None:
    _add("exams", sa.Column("grading_policy_json", sa.Text(), nullable=True))
    _add("student_results", sa.Column("handwritten_name", sa.String(length=255), nullable=True))
    _add("student_results", sa.Column("confirmed_name", sa.String(length=255), nullable=True))
    _add("visual_exam_answers", sa.Column("original_transcription", sa.Text(), nullable=True))
    _add("visual_exam_answers", sa.Column("handwritten_name", sa.String(), nullable=True))
    _add("visual_exam_answers", sa.Column("registered_name", sa.String(), nullable=True))
    _add("visual_exam_answers", sa.Column("confirmed_name", sa.String(), nullable=True))
    _add("visual_exam_answers", sa.Column("identity_source", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def _drop(table: str, name: str) -> None:
        if table not in inspector.get_table_names():
            return
        names = {col["name"] for col in inspector.get_columns(table)}
        if name in names:
            op.drop_column(table, name)

    for table, name in (
        ("visual_exam_answers", "identity_source"),
        ("visual_exam_answers", "confirmed_name"),
        ("visual_exam_answers", "registered_name"),
        ("visual_exam_answers", "handwritten_name"),
        ("visual_exam_answers", "original_transcription"),
        ("student_results", "confirmed_name"),
        ("student_results", "handwritten_name"),
        ("exams", "grading_policy_json"),
    ):
        _drop(table, name)
