"""지식 검토 기한(11/04 태훈 5번) — 컬렉션마다 검토 주기, 기한 지난 원문 페이지를 "검토 필요"로, 사람은 [검토 완료]만.

업계: Guru 검증(담당·검증 주기·미검증 카드) · Confluence 페이지 검증 · Zendesk 문서 검토 기한 — 같은 틀.
단위 = 원문 페이지(같은 페이지 조각은 한 번에 검토). 마지막 검토 = 조각의 reviewed_at(승인·검토 완료 때 남는 사람 확인 시각)과
content_edited_at(사람이 고친 때) 중 늦은 것, 둘 다 없으면 등록한 때 — 고친 것도 검토다(리뷰 M2: 검토일이 있으면 고쳐도 목록에 남았다).
페이지는 가장 오래된 조각 기준(한 조각이라도 기한이 지나면 페이지가 검토 필요).
원문 바뀜(11/04, Confluence "검증 해제" 관례): 컨플 동기화가 바뀐 페이지를 찾았는데 사람이 고친 조각이 있어 덮지 않은 페이지(충돌 —
rag_source_connection.retry_pages why='conflict')는 기한과 상관없이 맨 위에 "원문 바뀜". 그 밖의 바뀐 페이지는 동기화가 이미 새 원문으로 바꿨다.
11/05 태훈 "주기마다 전부 검수는 너무 큰 일 — 필수만": 기간만으로는 안 올린다(사건 기반 검증). 올라오는 것 =
  ① 원문 바뀜(동기화 충돌) ② 신고(그 페이지 조각에 [답변 틀림 · 내용이 낡음] 정정 요청이 대기 중)
  ③ 많이 쓰이는데 오래됨(기한 지남 + 최근 30일 채팅 근거 USED_MIN회 이상, 많이 쓰인 순 위 USED_TOP개).
  나머지 기한 지남은 quiet 수만 — 채팅 근거 카드의 '기한 지남' 배지로만 보인다. (로컬 11/05: 기한 지남 18 → ③ 5개)
안 하는 것: 자동 알림·독촉(쓰는지 먼저 본다) · 내용이 바뀌었는지 AI 판단(원문 원장 동기화의 일) · 조각 단위 검토.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Mapping, Optional

from core.database import get_conn

DEFAULT_INTERVAL_DAYS = 90
INTERVAL_CHOICES = (30, 90, 180)   # + None(안 함)
USAGE_DAYS = 30   # "많이 쓰인 순" — 최근 30일 채팅 답이 근거로 쓴 횟수
USED_MIN = 3      # ③ 많이 쓰임 = 최근 30일 채팅 근거 3회 이상
USED_TOP = 10     # ③은 위 10개까지(관리자가 한 번에 볼 만큼)

# 원문 페이지 제목 — 컨플 조각은 적재 원장(rag_import_page)의 페이지 제목 › 첫 제목 경로 › 원본 이름.
# 공간 일괄 적재는 source_file에 묶음 이름("Confluence 16개 페이지(…)")이 들어가 페이지 제목처럼 보이던 것(11/05 태훈).
# 쓰는 쪽: FROM rag_knowledge k LEFT JOIN page_title pt ON pt.page_id = k.confluence_page_id (WITH절에 PAGE_TITLE_CTE)
# 제목이 빈 최신 줄(실패·제한 재수집)이 옛 제목을 가리지 않게 빈 제목은 뺀다(리뷰 L5)
PAGE_TITLE_CTE = ("page_title AS (SELECT DISTINCT ON (page_id) page_id, title FROM rag_import_page "
                  "WHERE COALESCE(title, '') <> '' ORDER BY page_id, id DESC)")
TITLE_SQL = "COALESCE(NULLIF(pt.title, ''), k.heading_path[1], k.source_file)"
# 위가 다 없으면(직접 입력) 내용 첫 줄 — "지식 #18"만 보이던 것(11/05 UI 검수). 제목 기호(#)는 뗀다
FIRST_LINE_SQL = "NULLIF(left(regexp_replace(split_part(btrim(k.content), E'\\n', 1), '^#+\\s*', ''), 60), '')"


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
             changed_pages: Iterable[str] = (), reported: Mapping[int, int] = {}) -> list[dict]:
    """검토 필요 페이지 — 원문 바뀜(changed_pages = 동기화 충돌 페이지 번호) 먼저, 그다음 기한 지난 것(채팅에 많이 쓰인 순 → 오래 지난 순).
    interval_days None = 기한은 안 봄(원문 바뀜은 그래도 보인다).
    used = 조각 id → 최근 채팅 답 수(같은 답이 한 페이지 조각 여럿을 써도 페이지 합은 조각 합 — 화면은 "쓰인 횟수"로 충분)."""
    changed = {f"p:{p}" for p in changed_pages}
    if not interval_days and not changed and not reported:
        return []
    groups: dict[str, list[Mapping]] = {}
    for r in rows:
        groups.setdefault(doc_key(r), []).append(r)
    out = []
    for key, rs in groups.items():
        last = min(last_touch(r) for r in rs)
        overdue = (now - (last + timedelta(days=interval_days))).days if interval_days else None
        source_changed = key in changed
        n_reported = sum(reported.get(r["id"], 0) for r in rs)   # 신고 건수(조각 수 아님, 리뷰 L6)
        if not source_changed and not n_reported and (overdue is None or last + timedelta(days=interval_days) >= now):
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
            "used": (u := sum(used.get(r["id"], 0) for r in rs)), "reported": n_reported,
            # 왜 올라왔나(11/05): 원문 바뀜 › 신고 › 많이 쓰임(기한 지남 + 채팅 USED_MIN회 이상) › quiet(기한만 — 목록엔 안 올림)
            "reason": "source_changed" if source_changed else "reported" if n_reported else "used" if u >= USED_MIN else "quiet",
        })
    rank = {"source_changed": 0, "reported": 1, "used": 2, "quiet": 3}
    out.sort(key=lambda d: (rank[d["reason"]], -d["used"], -(d["overdue_days"] or 0), d["key"]))
    return out


def pick_listed(docs: list[dict]) -> tuple[list[dict], int]:
    """목록에 올릴 것 = 원문 바뀜·신고 전부 + 많이 쓰임 위 USED_TOP개. 나머지 수(quiet + 많이 쓰임 넘친 것)도 돌려준다."""
    must = [d for d in docs if d["reason"] in ("source_changed", "reported")]
    used = [d for d in docs if d["reason"] == "used"][:USED_TOP]
    return must + used, len(docs) - len(must) - len(used)


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
            f"""WITH {PAGE_TITLE_CTE}
               SELECT k.id, k.confluence_page_id, k.source_document_id, k.ingestion_job_id, k.source_file,
                      COALESCE({TITLE_SQL}, {FIRST_LINE_SQL}) AS title, k.reviewed_at, k.content_edited_at, k.created_at, k.owner
               FROM rag_knowledge k LEFT JOIN page_title pt ON pt.page_id = k.confluence_page_id
               WHERE k.namespace_id = $1 AND k.status = 'active'""", ns["id"])
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
        # 신고 — 이 컬렉션 조각을 짚은 [답변 틀림 · 내용이 낡음] 정정 요청(대기 중). 신고 뒤에 그 조각을 검토했거나 고쳤으면 안 센다 —
        # [검토 완료]·바로 고치기를 해도 카드가 안 빠지던 것(리뷰 M1). 정정 요청 자체는 정정 요청 화면에서 처리
        reported = {r["target_id"]: r["n"] for r in await conn.fetch(
            "SELECT i.target_id, count(*) AS n FROM ops_improvement_item i JOIN rag_knowledge k ON k.id = i.target_id "
            "WHERE i.kind = 'correction' AND i.status = 'pending' AND i.target_type = 'knowledge' AND i.user_reason = 'stale' "
            "AND i.target_id = ANY($1::int[]) AND i.created_at > COALESCE(GREATEST(k.reviewed_at, k.content_edited_at), '-infinity') "
            "GROUP BY 1", ids)} if ids else {}
    docs = due_docs([dict(r) for r in rows], ns["review_interval_days"], now, used, changed, reported)
    listed, quiet = pick_listed(docs)
    return {"namespace": namespace, "interval_days": ns["review_interval_days"], "usage_days": USAGE_DAYS,
            "used_min": USED_MIN, "used_top": USED_TOP, "total_docs": len({doc_key(dict(r)) for r in rows}), "due": listed, "quiet": quiet}


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
