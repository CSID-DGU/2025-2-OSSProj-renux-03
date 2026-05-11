from __future__ import annotations

from pydantic import BaseModel, Field


class IngestRunRequest(BaseModel):
    source_name: str | None = Field(default=None, description="수집할 source 이름. 없으면 활성 source 전체")
    limit: int | None = Field(default=None, ge=1, le=5000, description="최대 수집 문서 수. null이면 해당 source 전체")
    force: bool = False
    clean: bool = Field(default=False, description="기존 ingestion 산출물과 Chroma 컬렉션을 초기화한 뒤 재수집")


class SearchRequest(BaseModel):
    query: str
    category: str | None = None
    campus: str | None = None
    date_range: list[str] | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class SearchHit(BaseModel):
    id: str
    score: float
    content: str
    metadata: dict


class IngestStatusItem(BaseModel):
    source_name: str
    status: str
    fetched: int = 0
    stored: int = 0
    skipped: int = 0
    failed: int = 0
    started_at: str
    finished_at: str | None = None
    errors: list[str] = Field(default_factory=list)
