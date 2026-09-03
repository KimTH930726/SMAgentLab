"""정책 본문 LLM 분해 — segment 단위로 (a)서술/(b)파라미터/(c)코드열거형/(d)상태전이 분류.

docs/policy-doc-pipeline-plan.md §2-3, §1 실측 근거로 검증된 프롬프트를 그대로 옮김(2026-09-03,
실 샘플 5건으로 프로토타입 검증 완료 — 혼재 row 분리, 코드열거형→param 흡수, 상태전이→
unresolved+사유 캡처 전부 정상 동작 확인). (c)코드열거형은 (b)파라미터와 동일한 shape
(name/condition/value/unit)로 흡수 가능해 별도 타입을 안 둔다. (d)상태전이는 Phase 1에서
자동 분류하지 않고 unresolved로만 캡처한다(§2, 실제 조회 수요가 확인되면 전용 구조 검토).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from service.llm.factory import get_llm_provider

SYSTEM_PROMPT = """당신은 비즈니스 정책서 원문을 구조화된 데이터로 분해하는 전문가입니다.
정책 본문(중첩 리스트 형식)을 의미 단위(segment)로 나누고, 각 segment를 다음 중 하나로 분류하세요.

- narrative: 서술 규칙 (예: "재고 없으면 SOLD OUT 표기"). 자연어 Q&A로 검색될 대상.
- param: 파라미터 팩트 또는 코드-값 열거형 (예: "일반 배달: 20개", "HCB01 : 베이직 제휴카드").
  name(무엇에 대한 값인지)/condition(조건이나 코드)/value(값)/unit(단위, 없으면 null)로 추출.
- unresolved: 위 두 가지 어디에도 안 맞는 내용(상태 전이 규칙(A→B) 등 구조화 방법이 아직
  정해지지 않은 패턴, 또는 애매해서 자동 분류가 위험한 내용). reason에 왜 분류 못 했는지 남기세요.

반드시 JSON만 출력하세요. 다른 설명 없이.
형식: {"segments": [{"type": "narrative"|"param"|"unresolved", "text": "원문 조각",
"extracted": {"name":.., "condition":.., "value":.., "unit":..} (type=param일 때만),
"reason": "..." (type=unresolved일 때만)}]}"""

# 용어집 시트 항목 중 일부(예: "결제 완료 — 상태코드: 11")는 정의문이 아니라 파라미터 팩트에
# 가깝다(§2-2 예외). v1은 policy_param이 policy_item FK를 필수로 요구해 용어집 단독으로는
# 넣을 자리가 없어 이 분류를 하지 않고 전부 rag_glossary로 보낸다(service.py 참고, 과설계 방지) —
# 검토 UI 도입 시 재분류 대상.


@dataclass
class Segment:
    type: str  # "narrative" | "param" | "unresolved"
    text: str
    extracted: Optional[dict] = None  # type=param일 때만
    reason: Optional[str] = None      # type=unresolved일 때만


def _strip_code_fence(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("```")[1]
        if s.startswith("json"):
            s = s[4:]
    return s.strip()


async def decompose_policy_body(policy_name: str, raw_body: str) -> list[Segment]:
    """정책 본문을 segment 목록으로 분해. LLM 호출/파싱 실패 시 전체를 unresolved 1개로 반환
    (파이프라인이 죽지 않고 사람이 검토할 수 있게)."""
    if not raw_body.strip():
        return []
    provider = get_llm_provider()
    try:
        result = await provider.generate_once(
            prompt=f"정책명: {policy_name}\n\n[정책 본문]\n{raw_body}",
            system=SYSTEM_PROMPT,
            max_tokens=1500,
        )
        parsed = json.loads(_strip_code_fence(result))
        segments = []
        for seg in parsed.get("segments", []):
            segments.append(Segment(
                type=seg.get("type", "unresolved"),
                text=seg.get("text", ""),
                extracted=seg.get("extracted"),
                reason=seg.get("reason"),
            ))
        return segments
    except Exception as e:
        return [Segment(
            type="unresolved", text=raw_body,
            reason=f"LLM 분해 실패({type(e).__name__}) — 원문 전체를 검토 대상으로 보존",
        )]
