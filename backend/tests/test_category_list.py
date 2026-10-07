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


def test_block_says_when_truncated():
    b = cl.CategoryList(names=["배송정책"], total=55, lines=["- a > b: c"] * 40).block()
    assert b.startswith("[분류 목록: 배송정책 — 정책 55개 — 그중 40개만 표시")
    assert "그중" not in cl.CategoryList(names=["x"], total=2, lines=["- 1", "- 2"]).block()
