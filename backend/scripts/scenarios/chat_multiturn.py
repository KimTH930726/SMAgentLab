"""멀티턴 실서버 시나리오(컨테이너 안에서 실행 — eval_answers를 쓴다). 온라인스토어 DB(컨플루언스 지식 없음)만 쓴다.
T1 생략형 후속 질문 / T2 다른 대화의 같은 후속 질문(캐시 오염) / T3 주제 전환 / T4 긴 대화 기억 / T5 목록 뒤 참조 / T6 정확도(단독 vs 앞 대화 뒤)."""
import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import _lock  # noqa: E402
_lock.hold(__file__)   # 실서버 시나리오는 한 번에 하나만

import asyncio
import json
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")
import logging  # noqa: E402
logging.disable(logging.INFO)

API = "http://localhost:8000"
NS = "온라인스토어 DB"
OUT = "eval_out/multiturn_result.jsonl"


def _req(method, path, body=None, tok=None):
    return urllib.request.Request(API + path, method=method,
                                  data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                                  headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {tok}"} if tok else {})})


TOK = json.load(urllib.request.urlopen(_req("POST", "/api/auth/login", {"username": "admin", "password": "1111"})))["access_token"]


def clear_cache():
    urllib.request.urlopen(_req("DELETE", "/api/admin/cache?namespace=" + urllib.parse.quote(NS), tok=TOK))


def ask(q, conv=None, no_cache=True):
    if no_cache:
        clear_cache()
    ev = []
    for raw in urllib.request.urlopen(_req("POST", "/api/chat/stream", {"namespace": NS, "question": q, "conversation_id": conv}, TOK), timeout=300):
        line = raw.decode("utf-8", "ignore").strip()
        if line.startswith("data:"):
            ev.append(json.loads(line[5:].strip()))
    conv = next((e["conversation_id"] for e in ev if e.get("type") == "meta" and e.get("conversation_id")), conv)
    ans = "".join(e.get("data", "") for e in ev if e.get("type") == "token")
    cached = not any(e.get("type") == "status" and e.get("step") == "llm" for e in ev) and not any(e.get("type") == "clarify" for e in ev)
    cites = next((e.get("policy_citations") for e in reversed(ev) if e.get("type") == "meta" and e.get("policy_citations")), []) or []
    return conv, ans, cached, [c.get("policy_name") for c in cites][:5]


def log(name, **kw):
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(json.dumps({"test": name, **kw}, ensure_ascii=False) + "\n")


def short(s, n=260):
    return " ".join((s or "").split())[:n]


async def t6():
    """정확도: 골든 문항을 단독으로 vs 앞에 다른 골든 문항 2개를 물은 대화 뒤에 — 같은 채점기·기준으로 비교."""
    import eval_answers as E
    from core.database import init_pool, get_conn
    from service.policy import track2
    await init_pool()
    async with get_conn() as conn:
        ns_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM ops_namespace")}
    golden = await track2._load_golden_set(track2._GOLDEN_SET_PATH, ns_ids)
    raw = {json.loads(l)["query"]: json.loads(l) for l in track2._GOLDEN_SET_PATH.read_text(encoding="utf-8").splitlines() if l.strip()}
    # 앞서 "정답"이던 문항만(어려운 문항이 섞이면 앞 대화 탓인지 원래 틀린 건지 못 가린다) — 기준 채점 파일은 인자로(없으면 전부)
    base = sys.argv[1] if len(sys.argv) > 1 else ""
    prev = {json.loads(l)["qid"]: json.loads(l) for l in open(base, encoding="utf-8") if l.strip()} if base else {}
    pool = [g for g in golden if g["namespace_name"] == NS and g["type"] in ("param", "condition_filter", "narrative")]
    if prev:
        pool = [g for g in pool if prev.get(raw[g["query"]]["qid"], {}).get("judge", {}).get("label") == "정답"]
    targets = pool[:8]
    # 앞 대화: 같은 업무(시트)의 다른 문항 2개 — 비슷한 주제일수록 앞 맥락이 섞일 위험이 크다
    rows = []
    for g in targets:
        qid = raw[g["query"]]["qid"]
        sheet = lambda x: (raw[x["query"]].get("source") or {}).get("sheet")   # noqa: E731 — 같은 시트(같은 업무)의 앞 문항
        same = [x for x in pool if x is not g and sheet(x) == sheet(g)] or [x for x in pool if x is not g]
        pre = same[:2]
        _, solo, _, _ = ask(g["query"])
        conv = None
        for p in pre:
            conv, _, _, _ = ask(p["query"], conv)
        _, multi, _, _ = ask(g["query"], conv)
        async with get_conn() as conn:
            refs = await conn.fetch("SELECT policy_name, array_to_string(category_path, ' > ') AS cat, raw_body FROM policy_item "
                                    "WHERE id = ANY($1::int[]) AND status NOT IN ('deprecated','rejected') ORDER BY id LIMIT 15", list(g["gold_ids"]))
        base = {"type": g["type"], "question": g["query"], "expected": raw[g["query"]].get("expected_answer"),
                "refs": [{"policy": r["policy_name"], "category": r["cat"], "body": (r["raw_body"] or "")[:600]} for r in refs],
                "n_refs_total": len(refs), "ref_names": []}
        rows.append((qid, [p["query"] for p in pre], {**base, "qid": qid, "answer": solo}, {**base, "qid": qid, "answer": multi}))
    import httpx
    async with httpx.AsyncClient(timeout=120) as client:
        model = E.JUDGE_MODEL or (await client.get(f"{E.JUDGE_URL}/models")).json()["data"][0]["id"]
        for qid, pre, a, b in rows:
            ja, jb = await E._judge_one(client, model, a), await E._judge_one(client, model, b)
            log("T6", qid=qid, pre=pre, solo=ja["label"], multi=jb["label"], solo_reason=ja["reason"][:120], multi_reason=jb["reason"][:120],
                solo_ans=short(a["answer"]), multi_ans=short(b["answer"]))
            print(f"T6 {qid} 단독 {ja['label']} / 앞 대화 뒤 {jb['label']}", flush=True)


def main():
    _main()


def _main():
    open(OUT, "w").close()
    # T1 생략형 후속
    c1, a1, _, _ = ask("선물하기 교환 정책 알려줘")
    c1, a2, _, cit = ask("수신자는요?", c1)
    log("T1", turn1=short(a1), followup=short(a2), cites=cit)
    print("T1 done", flush=True)
    # T2 다른 대화의 같은 후속(캐시를 안 비우고 — 실제 운영처럼)
    c2, b1, _, _ = ask("리워드 텀블러 쿠폰 지급 정책 알려줘", no_cache=False)   # T1 후속 답 캐시를 지우지 않게
    c2, b2, cached, cit2 = ask("수신자는요?", c2, no_cache=False)
    log("T2", turn1=short(b1), followup=short(b2), cached=cached, same_as_T1=(b2 == a2), cites=cit2)
    print("T2 done", cached, b2 == a2, flush=True)
    # T3 주제 전환 — 같은 질문 단독과 비교
    c3, _, _, _ = ask("쿠폰 중복 사용 가능해?")
    c3, s3, _, cit3 = ask("반품 신청은 언제까지 할 수 있어?", c3)
    _, solo3, _, solo_cit3 = ask("반품 신청은 언제까지 할 수 있어?")
    log("T3", after_coupon=short(s3), solo=short(solo3), cites_after=cit3, cites_solo=solo_cit3)
    print("T3 done", flush=True)
    # T4 긴 대화 기억(요약은 4교환마다)
    c4 = None
    for q in ["상품쿠폰은 장바구니쿠폰이랑 같이 쓸 수 있어?", "배송비쿠폰은 어떤 특징이 있어?", "선물하기 주문은 수신자가 반품할 수 있어?",
              "교환 철회는 언제 할 수 있어?", "리워드 별은 언제 지급돼?"]:
        c4, _, _, _ = ask(q, c4)
    c4, a4, _, _ = ask("내가 맨 처음에 물어본 질문이 뭐였고, 답이 뭐였는지 한 줄로 정리해줘", c4)
    log("T4", recall=short(a4, 400))
    print("T4 done", flush=True)
    # T5 목록 뒤 참조
    c5, l5, _, _ = ask("교환 정책 전부 보여줘")
    c5, r5, _, cit5 = ask("그 중에 교환 철회는 언제 가능해?", c5)
    log("T5", list_len=len(l5), followup=short(r5), cites=cit5)
    print("T5 done", flush=True)
    asyncio.run(t6())
    clear_cache()


main()
