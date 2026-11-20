"""컨플 참조 자리 표시(11/19) — Jira 이슈 키·첨부 파일 이름이 파서를 지나도 남는다. 원장 media에 없는 옛 원문은 예전 그대로."""
import random

from bs4 import BeautifulSoup

from agents.knowledge_rag.ingestion import confluence_media as cm
from agents.knowledge_rag.ingestion import web_crawler as w
from agents.knowledge_rag.ingestion.source_ledger import rebuild_parsed


def text(storage: str, ref: bool = True) -> str:
    return w._extract_text(BeautifulSoup(w.prepare_storage_html(cm.apply_media(storage, None, diagram_text_on=False, image_marks=False,
                                                                                ref_marks=ref)), "lxml"))


def M(name, inner=""):
    return f'<ac:structured-macro ac:name="{name}" ac:schema-version="1">{inner}</ac:structured-macro>'


def P(k, v):
    return f'<ac:parameter ac:name="{k}">{v}</ac:parameter>'


def test_jira_key_inline_keeps_sentence():
    t = text("<p>배포는 " + M("jira", P("server", "JIRA") + P("key", "OPS-1234")) + " 에서 진행</p>")
    assert "[Jira: OPS-1234]" in t and "배포는" in t and "에서 진행" in t


def test_jira_query_marked():
    assert "[Jira 목록: project = OPS — 원문 페이지에서 확인]" in text("<p>a</p>" + M("jira", P("jqlQuery", "project = OPS")))


def test_view_file_marked_with_name():
    t = text("<p>앞</p>" + M("view-file", '<ac:parameter ac:name="name"><ri:attachment ri:filename="정산 매뉴얼.xlsx" /></ac:parameter>'))
    assert "[첨부: 정산 매뉴얼.xlsx — 원문 페이지에서 확인]" in t


def test_attachment_link_keeps_body_and_name():
    t = text('<p>자세한 건 <ac:link><ri:attachment ri:filename="운영가이드.pdf" /><ac:plain-text-link-body><![CDATA[운영 가이드]]>'
             "</ac:plain-text-link-body></ac:link> 참고</p>")
    assert "운영 가이드 [첨부: 운영가이드.pdf]" in t and "참고" in t


def test_page_link_untouched():
    # 첨부가 아닌 페이지 링크는 그대로(본문 글만)
    t = text('<p>관련 <ac:link><ri:page ri:content-title="배포 절차" /><ac:link-body>배포 절차</ac:link-body></ac:link> 확인</p>')
    assert "[첨부" not in t


def test_self_closing_jira_inside_expand_keeps_outer_body():
    t = text(M("expand", P("title", "펼침") + "<ac:rich-text-body><p>위</p>" + '<ac:structured-macro ac:name="jira" />'
               + "<p>아래본문</p></ac:rich-text-body>"))
    assert "아래본문" in t and "[Jira" in t


def test_off_means_old_behaviour():
    s = "<p>a</p>" + M("jira", P("key", "OPS-1"))
    assert cm.apply_media(s, None, diagram_text_on=False, image_marks=False) == s
    assert "OPS-1" not in text(s, ref=False)


def test_rebuild_follows_ledger_flag():
    raw = "<p>본문</p>" + M("view-file", '<ac:parameter ac:name="name"><ri:attachment ri:filename="a.pdf" /></ac:parameter>')
    row = {"source_type": "confluence", "raw_content": raw, "title": "t", "parent_title": None, "source_ref": "1", "source_version": 1}
    assert "[첨부: a.pdf" not in rebuild_parsed({**row, "media": {"diagram_text": True}}).raw_text      # 옛 원장 — 다시 분할이 바뀌지 않게
    assert "[첨부: a.pdf" in rebuild_parsed({**row, "media": {"diagram_text": True, "ref_marks": True}}).raw_text


