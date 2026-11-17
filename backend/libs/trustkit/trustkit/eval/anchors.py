"""정답 닻(gold anchors) — 골든셋 정답을 조각 id 대신 "정답 조각에만 있던 줄의 해시"로 적는다(OpsLens 11/16).

조각 id로 적은 정답은 다시 적재하면 id가 바뀌어 측정이 0%가 된다. 페이지로 적으면 그 페이지 조각 아무거나 걸려도 정답이라 느슨해진다.
닻 = 다른 조각(같은 범위)에 없는 줄의 해시 → 지금 조각 중 그 줄을 가진 것이 정답(다시 잘려 나뉘면 양쪽 다, 내용이 바뀌면 0 — 숨기지 않음).
자기만 가진 줄이 없는 정답은 닻 줄의 80% 이상을 가진 조각만(통째 담은 상위 조각은 정답, 일부만 겹친 이웃은 아님).
원문은 저장하지 않는다(해시만). 표준 라이브러리만 — 줄 규칙·해시는 적재 품질 검사(ingest.quality)와 같다.
한계: 자기만 가진 줄이 없는 정답(80% 기준)은 내용이 그대로여도 다시 잘려 80%가 두 조각으로 갈리면 못 찾는다 — 조용히 맞힘으로 세지 않고
"못 찾은 문항" 개수로 드러난다(그때 그 문항 정답을 다시 고른다). 고유 줄이 있는 정답(11/16 실측 50 중 34)은 이 한계가 없다.
"""
from __future__ import annotations

from trustkit.ingest.quality import MIN_LINE, line_hash, text_lines



# ─────────────────────────────────────────────────────────────────────────────
# 조각 id로 적은 정답은 다시 적재하면 id가 바뀌어 측정이 0%가 된다. 페이지(gold_pages)로 적으면 그 페이지 조각 아무거나 걸려도 정답이라
# 느슨해진다(점수 부풀림). 그래서 "정답 조각에만 있던 줄"의 해시를 닻으로 적고, 지금 조각 중 그 줄을 가진 것을 정답으로 다시 찾는다 —
# 다시 잘려 두 조각으로 나뉘면 둘 다, 내용이 바뀌어 줄이 사라지면 0(숨기지 않고 개수로 알림). 원문은 저장하지 않는다(해시만).
# 페이지 id가 있으면 그 페이지 조각 안에서만 찾는다(다른 페이지의 같은 줄이 끼지 않게). 없으면(7월 옛 일괄 적재) 전체에서.

def anchor_hashes(content: str) -> list[str]:
    """닻 후보 줄 — 제목 줄·짧은 줄(표 구분선 등) 빼고, 순서 유지·중복 제거."""
    out: dict = {}
    for ln in text_lines(content):
        if not ln.startswith("#") and len(ln) >= MIN_LINE:
            out.setdefault(line_hash(ln), None)
    return list(out)


NOT_UNIQUE_COVER_PCT = 80   # 자기만 가진 줄이 없는 정답은 닻 줄의 80% 이상을 가진 조각만 정답(일부만 겹친 이웃 조각을 정답으로 치지 않게, 11/16 실측 24곳)


def pick_anchors(gold_content: str, others: list[str]) -> tuple[list[str], int]:
    """정답 조각의 닻 → (닻 줄, 필요한 줄 수). 다른 조각(같은 범위)에 없는 줄이 있으면 그 줄들 · 하나만 맞아도 정답(다시 잘려 나뉘어도
    양쪽 다 찾게). 전부 다른 조각과 겹치면 전체 줄 · 그 80% 이상을 가진 조각만 정답(통째 담은 상위 조각은 정답, 일부만 겹친 이웃은 아님)."""
    mine = anchor_hashes(gold_content)
    seen = {h for o in others for h in anchor_hashes(o)}
    uniq = [h for h in mine if h not in seen]
    if uniq:
        return uniq, 1
    return mine, max(1, -(-len(mine) * NOT_UNIQUE_COVER_PCT // 100))   # 올림 — 정수 계산(부동소수 경계 오차 없이)


def anchors_for(gold_ids: list[int], live: list[dict], inactive_gold: list[dict] = ()) -> dict:
    """정답 조각 id → {"page", "lines", "need"} — live = 사용 중 조각 전체(id·content·confluence_page_id·content_norm_md5).
    같은 범위(같은 페이지, 페이지 없으면 전체)의 다른 조각과 비교 — 글자까지 같은 사본은 빼고(사본은 원래 정답으로 친다).
    inactive_gold = 정정으로 내려간 정답 조각(닻은 만들되 다른 정답의 비교 대상엔 안 넣음 — 닻은 그 현행 버전을 찾는다).
    줄 해시는 조각마다 한 번만 계산(6천 조각 × 문항 수만큼 다시 자르지 않게)."""
    by_id = {r["id"]: r for r in [*live, *inactive_gold]}
    hashes = {cid: anchor_hashes(r["content"]) for cid, r in by_id.items()}
    holders: dict = {}
    for r in live:
        for h in hashes[r["id"]]:
            holders.setdefault(h, []).append(r["id"])
    out = {}
    for gid in gold_ids:
        g = by_id.get(gid)
        if g is None:
            continue
        page = g["confluence_page_id"]
        def other(cid: int) -> bool:
            o = by_id[cid]
            copy = g["content_norm_md5"] is not None and o["content_norm_md5"] == g["content_norm_md5"]   # 지문 없는 조각끼리는 사본으로 안 봄
            return cid != gid and not copy and (not page or o["confluence_page_id"] == page)
        mine = hashes[gid]
        uniq = [h for h in mine if not any(other(c) for c in holders.get(h, ()))]
        out[gid] = ({"page": page, "lines": uniq, "need": 1} if uniq else
                    {"page": page, "lines": mine, "need": max(1, -(-len(mine) * NOT_UNIQUE_COVER_PCT // 100))})
    return out


def resolve_hashed(anchors: list[str], chunks: list[tuple[int, set]], need: int = 1) -> list[int]:
    """닻 줄을 need개 이상 가진 조각 id — chunks = (id, 그 조각의 닻 후보 줄 해시 집합). 해시를 미리 계산해 둔 쪽(측정 로더)이 쓴다."""
    want = set(anchors)
    return [cid for cid, hs in chunks if len(want & hs) >= need]


def resolve_anchors(anchors: list[str], chunks: list[tuple[int, str]], need: int = 1) -> list[int]:
    """닻 줄을 need개 이상 가진 지금 조각 id(순서 = 주어진 순서)."""
    return resolve_hashed(anchors, [(cid, set(anchor_hashes(content))) for cid, content in chunks], need)
