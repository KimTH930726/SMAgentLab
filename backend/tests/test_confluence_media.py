"""컨플 그림 처리(10/29) — draw.io 원본 → 글(결정적), 그림 자리 표시, 끄면 예전과 같음.
불변식: 끈 상태는 원문 그대로 · 같은 XML이면 같은 글 · 도형 이름이 하나도 안 빠짐(유실 0) · 원본에 없는 이름이 안 생김(생성 0)."""
import base64
import urllib.parse
import zlib

from bs4 import BeautifulSoup

from agents.knowledge_rag.ingestion import confluence_media as cm
from agents.knowledge_rag.ingestion import web_crawler as w

MODEL = ('<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
         '<mxCell id="a" value="주문 접수" vertex="1" parent="1"/>'
         '<mxCell id="b" value="&lt;b&gt;결제&lt;/b&gt; 승인" style="html=1;" vertex="1" parent="1"/>'
         '<object id="c" label="배차 요청"><mxCell vertex="1" parent="1"/></object>'
         '<mxCell id="e1" value="성공" edge="1" source="a" target="b" parent="1"/>'
         '<mxCell id="e2" edge="1" source="b" target="c" parent="1"/>'
         '<mxCell id="e3" value="실패 시 취소" edge="1" parent="1"/>'
         '</root></mxGraphModel>')


def _compressed(model: str) -> str:
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    raw = c.compress(urllib.parse.quote(model).encode()) + c.flush()
    return f'<mxfile><diagram id="d" name="p1">{base64.b64encode(raw).decode()}</diagram></mxfile>'


def test_diagram_text_names_and_flows_plain_and_compressed():
    plain = cm.diagram_text(f'<mxfile><diagram id="d" name="p1">{MODEL}</diagram></mxfile>')
    assert plain == cm.diagram_text(_compressed(MODEL)) == cm.diagram_text(MODEL)   # 저장 형식이 달라도 같은 글(결정적)
    assert plain.splitlines() == ["구성: 주문 접수 · 결제 승인 · 배차 요청 · 실패 시 취소",
                                  "흐름: 주문 접수 → 결제 승인 (성공)", "흐름: 결제 승인 → 배차 요청"]
    for name in ("주문 접수", "결제 승인", "배차 요청", "실패 시 취소"):   # 유실 0
        assert name in plain
    assert "<b>" not in plain                                         # 라벨 속 HTML은 글자만


def test_long_diagram_is_marked_cut():
    many = "".join(f'<mxCell id="v{i}" value="도형{i:04d}" vertex="1" parent="1"/>' for i in range(2000))
    t = cm.diagram_text(f"<mxGraphModel><root>{many}</root></mxGraphModel>")
    assert len(t) <= cm.DIAGRAM_TEXT_MAX + 30 and t.endswith("…(이하 원문 페이지에서 확인)")


STORAGE = ('<h2>주문 흐름</h2><p>설명</p>'
           '<ac:structured-macro ac:name="expand"><ac:rich-text-body>'
           '<ac:structured-macro ac:name="drawio" ac:schema-version="1"><ac:parameter ac:name="diagramName">주문도</ac:parameter>'
           '<ac:parameter ac:name="simple">false</ac:parameter></ac:structured-macro>'
           '</ac:rich-text-body></ac:structured-macro>'
           '<ac:image ac:alt="화면 예시"><ri:attachment ri:filename="screen.png"/></ac:image>'
           '<ac:image><ri:url ri:value="https://x/y.png"/></ac:image>'
           '<h2>다음</h2><p>끝</p>')


def _text(storage: str) -> str:
    return w._extract_text(BeautifulSoup(w.prepare_storage_html(storage), "lxml"))


def test_off_is_exactly_old_behavior():
    assert cm.apply_media(STORAGE, {"주문도": "구성: A"}, diagram_text_on=False, image_marks=False) == STORAGE


def test_diagram_inside_expand_and_image_marks_land_in_their_section():
    assert cm.find_diagrams(STORAGE) == ["주문도"]                      # 접기 안의 draw.io도 찾는다
    out = cm.apply_media(STORAGE, {"주문도": "구성: 주문 접수 · 결제\n흐름: 주문 접수 → 결제"}, diagram_text_on=True, image_marks=True)
    secs = w._extract_heading_sections(BeautifulSoup(w.prepare_storage_html(out), "lxml"))
    first = next(s for s in secs if s["title"] == "주문 흐름")["content"]
    assert "[다이어그램: 주문도]" in first and "흐름: 주문 접수 → 결제" in first
    assert "[이미지: 화면 예시 — 원문 페이지에서 확인]" in first and "[이미지: https://x/y.png — 원문 페이지에서 확인]" in first
    assert "false" not in _text(out).split("[다이어그램")[1].split("\n")[0]   # 매크로 파라미터 잔재 없음
    assert next(s for s in secs if s["title"] == "다음")["content"] == "끝"   # 다른 섹션은 그대로


def test_missing_diagram_source_is_visible_not_silent():
    out = cm.apply_media(STORAGE, {}, diagram_text_on=True, image_marks=True)
    assert "[다이어그램: 주문도 — 원문 페이지에서 확인]" in _text(out)
    only_text = cm.apply_media(STORAGE, {}, diagram_text_on=True, image_marks=False)
    assert "[이미지:" not in _text(only_text)


