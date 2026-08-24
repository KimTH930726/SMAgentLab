"""§9 폴링 자동화 — 백그라운드 스케줄러 (docs/email-analysis-channel-plan.md §5 Phase 2).

지금까지는 "수동 1회성 실행" 버튼(POST /collect/run)만 동작했다 — 관리자 화면의
"폴링 자동화 ON/OFF" 토글은 설정값만 저장할 뿐 실제로 주기적으로 도는 무언가가
없었다. 이 모듈이 그 실행부다.

설계 원칙:
- email_collection_enabled/email_polling_interval_minutes/email_lookback_days를
  매 사이클마다 새로 읽는다 — 관리자가 화면에서 값을 바꾸면 재시작 없이 다음
  사이클부터 바로 반영된다.
- 꺼져 있어도 루프 자체는 계속 돌면서 30초마다 다시 확인한다 — 켜는 순간
  재시작 없이 스케줄러가 잡아챈다.
- 이전 사이클이 아직 실행 중이면 다음 사이클을 건너뛴다(중복 실행 방지) —
  메일함이 많거나 LLM이 느려서 한 사이클이 폴링 주기보다 길어져도 안전하다.
- 한 namespace 처리 중 예외가 나도 다른 namespace는 계속 처리한다
  (run_manual_collection 자체도 메일함 단위로 이미 같은 원칙을 적용하고 있음).
"""
import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from core.database import get_conn
from service.email_voc import delegated_auth, pipeline, retention, routing_service

logger = logging.getLogger(__name__)
# 이 프로젝트 전체에 logging.basicConfig()가 없어 루트 로거가 기본 WARNING 레벨이라
# logger.info()가 어디서도 출력되지 않는다(발견 시점: 스케줄러 로그가 전혀 안 보임).
# 스케줄러는 무인 백그라운드 잡이라 운영 가시성이 핵심이므로, 전역 로깅 설정을
# 건드리는 대신 이 모듈만 자체 핸들러를 달아 항상 보이게 한다.
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setLevel(logging.INFO)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)
    logger.propagate = False

_DISABLED_CHECK_INTERVAL_SECONDS = 30
_task: Optional[asyncio.Task] = None
_running_cycle = False
_last_cleanup_date: Optional[date] = None


async def _maybe_run_cleanup() -> None:
    """하루 1회로 제한해서 30일 보관 정책을 실행한다.

    폴링 on/off와 무관하게 매 루프 반복마다 확인한다 — 폴링이 꺼져 있어도(Track B
    대기 중이라도) 이미 쌓인 데이터는 계속 정리돼야 한다.
    """
    global _last_cleanup_date
    today = date.today()
    if _last_cleanup_date == today:
        return
    try:
        await retention.cleanup_old_records()
    except Exception:
        logger.exception("[VOC 스케줄러] 보관정책 정리 중 오류 — 내일 다시 시도")
        return
    _last_cleanup_date = today


