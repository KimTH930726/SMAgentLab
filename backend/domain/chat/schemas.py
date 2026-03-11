"""대화 도메인 — Pydantic 스키마."""
from typing import Optional
from pydantic import BaseModel, Field

from core.config import settings


class ChatRequest(BaseModel):
    namespace: str
    question: str
    w_vector: float = Field(default_factory=lambda: settings.default_w_vector, ge=0.0, le=1.0)
    w_keyword: float = Field(default_factory=lambda: settings.default_w_keyword, ge=0.0, le=1.0)
    top_k: int = Field(default_factory=lambda: settings.default_top_k, ge=1, le=20)
    conversation_id: Optional[int] = None
    category: Optional[str] = None


class KnowledgeResult(BaseModel):
    id: int
    container_name: Optional[str]
    target_tables: Optional[list[str]]
    content: str
    query_template: Optional[str]
    final_score: float


class ChatResponse(BaseModel):
    conversation_id: int
    question: str
    mapped_term: Optional[str]
    results: list[KnowledgeResult]
    answer: str


class ConversationCreate(BaseModel):
    namespace: str
    title: str = ""


class ConversationResponse(BaseModel):
    id: int
    namespace: str
    title: str
    trimmed: bool = False
    created_at: str


class MessageResponse(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    mapped_term: Optional[str]
    results: Optional[list]
    status: str = "completed"
    has_feedback: bool = False
    created_at: str


# ─── Debug ───────────────────────────────────────────────────────────────────

class GlossaryMatchInfo(BaseModel):
    term: str
    description: str
    similarity: float


class DebugResult(BaseModel):
    id: int
    container_name: Optional[str]
    target_tables: Optional[list[str]]
    content: str
    query_template: Optional[str]
    base_weight: float
    v_score: float
    k_score: float
    final_score: float


class FewshotResult(BaseModel):
    question: str
    answer: str
    similarity: float


class DebugSearchResponse(BaseModel):
    question: str
    namespace: str
    enriched_query: str
    glossary_match: Optional[GlossaryMatchInfo]
    w_vector: float
    w_keyword: float
    fewshots: list[FewshotResult]
    results: list[DebugResult]
    context_preview: str
