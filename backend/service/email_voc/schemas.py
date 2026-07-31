"""VOC 이메일 분석 채널 — 스키마 (docs/email-analysis-channel-plan.md)."""
from datetime import date, datetime, timedelta
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class EmailAnalyzeRequest(BaseModel):
    """§11 Track A #2 테스트 진입점 — 실제 Graph API 연동 전, 텍스트 직접 입력으로
    분류/심각도/오배치 판정 프롬프트를 검증하기 위한 요청. 나중에 §5 Phase 1의
    수동 실행 기능(관리자가 기간 지정) 뒤에서 실제 이메일에도 동일 함수가 재사용된다."""
    namespace: str
    subject: str = ""
    body: str
    part: Optional[str] = None


class EmailAnalysisOut(BaseModel):
    category: str  # system_error / user_mistake / uncertain
    severity: str  # low / medium / high / urgent
    mismatch_flagged: bool
    knowledge_ref_ids: list[int]
    resolution_draft: Optional[str] = None
    reasoning: Optional[str] = None
    mapped_term: Optional[str] = None


class EmailCollectionSettingsOut(BaseModel):
    """§9 폴링 정책 — ops_system_config에 영속 저장(재시작에도 유지)."""
    email_collection_enabled: bool
    email_polling_interval_minutes: int
    email_lookback_days: int


class EmailCollectionSettingsUpdate(BaseModel):
    email_collection_enabled: Optional[bool] = None
    email_polling_interval_minutes: Optional[int] = None
    email_lookback_days: Optional[int] = None


class VocRoutingCreate(BaseModel):
    """§10 담당자 라우팅 매핑 — 파트별 메일함 1개당 1행(§3 Q7 결정: 메일함 분리)."""
    namespace: str
    part: str = Field(min_length=1)
    mailbox_upn: str = Field(min_length=1)
    teams_webhook_url: Optional[str] = None
    oncall_contact_name: Optional[str] = None
    oncall_contact_phone: Optional[str] = None


class VocRoutingUpdate(BaseModel):
    # min_length=1 — None(값 변경 안 함)은 허용하되, 빈 문자열로 지우는 건 금지
    # (routing_service.update_routing은 part/mailbox_upn 없이는 파이프라인이 동작 못 함)
    part: Optional[str] = Field(default=None, min_length=1)
    mailbox_upn: Optional[str] = Field(default=None, min_length=1)
    teams_webhook_url: Optional[str] = None
    oncall_contact_name: Optional[str] = None
    oncall_contact_phone: Optional[str] = None
    is_active: Optional[bool] = None


class VocRoutingOut(BaseModel):
    id: int
    namespace: str
    part: str
    mailbox_upn: str
    teams_webhook_url: Optional[str] = None
    oncall_contact_name: Optional[str] = None
    oncall_contact_phone: Optional[str] = None
    is_active: bool
    created_at: str
    updated_at: str


class ManualCollectionRequest(BaseModel):
    """§9 수동 1회성 실행 — 관리자가 임의 기간(from~to)을 지정해 즉시 수집+분석을 트리거.

    기본값은 "오늘부터 7일 전까지"(date_from=오늘-7일, date_to=오늘) — 프론트에서
    기본값을 채워 보내되, 서버에서도 동일 기본값과 유효성 검사를 한 번 더 강제한다.
    """
    namespace: str
    date_from: date = Field(default_factory=lambda: date.today() - timedelta(days=7))
    date_to: date = Field(default_factory=date.today)

    @model_validator(mode="after")
    def _validate_range(self) -> "ManualCollectionRequest":
        if self.date_from > self.date_to:
            raise ValueError("시작일(date_from)은 종료일(date_to)보다 이후일 수 없습니다.")
        if self.date_to > date.today():
            raise ValueError("종료일(date_to)은 오늘보다 미래일 수 없습니다.")
        if (self.date_to - self.date_from).days > 90:
            raise ValueError("조회 기간은 최대 90일까지만 가능합니다.")
        return self


class MailboxCollectionResult(BaseModel):
    mailbox_upn: str
    part: str
    ok: bool
    error: Optional[str] = None
    fetched: int = 0
    analyzed: int = 0
    skipped_duplicate: int = 0
    notified: int = 0
    notify_failed: int = 0


class ManualCollectionResult(BaseModel):
    date_from: date
    date_to: date
    mailboxes: list[MailboxCollectionResult]


class GraphCredentialsStatus(BaseModel):
    """실제 client_secret은 절대 반환하지 않음 — 설정 여부와 tenant/client id만 표시."""
    configured: bool
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None


class GraphCredentialsUpdate(BaseModel):
    tenant_id: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)


class EmailAnalysisHistoryItem(BaseModel):
    id: int
    mailbox_upn: str
    part: Optional[str] = None
    subject: str
    sender: str
    received_at: Optional[str] = None
    category: Optional[str] = None
    severity: Optional[str] = None
    mismatch_flagged: bool
    knowledge_ref_ids: list[int] = []
    resolution_draft: Optional[str] = None
    reasoning: Optional[str] = None
    status: str
    teams_sent_at: Optional[str] = None
    notify_error: Optional[str] = None
    created_at: str


class PollCycleItem(BaseModel):
    """스케줄러가 한 번 돌 때마다의 결과 — §9 "폴링 상태 가시성"."""
    id: int
    started_at: datetime
    finished_at: Optional[datetime] = None
    namespaces_processed: int
    mailboxes_ok: int
    mailboxes_failed: int
    total_fetched: int
    total_analyzed: int
    total_notified: int
    total_notify_failed: int
    total_skipped_duplicate: int
    error_summary: Optional[str] = None


class SchedulerStatus(BaseModel):
    """관리자 화면 "실시간 상태" 카드 — 지금 도는 중인지 + 마지막 결과 + 다음 예상 실행시각."""
    enabled: bool
    is_running_now: bool
    polling_interval_minutes: int
    last_cycle: Optional[PollCycleItem] = None
    next_estimated_at: Optional[datetime] = None


class TeamsTestNotifyRequest(BaseModel):
    """§11 Track A #3 테스트 진입점 — 공식 파트별 채널(Q4) 확정 전, 테스트용 Teams
    채널에서 즉시 발급 가능한 Workflows 웹훅 URL로 실제 POST 발송까지 검증한다."""
    webhook_url: str
    subject: str = "(테스트) VOC 알림"
    sender: str = ""
    part: str = ""
    category: str = "system_error"
    severity: str = "high"
    mismatch_flagged: bool = False
    resolution_draft: Optional[str] = None
    oncall_contact_name: Optional[str] = None
