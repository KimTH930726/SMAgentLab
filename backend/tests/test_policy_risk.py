"""정책 위험도 분류(service/policy/risk.py) — 결정론적 등급 경계와 근거 문구."""
import importlib.util as _ilu
import sys
from pathlib import Path

import pytest

_spec = _ilu.spec_from_file_location("service.policy.risk", str(Path(__file__).resolve().parent.parent / "service/policy/risk.py"))
risk = _ilu.module_from_spec(_spec)
sys.modules["service.policy.risk"] = risk
_spec.loader.exec_module(risk)


@pytest.mark.parametrize("parse_status, unresolved, chunks, prior, level", [
    ("unresolved", 2, 1, False, "high"),
    ("partial", 1, 3, False, "high"),
    ("parsed", 0, 1, True, "high"),     # 이전에 반려된 항목은 내용이 단순해도 사람이 다시 본다
    ("parsed", 0, 2, False, "medium"),
    ("parsed", 0, 5, False, "medium"),
    ("parsed", 0, 1, False, "low"),
    ("parsed", 0, 0, False, "low"),     # 청크 0개(실데이터엔 없음 — 폴백 청크가 생김)도 낮음
])
def test_level_boundaries(parse_status, unresolved, chunks, prior, level):
    assert risk.classify(parse_status, unresolved, chunks, prior).level == level


def test_only_low_has_auto_rule():
    assert risk.classify("parsed", 0, 1, False).rule_key == risk.LOW_RISK_RULE
    assert risk.classify("parsed", 0, 2, False).rule_key is None
    assert risk.classify("unresolved", 1, 1, False).rule_key is None


def test_reasons_explain_every_cause():
    """등급마다 '왜 이 등급인지' — 높음 사유가 여럿이면 모두, 청크 분리도 덧붙인다."""
    r = risk.classify("partial", 2, 3, True)
    assert len(r.reasons) == 3
    assert "일부" in r.reasons[0] and "미해결 조각 2개" in r.reasons[0]
    assert "반려" in r.reasons[1] and "3개로 나뉨" in r.reasons[2]
    assert "2개로 나뉨" in risk.classify("parsed", 0, 2, False).reasons[0]


def test_classify_row_handles_nulls():
    row = {"parse_status": "parsed", "unresolved_count": None, "chunk_count": None, "prior_reject": None}
    assert risk.classify_row(row).level == "low"


def test_reverted_auto_pass_is_high_and_never_auto_again():
    """사람이 자동 통과를 되돌린 항목 — 다음 실행·임포트 때 다시 자동 통과되면 되돌린 의미가 없다(/code-review)."""
    r = risk.classify("parsed", 0, 1, False, prior_revert=True)
    assert r.level == "high" and r.rule_key is None and "되돌린" in r.reasons[0]


def test_prior_reject_counts_resubmitted_too():
    """이력 도입 전 반려 → 수정·재제출된 항목은 'rejected' 행 없이 'resubmitted'만 있다 — 그래도 이전 반려로 본다."""
    assert "'resubmitted'" in risk.RISK_FEATURES_SQL.split("AS prior_reject")[0]


@pytest.mark.parametrize("args, short, step_kw", [
    (("partial", 2, 1, False), "미분류 조각 2개", "편입"),
    (("parsed", 0, 1, True), "이전에 반려됨", "반려됐던"),
    (("parsed", 0, 3, False), "서술 3개로 나뉨", "쪼개지지"),
    (("parsed", 0, 1, False), "구조화 완료", "자동 통과"),
])
def test_short_reason_and_next_step(args, short, step_kw):
    """목록 한 줄에 보이는 짧은 이유와 할 일 — 마우스를 올리거나 펼치지 않아도 왜 이 등급인지 알 수 있게(2026-10-02)."""
    r = risk.classify(*args)
    assert r.short == short and step_kw in r.next_step


def test_multiple_high_reasons_all_in_short():
    r = risk.classify("unresolved", 1, 1, True, prior_revert=True)
    assert r.short == "미분류 조각 1개 · 이전에 반려됨 · 자동 통과 되돌림" and "편입" in r.next_step
