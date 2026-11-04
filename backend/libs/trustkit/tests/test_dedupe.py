"""중복 판정 2단(11/04) — 양식 줄은 데이터에서 세고(하드코딩 없음), 글자 유사도는 양식 줄을 빼고 잰다. 사례 + 성질(무작위) 테스트."""
import random

from trustkit.ingest.dedupe import norm_text, template_lines, text_similarity


def test_norm_text_only_whitespace():
    assert norm_text(" a\n\n b\t c ") == "a b c"
    assert norm_text("a b") != norm_text("a  b c")


def test_template_threshold_scales_with_page_count():
    # 3쪽에 나오는 줄: 작은 컬렉션(10쪽)에선 양식, 큰 컬렉션(1,000쪽 → 1% = 10쪽)에선 아님
    small = [(f"p{i}", "■ 담당: 운영팀\n고유 본문 %d" % i if i < 3 else "고유 %d" % i) for i in range(10)]
    big = small + [(f"q{i}", "다른 글 %d" % i) for i in range(990)]
    assert "■ 담당: 운영팀" in template_lines(small)
    assert "■ 담당: 운영팀" not in template_lines(big)


def test_same_page_counted_once():
    # 한 페이지 안 반복 줄은 양식이 아님(페이지 수로 센다)
    pages = [("p1", "반복 줄\n가"), ("p1", "반복 줄\n나"), ("p1", "반복 줄\n다"), ("p2", "다른")]
    assert template_lines(pages) == frozenset()


def test_sibling_pages_low_after_template_removed():
    form = "■ 대상: {}\n■ 기간: {}\n■ 담당: 마케팅팀\n■ 문의: 고객센터"
    a, b = form.format("신규 회원", "3/1~3/31"), form.format("VIP 회원", "5/1~5/15")
    pages = [(f"p{i}", form.format(f"대상{i}", f"기간{i}")) for i in range(10)] + [("pa", a), ("pb", b)]
    t = template_lines(pages)
    assert text_similarity(a, b) > text_similarity(a, b, t)   # 양식 줄을 빼면 차이가 드러난다
    assert text_similarity(a, b, t) < 0.8


def test_near_duplicate_stays_high():
    a = "## 배치 점검\n재기동 전 상태 확인\n담당 운영팀"
    b = "## 배치 점검\n재기동 전 상태 꼭 확인\n담당 운영팀"
    assert text_similarity(a, b) >= 0.8


def test_template_only_chunks_compare_original():
    t = frozenset({"■ 담당: 마케팅팀"})
    assert text_similarity("■ 담당: 마케팅팀", "■ 담당: 마케팅팀", t) == 1.0


def test_properties_random():
    """성질: 대칭 · 0~1 · 자기 자신 1 · 공백만 다른 글 1 · 양식 줄을 더해도 양식 뺀 유사도는 그대로."""
    rnd = random.Random(1104)
    words = ["결제", "오류", "배치", "재기동", "주문", "취소", "확인", "운영팀", "■", "대상:", "3/1"]
    for _ in range(300):
        a = "\n".join(" ".join(rnd.choices(words, k=rnd.randint(1, 6))) for _ in range(rnd.randint(1, 6)))
        b = "\n".join(" ".join(rnd.choices(words, k=rnd.randint(1, 6))) for _ in range(rnd.randint(1, 6)))
        t = frozenset({"■ 양식 줄"})
        s = text_similarity(a, b, t)
        assert 0.0 <= s <= 1.0 and abs(s - text_similarity(b, a, t)) < 1e-9
        assert text_similarity(a, a, t) == 1.0
        assert text_similarity(a, "  " + a.replace("\n", " \n "), t) == 1.0
        assert abs(text_similarity(a + "\n■ 양식 줄", b + "\n■ 양식 줄", t) - s) < 1e-9
