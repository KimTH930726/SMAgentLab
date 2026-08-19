"""1~3단계 파이프라인 오케스트레이션 — 수동 1회성 실행 (§5 Phase 1, §9, §11 Track A #1 후속).

이전까지의 test-analyze/test-notify는 각 단계를 독립적으로 테스트하는 진입점이었다.
이 모듈은 실제로 "메일함에서 수집 → 분석 → 저장(중복 방지) → 라우팅 조회 → Teams 발송"까지
이어붙인 진짜 파이프라인이다. Graph API 자격증명이 아직 설정되지 않았다면(Track B 승인 전)
메일함별로 명확한 에러만 반환하고 다른 메일함 처리는 계속 진행한다 — 하나가 막혀도
전체가 죽지 않게 하기 위함.
"""
import asyncio
import logging
from datetime import date, datetime, time, timezone
from typing import Optional

from core.database import get_conn, resolve_namespace_id
from service.email_voc import delegated_auth, graph_client, routing_service, teams_notify
from service.email_voc.service import analyze_email, check_relevance

logger = logging.getLogger(__name__)

# 메일 1건당 임베딩+RAG검색+LLM호출+Teams발송까지 걸리는 체인이라 순차 처리하면
# 메일함당 메일이 많을 때 수 분씩 걸릴 수 있다. LLM/DB에 과부하를 주지 않는 선에서
# 동시 처리 상한을 두고 asyncio.gather로 병렬화한다.
_MAX_CONCURRENT_MESSAGES = 5


