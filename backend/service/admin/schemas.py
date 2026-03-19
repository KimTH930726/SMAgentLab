"""관리 도메인 — Pydantic 스키마 (namespace, stats, llm)."""
from typing import Optional
from pydantic import BaseModel


# ─── Namespace ───────────────────────────────────────────────────────────────

class NamespaceCreate(BaseModel):
    name: str
    description: str = ""


class KnowledgeCategoryCreate(BaseModel):
    name: str


class KnowledgeCategoryOut(BaseModel):
    id: int
    namespace: str
    name: str
    created_at: str


class NamespaceInfo(BaseModel):
    name: str
    description: str
    owner_part: Optional[str] = None
    knowledge_count: int
    glossary_count: int
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None
    created_at: str


# ─── Stats ───────────────────────────────────────────────────────────────────

class NamespaceStats(BaseModel):
    namespace: str
    total_queries: int
    resolved: int
    pending: int
    unresolved: int
    positive_feedback: int
    negative_feedback: int
    knowledge_count: int = 0
    glossary_count: int = 0


class StatsResponse(BaseModel):
    namespaces: list[NamespaceStats]
    unresolved_cases: list[dict]


class TermStat(BaseModel):
    term: str
    total: int
    pending: int
    unresolved: int


class NamespaceDetailStats(BaseModel):
    namespace: str
    total_queries: int
    resolved: int
    pending: int
    unresolved: int
    term_distribution: list[TermStat]
    unresolved_cases: list[dict]


# ─── LLM Settings ───────────────────────────────────────────────────────────

class LLMConfigUpdate(BaseModel):
    provider: str
    ollama_base_url: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_timeout: Optional[int] = None
    inhouse_llm_url: Optional[str] = None
    inhouse_llm_api_key: Optional[str] = None
    inhouse_llm_model: Optional[str] = None
    inhouse_llm_agent_code: Optional[str] = None
    inhouse_llm_response_mode: Optional[str] = None
    inhouse_llm_timeout: Optional[int] = None


class LLMTestRequest(BaseModel):
    provider: str
    ollama_base_url: Optional[str] = None
    ollama_model: Optional[str] = None
    inhouse_llm_url: Optional[str] = None
    inhouse_llm_api_key: Optional[str] = None
    inhouse_llm_model: Optional[str] = None
    inhouse_llm_agent_code: Optional[str] = None
    inhouse_llm_response_mode: Optional[str] = None


class ThresholdUpdate(BaseModel):
    glossary_min_similarity: Optional[float] = None
    fewshot_min_similarity: Optional[float] = None
    knowledge_min_score: Optional[float] = None
    knowledge_high_score: Optional[float] = None
    knowledge_mid_score: Optional[float] = None


class SearchDefaultsUpdate(BaseModel):
    default_top_k: Optional[int] = None
    default_w_vector: Optional[float] = None
    default_w_keyword: Optional[float] = None
