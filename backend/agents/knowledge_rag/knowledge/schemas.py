"""지식/용어집 도메인 — Pydantic 스키마."""
from typing import Optional
from pydantic import BaseModel, Field


# ─── Knowledge ───────────────────────────────────────────────────────────────

# 가중치(base_weight) 입력은 2026-10-02 제거 — 쓰인 적 없고(활성 지식 전부 1.0) 틀린 검색은 배수로 덮지 않고 내용·업무구분·
# 용어집을 고친다(정정 흐름). 컬럼과 검색식의 (1 + base_weight)는 남겨 모든 값 1.0 = 순위·임계값 그대로(메일 VOC 인용 임계값이
# 부풀려진 점수 기준이라, 식에서 빼려면 그 임계값 재측정이 먼저). 옛 화면이 보내도 무시된다(정의 안 된 필드).
class KnowledgeCreate(BaseModel):
    namespace: str
    content: str
    category: Optional[str] = None


class KnowledgeUpdate(BaseModel):
    content: Optional[str] = None
    category: Optional[str] = None


class DuplicateMatchOut(BaseModel):
    id: int
    content: str
    similarity: float


class KnowledgeOut(BaseModel):
    id: int
    namespace: str
    content: str
    base_weight: float
    category: Optional[str] = None
    status: str = "active"
    source_file: Optional[str] = None
    source_chunk_idx: Optional[int] = None
    source_type: Optional[str] = None
    created_by_part: Optional[str] = None
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None
    created_at: str
    updated_at: str
    pending_review: bool = False
    duplicate_matches: list[DuplicateMatchOut] = []


# ─── Bulk / Ingestion ──────────────────────────────────────────────────────

class BulkKnowledgeItem(BaseModel):
    content: str
    category: Optional[str] = None
    # 컨플루언스 벌크 리뷰 화면에서 넘어오는 조상 헤딩 목록(2026-09-22 수정) — 이전엔
    # 이 필드가 스키마에 없어서 확정(POST /knowledge/bulk) 시점에 조용히 버려지고 있었다.
    # heading_path 컬럼 자체는 v2.98에 추가됐지만, 실제 UI 확정 흐름(미리보기→리뷰→확정)이
    # 그 값을 한 번도 실어 나르지 않아 백필 스크립트로 소급 적용한 레거시 행 외엔 전부
    # NULL로 저장되고 있었다 — 이번에 함께 수정.
    heading_path: Optional[list[str]] = None
    # 컨플루언스 원본 식별(2026-09-28) — 미리보기가 이 값을 갖고 있었지만 확정 요청 스키마에
    # 필드가 없어 조용히 버려졌다(heading_path와 같은 결함의 두 번째 사례, 실 DB 64건 전부 NULL).
    # 있으면 같은 페이지의 기존 active 행을 이 등록이 성공할 때 원자적으로 교체한다.
    confluence_page_id: Optional[str] = None
    confluence_version: Optional[int] = None


class BulkCreateRequest(BaseModel):
    namespace: str
    items: list[BulkKnowledgeItem]
    source_file: Optional[str] = None
    source_type: str = "manual"


class IngestionJobOut(BaseModel):
    id: int
    namespace_id: int
    source_file: Optional[str]
    source_type: Optional[str]
    status: str
    total_chunks: int
    created_chunks: int
    pending_chunks: int = 0
    auto_glossary: int
    chunk_strategy: Optional[str]
    error_message: Optional[str]
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None
    created_at: str
    completed_at: Optional[str]


# ─── Glossary ────────────────────────────────────────────────────────────────

class GlossaryCreate(BaseModel):
    namespace: str
    term: str
    description: str


class GlossaryUpdate(BaseModel):
    term: str
    description: str


class GlossaryOut(BaseModel):
    id: int
    namespace: str
    term: str
    description: str
    created_by_part: Optional[str] = None
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None
