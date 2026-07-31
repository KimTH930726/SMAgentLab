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
from service.email_voc import graph_client, routing_service, teams_notify
from service.email_voc.service import analyze_email

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
) -> Optional[dict]:
    """분석 결과를 저장한다. 같은 (namespace, source_message_id)가 이미 있으면
    저장하지 않고 None을 반환한다 — §9의 재조회 윈도우 겹침에 대한 중복 방지."""
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
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,'analyzed')
            ON CONFLICT (namespace_id, source_message_id) DO NOTHING
            RETURNING id
            """,
            namespace_id, routing_id, source_message_id, mailbox_upn, subject, sender,
            received_at_dt, body, analysis["category"], analysis["severity"],
            analysis["mismatch_flagged"], analysis["knowledge_ref_ids"],
            analysis["resolution_draft"], analysis["reasoning"],
        )
    if row is None:
        return None
    return {"id": row["id"]}


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
    namespace: str, date_from: date, date_to: date, *, credentials: Optional[dict] = None,
) -> dict:
    """관리자가 지정한 기간(date_from~date_to)에 대해 등록된 모든 파트 메일함을 수집+분석+발송.

    credentials: 이미 복호화한 자격증명을 넘겨받으면 재조회하지 않는다 — 스케줄러가
    여러 namespace를 순회할 때(scheduler.run_cycle_now) 매 namespace마다 같은
    자격증명을 다시 복호화하는 걸 피하기 위함. 넘기지 않으면(관리자 수동 실행 등
    단발성 호출) 여기서 직접 조회한다.
    """
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
    if ns_id is None:
        raise ValueError(f"존재하지 않는 namespace: {namespace}")

    if credentials is None:
        credentials = await routing_service.get_graph_credentials_decrypted()
    routing_rows = [r for r in await routing_service.list_routing(namespace) if r["is_active"]]

    received_after = datetime.combine(date_from, time.min, tzinfo=timezone.utc)
    received_before = datetime.combine(date_to, time.max, tzinfo=timezone.utc)

    results = []
    for routing in routing_rows:
        result = {
            "mailbox_upn": routing["mailbox_upn"], "part": routing["part"],
            "ok": False, "error": None, "fetched": 0, "analyzed": 0,
            "skipped_duplicate": 0, "notified": 0, "notify_failed": 0,
        }

        if not credentials:
            result["error"] = "Graph API 자격증명이 아직 설정되지 않았습니다 (Track B 승인 대기 — 관리자 설정에서 입력 필요)"
            results.append(result)
            continue

        try:
            token = await graph_client.get_access_token(
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

        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_MESSAGES)

        async def _process_one(msg: dict) -> str:
            async with semaphore:
                analysis = await analyze_email(
                    namespace, msg["subject"], msg["body"], part=routing["part"],
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
