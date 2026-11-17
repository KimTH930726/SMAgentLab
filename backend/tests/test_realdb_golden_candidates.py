"""골든셋 후보(service/eval/questions.py, 11/16) — 실 DB(트랜잭션 롤백). 사람이 정답을 골라야만 골든셋에 들어가는가, 같은 질문은 한 줄인가.

불변식:
- 후보 = 답변 오류 판정(target_type 'answer') + 지식 없음 거절(질의 기록 no_knowledge) 중 아직 안 정한 것 · 같은 컬렉션·같은 질문은 한 줄
- 정하면(정답·모른다·제외) 목록에서 사라지고 골든셋 줄은 정확히 1개 — 두 번 눌러도, 같은 질문의 다른 신호여도
- 정답 지식은 닻으로 저장되고, 측정 로더(real_knowledge_golden → 닻 풀기)가 지금 그 조각을 정답으로 다시 찾는다
- 답변 오류는 그 답변의 근거 중에서만 정답(엉뚱한 번호 400) · 모른다가 정답 = expect abstain 문항
"""
import asyncio
import json
import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from realdb import knowledge, load, ns, run  # noqa: E402

q = load("_rdb_real_eval_questions", "service/eval/questions.py")
NS = "시험_골든후보 시스템"


async def _msg(conn, n):
    conv = await conn.fetchval("INSERT INTO ops_conversation DEFAULT VALUES RETURNING id")
    return await conn.fetchval("INSERT INTO ops_message (conversation_id, role, content) VALUES ($1, 'assistant', '답') RETURNING id", conv)


async def _log(conn, n, question, status="no_knowledge", message_id=None):
    return await conn.fetchval("INSERT INTO ops_query_log (question, status, namespace_id, message_id) VALUES ($1, $2, $3, $4) RETURNING id",
                               question, status, n, message_id)


async def _answer_error(conn, n, question, evidence):
    mid = await _msg(conn, n)
    await _log(conn, n, question, status="pending", message_id=mid)
    cands = [{"key": f"k-{i}", "target_type": "knowledge", "target_id": i, "target_sub_id": None, "label": f"문서 #{i}"} for i in evidence]
    return await conn.fetchval(
        "INSERT INTO ops_improvement_item (kind, source, namespace_id, message_id, target_type, original, candidates, ai_verdict) "
        "VALUES ('answer_quality', 'chat_answer_auto', $1, $2, 'answer', '{}'::jsonb, $3::jsonb, $4::jsonb) RETURNING id",
        n, mid, json.dumps(cands), json.dumps({"reason": "근거는 맞는데 답이 잘못 읽음"}))


def _mine(items, n_name):
    return [x for x in items if x["namespace"] == n_name]


