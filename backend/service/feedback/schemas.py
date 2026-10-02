"""피드백 도메인 — Pydantic 스키마."""
from typing import Optional
from pydantic import BaseModel


class FeedbackCreate(BaseModel):
    knowledge_id: Optional[int] = None
    namespace: str
    question: str
    answer: Optional[str] = None
    is_positive: bool
    message_id: Optional[int] = None
