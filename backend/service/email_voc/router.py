"""VOC 이메일 분석 라우터 — §11 Track A #2 테스트 진입점.

실제 Graph API 연동(Track B 승인 대기) 전에, 관리자가 텍스트를 직접 붙여넣어
분류/심각도/오배치 판정 프롬프트를 검증할 수 있게 한다. 같은 analyze_email()
함수가 나중에 §5 Phase 1의 실제 이메일 수동 실행에도 그대로 재사용된다.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from core.dependencies import get_current_user, check_namespace_ownership, get_current_admin
from core.security import get_user_llm_credentials
from service.email_voc import delegated_auth, service, teams_notify, routing_service, pipeline, scheduler, graph_client
from service.email_voc.schemas import (
    EmailAnalyzeRequest, EmailAnalysisOut, TeamsTestNotifyRequest,
    EmailCollectionSettingsOut, EmailCollectionSettingsUpdate,
    VocRoutingCreate, VocRoutingUpdate, VocRoutingOut, MailFolderOut,
    ManualCollectionRequest, ManualCollectionResult,
    GraphCredentialsStatus, GraphCredentialsUpdate,
    DelegatedAuthConfig, DelegatedAuthStatus, DelegatedAuthStartResult,
    EmailAnalysisHistoryItem, PollCycleItem, SchedulerStatus,
)

router = APIRouter(prefix="/api/email-voc", tags=["email-voc"])


@router.post("/test-analyze", response_model=EmailAnalysisOut)
async def test_analyze_email(body: EmailAnalyzeRequest, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(body.namespace, user)
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="본문(body)을 입력해주세요.")

    result = await service.analyze_email(
        body.namespace, body.subject, body.body,
        part=body.part or "",
        user_credentials=get_user_llm_credentials(user),
    )
    return EmailAnalysisOut(**result)


@router.post("/test-notify")
async def test_notify_teams(body: TeamsTestNotifyRequest, admin: dict = Depends(get_current_admin)):
    """테스트용 Teams 채널 웹훅으로 실제 POST 발송까지 검증(§11 Track A #3).

    공식 파트별 채널(§3 Q4)이 확정되지 않아도, 아무 Teams 채널에서 Workflows
    웹훅을 즉시 발급받아(§8 — 채널 소유자면 누구나 가능) 이 엔드포인트로 검증 가능하다.
    """
    message = teams_notify.build_teams_message(
        subject=body.subject, sender=body.sender, part=body.part,
        analysis={
            "category": body.category,
            "severity": body.severity,
            "mismatch_flagged": body.mismatch_flagged,
            "resolution_draft": body.resolution_draft,
        },
        oncall_contact_name=body.oncall_contact_name,
    )
    ok, error = await teams_notify.send_teams_notification(body.webhook_url, message)
    if not ok:
        raise HTTPException(status_code=502, detail=f"Teams 발송 실패: {error}")
    return {"sent": True}


# ─── §9 폴링 설정 ───────────────────────────────────────────────────────────

@router.get("/settings", response_model=EmailCollectionSettingsOut)
async def get_collection_settings(_user: dict = Depends(get_current_user)):
    return await routing_service.get_settings()


@router.put("/settings", response_model=EmailCollectionSettingsOut)
async def update_collection_settings(body: EmailCollectionSettingsUpdate, admin: dict = Depends(get_current_admin)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return await routing_service.update_settings(updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─── §10 담당자 라우팅 매핑 ─────────────────────────────────────────────────

@router.get("/routing", response_model=list[VocRoutingOut])
async def list_voc_routing(namespace: str, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(namespace, user)
    return await routing_service.list_routing(namespace)


@router.post("/routing", response_model=VocRoutingOut, status_code=201)
async def create_voc_routing(body: VocRoutingCreate, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(body.namespace, user)
    try:
        return await routing_service.create_routing(body.namespace, body.model_dump(exclude={"namespace"}))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put("/routing/{routing_id}", response_model=VocRoutingOut)
async def update_voc_routing(routing_id: int, namespace: str, body: VocRoutingUpdate, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(namespace, user)
    try:
        updated = await routing_service.update_routing(routing_id, namespace, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not updated:
        raise HTTPException(status_code=404, detail="라우팅 매핑을 찾을 수 없습니다.")
    return updated


@router.delete("/routing/{routing_id}", status_code=204)
async def delete_voc_routing(routing_id: int, namespace: str, user: dict = Depends(get_current_user)):
    await check_namespace_ownership(namespace, user)
    await routing_service.delete_routing(routing_id, namespace)


@router.get("/mail-folders", response_model=list[MailFolderOut])
async def list_mail_folders(mailbox_upn: str, admin: dict = Depends(get_current_admin)):
    """라우팅 폼에서 "메일 폴더" 드롭다운을 채우기 위해 실제 폴더 목록을 조회한다.

    pipeline.run_manual_collection()과 동일한 자격증명 우선순위(Application 권한 →
    Delegated 세션)를 그대로 따른다 — 조회 시점에 실제로 메일을 가져올 수 있는
    자격증명과 다른 걸 쓰면 "폴더는 보이는데 정작 폴링은 안 되는" 혼란이 생긴다.
    """
    credentials = await routing_service.get_graph_credentials_decrypted()
    if credentials:
        token = await graph_client.get_access_token(
            credentials["tenant_id"], credentials["client_id"], credentials["client_secret"],
        )
    else:
        token = await delegated_auth.get_access_token_silent()
    if token is None:
        raise HTTPException(
            status_code=400,
            detail="Graph API 자격증명이 설정되지 않았습니다 — 폴더 목록을 조회하려면 먼저 로그인/자격증명 설정이 필요합니다.",
        )
    try:
        return await graph_client.list_mail_folders(mailbox_upn, token)
    except (graph_client.GraphAuthError, graph_client.GraphApiError) as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─── Graph API 자격증명 (§7 Q10 승인 후 관리자가 수기 입력) ──────────────────

@router.get("/graph-credentials", response_model=GraphCredentialsStatus)
async def get_graph_credentials(admin: dict = Depends(get_current_admin)):
    return await routing_service.get_graph_credentials_status()


@router.put("/graph-credentials", response_model=GraphCredentialsStatus)
async def update_graph_credentials(body: GraphCredentialsUpdate, admin: dict = Depends(get_current_admin)):
    return await routing_service.set_graph_credentials(body.tenant_id, body.client_id, body.client_secret)


# ─── Delegated 권한 로그인 (Application 권한/Track B 승인 전 임시 경로) ──────────

@router.get("/delegated-auth/status", response_model=DelegatedAuthStatus)
async def get_delegated_auth_status(admin: dict = Depends(get_current_admin)):
    return await delegated_auth.get_status()


@router.put("/delegated-auth/config", response_model=DelegatedAuthStatus)
async def update_delegated_auth_config(body: DelegatedAuthConfig, admin: dict = Depends(get_current_admin)):
    return await delegated_auth.save_config(body.tenant_id, body.client_id, body.redirect_uri, body.client_secret)


@router.post("/delegated-auth/start", response_model=DelegatedAuthStartResult)
async def start_delegated_auth(admin: dict = Depends(get_current_admin)):
    try:
        return await delegated_auth.start_login()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/delegated-auth/callback", response_class=HTMLResponse)
async def delegated_auth_callback(request: Request):
    """Microsoft가 로그인 완료 후 브라우저를 리다이렉트시키는 지점.

    브라우저가 직접 접속하는 URL이라 JWT 인증이 없다 — Microsoft가 우리 Bearer
    토큰을 알 리 없으므로 여기엔 애초에 못 건다. CSRF/재생공격 방어는 MSAL이
    state 파라미터와 PKCE code_verifier를 내부적으로 검증하는 것으로 충분하다
    (delegated_auth._auth_flow에 저장된 값과 대조 — 공격자는 이 값을 알 수 없다).
    """
    await delegated_auth.complete_login(dict(request.query_params))
    return (
        "<html><body style='font-family:sans-serif;padding:2rem'>"
        "<h3>로그인 처리 완료</h3>"
        "<p>이 창은 닫으셔도 됩니다. 관리자 화면으로 돌아가면 상태가 자동으로 갱신됩니다.</p>"
        "</body></html>"
    )


# ─── 1단계 수동 1회성 실행 (§9, §5 Phase 1) ──────────────────────────────────

@router.post("/collect/run", response_model=ManualCollectionResult)
async def run_manual_collection(body: ManualCollectionRequest, admin: dict = Depends(get_current_admin)):
    """관리자가 기간(date_from~date_to)을 지정해 즉시 수집+분석+발송을 1회 실행한다.

    Track B(Graph API 자격증명) 미설정 시에도 요청 자체는 받아들이되, 메일함별로
    "자격증명 미설정" 에러를 결과에 담아 반환한다 — 전체 실패로 처리하지 않는다.
    """
    try:
        return await pipeline.run_manual_collection(body.namespace, body.date_from, body.date_to)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─── 이력 조회 ──────────────────────────────────────────────────────────────

@router.get("/history", response_model=list[EmailAnalysisHistoryItem])
async def get_history(
    namespace: str, limit: int = 50, offset: int = 0,
    severity: Optional[str] = None, status: Optional[str] = None,
    mismatch_only: bool = False, keyword: Optional[str] = None,
    user: dict = Depends(get_current_user),
):
    await check_namespace_ownership(namespace, user)
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    return await pipeline.list_history(
        namespace, limit=limit, offset=offset,
        severity=severity or None, status=status or None,
        mismatch_only=mismatch_only, keyword=keyword or None,
    )


# ─── 폴링 실시간 상태 + 사이클 이력 ────────────────────────────────────────

@router.get("/scheduler-status", response_model=SchedulerStatus)
async def get_scheduler_status(_user: dict = Depends(get_current_user)):
    return await scheduler.get_status()


@router.get("/poll-cycles", response_model=list[PollCycleItem])
async def get_poll_cycles(limit: int = 20, offset: int = 0, _user: dict = Depends(get_current_user)):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    return await scheduler.list_poll_cycles(limit=limit, offset=offset)
