"""근거 정정 요청/응답 스키마."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

# auto = "답변 틀림"에서 대상을 안 고른 신고 — 서버가 그 답변의 근거 중 틀린 것을 판정(answer는 판정 결과 전용)
TargetType = Literal["knowledge", "policy_param", "policy_narrative", "missing", "auto"]


class CorrectionCreate(BaseModel):
    """채팅 근거 카드의 "이 근거 틀림"(또는 근거 없는 답변의 "이 내용이 빠졌어요") + 한 줄 입력.
    가중치·업무구분은 묻지 않는다. target_type='missing'이면 target_id 없음(지식 누락 신호)."""
    namespace: str
    message_id: Optional[int] = None
    target_type: TargetType
    target_id: Optional[int] = None  # knowledge: rag_knowledge.id / policy_*: policy_item.id / missing: 없음
    target_sub_id: Optional[int] = None  # policy_param.id 또는 policy_chunk.id
    user_input: str = Field(min_length=1, max_length=1000)


class ApproveBody(BaseModel):
    """담당자가 수정안을 고쳐서 승인할 수 있다(없으면 원장에 저장된 AI 수정안 그대로)."""
    proposed: Optional[dict] = None


class RejectBody(BaseModel):
    """반려 사유 필수 — 사용자 오정정 데이터로 쌓인다."""
    reason: str = Field(min_length=1, max_length=1000)


class RetargetBody(BaseModel):
    """담당자가 AI 판정 대상을 바꿈 — 원장에 저장된 후보 key, 또는 'missing'/'answer'."""
    key: str = Field(min_length=1, max_length=50)


class SeenBody(BaseModel):
    ids: list[int]