def test_ledger_rechunk_reproduces_media_without_network():
    """원장에 남긴 media로 다시 분할하면 미리보기와 같은 글(네트워크 없이), media 없는 옛 원장은 예전처럼(그림 버림)."""
    from agents.knowledge_rag.ingestion.source_ledger import rebuild_parsed
    media = {"diagram_text": True, "image_marks": True, "diagrams": {"주문도": "구성: X · Y"}}
    row = {"source_type": "confluence", "raw_content": STORAGE, "title": "t", "parent_title": None, "source_ref": "1",
           "source_version": 3, "media": media}
    d = rebuild_parsed(row)
    assert "구성: X · Y" in d.raw_text and "[이미지: 화면 예시" in d.raw_text and d.metadata["raw_html"] == STORAGE
    old = rebuild_parsed({**row, "media": None})
    assert "[다이어그램" not in old.raw_text and "[이미지" not in old.raw_text


def test_read_restriction_page_and_inherited_from_ancestor():
    """읽기 제한(10/29) — 페이지 자신·상위 페이지(물려받음) 어느 쪽이든 제한이면 적재하지 않는다. 제한 없음은 None."""
    lim = {"read": {"restrictions": {"user": {"results": [{"username": "a"}]}, "group": {"results": []}}}}
    free = {"read": {"restrictions": {"user": {"results": []}, "group": {"results": []}}}}
    assert w.read_restriction({"restrictions": lim, "ancestors": []}) == "page"
    assert w.read_restriction({"restrictions": free, "ancestors": [{"restrictions": free}, {"restrictions": lim}]}) == "ancestor"
    assert w.read_restriction({"restrictions": free, "ancestors": [{"restrictions": free}]}) is None
    # 제한 정보가 안 오면 모른다 = 넣지 않는다(리뷰 M2) — 페이지든 상위든
    assert w.read_restriction({}) == "unknown"
    assert w.read_restriction({"restrictions": free, "ancestors": [{"title": "상위"}]}) == "unknown"
    assert w.read_restriction({"restrictions": free, "ancestors": [{"title": "상위"}, {"restrictions": lim}]}) == "ancestor"


def test_ingest_llm_follows_setting_and_falls_back_visibly():
    """적재 LLM(10/29, ingest_llm) — local + 주소 있음 → 로컬, 주소 없음 → 시스템 LLM + 경고, system → 시스템 LLM.
    단위 테스트는 service.llm을 가짜로 바꿔 두고 돌아(conftest) 실제 모듈은 별도 프로세스에서 확인한다."""
    import subprocess
    import sys
    from pathlib import Path
    code = r"""
import logging, io
from core.config import settings
from service.llm import factory
buf = io.StringIO(); logging.getLogger("service.llm.factory").addHandler(logging.StreamHandler(buf))
settings.ingest_llm = "local"; settings.openai_compat_base_url = "http://local:8000/v1"; factory._ingest_local = None
llm, system = factory.get_ingest_llm(); assert type(llm).__name__ == "OpenAICompatProvider" and system is False
sentinel = object(); factory.get_llm_provider = lambda: sentinel
settings.openai_compat_base_url = ""
assert factory.get_ingest_llm() == (sentinel, True) and "시스템 LLM으로" in buf.getvalue()
settings.ingest_llm = "system"; settings.openai_compat_base_url = "http://local:8000/v1"
assert factory.get_ingest_llm() == (sentinel, True)
print("OK")
"""
    r = subprocess.run([sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parent.parent), capture_output=True, text=True, timeout=120)
    assert r.stdout.strip().endswith("OK"), r.stderr[-2000:]


def test_plain_labels_keep_angle_brackets_and_edge_text_boxes_join_their_edge():
    """순수 글 라벨의 "<"·">"는 지우지 않는다(리뷰 L5 — 유실 0), 연결선 위 글 상자(부모가 연결선)는 흐름 줄로(리뷰 L6)."""
    model = ('<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
             '<mxCell id="a" value="금액 &lt; 10000, &gt; 0" vertex="1" parent="1"/>'
             '<mxCell id="b" value="승인" vertex="1" parent="1"/>'
             '<mxCell id="e" edge="1" source="a" target="b" parent="1"/>'
             '<mxCell id="t" value="한도 안" vertex="1" parent="e"/>'
             '</root></mxGraphModel>')
    t = cm.diagram_text(model)
    assert "금액 < 10000, > 0" in t
    assert "흐름: 금액 < 10000, > 0 → 승인 (한도 안)" in t and "· 한도 안" not in t.splitlines()[0]


def test_self_closing_diagram_macro_does_not_swallow_following_content():
    st = ('<ac:structured-macro ac:name="expand"><ac:rich-text-body>'
          '<ac:structured-macro ac:name="drawio" ac:macro-id="x"/>'
          '<p>접기 안 본문</p></ac:rich-text-body></ac:structured-macro><p>다음 본문</p>')
    out = cm.apply_media(st, {}, diagram_text_on=True, image_marks=True)
    txt = _text(out)
    assert "접기 안 본문" in txt and "다음 본문" in txt   # 리뷰 L7 — 예전엔 바깥 닫는 태그까지 삼켜 본문이 사라졌다
