"""컨플루언스 지식 평가 문항 생성(합성) — 로컬 LLM이 원문 조각을 읽고 "그 조각으로 답할 수 있는 질문"을 만든다.

보안 규칙: 컨플루언스 원문은 폐쇄망 로컬 LLM(JUDGE_LLM_BASE_URL, ask_internal --target local과 같은 엔드포인트)으로만 보내고,
결과(질문·정답 지식 id)는 git 제외 폴더(eval_out/)에만 쓴다. 화면 출력은 개수·집계 숫자만 — 원문·질문 문장을 찍지 않는다.

업계 방식(합성 평가 세트, 예: RAGAS testset)과 같은 한계: 원문에서 만든 질문은 원문 단어를 닮기 쉬워 키워드 검색에 유리하다 —
프롬프트로 바꿔 말하기를 요구하고, 질문과 원문의 단어 겹침(중앙값)을 같이 기록해 해석에 쓴다. 사람 검수 전까지는 "검색 회귀 감지용".

    python scripts/confluence_synth_questions.py --out eval_out/confluence_synth.jsonl [--per-chunk 2] [--min-chars 80]
    # 한 컬렉션 조각만 표본으로, 질문은 다른 컬렉션에서 묻게(검색 범위 실험 — 11/02 조직 컬렉션 확장):
    python scripts/confluence_synth_questions.py --out eval_out/cx_synth.jsonl --ns "홈페이지 시스템" --sample 100 --per-chunk 1 --ask-ns 회원
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
sys.path.insert(0, str(Path(__file__).resolve().parent))   # knowledge_eval_set

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
# 범위 밖 문항(11/16, 골든셋 자동화 1단계) — 정답 = "모른다". 단정 오답(지식에 없는데 그럴듯하게 지어냄)을 따로 재려고.
# 업계: Giskard RAGET out-of-scope 질문(지식 베이스 맥락은 쓰되 답은 없는 질문). 조각에 답이 없다는 것만 보장되고, 다른 문서에 답이
# 있을 수는 있다 → 범위 밖 문항은 골든셋 후보로만(사람이 확인해야 들어감), 측정은 정상 문항 정답률과 섞지 않고 따로 센다.
# 11/16 표본 9 사람 검토: 정말 답 없음 3 · 다른 문서에 답 있음 1 · 사람·연락처(개인정보·상식) 5 — 원인은 지시문의 "담당 부서·연락처".
# 지시문만 고치면 다시 나올 수 있어 코드로도 거른다(PERSON_Q, 거른 수를 출력).
PERSON_Q = re.compile(r"연락처|전화|휴대폰|핸드폰|이메일|메일\s*주소|부서장|팀장|담당자\s*(?:이름|는\s*누구|가\s*누구)|누가\s*담당|누구에게")
OOS_PROMPT = """아래 [문서 조각]과 같은 업무·시스템에 대해 운영 담당자가 채팅에 물어볼 법하지만, 이 조각에는 답이 **없는** 질문을 {n}개 만들어라. 규칙:
1. 조각에 나온 시스템·업무·화면 이름을 써서 자연스럽게 묻는다.
2. 조각에 아예 없는 업무 정보(다른 설정값·처리 기준·일정·장애 이력·다른 절차)를 묻는다. 조각 문장을 조금 바꾸면 답이 되는 질문은 안 된다.
   사람·조직 질문(누가 담당인지, 연락처·전화·메일, 부서장)은 만들지 않는다 — 지식 시스템이 다루지 않는 개인정보다.
