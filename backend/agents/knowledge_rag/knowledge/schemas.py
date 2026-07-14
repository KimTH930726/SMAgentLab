"""지식/용어집 도메인 — Pydantic 스키마."""
from typing import Optional
from pydantic import BaseModel, Field


# ─── Knowledge ───────────────────────────────────────────────────────────────

class KnowledgeCreate(BaseModel):
    namespace: str
    container_name: Optional[str] = None
    target_tables: Optional[list[str]] = None
    content: str
    query_template: Optional[str] = None
    base_weight: float = Field(default=1.0, ge=0.0)
    category: Optional[str] = None


class KnowledgeUpdate(BaseModel):
    container_name: Optional[str] = None
    target_tables: Optional[list[str]] = None
    content: Optional[str] = None
    query_template: Optional[str] = None
    base_weight: Optional[float] = Field(default=None, ge=0.0)
    category: Optional[str] = None


class DuplicateMatchOut(BaseModel):
    id: int
    content: str
    similarity: float


class KnowledgeOut(BaseModel):
    id: int
    namespace: str
    container_name: Optional[str]
    target_tables: Optional[list[str]]
    content: str
    query_template: Optional[str]
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
    container_name: Optional[str] = None
    target_tables: Optional[list[str]] = None
    query_template: Optional[str] = None
    base_weight: float = Field(default=1.0, ge=0.0)
    category: Optional[str] = None


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
    auto_fewshot: int
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
