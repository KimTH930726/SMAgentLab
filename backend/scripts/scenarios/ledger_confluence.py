"""원장 컨플루언스 실경로 — 실제 페이지를 임시 파트에 등록 → 원장 저장·연결 → 다시 분할 no-op → 재등록 확인(A안) → 정리.
본문은 출력하지 않는다(사내 민감 문서 — 개수·상태·길이 같은 구조값만)."""
import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import _lock  # noqa: E402
_lock.hold(__file__)   # 실서버 시나리오는 한 번에 하나만

import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "http://localhost:8000"
URL = sys.argv[1]
NS = "zz원장컨플" + time.strftime("%H%M%S")
CAT = "시나리오"


def call(method, path, body=None, tok=None):
    req = urllib.request.Request(API + urllib.parse.quote(path, safe="/?=&"), method=method,
                                 data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {tok}"} if tok else {})})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        body = e.read() or b"null"
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"detail": f"(본문 {len(body)}바이트)"}


def sql(q):
    return subprocess.run(["docker", "exec", "ops-postgres", "psql", "-U", "ops", "-d", "opsdb", "-tA", "-c", q],
                          capture_output=True, text=True, encoding="utf-8").stdout.strip()


results = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    results.append(bool(cond))


def wait(job_id):
    for _ in range(300):
        _, j = call("GET", f"/api/knowledge/ingestion-jobs/{job_id}", tok=tok)
        if j and j["status"] not in ("processing", "pending"):
            return j["status"], (j.get("error_message") or "")[:120]
        time.sleep(1)
    return "timeout", ""


def preview():
    s, r = call("POST", "/api/knowledge/import/url/preview", {"namespace": NS, "url": URL}, tok)
    assert s == 200, (s, (r or {}).get("detail", "")[:200] if isinstance(r, dict) else s)
    return r


def items_of(p):
    return [{"content": c["text"], "category": CAT, "heading_path": c["heading_path"], "source_document_id": p["source_document_id"],
             "confluence_page_id": p["confluence_page_id"], "confluence_version": p["confluence_version"]} for c in p["chunks"]]


def register(p, confirm=False):
    return call("POST", "/api/knowledge/bulk", {"namespace": NS, "items": items_of(p), "source_file": p["source_name"],
                                                "source_type": "confluence", "confirm_replace": confirm}, tok)


