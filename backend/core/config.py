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
    inhouse_llm_url: str = ""
    inhouse_llm_api_key: str = ""
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
    inhouse_llm_response_mode: str = "streaming"
    inhouse_llm_timeout: int = 120

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

    # JWT 인증
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7


settings = Settings()
