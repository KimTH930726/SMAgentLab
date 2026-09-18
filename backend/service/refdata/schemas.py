"""참조데이터(공통코드/DB스키마) 검색 응답 스키마 — 평가 게이트 즉석 질의(UnifiedAdhocSearch)
에 chat과 동일한 네 번째 RRF 축을 보여주기 위한 최소 엔드포인트(2026-09-18)."""
from typing import Optional

from pydantic import BaseModel


class CommonCodeHitOut(BaseModel):
    work_code: Optional[str] = None
    group_code: Optional[str] = None
    group_code_name: Optional[str] = None
    code_id: str
    code_name: Optional[str] = None
    mgmt_values: Optional[str] = None  # asyncpg가 JSONB를 codec 미등록 상태로 raw 텍스트 반환
    rank: float


class DbColumnHitOut(BaseModel):
    table_name: str
    table_comment: Optional[str] = None
    column_name: str
    column_comment: Optional[str] = None
    data_type: Optional[str] = None
    nullable: Optional[str] = None
    rank: float


class RefDataSearchOut(BaseModel):
    common_codes: list[CommonCodeHitOut]
    db_columns: list[DbColumnHitOut]
