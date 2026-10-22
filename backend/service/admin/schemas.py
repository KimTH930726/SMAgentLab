"""관리 도메인 — Pydantic 스키마 (namespace, stats, llm)."""
from typing import Optional
from pydantic import BaseModel, Field

from service.validation import Name


# ─── Namespace ───────────────────────────────────────────────────────────────

class NamespaceCreate(BaseModel):
    name: Name
    description: str = Field(default="", max_length=1000)
    parent_id: Optional[int] = Field(default=None, ge=1, le=2_147_483_647)   # 상위 영역(선택, 최상위만 — 트리 깊이 1)


class NamespaceRename(BaseModel):
    new_name: Name


class NamespaceSettings(BaseModel):
    """업무 영역 담당 파트·상위 영역(관리자). owner_part 빈 문자열 또는 clear_owner = 해제. parent_id = 상위 지정, clear_parent = 최상위로."""
    owner_part: Optional[str] = None
    clear_owner: bool = False
    parent_id: Optional[int] = Field(default=None, ge=1, le=2_147_483_647)
    clear_parent: bool = False


class NamespaceInfo(BaseModel):
    id: int
    name: str
    description: str
    kind: str = "domain"                  # domain | policy_test(E2E 임시)
    owner_part_id: Optional[int] = None   # 이 영역에 직접 지정된 담당(수정 창·파트별 담당 지정이 읽고 쓰는 값). 담당 비교는 id로(파트 이름은 바뀔 수 있다)
    owner_part: Optional[str] = None      # 직접 지정된 담당 파트 이름
    effective_owner_part: Optional[str] = None      # 실효 담당 파트 이름 — 하위 영역이 비어 있으면 상위 담당(수정 권한 규칙과 같음, 권한 판단용)
    effective_owner_part_id: Optional[int] = None   # 실효 담당 id(상속 반영)
    owner_inherited: bool = False         # 실효 담당이 상위에서 물려받은 것인지
    parent_id: Optional[int] = None       # 상위 영역(트리 깊이 1, 없으면 최상위)
    parent: Optional[str] = None          # 상위 영역 이름
    knowledge_count: int
    glossary_count: int
    policy_count: int = 0      # 지금 쓰이는 정책(폐기·반려 제외)
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
