"""AI 감사 로그 한 줄 설명(10/27) — 종류마다 AI가 한 것 / 사람·사용자가 한 것(확정 전이면 None). 원문 없이 이름·숫자만."""
import importlib.util as _ilu
import sys
from pathlib import Path

import pytest

_ov_spec = _ilu.spec_from_file_location("service.eval.override", str(Path(__file__).resolve().parent.parent / "service" / "eval" / "override.py"))
_ov = _ilu.module_from_spec(_ov_spec)
sys.modules["service.eval.override"] = _ov   # conftest가 service 패키지를 가짜로 둔다 — decision_log가 가져오는 번복 규칙은 진짜로
_ov_spec.loader.exec_module(_ov)
_p = Path(__file__).resolve().parent.parent / "service" / "eval" / "decision_log.py"
_spec = _ilu.spec_from_file_location("decision_log_under_test", str(_p))
dl = _ilu.module_from_spec(_spec)
sys.modules["decision_log_under_test"] = dl
_spec.loader.exec_module(dl)
NAMES = {1: "딜리버스", 2: "제휴서비스"}


@pytest.mark.parametrize("ai, final, signals, want_ai, want_human", [
    ({"namespace_id": 1, "doc_type": "지식 문서"}, None, {}, "넣을 곳 → 딜리버스 · 지식 문서", None),
    ({"namespace_id": None}, {"namespace_id": 1, "choice": "other"}, {"proposed_id": 2}, "후보 제휴서비스 — 확신 없어 사람에게 물음", "딜리버스에 등록"),
    ({"namespace_id": 1}, {"namespace_id": 1, "choice": "accept"}, {}, "넣을 곳 → 딜리버스", "딜리버스에 등록 (AI 후보 그대로)"),
    ({}, None, {}, "정하지 못함 — 사람에게 물음", None),
])
def test_location(ai, final, signals, want_ai, want_human):
    d = dl.describe("knowledge_location", ai, final, signals, NAMES)
    assert d == {"ai": want_ai, "human": want_human}


@pytest.mark.parametrize("final, human", [
    (None, None), ({"picked": "*"}, "전부 보기"), ({"picked": "배송 > 알림"}, "고름: 배송 > 알림"), ({"typed": True}, "고르지 않고 직접 다시 물음"),
    ({"superseded": True}, "다음 되묻기로 넘어감"), ({"picked_unknown": True}, "선택지 밖 값"),
])
def test_clarify(final, human):
    d = dl.describe("chat_clarify", {"options": ["a", "b", "*"]}, final, {"n_options": 3}, NAMES)
    assert d == {"ai": "선택지 3개로 되물음", "human": human}


def test_source_check_and_suggest():
    ai = {"type": "conflict", "source": "load"}
    assert dl.describe("source_check", ai, {"issue": False, "reason": "duplicate"}, {}, {})["human"] == "문제 없음: 중복"
    assert dl.describe("source_check", ai, {"issue": True, "correction_id": 7}, {}, {})["human"] == "해결(정정 #7 승인)"
    s = dl.describe("collection_suggest", {"namespace": "제휴서비스"}, None, {"score": 0.52}, {})
    assert s == {"ai": "제휴서비스에서 찾아보기 제안 (관련도 0.52)", "human": None}
    assert dl.describe("collection_suggest", {"namespace": "x"}, {"picked": True}, {}, {})["human"] == "눌러서 다시 물음"
    assert dl.describe("unknown_kind", {}, {"x": 1}, {}, {}) == {"ai": "unknown_kind", "human": "확정"}


def test_location_changed_never_says_kept():
    """리뷰 L1 — choice는 옛 값·빈 값이 accept로 저장된다. 원장이 바뀜(human_changed)이라고 하면 "AI 후보 그대로"를 붙이지 않는다."""
    d = dl.describe("knowledge_location", {"namespace_id": 1}, {"namespace_id": 2, "choice": "accept"}, {}, NAMES, changed=True)
    assert d["human"] == "제휴서비스에 등록"


def test_duplicate_check():
    """중복 의심(11/03) — AI = 가장 비슷한 기존 지식·유사도, 사람 = 처리(그대로 저장·덮어쓰기·반려), 확정 전이면 None."""
    ai = {"duplicate_of": 12, "similarity": 0.962}
    assert dl.describe("duplicate_check", ai, None, {}, {}) == {"ai": "기존 #12와 중복 의심 (유사도 96.2%)", "human": None}
    assert dl.describe("duplicate_check", ai, {"action": "approve"}, {}, {})["human"] == "그대로 저장(중복 아님)"
    assert dl.describe("duplicate_check", ai, {"action": "reject"}, {}, {})["human"] == "반려(중복 맞음)"
    assert dl.describe("duplicate_check", ai, {"action": "merge", "target_id": 12}, {}, {})["human"] == "#12에 덮어쓰기"
    assert dl.describe("duplicate_check", {"duplicate_of": None, "similarity": None}, None, {}, {})["ai"] == "기존 #?와 중복 의심"
