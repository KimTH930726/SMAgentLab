"""지식 검토 기한(11/04 태훈 5번) — 컬렉션마다 검토 주기, 기한 지난 원문 페이지를 "검토 필요"로, 사람은 [검토 완료]만.

업계: Guru 검증(담당·검증 주기·미검증 카드) · Confluence 페이지 검증 · Zendesk 문서 검토 기한 — 같은 틀.
단위 = 원문 페이지(같은 페이지 조각은 한 번에 검토). 마지막 검토 = 조각의 reviewed_at(승인·검토 완료 때 남는 사람 확인 시각)과
content_edited_at(사람이 고친 때) 중 늦은 것, 둘 다 없으면 등록한 때 — 고친 것도 검토다(리뷰 M2: 검토일이 있으면 고쳐도 목록에 남았다).
페이지는 가장 오래된 조각 기준(한 조각이라도 기한이 지나면 페이지가 검토 필요).
원문 바뀜(11/04, Confluence "검증 해제" 관례): 컨플 동기화가 바뀐 페이지를 찾았는데 사람이 고친 조각이 있어 덮지 않은 페이지(충돌 —
rag_source_connection.retry_pages why='conflict')는 기한과 상관없이 맨 위에 "원문 바뀜". 그 밖의 바뀐 페이지는 동기화가 이미 새 원문으로 바꿨다.
기본 목록 = 원문 바뀜 + 기한 지났고 최근 채팅에 쓰인 것(11/05 — 컨플 400페이지를 한꺼번에 적재하면 90일 뒤 400개가 한 번에 올라온다).
기한만 지나고 안 쓰인 것은 reason='unused'로 같이 돌려주고 화면이 [미사용] 칩 뒤로 둔다(업계: Guru 검증도 많이 보는 카드부터).
안 하는 것: 자동 알림·독촉(쓰는지 먼저 본다) · 내용이 바뀌었는지 AI 판단(원문 원장 동기화의 일) · 조각 단위 검토.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Mapping, Optional

from core.database import get_conn

DEFAULT_INTERVAL_DAYS = 90
INTERVAL_CHOICES = (30, 90, 180)   # + None(안 함)
USAGE_DAYS = 30   # "많이 쓰인 순" — 최근 30일 채팅 답이 근거로 쓴 횟수


def doc_key(r: Mapping) -> str:
    """조각 → 원문 페이지 묶음 키. 페이지 번호 > 원문 > (적재 작업·파일 이름) > 조각 하나(묶을 근거가 없으면 따로 — 직접 입력이 한 덩어리로 섞이지 않게)."""
    if r.get("confluence_page_id"):
        return f"p:{r['confluence_page_id']}"
    if r.get("source_document_id") is not None:
        return f"d:{r['source_document_id']}"
    if r.get("source_file"):
        return f"f:{r.get('ingestion_job_id') or ''}:{r['source_file']}"
    return f"k:{r['id']}"


def last_touch(r: Mapping) -> datetime:
    seen = [t for t in (r["reviewed_at"], r["content_edited_at"]) if t]
    return max(seen) if seen else r["created_at"]


def due_docs(rows: Iterable[Mapping], interval_days: Optional[int], now: datetime, used: Mapping[int, int],
             changed_pages: Iterable[str] = ()) -> list[dict]:
    """검토 필요 페이지 — 원문 바뀜(changed_pages = 동기화 충돌 페이지 번호) 먼저, 그다음 기한 지난 것(채팅에 많이 쓰인 순 → 오래 지난 순).
    interval_days None = 기한은 안 봄(원문 바뀜은 그래도 보인다).
    used = 조각 id → 최근 채팅 답 수(같은 답이 한 페이지 조각 여럿을 써도 페이지 합은 조각 합 — 화면은 "쓰인 횟수"로 충분)."""
    changed = {f"p:{p}" for p in changed_pages}
    if not interval_days and not changed:
        return []
    groups: dict[str, list[Mapping]] = {}
    for r in rows:
        groups.setdefault(doc_key(r), []).append(r)
    out = []
    for key, rs in groups.items():
        last = min(last_touch(r) for r in rs)
        overdue = (now - (last + timedelta(days=interval_days))).days if interval_days else None
        source_changed = key in changed
        if not source_changed and (overdue is None or last + timedelta(days=interval_days) >= now):
            continue
        reviewed = [r for r in rs if r["reviewed_at"]]
        latest = max(reviewed, key=lambda r: r["reviewed_at"]) if reviewed else None
        out.append({
            "key": key, "title": next((r["title"] for r in rs if r.get("title")), None) or f"지식 #{rs[0]['id']}",
            "ids": sorted(r["id"] for r in rs), "n": len(rs),
            "last_reviewed_at": latest["reviewed_at"].isoformat() if latest else None,
            "last_reviewed_by": latest["owner"] if latest else None,
            "created_at": min(r["created_at"] for r in rs).isoformat(),
            "overdue_days": overdue if overdue is not None and overdue >= 0 else None, "source_changed": source_changed,
            "used": (u := sum(used.get(r["id"], 0) for r in rs)),
            # 왜 올라왔나(11/05 태훈 — 관리자에게 사유까지): 원문 바뀜 · 쓰이는 중(기한 지남 + 최근 채팅 근거) · 미사용(기한만 지남 — 기본 목록에서 뺀다)
            "reason": "source_changed" if source_changed else ("used" if u else "unused"),
        })
    out.sort(key=lambda d: (not d["source_changed"], -d["used"], -(d["overdue_days"] or 0), d["key"]))
    return out


def freshness(rows: Iterable[Mapping], want: Iterable[int], intervals: Mapping[int, Optional[int]], owners: Mapping[int, list[str]],
              now: datetime) -> dict[int, dict]:
    """근거 최신성(11/04, 채팅 근거 카드) — 요청한 조각마다 그 페이지의 마지막 검토(검토 필요 목록과 같은 묶음·같은 규칙)·기한 지남·담당.
    rows = 요청한 조각 + 같은 페이지 조각(namespace_id 포함). intervals = 컬렉션 id → 주기(None = 안 함)."""
    groups: dict[tuple, list[Mapping]] = {}
    for r in rows:
        groups.setdefault((r["namespace_id"], doc_key(r)), []).append(r)
    out = {}
    by_id = {r["id"]: r for r in rows}
    for i in want:
        r = by_id.get(i)
        if r is None:
            continue
        g = groups[(r["namespace_id"], doc_key(r))]
        oldest = min(g, key=last_touch)
        last = last_touch(oldest)
        days = intervals.get(r["namespace_id"])
        # "검토 날짜"는 그 날짜가 실제 검토일일 때만 — 다른 조각만 검토됐으면 등록·수정일이다(리뷰 L5)
        out[i] = {"last_at": last.isoformat(), "reviewed": oldest["reviewed_at"] is not None and oldest["reviewed_at"] == last,
                  "overdue": bool(days) and last + timedelta(days=days) < now, "interval_days": days,
                  "owners": owners.get(r["namespace_id"], [])}
    return out


async def knowledge_freshness(ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    async with get_conn() as conn:
        rows = await conn.fetch(
            """WITH want AS (SELECT * FROM rag_knowledge WHERE id = ANY($1::int[]) AND status = 'active')
               SELECT k.id, k.namespace_id, k.confluence_page_id, k.source_document_id, k.ingestion_job_id, k.source_file,
                      k.reviewed_at, k.content_edited_at, k.created_at
               FROM rag_knowledge k WHERE k.status = 'active' AND (k.id = ANY($1::int[]) OR EXISTS (
                 SELECT 1 FROM want w WHERE w.namespace_id = k.namespace_id AND (
                   (w.confluence_page_id IS NOT NULL AND k.confluence_page_id = w.confluence_page_id)
                   OR (w.confluence_page_id IS NULL AND w.source_document_id IS NOT NULL AND k.source_document_id = w.source_document_id)
                   OR (w.confluence_page_id IS NULL AND w.source_document_id IS NULL AND w.source_file IS NOT NULL
                       AND k.source_file = w.source_file AND k.ingestion_job_id IS NOT DISTINCT FROM w.ingestion_job_id))))""", ids)
        ns_ids = list({r["namespace_id"] for r in rows})
        intervals = {r["id"]: r["review_interval_days"] for r in await conn.fetch(
            "SELECT id, review_interval_days FROM ops_namespace WHERE id = ANY($1::int[])", ns_ids)}
        owners: dict[int, list[str]] = {}
        for r in await conn.fetch("SELECT o.namespace_id, p.name FROM ops_namespace_owner o JOIN ops_part p ON p.id = o.part_id "
                                  "WHERE o.namespace_id = ANY($1::int[]) ORDER BY p.name", ns_ids):
            owners.setdefault(r["namespace_id"], []).append(r["name"])
        now = await conn.fetchval("SELECT NOW()")
    return freshness([dict(r) for r in rows], ids, intervals, owners, now)


async def review_due(namespace: str) -> dict:
    async with get_conn() as conn:
        ns = await conn.fetchrow("SELECT id, review_interval_days FROM ops_namespace WHERE name = $1", namespace)
        if ns is None:
            raise LookupError("컬렉션을 찾을 수 없습니다.")
        rows = await conn.fetch(
            """SELECT id, confluence_page_id, source_document_id, ingestion_job_id, source_file,
                      COALESCE(source_file, heading_path[1]) AS title, reviewed_at, content_edited_at, created_at, owner
               FROM rag_knowledge WHERE namespace_id = $1 AND status = 'active'""", ns["id"])
        ids = [r["id"] for r in rows]
        # 지식 검색 화면 used_count와 같은 셈(부분 인덱스 idx_ops_message_assistant_created)
        used = {r["kid"]: r["n"] for r in await conn.fetch(
            "SELECT (e->>'id')::int AS kid, count(DISTINCT m.id) AS n FROM ops_message m CROSS JOIN LATERAL jsonb_array_elements(m.results) e "
            "WHERE m.role = 'assistant' AND m.created_at > NOW() - make_interval(days => $2) AND jsonb_typeof(m.results) = 'array' "
            "AND (e->>'id') ~ '^[0-9]+$' AND (e->>'id')::int = ANY($1::int[]) GROUP BY 1", ids, USAGE_DAYS)} if ids else {}
        now = await conn.fetchval("SELECT NOW()")
        # 동기화 충돌(원문이 바뀌었는데 사람이 고친 조각이 있어 덮지 않은 페이지) — 이 컬렉션 조각이 있는 페이지만 의미가 있다(도는 doc_key가 거른다)
        changed = [r["pid"] for r in await conn.fetch(
            "SELECT DISTINCT x->>'page_id' AS pid FROM rag_source_connection c CROSS JOIN LATERAL jsonb_array_elements(COALESCE(c.retry_pages, '[]'::jsonb)) x "
            "WHERE x->>'why' = 'conflict'")]
    docs = due_docs([dict(r) for r in rows], ns["review_interval_days"], now, used, changed)
    return {"namespace": namespace, "interval_days": ns["review_interval_days"], "usage_days": USAGE_DAYS,
            "total_docs": len({doc_key(dict(r)) for r in rows}), "due": docs}


async def mark_reviewed(namespace: str, ids: list[int], username: str) -> int:
    """[검토 완료] — 그 컬렉션의 사용 중 조각만(다른 컬렉션 id가 섞여도 안 바뀜). 누가(owner) 언제(reviewed_at). 바꾼 수."""
    async with get_conn() as conn:
        done = await conn.execute(
            """UPDATE rag_knowledge k SET reviewed_at = NOW(), owner = $3 FROM ops_namespace n
               WHERE n.name = $1 AND k.namespace_id = n.id AND k.id = ANY($2::int[]) AND k.status = 'active'""",
            namespace, ids, username)
    return int(done.split()[-1])


async def set_interval(namespace: str, days: Optional[int]) -> None:
    if days is not None and days not in INTERVAL_CHOICES:
        raise ValueError(f"검토 주기는 {', '.join(map(str, INTERVAL_CHOICES))}일 또는 안 함만 고를 수 있어요.")
    async with get_conn() as conn:
        done = await conn.execute("UPDATE ops_namespace SET review_interval_days = $2 WHERE name = $1", namespace, days)
    if done.split()[-1] == "0":
        raise LookupError("컬렉션을 찾을 수 없습니다.")
