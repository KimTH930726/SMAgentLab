"""등록 묶음(job) 품질 검사 (2026-10-07) — 적재 직후 저장된 청크 자체를 본다.

검색 지표(hit@K)·답변 정확도로는 청크 경계 중복이 안 보인다(같은 내용이 두 청크에 있으면 오히려 잘 걸린다). 그래서 컨플루언스 일괄
등록 51청크 중 20쌍이 인접 청크와 같은 줄을 공유한 결함이 9/22부터 2주간 아무 지표에도 안 잡혔다. job이 끝날 때마다 저장된
청크를 구조로만(본문은 기록하지 않음) 검사해 rag_ingestion_job.quality에 남기고, 등록 진행 화면이 경고를 띄운다.
"""
import hashlib
import json
import logging
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

_MIN_LINE = 8   # 이보다 짧은 줄("- Y", "|---|")은 원래 여러 곳에 나올 수 있어 중복 판정에서 뺀다


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").split("\n") if ln.strip()]


def _is_heading(line: str) -> bool:
    return line.startswith("#")


def assess(chunks: Iterable[tuple[str, Optional[list]]]) -> dict:
    """[(본문, heading_path)] (저장 순서) → 집계. 경고 기준:
    - 인접 중복: 이웃 청크와 같은 본문 줄(제목 줄 제외 — 상위 제목은 하위 청크 맥락으로 반복될 수 있음)
    - 제목으로 끝남: 다음 섹션 제목이 앞 청크 끝에 매달림
    - 제목만: 본문 없는 청크"""
    items = [(c or "", list(hp or [])) for c, hp in chunks]
    body_sets = [{hashlib.md5(ln.encode()).hexdigest()
                  for ln in _lines(c) if not _is_heading(ln) and len(ln) >= _MIN_LINE} for c, _ in items]
    adjacent = sum(1 for a, b in zip(body_sets, body_sets[1:]) if a & b)
    trailing = sum(1 for c, _ in items if _lines(c) and _is_heading(_lines(c)[-1]) and any(not _is_heading(x) for x in _lines(c)))
    heading_only = sum(1 for c, _ in items if _lines(c) and all(_is_heading(x) for x in _lines(c)))
    with_path = sum(1 for _, hp in items if hp)
    warnings = []
    if adjacent:
        warnings.append(f"이웃 청크와 같은 내용이 겹친 곳 {adjacent}곳")
    if trailing:
        warnings.append(f"다음 제목이 끝에 매달린 청크 {trailing}개")
    if heading_only:
        warnings.append(f"제목만 있고 본문이 없는 청크 {heading_only}개")
    return {"chunks": len(items), "adjacent_overlap": adjacent, "trailing_heading": trailing,
            "heading_only": heading_only, "with_heading_path": with_path, "warnings": warnings}


async def record_job_quality(conn, job_id: int) -> dict:
    rows = await conn.fetch(
        "SELECT content, heading_path FROM rag_knowledge WHERE ingestion_job_id = $1 "
        "AND status IN ('active', 'pending_review') ORDER BY source_chunk_idx NULLS LAST, id", job_id)
    q = assess((r["content"], r["heading_path"]) for r in rows)
    await conn.execute("UPDATE rag_ingestion_job SET quality = $1::jsonb WHERE id = $2", json.dumps(q, ensure_ascii=False), job_id)
    if q["warnings"]:
        logger.warning("[ingestion quality] job %s: %s", job_id, " · ".join(q["warnings"]))
    return q