async def _list_namespaces_with_active_routing() -> list[str]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT n.name
            FROM ops_voc_routing r
            JOIN ops_namespace n ON r.namespace_id = n.id
            WHERE r.is_active = true
            """
        )
    return [r["name"] for r in rows]


async def _record_cycle(started_at: datetime, finished_at: datetime, agg: dict, errors: list[str]) -> None:
    async with get_conn() as conn:
        await conn.execute(
            """
            INSERT INTO ops_email_poll_cycle
                (started_at, finished_at, namespaces_processed, mailboxes_ok, mailboxes_failed,
                 total_fetched, total_analyzed, total_notified, total_notify_failed,
                 total_skipped_duplicate, total_skipped_low_relevance, total_skipped_not_it, error_summary)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            """,
            started_at, finished_at, agg["namespaces_processed"], agg["mailboxes_ok"], agg["mailboxes_failed"],
            agg["total_fetched"], agg["total_analyzed"], agg["total_notified"], agg["total_notify_failed"],
            agg["total_skipped_duplicate"], agg["total_skipped_low_relevance"], agg["total_skipped_not_it"],
            ("\n".join(errors) if errors else None),
        )


async def run_cycle_now(settings: Optional[dict] = None) -> None:
    """한 사이클을 즉시 실행 — 루프 내부/테스트에서 모두 재사용.

    settings: _loop()이 email_collection_enabled 확인 차 이미 조회해둔 값을 그대로
    넘겨받아 같은 사이클 안에서 설정을 두 번 조회하지 않는다. 단독 호출(테스트 등)
    시에는 여기서 직접 조회한다.

    사이클 결과(성공/실패 메일함 수, 처리 건수, 에러 요약)를 ops_email_poll_cycle에
    남긴다 — 관리자 화면의 "실시간 상태"/"폴링 이력"이 이 테이블을 읽는다.
    """
    global _running_cycle
    if _running_cycle:
        logger.warning("[VOC 스케줄러] 이전 사이클이 아직 실행 중 — 이번 사이클은 건너뜁니다.")
        return
    _running_cycle = True
    started_at = datetime.now(timezone.utc)
    agg = {
        "namespaces_processed": 0, "mailboxes_ok": 0, "mailboxes_failed": 0,
        "total_fetched": 0, "total_analyzed": 0, "total_notified": 0,
        "total_notify_failed": 0, "total_skipped_duplicate": 0, "total_skipped_low_relevance": 0,
        "total_skipped_not_it": 0,
    }
    errors: list[str] = []
    try:
        if settings is None:
            settings = await routing_service.get_settings()
        date_to = date.today()
        date_from = date_to - timedelta(days=settings["email_lookback_days"])

        namespaces = await _list_namespaces_with_active_routing()
        if not namespaces:
            logger.info("[VOC 스케줄러] 활성 라우팅이 등록된 namespace가 없어 이번 사이클은 스킵합니다.")
            return

        # 자격증명은 namespace와 무관하게 하나뿐이라 루프 밖에서 한 번만 복호화한다
        # (namespace마다 다시 복호화하면 Fernet 연산+DB 조회가 namespace 수만큼 중복됨).
        credentials = await routing_service.get_graph_credentials_decrypted()
        # Application 권한이 아직 없으면(Track B 승인 대기) Delegated 로그인 세션으로
        # 대체 — 이것도 같은 이유로 namespace 루프 밖에서 한 번만 조회한다(매
        # namespace마다 MSAL silent 토큰 갱신을 반복 호출하지 않도록).
        access_token = None if credentials else await delegated_auth.get_access_token_silent()

        for namespace in namespaces:
            agg["namespaces_processed"] += 1
            try:
                result = await pipeline.run_manual_collection(
                    namespace, date_from, date_to, credentials=credentials, access_token=access_token,
                    skip_credential_resolution=True,
                )
                summary_parts = []
                for m in result["mailboxes"]:
                    agg["total_fetched"] += m["fetched"]
                    agg["total_analyzed"] += m["analyzed"]
                    agg["total_notified"] += m["notified"]
                    agg["total_notify_failed"] += m["notify_failed"]
                    agg["total_skipped_duplicate"] += m["skipped_duplicate"]
                    agg["total_skipped_low_relevance"] += m["skipped_low_relevance"]
                    agg["total_skipped_not_it"] += m["skipped_not_it"]
                    if m["ok"]:
                        agg["mailboxes_ok"] += 1
                        summary_parts.append(f"{m['mailbox_upn']}(fetched={m['fetched']},analyzed={m['analyzed']},notified={m['notified']})")
                    else:
                        agg["mailboxes_failed"] += 1
                        summary_parts.append(f"{m['mailbox_upn']}(에러: {m['error']})")
                        errors.append(f"{namespace}/{m['mailbox_upn']}: {m['error']}")
                logger.info("[VOC 스케줄러] namespace=%s 처리 완료 — %s", namespace, ", ".join(summary_parts) or "(라우팅 없음)")
            except Exception as e:
                logger.exception("[VOC 스케줄러] namespace=%s 처리 중 오류 — 다른 namespace는 계속 진행", namespace)
                errors.append(f"{namespace}: {e}")
    finally:
        finished_at = datetime.now(timezone.utc)
        await _record_cycle(started_at, finished_at, agg, errors)
        _running_cycle = False


async def get_status() -> dict:
    """관리자 화면 "실시간 상태" 카드용 — 지금 도는 중인지 + 마지막 사이클 결과 + 다음 예상 실행시각."""
    settings = await routing_service.get_settings()
    async with get_conn() as conn:
        last = await conn.fetchrow(
            "SELECT * FROM ops_email_poll_cycle ORDER BY started_at DESC LIMIT 1"
        )
    last_cycle = dict(last) if last else None
    next_estimated_at = None
    if settings["email_collection_enabled"] and last_cycle and last_cycle.get("finished_at"):
        next_estimated_at = last_cycle["finished_at"] + timedelta(minutes=settings["email_polling_interval_minutes"])
    return {
        "enabled": settings["email_collection_enabled"],
        "is_running_now": _running_cycle,
        "polling_interval_minutes": settings["email_polling_interval_minutes"],
        "last_cycle": last_cycle,
        "next_estimated_at": next_estimated_at,
    }


async def list_poll_cycles(limit: int = 20, offset: int = 0) -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT * FROM ops_email_poll_cycle ORDER BY started_at DESC LIMIT $1 OFFSET $2",
            limit, offset,
        )
    return [dict(r) for r in rows]


async def _loop() -> None:
    logger.info("[VOC 스케줄러] 시작됨 (%d초 간격으로 활성화 여부 확인)", _DISABLED_CHECK_INTERVAL_SECONDS)
    while True:
        await _maybe_run_cleanup()

        try:
            settings = await routing_service.get_settings()
        except Exception:
            logger.exception("[VOC 스케줄러] 설정 조회 실패 — %d초 후 재시도", _DISABLED_CHECK_INTERVAL_SECONDS)
            await asyncio.sleep(_DISABLED_CHECK_INTERVAL_SECONDS)
            continue

        if not settings["email_collection_enabled"]:
            await asyncio.sleep(_DISABLED_CHECK_INTERVAL_SECONDS)
            continue

        try:
            await run_cycle_now(settings)
        except Exception:
            logger.exception("[VOC 스케줄러] 사이클 실행 중 예외 — 다음 사이클에 재시도")

        interval_seconds = max(60, settings["email_polling_interval_minutes"] * 60)
        await asyncio.sleep(interval_seconds)


def start_scheduler() -> None:
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop())


async def stop_scheduler() -> None:
    """루프 태스크를 취소하고 실제로 멈출 때까지 기다린다.

    cancel()만 하고 기다리지 않으면, 마침 사이클이 도는 중이었을 때 그 finally
    블록(_record_cycle의 DB 쓰기)이 채 끝나기 전에 main.py가 곧이어 커넥션 풀을
    닫아버릴 수 있다 — 셧다운 시 드물게 "pool is closed" 에러 로그가 남는 원인.
    """
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
