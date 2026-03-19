"""프롬프트 관리 — Pydantic 스키마."""
from typing import Optional
from pydantic import BaseModel, Field


class PromptOut(BaseModel):
    id: int
    func_key: str
    func_name: str
    content: str
    description: str
    updated_at: str


class PromptUpdate(BaseModel):
    func_name: Optional[str] = Field(default=None, max_length=200)
    content: Optional[str] = None
    description: Optional[str] = None