tok = call("POST", "/api/auth/login", {"username": "admin", "password": "1111"})[1]["access_token"]
assert call("POST", "/api/namespaces", {"name": NS, "description": "원장 컨플루언스 실경로 시나리오(자동 삭제)"}, tok)[0] == 200
try:
    call("POST", f"/api/namespaces/{NS}/categories", {"name": CAT}, tok)
    ns_id = sql(f"SELECT id FROM ops_namespace WHERE name = '{NS}'")

    p1 = preview()
    d1 = p1["source_document_id"]
    meta = sql(f"SELECT source_type || '|' || raw_format || '|' || (source_ref IS NOT NULL) || '|' || length(raw_content) || '|' || "
               f"coalesce(source_version::text,'-') || '|' || chunk_strategy || '|' || status FROM rag_source_document WHERE id = {d1}")
    print("   미리보기: 조각", p1["chunk_count"], "· 전략", p1["detected_strategy"], "· 원장", meta)
    st, _, _, rawlen, ver, _, dstatus = meta.split("|")
    check("원장에 컨플루언스 storage HTML 원문 초안", st == "confluence" and meta.split("|")[1] == "html" and int(rawlen) > 0 and dstatus == "draft")
    check("원본 페이지 ID = 원장 위치", sql(f"SELECT source_ref FROM rag_source_document WHERE id = {d1}") == str(p1["confluence_page_id"]))

    s, r = register(p1)
    stt = wait(r["job_id"]) if s == 201 else (str(s), str(r)[:120])
    check("등록 완료", stt[0] == "completed", str(stt))
    n = sql(f"SELECT count(*) FROM rag_knowledge WHERE source_document_id = {d1} AND status IN ('active','pending_review')")
    total = sql(f"SELECT count(*) FROM rag_knowledge WHERE namespace_id = {ns_id} AND status IN ('active','pending_review')")
    check("조각 전부 원문에 연결(유실·중복 0)", int(n) == p1["chunk_count"] and total == n, f"연결 {n} / 파트 전체 {total} / 미리보기 {p1['chunk_count']}")
    check("조각마다 페이지 ID·상위 경로", sql(f"SELECT count(*) FROM rag_knowledge WHERE source_document_id = {d1} AND "
                                         f"(confluence_page_id IS NULL OR heading_path IS NULL)") == "0")
    check("원문 사용 중", sql(f"SELECT status FROM rag_source_document WHERE id = {d1}") == "active")

    s, r = call("POST", f"/api/knowledge/source-documents/{d1}/rechunk", {}, tok)
    check("다시 분할 = 등록 결과(no-op)", s == 200 and r.get("changed") is False, f"{s} old={r.get('old')} new={r.get('new')}")

    p2 = preview()
    s, c = call("POST", "/api/knowledge/bulk/replace-check", {"namespace": NS, "source_document_ids": [p2["source_document_id"]],
                                                              "confluence_page_ids": [str(p2["confluence_page_id"])]}, tok)
    check("손대지 않은 페이지 재등록 — 확인 불필요", s == 200 and c["needs_confirm"] is False, str({k: c[k] for k in ("edited", "removed", "partial", "mixed_category")}))

    kid = sql(f"SELECT min(id) FROM rag_knowledge WHERE source_document_id = {d1} AND status = 'active'")
    old = sql(f"SELECT content FROM rag_knowledge WHERE id = {kid}")
    s, _ = call("PUT", f"/api/knowledge/{kid}", {"content": old + "\n(시나리오 수정 표시)"}, tok)
    s, c = call("POST", "/api/knowledge/bulk/replace-check", {"namespace": NS, "source_document_ids": [p2["source_document_id"]],
                                                              "confluence_page_ids": [str(p2["confluence_page_id"])]}, tok)
    check("고친 조각 1 → 확인 필요", c["needs_confirm"] and c["edited"] == 1, str({k: c[k] for k in ("edited", "removed")}))
    before = sql(f"SELECT count(*) FROM rag_knowledge WHERE namespace_id = {ns_id} AND status IN ('active','pending_review')")
    s, r = register(p2)
    check("확인 없이 409 + 그대로", s == 409 and sql(f"SELECT count(*) FROM rag_knowledge WHERE namespace_id = {ns_id} AND status IN ('active','pending_review')") == before, str(s))
    s, r = register(p2, confirm=True)
    stt = wait(r["job_id"]) if s == 201 else (str(s), str(r)[:120])
    check("확인 후 교체 완료", stt[0] == "completed", str(stt))
    d2 = p2["source_document_id"]
    live = sql(f"SELECT count(*) FROM rag_knowledge WHERE namespace_id = {ns_id} AND status IN ('active','pending_review')")
    check("교체 뒤 살아있는 조각 = 새 미리보기 수(옛 조각 0)", int(live) == p2["chunk_count"] and
          sql(f"SELECT count(*) FROM rag_knowledge WHERE source_document_id = {d1} AND status IN ('active','pending_review')") == "0",
          f"{live} / {p2['chunk_count']}")
    check("원문1 교체됨·원문2 사용 중",
          sql(f"SELECT string_agg(status, ',' ORDER BY id) FROM rag_source_document WHERE id IN ({d1},{d2})") ==
          ("superseded,active" if d1 != d2 else "active"))
finally:
    s, _ = call("DELETE", f"/api/namespaces/{NS}", tok=tok)
    print("cleanup", s, "남은 파트 =", sql(f"SELECT count(*) FROM ops_namespace WHERE name = '{NS}'"))
print(f"{sum(results)}/{len(results)} PASS")
