"""청킹 엔진 — 2026-09-17 Confluence 일괄 임포트 파일럿 실측 버그 2건 회귀 테스트.

두 버그 모두 backend/scripts/... 없이, 파일럿 chunk 원문(id=12850~12867)을 직접 읽어
발견함(docs 없음, 세션 기록 참고). 픽스처는 그 원문 패턴을 최소 재현한 것.
"""
from agents.knowledge_rag.ingestion.adapters import ParsedDocument
from agents.knowledge_rag.ingestion.chunker import (
    MAX_CHUNK_CHARS,
    _chunk_by_sections,
    _strip_macro_artifacts,
)


def _doc(sections: list[dict]) -> ParsedDocument:
    return ParsedDocument(source_type="confluence", source_name="test", raw_text="", sections=sections)


class TestMacroArtifactStripping:
    def test_strips_diagram_macro_line(self):
        text = (
            "배달 주문 개인정보 파기 정책에 따른 데이터 삭제\n"
            "true 3.11. 장바구니 선계산 false auto top DlvsDeletePrivacy true 771 7"
        )
        result = _strip_macro_artifacts(text)
        assert "false auto top" not in result
        assert "배달 주문 개인정보 파기 정책" in result

    def test_leaves_normal_text_untouched(self):
        text = "정상적인 문장에는 true/false 값이 언급될 수 있다."
        assert _strip_macro_artifacts(text) == text


class TestShortIntroCarriedForward:
    def test_short_section_reattached_to_orphaned_child(self):
        """12856→12857 재현: "1.3.2 신규 주문"(짧은 도입부)이 직전의 큰 섹션(1.3.1)에
        붙어 flush되고, 그 하위 섹션(1.3.2.1)이 부모 제목 없이 고립되던 버그."""
        long_content = "가" * (MAX_CHUNK_CHARS - 200)  # 1.3.1과 합쳐지면 max를 넘기도록
        sections = [
            {"title": "1.3.1. 주문 유효성 검증", "content": long_content, "level": 2},
            {"title": "1.3.2. 신규 주문", "content": "성공 이후에 신규 주문을 전송한다.", "level": 2},
            {"title": "1.3.2.1. 시점", "content": "나" * 300, "level": 3},
        ]
        chunks = _chunk_by_sections(_doc(sections), MAX_CHUNK_CHARS, 50)

        # "1.3.2.1. 시점" chunk을 찾아 "1.3.2. 신규 주문" 맥락이 같이 딸려왔는지 확인
        orphan_candidates = [c for c in chunks if "1.3.2.1" in c.text]
        assert orphan_candidates, "1.3.2.1 섹션이 사라짐"
        assert "1.3.2. 신규 주문" in orphan_candidates[0].text

    def test_no_prefix_leak_when_not_needed(self):
        """짧은 섹션이 정상적으로 다음 섹션과 합쳐지는(flush 없이) 일반 케이스는 중복 삽입 없어야."""
        sections = [
            {"title": "A", "content": "짧은 도입부", "level": 1},
            {"title": "B", "content": "본문", "level": 2},
        ]
        chunks = _chunk_by_sections(_doc(sections), MAX_CHUNK_CHARS, 10)
        assert len(chunks) == 1
        assert chunks[0].text.count("짧은 도입부") == 1
