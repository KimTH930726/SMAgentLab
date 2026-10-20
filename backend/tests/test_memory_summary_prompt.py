"""대화 요약 프롬프트 — 관리자가 템플릿에서 {dialogue}를 지워도 대화가 빠진 요약을 시키지 않는다(2026-10-20 대체 처리 점검에서 발견:
예전 판정은 바꾼 *뒤에* 자리 표시를 찾아 이 경우를 못 잡았다)."""
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_spec = importlib.util.spec_from_file_location("memory_under_test", str(Path(__file__).resolve().parents[1] / "service/chat/memory.py"))
mem = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mem)


@pytest.mark.asyncio
@pytest.mark.parametrize("template", ["요약해 주세요(자리 표시 없음)", "대화:\n{dialogue}\n요약:"])
async def test_dialogue_always_reaches_llm(monkeypatch, template):
    monkeypatch.setattr(mem, "load_prompt", AsyncMock(return_value=template))
    llm = AsyncMock()
    llm.generate.return_value = ("요약", None)
    await mem._summarize_with_llm([{"role": "user", "content": "배송 지연 문의"}], llm)
    assert "배송 지연 문의" in llm.generate.call_args.kwargs["question"]
