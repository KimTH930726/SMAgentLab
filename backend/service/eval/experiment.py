"""개선 후보 실험 실행기(10/30 사용자 "실험해 보기를 누르면 실제로 돌리고 측정까지 — 결과를 보고 운영 반영을 정한다").

후보가 기계로 적용할 수 있는 변경(change)을 들고 있으면 [실험해 보기] 때 작업 하나를 대기열에 넣는다. 작업 = 같은 날 같은 방식으로
지금 설정(전)과 바꾼 설정(후)을 재고 → eval_gate.py로 판정해 후보에 연결(--candidate, 기준선은 안 옮김 --keep-baseline).
운영은 바꾸지 않는다 — 변경은 측정 프로세스(하위 프로세스)의 환경·옵션에만. 운영 반영은 사람이 결과를 보고 [운영에 반영했어요].

change 모양(후보가 들고 있음, improve._cand):
  {"env": {"POLICY_KEYWORD_RESCUE_SLOTS": "5"}}        — 측정 프로세스 환경 변수(모듈 상수가 읽는 값)
  {"prompt_append": "- …"}                              — 지금 채팅 지시문 끝 [원칙]에 한 줄 더한 후보 지시문
  {"policy_top_k": 0, "no_expand": true}               — 업무지식 답 측정의 문맥 옵션
한 번에 하나만 돈다(게이트웨이 한도). 서버가 다시 시작되면 돌던 작업은 실패("다시 시작돼 중단")로 — 조용히 멈춰 있지 않게.
측정 스크립트는 숫자만 찍는다(원문·질문·답은 eval_out/ 파일에만 — 컨플 보안 규칙). 진행은 그 출력의 "n/m" 줄로.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

from core.database import get_conn

logger = logging.getLogger(__name__)

APP = Path(__file__).resolve().parents[2]          # /app (backend)
OUT = APP / "eval_out" / "exp"                      # git 제외 — 원문 파생물
REPEAT = 2                                          # 운영 반영 조건(2회 이상, improve.confident_wrong_error)과 같게
KNOWLEDGE_SET = "eval_out/confluence_synth.jsonl"
_PROGRESS = re.compile(r"^\s*(\d+)/(\d+)\s*$")
_SAVED = re.compile(r"기록 #(\d+) 저장")
_POLL_SECONDS = 5


def runnable(c: dict, axis: str) -> Optional[str]:
    """자동 실험이 되면 None, 안 되면 사람이 읽을 이유 — 화면이 그대로 보인다."""
    ch = c.get("change") or {}
    if not ch:
        return "바꿀 것이 글로만 있어 자동 실험 불가 — 개발 작업 필요"
    if c.get("measure") not in ("retrieval", "answers"):
        return "이미 다시 계산한 후보(LLM 없음) — 실험 없이 결과가 위에 있어요"
    if (ch.get("policy_top_k") is not None or ch.get("no_expand") or ch.get("verify")) and axis != "knowledge":
        return "문맥 옵션·근거 확인 실험은 업무지식 축만"
    return None


_SECTION = re.compile(r"^\[[^\]]+\]\s*$")   # 줄 전체가 [머리]인 줄 — 규칙 안의 "[참고 문서]"는 머리가 아니다(리뷰 10/30 H1: 첫 규칙을 반으로 잘랐다)


def candidate_prompt(append: str, current: str) -> str:
    """지금 지시문 [원칙] 절 끝(다음 [머리] 줄 앞)에 한 줄 — 원래 글은 한 글자도 안 바뀐다(테스트: 넣은 줄을 빼면 원문).
    [원칙] 머리 줄이 없으면 맨 끝에."""
    line = append if append.startswith("-") else f"- {append}"
    lines = current.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.strip() == "[원칙]"), None)
    if start is None:
        return current + "\n" + line
    end = next((i for i in range(start + 1, len(lines)) if _SECTION.match(lines[i])), len(lines))
    while end > start + 1 and not lines[end - 1].strip():   # 절 끝의 빈 줄 앞에 넣는다
        end -= 1
    return "\n".join(lines[:end] + [line] + lines[end:])


def plan(job_id: int, cand: dict, axis: str, *, label: str, by: str, prompt_file: Optional[str] = None) -> list[dict]:
    """작업 단계 = [{stage, argv, env}] — 순수 함수(테스트가 모양을 확인). 경로는 /app 기준 상대."""
    ch = cand.get("change") or {}
    env = {k: str(v) for k, v in (ch.get("env") or {}).items()}
    py = sys.executable
    d = f"eval_out/exp/job{job_id}"
    steps: list[dict] = []
    gate = [py, "scripts/eval_gate.py", "--axis", axis, "--save", label, "--candidate", str(cand["id"]), "--keep-baseline", "--by", by]
    if axis == "policy" and cand["measure"] == "retrieval":
        steps.append({"stage": "지금 설정 검색", "argv": [py, "scripts/eval_golden_v0.py", "--details", f"{d}_retr_before.jsonl"], "env": {}})
        steps.append({"stage": "바꾼 설정 검색", "argv": [py, "scripts/eval_golden_v0.py", "--details", f"{d}_retr_after.jsonl"], "env": env})
        gate += ["--retrieval-before", f"{d}_retr_before.jsonl", "--retrieval-after", f"{d}_retr_after.jsonl"]
    elif axis == "policy":
        for side, e in (("before", {}), ("after", env)):
            name = "지금 설정" if side == "before" else "바꾼 설정"
            collect = [py, "scripts/eval_answers.py", "collect", "--out", f"{d}_ans_{side}.jsonl", "--repeat", str(REPEAT)]
            if side == "after" and prompt_file:
                collect += ["--prompt-file", prompt_file]
            steps.append({"stage": f"{name} 답", "argv": collect, "env": e})
            steps.append({"stage": f"{name} 채점", "argv": [py, "scripts/eval_answers.py", "judge", "--in", f"{d}_ans_{side}.jsonl",
                                                           "--out", f"{d}_judged_{side}.jsonl"], "env": {}})
        gate += ["--answers-before", f"{d}_judged_before.jsonl", "--answers-after", f"{d}_judged_after.jsonl"]
    elif cand["measure"] == "retrieval":   # 업무지식 검색
        steps.append({"stage": "지금 설정 검색", "argv": [py, "scripts/eval_knowledge_retrieval.py", "--set", KNOWLEDGE_SET,
                                                     "--details", f"{d}_retr_before.jsonl"], "env": {}})
        steps.append({"stage": "바꾼 설정 검색", "argv": [py, "scripts/eval_knowledge_retrieval.py", "--set", KNOWLEDGE_SET,
                                                     "--details", f"{d}_retr_after.jsonl"], "env": env})
        gate += ["--retrieval-before", f"{d}_retr_before.jsonl", "--retrieval-after", f"{d}_retr_after.jsonl"]
    else:                                  # 업무지식 답(답 생성 + 채점이 한 스크립트)
        base = [py, "scripts/eval_knowledge_answers.py", "--set", KNOWLEDGE_SET, "--repeat", str(REPEAT)]
        steps.append({"stage": "지금 설정 답·채점", "argv": base + ["--out", f"{d}_ans_before.jsonl"], "env": {}})
        after = base + ["--out", f"{d}_ans_after.jsonl"]
        if prompt_file:
            after += ["--prompt-file", prompt_file]
        if ch.get("policy_top_k") is not None:
            after += ["--policy-top-k", str(int(ch["policy_top_k"]))]
        if ch.get("no_expand"):
            after += ["--no-expand"]
        if ch.get("verify"):
            after += ["--verify"]
        steps.append({"stage": "바꾼 설정 답·채점", "argv": after, "env": env})
        gate += ["--answers-before", f"{d}_ans_before.jsonl", "--answers-after", f"{d}_ans_after.jsonl"]
    steps.append({"stage": "게이트 판정", "argv": gate, "env": {}})
    return steps


# ── 대기열 ────────────────────────────────────────────────────────────────────────────

ACTIVE = ("queued", "running")


async def enqueue(cand_id: int, user_id: Optional[int]) -> dict:
    """작업 하나를 대기열에 — 같은 후보에 돌고 있는 작업이 있으면 그것을 돌려준다(두 번 눌러도 하나, 부분 고유 인덱스)."""
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """INSERT INTO eval_experiment_job (candidate_id, created_by_user_id) VALUES ($1, $2)
               ON CONFLICT (candidate_id) WHERE status IN ('queued', 'running') DO NOTHING
               RETURNING id, status""", cand_id, user_id)
        for _ in range(3):   # 진행 중 작업이 그사이 끝나면 SELECT가 비고, 그땐 다시 넣는다(리뷰 L1)
            if row is not None:
                break
            row = await conn.fetchrow("SELECT id, status FROM eval_experiment_job WHERE candidate_id = $1 AND status = ANY($2::text[])",
                                      cand_id, list(ACTIVE))
            if row is None:
                row = await conn.fetchrow(
                    """INSERT INTO eval_experiment_job (candidate_id, created_by_user_id) VALUES ($1, $2)
                       ON CONFLICT (candidate_id) WHERE status IN ('queued', 'running') DO NOTHING RETURNING id, status""", cand_id, user_id)
    _wake.set()
    return dict(row) if row else {"id": None, "status": "queued"}


async def _set(job_id: int, **kw) -> None:
    cols = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(kw))
    async with get_conn() as conn:
        await conn.execute(f"UPDATE eval_experiment_job SET {cols} WHERE id = $1", job_id, *kw.values())


STEP_TIMEOUT = 6 * 3600     # 단계 하나 상한(정책서 답 2회 ~2시간) — 게이트웨이 호출이 멈추면 실행기 전체가 막히지 않게(리뷰 M3)
_CHECK_SECONDS = 30         # 이 간격으로 후보 결정이 바뀌었는지 본다 — 그만둔 후보에 몇 시간씩 게이트웨이를 쓰지 않게


class Stopped(Exception):
    """후보 결정이 실험 중(approved)에서 바뀌어 멈춤."""


async def _still_wanted(job_id: int) -> bool:
    async with get_conn() as conn:
        return bool(await conn.fetchval("SELECT c.status = 'approved' FROM eval_experiment_job j JOIN eval_candidate c ON c.id = j.candidate_id "
                                        "WHERE j.id = $1", job_id))


async def _run_step(job_id: int, step: dict, idx: int, n: int) -> tuple[int, list[str]]:
    """하위 프로세스 하나 — 출력은 숫자 줄만 쓰고(진행·기록 번호) 마지막 몇 줄은 실패 사유로.
    끝나지 않고 나가면(시간 초과·결정 변경·예외) 자식을 죽이고 기다린다 — 버려진 자식이 다음 작업과 같이 게이트웨이를 쓰지 않게(리뷰 M3)."""
    await _set(job_id, stage=f"{idx}/{n} {step['stage']}", done=0, total=0)
    proc = await asyncio.create_subprocess_exec(*step["argv"], cwd=str(APP), env={**os.environ, **step["env"]},
                                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, limit=1 << 20)
    tail: list[str] = []
    assert proc.stdout is not None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + STEP_TIMEOUT
    next_check = loop.time() + _CHECK_SECONDS
    finished = False
    try:
        while True:
            now = loop.time()
            if now >= deadline:
                raise asyncio.TimeoutError
            if now >= next_check:   # 출력이 계속 나와도 일정 간격으로 — 출력이 없을 때만 보면 진행 줄을 찍는 단계는 끝까지 안 멈췄다(리뷰 10/30 M1)
                next_check = now + _CHECK_SECONDS
                if not await _still_wanted(job_id):
                    raise Stopped
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=max(0.1, min(next_check, deadline) - now))
            except asyncio.TimeoutError:
                continue
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip()
            if not line or "Warning" in line or "warnings.warn" in line:
                continue
            tail = (tail + [line])[-6:]
            m = _PROGRESS.match(line)
            if m:
                await _set(job_id, done=int(m.group(1)), total=int(m.group(2)))
        code = await proc.wait()
        finished = True
        return code, tail
    finally:
        if not finished and proc.returncode is None:
            proc.kill()
            await proc.wait()


async def run_job(job_id: int) -> None:
    async with get_conn() as conn:
        j = await conn.fetchrow(
            """SELECT j.id, j.candidate_id, c.key, c.title, c.measure, c.change, g.axis, u.username
               FROM eval_experiment_job j JOIN eval_candidate c ON c.id = j.candidate_id
               JOIN eval_improvement_run i ON i.id = c.run_id JOIN eval_gate_run g ON g.id = i.gate_run_id
               LEFT JOIN ops_user u ON u.id = j.created_by_user_id WHERE j.id = $1""", job_id)
    cand = {**dict(j), "id": j["candidate_id"], "change": json.loads(j["change"]) if isinstance(j["change"], str) else j["change"]}
    why = runnable(cand, j["axis"])
    if why:
        await _set(job_id, status="failed", error=why, finished_at=_now())
        return
    OUT.mkdir(parents=True, exist_ok=True)
    prompt_file = None
    if (cand["change"] or {}).get("prompt_append"):
        from service.llm.base import resolve_system_prompt
        pf = OUT / f"job{job_id}_prompt.txt"
        pf.write_text(candidate_prompt(cand["change"]["prompt_append"], await resolve_system_prompt()), encoding="utf-8")
        prompt_file = str(pf.relative_to(APP))
    steps = plan(job_id, cand, j["axis"], label=f"실험 #{job_id} — 후보 #{cand['id']} {cand['title']}"[:120],
                 by=j["username"] or "experiment", prompt_file=prompt_file)
    await _set(job_id, status="running", started_at=_now())
    for k, step in enumerate(steps, 1):
        if not await _still_wanted(job_id):
            await _set(job_id, status="failed", finished_at=_now(), error="후보 결정이 바뀌어 멈췄어요(실험 중이 아님)")
            return
        try:
            code, tail = await _run_step(job_id, step, k, len(steps))
        except Stopped:
            await _set(job_id, status="failed", finished_at=_now(), error="후보 결정이 바뀌어 멈췄어요(실험 중이 아님)")
            return
        except asyncio.TimeoutError:
            await _set(job_id, status="failed", finished_at=_now(), error=f"{step['stage']} 시간 초과({STEP_TIMEOUT // 3600}시간)")
            return
        last_step = k == len(steps)
        if last_step:   # eval_gate는 게이트 실패에도 1을 돌려준다 — 기록이 저장됐으면 작업은 끝난 것(판정은 결과 칸에)
            saved = next((int(m.group(1)) for line in tail if (m := _SAVED.search(line))), None)
            if saved is not None:
                await _set(job_id, status="done", gate_run_id=saved, finished_at=_now(), stage=None)
                await _link_to_current(job_id, saved)
                return
        if code != 0 or last_step:
            await _set(job_id, status="failed", finished_at=_now(), error=f"{step['stage']} 실패(종료 코드 {code}){error_kind(tail)}")
            return


_KIND = re.compile(r"^(\w+(?:Error|Exception|Unavailable))\b")


def error_kind(tail: list[str]) -> str:
    """실패 사유는 예외 종류만 — 출력 줄을 그대로 화면에 옮기면 예외 메시지에 데이터가 섞일 수 있다(리뷰 L4, 컨플 보안)."""
    kinds = [m.group(1) for ln in tail if (m := _KIND.match(ln.strip()))]
    return f": {kinds[-1]}" if kinds else ""


async def _link_to_current(job_id: int, gate_run_id: int) -> None:
    """진단을 다시 돌려 후보 줄이 새로 생겼으면(작업은 eval_improve가 새 줄로 옮김) 측정도 지금 후보에 붙인다(리뷰 M1).
    eval_gate --candidate는 작업을 짤 때의 id에 붙였으니, 그 뒤 옮겨진 새 후보에도 같은 측정을 더한다(중복 없이)."""
    async with get_conn() as conn:
        await conn.execute("""UPDATE eval_candidate c SET measured_gate_run_ids = array_append(COALESCE(c.measured_gate_run_ids, '{}'), $2)
                              FROM eval_experiment_job j WHERE j.id = $1 AND c.id = j.candidate_id
                                AND NOT ($2 = ANY(COALESCE(c.measured_gate_run_ids, '{}')))""", job_id, gate_run_id)


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


_wake = asyncio.Event()
_task: Optional[asyncio.Task] = None


async def _loop() -> None:
    while True:
        nxt = None
        try:
            _wake.clear()   # 찾기 전에 — 찾은 뒤 지우면 그사이 들어온 작업 신호를 잃는다(리뷰 L2)
            async with get_conn() as conn:
                nxt = await conn.fetchval("SELECT id FROM eval_experiment_job WHERE status = 'queued' ORDER BY id LIMIT 1")
            if nxt is None:
                try:
                    await asyncio.wait_for(_wake.wait(), timeout=60)
                except asyncio.TimeoutError:
                    pass
                continue
            await run_job(nxt)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — 한 작업의 예외로 실행기가 멈추지 않게(작업은 실패로 남김)
            logger.warning("실험 실행기 오류: %s", e)
            try:
                if nxt is not None:
                    await _set(nxt, status="failed", error=f"실행기 오류: {type(e).__name__}", finished_at=_now())
            except Exception as e2:  # noqa: BLE001
                logger.warning("실험 작업 실패 기록도 실패: %s", e2)
            await asyncio.sleep(_POLL_SECONDS)


async def start() -> None:
    """기동 때 — 이전 프로세스가 돌리던 작업은 실패로 닫고(되살릴 길이 없다) 실행기를 띄운다."""
    global _task
    async with get_conn() as conn:
        n = await conn.execute("""UPDATE eval_experiment_job SET status = 'failed', finished_at = NOW(),
                                      error = '서버가 다시 시작돼 중단됨 — [다시 실행]으로 처음부터'
                                  WHERE status = 'running'""")
        if n and "UPDATE 0" not in n:
            logger.warning("[실험] 재시작으로 중단된 작업 정리: %s", n)
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_loop())


async def stop() -> None:
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
