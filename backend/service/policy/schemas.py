"""정책서 임포트 API — 요청/응답 스키마."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class SheetSummaryOut(BaseModel):
    sheet_name: str
    kind: str
    created_items: int = 0
    new_versions: int = 0
    unchanged_skipped: int = 0
    params_extracted: int = 0
    narratives_extracted: int = 0
    unresolved_segments: int = 0
    glossary_added: int = 0
    glossary_duplicate_skipped: int = 0
    fallback_chunks_added: int = 0
    skip_reason: Optional[str] = None


class ImportSummaryOut(BaseModel):
    source_file: str
    sheets: list[SheetSummaryOut]


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


class PolicySearchOut(BaseModel):
    params: list[ParamHitOut]
    narratives: list[NarrativeHitOut]


class UnresolvedSegmentOut(BaseModel):
    text: str
    reason: Optional[str] = None


class UnresolvedItemOut(BaseModel):
    item_id: int
    logical_id: int
    policy_name: str
    category_path: list[str]
    segments: list[UnresolvedSegmentOut]


class SystemUnresolvedGroupOut(BaseModel):
    system_key: str
    item_count: int
    segment_count: int
    items: list[UnresolvedItemOut]


class UnresolvedSummaryOut(BaseModel):
    total_items: int
    total_segments: int
    by_system: list[SystemUnresolvedGroupOut]


class PromoteSegmentRequest(BaseModel):
    namespace: str
    segment_index: int = Field(ge=0)


class PromoteSegmentOut(BaseModel):
    remaining_segments: int


class ParamOut(BaseModel):
    id: int
    name: str
    condition: Optional[str] = None
    value: Optional[str] = None
    unit: Optional[str] = None


class ChunkOut(BaseModel):
    id: int
    chunk_text: str
    chunk_idx: int


class PolicyItemOut(BaseModel):
    item_id: int
    logical_id: int
    version: int
    policy_name: str
    category_path: list[str]
    raw_body: str
    status: str
    parse_status: str
    system_key: Optional[str] = None
    params: list[ParamOut]
    narratives: list[ChunkOut]
    matched_via: list[str] = []


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
