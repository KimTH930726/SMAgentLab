"""Tests for service/policy/query_type.py — navigation형 질의 판별 규칙.

89문항 골든셋 실측(precision 96.0%, recall 100.0%)에서 확인된 실제 케이스를 그대로
회귀 테스트로 남긴다 — 패턴을 나중에 손댈 때 이 경계 케이스들이 계속 맞는지 보장하려고."""
import importlib.util as _ilu
from pathlib import Path

_spec = _ilu.spec_from_file_location(
    "service.policy.query_type", str(Path(__file__).resolve().parent.parent / "service" / "policy" / "query_type.py")
)
_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
looks_like_navigation_query = _mod.looks_like_navigation_query


class TestLooksLikeNavigationQuery:
    def test_detects_real_navigation_examples(self):
        assert looks_like_navigation_query("우리 회사에서 환불을 어떻게 처리하는지 전체적으로 알려줄 수 있을까요?")
        assert looks_like_navigation_query("우리 회사에서 주문에 관련된 모든 정책을 한 번에 볼 수 있을까요?")
        assert looks_like_navigation_query("장바구니 관련 정책 다 보여줘")
        assert looks_like_navigation_query("배달비 정책 리스트 좀")
        assert looks_like_navigation_query("근무시간 규정이 어떻게 되어있는지 한눈에 볼 수 있을까요?")

    def test_does_not_flag_param_or_narrative_examples(self):
        assert not looks_like_navigation_query("도보 배달할 때 장바구니에 몇 개까지 담을 수 있어?")
        assert not looks_like_navigation_query("재고 없으면 화면에 어떻게 표시돼?")
        assert not looks_like_navigation_query("야구장 매장에만 적용되는 규칙은?")

    def test_known_false_positive_kept_as_documented_limitation(self):
        """실측 때 유일한 오탐이었던 케이스 — "전체"+"한 번에"가 겹쳐서 narrative인데
        navigation으로 잘못 판별됨. 패턴을 더 좁히면 recall이 떨어지므로(실측 100%→낮아짐)
        의도적으로 감수한 한계다. 이 테스트는 "여전히 이렇게 동작한다"는 걸 문서화."""
        assert looks_like_navigation_query("이 정책을 여러 제품이나 서비스 그룹 전체에 한 번에 적용할 수 있나요?")

    def test_quantity_question_with_han_beon_e_not_flagged(self):
        """"한 번에" 단독으로는 신호로 안 씀(수량 질문과 충돌해 정밀도가 떨어졌었음,
        2026-09-11 실측: 82.1%→96.0%)."""
        assert not looks_like_navigation_query("도보로 배달할 때 한 번에 주문할 수 있는 메뉴가 몇 개인지 알려줄 수 있나요?")
