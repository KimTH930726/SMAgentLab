"""정책서 API — 검색·실험실 응답 스키마(정책서 화면 조회는 dict 그대로, catalog.py)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ParamHitOut(BaseModel):
    item_id: int
    logical_id: int
    policy_name: str
    category_path: list[str]
    status: str
    param_name: str
    condition: Optional[str] = None
    value: Optional[str] = None
    unit: Optional[str] = None
    raw_body: str = ""
    remark: Optional[str] = None   # 비고(v0 10/20) — 검색엔 안 쓰고 답변 근거로
    score: float = 0.0


class NarrativeHitOut(BaseModel):
    item_id: int
    logical_id: int
    policy_name: str
    category_path: list[str]
    status: str
    chunk_text: str
    score: float
    raw_body: str = ""
    remark: Optional[str] = None   # 비고(v0 10/20)


class PolicySearchOut(BaseModel):
    params: list[ParamHitOut]
    narratives: list[NarrativeHitOut]
