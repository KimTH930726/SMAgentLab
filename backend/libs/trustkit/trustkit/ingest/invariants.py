"""청크 불변식 — 분할·추출·임포트처럼 입력을 바꿔 저장하는 코드가 지켜야 할 성질을 사례가 아니라 전체로 검사한다.

"X가 들어 있다"는 "유실·중복이 없다"를 보증하지 않는다(OpsLens 2026-10-07 장애: 기존 테스트는 다 통과했는데 청크 20쌍이 겹치고,
고치자 짧은 형제 섹션이 통째로 사라짐). 분할기를 바꿀 때 무작위 구조·실데이터 모양을 넣어 이 검사가 빈 목록을 돌려줘야 한다.
  I1 유실 0 — 입력의 모든 본문 줄이 어떤 청크엔가 있다
  I2 중복 0 — 본문 줄은 정확히 한 청크에만 있다(제목 줄은 하위 청크 맥락용 반복 허용)
  I2b 잘린 줄 없음 — 출력의 본문 줄은 전부 입력 줄 그대로
  I3 청크가 제목 줄로 끝나지 않는다
  I4 크기 — max_chars 이내(주어졌을 때)
  I5 상위 맥락 — 청크 첫 본문 줄이 속한 섹션의 조상 제목이 제목 경로(또는 청크 안 제목 줄)에 있다
입력 섹션: [{"title", "content", "level"}] (문서 순서), 출력 청크: [(text, heading_path)]. 제목 줄은 "#"로 시작한다고 본다.
"""
from __future__ import annotations

from typing import Optional, Sequence


def _body_lines(sections: Sequence[dict]) -> list[str]:
    return [ln.strip() for s in sections for ln in (s.get("content") or "").split("\n") if ln.strip()]


def check_chunks(sections: Sequence[dict], chunks: Sequence[tuple[str, Sequence[str]]],
                 max_chars: Optional[int] = None) -> list[str]:
    """위반 목록(빈 목록이면 통과). 메시지는 본문을 20자까지만 담는다."""
    texts = [t for t, _ in chunks]
    errs: list[str] = []
    for ln in _body_lines(sections):
        n = sum(t.count(ln) for t in texts)
        if n < 1:
            errs.append(f"I1 유실: {ln[:20]}")
        elif n > 1:
            errs.append(f"I2 중복({n}): {ln[:20]}")
    inputs = set(_body_lines(sections))
    for t in texts:
        for ln in t.split("\n"):
            s = ln.strip()
            if s and not s.startswith("#") and s not in inputs:
                errs.append(f"I2b 잘린 줄: {s[:20]}")
    for t in texts:
        lines = [ln for ln in t.split("\n") if ln.strip()]
        if lines and lines[-1].lstrip().startswith("#"):
            errs.append(f"I3 제목으로 끝남: {lines[-1][:20]}")
        if max_chars is not None and len(t) > max_chars:
            errs.append(f"I4 크기 {len(t)}")
    owner, stack = {}, []
    for s in sections:
        while stack and stack[-1][0] >= s["level"]:
            stack.pop()
        for ln in (s.get("content") or "").split("\n"):
            if ln.strip():
                owner[ln.strip()] = [t for _, t in stack]
        stack.append((s["level"], s["title"]))
    for t, hp in chunks:
        first = next((ln.strip() for ln in t.split("\n") if ln.strip() and not ln.lstrip().startswith("#")), None)
        if first and first in owner:
            ctx = set(hp or []) | {ln.lstrip("# ").strip() for ln in t.split("\n") if ln.lstrip().startswith("#")}
            missing = [a for a in owner[first] if a not in ctx]
            if missing:
                errs.append(f"I5 상위 맥락 빠짐: {missing}")
    return errs
