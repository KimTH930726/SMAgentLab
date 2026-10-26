"""다른 컬렉션에서 찾아보기 제안 규칙(service/chat/collection_suggest.should_suggest, C2) — 측정(scripts/eval_collection_suggest.py)과 같은 식."""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_db = types.ModuleType("core.database")
_db.get_conn = None
_prev = sys.modules.get("core.database")
sys.modules["core.database"] = _db
try:
    _spec = importlib.util.spec_from_file_location("collection_suggest_t", ROOT / "service" / "chat" / "collection_suggest.py")
    cs = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(cs)
finally:
    if _prev is not None:
        sys.modules["core.database"] = _prev
    else:
        del sys.modules["core.database"]


def test_rule_matches_measured_floor_and_margin():
    assert cs.FLOOR == 0.48 and cs.MARGIN == 0.0   # 10/26 실사용 측정으로 정한 값(틀린 제안 0) — 바꾸면 두 측정을 다시 잰다
    assert cs.should_suggest(here_best=0.30, here_adopted=0, other_best=0.49)       # 여기 근거 없음 + 다른 곳 하한 넘음
    assert not cs.should_suggest(here_best=0.30, here_adopted=0, other_best=0.46)   # 하한 미달(실사용에서 틀린 제안이던 점수대)
    assert cs.should_suggest(here_best=0.50, here_adopted=2, other_best=0.52)       # 여기도 있지만 다른 곳이 더 높음
    assert not cs.should_suggest(here_best=0.60, here_adopted=2, other_best=0.52)   # 여기가 더 맞음 — 묻지 않음


def test_rule_is_monotone_in_other_score():
    """성질: 다른 곳 점수가 오르면 제안이 꺼지지 않는다(하한·차이 규칙이 뒤집히지 않음)."""
    for here in (0.2, 0.5, 0.7):
        for adopted in (0, 3):
            seen = False
            for k in range(0, 101):
                s = cs.should_suggest(here, adopted, k / 100)
                assert not (seen and not s), (here, adopted, k)
                seen = seen or s
