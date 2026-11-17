"""실사용 문항 — 정정 요청이 승인된 채팅 질문을 시험지 후보로 (2026-10-26, 사용자 "실사용 질문을 근거로 한 자가 개선 루프").

실사용 질문엔 정답이 없어 그대로는 채점할 수 없다. 사람이 신고 → 담당자가 정정 승인한 질문은 "고친 정책 = 정답"이 생긴다 →
승인되는 순간 시험지 후보 한 줄(`eval_question_candidate`), 관리자가 [시험지에 넣기]를 누르면 골든 측정이 "실사용(real)" 층으로 함께 잰다.
실사용 층은 잠근 시험지(LOCKED_TYPES) — 진단·묶음·후보를 만들 때 보지 않고 판정 때만 본다(같은 문항으로 고치고 채점하지 않게).
질문 글은 이미 질의 기록(ops_query_log)에 있는 사용자 질문 그대로 — 새로 원문을 만드는 건 없다.
정답이 정책인 것만 정책 골든에 들어간다(지식 정정은 후보로 쌓되 지식 측정 연결은 다음 — 백로그 0-7).
"""
from __future__ import annotations

import logging
from typing import Optional

from core.database import get_conn

logger = logging.getLogger(__name__)

POLICY_TARGETS = ("policy_param", "policy_narrative")
STATUSES = ("proposed", "added", "dismissed")


async def from_correction(item: dict, applied_target_id: Optional[int]) -> Optional[int]:
    """정정 승인 직후(best-effort — 실패해도 승인은 그대로, 로그만). 채팅 신고만 — 채팅 메시지에서 온 신고만 message_id를 가진다
    (정책서 화면 제안·JSON 가져오기·적재 점검은 없음). source로 거르면 안 된다 — 채팅 신고도 source가 채워진다(chat_answer_auto 등,
    NOT NULL — 10/26 리뷰 H1: source None 검사로 0건). 같은 정정은 한 번만(improvement_item_id 고유)."""
    if not item.get("message_id"):
        return None
    try:
        async with get_conn() as conn:
            q = await conn.fetchrow("SELECT question, namespace_id FROM ops_query_log WHERE message_id = $1 ORDER BY id DESC LIMIT 1",
                                    item["message_id"])
            if not q or not (q["question"] or "").strip():
                return None
            gold: dict = {"target_type": item["target_type"]}
            if item["target_type"] in POLICY_TARGETS and applied_target_id:
                p = await conn.fetchrow("SELECT logical_id, source_key FROM policy_item WHERE id = $1", applied_target_id)
                if p:
                    gold.update(kind="policy", logical_id=p["logical_id"], source_key=p["source_key"])
            elif applied_target_id:
                gold.update(kind="knowledge", id=applied_target_id)
            import json
            return await conn.fetchval(
                # 정정 승인 자체가 사람 검수 — 바로 시험지에(added). 이상한 질문만 리포트에서 뺀다(사용자 10/26 "실사용 문항을 따로 관리 안 함")
                """INSERT INTO eval_question_candidate (improvement_item_id, namespace_id, question, gold, status, decided_at)
                   VALUES ($1, $2, $3, $4::jsonb, 'added', NOW()) ON CONFLICT (improvement_item_id) DO NOTHING RETURNING id""",
                item["id"], q["namespace_id"] or item.get("namespace_id"), q["question"].strip(), json.dumps(gold, ensure_ascii=False))
    except Exception:
        logger.warning("정정 승인 → 시험지 후보 만들기 실패(item=%s) — 승인은 반영됨", item.get("id"), exc_info=True)
        return None


async def list_candidates(status: Optional[str]) -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """SELECT c.id, c.created_at, c.status, c.question, c.gold, c.improvement_item_id, c.decided_at, n.name AS namespace,
                      u.username AS decided_by, i.user_input AS report
               FROM eval_question_candidate c
               LEFT JOIN ops_namespace n ON n.id = c.namespace_id
               LEFT JOIN ops_user u ON u.id = c.decided_by_user_id
               LEFT JOIN ops_improvement_item i ON i.id = c.improvement_item_id
               WHERE ($1::text IS NULL OR c.status = $1) ORDER BY c.created_at DESC LIMIT 200""", status)
    import json
    out = []
    for r in rows:
        d = dict(r)
        d["gold"] = json.loads(d["gold"]) if isinstance(d["gold"], str) else d["gold"]
        d["qid"] = f"REAL-{d['id']}"
        d["measurable"] = (d["gold"] or {}).get("kind") == "policy" and bool((d["gold"] or {}).get("source_key"))
        out.append(d)
    return out


