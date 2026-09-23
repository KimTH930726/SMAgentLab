"""Tests for service/policy/decompose.py — suggest_param_fields() (2026-09-23 신규)."""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent

sys.modules["service.policy"] = MagicMock()
sys.modules["service.llm"] = MagicMock()
sys.modules["service.llm.factory"] = MagicMock()

_spec = _ilu.spec_from_file_location(
    "service.policy.decompose", str(_backend_dir / "service" / "policy" / "decompose.py")
)
decompose = _ilu.module_from_spec(_spec)
sys.modules["service.policy.decompose"] = decompose
_spec.loader.exec_module(decompose)


def _mock_provider(return_value=None, side_effect=None):
    provider = MagicMock()
    provider.generate_once = AsyncMock(return_value=return_value, side_effect=side_effect)
    return provider


class TestSuggestParamFields:
    @pytest.mark.asyncio
    async def test_blank_segment_returns_none_without_llm_call(self, monkeypatch):
        provider = _mock_provider()
        monkeypatch.setattr(decompose, "get_llm_provider", MagicMock(return_value=provider))
        result = await decompose.suggest_param_fields("   ")
        assert result is None
        provider.generate_once.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_json_parsed_correctly(self, monkeypatch):
        provider = _mock_provider(return_value='{"name": "최대개수", "condition": "일반 배달", "value": "20", "unit": "개"}')
        monkeypatch.setattr(decompose, "get_llm_provider", MagicMock(return_value=provider))

        result = await decompose.suggest_param_fields("일반 배달: 20개")

        assert result == {"name": "최대개수", "condition": "일반 배달", "value": "20", "unit": "개"}

    @pytest.mark.asyncio
    async def test_code_fenced_json_stripped(self, monkeypatch):
        provider = _mock_provider(return_value='```json\n{"name": "상태", "condition": null, "value": "주문취소", "unit": null}\n```')
        monkeypatch.setattr(decompose, "get_llm_provider", MagicMock(return_value=provider))

        result = await decompose.suggest_param_fields("주문취소", reason="문맥 없는 단일 용어")

        assert result == {"name": "상태", "condition": None, "value": "주문취소", "unit": None}

    @pytest.mark.asyncio
    async def test_reason_included_in_prompt_when_given(self, monkeypatch):
        provider = _mock_provider(return_value='{"name": "a", "condition": null, "value": null, "unit": null}')
        monkeypatch.setattr(decompose, "get_llm_provider", MagicMock(return_value=provider))

        await decompose.suggest_param_fields("내용", reason="사유텍스트")

        call = provider.generate_once.call_args
        assert "사유텍스트" in call.kwargs["prompt"]

    @pytest.mark.asyncio
    async def test_malformed_json_returns_none(self, monkeypatch):
        provider = _mock_provider(return_value="이건 JSON이 아님")
        monkeypatch.setattr(decompose, "get_llm_provider", MagicMock(return_value=provider))

        result = await decompose.suggest_param_fields("내용")

        assert result is None

    @pytest.mark.asyncio
    async def test_non_dict_json_returns_none(self, monkeypatch):
        provider = _mock_provider(return_value='["a", "b"]')
        monkeypatch.setattr(decompose, "get_llm_provider", MagicMock(return_value=provider))

        result = await decompose.suggest_param_fields("내용")

        assert result is None

    @pytest.mark.asyncio
    async def test_llm_exception_returns_none(self, monkeypatch):
        provider = _mock_provider(side_effect=RuntimeError("LLM 게이트웨이 타임아웃"))
        monkeypatch.setattr(decompose, "get_llm_provider", MagicMock(return_value=provider))

        result = await decompose.suggest_param_fields("내용")

        assert result is None

    @pytest.mark.asyncio
    async def test_missing_fields_default_to_none(self, monkeypatch):
        provider = _mock_provider(return_value='{"name": "이름만있음"}')
        monkeypatch.setattr(decompose, "get_llm_provider", MagicMock(return_value=provider))

        result = await decompose.suggest_param_fields("내용")

        assert result == {"name": "이름만있음", "condition": None, "value": None, "unit": None}
