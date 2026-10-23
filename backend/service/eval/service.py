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
                      retrieval IS NOT NULL AS has_retrieval_gate,   -- 검색 후 파일만 있으면 비교 없이 0/0으로 보였다(재리뷰)
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


async def get_baseline() -> Optional[dict]:
    """현재 기준선 상세(없으면 None) — 화면 [검색]·[답]은 목록 범위(최신 n)와 무관하게 이걸 읽는다(리뷰 M1)."""
    async with get_conn() as conn:
        r = await conn.fetchrow("SELECT * FROM eval_gate_run WHERE is_baseline")
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


# ── 자가 개선 루프(#87 — eval-gate-redesign §10): 진단·후보 읽기 + 사람 검수 ─────────────────────────────

_CAND_JSON = ("qids", "replay", "hypotheses")
REVIEW_STATUSES = ("proposed", "approved", "held", "rejected")


def _cand(r) -> dict:
    d = dict(r)
    for k in _CAND_JSON:
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    return d


async def latest_improvement() -> Optional[dict]:
    """가장 최근 진단 + 그 후보(흐름 순서 = 저장 순서). 없으면 None."""
    async with get_conn() as conn:
        run = await conn.fetchrow(
            """SELECT i.id, i.created_at, i.created_by, i.gate_run_id, i.diagnosis, i.hypotheses, i.model, g.label AS gate_label,
                      g.is_baseline AS gate_is_baseline
               FROM eval_improvement_run i JOIN eval_gate_run g ON g.id = i.gate_run_id ORDER BY i.created_at DESC, i.id DESC LIMIT 1""")
        if not run:
            return None
        cands = await conn.fetch(
            """SELECT c.*, g.label AS adopted_gate_label FROM eval_candidate c LEFT JOIN eval_gate_run g ON g.id = c.adopted_gate_run_id
               WHERE c.run_id = $1 ORDER BY c.id""", run["id"])
    d = dict(run)
    for k in ("diagnosis", "hypotheses"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    from service.eval.techniques import BY_KEY
    from trustkit.eval.diagnose import CAUSES
    # 원인 이름·볼 곳·기법 이름은 서버가 붙인다 — 화면이 표를 따로 들고 있으면 대장과 어긋난다
    return {"run": d, "causes": [{"key": k, "name": n, "where": w} for k, n, w in CAUSES],
            "candidates": [{**_cand(c), "technique_name": BY_KEY.get(c["technique"], {}).get("name", c["technique"])} for c in cands]}


async def review_candidate(cand_id: int, *, status: str, reason: str, user_id: int) -> Optional[dict]:
    """사람 검수 — 시도 승인·보류·기각(이유 필수)·되돌리기(proposed). 채택된 후보는 바꾸지 않는다(판정 기록과 묶임)."""
    if status not in REVIEW_STATUSES:
        raise ValueError(f"status는 {REVIEW_STATUSES} 중 하나")
    reason = (reason or "").strip()[:300]
    if status == "rejected" and not reason:
        raise ValueError("기각 이유를 적어 주세요 — 다음 진단이 같은 후보를 이어받을 때 보입니다.")
    async with get_conn() as conn:
        async with conn.transaction():
            cur = await conn.fetchrow("SELECT status FROM eval_candidate WHERE id = $1 FOR UPDATE", cand_id)
            if not cur:
                return None
            if cur["status"] == "adopted":
                raise ValueError("이미 채택된 후보는 바꿀 수 없습니다.")
            # 되돌리기(proposed)는 검수 흔적을 비운다 — "누가 언제 정했나"가 남으면 결정 전 상태와 구별이 안 된다(리뷰 L5)
            undo = status == "proposed"
            await conn.execute(
                """UPDATE eval_candidate SET status = $2, review_reason = NULLIF($3, ''), reviewed_by_user_id = $4,
                          reviewed_at = CASE WHEN $5 THEN NULL ELSE NOW() END, carried_from_id = NULL WHERE id = $1""",
                cand_id, status, "" if undo else reason, None if undo else user_id, undo)
    return {"id": cand_id, "status": status}


async def adopt_candidate(cand_id: int, *, gate_run_id: int, user_id: int) -> Optional[dict]:
    """채택 = 시도 승인된 후보 + 통과한 판정 기록(승인 뒤에 잰 것, adopt_error). 게이트를 거치지 않은 채택은 없다(§10-5)."""
    async with get_conn() as conn:
        async with conn.transaction():
            c = await conn.fetchrow("SELECT status, reviewed_at FROM eval_candidate WHERE id = $1 FOR UPDATE", cand_id)
            if not c:
                return None
            g = await conn.fetchrow("SELECT verdict, kind, run_at FROM eval_gate_run WHERE id = $1", gate_run_id)
            err = adopt_error(c["status"], dict(g) if g else None, c["reviewed_at"])
            if err:
                raise ValueError(err)
            await conn.execute(
                """UPDATE eval_candidate SET status = 'adopted', adopted_gate_run_id = $2, reviewed_by_user_id = $3, reviewed_at = NOW()
                   WHERE id = $1""", cand_id, gate_run_id, user_id)
    return {"id": cand_id, "status": "adopted", "gate_run_id": gate_run_id}


from service.eval.improve import adopt_error, agreement  # noqa: E402 — 순수 규칙(테스트가 같은 함수를 검사)


async def decisions_report(days: int) -> dict:
    """품질 › AI 판단(읽기) — AI 판단 원장 종류별 집계(trustkit.decision.ledger.summarize) + 채팅 되묻기 고른 분포 + 개선 후보 검수.
    방향(directions)은 뺀다 — 위치 판정 값엔 AI 이유 문장이 같이 들어 있어 묶이지 않는다(표 한 줄이 판단 하나가 됨)."""
    from service.decision import ledger
    async with get_conn() as conn:
        rep = await ledger.report(conn, since_days=days)
        picks = await conn.fetch(
            """SELECT CASE WHEN final_value ? 'typed' THEN 'typed' WHEN final_value->>'picked' = '*' THEN 'all'
                           WHEN final_value ? 'picked' THEN 'option' WHEN final_value ? 'superseded' THEN 'superseded' ELSE 'other' END AS how,
                      COUNT(*) AS n
               FROM ops_ai_decision WHERE kind = 'chat_clarify' AND final_value IS NOT NULL AND created_at > NOW() - make_interval(days => $1)
               GROUP BY 1""", days)
        cands = await conn.fetch(
            """SELECT recommend, status, carried_from_id IS NOT NULL AS carried,
                      c.run_id = (SELECT id FROM eval_improvement_run ORDER BY created_at DESC, id DESC LIMIT 1) AS latest, COUNT(*) AS n
               FROM eval_candidate c JOIN eval_improvement_run i ON i.id = c.run_id
               WHERE i.created_at > NOW() - make_interval(days => $1) GROUP BY 1, 2, 3, 4""", days)
    kinds = {k: {kk: vv for kk, vv in v.items() if kk != "directions"} for k, v in rep.items()}
    # 사람이 직접 정한 후보만 센다 — 이어받은 줄은 같은 결정의 복사(진단 N번이면 N번 세던 것, 리뷰 M2).
    # 아직 제안(결정 전)은 최근 진단 것만 — 진단마다 새로 생겨 N번 세던 것(재리뷰)
    own = [{**dict(r), "carried_from_id": 1 if r["carried"] else None} for r in cands]
    by_status: dict[str, int] = {}
    for r in own:
        if r["carried_from_id"] is None and (r["status"] != "proposed" or r["latest"]):
            by_status[r["status"]] = by_status.get(r["status"], 0) + r["n"]
    return {"days": days, "kinds": kinds, "clarify_picks": {r["how"]: r["n"] for r in picks},
            "candidates": {"total": sum(by_status.values()), "by_status": by_status, **agreement(own)}}
