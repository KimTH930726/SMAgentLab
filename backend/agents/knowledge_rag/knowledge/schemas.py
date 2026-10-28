"""지식/용어집 도메인 — Pydantic 스키마."""
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ─── Knowledge ───────────────────────────────────────────────────────────────

# 가중치(base_weight) 입력은 2026-10-02 제거 — 쓰인 적 없고(활성 지식 전부 1.0) 틀린 검색은 배수로 덮지 않고 내용·
# 용어집을 고친다(정정 흐름). 컬럼과 검색식의 (1 + base_weight)는 남겨 모든 값 1.0 = 순위·임계값 그대로(메일 VOC 인용 임계값이
# 부풀려진 점수 기준이라, 식에서 빼려면 그 임계값 재측정이 먼저). 옛 화면이 보내도 무시된다(정의 안 된 필드).
class KnowledgeCreate(BaseModel):
    namespace: str
    content: str
    # E2(10/23) 위치 판정 원장 줄(POST /locate가 준 id)과 고른 방법 — 있으면 등록 뒤 같은 줄에 확정(다른 곳을 고르면 AI 오답 신호)
    decision_id: Optional[int] = None
    location_choice: Optional[str] = None   # accept | option | other | preset
    # AI 추천과 다른 곳을 골랐을 때 사람이 고른 이유(10/27, 선택) — wrong: AI가 틀림 · both_ok: 둘 다 맞는데 이쪽이 더 맞음 · context_changed: 상황이 바뀜.
    # 자가 개선 진단이 번복 묶음으로 읽는다(service/eval/override.py)
    override_reason: Optional[Literal["wrong", "both_ok", "context_changed"]] = None
    # 등록 화면이 미리 시작했다가 내용을 고쳐 버린 위치 판단들(10/28) — 등록하면서 원장에서 지운다(최종 판단만 남김)
    discard_decision_ids: list[int] = Field(default_factory=list, max_length=50)
    # 업무구분(category) 입력은 10/22 업무구분 폐지로 제거 — 옛 화면이 보내도 무시된다


class KnowledgeUpdate(BaseModel):
    content: Optional[str] = None


class DuplicateMatchOut(BaseModel):
    id: int
    content: str
    similarity: float


class KnowledgeOut(BaseModel):
    id: int
    namespace: str
    content: str
    base_weight: float
    status: str = "active"
    source_file: Optional[str] = None
    source_chunk_idx: Optional[int] = None
    source_type: Optional[str] = None
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None
    created_at: str
    updated_at: str
    pending_review: bool = False
    duplicate_matches: list[DuplicateMatchOut] = []
    # 문서 안 위치(2026-10-07) — 상위 경로와 등록 묶음. 구조 화면은 GET /{id}/structure
    heading_path: Optional[list[str]] = None
    ingestion_job_id: Optional[int] = None


# ─── Bulk / Ingestion ──────────────────────────────────────────────────────

class BulkKnowledgeItem(BaseModel):
    content: str
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
    # 원문 원장(2026-10-08) — 미리보기가 저장한 원문 id. 확정 때 청크에 연결하고 원문을 "사용 중"으로
    source_document_id: Optional[int] = None
    # 그 원문의 미리보기 조각 중 일부만 골라 등록했나 — 다시 분할이 뺀 조각을 되살리지 않게(리뷰 2026-10-08)
    source_document_partial: bool = False


class BulkCreateRequest(BaseModel):
    namespace: str
    items: list[BulkKnowledgeItem]
    source_file: Optional[str] = None
    source_type: str = "manual"
    # 같은 원본 재등록 때 기존 조각에 사람 손이 닿은 흔적이 있어도 교체 — 사용자가 사유를 보고 확인했을 때만(A안 2026-10-12)
    confirm_replace: bool = False
    decision_id: Optional[int] = None        # KnowledgeCreate와 같음(E2)
    location_choice: Optional[str] = None
    override_reason: Optional[Literal["wrong", "both_ok", "context_changed"]] = None
    # 등록 화면이 미리 시작했다가 내용을 고쳐 버린 위치 판단들(10/28) — 등록하면서 원장에서 지운다(최종 판단만 남김)
    discard_decision_ids: list[int] = Field(default_factory=list, max_length=50)


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


class GlossarySynonymOut(BaseModel):
    id: int
    synonym: str
    source: str             # llm_term(용어 등록 시 LLM) / llm_gap(반복된 지식 공백에서 LLM + 효과 확인) / llm_query(옛 수집분)
    evidence_count: int = 1
    active: bool = True     # 질문 기록 동의어는 서로 다른 질문 2건 이상이어야 검색에 쓰임


class GlossaryOut(BaseModel):
    id: int
    namespace: str
    term: str
    description: str
    source: Optional[str] = None            # 출처: policy_doc(정책서 최종본) / auto_extract(지식 등록 때 LLM 자동 추출, 10/28~) / NULL(사람 등록 — 옛 자동 추출도 섞임)
    created_by_user_id: Optional[int] = None
    created_by_username: Optional[str] = None
    synonyms: list[GlossarySynonymOut] = []