def test_pending_then_decide_once_and_anchor_resolves():
    async def scenario(conn):
        n = await ns(conn, NS)
        k1 = await knowledge(conn, n, "시험_정답 조각 고유 첫 줄입니다\n시험_정답 조각 고유 둘째 줄입니다")
        k2 = await knowledge(conn, n, "시험_다른 조각 본문 줄입니다")
        ae = await _answer_error(conn, n, "시험_답변오류 질문?", [k1, k2])
        l1 = await _log(conn, n, "시험_지식없음 질문?")
        await _log(conn, n, "  시험_지식없음 질문?  ")          # 같은 질문 다시 물음(공백만 다름) → 후보 한 줄
        l3 = await _log(conn, n, "시험_모른다 질문?")
        before = _mine((await q.pending_golden_candidates())["items"], NS)
        out = {"pending_sources": sorted((x["source"], x["question"]) for x in before)}
        # 답변 오류: 근거가 아닌 번호는 400
        try:
            await q.decide_golden_candidate(source="answer_error", ref_id=ae, action="knowledge", target_id=k2 + 999, user_id=None)
            out["bad_target"] = "accepted"
        except ValueError:
            out["bad_target"] = "rejected"
        r1 = await q.decide_golden_candidate(source="answer_error", ref_id=ae, action="knowledge", target_id=k1, user_id=None)
        r1b = await q.decide_golden_candidate(source="answer_error", ref_id=ae, action="knowledge", target_id=k1, user_id=None)   # 두 번 누름
        r2 = await q.decide_golden_candidate(source="no_knowledge", ref_id=l1, action="dismiss", target_id=None, user_id=None)
        r3 = await q.decide_golden_candidate(source="no_knowledge", ref_id=l3, action="abstain", target_id=None, user_id=None)
        after = _mine((await q.pending_golden_candidates())["items"], NS)
        rows = await conn.fetch("SELECT question, status, gold, source FROM eval_question_candidate WHERE namespace_id = $1 ORDER BY id", n)
        real = [x for x in await q.real_knowledge_golden(conn) if x["ns"] == NS]
        # 측정 로더가 닻을 풀어 지금 조각을 정답으로 찾나(스크립트 로더와 같은 규칙)
        from trustkit.eval.anchors import resolve_anchors
        chunks = [(r["id"], r["content"]) for r in await conn.fetch("SELECT id, content FROM rag_knowledge WHERE namespace_id = $1 AND status = 'active'", n)]
        kn = next(x for x in real if "gold_anchors" in x)
        a = kn["gold_anchors"][0]
        out.update(created=(r1["created"], r1b["created"], r2["created"], r3["created"]), after=len(after),
                   rows=[(r["question"], r["status"], r["source"], json.loads(r["gold"]).get("kind")) for r in rows],
                   real=sorted((x["question"], x.get("expect", "answer")) for x in real),
                   resolved=resolve_anchors(a["lines"], chunks, a["need"]), k1=k1, ask_fixed=all(x.get("ask_fixed") for x in real))
        return out

    out = run(scenario, modules=[q])
    assert out["pending_sources"] == [("answer_error", "시험_답변오류 질문?"), ("no_knowledge", "시험_모른다 질문?"),
                                      ("no_knowledge", "시험_지식없음 질문?")]
    assert out["bad_target"] == "rejected"
    assert out["created"] == (True, False, True, True)
    assert out["after"] == 0
    assert out["rows"] == [("시험_답변오류 질문?", "added", "answer_error", "knowledge"),
                           ("시험_지식없음 질문?", "dismissed", "no_knowledge", None),
                           ("시험_모른다 질문?", "added", "no_knowledge", "abstain")]
    assert out["real"] == [("시험_답변오류 질문?", "answer"), ("시험_모른다 질문?", "abstain")]   # 제외는 측정에 안 들어감
    assert out["ask_fixed"] is True   # 실사용 문항은 물은 컬렉션 그대로 잰다(리뷰 11/16 — 정답 컬렉션으로 옮기면 실패가 가려짐)
    assert out["resolved"] == [out["k1"]]


def test_same_question_from_other_signal_does_not_reappear():
    """답변 오류로 정한 질문이 지식 없음 거절로도 있으면(같은 컬렉션·같은 글) 그것도 안 보인다 — 한 줄 규칙."""
    async def scenario(conn):
        n = await ns(conn, NS)
        k1 = await knowledge(conn, n, "시험_정답 조각 고유 줄입니다")
        ae = await _answer_error(conn, n, "시험_같은 질문?", [k1])
        await _log(conn, n, "시험_같은 질문?")
        both = _mine((await q.pending_golden_candidates())["items"], NS)
        await q.decide_golden_candidate(source="answer_error", ref_id=ae, action="knowledge", target_id=k1, user_id=None)
        return len(both), len(_mine((await q.pending_golden_candidates())["items"], NS))

    assert run(scenario, modules=[q]) == (1, 0)   # 처음부터 한 줄(답변 오류 쪽), 정한 뒤 0


