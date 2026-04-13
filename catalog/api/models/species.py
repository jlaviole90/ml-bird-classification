"""SQLAlchemy and Pydantic models for the species table."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class SpeciesORM(Base):
    __tablename__ = "species"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cub_class_id = Column(Integer, unique=True, nullable=False)
    common_name = Column(Text, nullable=False)
    scientific_name = Column(Text, nullable=True)
    family = Column(Text, nullable=True)
    species_code = Column(String(10), nullable=True)
    taxonomic_order = Column(Integer, nullable=True)
    order = Column(Text, nullable=True)
    ebird_category = Column(Text, nullable=True)


# ── Pydantic schemas ────────────────────────────────────

class SpeciesResponse(BaseModel):
    id: int
    cub_class_id: int
    common_name: str
    scientific_name: str | None = None
    family: str | None = None
    species_code: str | None = None
    order: str | None = None

    model_config = {"from_attributes": True}


class SpeciesWithCount(SpeciesResponse):
    detection_count: int = 0