3. 일반 상식이나 IT 일반 지식만으로 답할 수 있는 질문은 안 된다 — 이 회사 내부 사실을 묻는다.
4. "이 문서", "위 내용"처럼 조각을 가리키는 말은 쓰지 않는다.
출력: JSON 배열만. 예: ["질문1"]

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
    ap.add_argument("--ns", action="append", help="이 컬렉션(이름) 조각만 — 여러 번 가능")
    ap.add_argument("--sample", type=int, default=0, help="조각을 이 개수만 무작위로(고정 시드)")
    ap.add_argument("--ask-ns", help="질문을 이 컬렉션에서 묻는 것으로 기록(기본 = 정답 조각의 컬렉션)")
    ap.add_argument("--oos", type=int, default=0, help="범위 밖 문항(정답 = 모른다)을 만들 조각 수 — 조각마다 1문항, 정상 문항과 다른 조각에서")
    a = ap.parse_args()
    await init_pool()
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT k.id, n.name AS ns, k.content, k.source_document_id, k.source_file, k.confluence_page_id, k.content_norm_md5 FROM rag_knowledge k "
            "JOIN ops_namespace n ON n.id = k.namespace_id "
            "WHERE k.source_type LIKE 'confluence%' AND k.status = 'active' ORDER BY k.id")
        # 정답 닻을 만들 비교 대상 = 사용 중 조각 전체(컨플 아닌 것도 — 같은 줄이 어디에 있든 닻이 겹치면 안 된다)
        live = [dict(r) for r in await conn.fetch(
            "SELECT id, content, confluence_page_id, content_norm_md5 FROM rag_knowledge WHERE status = 'active'")]
    rows = [r for r in rows if len((r["content"] or "").strip()) >= a.min_chars and (not a.ns or r["ns"] in a.ns)]
    import random
    rng = random.Random(1102)
    oos_rows = []
    if a.oos:   # 정상 문항과 겹치지 않는 조각에서(같은 조각의 정상 질문과 범위 밖 질문이 서로 단서가 되지 않게)
        oos_rows = sorted(rng.sample(rows, min(a.oos, len(rows))), key=lambda r: r["id"])
        rows = [r for r in rows if r["id"] not in {o["id"] for o in oos_rows}]
    if a.sample and len(rows) > a.sample:
        rows = sorted(rng.sample(rows, a.sample), key=lambda r: r["id"])
    from knowledge_eval_set import anchors_for
    anchors = anchors_for([r["id"] for r in rows], live)
    out, fails, overlaps = [], 0, []
    person_dropped = 0

    async def ask(client, model, prompt, n, chunk):
        resp = await client.post(f"{URL}/chat/completions", json={
            "model": model, "temperature": 0.3, "max_tokens": 600,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt.format(n=n, chunk=chunk[:3000])}]})
        text = resp.json()["choices"][0]["message"]["content"]
        qs = json.loads(re.search(r"\[.*\]", text, re.S).group(0))
        return [q.strip() for q in qs if isinstance(q, str) and q.strip()][:n]

    async with httpx.AsyncClient(timeout=180) as client:
        model = MODEL or (await client.get(f"{URL}/models")).json()["data"][0]["id"]
        for r in rows if a.per_chunk > 0 else []:   # --per-chunk 0 = 범위 밖 문항만(표본 확인용)
            try:
                qs = await ask(client, model, PROMPT, a.per_chunk, r["content"])
            except Exception:  # noqa: BLE001 — 한 조각 실패는 건너뛰고 개수만 보고
                fails += 1
                continue
            for j, q in enumerate(qs):
                overlaps.append(_overlap(q, r["content"]))
                # 정답 = 닻(조각 id 아님 — 다시 적재해도 찾음, knowledge_eval_set). 만들 때 id는 기록으로만
                out.append({"qid": f"k{r['id']}-{j}", "ns": a.ask_ns or r["ns"], "gold_ns": r["ns"], **({"ask_fixed": True} if a.ask_ns else {}),
                            "gold_ids_at_make": [r["id"]], "gold_anchors": [anchors[r["id"]]], "question": q,
                            "cluster": f"doc{r['source_document_id'] or r['source_file'] or r['id']}"})
        for r in oos_rows:
            try:
                qs = await ask(client, model, OOS_PROMPT, 1, r["content"])
            except Exception:  # noqa: BLE001
                fails += 1
                continue
            for q in qs:
                if PERSON_Q.search(q):   # 사람·연락처 질문 — 개인정보, 범위 밖 문항으로 쓰지 않음
                    person_dropped += 1
                    continue
                out.append({"qid": f"oos{r['id']}", "ns": a.ask_ns or r["ns"], "expect": "abstain", "question": q,
                            "seed_ids_at_make": [r["id"]], "cluster": f"doc{r['source_document_id'] or r['source_file'] or r['id']}"})
    Path(a.out).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out), encoding="utf-8")
    n_oos = sum(1 for x in out if x.get("expect") == "abstain")
    print(f"조각 {len(rows) + len(oos_rows)}개 → 문항 {len(out)}개(그중 범위 밖 {n_oos}) (실패 {fails}) · 파트 {len({x['ns'] for x in out})}개 · "
          f"원문 묶음 {len({x['cluster'] for x in out})}개 · 정답 닻이 겹쳐 80% 기준인 조각 {sum(1 for v in anchors.values() if v['need'] > 1)}")
    if a.oos:
        print(f"범위 밖: 사람·연락처 질문으로 거른 것 {person_dropped} — 남은 범위 밖 문항은 골든셋 후보(사람 확인 전엔 측정에 안 씀)")
    if overlaps:
        print(f"질문 단어 중 원문에 있는 비율: 중앙값 {statistics.median(overlaps):.0%} (높을수록 키워드 검색에 유리한 문항)")
    print(f"저장: {a.out} (git 제외 — 원문 파생물, 화면에 출력하지 않음)")


if __name__ == "__main__":
    asyncio.run(main())