def test_no_text_outside_macros_lost_random():
    # 불변식: 자리 표시를 켜도 매크로 밖 문장은 하나도 사라지지 않는다(무작위 배치)
    rng = random.Random(7)
    parts = [lambda i: f"<p>문장{i}</p>",
             lambda i: M("jira", P("key", f"K-{i}")),
             lambda i: M("view-file", f'<ac:parameter ac:name="name"><ri:attachment ri:filename="f{i}.xlsx" /></ac:parameter>'),
             lambda i: f'<p>링크앞{i} <ac:link><ri:attachment ri:filename="g{i}.pdf" /></ac:link> 링크뒤{i}</p>',
             lambda i: M("info", f"<ac:rich-text-body><p>정보{i}</p></ac:rich-text-body>")]
    for _ in range(200):
        chosen = [(rng.randrange(len(parts)), i) for i in range(rng.randint(1, 8))]
        s = "".join(parts[k](i) for k, i in chosen)
        before, after = text(s, ref=False), text(s, ref=True)
        for tok in [f"문장{i}" for k, i in chosen if k == 0] + [f"정보{i}" for k, i in chosen if k == 4] + \
                   [x for k, i in chosen if k == 3 for x in (f"링크앞{i}", f"링크뒤{i}")]:
            assert tok in before and tok in after
        for k, i in chosen:
            if k == 1:
                assert f"[Jira: K-{i}]" in after
            if k == 2:
                assert f"[첨부: f{i}.xlsx" in after
            if k == 3:
                assert f"[첨부: g{i}.pdf]" in after


def test_no_word_lost_inline_random():
    # 리뷰 H1(11/19) — 문단 안 매크로(연달아·상태 표시 뒤·JQL·첨부 보기·스스로 닫힌 링크·레이아웃 칸·표 칸)에서도 끈 글의 단어가 켠 글에 다 남는다.
    # 앞 테스트는 매크로를 문단 밖에만 둬서 "문단 안 <p>가 바깥 문단을 닫아 뒷글 유실"을 못 잡았다.
    rng = random.Random(11)
    inl = [lambda i: M("jira", P("key", f"K-{i}")),
           lambda i: M("jira", P("jqlQuery", f"project = Q{i}")),
           lambda i: M("status", P("title", f"상태{i}")),
           lambda i: M("view-file", f'<ac:parameter ac:name="name"><ri:attachment ri:filename="v{i}.xlsx" /></ac:parameter>'),
           lambda i: f'<ac:link><ri:attachment ri:filename="g{i}.pdf" /></ac:link>',
           lambda i: '<ac:link ac:anchor="top"/>']
    wraps = [lambda x: f"<p>{x}</p>", lambda x: f"<ul><li>{x}</li></ul>", lambda x: f"<table><tbody><tr><td>{x}</td></tr></tbody></table>",
             lambda x: f"<ac:layout><ac:layout-section><ac:layout-cell><p>{x}</p></ac:layout-cell></ac:layout-section></ac:layout>",
             lambda x: f"<h2>{x}</h2>"]
    for n in range(300):
        words, parts = [], []
        for j in range(rng.randint(1, 6)):
            w = f"낱말{n}x{j}"
            words.append(w)
            parts.append(w)
            for _ in range(rng.randint(0, 2)):
                parts.append(rng.choice(inl)(j))
        doc = rng.choice(wraps)(" ".join(parts)) + "<p>끝문장</p>"
        before, after = text(doc, ref=False), text(doc, ref=True)
        for w in words + ["끝문장"]:
            assert (w in before) <= (w in after), (doc, before, after)


def test_code_block_tag_text_does_not_change_depth():
    # 리뷰 L-a — 코드 블록 CDATA 안 "<p> <li> <td>" 글자가 열린 칸으로 세져 뒤 블록 자리 Jira가 사라지던 것
    code = M("code", "<ac:plain-text-body><![CDATA[<p>예시 <li> <td>]]></ac:plain-text-body>")
    t = text(code + M("jira", P("key", "Z-2")) + "<!-- <p> -->" + "<p>끝</p>")
    assert "[Jira: Z-2]" in t and "끝" in t


def test_has_ref_marks_false_means_identical_output():
    # 다시 파싱이 "판만 올리기"로 건너뛰는 근거(11/19) — 바꿀 곳이 없으면 켜도 결과가 똑같다(무작위)
    rng = random.Random(5)
    bits = ["<p>문장</p>", M("info", "<ac:rich-text-body><p>정보</p></ac:rich-text-body>"), M("status", P("title", "완료")),
            '<p><ac:link><ri:page ri:content-title="다른" /></ac:link></p>', '<ac:image><ri:attachment ri:filename="a.png" /></ac:image>',
            M("jira", P("key", "X-1")), M("view-file", '<ac:parameter ac:name="name"><ri:attachment ri:filename="f.pdf" /></ac:parameter>'),
            '<p><ac:link><ri:attachment ri:filename="g.pdf" /></ac:link></p>']
    for _ in range(300):
        s = "".join(rng.choice(bits) for _ in range(rng.randint(1, 6)))
        if not cm.has_ref_marks(s):
            assert text(s, ref=True) == text(s, ref=False)
        else:
            assert text(s, ref=True) != text(s, ref=False)
