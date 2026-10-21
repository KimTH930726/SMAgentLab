"""시나리오: v0 정책 정정 승인 → 버전 규칙(10/21, 정책 v0 재구축 1단계) — 실제 서버 HTTP + 실제 DB로 전/후 값 비교.

단위 테스트의 가짜 연결은 고유 인덱스(#80 "정책당 사용 중 1개")를 흉내 내지 않아, 정정 승인이 새 버전을 먼저 active로 넣다가
막히는 결함(리뷰 H1)을 못 잡았다. 그래서 진짜 Postgres·진짜 API로 한 번 돌린다. LLM 호출 없음(수정안을 직접 넣은 신고를 승인).
임시 영역(kind=policy_test — 채팅 범위에 안 섞임)만 만들고 끝나면 지운다.

실행(호스트): python backend/scripts/scenarios/policy_version_approve.py
"""
import json
import subprocess
import sys
import urllib.request

API = "http://localhost:8000/api"
NS = "E2E정책버전"
fails = 0


def sql(q: str) -> str:
    return subprocess.run(["docker", "exec", "ops-postgres", "psql", "-U", "ops", "-d", "opsdb", "-tA", "-c", q],
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()


def check(name: str, ok: bool) -> None:
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'} {name}")


def call(method: str, path: str, token: str | None = None, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def cleanup() -> None:
    sql(f"DELETE FROM ops_improvement_item WHERE namespace_id = (SELECT id FROM ops_namespace WHERE name = '{NS}')")
    sql(f"DELETE FROM ops_namespace WHERE name = '{NS}'")


cleanup()
sql(f"INSERT INTO ops_namespace (name, description, kind) VALUES ('{NS}', 'scenario', 'policy_test')")
ns_id = sql(f"SELECT id FROM ops_namespace WHERE name = '{NS}'")
item_id = sql(f"""INSERT INTO policy_item (namespace_id, category_path, policy_name, raw_body, content_hash, status, parse_status,
                  pipeline_version, source_key) VALUES ({ns_id}, ARRAY['시나리오'], '쿠폰 유효기간', '발급일로부터 14일 사용 가능',
                  md5('a'), 'active', 'parsed', 'v0_whole', 'pol_scenario_ver') RETURNING id""").splitlines()[0]
chunk_id = sql(f"""INSERT INTO policy_chunk (policy_item_id, chunk_text, embedding, chunk_idx)
                   SELECT {item_id}, '발급일로부터 14일 사용 가능', (SELECT embedding FROM policy_chunk WHERE embedding IS NOT NULL LIMIT 1), 0
                   RETURNING id""").splitlines()[0]
logical = sql(f"SELECT logical_id FROM policy_item WHERE id = {item_id}")
# 반려된 v2가 이미 있는 상태 — 예전 "원본+1" 규칙이면 새 버전 번호가 겹쳤다
sql(f"""INSERT INTO policy_item (namespace_id, category_path, policy_name, raw_body, content_hash, status, parse_status,
        pipeline_version, logical_id, version, supersedes_id) VALUES ({ns_id}, ARRAY['시나리오'], '쿠폰 유효기간', '반려된 수정',
        md5('b'), 'rejected', 'parsed', 'v0_whole', {logical}, 2, {item_id})""")
corr_id = sql(f"""INSERT INTO ops_improvement_item (kind, source, namespace_id, target_type, target_id, target_sub_id, original, proposed, user_input)
                  VALUES ('evidence_correction', 'chat', {ns_id}, 'policy_narrative', {item_id}, {chunk_id},
                  '{{"raw_body": "발급일로부터 14일 사용 가능", "chunk_text": "발급일로부터 14일 사용 가능"}}'::jsonb,
                  '{{"chunk_text": "발급일로부터 7일 사용 가능", "raw_body": "발급일로부터 7일 사용 가능"}}'::jsonb, '7일로 바뀜')
                  RETURNING id""").splitlines()[0]

before = sql(f"SELECT string_agg(version || ':' || status, ',' ORDER BY version) FROM policy_item WHERE logical_id = {logical}")
check(f"전: v1 사용 중 · v2 반려 ({before})", before == "1:active,2:rejected")

_, tok = call("POST", "/auth/login", body={"username": "admin", "password": "1111"})
token = tok.get("access_token")
proposed = json.loads(sql(f"SELECT proposed FROM ops_improvement_item WHERE id = {corr_id}"))
status, body = call("POST", f"/corrections/{corr_id}/approve", token, {"proposed": proposed})
check(f"정정 승인 200 (받은 {status} {str(body)[:120]})", status == 200)

after = sql(f"SELECT string_agg(version || ':' || status, ',' ORDER BY version) FROM policy_item WHERE logical_id = {logical}")
check(f"후: v1 내려감 · v2 반려 그대로 · v3 사용 중 ({after})", after == "1:deprecated,2:rejected,3:active")
check("사용 중은 정책당 1개", sql(f"SELECT count(*) FROM policy_item WHERE logical_id = {logical} AND status = 'active'") == "1")
new_body = sql(f"SELECT raw_body FROM policy_item WHERE logical_id = {logical} AND status = 'active'")
check("새 버전 원문 = 수정안", new_body == "발급일로부터 7일 사용 가능")
check("원천 키 유지", sql(f"SELECT source_key FROM policy_item WHERE logical_id = {logical} AND status = 'active'") == "pol_scenario_ver")
check("신고 상태 approved", sql(f"SELECT status FROM ops_improvement_item WHERE id = {corr_id}") == "approved")

# 검색 대상 규칙: v0 검토 대기는 검색에서 빠진다 — 대기 버전을 직접 만들어 확인(2단계 직접 수정이 만들 상태)
sql(f"""INSERT INTO policy_item (namespace_id, category_path, policy_name, raw_body, content_hash, status, parse_status,
        pipeline_version, logical_id, version) VALUES ({ns_id}, ARRAY['시나리오'], '쿠폰 유효기간', '대기 중인 수정',
        md5('c'), 'pending_review', 'parsed', 'v0_whole', {logical}, 4)""")
dup = subprocess.run(["docker", "exec", "ops-postgres", "psql", "-U", "ops", "-d", "opsdb", "-tA", "-c",
                      f"""INSERT INTO policy_item (namespace_id, category_path, policy_name, raw_body, content_hash, status, parse_status,
                          pipeline_version, logical_id, version) VALUES ({ns_id}, ARRAY['x'], 'x', 'x', md5('d'), 'pending_review',
                          'parsed', 'v0_whole', {logical}, 5)"""], capture_output=True, text=True, encoding="utf-8")
check("검토 대기는 정책당 1개(두 번째는 DB가 막음)", dup.returncode != 0 and "uq_policy_one_pending_v0" in dup.stderr)
visible = sql(f"""SELECT string_agg(raw_body, '|') FROM policy_item i WHERE i.logical_id = {logical}
                  AND (i.status = 'active' OR (i.status = 'pending_review' AND i.pipeline_version IS DISTINCT FROM 'v0_whole'))""")
check(f"검색 대상 = 사용 중 버전만, 대기 버전 제외 ({visible})", visible == "발급일로부터 7일 사용 가능")

cleanup()
check("정리: 임시 영역 삭제", sql(f"SELECT count(*) FROM ops_namespace WHERE name = '{NS}'") == "0")
print(f"\n{'모두 통과' if not fails else f'실패 {fails}건'}")
sys.exit(1 if fails else 0)
