"""지식 골든셋 읽기 — 문항을 정답 지식이 **지금** 있는 영역으로 묻는다(10/29, 운영 질의 감시 설계 §3-5).

합성 문항(confluence_synth_questions.py)은 만들 때의 영역 이름(ns)을 적어 둔다. 영역을 재구성하면(10/2x) 옛 이름으로 물어 검색 범위 밖이 되고
측정이 0%가 됐다 — 이름 대신 정답 지식 id로 지금 영역을 찾는다. 정답 지식이 모두 활성이 아니면 적힌 영역 그대로(그 문항은 정답 없음으로 센다).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs" / "trustkit"))
# 닻 규칙 본체는 신뢰도 레이어(trustkit.eval.anchors — 앱의 골든셋 후보 결정도 같은 규칙). 여기서 다시 내보낸다(측정 스크립트·테스트가 씀)
from trustkit.eval.anchors import (  # noqa: E402,F401
    NOT_UNIQUE_COVER_PCT, anchor_hashes, anchors_for, pick_anchors, resolve_anchors, resolve_hashed,
)


async def _resolve_gold_anchors(conn, items: list[dict]) -> None:
    """닻이 적힌 문항(gold_anchors 칸이 있으면 빈 목록이어도)의 gold_ids를 지금 조각으로 다시 계산 — 못 찾으면 빈 목록(검색 놓침으로 셈, 개수 알림).
    적힌 gold_ids는 무시. 조각 줄 해시는 한 번만 계산해 문항·닻마다 다시 자르지 않는다(리뷰 11/16 — 페이지 없는 닻은 전체 조각이 대상)."""
    todo = [it for it in items if "gold_anchors" in it]
    if not todo:
        return
    pages = sorted({a["page"] for it in todo for a in it["gold_anchors"] if a.get("page")})
    by_page: dict = {}
    for r in await conn.fetch("SELECT id, content, confluence_page_id FROM rag_knowledge WHERE status = 'active' "
                              "AND confluence_page_id = ANY($1::text[]) ORDER BY id", pages):
        by_page.setdefault(r["confluence_page_id"], []).append((r["id"], set(anchor_hashes(r["content"]))))
    everything = None
    lost = 0
    for it in todo:
        ids: list[int] = []
        for a in it["gold_anchors"]:
            if a.get("page"):
                pool = by_page.get(a["page"], [])
            else:
                if everything is None:
                    everything = [(r["id"], set(anchor_hashes(r["content"]))) for r in await conn.fetch(
                        "SELECT id, content FROM rag_knowledge WHERE status = 'active' ORDER BY id")]
                pool = everything
            ids += [i for i in resolve_hashed(a["lines"], pool, a.get("need", 1)) if i not in ids]
        it["gold_ids"] = ids
        lost += not ids
    if lost:
        print(f"⚠ 정답 닻을 지금 조각에서 못 찾은 문항 {lost}/{len(todo)}개(내용이 바뀌었거나 미적재) — 검색 놓침으로 센다")


def split_abstain(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """(정답 있는 문항, 범위 밖 문항 expect=abstain). 범위 밖은 정답 조각이 없어 검색 측정·정답률에 섞지 않는다(따로 센다)."""
    return [it for it in items if it.get("expect") != "abstain"], [it for it in items if it.get("expect") == "abstain"]


async def _all_items(conn, path: str, real: bool) -> list[dict]:
    """파일 문항 + (real) 골든셋에 든 실사용 지식 문항(평가 게이트의 골든셋 후보에서 사람이 정답을 고른 것). 파일과 같은 질문은 파일 쪽만."""
    items = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    if real:
        from service.eval.questions import real_knowledge_golden
        have = {it["question"] for it in items}
        extra = [it for it in await real_knowledge_golden(conn) if it["question"] not in have]
        print(f"실사용 문항 {len(extra)}개 더함(골든셋 후보에서 사람이 정답을 고른 것)")
        items += extra
    return items


async def load_abstain_items(conn, path: str, real: bool = False) -> list[dict]:
    """범위 밖 문항(정답 = 모른다)만 — scripts/eval_abstain.py가 단정 오답을 따로 잰다."""
    return split_abstain(await _all_items(conn, path, real))[1]


async def load_items(conn, path: str, real: bool = False) -> list[dict]:
    """정답 있는 문항만 — 범위 밖 문항은 개수만 알리고 뺀다(따로 eval_abstain.py, 11/16)."""
    items, oos = split_abstain(await _all_items(conn, path, real))
    if oos:
        print(f"범위 밖 문항 {len(oos)}개는 이 측정에서 뺌(정답 = 모른다 — 정답률·검색에 안 섞음, eval_abstain.py로 따로)")
    await _resolve_gold_anchors(conn, items)   # 닻이 있으면 gold_ids를 지금 조각으로 다시 계산(적힌 id는 무시)
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
    # 정답과 글자까지 같은 활성 조각(다른 페이지에 똑같이 실린 글)도 정답 — 검색이 같은 글을 하나로 접어 어느 id가 남을지는 점수·id 순(11/09).
    # 접기는 검색 범위 전체(하위·연결 컬렉션 포함)라 여기도 컬렉션을 가리지 않는다(리뷰 L4). 묻는 영역은 원래 정답 id로만(아래 now)
    # 정정으로 정답이 새 버전이 됐으면 그 현행 버전(같은 논리 문서)과 그 사본도 정답(v2.228 — 같은 글 사본까지 같이 정정하니, md5만 보면
    # 정답이 0개가 돼 맞는 정정이 "검색 놓침"으로 잡혔다. 독립 점검 M6 11/10)
    same = {}
    for r in await conn.fetch(
            """WITH g AS (SELECT id, content_norm_md5, coalesce(logical_document_id, id) AS lid FROM rag_knowledge WHERE id = ANY($1::int[])),
                    cur AS (SELECT g.id gid, k.id, k.content_norm_md5 FROM g JOIN rag_knowledge k ON k.status = 'active'
                              AND (k.content_norm_md5 = g.content_norm_md5 OR coalesce(k.logical_document_id, k.id) = g.lid))
               SELECT gid, id FROM cur WHERE id <> gid
               UNION SELECT c.gid, k.id FROM cur c JOIN rag_knowledge k
                  ON k.status = 'active' AND k.content_norm_md5 = c.content_norm_md5 AND k.id <> c.gid
               ORDER BY 1, 2""", gold):
        same.setdefault(r["gid"], []).append(r["id"])
    for it in items:
        it["gold_ids"] = list(it["gold_ids"]) + [j for i in it["gold_ids"] for j in same.get(i, []) if j not in it["gold_ids"]]
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
