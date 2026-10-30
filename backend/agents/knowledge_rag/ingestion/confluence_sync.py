"""컨플 공간 동기화(10/30 사용자 "옮기면서 동기화 만들어라" · A안: 연결한 사람의 저장된 PAT로 — 사내 API/MCP 게이트웨이가 오면 공급자만 교체).

업계 방식(docs/domains/knowledge/sources/confluence/confluence-sync-research.md): 자주 도는 **증분**(마지막 동기화 뒤 수정된 페이지만 —
CQL lastmodified) + 드물게 도는 **전체 대조**(지금 보이는 페이지 목록 전체와 비교 — 삭제·권한 제한은 증분으로 안 잡힌다).
- 증분(하루 1회): 바뀐 페이지를 같은 묶음 연결 규칙(rag_import_rule)으로 자리를 정해 다시 등록 — 옛 조각은 같은 페이지 재등록이 교체.
  우리 쪽에서 사람이 고친 조각이 있으면 덮지 않고 "확인 필요"로 남긴다(지식 등록의 재등록 확인과 같은 규칙, A안 2026-10-12).
  읽기 제한이 걸린 페이지는 조각을 승인 대기로 내린다(지우지 않음). 규칙이 없는 새 묶음은 건드리지 않고 센다(사람이 연결해야).
- 전체 대조(주 1회): 목록을 **끝까지 다 받았을 때만** 사라진 페이지의 조각을 승인 대기로 내린다 — 끊긴 목록으로 판단하면 대량 오삭제.
LLM은 쓰지 않는다. 자동 실행은 설정 confluence_sync(기본 꺼짐 — 개인 토큰을 서버가 정기적으로 쓰는 것은 운영 반입 전 보안 확인 필요).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

from core.database import get_conn

logger = logging.getLogger(__name__)

PATH_SEP = " › "
INCREMENTAL_EVERY = timedelta(hours=24)
FULL_EVERY = timedelta(days=7)
_LOOP_SECONDS = 1800


# ── 공급자(바꿔 끼우는 곳) ──────────────────────────────────────────────────────────────

class ConfluenceSource(Protocol):
    """컨플을 읽는 세 가지 — 구현만 바꾸면(PAT REST → 사내 API → MCP 게이트웨이) 동기화 나머지는 그대로."""

    async def changed_since(self, since: datetime) -> list[dict]:
        """마지막 동기화 뒤 수정·생성된 페이지 [{page_id, title, ancestors: [{id, title}]}] (ancestors = 공간 맨 위부터)."""

    async def page(self, page_id: str):
        """페이지 본문(ParsedDocument — metadata.restricted·version·raw_html 포함)."""

    async def list_all(self) -> tuple[list[str], bool]:
        """연결 범위의 지금 보이는 페이지 id 전체와 끝까지 받았나(잘림 없음)."""

    async def resolve(self) -> None:
        """연결 범위를 id로 확정(제목 주소 등) — 실패하면 예외."""


def cql_changed(space_key: Optional[str], root_id: Optional[str], since: datetime) -> str:
    """증분 질의 — 1시간 앞에서부터(Onyx와 같은 여유). CQL은 시각을 서버 시간대로 읽는데 우리는 UTC로 넘긴다 — KST(+9) 서버면
    9시간 더 앞에서부터라 놓치지 않는다(더 받을 뿐, 같은 내용은 해시로 건너뜀). UTC보다 뒤인 시간대 서버면 놓칠 수 있다(한계)."""
    since = since if since.tzinfo else since.replace(tzinfo=timezone.utc)   # 시간대 없는 값 = UTC
    t = (since.astimezone(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
    scope = f"ancestor = {root_id}" if root_id else f'space = "{space_key}"'
    return f'type = page AND {scope} AND lastmodified >= "{t}" ORDER BY lastmodified ASC'


class RestPatSource:
    """Confluence DC REST + 개인 PAT(Bearer). 연결한 사람이 볼 수 있는 범위만 보인다 — 한계는 화면·문서에 표시."""

    def __init__(self, root_url: str, token: str):
        from agents.knowledge_rag.ingestion.web_crawler import _parse_confluence_url
        self.root_url = root_url
        self.token = token
        self.base_url, self.root_id, self.space_key, self.title_hint = _parse_confluence_url(root_url)

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    async def resolve(self) -> None:
        """제목 주소(/display/KEY/제목)면 그 페이지 id를 찾아 둔다 — 없으면 증분이 공간 전체를 받고 묶음 자리도 틀린다(리뷰 M1).
        미리보기·전체 목록과 같은 범위(그 페이지 아래)."""
        if self.root_id or not self.title_hint:
            return
        import httpx
        from agents.knowledge_rag.ingestion.web_crawler import FETCH_TIMEOUT
        async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT, verify=False) as cl:
            r = await cl.get(f"{self.base_url}/rest/api/content", headers=self._h(),
                             params={"spaceKey": self.space_key, "title": self.title_hint, "type": "page", "limit": 1})
            r.raise_for_status()
            res = r.json().get("results") or []
        if not res:
            raise LookupError("연결한 페이지를 찾지 못함(제목이 바뀌었을 수 있음)")
        self.root_id = str(res[0]["id"])

    async def changed_since(self, since: datetime) -> list[dict]:
        import httpx
        from agents.knowledge_rag.ingestion.web_crawler import FETCH_TIMEOUT
        out: list[dict] = []
        start = 0
        async with httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT, verify=False) as cl:
            while True:
                r = await cl.get(f"{self.base_url}/rest/api/content/search", headers=self._h(),
                                 params={"cql": cql_changed(self.space_key, self.root_id, since), "limit": 100, "start": start,
                                         "expand": "ancestors"})
                r.raise_for_status()
                j = r.json()
                res = j.get("results", [])
                out += [{"page_id": str(x["id"]), "title": x.get("title"),
                         "ancestors": [{"id": str(a["id"]), "title": a.get("title")} for a in x.get("ancestors", [])]} for x in res]
                if not res or not (j.get("_links") or {}).get("next"):
                    break
                start += len(res)
        return out

    async def page(self, page_id: str):
        from agents.knowledge_rag.ingestion.web_crawler import fetch_confluence_by_id
        return await fetch_confluence_by_id(self.base_url, page_id, self.token, diagram_text=True, image_marks=False)

    async def list_all(self) -> tuple[list[str], bool]:
        from agents.knowledge_rag.ingestion import team_import as ti
        from agents.knowledge_rag.ingestion.web_crawler import fetch_confluence_tree
        if self.root_id or self.title_hint:
            t = await fetch_confluence_tree(self.root_url, self.token, max_depth=10, max_pages=ti.MAX_PAGES)
            ids = [str(x["page_id"]) for x in t["tree"]]
            return ids, not (t.get("truncated") or t.get("max_depth_reached"))
        _, tree, truncated = await ti.fetch_space_pages(self.base_url, self.space_key, self.token)
        return [str(x["page_id"]) for x in tree], not truncated


# ── 순수 규칙(테스트가 성질로 확인) ────────────────────────────────────────────────────

def place(root_id: Optional[str], ancestors: list[dict], page: dict) -> Optional[dict]:
    """바뀐 페이지의 묶음 자리 — team_import.bundles_of·paths_of와 같은 규칙(묶음 = 연결 범위 바로 아래 페이지, 경로 = 그 아래 조상 제목들).
    root_id 없음 = 공간 전체(맨 위 페이지가 묶음). 연결 범위 밖이면 None."""
    if root_id:
        ids = [a["id"] for a in ancestors]
        if page.get("page_id") == root_id or root_id not in ids:
            return None
        below = ancestors[ids.index(root_id) + 1:]
    else:
        below = list(ancestors)
    titles = [a.get("title") or a["id"] for a in below]
    own = page.get("title") or page.get("page_id")
    return {"bundle": titles[0] if titles else own, "depth": len(below) + 1, "path": PATH_SEP.join(titles), "title": own,
            "page_id": page.get("page_id")}


def missing_pages(known: list[str], listed: list[str], complete: bool) -> Optional[list[str]]:
    """전체 대조 — 끝까지 받은 목록일 때만 사라진 페이지(우리 쪽엔 있는데 목록에 없음). 덜 받았으면 None(판단 안 함)."""
    if not complete:
        return None
    seen = set(listed)
    return sorted({k for k in known if k not in seen})


# ── 실행 ──────────────────────────────────────────────────────────────────────────
# 이 연결의 페이지 = 이 팀·이 공간의 적재된 배치에서 '적재됨'인 페이지(rag_import_page). 동기화가 새로 넣은 페이지·작업도
# 같은 배치에 기록한다 — 배치 롤백이 동기화 결과까지 되돌리고, 다른 경로(남의 컬렉션·개인 등록)로 들어온 같은 페이지는
# 건드리지 않는다(업계 수집기도 연결마다 자기가 가져온 문서 목록을 따로 둔다 — Onyx document_by_connector_credential_pair, 리뷰 H1 10/30).

MAX_RETRY = 5               # 실패한 페이지를 다음 증분에 다시 시도하는 횟수(충돌은 사람이 풀 때까지 계속 — 화면 '확인 필요')
MISSING_CAP = 0.3           # 전체 대조에서 사라짐이 이 비율을 넘으면 내리지 않고 보고만(담당 교체로 보이는 범위가 줄었을 수 있다)
ERROR_BACKOFF = timedelta(hours=6)


async def _source_for(conn, c) -> tuple[Optional["ConfluenceSource"], Optional[str]]:
    """연결의 공급자 — 지금은 담당자 개인 PAT. 토큰이 없으면 (None, 이유)."""
    from core.security import get_user_confluence_pat
    u = await conn.fetchrow("SELECT id, username, encrypted_confluence_pat FROM ops_user WHERE id = $1", c["owner_user_id"])
    token = get_user_confluence_pat(dict(u)) if u else None
    if not token:
        return None, "담당자 컨플 토큰이 없거나 만료 — 다른 담당자가 [지금 동기화]로 다시 연결해 주세요"
    return RestPatSource(c["root_url"], token), None


async def blocked_reason(conn, c) -> Optional[str]:
    """지금 돌면 안 되는 이유 — 적재 전(연결만 하고 적재가 안 끝남)·적재 중(같은 페이지를 두 경로가 넣는 경쟁)·실험 중(전/후 측정 사이에
    지식이 바뀌면 판정이 흐려짐). 없으면 None."""
    if await _home_batch(conn, c) is None:
        return "적재가 끝난 뒤부터 동기화해요"
    if await conn.fetchval("SELECT 1 FROM rag_import_batch WHERE team_id = $1 AND status = 'loading'", c["team_id"]):
        return "적재 중이라 끝난 뒤에 동기화해요"
    if await conn.fetchval("SELECT 1 FROM eval_experiment_job WHERE status = 'running'"):
        return "개선 후보 실험 중이라 끝난 뒤에 동기화해요"
    return None


async def _home_batch(conn, c) -> Optional[int]:
    """이 연결의 최근 적재 배치 — 새 페이지·작업을 여기에 기록한다."""
    return await conn.fetchval("SELECT id FROM rag_import_batch WHERE team_id = $1 AND root_url = $2 AND status = 'loaded' "
                               "ORDER BY id DESC LIMIT 1", c["team_id"], c["root_url"])


_MINE_SQL = """SELECT DISTINCT ON (ip.page_id) ip.id, ip.batch_id, ip.page_id, ip.final_namespace_id ns
               FROM rag_import_page ip JOIN rag_import_batch b ON b.id = ip.batch_id
               WHERE b.team_id = $1 AND b.root_url = $2 AND b.status = 'loaded' AND ip.status = 'loaded' {extra}
               ORDER BY ip.page_id, ip.batch_id DESC"""


async def _mine(conn, c, page_id: str):
    return await conn.fetchrow(_MINE_SQL.format(extra="AND ip.page_id = $3"), c["team_id"], c["root_url"], str(page_id))


async def _known_pages(conn, c) -> list[dict]:
    """이 연결로 들어와 지금 사용 중·승인 대기인 페이지와 그 컬렉션."""
    from agents.knowledge_rag.ingestion.team_import import ACTIVE_KNOWLEDGE
    rows = await conn.fetch(_MINE_SQL.format(extra="""AND EXISTS (SELECT 1 FROM rag_knowledge k WHERE k.confluence_page_id = ip.page_id
                                                     AND k.namespace_id = ip.final_namespace_id AND k.status = ANY($3::text[]))"""),
                            c["team_id"], c["root_url"], list(ACTIVE_KNOWLEDGE))
    return [dict(r) for r in rows]


async def _write_denied(conn, ns_id: int, owner: dict) -> Optional[str]:
    """담당자가 그 컬렉션에 쓸 수 있나 — 화면 등록·적재 확정과 같은 규칙(edit_denied_reason)."""
    from core.dependencies import EFFECTIVE_OWNERS_SQL, edit_denied_reason
    name = await conn.fetchval("SELECT name FROM ops_namespace WHERE id = $1", ns_id)
    if name is None:
        return "컬렉션 없음"
    return edit_denied_reason([r["part_id"] for r in await conn.fetch(EFFECTIVE_OWNERS_SQL, name)], owner)


async def _reingest(src, c, ch: dict, rules: dict, owner: dict, out: dict) -> Optional[str]:
    """바뀐 페이지 하나 — 자리 정하기 → 권한 → 본문 → (제한이면 내림 · 같으면 건너뜀) → 원문 초안 → 같은 페이지 재등록(교체) → 배치에 기록.
    다시 시도할 페이지면 그 이유('failed'·'conflict'), 아니면 None."""
    from agents.knowledge_rag.ingestion import team_import as ti
    from agents.knowledge_rag.ingestion.source_ledger import content_hash, hold_restricted_chunks, rechunk_items, save_draft_for_parsed
    from agents.knowledge_rag.knowledge import service
    pid = str(ch["page_id"])
    pl = place(src.root_id, ch.get("ancestors") or [], ch)
    async with get_conn() as conn:
        mine = await _mine(conn, c, pid)
        if mine is not None:
            ns_id, batch_id, row_id = mine["ns"], mine["batch_id"], mine["id"]
        else:
            if await ti._existing_ns(conn, pid) is not None:
                out["elsewhere"] += 1   # 다른 경로(다른 팀·개인 등록)로 들어온 페이지 — 그쪽 담당이 관리
                return None
            if pl is None:
                out["out_of_scope"] += 1
                return None
            has, ns_id = ti.connect_target(pl, rules)
            if not has:
                out["no_rule"] += 1   # 규칙 없는 새 묶음 — 사람이 연결해야
                return None
            if ns_id is None:
                out["excluded"] += 1
                return None
            batch_id, row_id = await _home_batch(conn, c), None
            if batch_id is None:
                return None
            # 이 공간의 어느 배치에서든 사람이 빼거나 보류·롤백(held)한 페이지 — 규칙보다 사람 결정(나중 배치만 롤백한 경우도, 재리뷰 M2)
            if await conn.fetchval("SELECT 1 FROM rag_import_page ip JOIN rag_import_batch b ON b.id = ip.batch_id WHERE b.team_id = $1 "
                                   "AND b.root_url = $2 AND ip.page_id = $3 AND ip.status IN ('excluded', 'held')",
                                   c["team_id"], c["root_url"], pid):
                out["excluded"] += 1
                return None
        denied = await _write_denied(conn, ns_id, owner)
    if denied:
        out["denied"] += 1
        return None
    doc = await src.page(pid)
    if doc.metadata.get("restricted"):
        async with get_conn() as conn:
            held, _ = await hold_restricted_chunks(conn, ns_id, pid)
        out["restricted"] += 1
        out["held"] += held
        if held:
            out["_touched"].add(ns_id)
        return None
    raw = (doc.metadata.get("raw_html") or doc.raw_text or "").replace("\x00", "")   # 원장 저장과 같게 해시
    async with get_conn() as conn:
        same = await conn.fetchval("SELECT 1 FROM rag_source_document WHERE namespace_id = $1 AND source_ref = $2 AND status = 'active' "
                                   "AND content_hash = $3", ns_id, pid, content_hash(raw))
        if same:
            out["unchanged"] += 1
            return None
        ns_name = await conn.fetchval("SELECT name FROM ops_namespace WHERE id = $1", ns_id)
        doc_id = await save_draft_for_parsed(conn, ns_id, doc, chunk_strategy="auto", user_id=owner["id"], source_ref=pid, owner_only=True)
        row = await conn.fetchrow("SELECT * FROM rag_source_document WHERE id = $1", doc_id) if doc_id else None
    items = [{**it, "source_document_id": row["id"], "confluence_page_id": pid, "confluence_version": row["source_version"]}
             for it in rechunk_items(row)] if row is not None else []
    if not items:
        out["empty"] += 1
        return None
    try:
        res = await service.bulk_create_knowledge(namespace=ns_name, items=items, source_file=doc.source_name, source_type="confluence",
                                                  created_by_user_id=owner["id"], background=False)
    except service.ReplaceConfirmRequired:
        out["conflict"] += 1           # 사람이 고친 조각 — 덮지 않음(확인 필요)
        return "conflict"
    if res.get("status") != "completed":
        out["failed"] += 1
        return "failed"
    out["_touched"].add(ns_id)
    async with get_conn() as conn:
        async with conn.transaction():
            # 배치가 그사이 롤백됐으면 방금 넣은 조각도 되돌린다(어느 배치로도 못 되돌리는 조각이 남지 않게, 재리뷰 M1)
            if (await conn.execute("UPDATE rag_import_batch SET job_ids = array_append(job_ids, $2) WHERE id = $1 AND status = 'loaded'",
                                   batch_id, res["job_id"])).split()[-1] == "0":
                from agents.knowledge_rag.ingestion.team_import import ACTIVE_KNOWLEDGE
                await conn.execute("UPDATE rag_knowledge SET status = 'deleted', updated_at = NOW() WHERE ingestion_job_id = $1 "
                                   "AND status = ANY($2::text[])", res["job_id"], list(ACTIVE_KNOWLEDGE))
                await conn.execute("UPDATE rag_source_document SET status = 'superseded' WHERE id = $1 AND status = 'active'", row["id"])
                out["rolled_back"] += 1
                return None
            out["updated" if mine is not None else "added"] += 1
            if row_id is not None:
                await conn.execute("UPDATE rag_import_page SET job_id = $2, source_document_id = $3 WHERE id = $1", row_id, res["job_id"], row["id"])
            else:
                await conn.execute(
                    """INSERT INTO rag_import_page (batch_id, page_id, title, bundle, depth, path, status, final_namespace_id, job_id,
                                                     source_document_id, chunk_count, assigned_by)
                       VALUES ($1, $2, $3, $4, $5, $6, 'loaded', $7, $8, $9, $10, 'rule')
                       ON CONFLICT (batch_id, page_id) DO UPDATE SET status = 'loaded', final_namespace_id = $7, job_id = $8,
                         source_document_id = $9, chunk_count = $10, assigned_by = 'rule'""",
                    batch_id, pid, pl["title"] if pl else ch.get("title"), pl["bundle"] if pl else None, pl["depth"] if pl else 0,
                    pl["path"] if pl else None, ns_id, res["job_id"], row["id"], len(items))
    return None


def _empty_result(mode: str) -> dict:
    return {"mode": mode, "changed": 0, "updated": 0, "added": 0, "unchanged": 0, "restricted": 0, "held": 0, "conflict": 0,
            "no_rule": 0, "excluded": 0, "elsewhere": 0, "denied": 0, "rolled_back": 0, "out_of_scope": 0, "empty": 0, "failed": 0, "missing": 0,
            "incomplete": False, "too_many_missing": False, "_touched": set()}


def merge_retry(changes: list[dict], retry: list[dict]) -> list[dict]:
    """이번 증분 목록 + 지난번에 실패·충돌로 남긴 페이지(이번에 또 바뀐 것은 새 목록이 이김)."""
    seen = {str(x["page_id"]) for x in changes}
    return changes + [r for r in retry if str(r["page_id"]) not in seen]


def next_retry(prev: list[dict], results: list[tuple[dict, Optional[str]]]) -> list[dict]:
    """다음에 다시 시도할 페이지 — 실패는 MAX_RETRY회까지, 충돌은 풀릴 때까지(화면에 제목으로 보인다)."""
    tries = {str(r["page_id"]): r.get("tries", 0) for r in prev}
    out = []
    for ch, why in results:
        if why is None:
            continue
        n = tries.get(str(ch["page_id"]), 0) + 1
        if why == "failed" and n > MAX_RETRY:
            logger.warning("컨플 동기화: 재시도 포기 page=%s (%s회)", ch.get("page_id"), MAX_RETRY)
            continue
        out.append({"page_id": str(ch["page_id"]), "title": ch.get("title"), "ancestors": ch.get("ancestors") or [], "why": why, "tries": n})
    return out


async def sync_connection(conn_id: int, mode: str = "incremental") -> dict:
    """연결 하나 동기화 — 결과를 연결 기록에 남긴다(화면이 읽음). 한 번에 하나(연결 행 잠금으로 겹치지 않게)."""
    import httpx
    from agents.knowledge_rag.ingestion.source_ledger import hold_restricted_chunks
    out = _empty_result(mode)
    async with get_conn() as conn:
        c0 = await conn.fetchrow("SELECT * FROM rag_source_connection WHERE id = $1", conn_id)
        if c0 is None:
            return {"skipped": "연결이 없음"}
        why = await blocked_reason(conn, c0)
        if why:
            return {"skipped": why}   # 상태·시각은 그대로(다음 주기에 다시)
        c = await conn.fetchrow("UPDATE rag_source_connection SET status = 'running', error = NULL WHERE id = $1 AND status <> 'running' "
                                "RETURNING *", conn_id)
    if c is None:
        return {"skipped": "이미 동기화 중"}
    retry = []
    try:
        async with get_conn() as conn:
            src, why = await _source_for(conn, c)
            owner = await conn.fetchrow("SELECT id, role, part_id FROM ops_user WHERE id = $1", c["owner_user_id"])
            rules: dict = {}
            for r in await conn.fetch("SELECT bundle, grp, namespace_id FROM rag_import_rule WHERE team_id = $1 AND root_url = $2",
                                      c["team_id"], c["root_url"]):
                rb = rules.setdefault(r["bundle"], {"ns": None, "groups": {}})
                if r["grp"] is None:
                    rb["ns"] = r["namespace_id"]
                else:
                    rb["groups"][r["grp"]] = r["namespace_id"]
        if src is None or owner is None:
            await _finish(conn_id, "stopped", why or "담당자 없음", out, mode, None)
            return {**_public(out), "error": why}
        owner = dict(owner)
        await src.resolve()
        retry = _retry_of(c)
        if mode == "full":
            listed, complete = await src.list_all()
            async with get_conn() as conn:
                known = await _known_pages(conn, c)
            gone = missing_pages([k["page_id"] for k in known], listed, complete)
            if gone is None:
                out["incomplete"] = True   # 목록을 끝까지 못 받음 — 판단 안 함
            elif len(gone) > max(5, MISSING_CAP * len(known)):
                out["missing"] = len(gone)
                out["too_many_missing"] = True   # 담당 권한이 좁아졌거나 공간이 옮겨졌을 수 있다 — 내리지 않고 보고만
            else:
                ns_of = {k["page_id"]: k["ns"] for k in known}
                async with get_conn() as conn:
                    denied_ns = {ns for ns in {ns_of[p] for p in gone} if await _write_denied(conn, ns, owner)}   # 증분과 같은 권한 규칙(재리뷰 M3)
                    for pid in gone:
                        if ns_of[pid] in denied_ns:
                            out["denied"] += 1
                            continue
                        held, _ = await hold_restricted_chunks(conn, ns_of[pid], pid)
                        out["held"] += held
                        if held:
                            out["_touched"].add(ns_of[pid])
                        out["missing"] += 1
        else:
            since = c["last_incremental_at"] or c["created_at"]
            changes = merge_retry(await src.changed_since(since), retry)
            out["changed"] = len(changes)
            results = []
            for ch in changes:
                try:
                    results.append((ch, await _reingest(src, c, ch, rules, owner, out)))
                except Exception as e:  # noqa: BLE001 — 한 페이지 실패는 그 페이지만(결과에 세고 다음에 다시, 로그)
                    logger.warning("컨플 동기화: 페이지 실패 conn=%s page=%s: %s", conn_id, ch.get("page_id"), type(e).__name__)
                    out["failed"] += 1
                    results.append((ch, "failed"))
            retry = next_retry(retry, results)
        await _finish(conn_id, "ok", None, out, mode, retry)
    except Exception as e:  # noqa: BLE001 — 연결 전체 실패(토큰 만료·서버 오류)는 상태로 보인다(조용히 멈추지 않게)
        logger.warning("컨플 동기화 실패 conn=%s: %s", conn_id, type(e).__name__)
        denied = _auth_error(e)
        msg = "컨플 접근 실패 — 토큰 만료·권한 변경일 수 있어요" if denied else f"동기화 오류: {type(e).__name__}"
        await _finish(conn_id, "stopped" if denied else "error", msg, out, mode, None)
        return {**_public(out), "error": msg}
    finally:
        await _invalidate(out["_touched"])
    return _public(out)


def _auth_error(e: BaseException) -> bool:
    """401/403 — 크롤러가 HTTP 오류를 ValueError로 감싸 던지는 경로(페이지 트리)도 원인까지 본다(재리뷰 L1)."""
    import httpx
    for x in (e, e.__cause__):
        if isinstance(x, httpx.HTTPStatusError) and x.response.status_code in (401, 403):
            return True
    return False


def _retry_of(c) -> list[dict]:
    r = c["retry_pages"]
    return json.loads(r) if isinstance(r, str) else list(r or [])


def _public(out: dict) -> dict:
    return {k: v for k, v in out.items() if not k.startswith("_")}


async def _invalidate(ns_ids: set) -> None:
    """실제로 바뀐 컬렉션만 검색 캐시 비움."""
    if not ns_ids:
        return
    from shared.cache import invalidate_namespace_family
    async with get_conn() as conn:
        names = [r["name"] for r in await conn.fetch("SELECT name FROM ops_namespace WHERE id = ANY($1::int[])", list(ns_ids))]
    for n in names:
        await invalidate_namespace_family(n)


async def _finish(conn_id: int, status: str, error: Optional[str], out: dict, mode: str, retry: Optional[list]) -> None:
    """결과 기록 — 성공일 때만 그 방식의 마지막 시각을 옮긴다. retry None = 다시 시도 목록 그대로."""
    col = "last_full_at" if mode == "full" else "last_incremental_at"
    async with get_conn() as conn:
        await conn.execute(f"UPDATE rag_source_connection SET status = $2, error = $3, last_result = $4::jsonb, "
                           f"{col} = CASE WHEN $2 = 'ok' THEN NOW() ELSE {col} END, last_run_at = NOW(), "
                           f"retry_pages = COALESCE($5::jsonb, retry_pages) WHERE id = $1",
                           conn_id, status, error, json.dumps(_public(out), ensure_ascii=False),
                           None if retry is None else json.dumps(retry, ensure_ascii=False))


async def ensure_connection(conn, team_id: int, root_url: str, title: Optional[str], owner_user_id: int, since) -> int:
    """묶음 연결 때 — 연결 기록(공간 하나 = 한 줄). 증분 시작점 = 미리보기 때(그 뒤 바뀐 것부터). 적재가 끝나기 전엔 돌지 않는다(blocked_reason).
    롤백으로 멈춘 연결도 다시 연결하면 재개."""
    return await conn.fetchval(
        """INSERT INTO rag_source_connection (team_id, root_url, title, owner_user_id, last_incremental_at)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (team_id, root_url) DO UPDATE SET title = EXCLUDED.title, owner_user_id = EXCLUDED.owner_user_id,
             status = CASE WHEN rag_source_connection.status = 'stopped' THEN 'ok' ELSE rag_source_connection.status END,
             error = CASE WHEN rag_source_connection.status = 'stopped' THEN NULL ELSE rag_source_connection.error END
           RETURNING id""", team_id, root_url, title, owner_user_id, since)


async def stop_after_rollback(conn, team_id: int, root_url: str) -> None:
    """배치 롤백 뒤 — 이 공간에 남은 적재가 없으면 연결을 멈춘다(안 그러면 다음 증분이 되돌린 페이지를 규칙대로 다시 넣는다, 리뷰 M3)."""
    if await conn.fetchval("SELECT 1 FROM rag_import_batch WHERE team_id = $1 AND root_url = $2 AND status = 'loaded'", team_id, root_url):
        return
    await conn.execute("UPDATE rag_source_connection SET status = 'stopped', error = '적재를 되돌려 멈춤 — 다시 연결하면 재개' "
                       "WHERE team_id = $1 AND root_url = $2 AND status <> 'running'", team_id, root_url)


# ── 자동 실행(설정 confluence_sync) ──────────────────────────────────────────────────

_task: Optional[asyncio.Task] = None


async def run_due() -> None:
    now = datetime.now(timezone.utc)
    async with get_conn() as conn:
        rows = await conn.fetch("SELECT id, status, last_incremental_at, last_full_at, last_run_at FROM rag_source_connection "
                                "WHERE status IN ('ok', 'error')")
    for r in rows:
        if r["status"] == "error" and r["last_run_at"] and now - r["last_run_at"] < ERROR_BACKOFF:
            continue   # 오류 뒤엔 잠시 쉰다(30분마다 전체 목록을 다시 받는 폭주 방지)
        if r["last_full_at"] is None or now - r["last_full_at"] >= FULL_EVERY:
            if (await sync_connection(r["id"], "full")).get("error"):
                continue   # 전체 대조가 막혔으면(토큰 만료 등) 같은 주기에 증분을 또 부르지 않는다(재리뷰 L3)
        if r["last_incremental_at"] is None or now - r["last_incremental_at"] >= INCREMENTAL_EVERY:
            await sync_connection(r["id"], "incremental")


async def _loop() -> None:
    await asyncio.sleep(120)
    while True:
        try:
            await run_due()
        except Exception as e:  # noqa: BLE001
            logger.warning("컨플 동기화 실행기 오류: %s", e)
        await asyncio.sleep(_LOOP_SECONDS)


async def start() -> None:
    """기동 때 — 돌던 동기화(서버 재시작으로 끊김)를 오류로 닫고, 설정이 켜져 있으면 실행기를 띄운다."""
    global _task
    async with get_conn() as conn:
        await conn.execute("UPDATE rag_source_connection SET status = 'error', error = '서버가 다시 시작돼 중단 — 다음 주기에 다시' "
                           "WHERE status = 'running'")
    from core.config import settings
    if getattr(settings, "confluence_sync", False) and (_task is None or _task.done()):
        _task = asyncio.get_running_loop().create_task(_loop())


async def stop() -> None:
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
