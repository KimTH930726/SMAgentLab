"""적재 품질 검사 — 저장된 청크 자체를 구조로 본다(본문은 기록하지 않음).

검색 지표·답 정확도로는 청크 경계 중복이 안 보인다(같은 내용이 두 청크에 있으면 오히려 잘 걸린다). OpsLens 실사고(2026-09-22~10-07):
컨플루언스 51청크 중 20쌍이 이웃 청크와 같은 줄을 공유했는데 2주간 어떤 지표에도 안 잡혔다. 적재 묶음이 끝날 때마다 이 검사를 돌려
경고를 남기고 화면에 띄운다.
"""
from __future__ import annotations

import hashlib
from typing import Iterable, Optional

MIN_LINE = 8   # 이보다 짧은 줄("- Y", "|---|")은 원래 여러 곳에 나올 수 있어 중복 판정에서 뺀다


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").split("\n") if ln.strip()]


def _is_heading(line: str) -> bool:
    return line.startswith("#")


def assess(chunks: Iterable[tuple[str, Optional[list]]]) -> dict:
    """[(본문, 제목 경로)] (저장 순서) → 집계. 경고 기준:
    - 인접 중복: 이웃 청크와 같은 본문 줄(제목 줄 제외 — 상위 제목은 하위 청크 맥락으로 반복될 수 있음)
    - 제목으로 끝남: 다음 섹션 제목이 앞 청크 끝에 매달림
    - 제목만: 본문 없는 청크"""
    items = [(c or "", list(hp or [])) for c, hp in chunks]
    body_sets = [{hashlib.md5(ln.encode()).hexdigest()
                  for ln in _lines(c) if not _is_heading(ln) and len(ln) >= MIN_LINE} for c, _ in items]
    adjacent = sum(1 for a, b in zip(body_sets, body_sets[1:]) if a & b)
    lines = [_lines(c) for c, _ in items]
    trailing = sum(1 for ls in lines if ls and _is_heading(ls[-1]) and any(not _is_heading(x) for x in ls))
    heading_only = sum(1 for ls in lines if ls and all(_is_heading(x) for x in ls))
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
