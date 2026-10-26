"""평가 게이트 판정 기록 읽기 (2026-10-23, #86 eval_gate_run — eval-gate-redesign §4-3). 쓰기는 scripts/eval_gate.py --save뿐,
화면·API는 저장된 결과를 읽기만 한다(통계 재계산 없음). 기준선 지정만 관리자가 바꾼다."""
import asyncio
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
                      answers->'groups'->'전체' AS answers_total,
                      answers->'groups' AS answers_groups
               FROM eval_gate_run ORDER BY run_at DESC, id DESC LIMIT $1""", limit)
    out = []
    for r in rows:
        d = _row(r)
        for k in ("answers_failed", "answers_total", "answers_groups"):
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

_CAND_JSON = ("qids", "replay", "hypotheses", "measurements")
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
            """SELECT c.*, g.label AS adopted_gate_label,
                      COALESCE((SELECT jsonb_agg(jsonb_build_object('id', m.id, 'label', m.label, 'verdict', m.verdict, 'run_at', m.run_at,
                                                             'diff', m.answers->'groups'->'전체'->'diff') ORDER BY m.id)
                                FROM eval_gate_run m WHERE m.id = ANY(c.measured_gate_run_ids)), '[]'::jsonb) AS measurements
               FROM eval_candidate c LEFT JOIN eval_gate_run g ON g.id = c.adopted_gate_run_id
               WHERE c.run_id = $1 ORDER BY c.id""", run["id"])
        accuracy = await conn.fetchval("SELECT (summary->'answers'->>'accuracy')::float FROM eval_gate_run WHERE id = $1",
                                       run["gate_run_id"])
    d = dict(run)
    for k in ("diagnosis", "hypotheses"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    from service.eval.techniques import BY_KEY
    from trustkit.eval.diagnose import CAUSES
    # 원인 이름·볼 곳·기법 이름은 서버가 붙인다 — 화면이 표를 따로 들고 있으면 대장과 어긋난다
    d["accuracy"] = accuracy
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
            g = await conn.fetchrow("SELECT verdict, kind, run_at, answers FROM eval_gate_run WHERE id = $1", gate_run_id)
            gd = {**dict(g), "answers": _j(g["answers"])} if g else None
            err = adopt_error(c["status"], gd, c["reviewed_at"])
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
            "candidates": {"total": sum(by_status.values()), "by_status": by_status, **agreement(own)},
            "changed": await changed_decisions(days)}


async def changed_decisions(days: int, limit: int = 50) -> list[dict]:
    """사람이 AI 값을 바꾼 판단(최신순) — AI가 고른 곳·AI 이유 → 사람이 고른 곳 · "AI가 틀렸나요?" 답·사유(사용자 10/26 "이유도 보여야 관리").
    컬렉션은 이름으로, 대상 문서는 출처 이름(파일·제목 경로)만 — 본문은 안 보낸다. AI 이유는 위치 판정이 남긴 한 문장(300자 이하, 원장 규칙)."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            """SELECT d.id, d.kind, d.created_at, d.changed_at, d.ai_value, d.final_value, d.signals->>'reason' AS ai_reason,
                      d.ai_verdict, d.verdict_reason, u.username AS changed_by,
                      na.name AS ai_ns, nf.name AS final_ns,
                      (SELECT COALESCE(k.source_file, array_to_string(k.heading_path, ' > ')) FROM rag_knowledge k
                        WHERE k.id = ANY(d.target_ids) ORDER BY k.id LIMIT 1) AS target_name
               FROM ops_ai_decision d
               LEFT JOIN ops_user u ON u.id = d.changed_by_user_id
               LEFT JOIN ops_namespace na ON na.id::text = d.ai_value->>'namespace_id'
               LEFT JOIN ops_namespace nf ON nf.id::text = d.final_value->>'namespace_id'
               WHERE d.human_changed AND d.created_at > NOW() - make_interval(days => $1)
               ORDER BY COALESCE(d.changed_at, d.created_at) DESC LIMIT $2""", days, limit)
    out = []
    for r in rows:
        ai, fin = _j(r["ai_value"]) or {}, _j(r["final_value"]) or {}
        out.append({"id": r["id"], "kind": r["kind"], "at": r["changed_at"] or r["created_at"], "changed_by": r["changed_by"],
                    "target": r["target_name"], "ai": {"collection": r["ai_ns"], "doc_type": ai.get("doc_type"), "reason": r["ai_reason"]},
                    "human": {"collection": r["final_ns"], "choice": fin.get("choice")},
                    "verdict": r["ai_verdict"], "verdict_reason": r["verdict_reason"]})
    return out


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


async def question_detail(run_id: int, qid: str) -> Optional[dict]:
    """문항 하나의 흐름 — 질문 → 정답 → 검색 → 답(회차별) → 왜(채점 이유·진단 칸·AI 묶음) → 후보 → 고치는 법·측정 결과.
    질문·답·채점 이유는 결과 파일에서 읽어 돌려주기만 한다(DB에 저장 안 함, service/eval/files.py). 관리자만(라우터).
    판정 기록이 없으면 None, 결과 파일·문항이 없으면 ResultFileError."""
    from service.eval import files
    from service.eval.techniques import BY_KEY
    from trustkit.eval.diagnose import CAUSES

    run = await get_run(run_id)
    if not run:
        return None
    inputs = (run.get("summary") or {}).get("inputs") or {}

    def _read(name):   # 파일 찾기·읽기(~2MB 파싱)는 이벤트 루프 밖에서(10/26 리뷰 L2)
        p = files.find_result(name)
        return files.rows_by_qid(p) if p else None
    after_rows, retr_rows, before_rows = [await asyncio.to_thread(_read, inputs.get(k))
                                         for k in ("answers_after", "retrieval_after", "answers_before")]
    after = (after_rows or {}).get(qid)
    retr = ((retr_rows or {}).get(qid) or [None])[0]
    if not after and not retr:
        raise files.ResultFileError("이 판정의 결과 파일이 서버에 없거나 이 문항이 없습니다(eval_out/)")
    before = (before_rows or {}).get(qid)

    def ans(r: dict) -> dict:
        j = r.get("judge") or {}
        return {"rep": r.get("rep", 0), "answer": r.get("answer") or "", "label": j.get("label"), "reason": j.get("reason") or "",
                "abstained": bool(r.get("abstained")), "error": r.get("error"), "clarified": bool(r.get("clarified"))}

    first = (after or [{}])[0]   # 검색만 잰 판정이면 답 줄이 없다 — 검색 행으로만 보여 준다(10/26 리뷰 L1)
    out = {
        "qid": qid, "type": first.get("type") or (retr or {}).get("type"), "namespace": first.get("namespace") or (retr or {}).get("ns"),
        "question": first.get("question") or "", "expected": first.get("expected") or "", "gold_policies": first.get("ref_names") or [],
        "retrieval": {"gold_in_context": bool(first.get("gold_in_context") if after else (retr or {}).get("hit")),
                      "mapped_term": first.get("mapped_term") or (retr or {}).get("mapped"),
                      **({"hit": bool(retr.get("hit")), "top1": bool(retr.get("top1")), "coverage": retr.get("coverage"),
                          "clarify": bool(retr.get("clarify"))} if retr else {})},
        "answers": [ans(r) for r in after or []],
        "before": [ans(r) for r in before] if before else None,
        "moved": ("up" if qid in ((run.get("answers") or {}).get("up_qids") or []) else
                  "down" if qid in ((run.get("answers") or {}).get("down_qids") or []) else
                  ("same" if run.get("answers") else None)),
        "cause": None, "hypotheses": [], "candidates": [],
    }
    # 왜·후보 — 이 판정을 진단한 가장 최근 기록에서
    async with get_conn() as conn:
        imp = await conn.fetchrow("SELECT id, diagnosis, hypotheses FROM eval_improvement_run WHERE gate_run_id = $1 "
                                  "ORDER BY created_at DESC, id DESC LIMIT 1", run_id)
        cands = await conn.fetch(
            """SELECT c.id, c.key, c.title, c.cause, c.technique, c.action, c.measure, c.status, c.qids, c.measured_gate_run_ids
               FROM eval_candidate c WHERE c.run_id = $1 AND c.qids ? $2 ORDER BY c.id""", imp["id"], qid) if imp else []
        meas_ids = sorted({m for c in cands for m in (c["measured_gate_run_ids"] or [])})
        meas = {r["id"]: dict(r) for r in await conn.fetch(
            "SELECT id, label, verdict, answers FROM eval_gate_run WHERE id = ANY($1::bigint[])", meas_ids)} if meas_ids else {}
    if imp:
        diag = imp["diagnosis"] if isinstance(imp["diagnosis"], dict) else json.loads(imp["diagnosis"])
        names = {k: (n, w) for k, n, w in CAUSES}
        key = next((k for k, qs in (diag.get("causes") or {}).items() if qid in qs), None)
        if key:
            out["cause"] = {"key": key, "name": names.get(key, (key, ""))[0], "where": names.get(key, ("", ""))[1]}
        hyps = imp["hypotheses"] if isinstance(imp["hypotheses"], list) else json.loads(imp["hypotheses"] or "[]")
        out["hypotheses"] = [{"name": h["name"], "hint": h.get("hint", ""), "n": len(h.get("qids") or [])}
                             for h in hyps if qid in (h.get("qids") or [])]
    for c in cands:
        ms = []
        for mid in c["measured_gate_run_ids"] or []:
            m = meas.get(mid)
            if not m:
                continue
            a = m["answers"] if isinstance(m["answers"], dict) else json.loads(m["answers"] or "null") or {}
            ms.append({"id": mid, "label": m["label"], "verdict": m["verdict"],
                       "this_question": "up" if qid in (a.get("up_qids") or []) else "down" if qid in (a.get("down_qids") or []) else "same"})
        out["candidates"].append({"id": c["id"], "title": c["title"], "status": c["status"], "action": c["action"], "measure": c["measure"],
                                  "technique_name": BY_KEY.get(c["technique"], {}).get("name", c["technique"]), "measurements": ms})
    return out


async def mark_hypothesis(improvement_id: int, *, cause: str, name: str, status: str, user_id: int) -> Optional[dict]:
    """AI 묶음 확인(C1, 10/26) — confirmed = 맞아요(이름 고정, 다음 진단이 이 이름에 배정), rejected = 다시 묶기(다음 진단이 이 칸을 새로 묶음),
    None = 되돌리기. 진단 기록(eval_improvement_run.hypotheses)과 그 진단 후보 카드의 묶음을 같이 고친다. 없는 묶음이면 None."""
    if status not in ("confirmed", "rejected", "open"):
        raise ValueError("status는 confirmed / rejected / open")
    async with get_conn() as conn:
        async with conn.transaction():
            run = await conn.fetchrow("SELECT hypotheses FROM eval_improvement_run WHERE id = $1 FOR UPDATE", improvement_id)
            if not run:
                return None
            hyps = _j(run["hypotheses"]) or []
            hit = [h for h in hyps if h.get("cause") == cause and h.get("name") == name]
            if not hit:
                return None
            for h in hit:
                h["status"] = status
                h["reviewed_by"] = user_id
            await conn.execute("UPDATE eval_improvement_run SET hypotheses = $2::jsonb WHERE id = $1",
                               improvement_id, json.dumps(hyps, ensure_ascii=False))
            for c in await conn.fetch("SELECT id, hypotheses FROM eval_candidate WHERE run_id = $1 AND cause = $2", improvement_id, cause):
                ch = _j(c["hypotheses"]) or []
                if any(h.get("name") == name for h in ch):
                    for h in ch:
                        if h.get("name") == name:
                            h["status"] = status
                    await conn.execute("UPDATE eval_candidate SET hypotheses = $2::jsonb WHERE id = $1", c["id"], json.dumps(ch, ensure_ascii=False))
    return {"cause": cause, "name": name, "status": status}
