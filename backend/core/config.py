from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    설정값 우선순위: .env 환경변수 > 아래 코드 기본값
    - .env에는 인프라 접속정보와 시크릿만 둔다 (DB, JWT키, Fernet키 등)
    - 앱 로직 설정은 여기 코드 기본값으로 관리한다 (Admin UI에서 런타임 변경 가능)
    """
    model_config = {"env_file": ".env"}

    @field_validator("policy_abstain_min_score", mode="before")
    @classmethod
    def _empty_as_off(cls, v):
        # compose가 비어 있는 값을 ""로 넘긴다(POLICY_ABSTAIN_MIN_SCORE: ${...:-}) — 빈 값 = 꺼짐
        return None if v in ("", None) else v

    # ── .env에서 주입 (인프라/시크릿) ─────────────────────────────
    database_url: str = "postgresql://ops:ops1234@localhost:5432/opsdb"
    llm_provider: str = "inhouse"
    ollama_base_url: str = "http://host.docker.internal:11434"

    # InHouse DevX LLM — OAuth2 Client Credentials 인증
    # base_url 이하 /api/v1/auth/token, /api/v1/agent/chat 사용
    inhouse_llm_base_url: str = "https://devx-gw.shinsegae-inc.com"
    inhouse_llm_client_id: str = ""        # OAuth client_id (시스템 공통)
    inhouse_llm_client_secret: str = ""    # OAuth client_secret (시스템 공통)

    jwt_secret_key: str = "change-this-secret-key-in-production"
    fernet_secret_key: str = ""
    admin_default_password: str = "1111"

    # ── 코드 기본값 (Admin UI에서 런타임 변경 가능) ───────────────
    # 임베딩
    embedding_model: str = "nlpai-lab/KURE-v1"
    vector_dim: int = 1024  # 코드에서 실제로 참조되진 않음(DB 컬럼 타입이 SoT) — 정보성 값만 동기화

    # LLM 프로바이더 상세
    ollama_model: str = "exaone3.5:7.8b"
    ollama_timeout: int = 900
    inhouse_llm_model: str = ""
    inhouse_llm_agent_code: str = "playground"
    inhouse_llm_agent_id: str = "b6958377-73f2-4234-a49c-2aa878350a2e"
    # DevX 게이트웨이 /agent/chat은 사전 등록된 conversation_id만 허용.
    # 임의 UUID 사용 시 0바이트 응답 → 시스템 공통 고정 ID 사용.
    # 우리 자체 대화 메모리(요약+시맨틱 리콜)가 history를 직렬화해 query에 포함하므로
    # dify 쪽 멀티턴 메모리는 사실상 무시함.
    inhouse_llm_conversation_id: str = ""
    inhouse_llm_response_mode: str = "streaming"
    # 120→180(2026-10-06, v2.128): 게이트웨이 첫 토큰까지 100~150초인 날이 실측돼 120초에서 끊겨 "연결 실패"로 남았다
    inhouse_llm_timeout: int = 180
    # OAuth 토큰 만료 전 갱신 여유(초). 응답의 expires_in 보다 이 값만큼 일찍 재발급.
    inhouse_llm_token_refresh_buffer: int = 60

    # 검색 기본값
    default_top_k: int = 3
    default_w_vector: float = 0.7
    default_w_keyword: float = 0.3

    # 용어집 활용 방식(v2.128, agents/knowledge_rag/knowledge/glossary_terms.py):
    #   lexical   — 질문에 글자 그대로 나온 용어·동의어만(설명은 LLM 문맥, 동의어는 키워드 검색 확장)
    #   embedding — 예전 방식(질문 임베딩 ↔ 용어 설명 최근접 1개를 glossary_min_similarity 이상이면 검색어에 붙임)
    #   off       — 안 씀
    glossary_match_mode: str = "lexical"
    # 근거 없음 즉시 판정(v2.128, 기본 꺼짐 — 10/8 이후 실측으로 값 확정): 지식 채택 0건·공통코드/DB 0건이고, 정책 서술 최고점이
    # 이 값 미만이며 정책 파라미터 ts_rank 최고가 policy_abstain_min_param_rank 미만이면 LLM을 부르지 않고 "관련 지식을 찾지 못했습니다"
    policy_abstain_min_score: Optional[float] = None
    policy_abstain_min_param_rank: float = 0.0
    # 질문 기록 → 용어 동의어 자동 수집 주기(시간, 0이면 끔) — agents/knowledge_rag/knowledge/glossary_mining.py
    glossary_mining_interval_hours: int = 24

    # 검색 임계값
    glossary_min_similarity: float = 0.5
    knowledge_min_score: float = 0.35
    knowledge_high_score: float = 0.8
    knowledge_mid_score: float = 0.55
    # 지식 등록 시 중복 의심 판정 임계값 — 이 이상이면 즉시 반영하지 않고 승인 대기로 등록
    duplicate_min_similarity: float = 0.88

    # 리랭커 (CrossEncoder)
    reranker_enabled: bool = False  # 폐쇄망: 모델 번들링 후 True로 전환
    reranker_model: str = "dragonkue/bge-reranker-v2-m3-ko"
    reranker_candidates: int = 20  # 1차 검색에서 가져올 후보 수 (리랭킹 후 top_k로 압축)

    # 지식 신선도 decay
    # 0이면 비활성화. 양수이면 해당 일수를 반감기로 score에 decay 적용.
    freshness_decay_halflife_days: int = 0

    # Semantic Cache (Redis)
    redis_url: str = ""  # 비어있으면 캐시 비활성화. 예: redis://ops-redis:6379/0

    # JWT 인증
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # DB 커넥션 풀
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10
    db_pool_acquire_timeout: float = 10.0  # 풀이 고갈됐을 때 커넥션 대기 최대 시간(초)


settings = Settings()
