"""평가 게이트 판정 기록 읽기 (2026-10-23, #86 eval_gate_run — eval-gate-redesign §4-3). 쓰기는 scripts/eval_gate.py --save뿐,
화면·API는 저장된 결과를 읽기만 한다(통계 재계산 없음). 기준선 지정만 관리자가 바꾼다."""
import json
from typing import Optional

from core.database import get_conn

BASELINE_LOCK = 86_001   # scripts/eval_gate.py와 같은 값 — 기준선을 옮기는 두 길이 한 번에 하나만(리뷰 M1)
_JSON = ("retrieval", "answers", "summary", "judge_meta")


def _row(r) -> dict:
    d = dict(r)
    for k in _JSON:
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    return d


async def list_runs(limit: int) -> list[dict]:
    """이력 — 목록에 필요한 헤드라인만(문항별 qid 목록은 상세에서). 최신순."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            """SELECT id, run_at, created_by, label, kind, golden_version, verdict, is_baseline, git_commit, notes, judge_meta,
                      summary - 'inputs' AS summary,
                      jsonb_array_length(COALESCE(retrieval->'down', '[]'::jsonb)) AS retrieval_down,
                      jsonb_array_length(COALESCE(retrieval->'up', '[]'::jsonb)) AS retrieval_up,
                      answers->'failed' AS answers_failed,
                      answers->'groups'->'전체' AS answers_total
               FROM eval_gate_run ORDER BY run_at DESC, id DESC LIMIT $1""", limit)
    out = []
    for r in rows:
        d = _row(r)
        for k in ("answers_failed", "answers_total"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        # 목록에선 qid 목록을 뺀다(상세에서) — 문항 수만큼 커진다
        if d.get("summary") and isinstance(d["summary"].get("answers"), dict):
            wrong = d["summary"]["answers"].pop("retrieval_ok_answer_wrong", None)
            d["summary"]["answers"]["retrieval_ok_answer_wrong_n"] = len(wrong) if wrong is not None else None
        out.append(d)
    return out


async def get_run(run_id: int) -> Optional[dict]:
    async with get_conn() as conn:
        r = await conn.fetchrow("SELECT * FROM eval_gate_run WHERE id = $1", run_id)
    return _row(r) if r else None


async def set_baseline(run_id: int) -> Optional[dict]:
    """기준선 옮기기 — 실패한 판정은 기준선이 될 수 없다(나빠진 상태를 기준으로 삼으면 다음 게이트가 하락을 못 본다).
    한 트랜잭션에서 옛 줄을 내리고 새 줄을 올린다(부분 고유 인덱스 — 기준선 한 행). 없는 id면 None, 실패 판정이면 ValueError."""
    async with get_conn() as conn:
        async with conn.transaction():
            # 행 잠금만으론 부족 — 두 요청이 서로 다른 행을 올리면 둘째가 고유 인덱스에 부딪혀 500(리뷰 M1)
            await conn.execute("SELECT pg_advisory_xact_lock($1)", BASELINE_LOCK)
            r = await conn.fetchrow("SELECT id, verdict, is_baseline FROM eval_gate_run WHERE id = $1", run_id)
            if not r:
                return None
            if r["verdict"] == "fail":
                raise ValueError("실패한 판정은 기준선으로 지정할 수 없습니다.")
            if not r["is_baseline"]:
                await conn.execute("UPDATE eval_gate_run SET is_baseline = FALSE WHERE is_baseline")
                await conn.execute("UPDATE eval_gate_run SET is_baseline = TRUE WHERE id = $1", run_id)
    return {"id": run_id, "is_baseline": True}
