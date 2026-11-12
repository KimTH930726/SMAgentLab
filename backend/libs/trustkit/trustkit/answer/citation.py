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


# ── 숫자·시각 근거 대조(11/11 시연 리뷰, backlog 0-0h B) — 형식 검사(strip_uncited)는 번호만 본다: "…03:30 이전 종료 조건을 따르는 것이 적절 [#20606]"은
# 번호가 있어 통과했지만 #20606엔 03:30이 없었다(정정된 #65251의 값을 다른 문서로 넓힌 추론 + 근거 오표기). 단정 오답이 가장 위험한 곳이 숫자라
# 숫자·시각만 기계적으로 본다(추가 LLM 0 — 자기 검증은 단정 오답을 늘렸다, 10/30 #11). 지금은 **측정만**(답을 바꾸지 않음): 빼기·표시는 숫자 보고 결정.
CITE_IDS = re.compile(r"\[(?:문서\s*)?#(\d+)((?:\s*,\s*(?:문서\s*)?#?\d+)*)\]")
_TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3])\s*(?::|시(?!간))\s*([0-5]\d)?\s*분?")   # "2시간"은 시각 아님
_NUM_UNIT = re.compile(r"(?<![\d.:])(\d{1,6}(?:\.\d+)?)\s?(%|일|회|분|시간|건|개|초|원|명|년|개월|주|번)(?![가-힣A-Za-z])")


def _cited(line: str) -> list[int]:
    out = []
    for m in CITE_IDS.finditer(line):
        out.append(int(m.group(1)))
        out += [int(x) for x in re.findall(r"\d+", m.group(2) or "")]
    return out


def _times(text: str) -> set[int]:
    """시각 → 분(하루 기준). "03:30" · "3시 30분" · "04시" · "4시" 같은 값으로 본다."""
    out = set()
    for m in _TIME.finditer(text):
        out.add(int(m.group(1)) * 60 + int(m.group(2) or 0))   # 패턴이 이미 ':'·'시'를 요구한다
    return out


def _amounts(text: str) -> set[tuple[str, str]]:
    text = _TIME.sub(" ", text)   # 시각의 "30분"을 양(30분)으로 또 세지 않게
    return {(m.group(1).rstrip("0").rstrip(".") if "." in m.group(1) else m.group(1).lstrip("0") or "0", m.group(2))
            for m in _NUM_UNIT.finditer(text)}


class NumberCheck(NamedTuple):
    checked: int                  # 숫자·시각이 있고 근거 번호가 붙은 줄 수
    unsupported: int              # 그 줄의 숫자·시각 중 하나라도 인용한 근거 본문에 없는 줄 수
    unknown_cite: int             # 문맥에 없는 번호를 단 줄 수(지어낸 번호)
    lines: list                   # [(줄 번호, 없는 값 개수)] — 값·원문은 안 남김(운영 기록용)


def check_numbers(answer: str, docs: dict[int, str]) -> NumberCheck:
    """답 줄마다: 근거 번호가 붙은 줄의 시각(분)·숫자+단위가 인용한 근거 본문 어딘가에 있는가. docs = {문서 번호: 본문}(문맥에 넣은 것)."""
    checked = unsupported = unknown = 0
    lines = []
    for i, ln in enumerate(answer.split("\n")):
        ids = _cited(ln)
        if not ids:
            continue
        body = re.sub(r"\[(?:문서\s*)?#[^\]]*\]", " ", ln)   # 번호 표식 자체의 숫자는 값이 아니다
        t, a = _times(body), _amounts(body)
        if not t and not a:
            continue
        checked += 1
        known = [docs[x] for x in dict.fromkeys(ids) if x in docs]
        if len(known) < len(set(ids)):   # 같은 번호 중복([#12, #12, #999])이 지어낸 번호를 가리지 않게 — 고유 번호끼리 비교(리뷰 L4)
            unknown += 1
        src = "\n".join(known)
        miss = len(t - _times(src)) + len(a - _amounts(src))
        if miss:
            unsupported += 1
            lines.append((i, miss))
    return NumberCheck(checked, unsupported, unknown, lines)


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
