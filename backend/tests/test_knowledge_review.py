"""검토 기한(11/04) — 원문 페이지 묶음·기한 계산·정렬. 순수 함수(DB 없음)."""
import random
from datetime import datetime, timedelta, timezone

from agents.knowledge_rag.knowledge import review

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)


def row(i, *, page=None, doc=None, job=None, file=None, days_ago=200, reviewed_ago=None, edited_ago=None, owner=None):
    return {"id": i, "confluence_page_id": page, "source_document_id": doc, "ingestion_job_id": job, "source_file": file,
            "title": file, "created_at": NOW - timedelta(days=days_ago), "owner": owner,
            "reviewed_at": NOW - timedelta(days=reviewed_ago) if reviewed_ago is not None else None,
            "content_edited_at": NOW - timedelta(days=edited_ago) if edited_ago is not None else None}


def test_interval_off_means_nothing_due():
    assert review.due_docs([row(1)], None, NOW, {}) == []


def test_page_due_by_oldest_chunk_and_review_resets():
    rs = [row(1, page="P", file="환불", reviewed_ago=10), row(2, page="P", file="환불", days_ago=100)]   # 한 조각이 기한 지남
    [d] = review.due_docs(rs, 90, NOW, {})
    assert (d["ids"], d["n"], d["overdue_days"]) == ([1, 2], 2, 10)
    rs[1]["reviewed_at"] = NOW - timedelta(days=1)   # 페이지 전체 검토 완료
    assert review.due_docs(rs, 90, NOW, {}) == []


def test_edit_after_review_counts_as_review():
    """검토일이 있어도 그 뒤에 고쳤으면 고친 날이 마지막 검토(리뷰 M2)"""
    assert review.due_docs([row(1, reviewed_ago=100, edited_ago=5)], 90, NOW, {}) == []
    [d] = review.due_docs([row(1, reviewed_ago=100, edited_ago=95)], 90, NOW, {})
    assert d["overdue_days"] == 5


def test_last_review_falls_back_to_edit_then_created():
    assert review.due_docs([row(1, days_ago=200, edited_ago=10)], 90, NOW, {}) == []   # 고친 지 10일
    [d] = review.due_docs([row(1, days_ago=91)], 90, NOW, {})
    assert d["last_reviewed_at"] is None and d["overdue_days"] == 1


def test_manual_chunks_not_lumped_together():
    rs = [row(1), row(2), row(3, job=7, file="a.txt"), row(4, job=7, file="a.txt"), row(5, doc=9), row(6, doc=9)]
    keys = sorted(d["key"] for d in review.due_docs(rs, 90, NOW, {}))
    assert keys == ["d:9", "f:7:a.txt", "k:1", "k:2"]


def test_sorted_by_usage_then_overdue():
    rs = [row(1, page="A", days_ago=300), row(2, page="B", days_ago=100), row(3, page="C", days_ago=200)]
    out = review.due_docs(rs, 90, NOW, {2: 5, 3: 5})
    assert [d["key"] for d in out] == ["p:C", "p:B", "p:A"]   # 쓰인 횟수 같으면 오래 지난 것 먼저
    assert [d["used"] for d in out] == [5, 5, 0]


def test_source_changed_first_even_if_not_overdue():
    """원문 바뀜(동기화 충돌) = 기한과 상관없이 맨 위, 검토 주기 '안 함'이어도 보인다"""
    rs = [row(1, page="A", days_ago=200), row(2, page="B", reviewed_ago=1), row(3, page="C", days_ago=300)]
    out = review.due_docs(rs, 90, NOW, {1: 9, 3: 9}, changed_pages=["B"])
    assert [d["key"] for d in out] == ["p:B", "p:C", "p:A"]
    assert out[0]["source_changed"] is True and out[0]["overdue_days"] is None
    off = review.due_docs(rs, None, NOW, {}, changed_pages=["B"])
    assert [d["key"] for d in off] == ["p:B"]


def test_freshness_same_rule_as_due_list():
    """근거 카드 최신성 = 검토 필요 목록과 같은 페이지 묶음·같은 규칙(두 화면이 다른 말을 하지 않게)"""
    rs = [dict(row(1, page="P", reviewed_ago=5), namespace_id=1), dict(row(2, page="P", days_ago=120), namespace_id=1),
          dict(row(3, days_ago=10), namespace_id=1), dict(row(4, page="P", days_ago=300), namespace_id=2)]   # 다른 컬렉션의 같은 페이지 번호는 따로
    f = review.freshness(rs, [1, 3, 4, 99], {1: 90, 2: None}, {1: ["딜리버스파트"]}, NOW)
    assert set(f) == {1, 3, 4}
    assert f[1]["overdue"] is True and f[1]["owners"] == ["딜리버스파트"]   # 2번 조각 때문에 페이지가 기한 지남
    assert f[1]["reviewed"] is False   # 마지막 검토를 정한 건 검토 안 된 2번 조각(등록일) — "검토 날짜"로 보이면 안 된다(리뷰 L5)
    assert f[3]["overdue"] is False
    assert f[4]["overdue"] is False and f[4]["interval_days"] is None and f[4]["owners"] == []   # 검토 안 함 컬렉션
    due = {d["key"] for d in review.due_docs([r for r in rs if r["namespace_id"] == 1], 90, NOW, {})}
    assert ("p:P" in due) == f[1]["overdue"] and ("k:3" in due) == f[3]["overdue"]


def test_invariant_every_chunk_once_random():
    """무작위 — 기한 지난 페이지들의 조각은 겹치지 않고, 기한 안 지난 조각은 없다(유실·중복 0)."""
    rnd = random.Random(1104)
    for _ in range(200):
        rs = []
        for i in range(rnd.randint(1, 30)):
            k = rnd.choice(["page", "doc", "file", "none"])
            rs.append(row(i, page=f"P{rnd.randint(1, 4)}" if k == "page" else None, doc=rnd.randint(1, 3) if k == "doc" else None,
                          job=rnd.choice([None, 1]), file=f"f{rnd.randint(1, 2)}" if k == "file" else None,
                          days_ago=rnd.randint(0, 300), reviewed_ago=rnd.choice([None, rnd.randint(0, 300)])))
        out = review.due_docs(rs, 90, NOW, {})
        ids = [i for d in out for i in d["ids"]]
        assert len(ids) == len(set(ids))
        by_key = {}
        for r in rs:
            by_key.setdefault(review.doc_key(r), []).append(r)
        want = {k for k, g in by_key.items()
                if min(review.last_touch(r) for r in g) + timedelta(days=90) < NOW}
        assert {d["key"] for d in out} == want
        for d in out:
            assert sorted(r["id"] for r in by_key[d["key"]]) == d["ids"]
