"""틀린 문항 리포트 (2026-10-26, 사용자 "틀린 문항을 보고 — 어떤 질문이었고 어떻게 답했는데 왜 틀렸고, 어떤 구조로 저장돼 있어서 틀렸는지,
맞게 하려면 어떤 기법을 적용해 볼 수 있는지 — 사람이 검수하고 적용", "실제로 효과가 있어야").

문항 중심 한 화면: 틀린 문항마다 왜(처음 실패한 단계·채점 이유) · 저장 구조(정답 데이터가 어떤 모양으로 저장돼 있나) · 적용해 볼 기법(후보)과
그 기법의 **실제 효과(잠근 시험지 = 사람·실사용 문항에서 좋아졌나)**. 질문·답·채점 이유는 결과 파일에서 읽기만(DB 저장 없음, 관리자만).
저장 구조는 데이터에서 잰다(LLM 0): 원문 길이·표/구간 서술·파라미터 수·조각 수·정답이 여러 정책에 흩어짐·참고표/개요에만 있음·
질문 단어가 문서에 적음·용어 매핑 없음. 판단이 아니라 사실 표시 — 어떤 모양이 정답률을 낮추는지는 모양별 정답률로 따로 본다.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Optional

from core.database import get_conn

GOLDEN_DIR = Path("/app/eval_out/golden_v0/reviewed")
_TABLE = re.compile(r"\|.*\||\t|~\s*\d|이상.*미만|구간")
_TOK = re.compile(r"[가-힣A-Za-z0-9]{2,}")


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def _golden() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in sorted(GOLDEN_DIR.glob("*.jsonl")) if GOLDEN_DIR.exists() else []:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                g = json.loads(line)
                if g.get("verdict") == "approved":
                    out[g["qid"]] = g
    return out


def storage_inputs(ans_rows: Optional[list], retr_row: Optional[dict]) -> tuple[Optional[str], Optional[bool]]:
    """저장 꼬리표에 넣을 (용어 매핑, 정답이 문맥에 들었나) — 리포트·문항 상세가 같은 값을 쓰게 한 곳에서(리뷰 L3).
    매핑은 검색 측정 행이 있으면 그것(검색 판정 기준), 없으면 답 측정 행. 문맥 포함은 답 측정 행에만 있다(없으면 모름 = None)."""
    first = (ans_rows or [{}])[0]
    mapped = retr_row.get("mapped") if retr_row else first.get("mapped_term")
    return mapped, (first.get("gold_in_context") if ans_rows else None)


def storage_tags(question: str, gold: list[dict], policies: dict[str, dict], refs: dict[str, dict], *, mapped: Optional[str],
                 gold_in_context: Optional[bool], unused_qid: bool) -> tuple[list[dict], list[str]]:
    """(정답 데이터 목록, 저장 상태 표시) — 순수 함수(테스트). policies/refs = source_key → 행 요약."""
    items, tags = [], []
    kinds = {x.get("kind") for x in gold}
    if not gold:
        tags.append("정답 데이터 없음(답 없는 질문)")
    qt = set(_TOK.findall(question or ""))
    for x in gold:
        k, key = x.get("kind"), x.get("source_key")
        if k == "policy" and key in policies:
            p = policies[key]
            items.append({"kind": "정책", "name": p["name"], "path": p["path"], "body_len": p["body_len"], "params": p["params"],
                          "chunks": p["chunks"], "table": p["table"], "remark": p["remark"]})
            if not p["params"]:
                tags.append("원문만 · 파라미터 0개")
            if p["table"]:
                tags.append("표·구간으로 적힘")
            if qt and len(qt & set(_TOK.findall(p["body"] or ""))) / len(qt) < 0.3:
                tags.append("질문 표현이 문서와 다름")
        elif k in ("ref_table", "overview") and key in refs:
            r = refs[key]
            items.append({"kind": "참고표" if k == "ref_table" else "개요", "name": r["name"], "rows": r.get("rows")})
            if unused_qid:
                tags.append(f"{'참고표' if k == 'ref_table' else '개요'}에만 있음 — 채팅에 안 쓰임")
        elif key:
            items.append({"kind": {"policy": "정책", "ref_table": "참고표", "overview": "개요"}.get(k, k), "name": None, "missing": True})
            tags.append("정답 데이터가 현행에 없음")
    if sum(1 for x in gold if x.get("kind") == "policy") > 1:
        tags.append("정답이 정책 여러 개에 흩어짐")
    if "policy" in kinds and gold_in_context is False:
        tags.append("정답 정책이 문맥에 못 들어감")
    if gold and mapped is None and "policy" in kinds:
        tags.append("용어 매핑 없음")
    return items, list(dict.fromkeys(tags))   # 순서 유지 · 중복 제거


async def _lookups(conn, golden: dict[str, dict], qids: list[str]) -> tuple[dict, dict]:
    keys = {x.get("source_key") for q in qids for x in (golden.get(q) or {}).get("gold", []) if x.get("source_key")}
    if not keys:
        return {}, {}
    pol = {r["source_key"]: {"name": r["policy_name"], "path": " > ".join(r["category_path"] or []), "body": r["raw_body"],
                             "body_len": len(r["raw_body"] or ""), "params": r["n_param"], "chunks": r["n_chunk"],
                             "table": bool(_TABLE.search(r["raw_body"] or "")), "remark": bool((r["remark"] or "").strip())}
           for r in await conn.fetch(
               """SELECT i.source_key, i.policy_name, i.category_path, i.raw_body, i.remark,
                         (SELECT count(*) FROM policy_param p WHERE p.policy_item_id = i.id) AS n_param,
                         (SELECT count(*) FROM policy_chunk c WHERE c.policy_item_id = i.id) AS n_chunk
                  FROM policy_item i WHERE i.status = 'active' AND i.source_key = ANY($1::text[])""", list(keys))}
    refs = {r["source_key"]: {"name": r["title"], "rows": r["n"]} for r in await conn.fetch(
        """SELECT t.source_key, t.title, (SELECT count(*) FROM ref_table_row x WHERE x.ref_table_id = t.id) AS n
           FROM ref_table t WHERE t.status = 'active' AND t.source_key = ANY($1::text[])""", list(keys))}
    refs.update({r["source_key"]: {"name": r["title"], "rows": None} for r in await conn.fetch(
        "SELECT source_key, title FROM policy_doc_overview WHERE status = 'active' AND source_key = ANY($1::text[])", list(keys))})
    return pol, refs


def _locked_effect(answers: Optional[dict], locked: Optional[tuple] = None) -> Optional[dict]:
    """측정 한 번의 잠근 시험지 효과 — 사람·실사용 층 전/후(문항 수 가중). 층이 없으면 None."""
    if locked is None:
        from service.eval.improve import LOCKED_TYPES as locked
    groups = (answers or {}).get("groups") or {}
    xs = [groups[t] for t in locked if groups.get(t) and groups[t].get("n")]
    if not xs:
        return None
    n = sum(x["n"] for x in xs)
    before = sum(x["before"] * x["n"] for x in xs) / n
    after = sum(x["after"] * x["n"] for x in xs) / n
    return {"n": n, "before": before, "after": after, "down": any(x["diff"] < 0 for x in xs)}


async def build_report() -> Optional[dict]:
    """지금 점수(기준 판정) + 그 판정을 진단한 가장 최근 기록 → 틀린 문항 목록·기법 요약. 진단이 없으면 None."""
    from service.eval import files
    from service.eval.service import _row
    from service.eval.techniques import BY_KEY
    from trustkit.eval.diagnose import CAUSES
    from trustkit.eval.gate import per_question

    async with get_conn() as conn:
        base = await conn.fetchrow("SELECT * FROM eval_gate_run WHERE is_baseline")
        if not base:
            return None
        base = _row(base)
        imp = await conn.fetchrow("SELECT id, created_at, diagnosis, gate_run_id FROM eval_improvement_run WHERE gate_run_id = $1 "
                                  "ORDER BY created_at DESC, id DESC LIMIT 1", base["id"])
        imp = await conn.fetchrow("SELECT id, created_at, diagnosis, gate_run_id FROM eval_improvement_run ORDER BY created_at DESC, id DESC LIMIT 1") \
            if not imp else imp
        if not imp:
            return None
        cands = [dict(r) for r in await conn.fetch(
            """SELECT c.id, c.key, c.cause, c.technique, c.title, c.action, c.measure, c.qids, c.status, c.recommend, c.why,
                      c.reviewed_at, c.measured_gate_run_ids, c.review_reason
               FROM eval_candidate c WHERE c.run_id = $1 ORDER BY c.id""", imp["id"])]
        meas_ids = sorted({m for c in cands for m in (c["measured_gate_run_ids"] or [])})
        meas = {r["id"]: {"label": r["label"], "verdict": r["verdict"], "answers": _j(r["answers"]), "retrieval": _j(r["retrieval"])}
                for r in await conn.fetch("SELECT id, label, verdict, answers, retrieval FROM eval_gate_run WHERE id = ANY($1::bigint[])",
                                          meas_ids)} if meas_ids else {}
        ing = await conn.fetchval("SELECT results FROM ingest_check_run ORDER BY created_at DESC, id DESC LIMIT 1")
        golden = await asyncio.to_thread(_golden)
        diag = _j(imp["diagnosis"]) or {}
        cause_of = {q: k for k, qs in (diag.get("causes") or {}).items() for q in qs}

        # 틀린 문항 = 진단 칸(잠근 문항 제외) + 잠근 문항 중 틀린 것(보기만 — 고칠 때 쓰지 않음)
        inputs = (base.get("summary") or {}).get("inputs") or {}

        missing: list[str] = []   # 판정 기록엔 있는데 서버에 없는 결과 파일 — 0건으로 조용히 보이지 않게 화면에 알린다(리뷰 L1)

        def _read(name):
            p = files.find_result(name)
            if name and not p:
                missing.append(name)
            return files.rows_by_qid(p) if p else {}
        ans_rows, retr_rows = await asyncio.to_thread(_read, inputs.get("answers_after")), await asyncio.to_thread(_read, inputs.get("retrieval_after"))
        flat = [{"qid": r["qid"], "label": (r.get("judge") or {}).get("label"), "type": r.get("type"), "rep": r.get("rep", 0)}
                for rs in ans_rows.values() for r in rs if (r.get("judge") or {}).get("label") not in (None, "판정불가")]
        acc, meta = per_question(flat)
        from service.eval.improve import LOCKED_TYPES
        failed = sorted(q for q, a in acc.items() if a < 0.5)
        pol, refs = await _lookups(conn, golden, failed)
        # 실사용 문항(REAL-<id>)의 지금 상태 — 시험지에서 뺀 문항은 다음 측정부터 빠진다(그 전까진 이 측정에 남음)
        real_ids = [int(q[5:]) for q in failed if q.startswith("REAL-") and q[5:].isdigit()]
        real_status = {f"REAL-{r['id']}": r["status"] for r in await conn.fetch(
            "SELECT id, status FROM eval_question_candidate WHERE id = ANY($1::int[])", real_ids)} if real_ids else {}

    unused = set()
    unmeasured = []   # 정답 데이터를 채팅이 못 써서 측정에서도 빠진 문항(쓰임 검사) — 틀린 문항 목록에 안 잡히니 따로 보인다
    for r in (_j(ing) or []):
        if r.get("key", "").startswith("usage_"):
            unused |= set(r.get("qids") or [])
            if r.get("bad"):
                unmeasured.append({"store": r["store"], "n": r["bad"], "total": r["total"], "ref": r["ref"], "qids": r.get("qids") or []})
    names = {k: n for k, n, _ in CAUSES}
    by_q: dict[str, list[dict]] = {}
    for c in cands:
        for q in _j(c["qids"]) or []:
            by_q.setdefault(q, []).append(c)

    def effect(c) -> Optional[dict]:
        ids = c["measured_gate_run_ids"] or []
        if not ids:
            return None
        m = meas.get(ids[-1])
        if not m:
            return None
        a = m["answers"] or {}
        tot = (a.get("groups") or {}).get("전체") or {}
        return {"gate_run_id": ids[-1], "verdict": m["verdict"], "label": m["label"], "total_diff": tot.get("diff"),
                "locked": _locked_effect(a), "retrieval_up": len((m["retrieval"] or {}).get("up") or []),
                "retrieval_down": len((m["retrieval"] or {}).get("down") or [])}

    rows = []
    for q in failed:
        rs = ans_rows.get(q) or [{}]
        first = rs[0]
        g = golden.get(q) or {}
        mapped, gic = storage_inputs(ans_rows.get(q), (retr_rows.get(q) or [None])[0])
        items, tags = storage_tags(first.get("question") or g.get("query") or "", g.get("gold") or [], pol, refs,
                                   mapped=mapped, gold_in_context=gic, unused_qid=q in unused)
        locked = meta[q][0] in LOCKED_TYPES
        rows.append({"qid": q, "type": meta[q][0], "locked": locked, "question": first.get("question") or "",
                     "labels": [((r.get("judge") or {}).get("label")) for r in rs],
                     "cause": None if locked else cause_of.get(q), "cause_name": None if locked else names.get(cause_of.get(q)),
                     "storage": tags, "gold": items, "real_status": real_status.get(q),
                     "techniques": [{"id": c["id"], "title": c["title"], "status": c["status"],
                                     "technique_name": BY_KEY.get(c["technique"], {}).get("name", c["technique"])} for c in by_q.get(q, [])]})
    techniques = [{"id": c["id"], "key": c["key"], "cause": c["cause"], "technique": c["technique"], "title": c["title"], "status": c["status"], "recommend": c["recommend"],
                   "technique_name": BY_KEY.get(c["technique"], {}).get("name", c["technique"]), "action": c["action"],
                   "measure": c["measure"], "why": c["why"], "review_reason": c["review_reason"], "reviewed_at": c["reviewed_at"],
                   "n_failed": sum(1 for q in (_j(c["qids"]) or []) if q in acc and acc[q] < 0.5), "effect": effect(c)}
                  for c in cands]
    s = (base.get("summary") or {}).get("answers") or {}
    bt = s.get("by_type") or {}
    lk = [bt[t] for t in LOCKED_TYPES if bt.get(t)]
    ln = sum(x["n"] for x in lk)
    return {"gate_run_id": base["id"], "gate_label": base["label"], "improvement_id": imp["id"], "diagnosed_at": imp["created_at"],
            "score": {"total": s.get("accuracy"), "locked": (sum(x["accuracy"] * x["n"] for x in lk) / ln) if ln else None, "locked_n": ln,
                      "retrieval": ((base.get("summary") or {}).get("retrieval") or {}).get("recall"), "n": len(acc)},
            "failed": rows, "techniques": techniques, "unmeasured": unmeasured,
            # 진단이 지금 점수가 아닌 다른 측정 기준일 수 있다(운영 반영 직후 — 새 측정은 아직 진단 전) → 화면이 "진단은 #N 기준"(리뷰 L2)
            "diagnosed_gate_run_id": imp["gate_run_id"], "files_missing": missing}


async def storage_for(qid: str, *, mapped: Optional[str], gold_in_context: Optional[bool], question: str) -> dict:
    """문항 상세의 ④ 저장 구조 — 리포트와 같은 규칙."""
    golden = await asyncio.to_thread(_golden)
    g = golden.get(qid) or {}
    async with get_conn() as conn:
        pol, refs = await _lookups(conn, golden, [qid])
        ing = await conn.fetchval("SELECT results FROM ingest_check_run ORDER BY created_at DESC, id DESC LIMIT 1")
    unused = any(qid in (r.get("qids") or []) for r in (_j(ing) or []) if r.get("key", "").startswith("usage_"))
    items, tags = storage_tags(question or g.get("query") or "", g.get("gold") or [], pol, refs, mapped=mapped,
                               gold_in_context=gold_in_context, unused_qid=unused)
    return {"gold": items, "tags": tags}


def layer_qids(answers: Optional[dict], retrieval: Optional[dict], mode: str = "lexical") -> dict:
    """[검색]·[답] 층 줄을 누르면 여는 문항 — 층(type)별 검색 놓침(정답 정책이 문맥에 안 들어감) · 답 틀림(회차 평균 정답 < 0.5).
    판정 기록의 숫자와 같은 규칙: 검색은 답 없는 질문·다른 용어 방식 줄을 빼고(eval_gate._retrieval_rows), 답은 판정불가를 뺀다."""
    from trustkit.eval.gate import per_question
    missed: dict = {}
    for q, rs in (retrieval or {}).items():
        rs_mode = [x for x in rs if x.get("set") != "negative" and x.get("mode", mode) == mode]
        if not rs_mode:
            continue
        r = rs_mode[-1]
        if not r.get("hit"):
            missed.setdefault(r.get("type") or "-", []).append(q)
    rows = [{"qid": q, "label": (x.get("judge") or {}).get("label"), "type": x.get("type")}
            for q, xs in (answers or {}).items() for x in xs
            if (x.get("judge") or {}).get("label") and (x.get("judge") or {}).get("label") != "판정불가"]
    acc, meta = per_question(rows)
    wrong: dict = {}
    for q, a in acc.items():
        if a < 0.5:
            wrong.setdefault(meta[q][0] or "-", []).append(q)
    return {"missed": {t: sorted(v) for t, v in missed.items()}, "wrong": {t: sorted(v) for t, v in wrong.items()}}

