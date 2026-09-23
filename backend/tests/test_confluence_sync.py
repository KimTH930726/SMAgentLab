"""Confluence 페이지 버전 추적(v2.81) — 재임포트 스킵 판정 로직 검증."""
from agents.knowledge_rag.knowledge.router import _confluence_page_is_unchanged, _enrich_heading_path


class TestConfluencePageIsUnchanged:
    def test_new_page_is_not_unchanged(self):
        assert _confluence_page_is_unchanged("123", 5, {}) is False

    def test_same_version_is_unchanged(self):
        assert _confluence_page_is_unchanged("123", 5, {"123": 5}) is True

    def test_different_version_is_not_unchanged(self):
        assert _confluence_page_is_unchanged("123", 6, {"123": 5}) is False

    def test_missing_fetched_version_always_reimports(self):
        """Confluence API가 version을 안 주면(구버전 서버 등) 안전한 쪽(재등록)으로."""
        assert _confluence_page_is_unchanged("123", None, {"123": 5}) is False

    def test_other_page_ids_dont_interfere(self):
        existing = {"111": 3, "222": 7}
        assert _confluence_page_is_unchanged("222", 7, existing) is True
        assert _confluence_page_is_unchanged("111", 7, existing) is False


class _FakeDoc:
    def __init__(self, parent_title):
        self.metadata = {"parent_title": parent_title}


class TestEnrichHeadingPath:
    """직계 상위 페이지 제목을 heading_path 맨 앞에 얹는 로직(2026-09-22, §8 카테고리
    자동화 부가 효과) — 실사고(배민/쿠팡이츠 완전 동일 텍스트)의 일반 해법."""

    def test_prepends_parent_title_to_in_page_headings(self):
        result = _enrich_heading_path(_FakeDoc("외부서비스"), ["1.2.1 매장", "1.2.1.1 매장 관리"])
        assert result == ["외부서비스", "1.2.1 매장", "1.2.1.1 매장 관리"]

    def test_page_with_no_in_page_headings_still_gets_parent(self):
        """표만 있는 페이지처럼 h1~h4가 전혀 없어도, 상위 페이지 제목만으로 최소한의
        구분 신호는 남아야 한다(배민/쿠팡이츠 실사고가 정확히 이 케이스)."""
        assert _enrich_heading_path(_FakeDoc("외부서비스"), []) == ["외부서비스"]
        assert _enrich_heading_path(_FakeDoc("외부서비스"), None) == ["외부서비스"]

    def test_root_page_without_parent_keeps_in_page_headings_only(self):
        assert _enrich_heading_path(_FakeDoc(None), ["1장", "1.1절"]) == ["1장", "1.1절"]

    def test_no_duplicate_when_first_in_page_heading_matches_parent(self):
        result = _enrich_heading_path(_FakeDoc("외부서비스"), ["외부서비스", "세부"])
        assert result == ["외부서비스", "세부"]