async def decide(cand_id: int, *, status: str, user_id: int) -> Optional[dict]:
    """[시험지에 넣기] = added · [빼기] = dismissed · [되돌리기] = proposed."""
    if status not in STATUSES:
        raise ValueError(f"status는 {STATUSES} 중 하나")
    async with get_conn() as conn:
        r = await conn.execute(
            """UPDATE eval_question_candidate SET status = $2, decided_by_user_id = $3,
                      decided_at = CASE WHEN $2 = 'proposed' THEN NULL ELSE NOW() END WHERE id = $1""",
            cand_id, status, None if status == "proposed" else user_id)
    return {"id": cand_id, "status": status} if r.endswith(" 1") else None


async def real_golden(conn) -> list[dict]:
    """시험지에 넣은 실사용 문항 → 골든 파일 줄과 같은 모양(eval_golden_v0·eval_answers가 파일 줄에 더해 쓴다). 정답이 정책인 것만.
    같은 질문은 하나만(먼저 넣은 것) — 두 사람이 같은 질문을 신고해 둘 다 넣어도 측정이 멈추지 않게(10/26 리뷰 M1). 골든 파일과 겹치는
    질문은 부르는 쪽(로더)이 건너뛰고 개수를 보인다."""
    import json
    rows = await conn.fetch(
        """SELECT c.id, c.question, c.gold, n.name AS ns FROM eval_question_candidate c
           LEFT JOIN ops_namespace n ON n.id = c.namespace_id WHERE c.status = 'added' ORDER BY c.id""")
    out, seen = [], set()
    for r in rows:
        g = json.loads(r["gold"]) if isinstance(r["gold"], str) else r["gold"]
        if (g or {}).get("kind") != "policy" or not g.get("source_key") or not r["ns"] or r["question"] in seen:
            continue
        seen.add(r["question"])
        out.append({"qid": f"REAL-{r['id']}", "verdict": "approved", "layer": "real", "expect": "answer", "query": r["question"],
                    "doc": r["ns"], "gold": [{"kind": "policy", "source_key": g["source_key"]}], "answer_facts": []})
    return out


# ── 골든셋 후보(11/16, 골든셋 자동화 2단계 — 제안서 TVjEPGCmiJ2EToJ1pfFaZH) ─────────────────────────────────────────────
# 채팅 신호 두 가지를 그때그때 모아 보인다(따로 쌓지 않음 — 원장·질의 기록이 원천): ① 답변 오류 판정(정정 원장 target_type='answer' — 근거는
# 맞는데 답이 잘못 읽음) ② 지식 없음 거절(질의 기록 status='no_knowledge'). 사람이 정답을 골라야만 골든셋(eval_question_candidate,
# status='added')에 들어간다 — 업계 공통(LangSmith 검토 대기열 → 데이터셋). 자동 추가 없음. 고를 수 있는 정답:
# 지식 조각(#id → 정답 닻으로 저장, 다시 적재해도 찾음) · 정책(그 정책 원천 키) · 모른다가 정답(범위 밖 — 단정 오답을 재는 문항) · 제외.
SOURCE_ANSWER_ERROR = "answer_error"
SOURCE_NO_KNOWLEDGE = "no_knowledge"
CANDIDATE_SOURCES = (SOURCE_ANSWER_ERROR, SOURCE_NO_KNOWLEDGE)
GOLD_ACTIONS = ("knowledge", "policy", "abstain", "dismiss")
_PENDING_LIMIT = 200
_POLICY_KINDS = ("policy_param", "policy_narrative")

# 질문 앞뒤 공백 지우기 — 저장은 늘 파이썬 str.strip() 기준(정정 승인·미리보기와 같음). SQL에서 같은 질문을 찾을 때도 줄바꿈·탭·전각 공백까지
# 지운다(btrim은 스페이스만 지워 끝에 줄바꿈이 붙은 질문이 정한 뒤에도 목록에 남았다 — 리뷰 11/16). 남는 차이(드문 유니코드 공백)는 파이썬에서 한 번 더 거른다
_WS = "[[:space:]\u00a0\u3000]"   # 줄바꿈·탭 + 줄바꿈 없는 공백·전각 공백 — 파이썬 strip이 지우는 글자 안에서만(BOM은 strip이 안 지워 빼야 같다, 재리뷰 11/16)
_TRIM = "regexp_replace({}, '^" + _WS + "+|" + _WS + "+$', '', 'g')"


