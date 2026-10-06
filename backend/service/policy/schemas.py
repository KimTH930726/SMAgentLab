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
    pipeline_reprocessed: int = 0
    moved: int = 0
    matched_by_body: int = 0
    duplicate_keys: int = 0
    skip_reason: Optional[str] = None


class ImportSummaryOut(BaseModel):
    source_file: str
    sheets: list[SheetSummaryOut]
    missing_marked: int = 0
    auto_review: Optional[dict] = None  # 임포트 직후 위험도 낮음 자동 통과 결과(규칙 꺼짐이면 None)


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


class PromoteParamRequest(BaseModel):
    namespace: str
    segment_index: int = Field(ge=0)
    name: str
    condition: Optional[str] = None
    value: Optional[str] = None
    unit: Optional[str] = None


class ItemActionRequest(BaseModel):
    namespace: str


class AutoReviewRunRequest(BaseModel):
    namespace: Optional[str] = None  # 없으면 전체 파트
    dry_run: bool = True             # 기본은 미리보기 — 실제 실행은 명시적으로


class AutoReviewRevertRequest(BaseModel):
    run_id: Optional[str] = None
    rule_key: Optional[str] = None
    namespace: Optional[str] = None  # 주면 그 파트만 — 화면은 항상 현재 파트로 보낸다


class UpdateParamRequest(BaseModel):
    namespace: str
    name: str
    condition: Optional[str] = None
    value: Optional[str] = None
    unit: Optional[str] = None


class UpdateNarrativeRequest(BaseModel):
    namespace: str
    chunk_text: str


class ItemStatusOut(BaseModel):
    status: str


class SuggestParamRequest(BaseModel):
    namespace: str
    segment_index: int = Field(ge=0)


class SuggestParamOut(BaseModel):
    name: Optional[str] = None
    condition: Optional[str] = None
    value: Optional[str] = None
    unit: Optional[str] = None


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
    # 위험도(조회 시 계산, risk.py) + 결정 출처 — 검토 큐 정렬·배지용
    risk_level: Optional[str] = None
    risk_reasons: list[str] = []
    risk_short: str = ""        # 목록 한 줄용 핵심 이유
    risk_next_step: str = ""    # 담당자가 할 일
    review_source: Optional[str] = None
    review_rule: Optional[str] = None
    review_sample: bool = False
    unresolved_segments: list[dict] = []


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
