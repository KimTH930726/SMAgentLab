"""동기화로 바뀐 페이지(11/05 태훈 "증분된 거 사이트 들어가 보거나 diff로 볼 수 있나") — 원문 원장의 옛 판(superseded) ↔ 새 판(active).
옛 판 = 같은 컬렉션·같은 페이지에서 새 판보다 먼저 만든 교체된 판 중 가장 최근(원장이 superseded_by를 채우지 않아 547건 모두 비어 있음, 11/05 실측).
본문 없는 판(읽기 제한 기록 등 raw_content '')은 비교하지 않는다. 컬렉션을 옮긴 페이지는 옮긴 컬렉션의 판끼리만 비교한다(옮김 자체는 변경이 아님).

업계: Confluence 페이지 기록의 '변경 사항 비교'. 많을 때를 먼저 생각해(수백 장) — 바뀐 줄 수 큰 순, 채팅에 쓰인 페이지만 거르기,
충돌(사람이 고친 조각이 있어 반영 안 함)은 따로. 목록은 줄 수만(본문 없음), 차이는 한 페이지씩 누를 때만.
범위 = 이 연결이 실제로 넣은 (페이지, 컬렉션) 쌍(같은 팀·주소의 적재 묶음, 적재된 페이지) — 같은 페이지가 다른 컬렉션에도 있으면 섞지 않는다(리뷰 M1).
"""
from __future__ import annotations

import asyncio
import difflib
import re
from html import unescape
from typing import Optional

from core.database import get_conn

_CELL = re.compile(r"</t[dh]>\s*<t[dh]\b[^>]*>", re.I)   # 표 칸 경계 — 붙어 나오지 않게(리뷰 L3)
_BLOCK = re.compile(r"</?(p|div|li|tr|h[1-6]|br|table|ul|ol|pre|blockquote)\b[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")
USAGE_DAYS = 30
MAX_PAGES = 300   # 한 번에 비교하는 페이지 수 상한(최근 반영 순) — 요청이 원문 수백 MB를 읽지 않게(리뷰 M3)


def text_lines(raw: Optional[str], fmt: Optional[str]) -> list[str]:
    """원장 원문 → 비교용 줄(공백만 다른 줄은 같게). html은 블록 경계에서 줄을 끊고, 표 칸은 " | "로, 태그는 뗀다."""
    s = raw or ""
    if fmt == "html":
        s = unescape(_TAG.sub("", _BLOCK.sub("\n", _CELL.sub(" | ", s))))
    return [" ".join(x.split()) for x in s.splitlines() if x.strip()]


def diff_counts(old: list[str], new: list[str]) -> tuple[int, int]:
    """(더한 줄, 뺀 줄) — 줄 단위(고친 줄은 하나 빼고 하나 더함)."""
    added = removed = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            removed += i2 - i1
        if op in ("replace", "insert"):
            added += j2 - j1
    return added, removed


# 이 연결이 넣은 (페이지, 컬렉션) — 적재된 페이지만, 취소한 묶음 빼고
_SCOPE_SQL = """SELECT p.page_id, p.final_namespace_id FROM rag_import_page p JOIN rag_import_batch b ON b.id = p.batch_id
                WHERE b.team_id = $1 AND b.root_url = $2 AND b.status <> 'cancelled' AND p.status = 'loaded'"""
_OLD_LATERAL = """JOIN LATERAL (SELECT x.id, x.raw_content, x.raw_format FROM rag_source_document x
                              WHERE x.namespace_id = n.namespace_id AND x.source_ref = n.source_ref AND x.source_type = 'confluence'
                                AND x.status = 'superseded' AND x.id < n.id AND COALESCE(x.raw_content, '') <> ''
                              ORDER BY x.id DESC LIMIT 1) o ON TRUE"""


def _count_all(rows: list) -> list[tuple[int, int]]:
    return [diff_counts(text_lines(r["old_raw"], r["old_fmt"]), text_lines(r["new_raw"], r["new_fmt"])) for r in rows]


