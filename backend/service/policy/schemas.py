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


class Track2TypeResultOut(BaseModel):
    type: str
    n: int
    a_hit_rate: float
    b_hit_rate: float
    a_precision: float
    b_precision: float
    b_hit_rdb_only: float
    b_hit_vector_only: float
    b_hit_both: float
    a_top1_accuracy: float = 0.0
    b_top1_param_accuracy: float = 0.0
    b_top1_narrative_accuracy: float = 0.0


class Track2ResultOut(BaseModel):
    total_n: int
    a_hit_rate: float
    b_hit_rate: float
    a_precision: float
    b_precision: float
    b_hit_rdb_only: float
    b_hit_vector_only: float
    b_hit_both: float
    by_type: list[Track2TypeResultOut]
    golden_set_file: str
    top_k: int
    a_top1_accuracy: float = 0.0
    b_top1_param_accuracy: float = 0.0
    b_top1_narrative_accuracy: float = 0.0


class Track2RunHistoryOut(BaseModel):
    id: int
    run_at: datetime
    top_k: int
    total_n: int
    a_hit_rate: float
    b_hit_rate: float
    a_precision: float
    b_precision: float
    b_hit_rdb_only: float
    b_hit_vector_only: float
    b_hit_both: float
    a_top1_accuracy: float
    b_top1_param_accuracy: float
    b_top1_narrative_accuracy: float
    by_type: list[Track2TypeResultOut]
    golden_set_file: str
    duration_seconds: float
    triggered_by: Optional[int] = None


class PipelineStatsTrendPointOut(BaseModel):
    day: str
    count: int


class PipelineStatsOut(BaseModel):
    policy_item: int
    policy_param: int
    policy_chunk: int
    ref_db_column: int
    ref_common_code: int
    trend: list[PipelineStatsTrendPointOut]
