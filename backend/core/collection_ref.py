"""컬렉션 키 전환 1단계(docs/domains/knowledge/collection-key-plan.md §3-1) — API가 컬렉션을 이름·업무 코드·id로 모두 받는다.

라우터 수백 곳이 이름(`namespace: str`)으로 받으므로, 요청 입구 한 곳(ASGI 미들웨어)에서 코드(POL-MBR)·숫자 id를 이름으로 바꿔 넘긴다.
이름은 DB를 보지 않고 그대로 통과(지금 화면·스크립트는 이름을 보낸다). 없는 코드·id도 그대로 — 뒤에서 "없는 컬렉션"으로 걸린다.
같은 글자의 이름이 있으면 이름이 이긴다(옛 값 그대로 동작). 3~4단계(캐시·골든셋을 id·코드로)가 끝나면 이 층을 걷어낸다.

바꾸는 곳: 쿼리 `namespace`·`preset_namespace` · 경로 `/api/namespaces/{x}`·`/api/stats/namespace/{x}` · JSON 본문 최상위의 같은 키.
안 바꾸는 곳: 파일 올리기(multipart) — 이름으로 보낸다.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Awaitable, Callable, Optional
from urllib.parse import parse_qsl, quote, unquote, urlencode

logger = logging.getLogger(__name__)

CODE_RE = re.compile(r"^(POL|SYS)-[A-Z]{2,5}$")
KEYS = ("namespace", "preset_namespace")
PATH_PREFIXES = ("/api/namespaces/", "/api/stats/namespace/")
MAX_BODY = 1_000_000   # 이보다 큰 JSON 본문은 보지 않는다(컬렉션 키가 든 요청은 작다)

Resolver = Callable[[str], Awaitable[Optional[str]]]


def looks_like_ref(v: object) -> bool:
    return isinstance(v, str) and bool(CODE_RE.match(v) or (v.isdigit() and len(v) < 10))


async def resolve_collection(ref: str) -> Optional[str]:
    """이름·업무 코드·id → 컬렉션 이름(없으면 None). 같은 글자의 이름이 있으면 그 이름."""
    from core.database import get_conn
    async with get_conn() as conn:
        if await conn.fetchval("SELECT 1 FROM ops_namespace WHERE name = $1", ref):
            return ref
        if ref.isdigit():
            return await conn.fetchval("SELECT name FROM ops_namespace WHERE id = $1", int(ref))
        return await conn.fetchval("SELECT name FROM ops_namespace WHERE code = $1", ref)


class CollectionRefMiddleware:
    def __init__(self, app, resolve: Optional[Resolver] = None):
        self.app = app
        self.resolve = resolve or resolve_collection

    async def _name(self, v: str) -> str:
        if not looks_like_ref(v):
            return v
        try:
            return await self.resolve(v) or v
        except Exception as e:   # noqa: BLE001 — 풀기 실패로 요청을 죽이지 않는다(뒤에서 없는 컬렉션으로 걸림)
            logger.warning("[collection_ref] %s 풀기 실패: %s", v, e)
            return v

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        scope = dict(scope)
        for p in PATH_PREFIXES:
            if scope["path"].startswith(p):
                head, _, tail = scope["path"][len(p):].partition("/")
                name = await self._name(unquote(head))
                if name != unquote(head):
                    path = p + name + ("/" + tail if tail else "")
                    scope["path"] = path
                    scope["raw_path"] = quote(path).encode()
                break
        qs = scope.get("query_string") or b""
        if qs:
            pairs = parse_qsl(qs.decode("latin-1"), keep_blank_values=True)
            new = [(k, await self._name(v) if k in KEYS else v) for k, v in pairs]
            if new != pairs:
                scope["query_string"] = urlencode(new).encode()
        headers = dict(scope.get("headers") or [])
        if scope.get("method") in ("POST", "PUT", "PATCH") and headers.get(b"content-type", b"").startswith(b"application/json"):
            receive = await self._json_body(scope, receive)
        await self.app(scope, receive, send)

    async def _json_body(self, scope, receive):
        chunks, more = [], True
        while more:
            msg = await receive()
            if msg["type"] != "http.request":   # 본문 전에 연결이 끊김 — 그대로 넘긴다
                return _replay([msg], receive)
            chunks.append(msg.get("body", b""))
            more = msg.get("more_body", False)
        body = b"".join(chunks)
        if len(body) <= MAX_BODY:
            try:
                data = json.loads(body)
            except ValueError:
                data = None
            if isinstance(data, dict) and any(looks_like_ref(data.get(k)) for k in KEYS):
                changed = False
                for k in KEYS:
                    if looks_like_ref(data.get(k)):
                        name = await self._name(data[k])
                        changed |= name != data[k]
                        data[k] = name
                if changed:
                    body = json.dumps(data, ensure_ascii=False).encode()
                    scope["headers"] = [(k, v) for k, v in scope["headers"] if k != b"content-length"] + \
                                       [(b"content-length", str(len(body)).encode())]
        return _replay([{"type": "http.request", "body": body, "more_body": False}], receive)


def _replay(msgs: list, receive):
    """읽어 둔 메시지를 먼저 돌려주고, 그다음은 원래 receive(끊김 감지 — SSE 스트리밍이 쓴다)."""
    pending = list(msgs)

    async def _receive():
        if pending:
            return pending.pop(0)
        return await receive()
    return _receive