async def list_changes(c, days: int) -> dict:
    """c = 연결 줄(team_id, root_url, retry_pages). → {items(바뀐 줄 수 큰 순, 내용 같은 판 제외), conflicts(반영 안 한 페이지)}"""
    async with get_conn() as conn:
        rows = await conn.fetch(
            f"""SELECT n.id AS new_id, o.id AS old_id, n.source_ref AS page_id, n.namespace_id, n.title, n.activated_at,
                       n.raw_content AS new_raw, n.raw_format AS new_fmt, o.raw_content AS old_raw, o.raw_format AS old_fmt,
                       ns.name AS namespace
                FROM rag_source_document n {_OLD_LATERAL}
                JOIN ops_namespace ns ON ns.id = n.namespace_id
                WHERE n.source_type = 'confluence' AND n.status = 'active'
                  AND n.activated_at > NOW() - make_interval(days => $3)
                  AND (n.source_ref, n.namespace_id) IN ({_SCOPE_SQL})
                ORDER BY n.activated_at DESC LIMIT {MAX_PAGES}""", c["team_id"], c["root_url"], days)
        # 채팅에 쓰인 횟수 = 그 페이지(같은 컬렉션)의 조각이 최근 근거로 쓰인 답 수 — 옛 판 조각도 센다(동기화 직후엔 근거가 대부분 옛 판, 리뷰 M2)
        keys = [(r["namespace_id"], r["page_id"]) for r in rows]
        used = {(r["ns"], r["pid"]): r["n"] for r in await conn.fetch(
            "SELECT k.namespace_id AS ns, k.confluence_page_id AS pid, count(DISTINCT m.id) AS n FROM ops_message m "
            "CROSS JOIN LATERAL jsonb_array_elements(m.results) e JOIN rag_knowledge k ON (e->>'id') ~ '^[0-9]+$' AND k.id = (e->>'id')::int "
            "WHERE m.role = 'assistant' AND m.created_at > NOW() - make_interval(days => $3) AND jsonb_typeof(m.results) = 'array' "
            "AND (k.namespace_id, k.confluence_page_id) IN (SELECT * FROM unnest($1::int[], $2::text[])) GROUP BY 1, 2",
            [k[0] for k in keys], [k[1] for k in keys], USAGE_DAYS)} if keys else {}
    counts = await asyncio.to_thread(_count_all, rows)   # 큰 표 페이지 비교가 이벤트 루프(채팅 SSE 등)를 막지 않게(리뷰 M3)
    items = []
    for r, (a, d) in zip(rows, counts):
        if a == 0 and d == 0:   # 다시 적재 등으로 내용 같은 새 판 — 변경이 아니다(리뷰 L1)
            continue
        items.append({"new_id": r["new_id"], "old_id": r["old_id"], "page_id": r["page_id"], "title": r["title"], "namespace": r["namespace"],
                      "activated_at": r["activated_at"].isoformat() if r["activated_at"] else None,
                      "added": a, "removed": d, "used": used.get((r["namespace_id"], r["page_id"]), 0)})
    items.sort(key=lambda x: (-(x["added"] + x["removed"]), x["page_id"]))
    conflicts = [{"page_id": p.get("page_id"), "title": p.get("title")} for p in (c["retry_pages"] or []) if p.get("why") == "conflict"]
    return {"days": days, "usage_days": USAGE_DAYS, "items": items, "conflicts": conflicts, "capped": len(rows) >= MAX_PAGES}


async def change_detail(c, new_id: int) -> Optional[dict]:
    """한 페이지의 옛 판 ↔ 새 판 글(비교 화면용). 이 연결이 넣은 사용 중 판이 아니면 None."""
    async with get_conn() as conn:
        r = await conn.fetchrow(
            f"""SELECT n.title, n.source_ref AS page_id, n.raw_content AS new_raw, n.raw_format AS new_fmt,
                       o.raw_content AS old_raw, o.raw_format AS old_fmt
                FROM rag_source_document n {_OLD_LATERAL}
                WHERE n.id = $3 AND n.source_type = 'confluence' AND n.status = 'active'
                  AND (n.source_ref, n.namespace_id) IN ({_SCOPE_SQL})""", c["team_id"], c["root_url"], new_id)
    if r is None:
        return None
    before, after = await asyncio.to_thread(lambda: ("\n".join(text_lines(r["old_raw"], r["old_fmt"])), "\n".join(text_lines(r["new_raw"], r["new_fmt"]))))
    return {"title": r["title"], "page_id": r["page_id"], "before": before, "after": after}
