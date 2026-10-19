"""A안(재등록 확인) + 2차 리뷰 실서버 시나리오 — 임시 파트를 만들어 쓰고 끝나면 지운다. 전/후 값 비교."""
import sys as _sys
from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parent))
import _lock  # noqa: E402
_lock.hold(__file__)   # 실서버 시나리오는 한 번에 하나만

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

API = "http://localhost:8000"
NS = "zz원장시나리오" + time.strftime("%H%M%S")
CAT = "시나리오"
TEXT = ("1.1. 개요\n원장 A안 시나리오 첫째 문장입니다 충분히 길게 씁니다.\n"
        "1.2. 처리\n원장 A안 시나리오 둘째 문장입니다 충분히 길게 씁니다.\n"
        "1.3. 예외\n원장 A안 시나리오 셋째 문장입니다 충분히 길게 씁니다.")


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


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    results.append(bool(cond))


def wait(job_id):
    for _ in range(120):
        _, j = call("GET", f"/api/knowledge/ingestion-jobs/{job_id}", tok=tok)
        if j and j["status"] not in ("processing", "pending"):
            return j["status"]
        time.sleep(1)
    return "timeout"


def preview(strategy="section"):
    s, r = call("POST", "/api/knowledge/import/text-split/preview", {"raw_text": TEXT, "strategy": strategy, "namespace": NS}, tok)
    assert s == 200, (s, r)
    return r


def items_of(r):
    return [{"content": c, "category": CAT, "heading_path": hp, "source_document_id": r["source_document_id"]}
            for c, hp in zip(r["chunks"], r["heading_paths"])]


def doc_chunks(doc_id, st="'active','pending_review'"):
    return sql(f"SELECT count(*) FROM rag_knowledge WHERE source_document_id = {doc_id} AND status IN ({st})")


