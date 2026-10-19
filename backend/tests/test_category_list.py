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


class TestClarify:
    """재질문(2026-10-12) — 대화가 끊기지 않게 좁은 규칙 하나: 주제만 말한 질문의 목록이 CLARIFY_MAX_ITEMS개 초과일 때만."""

    def _cl(self, total, places=(("주문", 1),), children=(), explicit=False):
        return cl.CategoryList(names=["주문"], total=total, lines=[], places=list(places), children=list(children), explicit_all=explicit)

    def test_asks_only_for_topic_question_over_limit(self):
        big = self._cl(cl.CLARIFY_MAX_ITEMS + 1, children=[("주문 > 장바구니", 10), ("주문 > 선물하기", 8), ("주문 > 결제", 3), ("주문 > 기타", 1)])
        c = cl.clarify_for(big, "주문 정책 좀")
        assert c and [o["scope"] for o in c["options"]] == ["주문 > 장바구니", "주문 > 선물하기", "주문 > 결제", "*"]   # 많은 순 3 + 전부
        assert c["options"][-1]["query"] == "주문 정책 좀"
        assert cl.clarify_for(self._cl(cl.CLARIFY_MAX_ITEMS, children=big.children), "주문 정책 좀") is None   # 기준 이하
        assert cl.clarify_for(self._cl(99, children=big.children, explicit=True), "주문 정책 전부") is None     # 전체 명시
        assert cl.clarify_for(self._cl(99, children=[("주문 > 장바구니", 99)]), "주문 정책 좀") is None        # 나눌 데가 없음
        assert cl.clarify_for(None, "아무 질문") is None                                                    # 목록 없는 일반 질문

    def test_places_first_when_name_is_in_several_jobs(self):
        c = cl.clarify_for(self._cl(30, places=[("배송 > 공통", 20), ("클레임 > 공통", 10)]), "공통 정책 좀")
        assert [o["scope"] for o in c["options"]] == ["배송 > 공통", "클레임 > 공통", "*"] and "여러 업무" in c["message"]

    def test_says_all_and_parent_narrowing(self):
        assert cl.says_all("배송 정책 전부 보여줘") and cl.says_all("모든 쿠폰 정책") and not cl.says_all("배송 정책 좀")
        places = {"배송 > B2B > 공통": {1}, "클레임 > 공통": {2}}
        assert cl._narrow_by_parent("클레임 공통 정책 좀", places) == {"클레임 > 공통": {2}}
        assert cl._narrow_by_parent("공통 정책 좀", places) == places   # 못 좁히면 그대로(다 보여주되 업무별로 묶음)

    def test_block_groups_by_job_when_several_places(self):
        b = self._cl(2, places=[("배송 > 공통", 1), ("클레임 > 공통", 1)]).block()
        assert "업무별로 묶어" in b and "배송 > 공통" in b
        assert "업무별로" not in self._cl(2).block()


class TestFindCategoryListWithIndex:
    """find_category_list를 가짜 DB로 끝까지 — 색인(위치·하위 분류), 고른 범위(scope) 조회, 업무로 좁히기, 전체 명시(리뷰 2026-10-12 M4)."""
    ROWS = [  # (id, category_path, policy_name)
        (1, ["5.클레임", "5-5.공통", "1.중복 클레임"], "중복 클레임"),
        (2, ["4.배송", "4-3.비즈몰(B2B) 배송정책", "1.공통"], "공통 정책"),
        (3, ["3.주문", "3-1.장바구니", "1.담기"], "담기"),
        (4, ["3.주문", "3-1.장바구니", "2.보관"], "보관"),
        (5, ["3.주문", "3-2.선물하기", "1.선물"], "선물"),
        (6, ["3.주문", "A", "x"], "한 글자 하위 분류"),
    ]

    def _patch(self, monkeypatch):
        import asyncio
        from contextlib import asynccontextmanager
        rows = self.ROWS

        class Conn:
            async def fetch(self, sql, *a):
                if "SELECT id, category_path" in sql:
                    return [{"id": i, "category_path": p} for i, p, _ in rows]
                ids = set(a[0])
                return [{"cat": " > ".join(p), "policy_name": n, "first": "본문"} for i, p, n in rows if i in ids]

        @asynccontextmanager
        async def cm():
            yield Conn()

        class Emb:
            async def embed(self, text):
                return [0.0, 0.0]
        import types
        monkeypatch.setattr(cl, "get_conn", cm)
        monkeypatch.setitem(sys.modules, "shared.embedding", types.SimpleNamespace(embedding_service=Emb()))
        cl._cache.clear()
        return asyncio

    def test_index_places_children_and_scope(self, monkeypatch):
        asyncio = self._patch(monkeypatch)
        run = lambda q, **k: asyncio.run(cl.find_category_list(1, q, [0.0, 0.0], **k))
        r = run("주문 정책 좀", keyword_nav=False)
        assert r.total == 4 and r.places == [("주문", 4)]
        assert sorted(c for c, _ in r.children) == ["주문 > 선물하기", "주문 > 장바구니"]   # 한 글자 하위 분류("A")는 선택지 아님
        assert not r.explicit_all
        s = run("주문 > 장바구니 정책 전부 보여줘", keyword_nav=True, scope="주문 > 장바구니")   # 고른 범위는 경로로 바로
        assert s.total == 2 and s.names == ["장바구니"] and not s.scope_missed
        m = run("주문 정책 전부", keyword_nav=True, scope="주문 > 없음")
        assert m is not None and m.scope_missed and m.total == 4   # 못 찾으면 질문 글로 다시 찾고 표시

    def test_same_name_in_two_jobs_narrowed_by_question(self, monkeypatch):
        asyncio = self._patch(monkeypatch)
        both = asyncio.run(cl.find_category_list(1, "공통 정책 전부", [0.0, 0.0], keyword_nav=True))
        assert both.total == 2 and len(both.places) == 2 and both.explicit_all and "업무별로 묶어" in both.block()
        only = asyncio.run(cl.find_category_list(1, "클레임 공통 정책 전부", [0.0, 0.0], keyword_nav=True))
        assert only.total == 1 and only.places == [("클레임 > 공통", 1)]


