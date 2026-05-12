from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    설정값 우선순위: .env 환경변수 > 아래 코드 기본값
    - .env에는 인프라 접속정보와 시크릿만 둔다 (DB, JWT키, Fernet키 등)
    - 앱 로직 설정은 여기 코드 기본값으로 관리한다 (Admin UI에서 런타임 변경 가능)
    """
    model_config = {"env_file": ".env"}

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
    embedding_model: str = "paraphrase-multilingual-mpnet-base-v2"
    vector_dim: int = 768

    # LLM 프로바이더 상세
    ollama_model: str = "exaone3.5:7.8b"
    ollama_timeout: int = 900
    inhouse_llm_model: str = ""
    inhouse_llm_agent_code: str = "playground"
    inhouse_llm_agent_id: str = "b6958377-73f2-4234-a49c-2aa878350a2e"
    inhouse_llm_response_mode: str = "streaming"
    inhouse_llm_timeout: int = 120
    # OAuth 토큰 만료 전 갱신 여유(초). 응답의 expires_in 보다 이 값만큼 일찍 재발급.
    inhouse_llm_token_refresh_buffer: int = 60

    # 검색 기본값
    default_top_k: int = 3
    default_w_vector: float = 0.7
    default_w_keyword: float = 0.3

    # 검색 임계값
    glossary_min_similarity: float = 0.5
    fewshot_min_similarity: float = 0.6
    knowledge_min_score: float = 0.35
    knowledge_high_score: float = 0.8
    knowledge_mid_score: float = 0.55

    # Semantic Cache (Redis)
    redis_url: str = ""  # 비어있으면 캐시 비활성화. 예: redis://ops-redis:6379/0

    # JWT 인증
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7


settings = Settings()
