"""임베딩 모델 후보 비교 벤치마크 — 실제 골든셋(89문항, tests/fixtures/golden_set)과
실제 policy_item 데이터로 Track2(docs/policy-doc-pipeline-plan.md §4)의 Track A(지식-only
벡터검색) 방식을 그대로 재현하되, 임베딩 모델만 바꿔가며 hit@K를 비교한다.

실행 전제: huggingface.co 접근이 가능해야 후보 모델(BGE-M3/KURE-v1 등)을 내려받을 수 있다.
(2026-09-07 기준 사내 개발망에서 huggingface.co가 도메인 레벨로 막혀 있어 미실행 상태 —
 사내 HF 미러/프록시가 생기거나 외부망에서 실행할 때 이 스크립트를 그대로 쓰면 된다.)

실행 방법 (백엔드 컨테이너 안, DB·golden set 둘 다 접근 가능한 환경에서):
    docker exec -it ops-backend python scripts/bench_embedding_models.py

또는 로컬 venv에 asyncpg+sentence-transformers 설치 후 DATABASE_URL을 host 노출 포트로
바꿔서 직접 실행해도 된다(예: postgresql://ops:ops1234@localhost:5432/opsdb).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import asyncpg
import numpy as np
from sentence_transformers import SentenceTransformer

_GOLDEN_SET_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "golden_set" / "online_delivus_v1.jsonl"
_FILE_TO_NAMESPACE = [("온라인스토어", "온라인스토어 DB"), ("딜리버스", "딜리버스 DB")]
_TOP_K = 10

# 비교 후보 — (표시 이름, HF 모델 id). 2026-09-07 시점 MTEB-ko-retrieval 리더보드 기준 상위권 +
# 현재 프로덕션 모델. 필요하면 후보 추가/제거만 하면 됨.
_MODELS = [
    ("현재(baseline)", "paraphrase-multilingual-mpnet-base-v2"),
    ("BGE-M3", "BAAI/bge-m3"),
    ("dragonkue/BGE-m3-ko", "dragonkue/BGE-m3-ko"),
    ("KURE-v1", "nlpai-lab/KURE-v1"),
]


def _namespace_for_file(file_: str):
    for needle, ns in _FILE_TO_NAMESPACE:
        if needle in file_:
            return ns
    return None


async def _load_data(conn: asyncpg.Connection):
    items = [dict(r) for r in await conn.fetch(
        "SELECT id, namespace_id, policy_name, category_path, raw_body, source_file, source_sheet, "
        "source_row, status FROM policy_item"
    )]
    params = [dict(r) for r in await conn.fetch(
        "SELECT policy_item_id, condition FROM policy_param"
    )]
    namespaces = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM ops_namespace")}
    return items, params, namespaces


def _resolve_gold_ids(entry, items_by_ns, conditions_by_item, ns_name_to_id):
    qtype, src = entry["type"], entry["source"]
    ns_name = _namespace_for_file(src.get("file", ""))
    if ns_name is None or ns_name not in ns_name_to_id:
        return None
    ns_id = ns_name_to_id[ns_name]
    ns_items = [it for it in items_by_ns.get(ns_id, []) if it["status"] != "deprecated"]

    if qtype in ("param", "narrative"):
        for it in ns_items:
            if it["source_file"] == src["file"] and it["source_sheet"] == src["sheet"] and it["source_row"] == src["row"]:
                return {it["id"]}
        return set()
    elif qtype == "navigation":
        cat = src["category"]
        return {it["id"] for it in ns_items if it["category_path"] and cat in it["category_path"]}
    else:  # condition_filter
        cond = src["condition"]
        return {it["id"] for it in ns_items if cond in conditions_by_item.get(it["id"], set())}


async def main():
    db_url = os.environ.get("DATABASE_URL", "postgresql://ops:ops1234@localhost:5432/opsdb")
    conn = await asyncpg.connect(db_url)
    try:
        items, params, ns_name_to_id = await _load_data(conn)
    finally:
        await conn.close()

    items_by_ns: dict[int, list] = {}
    for it in items:
        items_by_ns.setdefault(it["namespace_id"], []).append(it)
    conditions_by_item: dict[int, set] = {}
    for p in params:
        conditions_by_item.setdefault(p["policy_item_id"], set()).add(p["condition"])

    golden = [json.loads(l) for l in open(_GOLDEN_SET_PATH, encoding="utf-8") if l.strip()]

    active_items = [it for it in items if it["status"] != "deprecated"]
    corpus_texts, corpus_ids = [], []
    for it in active_items:
        cats = " / ".join(it["category_path"] or [])
        corpus_texts.append(f"[{it['policy_name']}] ({cats})\n{it['raw_body']}")
        corpus_ids.append(it["id"])

    scored_entries = []
    for entry in golden:
        gold_ids = _resolve_gold_ids(entry, items_by_ns, conditions_by_item, ns_name_to_id)
        if gold_ids:
            scored_entries.append((entry, gold_ids))

    print(f"corpus={len(corpus_texts)}건, 채점 대상 질문={len(scored_entries)}/{len(golden)}건\n")

    results = {}
    for label, model_name in _MODELS:
        print(f"=== {label} ({model_name}) 로딩 중... ===")
        t0 = time.monotonic()
        model = SentenceTransformer(model_name)
        load_s = time.monotonic() - t0

        t0 = time.monotonic()
        corpus_emb = np.asarray(
            model.encode(corpus_texts, normalize_embeddings=True, show_progress_bar=False, batch_size=16),
            dtype=np.float32,
        )
        encode_corpus_s = time.monotonic() - t0

        queries = [e["query"] for e, _ in scored_entries]
        t0 = time.monotonic()
        query_emb = np.asarray(
            model.encode(queries, normalize_embeddings=True, show_progress_bar=False, batch_size=16),
            dtype=np.float32,
        )
        encode_query_s = time.monotonic() - t0

        sims = query_emb @ corpus_emb.T
        per_type: dict[str, list[bool]] = {}
        for i, (entry, gold_ids) in enumerate(scored_entries):
            top_idx = np.argsort(-sims[i])[:_TOP_K]
            top_ids = {corpus_ids[j] for j in top_idx}
            per_type.setdefault(entry["type"], []).append(bool(gold_ids & top_ids))

        overall_hits = [h for hits in per_type.values() for h in hits]
        overall_rate = sum(overall_hits) / len(overall_hits)
        results[label] = {
            "overall": overall_rate,
            "by_type": {t: sum(h) / len(h) for t, h in per_type.items()},
            "dim": corpus_emb.shape[1],
            "load_s": round(load_s, 1), "encode_corpus_s": round(encode_corpus_s, 1),
        }
        print(f"  차원={corpus_emb.shape[1]}, 로딩={load_s:.1f}s, corpus 임베딩={encode_corpus_s:.1f}s, "
              f"전체 hit@{_TOP_K}={overall_rate:.1%}")
        for t, r in results[label]["by_type"].items():
            print(f"    {t}: {r:.1%}")
        print()
        del model

    print("\n=== 최종 비교표 ===")
    header = f"{'유형':<18}" + "".join(f"{label:>20}" for label, _ in _MODELS)
    print(header)
    all_types = sorted({t for r in results.values() for t in r["by_type"]})
    for t in all_types:
        print(f"{t:<18}" + "".join(f"{results[label]['by_type'].get(t, 0):>19.1%} " for label, _ in _MODELS))
    print(f"{'전체':<18}" + "".join(f"{results[label]['overall']:>19.1%} " for label, _ in _MODELS))

    out_path = Path(__file__).parent / "bench_embedding_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n결과 저장:", out_path)


if __name__ == "__main__":
    asyncio.run(main())
