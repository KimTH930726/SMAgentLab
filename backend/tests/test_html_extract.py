"""컨플루언스/웹 HTML 추출 (2026-10-07 적재 파이프라인 감사) — 내용 유실 회귀 방지.

예전: 코드 매크로 SQL(CDATA)이 섹션·raw_text 양쪽에서 사라짐, h5/h6 제목 유실, 표가 칸마다 한 줄로 풀림, dt/dd·div 직속 글자 누락,
raw_text 순서 깨짐. 불변식: 입력에 있는 모든 글자 덩어리가 섹션 어딘가에 정확히 한 번, 문서 순서대로.
"""
from bs4 import BeautifulSoup

from agents.knowledge_rag.ingestion import web_crawler as w

HTML = ('<p>서두</p><h2>설정</h2><p>아래 쿼리로 확인</p>'
        '<ac:structured-macro ac:name="code"><ac:parameter ac:name="language">sql</ac:parameter>'
        '<ac:plain-text-body><![CDATA[SELECT *\nFROM ord WHERE st<1]]></ac:plain-text-body></ac:structured-macro>'
        '<h5>세부</h5><p>세부 본문</p>'
        '<table><tr><th>코드</th><th>의미</th></tr><tr><td><p>A</p></td><td>승인</td></tr></table>'
        '<dl><dt>용어</dt><dd>뜻</dd></dl><div>div 직속 글자<p>안쪽 p</p></div><ul><li>바깥<ul><li>안쪽</li></ul></li></ul>')


def _soup():
    return BeautifulSoup(w.prepare_storage_html(HTML), "lxml")


def test_sections_keep_everything_once_in_order():
    secs = w._extract_heading_sections(_soup())
    assert [(s["level"], s["title"]) for s in secs] == [(0, ""), (2, "설정"), (5, "세부")]
    allc = "\n".join(s["content"] for s in secs)
    for piece in ["서두", "아래 쿼리로 확인", "SELECT *\nFROM ord WHERE st<1", "세부 본문", "| 코드 | 의미 |", "| A | 승인 |",
                  "용어", "뜻", "div 직속 글자", "안쪽 p", "바깥"]:
        assert allc.count(piece) == 1, piece
    assert "```" in secs[1]["content"]                     # 코드는 코드 블록으로
    assert allc.index("세부 본문") < allc.index("| 코드 | 의미 |") < allc.index("div 직속 글자")


def test_raw_text_same_order_as_sections():
    raw = w._extract_text(_soup())
    assert raw.index("## 설정") < raw.index("SELECT") < raw.index("## 세부") < raw.index("| A | 승인 |")


def test_macro_parameters_not_leaked_as_text():
    assert "language" not in w._extract_text(_soup()) and "sql\n" not in w._extract_text(_soup())


class TestReviewEdgeCases:
    """코드 리뷰(2026-10-07) 경계 사례."""

    def _secs(self, html):
        return w._extract_heading_sections(BeautifulSoup(w.prepare_storage_html(html), "lxml"))

    def test_layout_table_with_headings_keeps_sections(self):
        secs = self._secs("<table><tr><td><h2>섹션A</h2><p>내용A</p></td></tr><tr><td><h2>섹션B</h2><p>내용B</p></td></tr></table>")
        assert [s["title"] for s in secs] == ["섹션A", "섹션B"]
        assert secs[0]["content"] == "내용A" and secs[1]["content"] == "내용B"

    def test_div_direct_text_keeps_order(self):
        secs = self._secs("<h2>T</h2><div><p>첫째</p>둘째<p>셋째</p></div>")
        assert secs[0]["content"].split("\n") == ["첫째", "둘째", "셋째"]

    def test_cell_newline_and_pipe_do_not_break_row(self):
        secs = self._secs("<h2>T</h2><table><tr><td><pre>a\nb</pre></td><td>x|y</td></tr></table>")
        assert secs[0]["content"] == "| a b | x\\|y |"
