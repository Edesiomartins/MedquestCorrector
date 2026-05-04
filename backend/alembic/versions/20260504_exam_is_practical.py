"""Flag prova prática em exams

Revision ID: 20260504_exam_is_practical
Revises: 20260429_exam_question_criteria
Create Date: 2026-05-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20260504_exam_is_practical"
down_revision = "20260429_exam_question_criteria"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {col["name"] for col in inspector.get_columns("exams")}
    if "is_practical" not in cols:
        op.add_column(
            "exams",
            sa.Column(
                "is_practical",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {col["name"] for col in inspector.get_columns("exams")}
    if "is_practical" in cols:
        op.drop_column("exams", "is_practical")
