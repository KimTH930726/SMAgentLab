"""컨플루언스 지식 평가 문항 생성(합성) — 로컬 LLM이 원문 조각을 읽고 "그 조각으로 답할 수 있는 질문"을 만든다.

보안 규칙: 컨플루언스 원문은 폐쇄망 로컬 LLM(JUDGE_LLM_BASE_URL, ask_internal --target local과 같은 엔드포인트)으로만 보내고,
결과(질문·정답 지식 id)는 git 제외 폴더(eval_out/)에만 쓴다. 화면 출력은 개수·집계 숫자만 — 원문·질문 문장을 찍지 않는다.

업계 방식(합성 평가 세트, 예: RAGAS testset)과 같은 한계: 원문에서 만든 질문은 원문 단어를 닮기 쉬워 키워드 검색에 유리하다 —
프롬프트로 바꿔 말하기를 요구하고, 질문과 원문의 단어 겹침(중앙값)을 같이 기록해 해석에 쓴다. 사람 검수 전까지는 "검색 회귀 감지용".

    python scripts/confluence_synth_questions.py --out eval_out/confluence_synth.jsonl [--per-chunk 2] [--min-chars 80]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

URL = os.environ.get("JUDGE_LLM_BASE_URL", "http://10.21.37.34:8000/v1")
MODEL = os.environ.get("JUDGE_LLM_MODEL", "")

SYSTEM = "너는 사내 IT 운영 문서로 검색 평가 문항을 만드는 도우미다. 지시한 JSON만 출력한다."
PROMPT = """아래 [문서 조각]만 보고 답할 수 있는 질문을 {n}개 만들어라. 규칙:
1. 운영 담당자가 실제로 채팅에 물어볼 법한 구체적인 질문. 조각 하나로 답이 나와야 한다.
2. 조각의 문장·제목 표현을 그대로 베끼지 말고 다른 말(동의어·일상 표현)로 묻는다. 고유명사·코드값은 그대로 써도 된다.
3. "이 문서", "위 내용"처럼 조각을 가리키는 말은 쓰지 않는다.
출력: JSON 배열만. 예: ["질문1", "질문2"]

[문서 조각]
{chunk}"""


def _overlap(q: str, chunk: str) -> float:
    from trustkit.text.ko import tokenize
    a, b = set(tokenize(q)), set(tokenize(chunk))
    return len(a & b) / len(a) if a else 0.0


async def main() -> None:
    import httpx
    from core.database import init_pool, get_conn
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-chunk", type=int, default=2)
    ap.add_argument("--min-chars", type=int, default=80)
    a = ap.parse_args()
    await init_pool()
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT k.id, n.name AS ns, k.content, k.source_document_id, k.source_file FROM rag_knowledge k "
            "JOIN ops_namespace n ON n.id = k.namespace_id "
            "WHERE k.source_type LIKE 'confluence%' AND k.status = 'active' ORDER BY k.id")
    rows = [r for r in rows if len((r["content"] or "").strip()) >= a.min_chars]
    out, fails, overlaps = [], 0, []
    async with httpx.AsyncClient(timeout=180) as client:
        model = MODEL or (await client.get(f"{URL}/models")).json()["data"][0]["id"]
        for r in rows:
            try:
                resp = await client.post(f"{URL}/chat/completions", json={
                    "model": model, "temperature": 0.3, "max_tokens": 600,
                    "messages": [{"role": "system", "content": SYSTEM},
                                 {"role": "user", "content": PROMPT.format(n=a.per_chunk, chunk=r["content"][:3000])}]})
                text = resp.json()["choices"][0]["message"]["content"]
                qs = json.loads(re.search(r"\[.*\]", text, re.S).group(0))
                qs = [q.strip() for q in qs if isinstance(q, str) and q.strip()][: a.per_chunk]
            except Exception:  # noqa: BLE001 — 한 조각 실패는 건너뛰고 개수만 보고
                fails += 1
                continue
            for j, q in enumerate(qs):
                overlaps.append(_overlap(q, r["content"]))
                out.append({"qid": f"k{r['id']}-{j}", "ns": r["ns"], "gold_ids": [r["id"]], "question": q,
                            "cluster": f"doc{r['source_document_id'] or r['source_file'] or r['id']}"})
    Path(a.out).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out), encoding="utf-8")
    print(f"조각 {len(rows)}개 → 문항 {len(out)}개 (실패 {fails}) · 파트 {len({x['ns'] for x in out})}개 · 원문 묶음 {len({x['cluster'] for x in out})}개")
    if overlaps:
        print(f"질문 단어 중 원문에 있는 비율: 중앙값 {statistics.median(overlaps):.0%} (높을수록 키워드 검색에 유리한 문항)")
    print(f"저장: {a.out} (git 제외 — 원문 파생물, 화면에 출력하지 않음)")


if __name__ == "__main__":
    asyncio.run(main())