def _unique_evidence(evid: list[dict]) -> list[dict]:
    """근거 버튼 목록 — 같은 지식·같은 정책 항목은 한 번(정책 파라미터 여러 개는 같은 정책 항목 id라 버튼·화면 key가 겹쳤다, 리뷰 11/16)."""
    out, seen = [], set()
    for c in evid:
        k = (c["target_type"] in _POLICY_KINDS, c["target_id"])
        if k not in seen:
            seen.add(k)
            out.append({"target_type": c["target_type"], "target_id": c["target_id"], "label": c.get("label")})
    return out

_ANSWER_ERROR_SQL = f"""
SELECT i.id AS ref_id, i.created_at, n.name AS namespace, i.namespace_id, i.message_id, i.ai_verdict, i.candidates, i.user_input, q.question
FROM ops_improvement_item i JOIN ops_namespace n ON n.id = i.namespace_id
LEFT JOIN LATERAL (SELECT {_TRIM.format("ql.question")} AS question FROM ops_query_log ql WHERE ql.message_id = i.message_id
                   ORDER BY ql.id DESC LIMIT 1) q ON TRUE
WHERE i.target_type = 'answer' AND i.message_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM eval_question_candidate c WHERE c.improvement_item_id = i.id
                  OR (c.namespace_id = i.namespace_id AND c.question = q.question))
ORDER BY i.created_at DESC LIMIT $1"""

# 같은 컬렉션·같은 질문은 한 줄(가장 최근 거절) — 같은 질문을 여러 번 물어도 후보는 하나. 이미 골든셋·제외에 같은 질문이 있으면(출처 무관) 안 보인다
_NO_KNOWLEDGE_SQL = f"""
SELECT * FROM (
  SELECT DISTINCT ON (l.namespace_id, t.q) l.id AS ref_id, l.created_at, n.name AS namespace, l.namespace_id, t.q AS question
  FROM ops_query_log l JOIN ops_namespace n ON n.id = l.namespace_id
  CROSS JOIN LATERAL (SELECT {_TRIM.format("coalesce(l.question, '')")} AS q) t
  WHERE l.status = 'no_knowledge' AND t.q <> ''
    AND NOT EXISTS (SELECT 1 FROM eval_question_candidate c WHERE c.query_log_id = l.id
                    OR (c.namespace_id = l.namespace_id AND c.question = t.q))
  ORDER BY l.namespace_id, t.q, l.id DESC) x
ORDER BY created_at DESC LIMIT $1"""


def _j(v):
    import json
    return json.loads(v) if isinstance(v, str) else v


async def pending_golden_candidates() -> dict:
    """아직 정답을 고르지 않은 후보 — {"items": [...], "counts": {채널: n}}. 질문은 사용자가 쓴 글 그대로(관리자만 보는 화면)."""
    async with get_conn() as conn:
        ae = await conn.fetch(_ANSWER_ERROR_SQL, _PENDING_LIMIT)
        nk = await conn.fetch(_NO_KNOWLEDGE_SQL, _PENDING_LIMIT)
        # 같은 컬렉션·같은 질문이 이미 골든셋·제외에 있으면 안 보인다 — 결정 API가 그 질문엔 새 줄을 안 만들어(한 줄 규칙) 목록에 영영 남던 것
        taken = {(r["namespace_id"], r["question"]) for r in await conn.fetch("SELECT namespace_id, question FROM eval_question_candidate")}
    items = []
    seen: set = set()
    for r in ae:
        q = (r["question"] or "").strip()
        if not q or (r["namespace_id"], q) in taken or (r["namespace_id"], q) in seen:
            continue   # 질문 글 없음(질의 기록 보존 기한) · 이미 정함 · 같은 질문의 다른 신고(한 줄만)
        seen.add((r["namespace_id"], q))
        v = _j(r["ai_verdict"]) or {}
        evid = [c for c in (_j(r["candidates"]) or []) if c.get("target_type") in ("knowledge",) + _POLICY_KINDS]
        items.append({"source": SOURCE_ANSWER_ERROR, "ref_id": r["ref_id"], "created_at": r["created_at"], "namespace": r["namespace"],
                      "question": q, "ai_reason": v.get("reason"), "report": r["user_input"], "evidence": _unique_evidence(evid)})
    for r in nk:
        q = r["question"].strip()
        if not q or (r["namespace_id"], q) in taken or (r["namespace_id"], q) in seen:
            continue   # 이미 정함(저장 기준 strip으로 다시 봄) · 같은 질문이 답변 오류 후보로도 있음 — 한 줄만(근거가 있는 쪽)
        seen.add((r["namespace_id"], q))
        items.append({"source": SOURCE_NO_KNOWLEDGE, "ref_id": r["ref_id"], "created_at": r["created_at"], "namespace": r["namespace"],
                      "question": q, "ai_reason": None, "report": None, "evidence": []})
    items.sort(key=lambda x: x["created_at"], reverse=True)
    counts = {s: sum(1 for x in items if x["source"] == s) for s in CANDIDATE_SOURCES}
    return {"items": items, "counts": counts}


