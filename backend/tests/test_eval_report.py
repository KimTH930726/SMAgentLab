"""틀린 문항 리포트의 저장 구조 표시(service/eval/report.storage_tags) — 데이터 사실에서 나온 표시만, 중복 없이, 경우별로."""
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
    _spec = importlib.util.spec_from_file_location("eval_report_t", ROOT / "service" / "eval" / "report.py")
    rp = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(rp)
finally:
    if _prev is not None:
        sys.modules["core.database"] = _prev
    else:
        del sys.modules["core.database"]

POL = {"k1": {"name": "무료배송 기준", "path": "배송", "body": "온라인 주문 30,000원 이상 무료배송", "body_len": 20, "params": 0, "chunks": 1,
              "table": False, "remark": False},
       "k2": {"name": "지역 배송비", "path": "배송", "body": "구간 | 금액 |", "body_len": 9, "params": 3, "chunks": 1, "table": True, "remark": True}}
REF = {"r1": {"name": "지역별 배송비", "rows": 12}}


def test_tags_reflect_storage_facts():
    items, tags = rp.storage_tags("온라인 무료배송 기준 금액?", [{"kind": "policy", "source_key": "k1"}], POL, REF,
                                  mapped="무료배송", gold_in_context=True, unused_qid=False)
    assert items[0]["name"] == "무료배송 기준" and tags == ["원문만 · 파라미터 0개"]
    _, t2 = rp.storage_tags("지역 배송비?", [{"kind": "policy", "source_key": "k1"}, {"kind": "policy", "source_key": "k2"}], POL, REF,
                            mapped=None, gold_in_context=False, unused_qid=False)
    for t in ("원문만 · 파라미터 0개", "표·구간으로 적힘", "정답이 정책 여러 개에 흩어짐", "정답 정책이 문맥에 못 들어감", "용어 매핑 없음"):
        assert t in t2, t
    assert len(t2) == len(set(t2))
    _, t3 = rp.storage_tags("도서 지역?", [{"kind": "ref_table", "source_key": "r1"}], POL, REF, mapped=None, gold_in_context=None, unused_qid=True)
    assert t3 == ["참고표에만 있음 — 채팅에 안 쓰임"]   # 참고표 문항엔 정책 표시(용어 매핑 등)를 붙이지 않는다
    _, t4 = rp.storage_tags("없는 질문", [], POL, REF, mapped=None, gold_in_context=None, unused_qid=False)
    assert t4 == ["정답 데이터 없음(답 없는 질문)"]
    _, t5 = rp.storage_tags("q", [{"kind": "policy", "source_key": "gone"}], POL, REF, mapped="x", gold_in_context=True, unused_qid=False)
    assert "정답 데이터가 현행에 없음" in t5


def test_locked_effect_weighted_and_down():
    a = {"groups": {"human": {"n": 40, "before": 0.775, "after": 0.8, "diff": 0.025}, "real": {"n": 10, "before": 0.5, "after": 0.4, "diff": -0.1}}}
    e = rp._locked_effect(a, ("human", "real"))
    assert e["n"] == 50 and abs(e["before"] - (0.775 * 40 + 0.5 * 10) / 50) < 1e-9 and e["down"] is True
    assert rp._locked_effect({"groups": {"fact": {"n": 3, "before": 1, "after": 1, "diff": 0}}}, ("human", "real")) is None


def test_layer_qids_match_gate_counting_rules():
    """층 줄을 누르면 여는 문항 — 판정 숫자와 같은 규칙(검색: 답 없는 질문·다른 방식 줄 제외 / 답: 회차 평균 < 0.5, 판정불가 제외)."""
    retr = {"A": [{"qid": "A", "type": "fact", "hit": False, "mode": "lexical"}],
            "B": [{"qid": "B", "type": "fact", "hit": True, "mode": "lexical"}],
            "C": [{"qid": "C", "type": "fact", "hit": False, "mode": "embedding"}],      # 다른 방식 — 세지 않음
            "N": [{"qid": "N", "type": "abstain", "hit": False, "set": "negative"}],     # 답 없는 질문 — 검색 분모 밖
            "L": [{"qid": "L", "type": "list", "hit": False}]}                           # mode 없는 파일 — 그대로
    j = lambda lab: {"judge": {"label": lab}}   # noqa: E731
    ans = {"A": [{"type": "fact", **j("오답")}, {"type": "fact", **j("오답")}],
           "B": [{"type": "fact", **j("정답")}, {"type": "fact", **j("오답")}],          # 반반 — 틀림 아님
           "N": [{"type": "abstain", **j("오답")}],
           "X": [{"type": "fact", **j("판정불가")}]}                                     # 분모 밖
    out = rp.layer_qids(ans, retr)
    assert out["missed"] == {"fact": ["A"], "list": ["L"]}
    assert out["wrong"] == {"fact": ["A"], "abstain": ["N"]}
    assert rp.layer_qids(None, None) == {"missed": {}, "wrong": {}}


def test_storage_inputs_same_for_report_and_detail():
    """리뷰 L3 — 리포트·문항 상세가 같은 (매핑, 문맥 포함)을 쓴다: 매핑은 검색 행 우선, 문맥 포함은 답 행에만(없으면 모름)."""
    ans = [{"mapped_term": "답쪽", "gold_in_context": False}]
    assert rp.storage_inputs(ans, {"mapped": "검색쪽"}) == ("검색쪽", False)
    assert rp.storage_inputs(ans, None) == ("답쪽", False)
    assert rp.storage_inputs(None, {"mapped": None}) == (None, None)   # 검색만 잰 판정 — "문맥에 못 들어감"으로 단정하지 않음
