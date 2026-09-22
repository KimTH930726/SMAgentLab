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


class TestHeadingPath:
    """Parent-Child 문맥 유실 수정(2026-09-22) — 조상 헤딩 추적 회귀 테스트.

    외부서비스DB 실사고(배달의민족/쿠팡이츠 청크가 완전 동일 텍스트인데 채널 구분
    정보가 없었음)의 근본 원인 재현: 상위 채널 헤딩(레벨 1) 밑에 똑같은 하위 섹션
    번호(레벨 2)가 반복되는 구조에서, 하위 섹션만 매칭돼도 상위 채널을 알 수 있어야
    한다."""

    def test_ancestor_heading_attached_to_deeply_nested_section(self):
        long_padding = "패딩" * (MAX_CHUNK_CHARS // 2)  # 강제로 각 섹션이 별도 청크가 되게
        sections = [
            {"title": "배달의민족", "content": "배달의민족 채널 정의", "level": 1},
            {"title": "매장", "content": long_padding, "level": 2},
            {"title": "시점", "content": "유효성 검증 이후 결제가 완료된 시점에 전송된다.", "level": 3},
            {"title": "쿠팡이츠", "content": "쿠팡이츠 채널 정의", "level": 1},
            {"title": "매장", "content": long_padding, "level": 2},
            {"title": "시점", "content": "유효성 검증 이후 결제가 완료된 시점에 전송된다.", "level": 3},
        ]
        chunks = _chunk_by_sections(_doc(sections), MAX_CHUNK_CHARS, 10)

        sijeom_chunks = [c for c in chunks if "유효성 검증 이후 결제가 완료된 시점" in c.text]
        assert len(sijeom_chunks) == 2, f"'시점' 내용을 담은 청크가 2개여야 함: {[c.text for c in chunks]}"
        heading_paths = [c.heading_path for c in sijeom_chunks]
        # 조상 전체(채널 레벨 + 중간 "매장" 레벨)가 다 담겨야 함
        assert ["배달의민족", "매장"] in heading_paths
        assert ["쿠팡이츠", "매장"] in heading_paths

    def test_sibling_heading_does_not_leak_into_ancestor_path(self):
        """같은 레벨의 형제 섹션은 조상이 아니다 — 스택에서 pop돼야 함."""
        sections = [
            {"title": "1.2.1", "content": "형제A", "level": 2},
            {"title": "1.2.2", "content": "형제B", "level": 2},
        ]
        chunks = _chunk_by_sections(_doc(sections), MAX_CHUNK_CHARS, 10)
        merged = [c for c in chunks if "형제B" in c.text]
        assert merged
        assert "1.2.1" not in merged[0].heading_path


class TestAlwaysSplitAndContinuation:
    """confluence-chunking-spec.md §3 결론 반영 + 조건/예외 연결 표현 규칙(2026-09-22)."""

    def test_always_split_keeps_small_sections_separate(self):
        """always_split=False면 작은 섹션끼리 병합되지만, True면 항상 따로 flush."""
        sections = [
            {"title": "A", "content": "짧은 내용 A", "level": 2},
            {"title": "B", "content": "짧은 내용 B", "level": 2},
        ]
        merged = _chunk_by_sections(_doc(sections), MAX_CHUNK_CHARS, 10, always_split=False)
        split = _chunk_by_sections(_doc(sections), MAX_CHUNK_CHARS, 10, always_split=True)
        assert len(merged) == 1
        assert len(split) == 2

    def test_continuation_word_forces_merge_even_when_always_split(self):
        """"단, ..."으로 시작하는 섹션은 always_split=True여도 직전 청크에 강제 병합."""
        sections = [
            {"title": "조치", "content": "담당자 승인 후 재처리한다.", "level": 2},
            {"title": "", "content": "단, 서비스를 재시작하면 안 된다.", "level": 0},
        ]
        chunks = _chunk_by_sections(_doc(sections), MAX_CHUNK_CHARS, 10, always_split=True)
        assert len(chunks) == 1
        assert "재처리한다" in chunks[0].text
        assert "단, 서비스를 재시작하면 안 된다" in chunks[0].text

    def test_continuation_word_mid_sentence_not_falsely_matched(self):
        """본문 중간에 "단"/"주의" 등이 나오는 정상 문장은 연결 표현으로 오인하면 안 됨."""
        sections = [
            {"title": "A", "content": "이 정책은 일단 적용을 시작하면 되돌릴 수 없다.", "level": 2},
            {"title": "B", "content": "주의사항 문서를 반드시 확인해야 한다.", "level": 2},
        ]
        # "주의사항"으로 시작하는 B는 실제로 연결 규칙에 해당(의도된 매치) — A는 "단"이
        # 중간에 있을 뿐 시작이 아니므로 무관하게 always_split대로 분리돼야 함
        chunks = _chunk_by_sections(_doc(sections), MAX_CHUNK_CHARS, 10, always_split=True)
        assert len(chunks) == 1  # B가 "주의사항"으로 시작해 A에 강제 병합됨
        assert "일단 적용" in chunks[0].text
        assert "주의사항 문서" in chunks[0].text
