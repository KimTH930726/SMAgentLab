"""동기화로 바뀐 페이지(11/05) — 원문 html → 비교 줄 · 바뀐 줄 수. 순수 함수(DB 없음).
불변식: 같은 글이면 0/0 · 공백·태그만 다르면 0/0 · 더한 줄 수 − 뺀 줄 수 = 새 줄 수 − 옛 줄 수."""
import random

from agents.knowledge_rag.ingestion.sync_changes import diff_counts, text_lines


def test_html_lines_ignore_tags_and_spaces():
    a = text_lines("<p>배송  지연</p><ul><li>쿠폰 재발급</li></ul>", "html")
    b = text_lines("<p>배송 지연 </p>\n<ul><li><b>쿠폰</b> 재발급</li></ul>", "html")
    assert a == b == ["배송 지연", "쿠폰 재발급"]
    assert diff_counts(a, b) == (0, 0)


def test_counts_edit_add_remove():
    old = ["가", "나", "다"]
    assert diff_counts(old, ["가", "나 고침", "다", "라"]) == (2, 1)   # 고친 줄 = 1 빼고 1 더함 + 새 줄 1
    assert diff_counts(old, []) == (0, 3) and diff_counts([], old) == (3, 0)


def test_count_balance_invariant_random():
    rnd = random.Random(1105)
    for _ in range(200):
        old = [f"줄{rnd.randint(0, 9)}" for _ in range(rnd.randint(0, 12))]
        new = [x for x in old if rnd.random() > 0.3] + [f"새{rnd.randint(0, 9)}" for _ in range(rnd.randint(0, 4))]
        a, d = diff_counts(old, new)
        assert a - d == len(new) - len(old) and 0 <= d <= len(old) and 0 <= a <= len(new)
