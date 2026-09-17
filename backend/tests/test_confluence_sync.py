"""Confluence 페이지 버전 추적(v2.81) — 재임포트 스킵 판정 로직 검증."""
from agents.knowledge_rag.knowledge.router import _confluence_page_is_unchanged


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
