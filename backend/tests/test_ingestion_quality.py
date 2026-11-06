"""등록 묶음 품질 검사 (2026-10-07) — 지표로 안 보이던 청크 경계 결함을 job마다 잡는다."""
from agents.knowledge_rag.ingestion.quality import assess


def test_clean_job_has_no_warnings():
    q = assess([("## 1. 가\n본문 하나입니다 길게", ["상위"]), ("## 2. 나\n본문 둘입니다 길게", ["상위"])])
    assert q["warnings"] == [] and q["with_heading_path"] == 2


def test_real_defect_shapes_are_flagged():
    """9/22 컨플루언스 일괄 등록의 실제 모양: 폐점 섹션이 두 청크에, 다음 상위 제목이 앞 청크 끝에."""
    closing = "## 1.2.1.2. 매장 폐점 관리\n영업 시간이 끝난 매장을 데몬이 폐점 처리한다"
    q = assess([(closing, []), (closing + "\n## 1.2.2. 메뉴", []), ("## 1.2.2. 메뉴", [])])
    assert q["adjacent_overlap"] == 1
    assert q["trailing_heading"] == 1
    assert q["heading_only"] == 1
    assert len(q["warnings"]) == 3


def test_repeated_parent_heading_alone_is_not_overlap():
    """상위 제목 줄만 반복되는 건(하위 청크 맥락) 중복이 아니다."""
    q = assess([("## 1. 상위\n## 1.1 가\n가 본문입니다 길게", []), ("## 1. 상위\n## 1.2 나\n나 본문입니다 길게", [])])
    assert q["adjacent_overlap"] == 0


# ── 원문 대조(11/05) — 원문이 원래 반복하는 줄은 결함이 아니다 ──────────────────

ROW = "| 문구 노출 확인 | iOS | 정상 |"


def test_source_repeat_is_not_defect():
    """체크리스트 표처럼 원문에 같은 행이 두 번 → 이웃 조각에 하나씩 있어도 결함 아님(원문 반복으로 셈)."""
    src = f"## 가\n{ROW}\n## 나\n{ROW}"
    q = assess([(f"## 가\n{ROW}", []), (f"## 나\n{ROW}", [])], source_text=src)
    assert (q["adjacent_overlap"], q["adjacent_source_repeat"], q["source_checked"], q["warnings"]) == (0, 1, True, [])


def test_split_copy_is_defect():
    """원문엔 한 번인데 나누면서 두 조각에 들어감 → 결함(원문 1번 · 조각 2번으로 기록)."""
    src = f"## 가\n{ROW}\n다음 문장입니다 길게"
    q = assess([(f"## 가\n{ROW}", []), (f"{ROW}\n다음 문장입니다 길게", [])], source_text=src)
    assert q["adjacent_overlap"] == 1 and q["adjacent_source_repeat"] == 0
    (line,) = q["overlap_pairs"][0]["lines"]
    assert (line["extra"], line["source"], line["chunks"], line["t"]) == (True, 1, 2, "table")


def test_no_source_keeps_old_rule_and_says_so():
    """원문이 없으면 예전 기준(이웃에 같은 줄 = 결함) — source_checked=False로 대체를 보이게."""
    q = assess([(f"## 가\n{ROW}", []), (f"## 나\n{ROW}", [])])
    assert (q["adjacent_overlap"], q["source_checked"]) == (1, False)
    assert q["overlap_pairs"][0]["lines"][0]["source"] is None


def test_record_keeps_no_body_text():
    """품질 기록엔 본문이 없다(줄 해시만) — 화면은 조각에서 해시로 다시 찾는다."""
    import json
    q = assess([(f"## 가\n{ROW}", []), (f"## 나\n{ROW}", [])], source_text=ROW)
    assert "문구 노출" not in json.dumps(q, ensure_ascii=False)


def test_counts_partition_pairs():
    """불변식: 겹친 쌍 = 결함 + 원문 반복(무작위 조각 배열로) — 한 쌍이 두 번 세지거나 빠지지 않는다."""
    import random
    rnd = random.Random(7)
    pool = [f"| 항목 {i} | 값 {i % 3} | 비고 |" for i in range(6)]
    for _ in range(200):
        chunks = ["\n".join(rnd.sample(pool, rnd.randint(1, 3))) for _ in range(rnd.randint(2, 6))]
        src = "\n".join(rnd.choice(pool) for _ in range(rnd.randint(3, 12)))
        q = assess([(c, []) for c in chunks], source_text=src)
        overlapping = sum(1 for a, b in zip(chunks, chunks[1:]) if set(a.split("\n")) & set(b.split("\n")))
        assert q["adjacent_overlap"] + q["adjacent_source_repeat"] == overlapping == len(q["overlap_pairs"])


def test_defect_pairs_kept_first_when_truncated():
    """쌍 목록이 상한을 넘으면 결함 쌍부터 남기고 잘림을 표시 — 앞 쌍이 전부 원문 반복이어도 뒤 결함이 화면에서 사라지지 않는다(리뷰 4)."""
    rep, dup = "| 반복 행 | 원문에 많음 |", "| 한 번만 | 원문에 한 번 |"
    chunks = [(rep, []) for _ in range(6)] + [(dup, []), (dup, [])]
    q = assess(chunks, source_text="\n".join([rep] * 6 + [dup]), max_pairs=2)
    assert q["overlap_pairs_truncated"] is True and len(q["overlap_pairs"]) == 2
    assert [p["defect"] for p in q["overlap_pairs"]].count(True) == 1 and q["adjacent_overlap"] == 1
