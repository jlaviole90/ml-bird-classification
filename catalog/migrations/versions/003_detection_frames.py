"""Detection frames -- store JPEG frames captured during detection sessions.

Revision ID: 003
Revises: 002
Create Date: 2026-04-14 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "detection_frames",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("detection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("detections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("has_bird", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("jpeg_data", sa.LargeBinary(), nullable=False),
        sa.Column("frame_width", sa.Integer(), nullable=True),
        sa.Column("frame_height", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_detection_frames_detection_id", "detection_frames", ["detection_id"])
    op.create_index("ix_detection_frames_captured_at", "detection_frames", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_detection_frames_captured_at")
    op.drop_index("ix_detection_frames_detection_id")
    op.drop_table("detection_frames")
