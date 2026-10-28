"""운영 번복(10/27) — 번복 판정(ledger.location_outcome) · 종류별 요약 · 위치 판정 제안 규칙 리플레이 · 후보.
리플레이 불변식: 현행 규칙의 맞은 제안 = 원장에 남은 실제 제안과 같은 규칙으로 센 값, 규칙마다 고침/새로 틀림은 현행 대비, "해 볼 만함"은 새로 틀림 0일 때만."""
import importlib.util as _ilu
import sys
from pathlib import Path

import pytest

_b = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = _ilu.spec_from_file_location(name, str(_b / rel))
    m = _ilu.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


ov = _load("override_under_test", "service/eval/override.py")
led = _load("ledger_under_test", "service/decision/ledger.py")


class TestLocationOutcome:
    def test_override_vs_recommendation_incl_unconfident_candidate(self):
        assert led.location_outcome("3", None, 4, "wrong") == (True, "wrong")
        assert led.location_outcome(None, "3", 4, None) == (True, None)      # 확신 없는 후보를 바꿔도 번복
        assert led.location_outcome(None, "3", 3, "wrong") == (False, None)  # 같은 곳 = 번복 아님, 이유 버림
        assert led.location_outcome(None, None, 3, "wrong") == (False, None)  # 추천 없음(판단 실패) = 번복 아님


def _row(i, kind="knowledge_location", ai=None, final=None, sig=None, changed=False, verdict=None):
    return {"id": i, "kind": kind, "ai": ai or {}, "final": final or {}, "signals": sig or {}, "changed": changed, "verdict": verdict}


class TestSummarize:
    def test_rates_reasons_patterns(self):
        names = {1: "딜리버스", 2: "제휴서비스"}
        rows = [_row(1, ai={"namespace_id": 2}, final={"namespace_id": 1, "override_reason": "wrong"}, changed=True),
                _row(2, sig={"proposed_id": 2}, final={"namespace_id": 1}, changed=True),
                _row(3, ai={"namespace_id": 1}, final={"namespace_id": 1}),
                _row(4, kind="chat_clarify", final={"picked": "*"})]
        clar = next(k for k in ov.summarize(rows, names)["kinds"] if k["kind"] == "chat_clarify")
        assert (clar["overrides"], clar["reasons"]) == (1, {"전부 보기": 1})
        s = ov.summarize(rows, names)
        loc = next(k for k in s["kinds"] if k["kind"] == "knowledge_location")
        assert (loc["decided"], loc["overrides"], loc["rate"]) == (3, 2, round(2 / 3, 3))
        assert loc["reasons"] == {"AI가 틀림": 1, "이유 안 남김": 1}
        assert loc["patterns"] == [{"pattern": "제휴서비스 → 딜리버스", "n": 2}]
        assert loc["override_ids"] == [1, 2] and not loc["rubber_stamp"]

    def test_override_from_values_not_stored_flag(self):
        """10/23 초기 줄처럼 같은 곳인데 changed=True여도 번복이 아니다 · 되묻기 = 전부 보기·직접 다시 물음 · 원문 확인 = 문제 없음."""
        assert ov.is_override(_row(1, sig={"proposed_id": 5}, final={"namespace_id": 5}, changed=True)) is False
        assert ov.is_override(_row(2, kind="chat_clarify", final={"picked": "*"})) is True
        assert ov.is_override(_row(3, kind="chat_clarify", final={"picked": "배송"})) is False
        assert ov.is_override(_row(4, kind="chat_clarify", final={"typed": True})) is True
        assert ov.is_override(_row(5, kind="source_check", final={"issue": False, "reason": "source_ok"})) is True
        assert ov.is_override(_row(6, kind="source_check", final={"issue": True})) is False
        s = ov.summarize([_row(i, kind="chat_clarify", final={"picked": "a"}) for i in range(30)], {})
        assert s["kinds"][0]["rubber_stamp"] is False   # 되묻기는 사용자 선택이라 형식적 승인 대상 아님

    def test_rubber_stamp_only_with_enough_samples(self):
        rows = [_row(i, ai={"namespace_id": 1}, final={"namespace_id": 1}) for i in range(25)]
        assert ov.summarize(rows, {})["kinds"][0]["rubber_stamp"] is True
        assert ov.summarize(rows[:10], {})["kinds"][0]["rubber_stamp"] is False   # 표본 부족이면 경고 안 함


