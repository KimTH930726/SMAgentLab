"""인용 강제(citation-required answering) — 답 문장마다 근거 문서 번호를 달게 하고, 번호가 없는 문장은 기계적으로 뺀다.

업계: 인용 필수 생성(ALCE — Gao et al. 2023 "Enabling LLMs to Generate Text with Citations", Perplexity·Bing 식 문장별 [n]) +
인용 없는 문장 걸러내기(attribution filtering). 10/30 #11 "답 뒤 근거 확인"은 같은 모델이 자기 답을 한 번 더 검증해 단정 오답이
오히려 늘었다(17.2→21.9%) — 여기선 **추가 LLM 호출 없이** 형식만 본다(비용 0).

규칙(순수 함수 strip_uncited):
- 번호 표식 = `[#12]` · `[#12, #13]` · `[#12, 13]` · `[문서 #12]` (문맥의 "--- 문서 #12 (" 표식과 같은 번호).
  번호가 없는 문맥 블록(정책 서술·정책 파라미터·참고표·공통코드·DB 스키마)을 근거로 쓴 줄은 `[정책]`·`[참고표]`·`[공통코드]`·`[DB]`
  — 번호를 붙일 수 없는 근거 때문에 맞는 줄이 빠지지 않게(리뷰 M2 11/03).
- 남기는 줄: 번호가 있는 줄 · 제목(#)·빈 줄·구분선 · 콜론으로 끝나는 머리 줄("다음과 같습니다:") · 원래 "확인 안 됨"을 쓴 줄.
- 빼는 줄: 그 밖의 본문 줄(번호 없음). 번호가 하나라도 있으면 그 줄은 남으므로 답이 통째로 비지는 않는다.
- 형식을 전혀 안 지킨 답(번호가 하나도 없음)은 지우지 않고 그대로 두고 'no_citations'로 표시 — 모델이 형식을 못 따른 것과 근거 없는
  답을 섞지 않는다(대체 처리는 보이게). 측정에서 이 비율을 따로 센다.
"""
from __future__ import annotations

import re
from typing import NamedTuple

CITE = re.compile(r"\[(?:(?:문서\s*)?#\d+(?:\s*,\s*(?:문서\s*)?#?\d+)*|정책|참고표|공통코드|DB)\]")
UNVERIFIED = "확인 안 됨"

CITE_RULE_KO = """
[근거 표시 규칙]
- 답의 문장(또는 목록 줄)마다 끝에 근거로 쓴 참고 문서 번호를 [#번호] 형식으로 붙인다. 예: "정산은 매일 02시에 돈다 [#12]", 여러 개면 [#12, #13].
- 참고 문서에서 찾지 못한 내용은 쓰지 않는다. 질문의 일부를 문서에서 못 찾으면 그 부분은 "확인 안 됨"이라고 쓴다.
- 번호는 참고 문서 머리의 "문서 #번호"를 그대로 쓴다. 번호를 지어내지 않는다.
- 번호가 없는 참고 블록(정책 서술·정책 파라미터·참고표·공통코드·DB 스키마)을 근거로 쓴 문장에는 [정책]·[참고표]·[공통코드]·[DB] 중 하나를 붙인다."""


class Stripped(NamedTuple):
    answer: str
    status: str          # 'kept'(다 남음) · 'stripped'(일부 뺌) · 'no_citations'(형식 미준수 — 그대로)
    removed: int         # 뺀 본문 줄 수


def _is_frame(line: str) -> bool:
    t = line.strip()
    return (not t or t.startswith("#") or set(t) <= set("-=*_|: ") or t.endswith(":") or t.endswith("：")
            or UNVERIFIED in t)


def strip_uncited(answer: str) -> Stripped:
    lines = answer.split("\n")
    if not any(CITE.search(ln) for ln in lines):
        return Stripped(answer, "no_citations", 0)
    kept, removed = [], 0
    for ln in lines:
        if CITE.search(ln) or _is_frame(ln):
            kept.append(ln)
        else:
            removed += 1
    out = "\n".join(kept).strip()
    out = re.sub(r"\n{3,}", "\n\n", out)
    return Stripped(out, "stripped" if removed else "kept", removed)
