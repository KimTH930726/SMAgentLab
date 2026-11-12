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


# ── 숫자·시각 근거 대조(check_numbers, 11/11 시연 리뷰) ──
from trustkit.answer.citation import check_numbers  # noqa: E402

DOCS = {65251: "조정 검토 시 AWS 작업은 03:30 이전에 반드시 종료되어야 함. 재시도 3회", 20606: "2024년 4월 18일 00:00~04:20 작업 계획",
        12: "정산 배치는 매일 2시 30분에 돈다"}


def test_demo_case_flags_number_not_in_cited_doc():
    """시연 장면 7 실례 — 03:30은 #65251에만 있는데 #20606을 근거로 단 추론 문장."""
    a = ("- 제약 조건: AWS 작업은 03:30 이전에 반드시 종료 [#65251]\n"
         "참고로, 2024년 4월 계획도 03:30 이전 종료 조건을 따르는 것이 적절합니다 [#20606]")
    r = check_numbers(a, DOCS)
    assert (r.checked, r.unsupported, r.unknown_cite) == (2, 1, 0) and r.lines == [(1, 1)]


def test_equivalent_time_forms_and_multi_cites_are_supported():
    a = "정산은 02:30에 돈다 [#12]\n종료는 3시 30분 이전, 재시도 3회 [#12, #65251]"
    assert check_numbers(a, DOCS).unsupported == 0


def test_uncited_or_numberless_lines_are_not_checked_and_marker_digits_ignored():
    a = "정산은 9시에 돈다\n담당은 운영 파트 [#12]\n## 1. 개요"
    r = check_numbers(a, DOCS)
    assert r.checked == 0 and r.unsupported == 0


def test_unknown_cite_number_is_counted():
    r = check_numbers("재시도 3회 [#999]", DOCS)
    assert (r.checked, r.unsupported, r.unknown_cite) == (1, 1, 1)


def test_property_every_flagged_line_has_a_value_missing_from_its_cited_docs():
    """성질 — 무작위 문장에서 표시된 줄은 정말 인용 본문에 없는 값을 가진다, 표시 안 된 줄의 값은 전부 인용 본문에 있다."""
    import random
    from trustkit.answer.citation import _amounts, _cited, _times
    rnd = random.Random(7)
    vals = ["03:30", "04:00", "2시 30분", "3회", "5회", "10일", "90일", "15%"]
    for _ in range(500):
        lines = [f"값 {rnd.choice(vals)} [#{rnd.choice([12, 65251, 20606])}]" for _ in range(rnd.randint(1, 4))]
        r = check_numbers("\n".join(lines), DOCS)
        flagged = {i for i, _ in r.lines}
        for i, ln in enumerate(lines):
            src = "\n".join(DOCS[x] for x in _cited(ln))
            body = ln.split("[")[0]
            missing = bool(_times(body) - _times(src) or _amounts(body) - _amounts(src))
            assert (i in flagged) == missing


def test_duplicate_cite_does_not_hide_unknown_number():
    r = check_numbers("재시도 3회 [#12, #12, #999]", DOCS)
    assert r.unknown_cite == 1
