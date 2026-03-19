"""Few-shot 도메인 — Pydantic 스키마."""
from typing import Optional
from pydantic import BaseModel


class FewshotOut(BaseModel):
    id: int
    namespace: str
    question: str
    answer: str
    knowledge_id: Optional[int]
    created_by_part: Optional[str] = None
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None
    created_at: str
    status: str = 'active'


class FewshotStatusUpdate(BaseModel):
    status: str


class FewshotCreate(BaseModel):
    namespace: str
    question: str
    answer: str
    knowledge_id: Optional[int] = None


class FewshotUpdate(BaseModel):
    question: Optional[str] = None
    answer: Optional[str] = None


class FewshotSearchRequest(BaseModel):
    namespace: str
    question: str


class FewshotResult(BaseModel):
    question: str
    answer: str
    similarity: float


class FewshotSearchResponse(BaseModel):
    question: str
    namespace: str
    fewshots: list[FewshotResult]
    prompt_section: str
