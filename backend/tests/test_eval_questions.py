"""실사용 문항(service/eval/questions.py) — 정정 승인 → 시험지 후보, 시험지에 넣은 것만 골든으로. DB는 가짜 연결로."""
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class _Conn:
    def __init__(self, rows=None, one=None):
        self.rows, self.one, self.calls = rows or [], one, []

    async def fetch(self, sql, *a):
        return self.rows

    async def fetchrow(self, sql, *a):
        self.calls.append(sql)
        return self.one

    async def fetchval(self, sql, *a):
        self.calls.append(sql)
        return 7


def _load(conn):
    db = types.ModuleType("core.database")

    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *e):
            return False

    db.get_conn = lambda: _Ctx()
    prev = sys.modules.get("core.database")
    sys.modules["core.database"] = db   # 이 모듈을 불러올 때만 — 끝나면 원래대로(다른 테스트가 진짜 모듈을 쓰게)
    try:
        spec = importlib.util.spec_from_file_location("eval_questions_t", ROOT / "service" / "eval" / "questions.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    finally:
        if prev is not None:
            sys.modules["core.database"] = prev
        else:
            del sys.modules["core.database"]
    return m


def test_only_chat_reports_become_candidates():
    # 실제 원장 값 그대로 — 채팅 신고도 source가 채워진다(NOT NULL). 첫 판은 source None을 채팅으로 가정해 실제론 0건이었다(10/26 리뷰 H1)
    conn = _Conn(one={"question": "질문", "namespace_id": 1, "logical_id": 5, "source_key": "k1"})
    q = _load(conn)
    base = {"id": 1, "target_type": "policy_narrative", "namespace_id": 1}
    for chat_source in ("chat_answer_auto", "chat_evidence_card", "review_flag_migrated"):
        assert asyncio.run(q.from_correction({**base, "message_id": 3, "source": chat_source}, 9)) == 7
    for other in ("policy_direct", "policy_ai", "v0_load_check", "golden_set"):   # 정책서 화면·적재 점검 — 채팅 메시지 없음
        assert asyncio.run(q.from_correction({**base, "message_id": None, "source": other}, 9)) is None
    assert any("ON CONFLICT (improvement_item_id) DO NOTHING" in c for c in conn.calls)   # 같은 정정은 한 번만


def test_failure_does_not_raise():
    class Boom(_Conn):
        async def fetchrow(self, *a):
            raise RuntimeError("db down")
    q = _load(Boom())
    assert asyncio.run(q.from_correction({"id": 1, "message_id": 3, "source": None, "target_type": "knowledge"}, 9)) is None


def test_real_golden_only_policy_with_key_and_namespace():
    rows = [{"id": 1, "question": "a", "ns": "POS", "gold": json.dumps({"kind": "policy", "source_key": "k1"})},
            {"id": 2, "question": "b", "ns": "POS", "gold": {"kind": "knowledge", "id": 4}},          # 지식 정답 — 정책 골든 아님
            {"id": 3, "question": "c", "ns": "POS", "gold": {"kind": "policy", "source_key": None}},  # 원천 키 없음
            {"id": 4, "question": "d", "ns": None, "gold": {"kind": "policy", "source_key": "k2"}}]   # 컬렉션 없음
    q = _load(_Conn(rows=rows))
    g = asyncio.run(q.real_golden(_Conn(rows=rows)))
    assert [x["qid"] for x in g] == ["REAL-1"]
    dup = rows[:1] + [{**rows[0], "id": 9}]   # 두 사람이 같은 질문을 신고해 둘 다 넣음 — 하나만(측정이 멈추지 않게, 리뷰 M1)
    assert [x["qid"] for x in asyncio.run(q.real_golden(_Conn(rows=dup)))] == ["REAL-1"]
    assert g[0]["layer"] == "real" and g[0]["verdict"] == "approved" and g[0]["gold"] == [{"kind": "policy", "source_key": "k1"}]
