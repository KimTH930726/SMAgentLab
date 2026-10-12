"""사내 LLM 사용량 기록 (2026-10-12) — 어느 기능이 얼마나 쓰는지 숫자가 없어 비용을 줄일 순서를 못 정하던 것.

호출 기능은 스택에서 자동 판별(호출부마다 이름을 붙이지 않음), 토큰은 게이트웨이 metadata.usage, 기록 실패는 호출을 막지 않는다.
"""
import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("_llm_usage", str(Path(__file__).resolve().parent.parent / "service" / "llm" / "usage.py"))
U = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(U)


def test_feature_mapping_by_module_prefix():
    assert U.feature_of("agents.knowledge_rag.agent") == "채팅 답변"
    assert U.feature_of("service.improvement.draft") == "정정 검토(AI 분석·초안)"
    assert U.feature_of("agents.knowledge_rag.ingestion.analyzer") == "등록 — 분할 방식 판단"
    assert U.feature_of("agents.knowledge_rag.knowledge.glossary_terms") == "용어집 — 동의어 생성·수집"
    assert U.feature_of("__main__") == "평가·스크립트"
    assert U.feature_of("some.new.module") == "some.new.module"      # 모르는 기능도 빠지지 않고 모듈 이름으로


def test_tokens_from_gateway_metadata():
    assert U.tokens_from({"usage": {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500}}) == (1200, 300)
    assert U.tokens_from({}) == (None, None)
    assert U.tokens_from(None) == (None, None)
    assert U.tokens_from({"usage": {"prompt_tokens": "x"}}) == (None, None)


def test_caller_module_skips_llm_layer():
    # 이 테스트 모듈에서 부르면 이 모듈 이름(LLM 계층·asyncio 등은 건너뜀)
    assert U.caller_module() == __name__


def test_record_outside_event_loop_does_not_raise():
    U.record(provider="inhouse", method="once", module="x", input_chars=1, output_chars=1,
             input_tokens=None, output_tokens=None, duration_ms=1, ok=True)


def test_record_keeps_task_reference_until_done():
    """리뷰: create_task 반환값을 버리면 실행 중 GC로 기록이 조용히 빠질 수 있다 — 끝날 때까지 참조 보관."""
    import asyncio

    async def main():
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow(row):
            started.set()
            await release.wait()
        U._insert = slow
        U.record(provider="inhouse", method="once", module="x", input_chars=1, output_chars=1,
                 input_tokens=None, output_tokens=None, duration_ms=1, ok=None)
        await started.wait()
        assert len(U._pending) == 1
        release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(U._pending) == 0
    asyncio.run(main())
