"""컬렉션 키 전환 1단계 — 입구 미들웨어가 업무 코드·id를 이름으로 바꾼다(core/collection_ref.py).

성질: ① 이름은 DB를 안 보고 그대로 ② 코드·id는 이름으로(쿼리·경로·JSON 본문) ③ 없는 값은 그대로 ④ 본문 길이 헤더가 바뀐 본문과 맞다
⑤ 파일 올리기·목록 본문은 안 건드림 ⑥ 본문을 읽은 뒤 receive는 원래 것으로(스트리밍 끊김 감지).
"""
import asyncio
import importlib.util
import json
from pathlib import Path

# conftest가 core 패키지를 가짜로 바꿔 둔다 — 이 모듈은 표준 라이브러리만 써서 파일에서 직접 읽는다
_spec = importlib.util.spec_from_file_location("_collection_ref", Path(__file__).resolve().parent.parent / "core" / "collection_ref.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
CollectionRefMiddleware, looks_like_ref = _mod.CollectionRefMiddleware, _mod.looks_like_ref

NAMES = {"SYS-HP": "홈페이지 시스템", "2568": "홈페이지 시스템", "POL-MBR": "회원"}


def _run(scope, body=b"", resolve_calls=None, more_msgs=None):
    seen = {}
    calls = resolve_calls if resolve_calls is not None else []

    async def resolve(ref):
        calls.append(ref)
        return NAMES.get(ref)

    async def app(scope, receive, send):
        seen["scope"] = scope
        msgs = []
        while True:
            m = await receive()
            msgs.append(m)
            if m["type"] != "http.request" or not m.get("more_body"):
                break
        seen["body"] = b"".join(m.get("body", b"") for m in msgs)
        seen["after"] = await receive()   # 본문 뒤 receive — 원래 것(끊김)

    queue = [{"type": "http.request", "body": body, "more_body": False}] + (more_msgs or [{"type": "http.disconnect"}])

    async def receive():
        return queue.pop(0)

    async def send(_):
        pass

    base = {"type": "http", "method": "GET", "path": "/api/x", "query_string": b"", "headers": []}
    base.update(scope)
    asyncio.run(CollectionRefMiddleware(app, resolve=resolve)(base, receive, send))
    return seen, calls


def test_looks_like_ref():
    assert looks_like_ref("SYS-HP") and looks_like_ref("POL-CARD") and looks_like_ref("2568")
    assert not looks_like_ref("홈페이지 시스템") and not looks_like_ref("sys-hp") and not looks_like_ref("SYS-H")
    assert not looks_like_ref(None) and not looks_like_ref(2568) and not looks_like_ref("")


def test_name_passes_without_lookup():
    from urllib.parse import quote
    seen, calls = _run({"path": "/api/namespaces/" + quote("회원") + "/supports", "query_string": ("namespace=" + quote("회원")).encode()})
    assert calls == []
    assert seen["scope"]["query_string"] == ("namespace=" + quote("회원")).encode()


def test_query_and_path_resolved():
    from urllib.parse import parse_qs
    seen, _ = _run({"path": "/api/namespaces/SYS-HP/supports", "query_string": b"namespace=POL-MBR&status=active"})
    assert seen["scope"]["path"] == "/api/namespaces/홈페이지 시스템/supports"
    assert seen["scope"]["raw_path"].decode() == "/api/namespaces/%ED%99%88%ED%8E%98%EC%9D%B4%EC%A7%80%20%EC%8B%9C%EC%8A%A4%ED%85%9C/supports"
    assert parse_qs(seen["scope"]["query_string"].decode()) == {"namespace": ["회원"], "status": ["active"]}
    seen, _ = _run({"path": "/api/stats/namespace/2568"})
    assert seen["scope"]["path"] == "/api/stats/namespace/홈페이지 시스템"


def test_unknown_ref_untouched():
    seen, calls = _run({"path": "/api/namespaces/SYS-ZZ", "query_string": b"namespace=99999"})
    assert calls == ["SYS-ZZ", "99999"]
    assert seen["scope"]["path"] == "/api/namespaces/SYS-ZZ" and seen["scope"]["query_string"] == b"namespace=99999"


def test_json_body_resolved_with_length():
    body = json.dumps({"namespace": "SYS-HP", "preset_namespace": "2568", "question": "SYS-HP 로그인"}).encode()
    seen, _ = _run({"method": "POST", "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]}, body)
    data = json.loads(seen["body"])
    assert data == {"namespace": "홈페이지 시스템", "preset_namespace": "홈페이지 시스템", "question": "SYS-HP 로그인"}   # 다른 칸은 그대로
    assert dict(seen["scope"]["headers"])[b"content-length"] == str(len(seen["body"])).encode()
    assert seen["after"] == {"type": "http.disconnect"}


def test_chunked_body():
    body = json.dumps({"namespace": "POL-MBR", "x": 1}).encode()
    seen = {}

    async def app(scope, receive, send):
        m = await receive()
        seen["body"], seen["more"] = m["body"], m["more_body"]

    queue = [{"type": "http.request", "body": body[:7], "more_body": True}, {"type": "http.request", "body": body[7:], "more_body": False}]

    async def receive():
        return queue.pop(0)

    async def resolve(ref):
        return NAMES.get(ref)

    scope = {"type": "http", "method": "POST", "path": "/api/chat", "query_string": b"", "headers": [(b"content-type", b"application/json")]}
    asyncio.run(CollectionRefMiddleware(app, resolve=resolve)(scope, receive, lambda _: None))
    assert json.loads(seen["body"]) == {"namespace": "회원", "x": 1} and seen["more"] is False


def test_untouched_bodies():
    for headers, body in (([(b"content-type", b"multipart/form-data; boundary=x")], b"--x\r\nnamespace=SYS-HP"),
                          ([(b"content-type", b"application/json")], b'["SYS-HP"]'),
                          ([(b"content-type", b"application/json")], b"{not json"),
                          ([(b"content-type", b"application/json")], b'{"namespace": "\xed\x99\x88"}')):
        seen, calls = _run({"method": "POST", "headers": headers}, body)
        assert seen["body"] == body and calls == []


def test_non_api_and_resolver_error():
    seen, calls = _run({"path": "/docs", "query_string": b"namespace=SYS-HP"})
    assert calls == [] and seen["scope"]["query_string"] == b"namespace=SYS-HP"

    async def boom(_):
        raise RuntimeError("db down")

    out = {}

    async def app(scope, receive, send):
        out["qs"] = scope["query_string"]

    async def receive():
        return {"type": "http.request", "body": b""}

    scope = {"type": "http", "method": "GET", "path": "/api/x", "query_string": b"namespace=SYS-HP", "headers": []}
    asyncio.run(CollectionRefMiddleware(app, resolve=boom)(scope, receive, lambda _: None))
    assert out["qs"] == b"namespace=SYS-HP"   # 풀기 실패 — 요청은 그대로(로그만)
