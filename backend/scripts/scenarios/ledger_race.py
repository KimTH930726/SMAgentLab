"""M2: 같은 원본(웹 URL)의 서로 다른 초안 두 개를 동시에 확정 → 끝나면 사용 중 원문 1개, 그 조각만 살아 있어야 한다.
임시 파트에서 하고 지운다. 3회 반복."""
import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import _lock  # noqa: E402
_lock.hold(__file__)   # 실서버 시나리오는 한 번에 하나만

import json
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "http://localhost:8000"
NS = "zz원장동시" + time.strftime("%H%M%S")


def call(method, path, body=None, tok=None):
    req = urllib.request.Request(API + urllib.parse.quote(path, safe="/?=&"), method=method,
                                 data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {tok}"} if tok else {})})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def sql(q):
    return subprocess.run(["docker", "exec", "ops-postgres", "psql", "-U", "ops", "-d", "opsdb", "-tA", "-c", q],
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


def wait(job_id):
    for _ in range(120):
        _, j = call("GET", f"/api/knowledge/ingestion-jobs/{job_id}", tok=tok)
        if j and j["status"] not in ("processing", "pending"):
            return j["status"]
        time.sleep(0.5)
    return "timeout"


tok = call("POST", "/api/auth/login", {"username": "admin", "password": "1111"})[1]["access_token"]
assert call("POST", "/api/namespaces", {"name": NS, "description": "원장 동시 확정 시나리오(자동 삭제)"}, tok)[0] == 200
ok = 0
try:
    call("POST", f"/api/namespaces/{NS}/categories", {"name": "시나리오"}, tok)
    ns_id = sql(f"SELECT id FROM ops_namespace WHERE name = '{NS}'")
    for rnd in range(3):
        url = f"http://race.example/{rnd}"
        ids = []
        for v in ("가", "나"):
            ids.append(sql(f"INSERT INTO rag_source_document (namespace_id, source_type, source_ref, title, raw_format, raw_content, "
                           f"content_hash, chunk_strategy, parser_version, status) VALUES ({ns_id}, 'web', '{url}', 't', 'html', "
                           f"'<p>{v}{rnd}</p>', md5('{v}{rnd}'), 'section', 'v2.130', 'draft') RETURNING id").splitlines()[0])
        jobs = {}

        def go(d, v):
            items = [{"content": f"동시 확정 {v} 판 {rnd} 조각 {i} 입니다 충분히 길게 씁니다 {time.time()}", "category": "시나리오",
                      "source_document_id": int(d)} for i in range(2)]
            s, r = call("POST", "/api/knowledge/bulk", {"namespace": NS, "items": items, "source_file": url, "source_type": "web",
                                                        "confirm_replace": True}, tok)
            jobs[d] = (s, r)
        th = [threading.Thread(target=go, args=(d, v)) for d, v in zip(ids, ("가", "나"))]
        [t.start() for t in th]
        [t.join() for t in th]
        st = [wait(jobs[d][1]["job_id"]) for d in ids]
        docs = sql(f"SELECT string_agg(id || ':' || status, ',' ORDER BY id) FROM rag_source_document WHERE id IN ({','.join(ids)})")
        active_docs = sql(f"SELECT count(*) FROM rag_source_document WHERE id IN ({','.join(ids)}) AND status = 'active'")
        live = sql(f"SELECT string_agg(DISTINCT source_document_id::text, ',') FROM rag_knowledge WHERE namespace_id = {ns_id} "
                   f"AND source_document_id IN ({','.join(ids)}) AND status IN ('active','pending_review')")
        errs = [sql(f"SELECT coalesce(error_message, '') FROM rag_ingestion_job WHERE id = {jobs[d][1]['job_id']}") for d in ids]
        # 겹치면 하나는 "먼저 확정됨"으로 실패(사유가 등록 이력에 남음), 안 겹치면 둘 다 완료(뒤가 앞을 교체)
        total = sql(f"SELECT count(*) FROM rag_knowledge WHERE namespace_id = {ns_id} AND source_file = '{url}' AND status IN ('active','pending_review')")
        print("   이 원본의 살아있는 조각 전체 =", total)
        good = total == "2" and (sorted(st) == ["completed", "failed"] and any("먼저 확정" in e for e in errs) or st == ["completed", "completed"]) and active_docs == "1" and live and "," not in live \
            and sql(f"SELECT status FROM rag_source_document WHERE id = {live}") == "active"
        ok += good
        print("PASS" if good else "FAIL", rnd, st, docs, "살아있는 조각의 원문 =", live, [e[:40] for e in errs])
finally:
    s, _ = call("DELETE", f"/api/namespaces/{NS}", tok=tok)
    print("cleanup", s, sql(f"SELECT count(*) FROM ops_namespace WHERE name = '{NS}'"))
print(f"{ok}/3 PASS")
