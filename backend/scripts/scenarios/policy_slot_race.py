"""실서버 동시성 시나리오(10/21 리뷰 7) — "정책당 대기 정정 1건" 잠금과 승인 경쟁을 진짜 Postgres·진짜 API로 확인한다.

단위 테스트의 가짜 연결은 잠금(pg_advisory_xact_lock)·행 잠금(FOR UPDATE)·트랜잭션 격리를 흉내 내지 않아, "확인과 쓰기 사이에
다른 요청이 끼어드는" 경쟁은 실 DB로만 볼 수 있다(ledger_race.py와 같은 방식). 임시 영역(kind=policy_test — 채팅 범위에 안 섞임)에
v0 정책 1건을 만들고 끝나면 지운다.

  A. 정책 화면 직접 수정 제안 N개를 동시에 → 대기 정정 정확히 1건, 나머지는 409 + 그 1건 번호(existing_id)
  B. "답변 틀림" + 곧바로 한 줄 의견을 동시에(3회) → 5xx 없음, 한 줄 저장됨, 그 답변의 원장 건수 1(신고 1번 = 검토 1건),
     정책 대기 정정은 여전히 1건(AI 추정이 바쁜 정책을 고르면 대상을 비워 둔다)
     LLM은 부르지 않는다 — 이번 주 게이트웨이 토큰 0(project_gateway_token_limit_week)이라 실행 동안만 LLM을 없는 주소(127.0.0.1:9)로
     돌려 AI 판정이 즉시 실패하게 한다(AI가 실패해도 신고는 남는 경로). 화면에서 바꿔 둔 LLM 설정이 있으면 되돌릴 때 지워지므로
     그때는 B를 건너뛴다(llm-local.setup.ts와 같은 가드).
  C. 승인 vs [직접 수정](PATCH) 동시(3회) + 승인 두 번 동시(1회) → 한쪽만 200, 다른 쪽은 409(또는 상대가 이미 끝난 뒤 도착해
     "대기 중인 신고가 아닙니다" 400), 사용 중 버전은 늘 1개, 이긴 쪽 내용이 남는다.
     AI 다시 쓰기(redraft)와의 경쟁은 안 한다 — LLM을 끈 상태에선 다시 쓰기가 저장 전에 실패해 경쟁이 생기지 않는다.

실행(호스트): py backend/scripts/scenarios/policy_slot_race.py   — 실패가 있으면 종료 코드 1
"""
import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import _lock  # noqa: E402
_lock.hold(__file__)   # 실서버 시나리오는 한 번에 하나만

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "http://localhost:8000/api"
NS = "zz정책슬롯동시" + time.strftime("%H%M%S")
N = 8
DEAD_LLM = "http://127.0.0.1:9"
fails = 0


