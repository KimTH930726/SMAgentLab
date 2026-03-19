"""LLM Provider 팩토리 — 싱글톤 + 런타임 전환."""
from core.config import settings
from service.llm.base import LLMProvider
from service.llm.inhouse import InHouseLLMProvider
from service.llm.ollama import OllamaProvider

_provider: LLMProvider | None = None
_runtime_config: dict | None = None


def get_llm_provider() -> LLMProvider:
    """싱글톤 LLM Provider 반환."""
    global _provider
    if _provider is None:
        _provider = _create_provider()
    return _provider


def _create_provider() -> LLMProvider:
    if _runtime_config is not None:
        provider_type = _runtime_config.get("provider", settings.llm_provider)
    else:
        provider_type = settings.llm_provider

    if provider_type == "inhouse":
        return InHouseLLMProvider(_runtime_config)
    else:
        return OllamaProvider(_runtime_config)


def switch_provider(config: dict) -> LLMProvider:
    """런타임에 LLM Provider를 전환한다. 재시작 시 .env 값으로 복귀."""
    global _provider, _runtime_config
    _runtime_config = config
    _provider = _create_provider()
    return _provider


def get_runtime_config() -> dict:
    """현재 유효한 LLM 설정 반환 (런타임 오버라이드 우선)."""
    cfg = _runtime_config or {}
    provider = cfg.get("provider", settings.llm_provider)
    return {
        "provider": provider,
        "is_runtime_override": _runtime_config is not None,
        "ollama": {
            "base_url": cfg.get("ollama_base_url", settings.ollama_base_url),
            "model": cfg.get("ollama_model", settings.ollama_model),
            "timeout": cfg.get("ollama_timeout", settings.ollama_timeout),
        },
        "inhouse": {
            "url": cfg.get("inhouse_llm_url", settings.inhouse_llm_url),
            "agent_code": cfg.get("inhouse_llm_agent_code", settings.inhouse_llm_agent_code),
            "model": cfg.get("inhouse_llm_model", settings.inhouse_llm_model) or "",
            "has_api_key": bool(cfg.get("inhouse_llm_api_key", settings.inhouse_llm_api_key)),
            "response_mode": cfg.get("inhouse_llm_response_mode", settings.inhouse_llm_response_mode),
            "timeout": cfg.get("inhouse_llm_timeout", settings.inhouse_llm_timeout),
        },
    }
