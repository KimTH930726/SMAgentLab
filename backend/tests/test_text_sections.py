"""공통 제목 인식 + 붙여넣기 분할 (2026-10-07).

붙여넣기 "섹션 기준"이 조용히 단락 분할로 바뀌고 상위 맥락(heading_path)이 비던 결함, PDF 번호 패턴이 "3.5kg"·날짜를 제목으로
잘라 본문을 바꾸던 결함, md 코드 블록 안 "# 주석"을 제목으로 보던 결함 — 경로마다 따로 있던 인식기를 하나로.
"""
from agents.knowledge_rag.ingestion.adapters import extract_heading_sections, heading_of, parse_markdown, parse_text
from agents.knowledge_rag.knowledge.service import split_text_to_items


class TestHeadingOf:
    def test_markdown_and_outline_headings(self):
        assert heading_of("## 배송") == (2, "배송")
        assert heading_of("1.2.1.2. 매장 개점 관리") == (4, "1.2.1.2. 매장 개점 관리")
        assert heading_of("1-1 개요") == (2, "1-1 개요")
        assert heading_of("3.2) 예외") == (2, "3.2) 예외")

    def test_not_headings(self):
        for line in ["1. 대분류 적용 가능",          # 한 단계 번호 = 목록 항목
                     "3.5kg 이하만 무료",             # 숫자 뒤 바로 글자
                     "2026-10-07 배포",               # 날짜(4자리 단계)
                     "1.5 이상이면 할인한다.",        # 문장
                     "1.2 버전부터 지원됩니다",
                     "1.2 버전부터 지원돼요.",
                     "#해시태그",                     # # 뒤 공백 없음
                     "1.2.3. " + "가" * 120]:          # 너무 김
            assert heading_of(line) is None, line


class TestExtractSections:
    def test_levels_and_preamble(self):
        secs = extract_heading_sections("서두\n# 1장\n본문a\n## 1.1 절\n본문b\n1.1.1. 항\n본문c")
        assert [(s["level"], s["title"]) for s in secs] == [(0, ""), (1, "1장"), (2, "1.1 절"), (3, "1.1.1. 항")]
        assert secs[0]["content"] == "서두"

    def test_code_fence_and_pdf_page_marker(self):
        secs = extract_heading_sections("# 설정\n```\n# 주석\nselect 1;\n```\n--- Page 2 ---\n끝")
        assert [s["title"] for s in secs] == ["설정"]
        assert "# 주석" in secs[0]["content"] and "Page 2" not in secs[0]["content"]

    def test_markdown_parser_uses_it(self):
        doc = parse_markdown("# 제목\n```\n# 주석\n```\n# 둘째\n본문", "t.md")
        assert [s["title"] for s in doc.sections] == ["제목", "둘째"]

    def test_txt_with_outline_gets_sections(self):
        assert len(parse_text("1.1 가\n본문\n1.2 나\n본문", "t.txt").sections) == 2
        assert parse_text("그냥 문단\n\n또 문단", "t.txt").sections == []


class TestPasteSplit:
    TEXT = ("1.2.1. 매장\n"
            "1.2.1.1. 매장 관리\n매장 등록·수정·삭제는 관리자 화면에서 한다.\n"
            "1.2.1.2. 매장 개점 관리\n매장 OPEN을 전송해 개점 처리한다. 매일 06:00 배치가 처리한다.\n"
            "1.2.2. 메뉴\n"
            "1.2.2.1. 메뉴 등록\n메뉴는 관리자 화면에서 등록한다.")

    def test_section_strategy_really_splits_by_section_with_heading_path(self):
        items = split_text_to_items(self.TEXT, "section")
        texts = [i["content"] for i in items]
        assert len(items) == 3
        assert texts[0].startswith("## 1.2.1. 매장") and "1.2.1.1. 매장 관리" in texts[0]
        assert items[1]["heading_path"] == ["1.2.1. 매장"]
        assert items[2]["content"].startswith("## 1.2.2. 메뉴")
        for key in ("관리자 화면에서 한다", "개점 처리한다", "메뉴는 관리자"):
            assert sum(key in t for t in texts) == 1

    def test_auto_same_as_section_when_headings_found(self):
        assert split_text_to_items(self.TEXT, "auto") == split_text_to_items(self.TEXT, "section")

    def test_no_headings_falls_back_to_old_rules(self):
        items = split_text_to_items("문단 하나\n\n문단 둘", "auto")
        assert [i["content"] for i in items] == ["문단 하나", "문단 둘"]
        assert all(i["heading_path"] == [] for i in items)


class TestReviewFalsePositives:
    """리뷰 2026-10-07: 번호로 시작하는 본문이 제목으로 잡히던 경우(IP·콜론으로 끝나는 문장)."""

    def test_more_non_headings(self):
        for line in ["192.168.0.1 서버에 접속", "1.2 처리 결과는 다음과 같다:", "1.2 처리 결과:"]:
            assert heading_of(line) is None, line
