"""정책서 임포트 파이프라인 — 엑셀 업로드 → 파싱 → LLM 분해 → RDB 적재.

docs/policy-doc-pipeline-plan.md §3 파이프라인, §2-1 버전 관리를 구현한다.

버전 관리(§2-1): 같은 (namespace, source_file, source_sheet, source_row)의 이전 버전과
content_hash를 비교한다. 같으면 재처리 없이 스킵(같은 파일 재업로드 시 불필요한 LLM 호출
방지). 다르면(내용 변경 또는 신규) — 절대 UPDATE하지 않고 새 policy_item row를 INSERT한다
(logical_id는 이전 row와 동일하게 유지, version+1, supersedes_id=이전 row). 이전 row는
status='deprecated'로 전환하되 삭제하지 않는다 — rag_knowledge 병합이 content를 그 자리에서
덮어써 이력이 소실되던 문제(knowledge-lifecycle-design.md 우선순위 1위)를 반복하지 않기 위함.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from core.database import get_conn, resolve_namespace_id
from shared.embedding import embedding_service
from service.policy import excel_parser, decompose
from agents.knowledge_rag.knowledge.service import create_glossary


def _content_hash(category_path: list[str], policy_name: str, raw_body: str, remark: str | None) -> str:
    payload = json.dumps(
        {"category_path": category_path, "policy_name": policy_name, "raw_body": raw_body, "remark": remark},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class SheetSummary:
    sheet_name: str
    kind: str
    created_items: int = 0
    new_versions: int = 0
    unchanged_skipped: int = 0
    params_extracted: int = 0
    narratives_extracted: int = 0
    unresolved_segments: int = 0
    glossary_added: int = 0
    glossary_duplicate_skipped: int = 0
    skip_reason: str | None = None


@dataclass
class ImportSummary:
    source_file: str
    sheets: list[SheetSummary] = field(default_factory=list)


async def _find_current_version(conn, ns_id: int, source_file: str, sheet_name: str, source_row: int):
    """같은 원본 위치(namespace/파일/시트/행)의 가장 최근 버전(deprecated 제외) row.
    없으면 None — 신규로 처리."""
    return await conn.fetchrow(
        """
        SELECT id, logical_id, version, content_hash FROM policy_item
        WHERE namespace_id = $1 AND source_file = $2 AND source_sheet = $3 AND source_row = $4
          AND status != 'deprecated'
        ORDER BY version DESC LIMIT 1
        """,
        ns_id, source_file, sheet_name, source_row,
    )


async def _ingest_policy_row(
    conn, ns_id: int, system_key: str, source_file: str, sheet: excel_parser.ParsedSheet,
    row: excel_parser.ParsedPolicyRow, summary: SheetSummary,
) -> None:
    new_hash = _content_hash(row.category_path, row.policy_name, row.raw_body, row.remark)
    current = await _find_current_version(conn, ns_id, source_file, sheet.sheet_name, row.source_row)

    if current is not None and current["content_hash"] == new_hash:
        summary.unchanged_skipped += 1
        return  # 내용 변경 없음 — 재처리(LLM 재호출 포함) 안 함

    logical_id = current["logical_id"] if current is not None else None
    version = (current["version"] + 1) if current is not None else 1
    supersedes_id = current["id"] if current is not None else None

    segments = await decompose.decompose_policy_body(row.policy_name, row.raw_body)
    has_unresolved = any(s.type == "unresolved" for s in segments)
    has_resolved = any(s.type in ("narrative", "param") for s in segments)
    parse_status = "unresolved" if not has_resolved else ("partial" if has_unresolved else "parsed")
    unresolved_payload = [
        {"text": s.text, "reason": s.reason} for s in segments if s.type == "unresolved"
    ] or None

    item_row = await conn.fetchrow(
        """
        INSERT INTO policy_item (
            namespace_id, system_key, category_path, policy_name, raw_body, remark,
            source_file, source_sheet, source_row, content_hash, status,
            logical_id, version, supersedes_id, parse_status, unresolved_segments
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'pending_review',$11,$12,$13,$14,$15::jsonb)
        RETURNING id, logical_id
        """,
        ns_id, system_key, row.category_path, row.policy_name, row.raw_body, row.remark,
        source_file, sheet.sheet_name, row.source_row, new_hash,
        logical_id, version, supersedes_id, parse_status,
        json.dumps(unresolved_payload, ensure_ascii=False) if unresolved_payload else None,
    )
    item_id = item_row["id"]

    if supersedes_id is not None:
        await conn.execute("UPDATE policy_item SET status = 'deprecated' WHERE id = $1", supersedes_id)
        summary.new_versions += 1
    else:
        summary.created_items += 1

    for seg in segments:
        if seg.type == "param" and seg.extracted:
            await conn.execute(
                """
                INSERT INTO policy_param (policy_item_id, name, condition, value, unit)
                VALUES ($1, $2, $3, $4, $5)
                """,
                item_id,
                str(seg.extracted.get("name") or "")[:500],
                str(seg.extracted.get("condition")) if seg.extracted.get("condition") is not None else None,
                str(seg.extracted.get("value")) if seg.extracted.get("value") is not None else None,
                str(seg.extracted.get("unit")) if seg.extracted.get("unit") is not None else None,
            )
            summary.params_extracted += 1
        elif seg.type == "narrative" and seg.text.strip():
            embedding = await embedding_service.embed(seg.text)
            await conn.execute(
                "INSERT INTO policy_chunk (policy_item_id, chunk_text, embedding, chunk_idx) VALUES ($1, $2, $3::vector, $4)",
                item_id, seg.text, str(embedding), summary.narratives_extracted,
            )
            summary.narratives_extracted += 1
        elif seg.type == "unresolved":
            summary.unresolved_segments += 1


async def _ingest_glossary_row(
    namespace: str, row: excel_parser.ParsedGlossaryRow, ns_id: int, conn, summary: SheetSummary,
) -> None:
    # §2-2 예외(일부 용어집 항목이 정의문 아닌 파라미터 팩트에 가까움, 예: "결제완료=상태코드11")는
    # v1에서 별도 분류 없이 전부 rag_glossary로 보낸다 — policy_param은 policy_item FK가 필수라
    # 용어집 단독으로는 넣을 자리가 없고, 이 소수 사례를 위해 v1 스코프를 늘리지 않는다(과설계 방지,
    # 필요하면 검토 UI 도입 시 재분류).
    try:
        await create_glossary(namespace, row.term, row.description)
        summary.glossary_added += 1
    except ValueError:
        summary.glossary_duplicate_skipped += 1


async def import_excel(namespace: str, system_key: str, filename: str, file_bytes: bytes) -> ImportSummary:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
    if ns_id is None:
        raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")

    sheets = excel_parser.parse_workbook(file_bytes)
    result = ImportSummary(source_file=filename)

    for sheet in sheets:
        summary = SheetSummary(sheet_name=sheet.sheet_name, kind=sheet.kind, skip_reason=sheet.skip_reason)
        if sheet.kind == "glossary":
            async with get_conn() as conn:
                for row in sheet.glossary_rows:
                    await _ingest_glossary_row(namespace, row, ns_id, conn, summary)
        elif sheet.kind == "policy":
            async with get_conn() as conn:
                for row in sheet.policy_rows:
                    await _ingest_policy_row(conn, ns_id, system_key, filename, sheet, row, summary)
        result.sheets.append(summary)

    return result