def sql(q: str) -> str:
    return subprocess.run(["docker", "exec", "ops-postgres", "psql", "-U", "ops", "-d", "opsdb", "-tA", "-c", q],
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()


def check(name: str, ok: bool) -> None:
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'} {name}")


def call(method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, object]:
    req = urllib.request.Request(API + urllib.parse.quote(path, safe="/?=&"), method=method,
                                 data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"null")
        except json.JSONDecodeError:
            return e.code, raw.decode("utf-8", "replace")[:200]


def together(*fns):
    """요청들을 같은 순간에 출발시킨다(Barrier) — 결과는 넣은 순서대로."""
    out = [None] * len(fns)
    gate = threading.Barrier(len(fns))

    def run(i, fn):
        gate.wait()
        out[i] = fn()
    th = [threading.Thread(target=run, args=(i, f)) for i, f in enumerate(fns)]
    [t.start() for t in th]
    [t.join() for t in th]
    return out


def detail_id(body) -> object:
    d = body.get("detail") if isinstance(body, dict) else None
    return d.get("existing_id") if isinstance(d, dict) else None


def detail_text(body) -> str:
    d = body.get("detail") if isinstance(body, dict) else body
    return str(d.get("message") if isinstance(d, dict) else d)


def active_item(logical: str) -> str:
    return sql(f"SELECT id FROM policy_item WHERE logical_id = {logical} AND status = 'active'")


def pending_on(logical: str) -> list[str]:
    """이 정책(모든 버전)을 겨눈 대기 정정 id — 잠금(open_correction_sql)과 같은 기준: 종류 무관, 정책 대상 종류."""
    rows = sql(f"SELECT o.id FROM ops_improvement_item o JOIN policy_item i ON i.id = o.target_id WHERE o.status = 'pending' "
               f"AND o.target_type IN ('policy_param', 'policy_narrative') AND i.logical_id = {logical} ORDER BY o.id")
    return rows.split() if rows else []


def propose(token: str, item_id: str, text: str) -> tuple[int, object]:
    return call("POST", "/corrections", token, {"namespace": NS, "target_type": "policy_narrative", "target_id": int(item_id),
                                                "source": "policy_direct", "proposed": {"raw_body": text, "remark": ""}})


def cleanup() -> None:
    sql(f"DELETE FROM ops_improvement_item WHERE namespace_id = (SELECT id FROM ops_namespace WHERE name = '{NS}')")
    sql(f"DELETE FROM ops_namespace WHERE name = '{NS}'")   # 정책·조각·대화는 ON DELETE CASCADE


_, login = call("POST", "/auth/login", body={"username": "admin", "password": "1111"})
token = login["access_token"]
admin_id = sql("SELECT id FROM ops_user WHERE username = 'admin'")
llm_before = None
try:
    sql(f"INSERT INTO ops_namespace (name, description, kind) VALUES ('{NS}', 'scenario policy_slot_race', 'policy_test')")
    ns_id = sql(f"SELECT id FROM ops_namespace WHERE name = '{NS}'")
    item_id = sql(f"""INSERT INTO policy_item (namespace_id, category_path, policy_name, raw_body, content_hash, status, parse_status,
                      pipeline_version, source_key) VALUES ({ns_id}, ARRAY['시나리오'], '동시 반품 기한', '반품은 7일 이내 신청',
                      md5('slot'), 'active', 'parsed', 'v0_whole', 'pol_scenario_slot') RETURNING id""").splitlines()[0]
    chunk_id = sql(f"""INSERT INTO policy_chunk (policy_item_id, chunk_text, embedding, chunk_idx)
                       SELECT {item_id}, '반품은 7일 이내 신청', (SELECT embedding FROM policy_chunk WHERE embedding IS NOT NULL LIMIT 1), 0
                       RETURNING id""").splitlines()[0]
    logical = sql(f"SELECT logical_id FROM policy_item WHERE id = {item_id}")

    # ── A. 정책 화면 제안 N개 동시 ──
    res = together(*[(lambda i=i: propose(token, item_id, f"반품은 {10 + i}일 이내 신청")) for i in range(N)])
    created = [b["id"] for s, b in res if s == 201]
    conflicts = [(s, b) for s, b in res if s == 409]
    check(f"A 동시 제안 {N}개 → 201 정확히 1개 (상태 {sorted(s for s, _ in res)})", len(created) == 1)
    check(f"A 나머지 {N - 1}개는 409", len(conflicts) == N - 1)
    check("A 409는 모두 그 1건 번호(existing_id)를 같이", bool(created) and all(detail_id(b) == created[0] for _, b in conflicts))
    check(f"A DB: 이 정책 대기 정정 1건 ({pending_on(logical)})", pending_on(logical) == [str(c) for c in created])
    first_pending = created[0] if created else None

    # ── B. "답변 틀림" + 곧바로 한 줄 ──
    _, cfg = call("GET", "/llm/config", token)
    if not isinstance(cfg, dict) or cfg.get("is_runtime_override"):
        print("SKIP B — 화면에서 바꿔 둔 LLM 설정이 있어 되돌리면 지워진다(LLM을 끄지 않고는 게이트웨이를 부르게 됨)")
    else:
        llm_before = cfg.get("provider")
        s, _ = call("PUT", "/llm/config", token, {"provider": "ollama", "ollama_base_url": DEAD_LLM})
        check(f"B 준비: LLM을 없는 주소로(토큰 0) — {s}", s == 200)
        conv = sql(f"INSERT INTO ops_conversation (title, user_id, namespace_id) VALUES ('slot race', {admin_id}, {ns_id}) RETURNING id").splitlines()[0]
        for rnd in range(3):
            sql(f"INSERT INTO ops_message (conversation_id, role, content) VALUES ({conv}, 'user', '반품 기한이 며칠이야 {rnd}')")
            cites = json.dumps({"policy_citations": [{"kind": "narrative", "item_id": int(active_item(logical)), "chunk_id": int(chunk_id)}]})
            msg = sql(f"INSERT INTO ops_message (conversation_id, role, content, results, metadata) VALUES ({conv}, 'assistant', "
                      f"'반품은 7일 이내 신청할 수 있어요 {rnd}', '[]'::jsonb, '{cites}'::jsonb) RETURNING id").splitlines()[0]
            line = f"기한이 14일로 바뀌었어요 {rnd}"
            fb, op = together(
                lambda: call("POST", "/feedback", token, {"namespace": NS, "question": "q", "is_positive": False, "message_id": int(msg)}),
                lambda: call("POST", "/corrections", token, {"namespace": NS, "target_type": "auto", "message_id": int(msg), "user_input": line}),
            )
            check(f"B{rnd} 5xx 없음 (답변 틀림 {fb[0]} · 한 줄 {op[0]} {detail_text(op[1])[:60] if op[0] >= 400 else ''})",
                  fb[0] < 500 and op[0] < 500)
            time.sleep(1.5)   # 백그라운드 추정(analyze_signal)이 끝날 시간 — LLM이 꺼져 있어 바로 실패한다
            stored = sql(f"SELECT count(*) FROM ops_improvement_item WHERE message_id = {msg} AND user_input = '{line}'")
            total = sql(f"SELECT count(*) FROM ops_improvement_item WHERE message_id = {msg}")
            check(f"B{rnd} 한 줄 저장됨 ({stored}건)", stored == "1")
            check(f"B{rnd} 그 답변의 원장 건수 1 — 신고 1번 = 검토 1건 ({total}건: "
                  f"{sql(f'SELECT string_agg(id || chr(58) || kind || chr(58) || target_type, chr(44)) FROM ops_improvement_item WHERE message_id = {msg}')})",
                  total == "1")
        check(f"B 후에도 이 정책 대기 정정 1건 ({pending_on(logical)})", pending_on(logical) == [str(first_pending)])

    # ── C. 승인 vs 직접 수정(PATCH), 승인 두 번 ──
    for rnd in range(4):
        pend = pending_on(logical)
        if not pend:
            s, b = propose(token, active_item(logical), f"반품은 {30 + rnd}일 이내 신청")
            if s != 201:
                check(f"C{rnd} 준비 제안 201 (받은 {s} {detail_text(b)[:80]})", False)
                break
            pend = [str(b["id"])]
        corr = pend[0]
        shown = json.loads(sql(f"SELECT proposed FROM ops_improvement_item WHERE id = {corr}"))
        before_active = active_item(logical)
        double = rnd == 3
        other_text = f"반품은 {50 + rnd}일 이내 신청"
        if double:
            a, p = together(lambda: call("POST", f"/corrections/{corr}/approve", token, {"proposed": shown}),
                            lambda: call("POST", f"/corrections/{corr}/approve", token, {"proposed": shown}))
            label = "승인 두 번"
        else:
            a, p = together(lambda: call("POST", f"/corrections/{corr}/approve", token, {"proposed": shown}),
                            lambda: call("PATCH", f"/corrections/{corr}/proposed", token, {"proposed": {"raw_body": other_text, "remark": ""}}))
            label = "승인 vs 직접 수정"
        codes = (a[0], p[0])
        wins = [r for r in (a, p) if r[0] == 200]
        loser = next((r for r in (a, p) if r[0] != 200), None)
        late = loser is not None and loser[0] == 400 and "대기 중인 신고가 아닙니다" in detail_text(loser[1])
        check(f"C{rnd} {label}: 한쪽만 200 (상태 {codes})", len(wins) == 1)
        check(f"C{rnd} 진 쪽은 409{' — 상대가 끝난 뒤 도착(400 대기 아님)' if late else ''} "
              f"({loser[0] if loser else '-'} {detail_text(loser[1])[:60] if loser else ''})",
              loser is not None and (loser[0] == 409 or late))
        actives = sql(f"SELECT count(*) FROM policy_item WHERE logical_id = {logical} AND status = 'active'")
        check(f"C{rnd} 사용 중 버전 1개 ({actives})", actives == "1")
        status = sql(f"SELECT status FROM ops_improvement_item WHERE id = {corr}")
        now_raw = sql(f"SELECT raw_body FROM policy_item WHERE logical_id = {logical} AND status = 'active'")
        if a[0] == 200:   # 승인이 이김 → 화면에 보인 안이 새 버전, 정정은 approved
            check(f"C{rnd} 승인이 이김 → 새 버전 = 보인 안, 정정 approved ({now_raw} · {status})",
                  now_raw == shown["raw_body"] and status == "approved" and active_item(logical) != before_active)
        elif not double:  # 직접 수정이 이김 → 정책 그대로, 정정은 대기 + 고친 안
            kept = json.loads(sql(f"SELECT proposed FROM ops_improvement_item WHERE id = {corr}"))
            check(f"C{rnd} 직접 수정이 이김 → 정책 그대로, 정정 대기 + 고친 안 ({status} · {kept.get('raw_body')})",
                  active_item(logical) == before_active and status == "pending" and kept.get("raw_body") == other_text)
    versions = sql(f"SELECT string_agg(version || ':' || status, ',' ORDER BY version) FROM policy_item WHERE logical_id = {logical}")
    print(f"   버전 이력: {versions}")
finally:
    if llm_before is not None:
        # 시작 전엔 화면 설정이 없었음을 확인했으므로 늘 해제(DELETE)로 되돌린다 — openai_compat로 다시 PUT하면 화면 설정이 새로 남았다(리뷰 10/21)
        s, _ = call("DELETE", "/llm/config", token)
        _, after = call("GET", "/llm/config", token)
        check(f"정리: LLM 설정 원래대로 ({llm_before} → {after.get('provider') if isinstance(after, dict) else after})",
              isinstance(after, dict) and after.get("provider") == llm_before and after.get("is_runtime_override") is False)
    cleanup()
    check("정리: 임시 영역 삭제", sql(f"SELECT count(*) FROM ops_namespace WHERE name = '{NS}'") == "0")
print(f"\n{'모두 통과' if not fails else f'실패 {fails}건'}")
sys.exit(1 if fails else 0)
