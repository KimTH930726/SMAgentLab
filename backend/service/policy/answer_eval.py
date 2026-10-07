"""최종 답변 정확도 실행 이력 (2026-10-07) — scripts/eval_answers.py report --save가 집계만 남기고, 평가 게이트 "답변 정확도" 탭이 읽는다.

검색 지표(track2 hit@K)와 달리 "답이 원문과 맞는가"를 로컬 LLM이 채점한 결과. 질문·답 원문은 저장하지 않는다(집계·유형별 건수만) —
문항별 상세는 실행한 사람의 eval_out/ 파일에만 있다.
"""
from __future__ import annotations

import json
from typing import Optional

from core.database import get_conn


async def save_run(*, label: str, variant: str, total_n: int, counts: dict, by_type: dict,
                   retrieval_ok_but_wrong: int, retrieval_ok: int, judge_model: str, notes: Optional[str] = None) -> int:
    async with get_conn() as conn:
        return await conn.fetchval(
            """
            INSERT INTO eval_answer_run (label, variant, total_n, counts, by_type, retrieval_ok_but_wrong, retrieval_ok,
                                         judge_model, notes)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8, $9) RETURNING id
            """,
            label, variant, total_n, json.dumps(counts, ensure_ascii=False), json.dumps(by_type, ensure_ascii=False),
            retrieval_ok_but_wrong, retrieval_ok, judge_model, notes,
        )


async def list_history(limit: int = 30) -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT id, run_at, label, variant, total_n, counts, by_type, retrieval_ok_but_wrong, retrieval_ok, judge_model, notes "
            "FROM eval_answer_run ORDER BY run_at DESC LIMIT $1", limit)
    out = []
    for r in rows:
        d = dict(r)
        for k in ("counts", "by_type"):
            if isinstance(d[k], str):
                d[k] = json.loads(d[k])
        out.append(d)
    return out
