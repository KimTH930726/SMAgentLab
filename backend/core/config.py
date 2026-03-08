from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env"}

    database_url: str = "postgresql://ops:ops1234@localhost:5432/opsdb"

    # 임베딩
    embedding_model: str = "paraphrase-multilingual-mpnet-base-v2"
    vector_dim: int = 768

    # LLM Provider ("ollama" | "inhouse")
    llm_provider: str = "ollama"

    # Ollama
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "exaone3.5:7.8b"
    ollama_timeout: int = 900

    # 사내 LLM (OpenAI 호환)
    inhouse_llm_url: str = ""
    inhouse_llm_api_key: str = ""
    inhouse_llm_model: str = ""
    inhouse_llm_timeout: int = 120

    # 검색 기본값
    default_top_k: int = 5
    default_w_vector: float = 0.7
    default_w_keyword: float = 0.3

    # 검색 임계값
    glossary_min_similarity: float = 0.5
    fewshot_min_similarity: float = 0.6
    knowledge_min_score: float = 0.35
    knowledge_high_score: float = 0.8
    knowledge_mid_score: float = 0.55

    # JWT 인증
    jwt_secret_key: str = "change-this-secret-key-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Fernet 암호화 (LLM API Key)
    fernet_secret_key: str = ""

    # 초기 관리자
    admin_default_password: str = "1111"


settings = Settings()
