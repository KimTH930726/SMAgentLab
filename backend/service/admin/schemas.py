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

class TermStat(BaseModel):
    term: str
    total: int
    no_knowledge: int = 0
    description: Optional[str] = None


class NamespaceDetailStats(BaseModel):
    """질의 상태는 답변 / 지식 공백 둘뿐(2026-10-02, #61). 틀린 답은 정정 요청(개선 원장 pending)으로 센다."""
    namespace: str
    total_queries: int
    answered: int
    no_knowledge: int          # 아직 안 메운 공백
    filled: int                # 지식 등록으로 메운 공백
    system_errors: int = 0     # LLM 연결 실패 — 위 통계(전체 포함)에서 빠짐
    corrections_open: int
    term_distribution: list[TermStat]


# ─── LLM Settings ───────────────────────────────────────────────────────────

class LLMConfigUpdate(BaseModel):
    provider: str
    ollama_base_url: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_timeout: Optional[int] = None
    # InHouse (OAuth2 Client Credentials)
    inhouse_llm_base_url: Optional[str] = None
    inhouse_llm_client_id: Optional[str] = None
    inhouse_llm_client_secret: Optional[str] = None
    inhouse_llm_agent_id: Optional[str] = None
    inhouse_llm_agent_code: Optional[str] = None
    inhouse_llm_conversation_id: Optional[str] = None
    inhouse_llm_model: Optional[str] = None
    inhouse_llm_response_mode: Optional[str] = None
    inhouse_llm_timeout: Optional[int] = None


class LLMTestRequest(BaseModel):
    provider: str
    ollama_base_url: Optional[str] = None
    ollama_model: Optional[str] = None
    # InHouse (OAuth2 Client Credentials)
    inhouse_llm_base_url: Optional[str] = None
    inhouse_llm_client_id: Optional[str] = None
    inhouse_llm_client_secret: Optional[str] = None
    inhouse_llm_agent_id: Optional[str] = None
    inhouse_llm_agent_code: Optional[str] = None
    inhouse_llm_conversation_id: Optional[str] = None
    inhouse_llm_model: Optional[str] = None
    inhouse_llm_response_mode: Optional[str] = None


class ThresholdUpdate(BaseModel):
    glossary_min_similarity: Optional[float] = None
    knowledge_min_score: Optional[float] = None
    knowledge_high_score: Optional[float] = None
    knowledge_mid_score: Optional[float] = None
    duplicate_min_similarity: Optional[float] = None


class SearchDefaultsUpdate(BaseModel):
    default_top_k: Optional[int] = None
    default_w_vector: Optional[float] = None
    default_w_keyword: Optional[float] = None
