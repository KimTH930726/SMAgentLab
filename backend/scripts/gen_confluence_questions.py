"""컨플루언스 검색 평가 질문 생성 (2026-10-07, v2.129) — 청크마다 1문항을 로컬 LLM(폐쇄망 Qwen)이 만든다.

컨플루언스 원문은 이 세션(외부 LLM)이 직접 읽지 않는 규칙이라, 원문은 컨테이너 안에서 로컬 LLM에만 보내고 결과는 git 제외 폴더
(eval_out/)에 JSONL({"chunk_id", "question"})로만 쓴다. 화면 출력은 개수뿐. 청크 id가 바뀌면(재분할·재적재) 다시 만든다 —
eval_confluence_structure.py / eval_confluence_integrated.py가 이 파일을 읽는다.

실행(컨테이너 안):
  python scripts/gen_confluence_questions.py --out eval_out/conf_questions_v2129.jsonl [--namespaces 외부서비스DB,딜리버스\\ DB]
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, "/app")

LOCAL_URL = os.environ.get("JUDGE_LLM_BASE_URL", "http://10.21.37.34:8000/v1")
SYSTEM = ("너는 사내 운영 지식 검색 평가용 질문을 만드는 사람이다. [문서]를 읽고, 이 문서만 보면 답할 수 있는 실무자 질문 1개를 "
          "한국어 한 문장으로만 출력하라. 문서의 제목·번호를 그대로 베끼지 말고 실제 사용자가 물을 법한 말투로. 다른 설명 금지.")


async def main(out: str, namespaces: list[str]) -> None:
    import httpx
    from core.database import init_pool, get_conn
    await init_pool()
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT k.id, k.content, k.heading_path FROM rag_knowledge k JOIN ops_namespace n ON n.id = k.namespace_id "
            "WHERE k.status = 'active' AND k.source_type LIKE 'confluence%' AND n.name = ANY($1::text[]) ORDER BY k.id", namespaces)
    async with httpx.AsyncClient(timeout=120) as client:
        model = (await client.get(f"{LOCAL_URL}/models")).json()["data"][0]["id"]
        n = 0
        with open(out, "w", encoding="utf-8") as f:
            for r in rows:
                ctx = (" > ".join(r["heading_path"]) + "\n" if r["heading_path"] else "") + r["content"]
                resp = await client.post(f"{LOCAL_URL}/chat/completions", json={
                    "model": model, "temperature": 0.2, "max_tokens": 120,
                    "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"[문서]\n{ctx}\n[문서 끝]"}]})
                try:
                    resp.raise_for_status()
                    lines = (resp.json()["choices"][0]["message"]["content"] or "").strip().splitlines()
                    q = lines[0].strip() if lines else ""
                except Exception as e:  # noqa: BLE001 — 한 청크 실패로 전체를 멈추지 않는다(id만 남김)
                    print(f"  [skip] chunk {r['id']}: {type(e).__name__}")
                    q = ""
                if q:
                    f.write(json.dumps({"chunk_id": r["id"], "question": q}, ensure_ascii=False) + "\n")
                    n += 1
    print(f"[gen] 청크 {len(rows)}개 중 질문 {n}개 → {out} (모델 {model})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--namespaces", default="외부서비스DB,딜리버스 DB")
    a = ap.parse_args()
    asyncio.run(main(a.out, [x.strip() for x in a.namespaces.split(",") if x.strip()]))
