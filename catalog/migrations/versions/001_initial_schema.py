"""Initial schema — species and detections tables.

Revision ID: 001
Revises: None
Create Date: 2024-01-01 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "species",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("cub_class_id", sa.Integer, unique=True, nullable=False),
        sa.Column("common_name", sa.Text, nullable=False),
        sa.Column("scientific_name", sa.Text, nullable=True),
        sa.Column("family", sa.Text, nullable=True),
    )

    op.create_table(
        "detections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("species_id", sa.Integer, sa.ForeignKey("species.id"), nullable=True),
        sa.Column("confidence", sa.Float, nullable=False),
        sa.Column("frame_s3_key", sa.Text, nullable=False),
        sa.Column("source_camera", sa.String(64), server_default="birdcam-01"),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bounding_box", JSONB, nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index("idx_detections_species", "detections", ["species_id"])
    op.create_index("idx_detections_time", "detections", ["detected_at"])


def downgrade() -> None:
    op.drop_index("idx_detections_time", table_name="detections")
    op.drop_index("idx_detections_species", table_name="detections")
    op.drop_table("detections")
    op.drop_table("species")
