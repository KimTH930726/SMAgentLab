from trustkit.answer.citation import CITE_RULE_KO, strip_uncited


def test_keeps_cited_and_frame_lines_drops_uncited():
    a = "다음과 같습니다:\n- 정산은 02시에 돈다 [#12]\n- 담당은 운영 파트다\n- 재시도는 1회 [#12, #13]"
    r = strip_uncited(a)
    assert r.status == "stripped" and r.removed == 1
    assert "담당은" not in r.answer and "[#12, #13]" in r.answer and r.answer.startswith("다음과 같습니다:")


def test_all_cited_is_kept_as_is():
    a = "## 요약\n정산은 02시 [문서 #3]\n\n재시도 1회 [#3]"
    r = strip_uncited(a)
    assert r.status == "kept" and r.removed == 0 and r.answer == a


def test_no_valid_marker_means_format_not_followed_and_answer_untouched():
    a = "정산은 02시\n근거 [#]\n잘못된 [#1x]"
    r = strip_uncited(a)
    assert r.status == "no_citations" and r.answer == a and r.removed == 0


def test_unverified_line_kept():
    r = strip_uncited("정산은 02시 [#1]\n보관 기간은 확인 안 됨\n근거 없는 덧붙임")
    assert r.status == "stripped" and r.removed == 1 and "확인 안 됨" in r.answer and "덧붙임" not in r.answer


def test_no_line_lost_or_invented():
    """불변식 — 남은 줄은 모두 원래 답의 줄(새 내용 0), 원래 본문 줄 수 = 남은 본문 줄 + 뺀 줄(조용한 유실 0)"""
    a = "머리:\n가 [#1]\n나\n\n다 [#2]\n라\n---\n마 확인 안 됨"
    r = strip_uncited(a)
    orig = a.split("\n")
    out = r.answer.split("\n")
    assert all(ln in orig for ln in out)
    nonempty = lambda xs: [x for x in xs if x.strip()]   # noqa: E731
    assert len(nonempty(orig)) == len(nonempty(out)) + r.removed


def test_unnumbered_block_markers_and_loose_list_count_as_cited():
    r = strip_uncited("포인트는 1만 원까지 [정책]\n코드는 A01 [공통코드]\n표 기준 [참고표]\n둘 다 [#12, 13]\n근거 없음")
    assert r.status == "stripped" and r.removed == 1 and "근거 없음" not in r.answer


def test_rule_text_mentions_format():
    assert "[#번호]" in CITE_RULE_KO and "확인 안 됨" in CITE_RULE_KO
