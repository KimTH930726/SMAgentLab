"""지식 골든셋 정답을 조각 id → 정답 닻(gold_anchors)으로 바꾼다(11/16, 골든셋 자동화 1단계 — 배포 전 전체 새로 넣기 대비).

닻 = 정답 조각에만 있던 줄의 해시(+ 컨플 페이지 id). 다시 적재해 조각 id가 바뀌어도 그 줄을 가진 조각이 정답이 된다
(규칙·근거는 knowledge_eval_set.py 머리말). 원문은 쓰지 않는다 — 해시만. 출력은 숫자만.

    python scripts/golden_anchors.py --in eval_out/confluence_synth.jsonl --out eval_out/confluence_synth_anchors.jsonl
    python scripts/golden_anchors.py --check eval_out/confluence_synth_anchors.jsonl --against eval_out/confluence_synth.jsonl

--check: 지금 DB에서 닻으로 다시 찾은 정답이 원래 적힌 조각 id와 문항마다 똑같은지(전/후 같음 = 변환이 정답을 바꾸지 않음).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import logging  # noqa: E402
logging.disable(logging.INFO)

from knowledge_eval_set import _resolve_gold_anchors, anchors_for  # noqa: E402


def _read(path: str) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


async def make(conn, items: list[dict]) -> tuple[list[dict], dict]:
    ids = sorted({i for it in items for i in it["gold_ids"]})
    cols = "id, content, confluence_page_id, content_norm_md5"
    live = [dict(r) for r in await conn.fetch(f"SELECT {cols} FROM rag_knowledge WHERE status = 'active'")]
    live_ids = {r["id"] for r in live}
    inactive = [dict(r) for r in await conn.fetch(f"SELECT {cols} FROM rag_knowledge WHERE id = ANY($1::int[]) AND status <> 'active'",
                                                   [i for i in ids if i not in live_ids])]
    anchors = anchors_for(ids, live, inactive)
    stats = {"items": len(items), "gold_chunks": len(ids), "missing": len(ids) - len(anchors),
             "with_page": sum(1 for a in anchors.values() if a["page"]), "no_page": sum(1 for a in anchors.values() if not a["page"]),
             "not_unique": sum(1 for a in anchors.values() if a["need"] > 1)}
    out = []
    for it in items:
        a = [anchors[i] for i in it["gold_ids"] if i in anchors]
        out.append({**{k: v for k, v in it.items() if k != "gold_ids"}, "gold_ids_at_make": it["gold_ids"], "gold_anchors": a})
    return out, stats


async def check(conn, anchored: list[dict], original: list[dict]) -> dict:
    """lost_gold = 원래 정답 중 지금 **사용 중인데** 닻이 못 찾은 것(진짜 유실). 원래 정답이 정정으로 새 버전이 돼 내려간 것은 inactive_original로
    따로 센다(닻은 그 현행 버전을 찾는다 — 유실 아님). empty = 정답을 하나도 못 찾은 문항(측정에서 검색 놓침으로 셈)."""
    before = {it["qid"]: set(it["gold_ids"]) for it in original}
    active = {r["id"] for r in await conn.fetch("SELECT id FROM rag_knowledge WHERE status = 'active' AND id = ANY($1::int[])",
                                                sorted({i for s in before.values() for i in s}))}
    items = [dict(it) for it in anchored]
    await _resolve_gold_anchors(conn, items)
    same = extra = lost = inactive = empty = 0
    for it in items:
        b, a = before.get(it["qid"], set()), set(it["gold_ids"])
        same += a == b
        extra += bool(a - b)
        lost += bool((b & active) - a)
        inactive += bool(b - active)
        empty += not a
    return {"items": len(items), "same": same, "extra_gold": extra, "lost_gold": lost, "inactive_original": inactive, "empty": empty}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src")
    ap.add_argument("--out")
    ap.add_argument("--check")
    ap.add_argument("--against")
    a = ap.parse_args()
    from core.database import get_conn, init_pool
    await init_pool()
    async with get_conn() as conn:
        if a.check:
            print(json.dumps(await check(conn, _read(a.check), _read(a.against)), ensure_ascii=False))
            return
        out, stats = await make(conn, _read(a.src))
    Path(a.out).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in out), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
