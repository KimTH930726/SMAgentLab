"""Confluence 재구성 프롬프터 효과 실측 — 원문(before) vs 재구성본(after)의 검색 정확도 비교.

배경: 2026-09-17 파일럿에서 정성적으로(체크리스트 통과/실패) 포맷 문제를 찾고 프롬프터로
고쳤는데, "그래서 검색 정확도가 실제로 얼마나 좋아지나"는 수치가 없었다. 이 스크립트는
같은 6개 청크(결함 4개: 12850~12853, 정상 대조군 2개: 12858/12863)에 대해 실제 사람이
물어볼 법한 질문(로컬 LLM 생성, backend/scripts/tmp_reformat/test_queries.json)을 원문/
재구성본 각각과 코사인 유사도로 비교 — "포맷을 이렇게 바꾸면 유사도가 이만큼 오른다"는
근거를 남긴다. Track2(정책서 A/B)와 같은 방법론(실측 코사인 유사도 비교), 다만 여긴
검색 파이프라인 전체가 아니라 "청크 포맷 자체의 질의 매칭력"만 격리해서 잰다.

실행 (컨테이너 안):
    docker compose exec backend python scripts/measure_reformat_accuracy.py
"""
import asyncio
import json
import sys

sys.path.insert(0, "/app")

import numpy as np

from core.database import init_pool, close_pool, get_conn
from shared.embedding import embedding_service

CHUNK_IDS = [12850, 12851, 12852, 12853, 12858, 12863]
TMP_DIR = "/app/scripts/tmp_reformat"


def cos_sim(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


async def fetch_raw(chunk_id: int) -> str:
    async with get_conn() as conn:
        row = await conn.fetchrow("SELECT content FROM rag_knowledge WHERE id = $1", chunk_id)
    return row["content"]


def read_reformatted(chunk_id: int) -> str:
    with open(f"{TMP_DIR}/reformatted_{chunk_id}.md", encoding="utf-8") as f:
        return f.read()


async def main() -> None:
    embedding_service.load()
    await init_pool()
    try:
        with open(f"{TMP_DIR}/test_queries.json", encoding="utf-8") as f:
            queries_by_id = {row["id"]: row["queries"] for row in json.load(f)}

        results = []
        for cid in CHUNK_IDS:
            raw = await fetch_raw(cid)
            reformatted = read_reformatted(cid)
            raw_vec = np.array(await embedding_service.embed(raw))
            ref_vec = np.array(await embedding_service.embed(reformatted))

            for q in queries_by_id[cid]:
                q_vec = np.array(await embedding_service.embed(q))
                sim_raw = cos_sim(q_vec, raw_vec)
                sim_ref = cos_sim(q_vec, ref_vec)
                results.append((cid, q, sim_raw, sim_ref, sim_ref - sim_raw))

        print(f"{'id':>6} | {'raw':>6} | {'reformatted':>11} | {'diff':>7} | query")
        print("-" * 90)
        for cid, q, sim_raw, sim_ref, diff in results:
            marker = "▲" if diff > 0.01 else ("▼" if diff < -0.01 else "≈")
            print(f"{cid:>6} | {sim_raw:>6.3f} | {sim_ref:>11.3f} | {diff:>+6.3f}{marker} | {q}")

        defect_ids = {12850, 12851, 12852, 12853}
        defect_diffs = [d for cid, _, _, _, d in results if cid in defect_ids]
        control_diffs = [d for cid, _, _, _, d in results if cid not in defect_ids]
        print("\n[요약]")
        print(f"결함 청크(12850~12853) 평균 유사도 변화: {np.mean(defect_diffs):+.4f} (n={len(defect_diffs)})")
        print(f"대조군(12858/12863) 평균 유사도 변화:      {np.mean(control_diffs):+.4f} (n={len(control_diffs)})")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
