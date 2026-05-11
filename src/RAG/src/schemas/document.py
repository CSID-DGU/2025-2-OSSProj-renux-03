from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


DocumentType = Literal[
    "notice",
    "pdf",
    "calendar",
    "rule",
    "academic",
    "food",
    "library",
    "department",
    "facility",
    "student_service",
    "international",
    "scholarship",
    "course",
    "public_stats",
]


class DocumentSchema(BaseModel):
    id: str = Field(..., description="Stable document identifier")
    source: str
    category: str
    sub_category: str | None = None
    title: str
    content: str
    url: str
    published_at: str | None = None
    updated_at: str | None = None
    department: str | None = None
    campus: str | None = None
    document_type: DocumentType
    has_attachment: bool = False
    attachment_urls: list[str] = Field(default_factory=list)
    valid_from: str | None = None
    valid_until: str | None = None
    collected_at: str


class ChunkMetadata(BaseModel):
    document_id: str
    source: str
    category: str
    sub_category: str | None = None
    title: str
    url: str
    source_url: str
    published_at: str | None = None
    department: str | None = None
    campus: str | None = None
    document_type: DocumentType
    chunk_index: int


class ChunkRecord(BaseModel):
    id: str
    content: str
    metadata: ChunkMetadata
