"""지식 골든셋 정답 닻(scripts/knowledge_eval_set.py, 11/16) — 다시 적재해 조각 경계·id가 바뀌어도 정답을 잃지 않고, 느슨해지지도 않는가.

성질(사례가 아니라 불변식으로):
- 내용이 그대로면 어떻게 다시 잘라도 정답 줄을 가진 조각이 하나 이상 찾아진다(유실 0)
- 찾아진 조각은 전부 정답 줄을 need개 이상 가진다(엉뚱한 조각 0)
- 자기만 가진 줄이 없는 정답은 일부만 겹친 이웃 조각을 정답으로 치지 않는다
- 정답 줄이 사라지면 0개(숨기지 않음 — 로더가 개수로 알림)
"""
import importlib.util
import random
from pathlib import Path

_P = Path(__file__).resolve().parent.parent / "scripts" / "knowledge_eval_set.py"
_spec = importlib.util.spec_from_file_location("knowledge_eval_set_script", _P)
kes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kes)


def _chunks(lines, cuts):
    """줄 목록을 cuts 위치에서 잘라 조각 글 목록."""
    out, prev = [], 0
    for c in list(cuts) + [len(lines)]:
        if c > prev:
            out.append("\n".join(lines[prev:c]))
        prev = c
    return out


def _doc(rng, n, shared=()):
    return [f"줄 {rng.randrange(10**9)} 내용입니다" for _ in range(n)] + list(shared)


def test_anchor_lines_skip_headings_and_short_lines():
    hs = kes.anchor_hashes("# 제목\n- Y\n|---|\n실제 본문 줄입니다\n실제 본문 줄입니다")
    assert len(hs) == 1   # 제목·짧은 줄 빼고, 같은 줄은 한 번


def test_rechunked_same_content_never_loses_gold_and_never_picks_unrelated():
    rng = random.Random(1116)
    for _ in range(300):
        lines = _doc(rng, rng.randrange(6, 30))
        old = _chunks(lines, sorted(rng.sample(range(1, len(lines)), rng.randrange(1, min(5, len(lines) - 1)))))
        new = _chunks(lines, sorted(rng.sample(range(1, len(lines)), rng.randrange(1, min(5, len(lines) - 1)))))
        new_chunks = list(enumerate(new))
        for gi, gold in enumerate(old):
            anchors, need = kes.pick_anchors(gold, [o for j, o in enumerate(old) if j != gi])
            got = kes.resolve_anchors(anchors, new_chunks, need)
            assert got, "같은 내용인데 정답 유실"
            for cid in got:
                assert len(set(anchors) & set(kes.anchor_hashes(new[cid]))) >= need
            # 정답 줄이 실제로 들어간 새 조각은 전부 찾아야 한다(다시 잘려 나뉜 양쪽)
            gold_lines = set(kes.anchor_hashes(gold))
            expect = [cid for cid, c in new_chunks if gold_lines & set(kes.anchor_hashes(c))]
            assert got == expect


def test_not_unique_gold_ignores_partial_neighbour_but_keeps_superset():
    shared = ["공통 안내 문장 하나입니다", "공통 안내 문장 둘입니다", "공통 안내 문장 셋입니다", "공통 안내 문장 넷입니다", "공통 안내 문장 다섯입니다"]
    gold = "\n".join(shared)                       # 자기만 가진 줄 없음(전부 다른 조각과 겹침)
    superset = "\n".join(shared + ["더 긴 조각의 추가 줄입니다"])
    neighbour = "\n".join(shared[:2] + ["이웃 조각 고유 줄입니다"])
    anchors, need = kes.pick_anchors(gold, [superset, neighbour])
    assert need == 4                               # 5줄의 80% 올림
    assert kes.resolve_anchors(anchors, [(1, superset), (2, neighbour), (3, gold)], need) == [1, 3]


def test_unique_anchor_one_line_is_enough():
    gold = "고유한 정답 줄입니다\n공통 머리 줄입니다"
    anchors, need = kes.pick_anchors(gold, ["공통 머리 줄입니다\n다른 내용 줄입니다"])
    assert need == 1 and len(anchors) == 1
    assert kes.resolve_anchors(anchors, [(7, "앞 내용 줄입니다\n고유한 정답 줄입니다"), (8, "공통 머리 줄입니다")], need) == [7]


def test_anchors_for_matches_pick_anchors_rule():
    """생성기·변환기가 쓰는 일괄 계산(anchors_for)이 한 건 규칙(pick_anchors)과 같은 닻을 낸다 — 같은 페이지 범위·사본 제외 포함."""
    rng = random.Random(7)
    shared = ["여러 조각에 나오는 공통 줄입니다", "또 다른 공통 안내 줄입니다"]
    live = []
    for cid in range(1, 41):
        page = f"p{cid % 4}" if cid % 5 else None
        lines = _doc(rng, rng.randrange(1, 4), shared[: rng.randrange(0, 3)])
        live.append({"id": cid, "content": "\n".join(lines), "confluence_page_id": page, "content_norm_md5": f"m{cid}"})
    live.append({**live[0], "id": 99, "content_norm_md5": live[0]["content_norm_md5"]})   # 글자까지 같은 사본
    got = kes.anchors_for([r["id"] for r in live], live)
    for g in live:
        others = [o["content"] for o in live if o["id"] != g["id"] and o["content_norm_md5"] != g["content_norm_md5"]
                  and (not g["confluence_page_id"] or o["confluence_page_id"] == g["confluence_page_id"])]
        lines, need = kes.pick_anchors(g["content"], others)
        assert (got[g["id"]]["lines"], got[g["id"]]["need"]) == (lines, need)


def test_out_of_scope_items_are_kept_out_of_scored_items():
    items = [{"qid": "a", "gold_anchors": []}, {"qid": "oos1", "expect": "abstain"}, {"qid": "b", "gold_ids": [1]}]
    scored, oos = kes.split_abstain(items)
    assert [x["qid"] for x in scored] == ["a", "b"] and [x["qid"] for x in oos] == ["oos1"]


def test_changed_content_is_lost_not_guessed():
    anchors, need = kes.pick_anchors("바뀌기 전 정답 줄입니다", [])
    assert kes.resolve_anchors(anchors, [(1, "바뀐 뒤 정답 줄입니다")], need) == []


def test_items_with_empty_anchors_get_empty_gold_not_keyerror():
    """리뷰 11/16: 변환 때 정답 조각이 이미 지워져 gold_anchors가 빈 문항 — gold_ids가 안 생겨 로더가 KeyError로 멈췄다. 이제 빈 목록(놓침으로 셈)."""
    import asyncio

    class Conn:
        async def fetch(self, *a):
            return []

    items = [{"qid": "a", "gold_anchors": []}, {"qid": "b", "gold_anchors": [{"page": "p1", "lines": ["x"], "need": 1}]}, {"qid": "c", "gold_ids": [3]}]
    asyncio.run(kes._resolve_gold_anchors(Conn(), items))
    assert [it.get("gold_ids") for it in items] == [[], [], [3]]


def test_resolve_hashed_matches_resolve_anchors():
    rng = random.Random(3)
    chunks = [(i, "\n".join(_doc(rng, rng.randrange(1, 5)))) for i in range(30)]
    anchors = kes.anchor_hashes(chunks[4][1])[:2]
    hashed = [(i, set(kes.anchor_hashes(c))) for i, c in chunks]
    assert kes.resolve_hashed(anchors, hashed, 1) == kes.resolve_anchors(anchors, chunks, 1) == [4]
