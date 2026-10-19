"""T2 재확인 — 대화 A의 후속 "수신자는요?" 답이 대화 B의 같은 후속 질문에 재사용되지 않는지 + 단독 질문은 여전히 캐시되는지."""
import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import _lock  # noqa: E402
_lock.hold(__file__)   # 실서버 시나리오는 한 번에 하나만

import json
import urllib.parse
import urllib.request

API, NS = "http://localhost:8000", "온라인스토어 DB"


def _req(method, path, body=None, tok=None):
    return urllib.request.Request(API + path, method=method, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
                                  headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {tok}"} if tok else {})})


TOK = json.load(urllib.request.urlopen(_req("POST", "/api/auth/login", {"username": "admin", "password": "1111"})))["access_token"]


def ask(q, conv=None):
    ev = []
    for raw in urllib.request.urlopen(_req("POST", "/api/chat/stream", {"namespace": NS, "question": q, "conversation_id": conv}, TOK), timeout=300):
        line = raw.decode("utf-8", "ignore").strip()
        if line.startswith("data:"):
            ev.append(json.loads(line[5:].strip()))
    conv = next((e["conversation_id"] for e in ev if e.get("type") == "meta" and e.get("conversation_id")), conv)
    ans = "".join(e.get("data", "") for e in ev if e.get("type") == "token")
    cached = not any(e.get("type") == "status" and e.get("step") == "llm" for e in ev)
    return conv, ans, cached


urllib.request.urlopen(_req("DELETE", "/api/admin/cache?namespace=" + urllib.parse.quote(NS), tok=TOK))
a, _, _ = ask("선물하기 교환 정책 알려줘")
a, fa, _ = ask("수신자는요?", a)
b, _, _ = ask("리워드 텀블러 쿠폰 지급 정책 알려줘")
b, fb, cached = ask("수신자는요?", b)
print("후속 질문 캐시 재사용:", cached, "· A와 같은 답:", fa == fb)
print("B 후속 답에 텀블러/쿠폰 언급:", ("텀블러" in fb) or ("쿠폰" in fb))
_, s1, c1 = ask("SR카드로 결제할 때 얼마 이상 써야 혜택을 받을 수 있나요?")
_, s2, c2 = ask("SR카드로 결제할 때 얼마 이상 써야 혜택을 받을 수 있나요?")
print("단독 질문 두 번째는 캐시:", c2, "(첫 번째:", c1, ")")
