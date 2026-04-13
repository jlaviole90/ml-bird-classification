"""eBird integration -- new tables and species/detections alterations.

Revision ID: 002
Revises: 001
Create Date: 2026-04-13 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Extend species table with eBird taxonomy columns ──
    op.add_column("species", sa.Column("species_code", sa.String(10), nullable=True))
    op.add_column("species", sa.Column("taxonomic_order", sa.Integer, nullable=True))
    op.add_column("species", sa.Column("order", sa.Text, nullable=True))
    op.add_column("species", sa.Column("ebird_category", sa.Text, nullable=True))

    # ── Extend detections table with eBird validation fields ──
    op.add_column("detections", sa.Column("raw_confidence", sa.Float, nullable=True))
    op.add_column("detections", sa.Column("ebird_frequency", sa.Float, nullable=True))
    op.add_column("detections", sa.Column("ebird_validated", sa.Boolean, server_default="false"))
    op.add_column("detections", sa.Column("validation_notes", sa.Text, nullable=True))

    # ── eBird local species list ──
    op.create_table(
        "ebird_local_species",
        sa.Column("species_code", sa.String(10), primary_key=True),
        sa.Column("common_name", sa.Text, nullable=False),
        sa.Column("scientific_name", sa.Text, nullable=True),
        sa.Column("last_observed", sa.Date, nullable=True),
        sa.Column("observation_count", sa.Integer, server_default="0"),
        sa.Column("is_notable", sa.Boolean, server_default="false"),
        sa.Column("region_code", sa.String(20), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── Seasonal frequency (week-by-week) ──
    op.create_table(
        "ebird_seasonal_frequency",
        sa.Column("species_code", sa.String(10), nullable=False),
        sa.Column("region_code", sa.String(20), nullable=False),
        sa.Column("week_number", sa.Integer, nullable=False),
        sa.Column("frequency", sa.Float, nullable=False),
        sa.Column("sample_size", sa.Integer, nullable=True),
        sa.PrimaryKeyConstraint("species_code", "region_code", "week_number"),
    )

    # ── Notable sightings ──
    op.create_table(
        "ebird_notable_sightings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("species_code", sa.String(10), nullable=False),
        sa.Column("common_name", sa.Text, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lat", sa.Float, nullable=True),
        sa.Column("lng", sa.Float, nullable=True),
        sa.Column("location_name", sa.Text, nullable=True),
        sa.Column("how_many", sa.Integer, nullable=True),
        sa.Column("valid", sa.Boolean, server_default="true"),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── Hotspots ──
    op.create_table(
        "ebird_hotspots",
        sa.Column("hotspot_id", sa.String(20), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lng", sa.Float, nullable=False),
        sa.Column("country_code", sa.String(5), nullable=True),
        sa.Column("subnational1", sa.String(10), nullable=True),
        sa.Column("latest_obs_date", sa.Date, nullable=True),
        sa.Column("num_species", sa.Integer, server_default="0"),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── Yard life list ──
    op.create_table(
        "yard_life_list",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("species_id", sa.Integer, sa.ForeignKey("species.id"), nullable=True),
        sa.Column("species_code", sa.String(10), unique=True, nullable=False),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_detections", sa.Integer, server_default="1"),
        sa.Column("best_confidence", sa.Float, nullable=True),
        sa.Column("best_frame_s3_key", sa.Text, nullable=True),
        sa.Column("ebird_confirmed", sa.Boolean, server_default="false"),
    )

    # ── Identification audit log ──
    op.create_table(
        "identification_audit_log",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("detection_id", UUID(as_uuid=True), sa.ForeignKey("detections.id", ondelete="CASCADE"), nullable=True),
        sa.Column("frame_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("model_name", sa.Text, nullable=False),
        sa.Column("inference_latency_ms", sa.Float, nullable=True),
        sa.Column("candidates", JSONB, nullable=False),
        sa.Column("ebird_region", sa.Text, nullable=True),
        sa.Column("ebird_week", sa.Integer, nullable=True),
        sa.Column("local_list_size", sa.Integer, nullable=True),
        sa.Column("local_list_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_rank", sa.Integer, nullable=True),
        sa.Column("accepted_species_code", sa.Text, nullable=True),
        sa.Column("final_confidence", sa.Float, nullable=True),
        sa.Column("was_rerouted", sa.Boolean, server_default="false"),
        sa.Column("is_notable", sa.Boolean, server_default="false"),
        sa.Column("decision_time_ms", sa.Float, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("pipeline_version", sa.Text, nullable=True),
    )

    op.create_index("idx_audit_detection", "identification_audit_log", ["detection_id"])
    op.create_index("idx_audit_time", "identification_audit_log", ["created_at"])
    op.create_index("idx_audit_rerouted", "identification_audit_log", ["was_rerouted"], postgresql_where=sa.text("was_rerouted = TRUE"))
    op.create_index("idx_audit_notable", "identification_audit_log", ["is_notable"], postgresql_where=sa.text("is_notable = TRUE"))


def downgrade() -> None:
    op.drop_index("idx_audit_notable", table_name="identification_audit_log")
    op.drop_index("idx_audit_rerouted", table_name="identification_audit_log")
    op.drop_index("idx_audit_time", table_name="identification_audit_log")
    op.drop_index("idx_audit_detection", table_name="identification_audit_log")
    op.drop_table("identification_audit_log")
    op.drop_table("yard_life_list")
    op.drop_table("ebird_hotspots")
    op.drop_table("ebird_notable_sightings")
    op.drop_table("ebird_seasonal_frequency")
    op.drop_table("ebird_local_species")

    op.drop_column("detections", "validation_notes")
    op.drop_column("detections", "ebird_validated")
    op.drop_column("detections", "ebird_frequency")
    op.drop_column("detections", "raw_confidence")

    op.drop_column("species", "ebird_category")
    op.drop_column("species", "order")
    op.drop_column("species", "taxonomic_order")
    op.drop_column("species", "species_code")
