"""적용 이력(10/27) — 후보 결정: 같은 key는 가장 최근 하나, 운영 반영은 그 뒤 다른 결정이 와도 남는다(리뷰 L3). 기법 근거 줄의 지금 상태."""
import datetime as dt
import importlib.util as _ilu
import sys
from pathlib import Path

_spec = _ilu.spec_from_file_location("history_under_test", str(Path(__file__).resolve().parent.parent / "service" / "eval" / "history.py"))
hs = _ilu.module_from_spec(_spec)
sys.modules["history_under_test"] = hs
_spec.loader.exec_module(hs)
T = lambda d: dt.datetime(2026, 10, d, 9)   # noqa: E731


def _r(i, key, status, day):
    return {"id": i, "key": key, "technique": "t", "title": key, "status": status, "reviewed_at": T(day), "review_reason": None,
            "measured_gate_run_ids": [], "adopted_gate_run_id": None}


def test_latest_per_key_but_adopted_kept():
    rows = [_r(1, "a", "held", 20), _r(2, "a", "rejected", 22),            # 같은 key → 최근 하나(안 함)
            _r(3, "b", "adopted", 21), _r(4, "b", "rejected", 25),         # 반영 뒤 안 함 → 둘 다
            _r(5, "c", "proposed", 26)]                                   # 결정 아님
    out = hs.from_decisions(rows, {"t": "검색"}, {"t": "기법"})
    assert sorted((x["candidate_id"], x["result"]) for x in out) == [(2, "안 함"), (3, "운영 반영"), (4, "안 함")]


def test_registry_now_state():
    techs = [{"key": "k", "stage": "검색", "name": "n", "values": [{"value": "기본 꺼짐"}], "evidence": [{"date": "2026-10-27", "note": "x"}]},
             {"key": "j", "stage": "검색", "name": "m", "values": [{"value": "0.48"}], "evidence": [{"date": "2026-10-26", "note": "y"}]}]
    assert [x["now"] for x in hs.from_registry(techs)] == ["꺼짐", None]
