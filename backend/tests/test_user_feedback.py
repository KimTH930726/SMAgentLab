"""사용자 피드백(10/30) — 자동 감지 장애 종류는 답 문구로만 정한다(원문을 내보내지 않음) · 모든 장애 문구가 세 종류 중 하나."""
import importlib.util as _ilu
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = _ilu.spec_from_file_location(name, str(_ROOT / rel))
    m = _ilu.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


# 실제 문구는 따로 이름으로 불러 두고, failure_kind가 안에서 import할 때만 monkeypatch로 끼운다 — 전역 sys.modules를 바꿔 두면
# 다른 테스트가 conftest의 가짜 모듈 대신 실제 모듈을 받는다(리뷰 L4, test_query_log_status와 같은 방식)
helpers = _load("chat_helpers_under_test", "service/chat/helpers.py")
router = _load("user_feedback_router_under_test", "service/user_feedback/router.py")
LLM_EMPTY_MSG, LLM_UNAVAILABLE_MSG, SYSTEM_ERROR_MSG = helpers.LLM_EMPTY_MSG, helpers.LLM_UNAVAILABLE_MSG, helpers.SYSTEM_ERROR_MSG
is_gateway_refusal, STATUSES, failure_kind = helpers.is_gateway_refusal, router.STATUSES, router.failure_kind


def test_failure_kind_by_answer_text(monkeypatch):
    monkeypatch.setitem(sys.modules, "service.chat.helpers", helpers)
    assert failure_kind(LLM_UNAVAILABLE_MSG) == "LLM 연결 실패·빈 응답"
    assert failure_kind(LLM_EMPTY_MSG) == "LLM 연결 실패·빈 응답"
    assert failure_kind(None) == "LLM 연결 실패·빈 응답" and failure_kind("") == "LLM 연결 실패·빈 응답"
    assert failure_kind(SYSTEM_ERROR_MSG) in ("처리 오류", "게이트웨이 거부")
    for a in (LLM_UNAVAILABLE_MSG, LLM_EMPTY_MSG, SYSTEM_ERROR_MSG, "아무 답"):
        assert failure_kind(a) in ("게이트웨이 거부", "LLM 연결 실패·빈 응답", "처리 오류")
        if is_gateway_refusal(a):
            assert failure_kind(a) == "게이트웨이 거부"


def test_statuses_match_migration_check():
    """화면·API 상태 = DB CHECK — main.py #103과 같은 다섯 개."""
    import re
    src = (_ROOT / "main.py").read_text(encoding="utf-8")
    m = re.search(r"CREATE TABLE IF NOT EXISTS ops_user_feedback.*?status TEXT NOT NULL DEFAULT 'new' CHECK \(status IN \(([^)]*)\)\)", src, re.S)
    assert m and tuple(x.strip(" '") for x in m.group(1).split(",")) == STATUSES