async def record_analysis(
    namespace_id: int,
    routing_id: Optional[int],
    source_message_id: str,
    mailbox_upn: str,
    subject: str,
    sender: str,
    received_at: Optional[str],
    body: str,
    analysis: dict,
    *,
    status: str = "analyzed",
) -> Optional[dict]:
    """분석 결과를 저장한다. 같은 (namespace, source_message_id)가 이미 있으면
    저장하지 않고 None을 반환한다 — §9의 재조회 윈도우 겹침에 대한 중복 방지.

    status: 기본은 'analyzed'(이후 mark_notify_result가 notified/notify_failed로 갱신).
    관련지식 임계치 미달로 LLM을 태우지 않고 건너뛴 경우 'skipped_relevance'로 직접
    저장한다(컬럼이 VARCHAR(20)이라 'skipped_low_relevance'는 21자로 안 들어감 —
    실제로 겪고 줄임) — 이 경우 category/severity 등은 None으로 들어온다.
    """
    # asyncpg는 timestamptz 파라미터에 datetime 객체를 요구한다 — SQL 쪽 ::timestamptz
    # 캐스트는 서버 파싱 단계라 클라이언트 바이너리 인코딩(문자열→datetime) 문제를 못 고친다.
    # Graph API(graph_client.fetch_messages)가 반환하는 receivedDateTime도 ISO 문자열이라
    # 실제 폴링에서도 그대로 거쳐가는 경로라 여기서 변환해야 한다.
    received_at_dt: Optional[datetime] = None
    if received_at:
        received_at_dt = datetime.fromisoformat(received_at.replace("Z", "+00:00"))

    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ops_email_analysis
                (namespace_id, routing_id, source_message_id, mailbox_upn, subject, sender,
                 received_at, body, category, severity, mismatch_flagged, knowledge_ref_ids,
                 resolution_draft, reasoning, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
            ON CONFLICT (namespace_id, source_message_id) DO NOTHING
            RETURNING id
            """,
            namespace_id, routing_id, source_message_id, mailbox_upn, subject, sender,
            received_at_dt, body, analysis["category"], analysis["severity"],
            analysis["mismatch_flagged"], analysis["knowledge_ref_ids"],
            analysis["resolution_draft"], analysis["reasoning"], status,
        )
    if row is None:
        return None
    return {"id": row["id"]}


async def _existing_message_ids(namespace_id: int, message_ids: list[str]) -> set[str]:
    """이미 저장된 source_message_id 집합을 한 번의 쿼리로 조회한다.

    §9 재조회 윈도우(lookback_days) 특성상, 매 폴링 사이클마다 같은 기간을
    다시 fetch하므로 대부분의 메일이 이미 처리된 것들이다. 이 사전 필터가
    없으면 이미 처리된 메일까지 매 사이클마다 관련지식 검색+LLM 분석을 다시
    돌리고 나서야 record_analysis()의 ON CONFLICT에서 중복임을 알게 된다 —
    실측으로 확인된 성능 문제(폴링 주기 1분에 사이클 하나가 몇 분씩 걸림).
    (namespace_id, source_message_id)에 UNIQUE 인덱스가 있어 이 배치 조회는 저렴하다.
    """
    if not message_ids:
        return set()
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT source_message_id FROM ops_email_analysis WHERE namespace_id = $1 AND source_message_id = ANY($2::text[])",
            namespace_id, message_ids,
        )
    return {r["source_message_id"] for r in rows}


async def mark_notify_result(analysis_id: int, ok: bool, error: Optional[str]) -> None:
    # $2를 status 대입과 문자열 비교에 이중으로 쓰면 asyncpg/Postgres가 타입을
    # 하나로 못 정해 AmbiguousParameterError가 난다 — ok를 별도 파라미터로 분리.
    async with get_conn() as conn:
        await conn.execute(
            """UPDATE ops_email_analysis
               SET status = $2, teams_sent_at = CASE WHEN $4 THEN NOW() ELSE teams_sent_at END,
                   notify_error = $3
               WHERE id = $1""",
            analysis_id, "notified" if ok else "notify_failed", error, ok,
        )


async def run_manual_collection(
    namespace: str, date_from: date, date_to: date, *,
    credentials: Optional[dict] = None, access_token: Optional[str] = None,
    skip_credential_resolution: bool = False,
) -> dict:
    """관리자가 지정한 기간(date_from~date_to)에 대해 등록된 모든 파트 메일함을 수집+분석+발송.

    credentials: 이미 복호화한 자격증명을 넘겨받으면 재조회하지 않는다 — 스케줄러가
    여러 namespace를 순회할 때(scheduler.run_cycle_now) 매 namespace마다 같은
    자격증명을 다시 복호화하는 걸 피하기 위함. 넘기지 않으면(관리자 수동 실행 등
    단발성 호출) 여기서 직접 조회한다.

    access_token: 이미 발급된 토큰을 직접 넘기면 Application 권한 토큰 발급
    (graph_client.get_access_token)을 건너뛰고 이 토큰을 모든 메일함 조회에 그대로
    쓴다. 명시적으로 넘기지 않고 Application 권한 자격증명도 미설정 상태라면,
    Delegated 권한 로그인 세션(delegated_auth)이 있는지 마지막으로 확인해 있으면
    그 토큰을 조용히 재사용한다 — Application 권한(Track B) 승인 전에도 관리자
    화면의 폴링 토글/수동실행 버튼이 그대로 동작하게 하기 위함. 이 경우 라우팅의
    mailbox_upn은 로그인한 본인 메일 주소와 일치해야 한다(Delegated 토큰은 본인
    메일함만 접근 가능).

    skip_credential_resolution: 호출부(scheduler)가 credentials/access_token을
    이미 스스로 시도해서 결정한 최종값(둘 다 None일 수도 있음)을 넘겼다는 표시.
    True면 위 자동 폴백(get_graph_credentials_decrypted/get_access_token_silent
    재시도)을 건너뛴다 — 안 그러면 namespace가 여러 개일 때 Delegated silent 토큰
    갱신(네트워크 호출)을 namespace 수만큼 반복하게 된다.
    """
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
    if ns_id is None:
        raise ValueError(f"존재하지 않는 namespace: {namespace}")

    if not skip_credential_resolution:
        if access_token is None and credentials is None:
            credentials = await routing_service.get_graph_credentials_decrypted()
        if access_token is None and not credentials:
            access_token = await delegated_auth.get_access_token_silent()
    routing_rows = [r for r in await routing_service.list_routing(namespace) if r["is_active"]]
    # §9 관련지식 임계치 — namespace와 무관하게 하나뿐이라 메일 단위로 반복 조회하지
    # 않도록 여기서 한 번만 읽는다.
    relevance_min_score = (await routing_service.get_settings())["email_relevance_min_score"]

    received_after = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    received_before = datetime.combine(date_to, time.max, tzinfo=timezone.utc)

    results = []
    for routing in routing_rows:
        result = {
            "mailbox_upn": routing["mailbox_upn"], "part": routing["part"],
            "ok": False, "error": None, "fetched": 0, "analyzed": 0,
            "skipped_duplicate": 0, "skipped_low_relevance": 0, "notified": 0, "notify_failed": 0,
        }

        if access_token is None and not credentials:
            result["error"] = (
                "Graph API 자격증명이 설정되지 않았습니다 — Application 권한(관리자 설정) "
                "또는 Delegated 로그인(VOC 이메일 > 개인 계정 로그인) 중 하나가 필요합니다"
            )
            results.append(result)
            continue

        try:
            token = access_token if access_token is not None else await graph_client.get_access_token(
                credentials["tenant_id"], credentials["client_id"], credentials["client_secret"],
            )
            messages = await graph_client.fetch_messages(
                routing["mailbox_upn"], token, received_after, received_before,
            )
        except (graph_client.GraphAuthError, graph_client.GraphApiError) as e:
            result["error"] = str(e)
            results.append(result)
            continue
        except Exception as e:  # 예상 못한 오류도 이 메일함만 실패 처리하고 나머지는 계속 진행
            logger.exception("메일함 %s 수집 중 예상치 못한 오류", routing["mailbox_upn"])
            result["error"] = f"수집 실패: {e}"
            results.append(result)
            continue

        result["fetched"] = len(messages)

        # 재조회 윈도우(lookback_days)가 매 사이클 겹치므로, fetch된 메일 대부분은
        # 이미 처리된 것들이다 — 관련지식 검색+LLM 분석을 시작하기 전에 먼저 걸러낸다.
        # (실측: 이 필터 없이 144건 중 130건 재분석에 약 190초 소요됨)
        already_processed = await _existing_message_ids(ns_id, [m["id"] for m in messages])
        result["skipped_duplicate"] = len(already_processed)
        messages = [m for m in messages if m["id"] not in already_processed]

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_MESSAGES)

        async def _process_one(msg: dict) -> str:
            async with semaphore:
                # §9 관련지식 사전 필터 — 모든 메일을 무조건 LLM에 태우면 VOC와 무관한
                # 메일(스팸/사내공지/CC참조 등)까지 비용을 쓰고 Teams 알림 노이즈가
                # 생긴다. 등록된 지식과의 최고 유사도가 임계치 미만이면 LLM 호출과
                # Teams 발송을 건너뛰고 이력에만 기록한다.
                relevance = await check_relevance(namespace, msg["subject"], msg["body"])
                if relevance.top_score < relevance_min_score:
                    saved = await record_analysis(
                        ns_id, routing["id"], msg["id"], routing["mailbox_upn"],
                        msg["subject"], msg["sender"], msg["received_at"], msg["body"],
                        {
                            "category": None, "severity": None, "mismatch_flagged": False,
                            "knowledge_ref_ids": [], "resolution_draft": None,
                            "reasoning": (
                                f"관련 지식 최고 유사도({relevance.top_score:.2f})가 "
                                f"임계치({relevance_min_score:.2f}) 미만이라 분석을 건너뜀"
                            ),
                        },
                        status="skipped_relevance",  # ops_email_analysis.status는 VARCHAR(20) — 20자 제한
                    )
                    return "skipped_low_relevance" if saved else "skipped_duplicate"

                analysis = await analyze_email(
                    namespace, msg["subject"], msg["body"], part=routing["part"],
                    precomputed=relevance,
                )
                saved = await record_analysis(
                    ns_id, routing["id"], msg["id"], routing["mailbox_upn"],
                    msg["subject"], msg["sender"], msg["received_at"], msg["body"], analysis,
                )
                if saved is None:
                    return "skipped_duplicate"

                # §10 — 심각도와 무관하게 항상 발송하되, 카드 포맷(강조 여부)만 심각도에 따라 달라진다
                if routing.get("teams_webhook_url"):
                    message = teams_notify.build_teams_message(
                        subject=msg["subject"], sender=msg["sender"], part=routing["part"],
                        analysis=analysis, oncall_contact_name=routing.get("oncall_contact_name"),
                    )
                    ok, error = await teams_notify.send_teams_notification(routing["teams_webhook_url"], message)
                    await mark_notify_result(saved["id"], ok, error)
                    return "notified" if ok else "notify_failed"
                return "analyzed"

        outcomes = await asyncio.gather(*(_process_one(m) for m in messages), return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                logger.exception("메일 처리 중 예외 (해당 건만 skip)", exc_info=outcome)
                continue
            if outcome == "skipped_duplicate":
                result["skipped_duplicate"] += 1
            elif outcome == "skipped_low_relevance":
                result["skipped_low_relevance"] += 1
            elif outcome == "notified":
                result["analyzed"] += 1
                result["notified"] += 1
            elif outcome == "notify_failed":
                result["analyzed"] += 1
                result["notify_failed"] += 1
            elif outcome == "analyzed":
                result["analyzed"] += 1

        result["ok"] = True
        results.append(result)

    return {"date_from": date_from, "date_to": date_to, "mailboxes": results}


async def list_history(namespace: str, limit: int = 50, offset: int = 0) -> list[dict]:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT a.id, a.mailbox_upn, r.part, a.subject, a.sender,
                   a.received_at::text, a.category, a.severity, a.mismatch_flagged,
                   a.knowledge_ref_ids, a.resolution_draft, a.reasoning, a.status,
                   a.teams_sent_at::text, a.notify_error, a.created_at::text
            FROM ops_email_analysis a
            LEFT JOIN ops_voc_routing r ON a.routing_id = r.id
            WHERE a.namespace_id = $1
            ORDER BY COALESCE(a.received_at, a.created_at) DESC
            LIMIT $2 OFFSET $3
            """,
            ns_id, limit, offset,
        )
    return [dict(r) for r in rows]
