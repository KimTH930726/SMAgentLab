"""탐색형 질문의 분류 목록 (2026-10-07) — "○○ 관련 정책 다 보여줘"에 그 분류의 정책을 빠짐없이.

검색은 "관련 깊은 상위 몇 개"라 분류 전체를 보장하지 못한다(실측 포함률 42%, 탐색형 정답 4/24). 분류 찾기 규칙을 고정:
번호 접두사·띄어쓰기 무시, 글자로 나온 가장 긴 이름 우선(같은 이름 여러 곳이면 전부), 없으면 의미 1위가 기준 이상일 때만, 못 찾으면 없음.
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.modules.setdefault("core", MagicMock())
sys.modules.setdefault("core.database", MagicMock())
_spec = importlib.util.spec_from_file_location(
    "_category_list", str(Path(__file__).resolve().parent.parent / "service" / "policy" / "category_list.py"))
cl = importlib.util.module_from_spec(_spec)
sys.modules["_category_list"] = cl   # dataclass가 모듈을 찾을 수 있게
_spec.loader.exec_module(cl)


def test_norm_strips_numbering_and_spacing():
    assert cl.norm_category("4-2.배송정책") == cl.norm_category("배송 정책") == "배송정책"
    assert cl.norm_category("1.배달의민족") == "배달의민족"
    assert cl.norm_category("7-3.B2C 재고") == "b2c재고"


def test_lexical_longest_wins_and_same_length_kept():
    names = ["재고", "b2c재고", "b2c", "교환"]
    assert cl.pick_categories("B2C 재고 관련 정책 전부 보여줘", names, None) == ["b2c재고"]
    assert sorted(cl.pick_categories("재고랑 교환 목록", ["재고", "교환"], None)) == ["교환", "재고"]


def test_semantic_fallback_needs_threshold():
    names = ["회수검수관리", "셀픽"]
    assert cl.pick_categories("반품 들어온 거 확인하는 정책 다 보여줘", names, {"회수검수관리": 0.583, "셀픽": 0.40}) == ["회수검수관리"]
    # 탐색형으로 잘못 판별된 비탐색 질문(실측 0.570) — 엉뚱한 목록을 붙이지 않는다
    assert cl.pick_categories("이 정책을 여러 제품 전체에 한 번에", names, {"회수검수관리": 0.570, "셀픽": 0.3}) == []
    assert cl.pick_categories("아무 질문", names, None) == []


def test_more_specific_category_wins_only_when_clearly_closer():
    """글자로 나온 이름("주문서")을 품은 더 구체적인 분류("주문서쿠폰정책")가 의미상 확연히(여유 이상) 가까울 때만 그쪽(2026-10-12)."""
    names = ["주문", "주문서", "주문서쿠폰정책", "주문서세부정책", "쿠폰"]
    q = "주문서에 적용되는 모든 쿠폰 관련 정책들을 한눈에"
    m = cl._SPECIFIC_MARGIN
    sims = {"주문": 0.55, "주문서": 0.60, "주문서쿠폰정책": 0.60 + m + 0.05, "주문서세부정책": 0.60 + m - 0.05, "쿠폰": 0.5}
    assert cl.pick_categories(q, names, sims) == ["주문서쿠폰정책"]
    # 여유보다 덜 가까우면 글자로 나온 이름 그대로("주문서" 자체를 묻는 질문을 구체 분류로 빼앗지 않게)
    close = {**sims, "주문서쿠폰정책": 0.60 + cl._SPECIFIC_MARGIN - 0.01, "주문서세부정책": 0.6}
    assert cl.pick_categories(q, names, close) == ["주문서"]
    # 글자로 나온 이름을 품지 않은 분류는 아무리 가까워도 후보가 아니다(엉뚱한 분류로 새지 않게)
    other = {**sims, "주문서쿠폰정책": 0.3, "주문서세부정책": 0.3, "배송정책": 0.99}
    assert cl.pick_categories(q, names + ["배송정책"], other) == ["주문서"]
    # 같은 길이로 함께 잡힌 이름은 각각 따로 — 한쪽만 구체 분류로 바뀌고 다른 쪽은 그대로(리뷰 2026-10-12)
    two = cl.pick_categories("배송 환불 정책 전부", ["배송", "환불", "배송비정책"],
                             {"배송": 0.5, "환불": 0.6, "배송비정책": 0.5 + m + 0.05})
    assert sorted(two) == ["배송비정책", "환불"]
    # 유사도가 없는 이름은 바꾸지 않는다
    assert sorted(cl.pick_categories("배송 환불 정책 전부", ["배송", "환불", "배송비정책"], {"배송비정책": 0.99})) == ["배송", "환불"]


def test_block_says_when_truncated():
    b = cl.CategoryList(names=["배송정책"], total=55, lines=["- a > b: c"] * 40).block()
    assert b.startswith("[분류 목록: 배송정책 — 정책 55개 — 그중 40개만 표시")
    assert "그중" not in cl.CategoryList(names=["x"], total=2, lines=["- 1", "- 2"]).block()



class TestTopicOverview:
    """실제 사용자 말투("재고 정책 좀") — 키워드 판별기가 놓치던 주제 전체 질문(2026-10-07 실측 키워드 재현 1/10)."""
    NAMES = ["재고", "재고정책", "환불", "상품", "쿠폰", "배송비"]

    def test_overview_phrasings(self):
        assert cl.is_topic_overview("재고 정책 좀", self.NAMES) == ["재고정책"]
        assert cl.is_topic_overview("재고에 관련된 정책은 없나?", self.NAMES) == ["재고"]
        assert cl.is_topic_overview("쿠폰에 대해서 알려줘", self.NAMES) == ["쿠폰"]
        assert cl.is_topic_overview("환불 규정 알려줘", self.NAMES) == ["환불"]
        assert cl.is_topic_overview("상품 환불 정책은?", self.NAMES) == ["상품", "환불"]   # 둘 다 → 함께 속한 정책만

    def test_specific_questions_are_not_overview(self):
        for q in ["배송비 얼마야?", "쿠폰이 뭐야?", "재고 최대 개수가 몇개야", "환불은 언제 돼?", "쿠폰 회수 어떻게 해?",
                  "단순변심이면 교환 가능해?"]:
            assert cl.is_topic_overview(q, self.NAMES) == [], q

    def test_no_category_name_no_overview(self):
        assert cl.is_topic_overview("사이렌 오더 정책은?", self.NAMES) == []