async def _knowledge_gold(conn, knowledge_id: int) -> dict:
    """지식 조각 → 정답 닻(trustkit.eval.anchors — 측정 로더·생성기와 같은 규칙). 비교 대상 = 같은 페이지 조각(페이지 없으면 전체 사용 중)."""
    from trustkit.eval.anchors import anchors_for
    g = await conn.fetchrow("SELECT id, content, confluence_page_id, content_norm_md5 FROM rag_knowledge WHERE id = $1 AND status = 'active'",
                            knowledge_id)
    if not g:
        raise ValueError("정답으로 고른 지식(사용 중)을 찾지 못했어요")
    cols = "id, content, confluence_page_id, content_norm_md5"
    live = (await conn.fetch(f"SELECT {cols} FROM rag_knowledge WHERE status = 'active' AND confluence_page_id = $1", g["confluence_page_id"])
            if g["confluence_page_id"] else await conn.fetch(f"SELECT {cols} FROM rag_knowledge WHERE status = 'active'"))
    a = anchors_for([g["id"]], [dict(r) for r in live]).get(g["id"])
    if not a or not a["lines"]:
        raise ValueError("이 지식엔 정답으로 쓸 본문 줄이 없어요(제목·짧은 줄뿐) — 다른 조각을 고르세요")
    return {"target_type": "knowledge", "kind": "knowledge", "id": g["id"], "anchors": [a]}


async def _policy_gold(conn, policy_item_id: int) -> dict:
    p = await conn.fetchrow("SELECT logical_id, source_key FROM policy_item WHERE id = $1 AND source_key IS NOT NULL", policy_item_id)
    if not p:
        raise ValueError("정답으로 고른 정책을 찾지 못했어요")
    return {"target_type": "policy", "kind": "policy", "logical_id": p["logical_id"], "source_key": p["source_key"]}


