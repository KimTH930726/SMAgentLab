"""적재 품질 검사 — 저장된 청크 자체를 구조로 본다(본문은 기록하지 않음).

검색 지표·답 정확도로는 청크 경계 중복이 안 보인다(같은 내용이 두 청크에 있으면 오히려 잘 걸린다). OpsLens 실사고(2026-09-22~10-07):
컨플루언스 51청크 중 20쌍이 이웃 청크와 같은 줄을 공유했는데 2주간 어떤 지표에도 안 잡혔다. 적재 묶음이 끝날 때마다 이 검사를 돌려
경고를 남기고 화면에 띄운다.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from typing import Iterable, Optional

MIN_LINE = 8   # 이보다 짧은 줄("- Y", "|---|")은 원래 여러 곳에 나올 수 있어 중복 판정에서 뺀다


MAX_LINES_PER_PAIR = 20   # 큰 반복 표가 기록을 키우지 않게 — 쌍마다 결함 줄 먼저 이만큼(리뷰 7)


def text_lines(text: str) -> list[str]:
    """검사 단위 줄 — 앞뒤 공백 뺀 빈 줄 아닌 줄(화면이 해시로 줄을 다시 찾을 때도 같은 규칙)."""
    return [ln.strip() for ln in (text or "").split("\n") if ln.strip()]


_lines = text_lines


def _is_heading(line: str) -> bool:
    return line.startswith("#")


def line_hash(line: str) -> str:
    """줄 식별(기록용) — 본문 대신 남기고, 화면은 조각에서 같은 해시의 줄을 다시 찾아 보여 준다."""
    return hashlib.md5(line.encode()).hexdigest()[:12]


def assess(chunks: Iterable[tuple[str, Optional[list]]], source_text: Optional[str] = None, max_pairs: int = 100) -> dict:
    """[(본문, 제목 경로)] (저장 순서) → 집계. 경고 기준:
    - 인접 중복: 이웃 청크와 같은 본문 줄(제목 줄 제외 — 상위 제목은 하위 청크 맥락으로 반복될 수 있음).
      source_text(분할 전 원문 글)가 있으면 **조각 전체에 원문보다 많이 나온 줄**만 결함으로 센다 — 원문이 원래 같은 줄을
      여러 번 쓰는 경우(체크리스트 표의 같은 항목)는 분할 결함이 아니다(11/05 앱 공간 77곳 중 원문보다 많은 줄 0). 없으면 예전 기준.
    - 제목으로 끝남: 다음 섹션 제목이 앞 청크 끝에 매달림
    - 제목만: 본문 없는 청크"""
    items = [(c or "", list(hp or [])) for c, hp in chunks]
    lines = [_lines(c) for c, _ in items]
    body = [{ln for ln in ls if not _is_heading(ln) and len(ln) >= MIN_LINE} for ls in lines]
    in_chunks = Counter(ln for ls in lines for ln in ls)
    in_source = Counter(_lines(source_text)) if source_text is not None else None
    adjacent = repeat = 0
    pairs, types = [], Counter()
    for i, (a, b) in enumerate(zip(body, body[1:])):
        common = sorted(a & b)
        if not common:
            continue
        info = []
        for ln in common:
            extra = in_source is None or in_chunks[ln] > in_source[ln]
            types["table" if ln.startswith("|") else "text"] += 1
            info.append({"h": line_hash(ln), "t": "table" if ln.startswith("|") else "text", "extra": extra,
                         "chunks": in_chunks[ln], "source": None if in_source is None else in_source[ln]})
        defect = any(x["extra"] for x in info)
        adjacent += defect
        repeat += not defect
        info.sort(key=lambda x: not x["extra"])
        pairs.append({"i": i, "defect": defect, "lines": info[:MAX_LINES_PER_PAIR]})
    # 결함 쌍 먼저 남긴다 — 앞 쌍이 전부 원문 반복이면 뒤 결함이 잘려 화면에서 사라지던 것(리뷰 4). 잘렸으면 표시
    truncated = len(pairs) > max_pairs
    pairs = sorted(pairs, key=lambda p: (not p["defect"], p["i"]))[:max_pairs]
    pairs.sort(key=lambda p: p["i"])
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
    # source_checked = 원문과 대조했나(False면 예전 기준 — 원문 반복도 결함으로 셈, 화면이 표시)
    return {"chunks": len(items), "adjacent_overlap": adjacent, "adjacent_source_repeat": repeat, "source_checked": in_source is not None,
            "overlap_pairs": pairs, "overlap_pairs_truncated": truncated, "overlap_line_types": dict(types), "trailing_heading": trailing,
            "heading_only": heading_only, "with_heading_path": with_path, "warnings": warnings}
