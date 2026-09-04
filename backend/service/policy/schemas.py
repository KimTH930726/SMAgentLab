"""정책서 임포트 API — 요청/응답 스키마."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


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


class NarrativeHitOut(BaseModel):
    item_id: int
    logical_id: int
    policy_name: str
    category_path: list[str]
    status: str
    chunk_text: str
    score: float


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
    status: str
    parse_status: str
    system_key: Optional[str] = None
    params: list[ParamOut]
    narratives: list[ChunkOut]
