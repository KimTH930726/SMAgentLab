"""사내 LLM(게이트웨이) 사용량 기록 (2026-10-12) — 호출 1건마다 어느 기능이·얼마나(토큰/글자)·얼마나 걸려·성공했나.

막힌 일: "사내 LLM 토큰이 너무 많이 나간다"(사용자 10/07)는데 호출 지점별 숫자가 없어 무엇부터 줄일지 정할 수 없었다.
업계 기준: OpenTelemetry GenAI 규약 — 호출(span)마다 gen_ai.usage.input_tokens / output_tokens. 게이트웨이(Dify 계열)는 응답
metadata.usage에 prompt_tokens·completion_tokens를 준다 — 있으면 그 값, 없으면 글자 수만.
호출 기능은 호출 스택에서 자동 판별(호출부마다 이름을 붙이지 않는다 — 새 호출부가 생겨도 빠지지 않게). 기록 실패는 호출을 막지 않는다.
본문은 저장하지 않는다(답변·문맥에 컨플루언스·정책 원문이 섞임) — 길이와 토큰 수만.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional

logger = logging.getLogger(__name__)

# 모듈 경로 접두사 → 화면 표시 이름(앞에서부터 처음 맞는 것). 없으면 모듈 경로 그대로.
_FEATURES: list[tuple[str, str]] = [
    ("agents.knowledge_rag.agent", "채팅 답변"),
    ("service.chat", "채팅(요약·기타)"),
    ("service.improvement", "정정 검토(AI 분석·초안)"),
    ("agents.knowledge_rag.ingestion.analyzer", "등록 — 분할 방식 판단"),
    ("agents.knowledge_rag.ingestion.tagger", "등록 — 자동 태깅"),
    ("agents.knowledge_rag.ingestion", "등록 — 기타"),
    ("agents.knowledge_rag.knowledge.glossary_terms", "용어집 — 동의어 생성·수집"),
    ("agents.knowledge_rag.knowledge.router", "등록 — 용어 자동 추출"),
    ("agents.knowledge_rag.knowledge", "지식 관리"),
    ("service.admin", "업무구분 추천"),
    ("service.policy", "정책서 처리"),
    ("service.email_voc", "VOC 메일"),
    ("service.voc", "VOC"),
    ("scripts", "평가·스크립트"),
    ("__main__", "평가·스크립트"),
]
_SKIP_PREFIXES = ("service.llm", "asyncio", "httpx", "anyio", "starlette", "fastapi", "contextlib", "concurrent")


def feature_of(module: str) -> str:
    for prefix, label in _FEATURES:
        if module == prefix or module.startswith(prefix + "."):   # 점 경계로만(service.chat ≠ service.chatops)
            return label
    return module or "알 수 없음"


def caller_module() -> str:
    """호출 스택에서 LLM 계층 밖의 첫 우리 코드 모듈."""
    f = sys._getframe(1)
    while f is not None:
        name = f.f_globals.get("__name__", "")
        if name and not name.startswith(_SKIP_PREFIXES):
            return name
        f = f.f_back
    return ""


def tokens_from(meta: Optional[dict]) -> tuple[Optional[int], Optional[int]]:
    """게이트웨이 응답 metadata.usage → (입력 토큰, 출력 토큰)."""
    u = (meta or {}).get("usage") or {}
    pt, ct = u.get("prompt_tokens"), u.get("completion_tokens")
    return (int(pt) if isinstance(pt, (int, float)) else None, int(ct) if isinstance(ct, (int, float)) else None)


async def _insert(row: tuple) -> None:
    try:
        from core.database import get_conn
        async with get_conn() as conn:
            await conn.execute("""
                INSERT INTO ops_llm_usage (provider, method, module, feature, input_chars, output_chars,
                                           input_tokens, output_tokens, duration_ms, ok)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """, *row)
    except Exception:
        logger.warning("LLM 사용량 기록 실패(호출에는 영향 없음)", exc_info=True)


_pending: set = set()   # 기록 태스크 참조 보관 — 이벤트 루프는 약한 참조만 들어 실행 중에 GC될 수 있다(리뷰 2026-10-12)


def record(*, provider: str, method: str, module: str, input_chars: int, output_chars: int,
           input_tokens: Optional[int], output_tokens: Optional[int], duration_ms: int, ok: Optional[bool]) -> None:
    """기록은 백그라운드로 — 응답 지연을 늘리지 않는다. ok=None은 사용자가 중단(실패로 세지 않음)."""
    row = (provider, method, module[:200], feature_of(module), input_chars, output_chars, input_tokens, output_tokens,
           duration_ms, ok)
    try:
        task = asyncio.get_running_loop().create_task(_insert(row))
    except RuntimeError:
        return   # 이벤트 루프 밖(동기 스크립트) — 기록 생략
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def summary(days: int = 7) -> list[dict]:
    """기능별 집계(최근 N일) — 호출 수·실패·입출력 토큰(없으면 글자)·평균 시간."""
    from core.database import get_conn
    async with get_conn() as conn:
        rows = await conn.fetch("""
            SELECT feature, count(*) AS calls, count(*) FILTER (WHERE NOT ok) AS failed, count(*) FILTER (WHERE ok IS NULL) AS aborted,
                   sum(input_tokens) AS input_tokens, sum(output_tokens) AS output_tokens,
                   count(*) FILTER (WHERE input_tokens IS NOT NULL) AS with_tokens,
                   sum(input_chars) AS input_chars, sum(output_chars) AS output_chars,
                   round(avg(duration_ms)) AS avg_ms, max(created_at)::text AS last_at
            FROM ops_llm_usage WHERE created_at > NOW() - make_interval(days => $1)
            GROUP BY feature ORDER BY sum(coalesce(input_tokens, 0) + coalesce(output_tokens, 0)) DESC, sum(input_chars) DESC
        """, days)
    return [dict(r) for r in rows]
