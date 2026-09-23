"""지식/용어집 도메인 — Pydantic 스키마."""
from typing import Optional
from pydantic import BaseModel, Field


# ─── Knowledge ───────────────────────────────────────────────────────────────

class KnowledgeCreate(BaseModel):
    namespace: str
    content: str
    base_weight: float = Field(default=1.0, ge=0.0)
    category: Optional[str] = None


class KnowledgeUpdate(BaseModel):
    content: Optional[str] = None
    base_weight: Optional[float] = Field(default=None, ge=0.0)
    category: Optional[str] = None


class DuplicateMatchOut(BaseModel):
    id: int
    content: str
    similarity: float


class KnowledgeOut(BaseModel):
    id: int
    namespace: str
    content: str
    base_weight: float
    category: Optional[str] = None
    status: str = "active"
    source_file: Optional[str] = None
    source_chunk_idx: Optional[int] = None
    source_type: Optional[str] = None
    created_by_part: Optional[str] = None
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None
    created_at: str
    updated_at: str
    pending_review: bool = False
    duplicate_matches: list[DuplicateMatchOut] = []


# ─── Bulk / Ingestion ──────────────────────────────────────────────────────

class BulkKnowledgeItem(BaseModel):
    content: str
    base_weight: float = Field(default=1.0, ge=0.0)
    category: Optional[str] = None
    # 컨플루언스 벌크 리뷰 화면에서 넘어오는 조상 헤딩 목록(2026-09-22 수정) — 이전엔
    # 이 필드가 스키마에 없어서 확정(POST /knowledge/bulk) 시점에 조용히 버려지고 있었다.
    # heading_path 컬럼 자체는 v2.98에 추가됐지만, 실제 UI 확정 흐름(미리보기→리뷰→확정)이
    # 그 값을 한 번도 실어 나르지 않아 백필 스크립트로 소급 적용한 레거시 행 외엔 전부
    # NULL로 저장되고 있었다 — 이번에 함께 수정.
    heading_path: Optional[list[str]] = None


class BulkCreateRequest(BaseModel):
    namespace: str
    items: list[BulkKnowledgeItem]
    source_file: Optional[str] = None
    source_type: str = "manual"


class IngestionJobOut(BaseModel):
    id: int
    namespace_id: int
    source_file: Optional[str]
    source_type: Optional[str]
    status: str
    total_chunks: int
    created_chunks: int
    pending_chunks: int = 0
    auto_glossary: int
    chunk_strategy: Optional[str]
    error_message: Optional[str]
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None
    created_at: str
    completed_at: Optional[str]


# ─── Glossary ────────────────────────────────────────────────────────────────

class GlossaryCreate(BaseModel):
    namespace: str
    term: str
    description: str


class GlossaryUpdate(BaseModel):
    term: str
    description: str


class GlossaryOut(BaseModel):
    id: int
    namespace: str
    term: str
    description: str
    created_by_part: Optional[str] = None
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None
