"""운영 지식은 시스템 컬렉션에만(11/02 D) — 서버 길목 검사(11/03 독립 리뷰 M1: 화면·위치 판단만 막고 API·동기화·적재는 정책 컬렉션에 그대로 썼다,
E2E 잔재 6건으로 재현) + 컬렉션 삭제 때 FK 없는 id 칸 정리(키 정리 5단계 — 지운 id를 가리키던 적재 기록 161건).

conftest가 core.*를 가짜로 바꿔 두므로 실제 파일을 경로로 읽는다.
"""
import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


database = _load("_sysonly_real_database", "core/database.py")
dep = _load("_sysonly_real_dependencies", "core/dependencies.py")

ROWS = {"딜리버스": {"id": 20, "kind": "domain", "area": "policy"},
        "딜리버스 시스템": {"id": 2569, "kind": "domain", "area": "system"},
        "stage:import": {"id": 9, "kind": "staging", "area": "policy"}}


def _conn():
    c = MagicMock()
    c.fetchrow = AsyncMock(side_effect=lambda sql, name: ROWS.get(name))
    c.execute = AsyncMock()
    return c


@pytest.mark.asyncio
@pytest.mark.parametrize("name,expect", [("딜리버스 시스템", 2569), ("stage:import", 9), ("없음", None)])
async def test_resolve_allows_system_staging_and_missing(name, expect):
    assert await database.resolve_knowledge_namespace_id(_conn(), name) == expect


@pytest.mark.asyncio
async def test_resolve_rejects_policy_collection():
    with pytest.raises(ValueError, match="정책 컬렉션엔 운영 지식"):
        await database.resolve_knowledge_namespace_id(_conn(), "딜리버스")


@pytest.mark.asyncio
async def test_router_guard_400_only_for_policy_collection():
    conn = _conn()

    @asynccontextmanager
    async def get_conn():
        yield conn

    with patch.object(dep, "get_conn", get_conn):
        with pytest.raises(HTTPException) as e:
            await dep.reject_knowledge_outside_system("딜리버스")
        assert e.value.status_code == 400
        for ok in ("딜리버스 시스템", "없음"):   # 없는 이름은 뒤 검사(404)에 맡긴다
            await dep.reject_knowledge_outside_system(ok)


def test_write_entry_points_use_guard():
    """등록·적재·동기화가 모두 지나는 두 길목이 거부 함수를 쓴다 — 누가 resolve_namespace_id로 되돌리면 여기서 걸린다."""
    src = (ROOT / "agents/knowledge_rag/knowledge/service.py").read_text(encoding="utf-8")
    for fn in ("async def create_knowledge(", "async def bulk_create_knowledge("):
        body = src[src.index(fn):]
        body = body[:body.index("\nasync def ", 1)]
        assert "resolve_knowledge_namespace_id(conn, namespace)" in body, fn
    router = (ROOT / "agents/knowledge_rag/knowledge/router.py").read_text(encoding="utf-8")
    gate = router[router.index("async def _ingest_gate("):]
    assert "await reject_knowledge_outside_system(namespace)" in gate[:gate.index("try:")]


@pytest.mark.asyncio
async def test_release_refs_clears_every_loose_column():
    conn = _conn()
    await database.release_namespace_refs(conn, 2569)
    sqls = [c.args[0] for c in conn.execute.await_args_list]
    # 넣을 곳이 비는 자동·검토 줄은 NULL로 만들기 전에 검토로(순서가 바뀌면 WHERE final = id가 아무것도 못 잡는다)
    review = next(i for i, s in enumerate(sqls) if "SET status = 'review'" in s)
    null_final = next(i for i, s in enumerate(sqls) if "SET final_namespace_id = NULL" in s)
    assert review < null_final
    for t, c in database.LOOSE_NAMESPACE_REFS:
        assert any(f"UPDATE {t} SET {c} = NULL WHERE {c} = $1" in s for s in sqls), (t, c)
    assert any("DELETE FROM ops_link" in s for s in sqls)
    cfg = next(c for c in conn.execute.await_args_list if "ops_system_config" in c.args[0])
    assert cfg.args[1] == ["glossary_mining_last_id:2569", "glossary_gap_done:2569"]
    assert all(c.args[1] in (2569, "2569", cfg.args[1]) for c in conn.execute.await_args_list)   # 다른 컬렉션 id는 건드리지 않음


@pytest.mark.asyncio
async def test_release_refs_only_existing_columns():
    """기동 중 마이그레이션(#106)은 아직 없는 칸을 빼고 넘긴다 — 넘긴 칸만 건드린다."""
    conn = _conn()
    await database.release_namespace_refs(conn, 7, [("rag_import_batch", "default_namespace_id")])
    sqls = " ".join(c.args[0] for c in conn.execute.await_args_list)
    assert "rag_import_page" not in sqls and "default_namespace_id = NULL" in sqls


# ── URL 가져오기 허용 호스트(11/03 보안 리뷰 #3 SSRF · #4 PAT 유출) ──────────────────────────────────────────
crawler = _load("_sysonly_real_crawler", "agents/knowledge_rag/ingestion/web_crawler.py")


@pytest.mark.parametrize("url", [
    "http://backend:8000/docs", "http://127.0.0.1:5432", "http://host.docker.internal:11434/api/tags",
    "http://169.254.169.254/latest/meta-data", "file:///etc/passwd", "ftp://confl.sinc.co.kr/x",
    "http://공격서버/display/X", "https://confl.sinc.co.kr.evil.com/display/X", "https://user@evil.com/display/X",
])
def test_fetch_url_blocks_outside_allowlist(url):
    with patch.object(crawler, "allowed_hosts", return_value={"confl.sinc.co.kr"}), pytest.raises(ValueError):
        crawler.check_fetch_url(url)


@pytest.mark.parametrize("url", ["https://confl.sinc.co.kr/display/app", "http://CONFL.sinc.co.kr/pages/viewpage.action?pageId=1"])
def test_fetch_url_allows_listed_host(url):
    with patch.object(crawler, "allowed_hosts", return_value={"confl.sinc.co.kr"}):
        crawler.check_fetch_url(url)


@pytest.mark.asyncio
async def test_redirect_to_internal_host_is_blocked():
    """허용 호스트가 내부 주소로 리다이렉트해도 다음 요청에서 막힌다(요청마다 검사)."""
    import httpx

    def handler(request):
        if request.url.host == "confl.sinc.co.kr":
            return httpx.Response(302, headers={"location": "http://backend:8000/docs"})
        return httpx.Response(200, text="내부 문서")
    with patch.object(crawler, "allowed_hosts", return_value={"confl.sinc.co.kr"}):
        async with crawler.guarded_client(transport=httpx.MockTransport(handler)) as c:
            with pytest.raises(ValueError, match="허용되지 않은"):
                await c.get("https://confl.sinc.co.kr/display/app")


def test_every_fetch_client_is_guarded():
    """수집 코드에서 httpx.AsyncClient를 직접 만들지 않는다 — guarded_client만(새 경로가 검사를 빠뜨리지 않게)."""
    for rel in ("agents/knowledge_rag/ingestion/web_crawler.py", "agents/knowledge_rag/ingestion/confluence_sync.py",
                "agents/knowledge_rag/ingestion/team_import.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert src.count("httpx.AsyncClient(") == (1 if rel.endswith("web_crawler.py") else 0), rel
