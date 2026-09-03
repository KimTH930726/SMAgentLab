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
