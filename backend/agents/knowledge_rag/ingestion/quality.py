"""등록 묶음(job) 품질 검사 (2026-10-07) — 적재 직후 저장된 청크 자체를 본다.

검색 지표(hit@K)·답변 정확도로는 청크 경계 중복이 안 보인다(같은 내용이 두 청크에 있으면 오히려 잘 걸린다). 그래서 컨플루언스 일괄
등록 51청크 중 20쌍이 인접 청크와 같은 줄을 공유한 결함이 9/22부터 2주간 아무 지표에도 안 잡혔다. job이 끝날 때마다 저장된
청크를 구조로만(본문은 기록하지 않음) 검사해 rag_ingestion_job.quality에 남기고, 등록 진행 화면이 경고를 띄운다.
"""
import json
import logging

from trustkit.ingest.quality import assess   # 검사 본체(신뢰도 레이어, 2026-10-19 이동) — 여기는 job 기록(DB)만

logger = logging.getLogger(__name__)


async def record_job_quality(conn, job_id: int) -> dict:
    rows = await conn.fetch(
        "SELECT content, heading_path FROM rag_knowledge WHERE ingestion_job_id = $1 "
        "AND status IN ('active', 'pending_review') ORDER BY source_chunk_idx NULLS LAST, id", job_id)
    q = assess((r["content"], r["heading_path"]) for r in rows)
    await conn.execute("UPDATE rag_ingestion_job SET quality = $1::jsonb WHERE id = $2", json.dumps(q, ensure_ascii=False), job_id)
    if q["warnings"]:
        logger.warning("[ingestion quality] job %s: %s", job_id, " · ".join(q["warnings"]))
    return q
