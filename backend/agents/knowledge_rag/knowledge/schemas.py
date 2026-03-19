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


class KnowledgeOut(BaseModel):
    id: int
    namespace: str
    container_name: Optional[str]
    target_tables: Optional[list[str]]
    content: str
    query_template: Optional[str]
    base_weight: float
    category: Optional[str] = None
    created_by_part: Optional[str] = None
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None
    created_at: str
    updated_at: str


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
