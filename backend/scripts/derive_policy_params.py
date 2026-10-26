"""정책 파라미터 파생 — 실행 안 된 v0 설계 단계 #9를 돌린다 (2026-10-26, 적재 검수 "파라미터 파생 981/981 어긋남").

    python scripts/derive_policy_params.py extract --out eval_out/params/derived_1026.jsonl [--limit N] [--concurrency 4]
    python scripts/derive_policy_params.py load --in eval_out/params/derived_1026.jsonl     # 지금 DATABASE_URL의 DB에 넣는다

extract: 숫자가 있는 현행 정책마다 로컬 LLM이 파라미터를 뽑고(원문은 로컬 LLM에만), 값이 원문에 글자로 없는 건 버린다(param_derive.validate_params).
         결과는 파일(eval_out, git 제외) — 이어서 실행 가능(이미 한 정책은 건너뜀). 출력은 개수만.
load:    파일을 DB에 넣는다 — 그 정책에 파라미터가 이미 있으면 건너뜀(사람이 고친 값 보호), 현행이 아닌 정책도 건너뜀. 운영 반영 전에는
         사본 DB(opsdb_exp)에서만 — 측정·게이트를 통과한 뒤에만 실제 DB에(사람 승인).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")
import logging  # noqa: E402
logging.disable(logging.INFO)

from service.policy.param_derive import DERIVE_SYSTEM, parse_params, validate_params  # noqa: E402


async def extract(out: Path, limit: int, concurrency: int) -> None:
    from core.database import close_pool, get_conn, init_pool
    from scripts.eval_answers import local_llm
    await init_pool()
    try:
        async with get_conn() as conn:
            rows = await conn.fetch("""SELECT id, source_key, policy_name, array_to_string(category_path, ' > ') AS cat, raw_body
                                       FROM policy_item WHERE status = 'active' AND raw_body ~ '[0-9]' ORDER BY id""")
    finally:
        await close_pool()
    done = set()
    if out.exists():
        # 오류로 끝난 정책은 다시 시도한다(이어서 실행 = 성공한 것만 건너뜀, 리뷰 L5) — 같은 정책 줄이 둘이면 load가 나중 것을 건너뜀(이미 있음)
        done = {r["policy_id"] for r in map(json.loads, filter(str.strip, out.read_text(encoding="utf-8").splitlines())) if not r.get("error")}
    todo = [r for r in rows if r["id"] not in done][: limit or None]
    llm = await local_llm()
    model = getattr(llm, "_model", "") or ""
    out.parent.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    stats = {"policies": 0, "kept": 0, "dropped": 0, "errors": 0}
    lock = asyncio.Lock()

    async def one(r) -> None:
        async with sem:
            prompt = f"[정책] {r['cat']} > {r['policy_name']}\n[원문]\n{r['raw_body']}"
            try:
                text = await llm.generate_once(prompt, system=DERIVE_SYSTEM, max_tokens=1200)
                keep, drop = validate_params(parse_params(text), r["raw_body"])
                err = None
            except Exception as e:  # noqa: BLE001 — 한 정책 실패는 기록하고 다음(다시 돌리면 이어서)
                keep, drop, err = [], [], type(e).__name__
            async with lock:
                with out.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"policy_id": r["id"], "source_key": r["source_key"], "params": keep, "dropped": len(drop),
                                        "error": err, "model": model}, ensure_ascii=False) + "\n")
                stats["policies"] += 1
                stats["kept"] += len(keep)
                stats["dropped"] += len(drop)
                stats["errors"] += bool(err)

    await asyncio.gather(*(one(r) for r in todo))
    print(f"추출: 대상 {len(rows)} · 이번 {stats['policies']}(이미 {len(done)}) · 남긴 값 {stats['kept']} · 원문에 없어 버린 값 {stats['dropped']}"
          f" · 오류 {stats['errors']}")


async def load(inp: Path, target: str | None) -> None:
    from core.config import settings
    from core.database import close_pool, get_conn, init_pool
    db = settings.database_url.rsplit("/", 1)[-1].split("?")[0]
    # 사본 DB(이름이 _exp로 끝남)가 기본 — 측정·게이트 전 값이 운영 DB에 들어가면 "이미 있음" 가드 때문에 다시 넣어도 안 고쳐진다(리뷰 M1).
    # 다른 DB는 --target에 그 이름을 그대로 적어야(사람 승인 뒤 실제 반영할 때)
    if not db.endswith("_exp") and target != db:
        raise SystemExit(f"대상 DB가 {db} — 사본(_exp)이 아니면 --target {db}로 확인하세요(측정·게이트 통과·사람 승인 뒤에만)")
    print(f"적재 대상 DB: {db}")
    rows = [json.loads(l) for l in inp.read_text(encoding="utf-8").splitlines() if l.strip()]
    await init_pool()
    n_ins = n_skip_has = n_skip_inactive = n_bad = 0
    try:
        async with get_conn() as conn:
            async with conn.transaction():
                for r in rows:
                    cur = await conn.fetchrow("SELECT status, raw_body, (SELECT count(*) FROM policy_param p WHERE p.policy_item_id = $1) AS n "
                                              "FROM policy_item WHERE id = $1", r["policy_id"])
                    if not cur or cur["status"] != "active":
                        n_skip_inactive += 1
                        continue
                    if cur["n"]:
                        n_skip_has += 1   # 이미 파라미터가 있음(사람이 고친 값일 수 있음) — 덮지 않는다
                        continue
                    keep, drop = validate_params(r["params"], cur["raw_body"])   # 넣을 때 다시 대조 — 그 사이 원문이 바뀌었으면 버린다
                    n_bad += len(drop)
                    for p in keep:
                        await conn.execute("INSERT INTO policy_param (policy_item_id, name, condition, value, unit) VALUES ($1, $2, $3, $4, $5)",
                                           r["policy_id"], p["name"], p.get("condition"), p["value"], p.get("unit"))
                        n_ins += 1
                # 파생 실행 표시 — 적재 검수가 "돌렸는데 값 없음"과 "안 돌림"을 가른다. 이번 파일이 지금 대상(숫자 있는 현행 정책) 전부를
                # 오류 없이 덮을 때만 남긴다 — 일부만(--limit)·오류 줄·그 사이 새 정책이 있으면 남기지 않아 검수가 계속 잡는다(재리뷰 M1)
                scope = {r["id"] for r in await conn.fetch("SELECT id FROM policy_item WHERE status = 'active' AND raw_body ~ '[0-9]'")}
                covered = {r["policy_id"] for r in rows if not r.get("error")}
                missing = scope - covered
                if missing:
                    print(f"파생 실행 표시 안 남김 — 대상 중 {len(missing)}개 정책이 파일에 없거나 오류(예: {sorted(missing)[:5]}) → extract 이어서 실행 후 다시 load")
                else:
                    await conn.execute("""INSERT INTO ops_data_marker (key, value, updated_at) VALUES ('policy_param_derive', $1, NOW())
                                          ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
                                       __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat())
                    print("파생 실행 표시 남김 — 대상 정책 전부 처리됨")
    finally:
        await close_pool()
    print(f"적재(DB {db}): 넣은 값 {n_ins} · 이미 파라미터 있어 건너뜀 {n_skip_has}개 정책 · 현행 아님 {n_skip_inactive} · 원문이 바뀌어 버림 {n_bad}")


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--out", required=True)
    e.add_argument("--limit", type=int, default=0)
    e.add_argument("--concurrency", type=int, default=4)
    lo = sub.add_parser("load")
    lo.add_argument("--in", dest="inp", required=True)
    lo.add_argument("--target", help="사본(_exp)이 아닌 DB에 넣을 때 그 DB 이름을 그대로(확인용)")
    a = ap.parse_args()
    if a.cmd == "extract":
        asyncio.run(extract(Path(a.out), a.limit, a.concurrency))
    else:
        asyncio.run(load(Path(a.inp), a.target))
    return 0


if __name__ == "__main__":
    sys.exit(main())
