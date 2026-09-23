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

중요한 규칙 네 가지 (2026-09-04, 실제 오류 사례로 추가):
1. "1. 상태 분류", "2. 상태 전이 기준" 같은 **숫자 헤더 줄 자체는 그 아래 내용 없이 단독으로
   segment로 뽑지 마세요.** 헤더는 구획 표시일 뿐 내용이 아닙니다 — 그 헤더 아래의 실제 내용을
   분류하고, 필요하면 name/text에 헤더 맥락을 포함시키세요.
2. **원문의 모든 의미 있는 내용을 빠짐없이 다뤄야 합니다.** 어느 항목에도 안 맞는다고 그냥
   빠뜨리지 말고 반드시 unresolved로라도 남기세요 — 누락은 오분류보다 나쁩니다.
3. **param의 value는 항상 단일 문자열이어야 합니다. 배열(리스트)로 넣지 마세요.** 여러 값이
   동시에 허용되는 경우(예: "판매상태가 판매대기/판매중/판매종료인 경우")는 쉼표로 이어붙인
   하나의 문자열로 만드세요(예: "판매대기, 판매중, 판매종료").
4. **여러 줄이 하나의 계산식/공식이나 이벤트-변화 규칙을 구성하면, 줄 단위로 쪼개지 마세요.**
   각 줄을 독립된 narrative/param으로 따로 뽑으면 그 줄들을 잇는 관계(뺄셈/덧셈, 증가/감소
   방향)가 사라집니다. 이런 경우 원문 전체를 하나의 narrative segment로 그대로 보존하거나
   (관계가 명확하면), 관계가 애매하면 unresolved로 보내세요. **원문에 없는 관계(예: "합",
   "평균")를 만들어내지 마세요** — 확실하지 않으면 원문 그대로 옮기거나 unresolved로.

예시(실패 사례 1): 원문이 "2. 상태 전이 기준\n   1) 등록 : 미등록 → 등록"이면,
- 틀린 예: {"type": "narrative", "text": "2. 상태 전이 기준"} ← 헤더만 뽑고 실제 내용("등록 :
  미등록 → 등록")이 통째로 누락됨. 절대 이렇게 하지 마세요.
- 맞는 예: {"type": "unresolved", "text": "등록 : 미등록 → 등록", "reason": "상태 전이 규칙(A→B),
  구조화 방법 미정"} ← 헤더는 버리고 실제 내용을 unresolved로 보존.

예시(실패 사례 2): 원문이 "판매상태가 '판매대기', '판매중', '판매종료'인 단품만 등록 가능"이면,
- 틀린 예: {"extracted": {"name": "등록 가능 단품 판매상태", "value": ["판매대기", "판매중", "판매종료"]}}
  ← value가 배열. DB엔 문자열 컬럼이라 이대로 저장하면 깨진 텍스트가 됨. 절대 이렇게 하지 마세요.
- 맞는 예: {"extracted": {"name": "등록 가능 단품 판매상태", "value": "판매대기, 판매중, 판매종료"}}

예시(실패 사례 3): 원문이 "기초재고\n- 안전재고\n- 변동재고\n- 불량재고"(뺄셈 계산식)이면,
- 틀린 예: {"type": "narrative", "text": "기초재고"}, {"type": "narrative", "text": "안전재고"},
  {"type": "narrative", "text": "변동재고"}, ... ← 줄마다 따로 뽑아서 "기초재고에서 이것들을
  뺀 값"이라는 계산식 관계가 통째로 사라짐. 각 조각이 단어 하나뿐이라 의미도 없음.
- 맞는 예: {"type": "narrative", "text": "가용재고 = 기초재고 - 안전재고 - 변동재고 - 불량재고"}
  ← 계산식 관계를 보존한 하나의 segment.

예시(실패 사례 4): 원문이 "결제완료\n변경 재고 :\n출하예정수량 +\n배분주문수량 +"(이벤트 발생 시
두 필드가 각각 증가)이면,
- 틀린 예: {"type": "narrative", "text": "변경 재고는 출하예정수량과 배분주문수량의 합으로
  산정한다"} ← 원문에 없는 "합"이라는 관계를 지어냄(할루시네이션). +가 "증가"라는 뜻인데
  사라짐. 절대 이렇게 짐작해서 채우지 마세요.
- 맞는 예: {"type": "narrative", "text": "결제완료 시 출하예정수량과 배분주문수량이 각각
  증가(+)한다"} ← 원문의 관계(각각 증가, 합이 아님)를 정확히 보존.

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


_PARAM_SUGGEST_SYSTEM_PROMPT = """당신은 정책 문서에서 자동 분류에 실패해 "미분류"로 남은
조각(segment) 하나를 보고, 사람이 파라미터(policy_param: name/condition/value/unit)로
등록하려는 걸 돕는 도우미입니다. 이 조각은 이미 자동 분해 단계에서 구조화에 실패한
내용이라 확신이 낮을 수 있습니다 — 무리해서 지어내지 말고, 최선의 추측을 제공하세요.

반드시 다음 JSON 형식으로만 답하세요(다른 텍스트 없이):
{"name": "...", "condition": "..." 또는 null, "value": "..." 또는 null, "unit": "..." 또는 null}

- name(무엇에 대한 값인지)/condition(조건이나 코드)/value(값)/unit(단위)로 추출하세요.
- value는 항상 단일 문자열이어야 합니다. 배열로 넣지 마세요 — 여러 값이면 쉼표로 이어붙인
  문자열 하나로 만드세요(예: "판매대기, 판매중, 판매종료").
- 이 조각이 파라미터로 구조화하기에 너무 애매하면, name만이라도 내용을 요약해 채우고
  condition/value/unit은 null로 두세요. 절대 내용을 지어내지 마세요."""


async def suggest_param_fields(segment_text: str, reason: Optional[str] = None) -> Optional[dict]:
    """미분류 segment 하나를 파라미터 필드(name/condition/value/unit)로 1차 추측 — "폼에
    값 넣을 사람이 없겠다"는 지적(2026-09-23)으로 추가. 어디까지나 사람이 확인/수정 후
    저장하는 폼의 프리필용 보조 기능이라, 실패해도 예외를 던지지 않고 None을 반환해
    프론트가 빈 폼으로 폴백하게 한다(decompose_policy_body와 동일한 방어 스타일)."""
    if not segment_text.strip():
        return None
    prompt = f"[분류 안 된 내용]\n{segment_text}"
    if reason:
        prompt += f"\n\n[왜 자동 분류가 안 됐는지]\n{reason}"
    try:
        provider = get_llm_provider()
        result = await provider.generate_once(
            prompt=prompt, system=_PARAM_SUGGEST_SYSTEM_PROMPT, max_tokens=300,
        )
        parsed = json.loads(_strip_code_fence(result))
        if not isinstance(parsed, dict):
            return None
        return {
            "name": parsed.get("name") or None,
            "condition": parsed.get("condition") or None,
            "value": parsed.get("value") or None,
            "unit": parsed.get("unit") or None,
        }
    except Exception:
        return None


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
