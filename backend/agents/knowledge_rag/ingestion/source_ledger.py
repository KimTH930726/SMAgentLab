"""지식 원문 원장 (2026-10-08, 업계 표준 방식 — 원문 = SoT, 청크·임베딩 = 언제든 다시 만드는 파생물).

계획: docs/tech/source-ledger-plan.md. 배경: 10/07 적재 결함 때 다시 자를 원문이 없어 저장 청크에서 원문을 복원하는 우회로
보정했다(추출 단계에서 사라진 내용은 그 방법으로도 못 살림).

흐름: 미리보기 때 원문을 초안으로 저장 → 확정(/bulk) 때 청크에 연결·"사용 중", 같은 원본의 이전 원문은 "교체됨"(그 청크는 bulk가
폐기) → 결함이 나면 `rechunk`로 원장의 원문을 지금 코드로 다시 분할.

"같은 원본" 판단(코드 리뷰 2026-10-08 — 이름만 같은 다른 문서가 서로를 조용히 폐기하던 것 수정):
  - 위치가 확실한 원본(컨플루언스 페이지 ID·웹 URL): 같은 위치면 같은 원본(내용이 바뀌었어도 새 버전으로 교체)
  - 그 밖(파일·붙여넣기): 위치(파일명)만으론 모른다 — 위치와 내용(해시)이 모두 같을 때만 같은 원본(그대로 다시 올린 경우)
초안 재사용도 아직 등록 전 초안끼리, 종류·위치·내용이 모두 같을 때만 — 사용 중 원문의 분할 방식을 미리보기가 덮지 않게.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

from agents.knowledge_rag.ingestion.adapters import ParsedDocument, extract_heading_sections

logger = logging.getLogger(__name__)

PARSER_VERSION = "v2.130"   # 파싱·분할 코드 판 — 결함 난 판으로 만든 원문·청크를 찾을 때(분할 코드를 고치면 올린다)
STABLE_REF_TYPES = ("confluence", "web")   # 위치만으로 같은 원본이라 볼 수 있는 종류


def content_hash(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


async def save_draft(conn, *, ns_id: int, source_type: str, source_ref: Optional[str], title: Optional[str],
                     parent_title: Optional[str], raw_format: str, raw_content: str, chunk_strategy: Optional[str],
                     source_version: Optional[int] = None, user_id: Optional[int] = None) -> int:
    """미리보기 원문을 초안으로 저장. 같은 파트·종류·위치·내용·분할 방식의 **초안**이 있으면 그 id.

    분할 방식도 같아야 재사용(리뷰 M3 2026-10-12): 예전엔 같은 원문을 다른 방식으로 다시 미리보면 초안의 방식을 덮어,
    먼저 본 미리보기로 확정하면 원장의 방식과 실제 조각이 어긋났다(다시 분할이 바뀐 것 없는데 "바뀜"으로 교체)."""
    raw_content = (raw_content or "").replace("\x00", "")   # 청크 저장과 같게(PostgreSQL이 NUL을 거부)
    h = content_hash(raw_content)
    row = await conn.fetchrow(
        "SELECT id FROM rag_source_document WHERE namespace_id = $1 AND status = 'draft' AND source_type = $2 "
        "AND source_ref IS NOT DISTINCT FROM $3 AND content_hash = $4 AND chunk_strategy IS NOT DISTINCT FROM $5 "
        "ORDER BY id DESC LIMIT 1",
        ns_id, source_type, source_ref, h, chunk_strategy)
    if row:
        # 제목·상위 제목·원본 판은 최신 미리보기 값으로(리뷰 L3 — 내용이 같아도 페이지 이름·판이 바뀔 수 있다)
        await conn.execute("UPDATE rag_source_document SET title = COALESCE($2, title), parent_title = $3, "
                           "source_version = COALESCE($4, source_version) WHERE id = $1",
                           row["id"], title, parent_title, source_version)
        return row["id"]
    return await conn.fetchval("""
        INSERT INTO rag_source_document
            (namespace_id, source_type, source_ref, title, parent_title, raw_format, raw_content, content_hash,
             source_version, chunk_strategy, parser_version, status, created_by_user_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'draft', $12) RETURNING id
    """, ns_id, source_type, source_ref, title, parent_title, raw_format, raw_content, h, source_version, chunk_strategy,
        PARSER_VERSION, user_id)


async def save_draft_for_parsed(conn, ns_id: int, doc: ParsedDocument, *, chunk_strategy: Optional[str],
                                user_id: Optional[int], source_ref: Optional[str] = None) -> Optional[int]:
    """파서 결과(ParsedDocument)에서 원문을 골라 초안 저장. 원문이 없는 형식(xlsx·csv — 행 자체가 지식)은 None."""
    raw_html = doc.metadata.get("raw_html")
    if doc.source_type in ("confluence", "web") and raw_html:
        fmt, raw = "html", raw_html
    elif doc.source_type in ("xlsx", "csv"):
        return None
    else:
        fmt, raw = "text", doc.raw_text
    if not (raw or "").strip():
        return None
    ref = source_ref or doc.metadata.get("page_id") or doc.metadata.get("url") or doc.source_name
    return await save_draft(conn, ns_id=ns_id, source_type=doc.source_type, source_ref=str(ref) if ref else None,
                            title=doc.metadata.get("title") or doc.source_name, parent_title=doc.metadata.get("parent_title"),
                            raw_format=fmt, raw_content=raw, chunk_strategy=chunk_strategy,
                            source_version=doc.metadata.get("version"), user_id=user_id)


async def valid_documents(conn, ns_id: int, doc_ids: list[int]) -> list[int]:
    """확정 요청에 실린 원문 id 중 이 파트의 초안·사용 중인 것만(리뷰: 조작된 요청이나 오래 열어둔 미리보기 탭이 다른 파트 원문이나
    이미 교체된 원문을 되살리지 못하게)."""
    if not doc_ids:
        return []
    rows = await conn.fetch("SELECT id FROM rag_source_document WHERE id = ANY($1::int[]) AND namespace_id = $2 "
                            "AND status IN ('draft', 'active')", doc_ids, ns_id)
    return sorted(r["id"] for r in rows)


async def replacing_documents(conn, ns_id: int, doc_ids: list[int], page_ids: list[str] = ()) -> list[int]:
    """이 등록이 대체하는 원문(자기 자신 포함 — 다시 분할) — 그 원문들의 기존 청크는 옛 버전이라 중복 비교에서 빼고 전환 때 폐기.

    page_ids: 이 등록에 실린 컨플루언스 페이지 — 그 페이지의 사용 중 원문도 대체 대상(리뷰 2026-10-12: 요청의 원문 id가 무효로 끊겨도
    페이지 교체는 그 페이지 조각을 전부 폐기하는데, 확인은 그 원문을 안 봐서 사람이 고친 조각이 확인 없이 사라질 수 있었다)."""
    doc_ids, page_ids = list(doc_ids or []), list(page_ids or [])
    if not doc_ids and not page_ids:
        return []
    rows = await conn.fetch("""
        SELECT DISTINCT o.id FROM rag_source_document o JOIN rag_source_document n ON n.id = ANY($2::int[])
        WHERE o.namespace_id = $1 AND n.namespace_id = $1 AND o.source_type = n.source_type AND (
              o.id = n.id
           OR (n.source_type = ANY($3::text[]) AND o.source_ref IS NOT NULL AND o.source_ref = n.source_ref)
           OR (o.source_ref IS NOT DISTINCT FROM n.source_ref AND o.content_hash = n.content_hash))
        UNION
        SELECT id FROM rag_source_document WHERE namespace_id = $1 AND source_type = 'confluence' AND status = 'active'
          AND source_ref = ANY($4::text[])
    """, ns_id, doc_ids, list(STABLE_REF_TYPES), page_ids)
    return sorted(r["id"] for r in rows)


def has_touch(reasons: dict) -> bool:
    return bool(reasons.get("edited") or reasons.get("removed") or reasons.get("partial") or reasons.get("mixed_category"))


async def touched_reasons(conn, ns_id: int, doc_ids: list[int], page_ids: list[str] = ()) -> dict:
    """원문(doc_ids)의 지금 조각에 사람이 손댄 흔적 — 교체(재등록·다시 분할)가 조용히 덮으면 안 되는 것(A안 2026-10-12).

    page_ids: 원장 이전에 등록된 컨플루언스 페이지 조각(원문 연결 없음) — 지금 데이터 대부분이 이것이라 같이 본다(고친 조각만 —
    v2.129 이전엔 조각마다 업무구분을 LLM이 따로 정해, 업무구분이 섞인 게 사람 손이 아니다).
    removed는 지금 "사용 중"인 원문에서, 사용 중이 된 뒤 빠진 조각만(리뷰 L2 — 이미 교체된 옛 원문이나 다시 분할 전 등록분의
    삭제가 다음 재등록마다 계속 걸리던 것. 실서버 시나리오에서 교체 직후 재등록이 또 확인을 묻는 것으로 발견).
    """
    doc_ids, page_ids = list(doc_ids or []), list(page_ids or [])
    if not doc_ids and not page_ids:
        return {"edited": 0, "removed": 0, "partial": False, "mixed_category": False}
    scope = ("k.namespace_id = $1 AND (k.source_document_id = ANY($2::int[]) "
             "OR (k.source_document_id IS NULL AND k.confluence_page_id = ANY($3::text[])))")
    row = await conn.fetchrow(f"""
        SELECT count(*) FILTER (WHERE k.status IN ('active', 'pending_review')
                                  AND (k.content_edited_at IS NOT NULL OR k.supersedes_id IS NOT NULL)) AS edited,
               (SELECT count(DISTINCT x.category) FROM rag_knowledge x
                WHERE x.namespace_id = $1 AND x.source_document_id = ANY($2::int[])
                  AND x.status IN ('active', 'pending_review') AND x.category IS NOT NULL
                GROUP BY x.source_document_id ORDER BY 1 DESC LIMIT 1) AS max_categories
        FROM rag_knowledge k WHERE {scope}
    """, ns_id, doc_ids, page_ids)
    removed = await conn.fetchval("""
        SELECT count(*) FROM rag_knowledge k JOIN rag_source_document d ON d.id = k.source_document_id
        WHERE k.namespace_id = $1 AND d.id = ANY($2::int[]) AND k.status IN ('deleted', 'rejected')
          AND d.status = 'active' AND k.updated_at > d.activated_at
    """, ns_id, doc_ids) if doc_ids else 0
    partial = await conn.fetchval("SELECT bool_or(registered_partial) FROM rag_source_document "
                                  "WHERE namespace_id = $1 AND id = ANY($2::int[]) AND status = 'active'",
                                  ns_id, doc_ids) if doc_ids else False
    return {"edited": int(row["edited"] or 0), "removed": int(removed or 0), "partial": bool(partial),
            "mixed_category": int(row["max_categories"] or 0) > 1}   # 한 원문 안에서 조각마다 업무구분을 다르게 둠


async def lock_sources(conn, ns_id: int, doc_ids: list[int], page_ids: list[str] = ()) -> None:
    """같은 원본(파트·종류·위치)의 확정을 한 번에 하나씩 — 트랜잭션 안에서만(리뷰 M2 2026-10-12).
    다른 초안 두 개로 같은 페이지를 동시에 확정하면 둘 다 "사용 중"이 돼 조각이 중복됐다. 원문 없이 페이지만 실린 등록도 같은 키."""
    if not doc_ids and not page_ids:
        return
    keys = await conn.fetch("""
        SELECT DISTINCT hashtext(namespace_id::text || '|' || source_type || '|' || COALESCE(source_ref, content_hash)) AS k
        FROM rag_source_document WHERE id = ANY($1::int[])
        UNION SELECT hashtext($2::text || '|confluence|' || p) FROM unnest($3::text[]) AS p
        ORDER BY 1""", list(doc_ids or []), str(ns_id), list(page_ids or []))
    for r in keys:   # 정렬 순서로 잡아 교착 방지
        await conn.execute("SELECT pg_advisory_xact_lock(7301, $1)", r["k"])


async def replace_check(conn, ns_id: int, doc_ids: list[int], page_ids: list[str] = ()) -> dict:
    """이 등록이 교체할 기존 조각에 사람 손이 닿은 흔적(A안). 흔적이 없으면 바로 교체해도 된다."""
    valid = await valid_documents(conn, ns_id, doc_ids)
    replacing = await replacing_documents(conn, ns_id, valid, page_ids)
    reasons = await touched_reasons(conn, ns_id, replacing, page_ids)
    return {**reasons, "needs_confirm": has_touch(reasons), "unlinked_sources": sorted(set(doc_ids or []) - set(valid))}


async def activate(conn, ns_id: int, doc_ids: list[int], replaced: list[int], partial_ids: list[int]) -> None:
    """확정된 원문 → "사용 중", 대체된 이전 원문 → "교체됨". 등록 때 일부 조각만 고른 원문은 표시(다시 분할이 뺀 조각을 되살리지 않게)."""
    if not doc_ids:
        return
    await conn.execute("UPDATE rag_source_document SET status = 'active', activated_at = NOW(), "
                       "registered_partial = (id = ANY($3::int[])) WHERE id = ANY($1::int[]) AND namespace_id = $2",
                       doc_ids, ns_id, partial_ids)
    old = [d for d in replaced if d not in doc_ids]
    if old:
        # 사용 중이던 것만 — 같은 원본의 다른 초안은 다른 등록이 아직 쓰는 중일 수 있다(동시 확정 시 그 등록의 조각이 원문 연결 없이
        # 중복으로 살아남던 것, 실서버 동시 시나리오로 발견 2026-10-12). 그 등록이 끝나면 이 원문을 교체한다.
        await conn.execute("UPDATE rag_source_document SET status = 'superseded' WHERE id = ANY($1::int[]) AND namespace_id = $2 "
                           "AND status = 'active'", old, ns_id)


def rebuild_parsed(doc_row) -> ParsedDocument:
    """원장의 원문 → 미리보기와 **같은 파서 함수**로 ParsedDocument(리뷰: 웹·PDF가 미리보기와 달랐다)."""
    st, raw = doc_row["source_type"], doc_row["raw_content"] or ""
    title = doc_row["title"] or ""
    if st == "confluence":
        from bs4 import BeautifulSoup
        from agents.knowledge_rag.ingestion import web_crawler as w
        soup = BeautifulSoup(w.prepare_storage_html(raw), "lxml")
        return ParsedDocument(source_type=st, source_name=title, raw_text=w._extract_text(soup),
                              sections=w._extract_heading_sections(soup),
                              metadata={"title": title, "parent_title": doc_row["parent_title"], "page_id": doc_row["source_ref"],
                                        "version": doc_row["source_version"], "raw_html": raw})
    if st == "web":
        from agents.knowledge_rag.ingestion.web_crawler import parse_web_html
        return parse_web_html(raw, doc_row["source_ref"] or "")
    from agents.knowledge_rag.ingestion import adapters as a
    if st == "md":
        return a.parse_markdown(raw, title or "doc.md")
    if st == "pdf":   # parse_pdf와 같게: 추출 텍스트에서 섹션(제목 개수 거름 없음)
        return ParsedDocument(source_type="pdf", source_name=title, raw_text=raw, sections=extract_heading_sections(raw))
    return a.parse_text(raw, title or "doc.txt")   # txt 등


def rechunk_items(doc_row) -> list[dict]:
    """원장의 원문을 지금 코드로 다시 분할 → bulk 등록 항목(내용·상위 맥락). 미리보기 때와 같은 분할 방식."""
    from agents.knowledge_rag.ingestion.chunker import chunk_document
    if doc_row["source_type"] == "paste":
        from agents.knowledge_rag.knowledge.service import split_text_to_items
        return [{"content": x["content"], "heading_path": x["heading_path"] or None}
                for x in split_text_to_items(doc_row["raw_content"] or "", doc_row["chunk_strategy"] or "auto")]
    doc = rebuild_parsed(doc_row)
    out = []
    for c in chunk_document(doc, strategy=doc_row["chunk_strategy"] or "auto"):
        hp = list(c.heading_path or [])
        if doc_row["source_type"] == "confluence":
            from agents.knowledge_rag.knowledge.router import _enrich_heading_path
            hp = _enrich_heading_path(doc, hp, doc_row["title"])
        out.append({"content": c.text.replace("\x00", ""), "heading_path": hp or None})
    return out