results = []
tok = call("POST", "/api/auth/login", {"username": "admin", "password": "1111"})[1]["access_token"]
s, _ = call("POST", "/api/namespaces", {"name": NS, "description": "원장 A안 시나리오(자동 삭제)"}, tok)
assert s == 200, s
try:
    call("POST", f"/api/namespaces/{NS}/categories", {"name": CAT}, tok)
    ns_id = sql(f"SELECT id FROM ops_namespace WHERE name = '{NS}'")

    # 1) 처음 등록
    p1 = preview(); d1 = p1["source_document_id"]
    s, r = call("POST", "/api/knowledge/bulk", {"namespace": NS, "items": items_of(p1), "source_file": "t", "source_type": "paste_split"}, tok)
    check("처음 등록 접수", s == 201 and r.get("unlinked_sources") == [], f"{s} {r}")
    check("처음 등록 완료", wait(r["job_id"]) == "completed")
    n1 = doc_chunks(d1)
    check("원문1 사용 중 + 조각 연결", sql(f"SELECT status FROM rag_source_document WHERE id = {d1}") == "active" and int(n1) == len(p1["chunks"]), n1)

    # 2) 손대지 않은 같은 원본 재등록 확인 → 묻지 않음
    p2 = preview(); d2 = p2["source_document_id"]
    check("같은 내용 재미리보기 = 새 초안(사용 중 원문 재사용 안 함)", d2 != d1, f"{d1} {d2}")
    s, c = call("POST", "/api/knowledge/bulk/replace-check", {"namespace": NS, "source_document_ids": [d2]}, tok)
    check("흔적 없으면 확인 불필요", s == 200 and c["needs_confirm"] is False, c)

    # 3) 조각 1 수정 + 조각 2 삭제 → 확인 필요
    ids = sql(f"SELECT string_agg(id::text, ',' ORDER BY id) FROM rag_knowledge WHERE source_document_id = {d1} AND status = 'active'").split(",")
    s, _ = call("PUT", f"/api/knowledge/{ids[0]}", {"content": "사람이 고친 첫째 조각 내용입니다."}, tok)
    check("조각 수정", s == 200, s)
    s, _ = call("DELETE", f"/api/knowledge/{ids[1]}", tok=tok)
    check("조각 삭제", s == 204, s)
    s, c = call("POST", "/api/knowledge/bulk/replace-check", {"namespace": NS, "source_document_ids": [d2]}, tok)
    check("확인 필요 — 고친 1·뺀 1", c["needs_confirm"] and c["edited"] == 1 and c["removed"] == 1 and not c["partial"], c)

    # 4) 확인 없이 등록 → 409, 아무것도 안 바뀜(전/후)
    before = (doc_chunks(d1), sql(f"SELECT status FROM rag_source_document WHERE id IN ({d1},{d2}) ORDER BY id"),
              sql(f"SELECT count(*) FROM rag_ingestion_job WHERE namespace_id = {ns_id}"))
    s, r = call("POST", "/api/knowledge/bulk", {"namespace": NS, "items": items_of(p2), "source_file": "t", "source_type": "paste_split"}, tok)
    after = (doc_chunks(d1), sql(f"SELECT status FROM rag_source_document WHERE id IN ({d1},{d2}) ORDER BY id"),
             sql(f"SELECT count(*) FROM rag_ingestion_job WHERE namespace_id = {ns_id}"))
    check("확인 없는 교체 409", s == 409 and "고친 조각 1건" in str(r), f"{s} {r}")
    check("409면 조각·원문·작업 그대로", before == after, f"{before} → {after}")

    # 5) 확인하고 등록 → 옛 조각 폐기, 원문1 교체됨, 원문2 사용 중
    s, r = call("POST", "/api/knowledge/bulk", {"namespace": NS, "items": items_of(p2), "source_file": "t", "source_type": "paste_split",
                                                "confirm_replace": True}, tok)
    check("확인 후 교체 등록 완료", s == 201 and wait(r["job_id"]) == "completed", f"{s} {r}")
    check("옛 조각 남은 것 0", doc_chunks(d1) == "0", doc_chunks(d1))
    check("원문1 교체됨·원문2 사용 중", sql(f"SELECT string_agg(status, ',' ORDER BY id) FROM rag_source_document WHERE id IN ({d1},{d2})") == "superseded,active")
    check("새 조각 = 미리보기 수", int(doc_chunks(d2)) == len(p2["chunks"]), doc_chunks(d2))
    s, c = call("POST", "/api/knowledge/bulk/replace-check", {"namespace": NS, "source_document_ids": [preview()["source_document_id"]]}, tok)
    check("교체 뒤엔 옛 삭제 이력이 다시 걸리지 않음(L2)", c["needs_confirm"] is False, c)

    # 6) 다시 분할 — 바뀐 것 없음
    s, r = call("POST", f"/api/knowledge/source-documents/{d2}/rechunk", {}, tok)
    check("다시 분할 no-op", s == 200 and r["changed"] is False, f"{s} {r}")

    # 7) L6: 폐기된 옛 조각 수정 거부
    s, r = call("PUT", f"/api/knowledge/{ids[2]}", {"content": "폐기된 조각을 고쳐 봄"}, tok)
    check("폐기 조각 수정 400", s == 400, f"{s} {r}")
    check("폐기 조각 내용·표시 그대로", sql(f"SELECT content_edited_at IS NULL FROM rag_knowledge WHERE id = {ids[2]}") == "t")

    # 8) M3: 분할 방식이 다르면 다른 초안
    a, b = preview("section")["source_document_id"], preview("paragraph")["source_document_id"]
    check("분할 방식별 초안 분리(M3)", a != b and sql(f"SELECT string_agg(chunk_strategy, ',' ORDER BY id) FROM rag_source_document WHERE id IN ({a},{b})") == "section,paragraph")

    # 9) L1: 미리보기 뒤 교체된 원문 → 등록은 되지만 연결 안 함 + 응답에 알림
    p9 = preview("paragraph"); d9 = p9["source_document_id"]
    sql(f"UPDATE rag_source_document SET status = 'superseded' WHERE id = {d9}")
    s, r = call("POST", "/api/knowledge/bulk", {"namespace": NS, "items": items_of(p9), "source_file": "t9", "source_type": "paste_split",
                                                "confirm_replace": True}, tok)
    check("연결 못 한 원문 응답에 알림(L1)", s == 201 and r.get("unlinked_sources") == [d9], f"{s} {r}")
    check("그 등록 완료", wait(r["job_id"]) == "completed")
    check("교체된 원문 되살아나지 않음", sql(f"SELECT status FROM rag_source_document WHERE id = {d9}") == "superseded"
          and doc_chunks(d9) == "0")
finally:
    s, _ = call("DELETE", f"/api/namespaces/{NS}", tok=tok)
    left = sql(f"SELECT (SELECT count(*) FROM ops_namespace WHERE name = '{NS}') || ',' || "
               f"(SELECT count(*) FROM rag_source_document d WHERE NOT EXISTS (SELECT 1 FROM ops_namespace n WHERE n.id = d.namespace_id))")
    print("cleanup", s, "남은 파트,고아 원문 =", left)
print(f"{sum(results)}/{len(results)} PASS")
