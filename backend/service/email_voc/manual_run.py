"""[지금 실행] 뒤에서 돌리기 + 진행 기록(11/06, docs/backlog.md "오래 걸리는데 진행 표시 없는 동작" ①).

전에는 요청 하나 안에서 수집 → 메일마다 LLM 분석 → 발송을 다 끝내고 응답해, 새 메일이 많으면 버튼만 몇 분 돌았다(실측 130건 190초).
이제 잡기(같은 namespace가 도는 중이면 거절) → 뒤에서 실행 → 화면은 status를 몇 초마다 읽는다(ProgressBar).

진행은 서버 메모리에만 둔다 — 백엔드가 재시작되면 돌던 작업도 같이 끊기므로 DB에 남겨도 이어 갈 수 없다.
재시작 뒤 status는 None("진행 기록 없음")이고 다시 누르면 된다.
전제: 백엔드 프로세스 하나(uvicorn 워커 1, Dockerfile CMD). 워커를 늘리면 잡기·상태가 워커마다 따로 놀아 DB로 옮겨야 한다(리뷰 11/06).
"""
import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Optional

from core.database import get_conn, resolve_namespace_id
from service.email_voc import pipeline

logger = logging.getLogger(__name__)

_runs: dict[str, dict] = {}       # namespace → 마지막 실행 상태(끝난 뒤에도 결과를 보여 주려고 남김 — namespace 수만큼만)
_tasks: set[asyncio.Task] = set()  # 뒤에서 도는 작업이 GC로 사라지지 않게 잡아 둔다


class AlreadyRunning(Exception):
    pass


def status(namespace: str) -> Optional[dict]:
    return _runs.get(namespace)


async def start(namespace: str, date_from: date, date_to: date) -> dict:
    async with get_conn() as conn:
        if await resolve_namespace_id(conn, namespace) is None:
            raise ValueError(f"존재하지 않는 namespace: {namespace}")
    # 확인과 등록 사이에 await가 없어 두 요청이 같이 들어와도 하나만 잡힌다(이벤트 루프 하나)
    cur = _runs.get(namespace)
    if cur is not None and cur["running"]:
        raise AlreadyRunning()
    state = {"running": True, "phase": "fetch", "mailbox": 0, "mailboxes": 0, "done": 0, "total": 0,
             "date_from": date_from, "date_to": date_to, "started_at": _now(), "finished_at": None, "result": None, "error": None}
    _runs[namespace] = state
    task = asyncio.create_task(_run(namespace, date_from, date_to, state))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return state


async def _run(namespace: str, date_from: date, date_to: date, state: dict) -> None:
    try:
        state["result"] = await pipeline.run_manual_collection(namespace, date_from, date_to, progress=state)
    except ValueError as e:
        state["error"] = str(e)
    except Exception as e:
        logger.exception("VOC 지금 실행 실패: %s", namespace)
        state["error"] = f"실행 실패({type(e).__name__}) — 서버 로그 확인"
    finally:
        state["running"] = False
        state["finished_at"] = _now()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