def test_repeat_decide_same_question_makes_one_row():
    """같은 질문을 두 번 정함(두 번 누름·다른 관리자가 늦게) → 골든셋 줄 1개, 두 번째는 created=False.
    진짜 동시 실행(연결 두 개)은 이 롤백 장치로는 못 본다(잠금이 커밋을 기다림) — 잠금·고유 키 두 개가 지킨다(백로그)."""
    async def scenario(conn):
        n = await ns(conn, NS)
        l1 = await _log(conn, n, "시험_동시 질문?")
        rs = []
        for _ in range(2):
            rs.append(await q.decide_golden_candidate(source="no_knowledge", ref_id=l1, action="abstain", target_id=None, user_id=None))
        return [r["created"] for r in rs], await conn.fetchval("SELECT count(*) FROM eval_question_candidate WHERE namespace_id = $1", n)

    assert run(scenario, modules=[q]) == ([True, False], 1)


@pytest.mark.parametrize("bad", [{"source": "x", "action": "abstain"}, {"source": "no_knowledge", "action": "x"},
                                 {"source": "no_knowledge", "action": "knowledge"}])
def test_input_validation(bad):
    with pytest.raises(ValueError):
        asyncio.run(q.decide_golden_candidate(ref_id=1, target_id=None, user_id=None, **bad))


def test_trailing_newline_question_disappears_once_decided():
    """리뷰 11/16 재현: 질의 기록 질문 끝에 줄바꿈("질문?\n")이 있으면, 정한 뒤에도 옛 줄이 다시 보이고 눌러도 사라지지 않았다
    (목록은 btrim — 스페이스만, 저장은 파이썬 strip). 같은 질문의 거절 두 건 → 후보 1 → 제외 → 후보 0."""
    async def scenario(conn):
        n = await ns(conn, NS)
        await _log(conn, n, "시험_줄바꿈 질문?\r\n")
        l2 = await _log(conn, n, "\t시험_줄바꿈 질문?\r\n")
        first = _mine((await q.pending_golden_candidates())["items"], NS)
        r = await q.decide_golden_candidate(source="no_knowledge", ref_id=first[0]["ref_id"], action="dismiss", target_id=None, user_id=None)
        after = _mine((await q.pending_golden_candidates())["items"], NS)
        return len(first), first[0]["question"], first[0]["ref_id"] == l2, r["created"], len(after)

    assert run(scenario, modules=[q]) == (1, "시험_줄바꿈 질문?", True, True, 0)


def test_policy_params_of_one_item_make_one_evidence_button():
    """정책 파라미터 여러 개는 같은 정책 항목 id — 버튼 하나(화면 key 겹침·같은 이름 버튼 여러 개, 리뷰 11/16)."""
    ev = [{"target_type": "policy_param", "target_id": 5, "label": "정책 · 환불"}, {"target_type": "policy_narrative", "target_id": 5, "label": "정책 · 환불"},
          {"target_type": "knowledge", "target_id": 5, "label": "문서 #5"}, {"target_type": "knowledge", "target_id": 7, "label": "문서 #7"}]
    assert [(e["target_type"], e["target_id"]) for e in q._unique_evidence(ev)] == [("policy_param", 5), ("knowledge", 5), ("knowledge", 7)]


def test_sql_trim_then_strip_equals_python_strip():
    """성질(재리뷰 11/16): SQL로 앞뒤를 자른 뒤 strip == 원문 strip — 그래야 목록(SQL)과 저장(파이썬 strip)이 같은 질문으로 맞는다.
    SQL이 strip이 안 지우는 글자(BOM)를 지우면 정한 후보가 영영 남았다. 흔한·드문 공백 글자 전부로 확인."""
    chars = ["\n", "\t", "\r", "\v", "\f", " ", " ", "　", " ", "​", "﻿", " ", "\x1c"]
    samples = [c + "시험 질문" + c for c in chars] + ["".join(chars) + "가 운데 공백" + "".join(reversed(chars))]

    async def scenario(conn):
        return [await conn.fetchval(f"SELECT {q._TRIM.format('$1::text')}", s) for s in samples]

    got = run(scenario, modules=[q])
    assert [g.strip() for g in got] == [s.strip() for s in samples]
