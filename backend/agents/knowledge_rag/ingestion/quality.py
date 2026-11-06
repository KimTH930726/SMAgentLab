"""등록 묶음(job) 품질 검사 (2026-10-07) — 적재 직후 저장된 청크 자체를 본다.

검색 지표(hit@K)·답변 정확도로는 청크 경계 중복이 안 보인다(같은 내용이 두 청크에 있으면 오히려 잘 걸린다). 그래서 컨플루언스 일괄
등록 51청크 중 20쌍이 인접 청크와 같은 줄을 공유한 결함이 9/22부터 2주간 아무 지표에도 안 잡혔다. job이 끝날 때마다 저장된
청크를 구조로만(본문은 기록하지 않음) 검사해 rag_ingestion_job.quality에 남기고, 등록 진행 화면이 경고를 띄운다.
"""
import asyncio
import json
import logging
from typing import Optional

from trustkit.ingest.quality import assess   # 검사 본체(신뢰도 레이어, 2026-10-19 이동) — 여기는 job 기록(DB)만

logger = logging.getLogger(__name__)


JOB_CHUNKS_SQL = ("SELECT id, content, heading_path, source_document_id FROM rag_knowledge WHERE ingestion_job_id = $1 "
                  "AND status IN ('active', 'pending_review', 'staging', 'staging_review') ORDER BY source_chunk_idx NULLS LAST, id")   # 보류(게시 전) 작업도 검수용으로


async def job_source_text(conn, rows) -> Optional[str]:
    """이 작업 조각들의 원문 글(원장 원문 → 적재와 같은 파서, source_ledger.rebuild_parsed). 원문 연결이 없는 조각이 하나라도 있거나
    다시 읽기에 실패하면 None — 검사는 예전 기준(원문 반복도 결함으로 셈)으로 하고 결과에 source_checked=False가 남는다(대체를 보이게)."""
    from agents.knowledge_rag.ingestion.source_ledger import rebuild_parsed
    if not rows or any(not r["source_document_id"] for r in rows):
        return None
    doc_ids = sorted({r["source_document_id"] for r in rows})
    texts = []
    for d in await conn.fetch("SELECT id, source_type, source_ref, title, parent_title, raw_content, source_version, media FROM rag_source_document "
                              "WHERE id = ANY($1::int[]) ORDER BY id", doc_ids):
        try:
            # 파싱(BeautifulSoup)은 CPU 일 — 이벤트 루프를 막지 않게 스레드로(리뷰 6)
            texts.append((d["raw_content"] or "") if d["source_type"] == "paste" else (await asyncio.to_thread(rebuild_parsed, dict(d))).raw_text)
        except Exception:   # noqa: BLE001 — 원문 다시 읽기 실패는 대조 없이(예전 기준) 검사, 로그
            logger.warning("품질 검사: 원문 %s 다시 읽기 실패 — 원문 대조 없이 검사", d["id"], exc_info=True)
            return None
    return "\n".join(texts)


async def record_job_quality(conn, job_id: int) -> dict:
    rows = await conn.fetch(JOB_CHUNKS_SQL, job_id)
    q = assess(((r["content"], r["heading_path"]) for r in rows), source_text=await job_source_text(conn, rows))
    for p in q["overlap_pairs"]:   # 쌍 = 조각 id(본문 아님) — 펼칠 때 순서가 바뀌어도 같은 쌍을 찾는다(리뷰 3)
        p["ids"] = [rows[p["i"]]["id"], rows[p["i"] + 1]["id"]]
    await conn.execute("UPDATE rag_ingestion_job SET quality = $1::jsonb WHERE id = $2", json.dumps(q, ensure_ascii=False), job_id)
    if q["warnings"]:
        logger.warning("[ingestion quality] job %s: %s", job_id, " · ".join(q["warnings"]))
    return q
