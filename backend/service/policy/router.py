"""정책서 API — 정책서 화면 조회(v0, 2026-10-21) · 즉석 검색 · 축적 현황(옛 실험실 Track2·답변 정확도 API는 v2.176에서 뺌).

v0 재구축 2단계(10/21)에서 옛 엑셀 경로 화면의 API(올리기·붙여넣기·미분류·파라미터/서술 줄 안 편집·위험도 검토 큐·항목 승인/반려)를 뺐다.
정책 내용 변경은 모두 정정 원장(/api/corrections — 정책 화면의 직접 수정·AI 수정 제안)으로 들어가 정정 요청에서 승인할 때만 새 버전이 된다.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.dependencies import get_current_user, get_current_admin, check_knowledge_read, check_namespace_ownership, reject_policy_outside_policy_collection
from service.policy import catalog, search as search_service
from service.policy import json_io, source_check
from service.policy.schemas import PolicySearchOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/policy", tags=["policy"])


# ── 정책서 화면(v0) — 읽기 전용. 권한: 조회는 로그인 사용자 누구나(check_knowledge_read) ─────────────

@router.get("/policies")
async def list_policies(namespace: str = Query(...), q: Optional[str] = Query(default=None, max_length=200),
                        user: dict = Depends(get_current_user)):
    """지금 쓰이는 정책 1건 = 1줄(문서 순서) + 검토 중 정정 번호."""
    await check_knowledge_read(namespace, user)
    return await catalog.list_policies(namespace, q)


@router.get("/policies/{logical_id}")
async def get_policy(logical_id: int, namespace: str = Query(...), user: dict = Depends(get_current_user)):
    """정책 상세 — 원문·비고·파라미터·용어·버전 이력·검토 중 정정."""
    await check_knowledge_read(namespace, user)
    out = await catalog.get_policy(namespace, logical_id)
    if out is None:
        raise HTTPException(status_code=404, detail="이 업무 영역에 지금 쓰이는 그 정책이 없어요 — 새로고침하세요.")
    return out


@router.get("/ref-tables")
async def list_ref_tables(namespace: str = Query(...), user: dict = Depends(get_current_user)):
    await check_knowledge_read(namespace, user)
    return await catalog.list_ref_tables(namespace)


@router.get("/ref-tables/{table_id}")
async def get_ref_table(table_id: int, namespace: str = Query(...), user: dict = Depends(get_current_user)):
    """참고표 원표 — 열 + 행({열 이름: 값})."""
    await check_knowledge_read(namespace, user)
    out = await catalog.get_ref_table(namespace, table_id)
    if out is None:
        raise HTTPException(status_code=404, detail="참고표를 찾을 수 없어요.")
    return out


@router.get("/overviews")
async def list_overviews(namespace: str = Query(...), user: dict = Depends(get_current_user)):
    """문서 개요(서문)."""
    await check_knowledge_read(namespace, user)
    return await catalog.list_overviews(namespace)


# ── 원문 확인(v0 재구축 3단계, 10/21) — 정책서 원문에 대한 질문·문제 + 제안 연결. 조회는 누구나, 처리는 담당 파트·관리자 ──────

class SourceCheckDecision(BaseModel):
    """[해결]의 메모(필수) / [문제 없음]의 사유(reason 4개 중 하나, 기타면 메모 필수 — 서비스가 확인)."""
    note: str = Field(default="", max_length=1000)
    reason: Optional[str] = Field(default=None, pattern="^(source_ok|already_fixed|duplicate|other)$")


@router.get("/source-checks/reasons")
async def get_no_issue_reasons(user: dict = Depends(get_current_user)):
    """[이슈 아님] 사유 사전(11/05) — 화면 문구도 이 한 곳에서."""
    from service.policy.source_check import NO_ISSUE_REASONS
    return [{"key": k, "label": v} for k, v in NO_ISSUE_REASONS.items()]


@router.get("/source-checks")
async def list_source_checks(namespace: str = Query(...), status: Optional[str] = Query(default=None, pattern="^(open|done)$"),
                             user: dict = Depends(get_current_user)):
    await check_knowledge_read(namespace, user)
    return await source_check.list_checks(namespace, status)


async def _check_owner(item_id: int, user: dict) -> None:
    ns = await source_check.check_namespace(item_id)
    if ns is None:
        raise HTTPException(status_code=404, detail="원문 확인 항목을 찾을 수 없어요.")
    await check_namespace_ownership(ns, user)


@router.post("/source-checks/{item_id}/resolve")
async def resolve_source_check(item_id: int, body: SourceCheckDecision, user: dict = Depends(get_current_user)):
    await _check_owner(item_id, user)
    return await source_check.decide_check(item_id, user, "resolved", body.note)


@router.post("/source-checks/{item_id}/dismiss")
async def dismiss_source_check(item_id: int, body: SourceCheckDecision, user: dict = Depends(get_current_user)):
    await _check_owner(item_id, user)
    return await source_check.decide_check(item_id, user, "dismissed", body.note, reason=body.reason)


async def _link_owner(link_id: int, user: dict) -> None:
    ns = await source_check.link_namespace(link_id)
    if ns is None:
        raise HTTPException(status_code=404, detail="제안 연결을 찾을 수 없어요.")
    await check_namespace_ownership(ns, user)


@router.post("/links/{link_id}/confirm")
async def confirm_link(link_id: int, user: dict = Depends(get_current_user)):
    await _link_owner(link_id, user)
    return await source_check.decide_link(link_id, user, True)


@router.post("/links/{link_id}/reject")
async def reject_link(link_id: int, user: dict = Depends(get_current_user)):
    await _link_owner(link_id, user)
    return await source_check.decide_link(link_id, user, False)


# ── JSON 내려받기·가져오기(관리자 전용, 10/21) — 가져오기는 바뀐 정책마다 정정 제안만 만든다(승인은 정정 요청에서) ──────

class JsonImportBody(BaseModel):
    namespace: str
    content: str = Field(min_length=1, max_length=json_io.MAX_CONTENT)
    filename: Optional[str] = Field(default=None, max_length=200)


class JsonApplyBody(JsonImportBody):
    doc_hash: str = Field(min_length=1, max_length=128)
    plan_hash: str = Field(min_length=1, max_length=128)


@router.get("/json/export")
async def export_policy_json(namespace: str = Query(...), user: dict = Depends(get_current_admin)):
    """지금 쓰이는 정책(얇은 형식 opslens/policy-v0/1)."""
    return await json_io.export_domain(namespace)


@router.post("/json/preview")
async def preview_policy_json(body: JsonImportBody, user: dict = Depends(get_current_admin)):
    """파일과 지금 정책 비교 — 같음·바뀜·파일에만·DB에만·검토 중이라 막힘. 아무것도 안 쓴다."""
    await reject_policy_outside_policy_collection(body.namespace)   # 정책은 정책 컬렉션에만(#105)
    return await json_io.preview(body.namespace, body.content)


@router.post("/json/apply")
async def apply_policy_json(body: JsonApplyBody, user: dict = Depends(get_current_admin)):
    """바뀐 정책마다 정정 제안 1건(출처 json_import). 미리보기 뒤 파일·정책이 바뀌었으면 409."""
    await reject_policy_outside_policy_collection(body.namespace)
    try:
        return await json_io.apply(body.namespace, user, body.content, body.doc_hash, body.plan_hash, body.filename)
    except json_io.ImportConflict as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.get("/search", response_model=PolicySearchOut)
async def search_policy(
    namespace: str = Query(...),
    q: str = Query(..., min_length=1),
    category: Optional[str] = Query(default=None),
    top_k: int = Query(default=10, ge=1, le=50),
    user: dict = Depends(get_current_user),
):
    """정책 데이터 검색 — 파라미터(RDB 정확 조회)와 서술(벡터 검색) 두 갈래를 함께 반환한다.

    대상은 채팅 검색과 같은 기준(visibility.searchable_sql — v0는 승인된 것만, 옛 엑셀 경로는 검토 대기 포함).
    응답의 `status`로 미검토 여부 구분.
    """
    await check_knowledge_read(namespace, user)
    try:
        result = await search_service.search_policy(namespace, q, category, top_k)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return PolicySearchOut(
        params=[p.__dict__ for p in result.params],
        narratives=[n.__dict__ for n in result.narratives],
    )


# /pipeline-stats(옛 실험실 모니터링 집계)는 11/03 삭제 — 그 화면은 001aa4c에서 내렸고 API만 남아 있었다(안 쓰는 기능 정리)
