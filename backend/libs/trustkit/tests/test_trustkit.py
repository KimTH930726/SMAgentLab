"""trustkit 단독 테스트 — 도커·DB 없이 `python -m pytest libs/trustkit/tests`. 사례보다 성질(유실·중복·순서·경계)로."""
import ast
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trustkit.answer.shaping import list_scoped  # noqa: E402
from trustkit.eval.gate import answer_gate, mcnemar_p, retrieval_gate  # noqa: E402
from trustkit.ingest.invariants import check_chunks  # noqa: E402
from trustkit.ingest.quality import assess  # noqa: E402
from trustkit.query.intent import looks_like_condition_query, looks_like_navigation_query  # noqa: E402
from trustkit.retrieval.bm25 import BM25Index  # noqa: E402
from trustkit.retrieval.chunk_header import contextual_header  # noqa: E402
from trustkit.retrieval.fusion import best_per_doc, rescue_order, rrf_order  # noqa: E402
from trustkit.text.ko import normalize_numbers, tokenize  # noqa: E402


def test_independence_no_app_imports():
    """패키지가 쓰는 쪽 코드를 import하면 이식할 수 없다 — 표준 라이브러리와 trustkit 자신만."""
    banned = {"core", "service", "shared", "agents", "main", "scripts"}
    for f in (ROOT / "trustkit").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                   [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for m in mods:
                assert m.split(".")[0] not in banned, f"{f.name}: {m}"


def test_ko_numbers_and_suffixes():
    assert normalize_numbers("30,000원 이상") == "30000 원 이상"
    assert set(tokenize("3만 원")) & set(tokenize("30,000원 이상 무료")) >= {"30000"}
    assert "투고백" in tokenize("투고백에만 따로")


def test_bm25_rare_term_wins():
    idx = BM25Index([(1, "배송 정책 일반 안내 정책"), (2, "SKU 구매 제한 투고백 2세트")] + [(10 + i, f"정책 안내 {i}") for i in range(20)])
    assert idx.score("투고백에만 적용되는 정책", 3)[0][0] == 2
    assert idx.score("없는 단어", 3) == []


def test_contextual_header():
    assert contextual_header("배송비", ["배송"], "3만원 이상 무료") == "배송비 (배송): 3만원 이상 무료"
    assert contextual_header("배송비", ["배송"], "배송비 (배송 / x): 원문") == "배송비 (배송 / x): 원문"


def test_fusion_invariants_random():
    rng = random.Random(7)
    for _ in range(300):
        primary = rng.sample(range(40), rng.randint(0, 20))
        secondary = rng.sample(range(40), rng.randint(0, 15))
        top_k, slots = rng.randint(1, 12), rng.randint(0, 4)
        out = rescue_order(primary, secondary, top_k, slots)
        assert len(out) == len(set(out))                                          # 중복 0
        assert set(primary) <= set(out)                                           # 주 채널 유실 0
        if primary:
            assert out[0] == primary[0] or top_k == 0                             # 1위는 주 채널
        rescued = [i for i in out[:top_k] if i not in primary[:top_k]]
        assert len(rescued) <= min(slots, max(top_k - 1, 0))                      # 구제 자리 상한
    assert best_per_doc([("a", 1), ("a", 2), ("b", 3)], key=lambda h: h[0]) == [("a", 1), ("b", 3)]
    assert rrf_order([[1, 2, 3], [3, 1]]) [0] == 1


def test_list_scoped_invariants():
    rng = random.Random(3)
    for _ in range(200):
        hits = rng.sample(range(30), rng.randint(0, 12))
        keep = set(rng.sample(range(30), rng.randint(0, 10)))
        out = list_scoped(hits, keep, key=lambda h: h)
        assert out == [h for h in hits if h in out]                               # 순서 유지
        assert {h for h in hits if h in keep} <= set(out)                         # 범위 안 유실 0
        first = next((i for i, h in enumerate(hits) if h in keep), len(hits))
        assert all(h in keep or hits.index(h) < first for h in out)


def test_intent():
    assert looks_like_navigation_query("배송 정책 전체 알려줘")
    assert looks_like_condition_query("투고백에만 따로 적용되는 규정?")
    assert not looks_like_condition_query("3만 적용 기준") and not looks_like_condition_query("전용계좌 기한")


def test_invariants_catch_real_defect_shapes():
    secs = [{"title": "1. 가", "content": "가 본문 첫 줄입니다", "level": 1},
            {"title": "2. 나", "content": "나 본문 첫 줄입니다", "level": 1}]
    good = [("## 1. 가\n가 본문 첫 줄입니다", ["1. 가"]), ("## 2. 나\n나 본문 첫 줄입니다", ["2. 나"])]
    assert check_chunks(secs, good) == []
    dup = good + [("## 2. 나\n나 본문 첫 줄입니다", [])]
    assert any(e.startswith("I2 중복") for e in check_chunks(secs, dup))
    lost = good[:1]
    assert any(e.startswith("I1 유실") for e in check_chunks(secs, lost))
    trailing = [("## 1. 가\n가 본문 첫 줄입니다\n## 2. 나", []), ("나 본문 첫 줄입니다", [])]
    assert any(e.startswith("I3") for e in check_chunks(secs, trailing))
    cut = [("## 1. 가\n가 본문 첫", []), ("## 1. 가\n가 본문 첫 줄입니다", []), good[1]]
    assert any(e.startswith("I2b") for e in check_chunks(secs, cut))
    assert assess(dup)["adjacent_overlap"] == 1


def test_answer_gate():
    before = [{"qid": f"q{i}", "label": "정답" if i < 10 else "오답", "type": "a", "cluster": f"c{i % 4}"} for i in range(20)]
    same = answer_gate(before, before)
    assert same["pass"] and same["groups"]["전체"]["diff"] == 0 and not same["groups"]["전체"]["significant"]
    worse = [{**r, "label": "오답"} for r in before]
    v = answer_gate(before, worse)
    assert not v["pass"] and "전체" in v["failed"] and v["groups"]["전체"]["down"] == 10


def test_retrieval_gate():
    b = {f"q{i}": i < 78 for i in range(88)}
    a = {f"q{i}": i < 84 for i in range(88)}
    v = retrieval_gate(b, a)
    assert v["pass"] and len(v["up"]) == 6 and v["down"] == [] and abs(v["p"] - 0.03125) < 1e-9 and v["significant"]
    assert mcnemar_p(0, 0) == 1.0
    assert not retrieval_gate(a, b)["pass"]


def test_stdlib_only():
    """이식성: 표준 라이브러리와 trustkit 자신만 import한다(외부 패키지 설치 없이 폐쇄망·다른 프로젝트에서 그대로)."""
    allowed = set(sys.stdlib_module_names) | {"trustkit", "__future__"}
    for f in (ROOT / "trustkit").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level > 0:
                continue   # 상대 import(패키지 안)는 trustkit 자신
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                   [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for m in mods:
                assert m.split(".")[0] in allowed, f"{f.name}: {m}"


def test_gate_fails_closed():
    """오차를 잴 수 없거나 비교할 게 없으면 통과가 아니다(리뷰 10/19 — 묶음 1개에서 100%→0%가 통과로 나왔다)."""
    before = [{"qid": f"q{i}", "label": "정답", "type": "a", "cluster": "X"} for i in range(30)]
    after = [{**r, "label": "오답"} for r in before]
    v = answer_gate(before, after)
    assert not v["pass"] and v["groups"]["전체"]["iid_fallback"]               # 묶음 1개 → 문항 독립 구간으로 대신, 표시
    assert not answer_gate([], [])["pass"]                                       # 짝지은 문항 0개
    assert not answer_gate(before, [{**r, "qid": "other"} for r in after])["pass"]
    assert not retrieval_gate({}, {})["pass"]
    one_b, one_a = before[:1], after[:1]
    assert not answer_gate(one_b, one_a)["pass"]                                 # 1문항 — 오차 못 잼 + 나빠짐 → 판정 불가 = 실패


def test_gate_single_cluster_mixed_diffs():
    """묶음 1개·차이가 섞인 일반 경우 — 문항 독립 t 구간(df=n−1)으로 대신 계산하고 표시한다."""
    from trustkit.eval.stats import iid_se, t95
    labels_b = ["정답"] * 10 + ["오답"] * 10
    labels_a = ["정답"] * 8 + ["오답"] * 2 + ["정답"] * 6 + ["오답"] * 4
    mk = lambda labs: [{"qid": f"q{i}", "label": l, "type": "a", "cluster": "X"} for i, l in enumerate(labs)]  # noqa: E731
    g = answer_gate(mk(labels_b), mk(labels_a))["groups"]["전체"]
    diffs = [(1.0 if x == "정답" else 0.0) - (1.0 if y == "정답" else 0.0) for x, y in zip(labels_a, labels_b)]
    m, se = sum(diffs) / 20, iid_se(diffs)
    assert g["iid_fallback"] and abs(g["lo"] - (m - t95(19) * se)) < 1e-12 and abs(g["hi"] - (m + t95(19) * se)) < 1e-12
    assert g["significant"] is False and g["worse"] is False               # +6 −2 섞임 → 구간이 0을 품음
    two = answer_gate(mk(["오답", "오답"]), mk(["정답", "정답"]))["groups"]["전체"]
    assert two["significant"] is False                                      # 분산 0 · 2문항은 "의미 있음"이 아님


def test_strip_suffix_cache_keeps_results():
    """lru_cache를 붙여도 결과가 같다 — 캐시 없는 원래 함수와 무작위 단어로 대조."""
    import random
    from trustkit.text import ko
    raw = ko.strip_suffix.__wrapped__
    rng = random.Random(5)
    syll = "가나다라마바사아자차카타파하은는이가을를의에서으로까지만도와과"
    for _ in range(3000):
        w = "".join(rng.choice(syll) for _ in range(rng.randint(1, 6)))
        assert ko.strip_suffix(w) == raw(w)
    assert BM25Index([(1, "a")]).built_at > 0
