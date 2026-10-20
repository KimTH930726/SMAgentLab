"""재질문 실서버 시나리오 — 주제만 말한 큰 목록 → 재질문(LLM 호출 0) → 선택 → 같은 대화에서 그 범위로 답(다시 묻지 않음) → 다시 열어도 선택지.
본문은 출력하지 않는다(개수·상태만)."""
import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import _lock  # noqa: E402
_lock.hold(__file__)   # 실서버 시나리오는 한 번에 하나만

import json
import subprocess
import urllib.request

API = "http://localhost:8000"
NS = "온라인스토어"


def post(path, body, tok=None):
    req = urllib.request.Request(API + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {tok}"} if tok else {})})
    return urllib.request.urlopen(req, timeout=300)


def get(path, tok):
    return json.load(urllib.request.urlopen(urllib.request.Request(API + path, headers={"Authorization": f"Bearer {tok}"}), timeout=60))


def sql(q):
    return subprocess.run(["docker", "exec", "ops-postgres", "psql", "-U", "ops", "-d", "opsdb", "-tA", "-c", q],
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


def stream(body, tok):
    ev = []
    for raw in post("/api/chat/stream", body, tok):
        line = raw.decode("utf-8", "ignore").strip()
        if line.startswith("data:"):
            ev.append(json.loads(line[5:].strip()))
    return ev


res = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    res.append(bool(cond))


tok = json.load(post("/api/auth/login", {"username": "admin", "password": "1111"}))["access_token"]
calls0 = sql("SELECT count(*) FROM ops_llm_usage")
ev = stream({"namespace": NS, "question": "주문 정책 좀"}, tok)
cf = next((e for e in ev if e.get("type") == "clarify"), None)
conv = next(e["conversation_id"] for e in ev if e.get("type") == "meta" and e.get("conversation_id"))
msg_id = next(e.get("message_id") for e in ev if e.get("type") == "done")
check("주제 질문 + 큰 목록 → 재질문", cf is not None, f"선택지 {len(cf['options']) if cf else 0}개")
check("재질문엔 LLM 호출 없음", sql("SELECT count(*) FROM ops_llm_usage") == calls0)
check("선택지 마지막 = 전부 보기(scope *)", cf and cf["options"][-1]["scope"] == "*")
meta = json.loads(sql(f"SELECT metadata::text FROM ops_message WHERE id = {msg_id}") or "{}")
check("메시지에 선택지 저장(다시 열기용)", bool(meta.get("clarify", {}).get("options")))
check("질의 기록 안 남김(선택한 다음 질문이 남음)", sql(f"SELECT count(*) FROM ops_query_log WHERE message_id = {msg_id}") == "0")

opt = cf["options"][0]
ev2 = stream({"namespace": NS, "question": opt["query"], "conversation_id": conv, "clarify_scope": opt["scope"]}, tok)
check("선택 → 다시 묻지 않음", not any(e.get("type") == "clarify" for e in ev2))
answer = "".join(e.get("data", "") for e in ev2 if e.get("type") == "token")
check("선택 → 답이 옴(같은 대화)", len(answer) > 20 and any(e.get("conversation_id") == conv for e in ev2 if e.get("type") == "meta"),
      f"답 {len(answer)}자")
msgs = get(f"/api/conversations/{conv}/messages", tok)
check("다시 열기: 대화에 질문·재질문·선택·답 4개", len(msgs) == 4, f"{len(msgs)}개")
check("다시 열기: 재질문 메시지에 선택지", bool((msgs[1].get("metadata") or {}).get("clarify")))

ev3 = stream({"namespace": NS, "question": "주문 정책 전부 보여줘", "conversation_id": conv}, tok)
check("전체를 명시하면 안 물음", not any(e.get("type") == "clarify" for e in ev3))
print(f"{sum(res)}/{len(res)} PASS · 대화 {conv}")