def test_parent_narrowing_longest_first():
    """"주문서 공통"에서 "주문"이 "주문서" 안에 있다고 "주문 > 공통"까지 남기지 않는다."""
    places = {"주문 > 공통": {1}, "주문서 > 공통": {2}, "배송 > 공통": {3}}
    assert cl._narrow_by_parent("주문서 공통 정책 좀", places) == {"주문서 > 공통": {2}}


def test_says_all_ignores_words_inside_category_names():
    assert not cl.says_all("전체취소 정책 좀", ["전체취소"]) and cl.says_all("전체취소 정책 전부", ["전체취소"])


def test_children_must_cover_most_of_list():
    """하위 분류가 목록 대부분을 덮을 때만 하위 분류로 묻는다 — 분류에 바로 붙은 정책이 많으면 안 묻는다."""
    many_direct = cl.CategoryList(names=["주문"], total=30, lines=[], places=[("주문", 30)],
                                  children=[("주문 > 장바구니", 2), ("주문 > 결제", 1)])
    assert cl.clarify_for(many_direct, "주문 정책 좀") is None


def test_specific_keeps_non_ancestor_places_and_leaf_not_parent():
    """재리뷰 2026-10-12: 조상 관계가 아닌 두 위치는 둘 다 남고, 고른 이름 안의 글자는 상위 이름으로 안 잡는다."""
    places = {"쿠폰 > 쿠폰회수": {1}, "클레임 > 쿠폰회수": {2}}
    assert cl._narrow_by_parent("쿠폰회수 정책 전부", places) == places   # "쿠폰회수"의 "쿠폰"은 상위 "쿠폰"이 아니다
    assert cl._narrow_by_parent("클레임 쿠폰회수 정책", places) == {"클레임 > 쿠폰회수": {2}}


class TestSpecificRule:
    ROWS = [(1, ["4.배송", "1.공통", "x"], "a"), (2, ["4.배송", "4-3.B2B", "1.공통"], "b"), (3, ["4.배송", "2.기타"], "c"),
            (4, ["1.주문관리", "3-1. 2) 공통"], "d")]

    def _run(self, monkeypatch, q):
        TestFindCategoryListWithIndex.ROWS, saved = self.ROWS, TestFindCategoryListWithIndex.ROWS
        try:
            asyncio = TestFindCategoryListWithIndex()._patch(monkeypatch)
            return asyncio.run(cl.find_category_list(1, q, [0.0, 0.0], keyword_nav=True))
        finally:
            TestFindCategoryListWithIndex.ROWS = saved

    def test_non_ancestor_places_both_kept(self, monkeypatch):
        r = self._run(monkeypatch, "배송 공통 정책 전부")
        assert sorted(p for p, _ in r.places) == ["배송 > B2B > 공통", "배송 > 공통"]

    def test_double_numbered_category_no_keyerror(self, monkeypatch):
        r = self._run(monkeypatch, "주문관리 공통 정책 전부")
        assert r is not None and r.total >= 1



def test_block_numbers_every_listed_policy():
    lines = [f"- a > 정책{i}: 첫 줄" for i in range(1, 21)]
    b = cl.CategoryList(names=["a"], total=20, lines=lines, item_ids=list(range(20))).block()
    body = b.split("\n")[1:]
    assert len(body) == 20                                             # 유실·중복 없음
    assert [ln.split(". ", 1)[0] for ln in body] == [str(i) for i in range(1, 21)]
    assert [ln.split(". ", 1)[1] for ln in body] == [ln[2:] for ln in lines]   # 내용은 그대로


def test_invalidate_clears_index():
    cl._cache[1] = object()
    cl.invalidate()
    assert cl._cache == {}
