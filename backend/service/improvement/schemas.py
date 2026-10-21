"""근거 정정 요청/응답 스키마."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

# auto = "답변 틀림"에서 대상을 안 고른 신고 — 서버가 그 답변의 근거 중 틀린 것을 판정(answer는 판정 결과 전용)
TargetType = Literal["knowledge", "policy_param", "policy_narrative", "missing", "auto"]


PolicySource = Literal["policy_direct", "policy_ai"]


class CorrectionCreate(BaseModel):
    """채팅 "답변 틀림" 한 줄(source 없음) 또는 정책서 화면의 수정 제안(source=policy_direct/policy_ai, 10/21).
    가중치·업무구분은 묻지 않는다. target_type='missing'이면 target_id 없음(지식 누락 신호).
    정책 제안: target_type='policy_narrative', target_id=policy_item.id(서술 조각은 서버가 고름).
    직접 수정은 proposed({raw_body, remark?}) 필수·user_input 없음, AI 제안은 user_input(한 줄) 필수."""
    namespace: str
    message_id: Optional[int] = None
    target_type: TargetType
    target_id: Optional[int] = None  # knowledge: rag_knowledge.id / policy_*: policy_item.id / missing: 없음
    target_sub_id: Optional[int] = None  # policy_param.id 또는 policy_chunk.id
    user_input: Optional[str] = Field(default=None, max_length=1000)   # 채팅 신고·AI 제안은 필수(라우터가 확인)
    source: Optional[PolicySource] = None
    proposed: Optional[dict] = None


class ApproveBody(BaseModel):
    """승인할 때 화면에 보인 수정안(검증용) — 저장된 안과 다르면 409. 반영되는 건 늘 저장된 안(고치려면 먼저 PATCH …/proposed)."""
    proposed: Optional[dict] = None


class ProposedBody(BaseModel):
    """정정 요청 카드의 [직접 수정] — 담당자가 고친 수정안 전체(대상 칸 + 정책이면 비고)."""
    proposed: dict


class RejectBody(BaseModel):
    """반려 사유 필수 — 사용자 오정정 데이터로 쌓인다."""
    reason: str = Field(min_length=1, max_length=1000)


class RedraftBody(BaseModel):
    """담당자가 AI에게 주는 다시 쓰기 요청 한 줄(예: "기한은 7일이 맞아요")."""
    feedback: str = Field(min_length=1, max_length=1000)


class RetargetBody(BaseModel):
    """담당자가 AI 판정 대상을 바꿈 — 원장에 저장된 후보 key, 또는 'missing'/'answer'."""
    key: str = Field(min_length=1, max_length=50)


class SeenBody(BaseModel):
    ids: list[int]