async def decide_golden_candidate(*, source: str, ref_id: int, action: str, target_id: Optional[int], user_id: int) -> dict:
    """후보 하나에 정답을 정해 골든셋에 넣거나(added) 뺀다(dismissed — 다시 안 보임). 같은 후보·같은 질문은 한 줄(잠금 + 고유 키).
    반환 {"id", "qid", "status", "created"} — 이미 누가 정했으면 created=False(화면은 목록을 다시 읽는다)."""
    import json
    if source not in CANDIDATE_SOURCES:
        raise ValueError(f"source는 {CANDIDATE_SOURCES} 중 하나")
    if action not in GOLD_ACTIONS:
        raise ValueError(f"action은 {GOLD_ACTIONS} 중 하나")
    if action in ("knowledge", "policy") and not target_id:
        raise ValueError("정답 지식·정책 번호를 주세요")
    async with get_conn() as conn:
        if source == SOURCE_ANSWER_ERROR:
            row = await conn.fetchrow(
                "SELECT i.id, i.namespace_id, i.candidates, (SELECT ql.question FROM ops_query_log ql WHERE ql.message_id = i.message_id "
                "ORDER BY ql.id DESC LIMIT 1) AS question FROM ops_improvement_item i WHERE i.id = $1 AND i.target_type = 'answer'", ref_id)
        else:
            row = await conn.fetchrow("SELECT id, namespace_id, NULL::jsonb AS candidates, question FROM ops_query_log "
                                      "WHERE id = $1 AND status = 'no_knowledge'", ref_id)
        if not row or not (row["question"] or "").strip():
            raise LookupError("후보를 찾지 못했어요(질의 기록이 지워졌거나 다른 신고)")
        q, ns_id = row["question"].strip(), row["namespace_id"]
        if source == SOURCE_ANSWER_ERROR and action in ("knowledge", "policy"):
            # 답변 오류는 "근거는 맞았다"는 판정 — 정답은 그 답변의 근거 중에서만(엉뚱한 번호를 넣지 않게)
            kinds = ("knowledge",) if action == "knowledge" else _POLICY_KINDS
            if not any(c.get("target_type") in kinds and c.get("target_id") == target_id for c in (_j(row["candidates"]) or [])):
                raise ValueError("이 답변의 근거가 아닌 번호예요 — 답변 오류는 근거 중에서 정답을 고르세요")
        if action == "knowledge":
            gold = await _knowledge_gold(conn, target_id)
        elif action == "policy":
            gold = await _policy_gold(conn, target_id)
        else:
            gold = {"kind": "abstain"} if action == "abstain" else {}
        status = "dismissed" if action == "dismiss" else "added"
        item_id = row["id"] if source == SOURCE_ANSWER_ERROR else None
        log_id = row["id"] if source == SOURCE_NO_KNOWLEDGE else None
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"eval_q:{ns_id}:{q}")   # preview.add_question과 같은 잠금
            dup = await conn.fetchrow(
                "SELECT id, status FROM eval_question_candidate WHERE (namespace_id = $1 AND question = $2) "
                "OR ($3::bigint IS NOT NULL AND improvement_item_id = $3) OR ($4::int IS NOT NULL AND query_log_id = $4) ORDER BY id LIMIT 1",
                ns_id, q, item_id, log_id)
            if dup:
                return {"id": dup["id"], "qid": f"REAL-{dup['id']}", "status": dup["status"], "created": False}
            new_id = await conn.fetchval(
                """INSERT INTO eval_question_candidate (improvement_item_id, query_log_id, namespace_id, question, gold, status, decided_at,
                                                        decided_by_user_id, source)
                   VALUES ($1, $2, $3, $4, $5::jsonb, $6, NOW(), $7, $8) RETURNING id""",
                item_id, log_id, ns_id, q, json.dumps(gold, ensure_ascii=False), status, user_id, source)
    return {"id": new_id, "qid": f"REAL-{new_id}", "status": status, "created": True}


async def real_knowledge_golden(conn) -> list[dict]:
    """골든셋에 든 실사용 지식 문항 → 지식 측정 문항 모양(knowledge_eval_set이 파일 문항에 더한다). 정답 = 닻 · 모른다가 정답 = expect abstain.
    같은 질문은 하나만(먼저 넣은 것 — real_golden과 같은 규칙)."""
    rows = await conn.fetch(
        """SELECT c.id, c.question, c.gold, n.name AS ns FROM eval_question_candidate c
           JOIN ops_namespace n ON n.id = c.namespace_id WHERE c.status = 'added' ORDER BY c.id""")
    out, seen = [], set()
    for r in rows:
        g = _j(r["gold"]) or {}
        if r["question"] in seen or g.get("kind") not in ("knowledge", "abstain"):
            continue
        if g["kind"] == "knowledge" and not g.get("anchors"):
            continue   # 옛 정정 승인 문항(조각 id만, 닻 없음) — 다시 적재하면 못 찾는다
        seen.add(r["question"])
        # ask_fixed — 사용자가 실제로 물은 컬렉션 그대로 잰다(정답 조각이 있는 컬렉션으로 옮기면 그 실패가 측정에서 사라진다, 리뷰 11/16)
        base = {"qid": f"REAL-{r['id']}", "ns": r["ns"], "question": r["question"], "layer": "real", "cluster": f"real{r['id']}", "ask_fixed": True}
        out.append({**base, "expect": "abstain"} if g["kind"] == "abstain" else {**base, "gold_anchors": g["anchors"]})
    return out