class TestReplay:
    def test_rule_scores_and_safe_best(self):
        # 문서 A: AI=1, 이웃 3표=2 → 사람 2 (이웃 5중 3이면 이웃 규칙이 고침)
        # 문서 B: AI=1, 이웃 4표=1 → 사람 1 (모든 규칙 맞음)
        rows = [{"id": 10, "ai": 1, "neighbors": [2, 2, 2, 1, 3], "final": 2},
                {"id": 11, "ai": 1, "neighbors": [1, 1, 1, 1, 2], "final": 1}]
        rp = ov.replay_location(rows)
        by = {r["rule"]: r for r in rp["rules"]}
        cur = rp["rules"][0]
        assert (cur["hit"], cur["n"]) == (1, 2)
        need3 = by["이웃 5중 3이면 이웃"]
        assert need3["hit"] == 2 and need3["fixed"] == [10] and need3["broken"] == []
        assert rp["best"]["rule"] == "이웃 5중 3이면 이웃"
        for r in rp["rules"]:   # 불변식: 고침·새로 틀림은 현행과의 차이 — 맞은 수 = 현행 + 고침 − 새로 틀림
            assert r["hit"] == cur["hit"] + len(r["fixed"]) - len(r["broken"])

    def test_no_best_when_rule_breaks_something(self):
        rows = [{"id": 1, "ai": 1, "neighbors": [2, 2, 2, 1, 1], "final": 2}, {"id": 2, "ai": 1, "neighbors": [2, 2, 2, 1, 1], "final": 1}]
        rp = ov.replay_location(rows)
        assert rp["best"] is None   # 고치는 만큼 망가뜨리면 추천 안 함

    def test_location_rows_skip_preset_and_no_neighbors(self):
        rows = [_row(1, ai={"namespace_id": 1}, final={"namespace_id": 2}, sig={"neighbors": [[2, 0.7], [2, 0.6]]}),
                _row(2, final={"namespace_id": 2}, sig={"preset_id": 2, "neighbors": [[2, 0.7]]}),
                _row(3, final={"namespace_id": 2}, sig={"neighbors": []}),
                _row(4, kind="chat_clarify", final={"picked": "*"})]
        assert [r["id"] for r in ov.location_rows(rows)] == [1]


# 추천 = 화면이 미리 골라 둔 곳(proposed) — 리뷰 10/27 H1: AI A·이웃 4/5 B면 화면은 B를 미리 고른다. 그대로 두면 번복이 아니다. preset은 번복 대상 아님(M1)
CASES = [
    # (ai, signals, final, 번복?)
    ({"namespace_id": 1}, {"proposed_id": 2}, {"namespace_id": 2}, False),   # H1: 미리 고른 이웃 그대로
    ({"namespace_id": 1}, {"proposed_id": 2}, {"namespace_id": 1}, True),    # 미리 고른 곳에서 AI 값으로 바꿈 = 번복
    ({"namespace_id": 1}, {"proposed_id": 1, "preset_id": 3}, {"namespace_id": 3}, False),   # M1: preset
    ({"namespace_id": 1}, {}, {"namespace_id": 2}, True),                    # 옛 줄(proposed 없음) = AI 값 기준
    ({}, {"proposed_id": 2}, {"namespace_id": 2}, False),
    ({}, {}, {"namespace_id": 2}, False),                                    # 추천 없음 = 번복 아님
]


@pytest.mark.parametrize("ai, sig, final, want", CASES)
def test_location_rule_same_in_ledger_and_override(ai, sig, final, want):
    assert ov.is_override(_row(1, ai=ai, sig=sig, final=final)) is want
    changed, _ = led.location_outcome(ai.get("namespace_id"), sig.get("proposed_id"), final["namespace_id"], None, preset_ns=sig.get("preset_id"))
    assert changed is want


def test_override_sql_matches_python():
    """OVERRIDE_SQL(감사 로그 "번복만"·표시) = is_override(진단) — 실 DB에서 VALUES 표로(쓰기 없음). DB가 없으면 건너뜀(리뷰 10/27 M2)."""
    import asyncio
    import json
    import os
    asyncpg = pytest.importorskip("asyncpg")
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL 없음")
    rows = [_row(i, ai=a, sig=s, final=f) for i, (a, s, f, _) in enumerate(CASES)]
    rows += [_row(100, kind="chat_clarify", final={"picked": "*"}), _row(101, kind="chat_clarify", final={"typed": True}),
             _row(102, kind="chat_clarify", final={"picked": "배송"}), _row(103, kind="source_check", final={"issue": False}),
             _row(104, kind="source_check", final={"issue": True}), _row(105, kind="collection_suggest", final={"picked": True})]

    async def run():
        conn = await asyncpg.connect(url)
        try:
            vals = ", ".join(f"(${4 * k + 1}::int, ${4 * k + 2}::text, ${4 * k + 3}::jsonb, ${4 * k + 4}::jsonb, ${4 * len(rows) + k + 1}::jsonb)"
                             for k in range(len(rows)))
            args = [x for r in rows for x in (r["id"], r["kind"], json.dumps(r["ai"]), json.dumps(r["final"]))] + [json.dumps(r["signals"]) for r in rows]
            got = await conn.fetch(f"SELECT d.id FROM (VALUES {vals}) AS d(id, kind, ai_value, final_value, signals) WHERE {ov.OVERRIDE_SQL}", *args)
            return {r["id"] for r in got}
        finally:
            await conn.close()
    try:
        got = asyncio.run(run())
    except OSError:
        pytest.skip("DB 연결 안 됨")
    assert got == {r["id"] for r in rows if ov.is_override(r)}
