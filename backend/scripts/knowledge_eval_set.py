"""지식 골든셋 읽기 — 문항을 정답 지식이 **지금** 있는 영역으로 묻는다(10/29, 운영 질의 감시 설계 §3-5).

합성 문항(confluence_synth_questions.py)은 만들 때의 영역 이름(ns)을 적어 둔다. 영역을 재구성하면(10/2x) 옛 이름으로 물어 검색 범위 밖이 되고
측정이 0%가 됐다 — 이름 대신 정답 지식 id로 지금 영역을 찾는다. 정답 지식이 모두 활성이 아니면 적힌 영역 그대로(그 문항은 정답 없음으로 센다).
"""
from __future__ import annotations

import json
from pathlib import Path


async def load_items(conn, path: str) -> list[dict]:
    items = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    # 정답이 페이지인 문항(gold_pages, 10/29 그림 처리 전/후 측정) — 다시 적재하면 조각 id가 바뀌므로 그 페이지의 **지금** 활성 조각을 정답으로
    pages = sorted({p for it in items for p in it.get("gold_pages") or []})
    if pages:
        by_page: dict = {}
        for r in await conn.fetch("SELECT id, confluence_page_id FROM rag_knowledge WHERE status = 'active' "
                                  "AND confluence_page_id = ANY($1::text[]) ORDER BY id", pages):
            by_page.setdefault(r["confluence_page_id"], []).append(r["id"])
        for it in items:
            if it.get("gold_pages"):
                it["gold_ids"] = [i for p in it["gold_pages"] for i in by_page.get(p, [])]
        none = sum(1 for it in items if it.get("gold_pages") and not it["gold_ids"])
        if none:
            print(f"⚠ 정답 페이지에 활성 조각이 없는 문항 {none}개(승인 대기·미적재) — 검색 놓침으로 센다")
    gold = sorted({i for it in items for i in it["gold_ids"]})
    now = {r["id"]: r["name"] for r in await conn.fetch(
        "SELECT k.id, n.name FROM rag_knowledge k JOIN ops_namespace n ON n.id = k.namespace_id "
        "WHERE k.id = ANY($1::int[]) AND k.status = 'active'", gold)}
    moved = split = 0
    for it in items:
        areas = [now[i] for i in it["gold_ids"] if i in now]
        cur = areas[0] if areas else None
        split += len(set(areas)) > 1   # 정답이 여러 영역에 흩어짐 — 첫 정답의 영역으로만 묻는다(나머지는 범위 밖일 수 있음, 리뷰 L6)
        # ask_fixed = 일부러 다른 컬렉션에서 묻는 문항(검색 범위 실험, 11/02) — 정답 위치로 옮기지 않는다
        if cur and cur != it["ns"] and not it.get("ask_fixed"):
            it["ns"] = cur
            moved += 1
    if moved:
        print(f"정답 지식의 지금 영역으로 묻기: {moved}/{len(items)}문항(만들 때 영역 이름과 다름)")
    if split:
        print(f"⚠ 정답 지식이 여러 영역에 흩어진 문항 {split}개 — 첫 정답의 영역으로만 물음")
    return items
