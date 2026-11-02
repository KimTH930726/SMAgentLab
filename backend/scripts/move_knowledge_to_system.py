"""정책 컬렉션 안의 운영 지식을 시스템 컬렉션으로 옮긴다(11/02 D — 정책서 = 정책 컬렉션, 운영 지식 = 시스템 컬렉션, taxonomy-standard.md).

옮기는 것: 지식(rag_knowledge, 상태 무관) · 원문(rag_source_document) · 팀 적재의 넣은 곳(rag_import_page.final_namespace_id) ·
  배치 기본 위치 · 적재 규칙 — 정책 컬렉션엔 지식을 못 넣으므로(400) 옛 값이 남으면 다시 적재·되돌리기가 막힌다.
남기는 것: 정책서·용어집·참고표(정책 컬렉션 몫) · AI가 그때 고른 위치(ai/proposed, 이력).
지식 updated_at: 옮기는 동안 트리거를 끈다(안 끄면 옮긴 지식 전부가 "방금 바뀜"이 되어 원문 재등록 확인이 거짓으로 뜬다).

점검: 표마다 받는 쪽(후) = 받는 쪽(전) + 옮긴 수, 보내는 쪽 지식·원문 0 — 다르면 트랜잭션 전체 되돌림.
되돌리기: --apply가 옮긴 id를 --log 파일에 적고, --revert <파일>이 그 id만 원래 컬렉션으로 돌린다(게이트 실패 시).

실행(컨테이너): python scripts/move_knowledge_to_system.py --src 딜리버스 --dst "딜리버스 시스템"                       # 미리보기
               python scripts/move_knowledge_to_system.py --src 딜리버스 --dst "딜리버스 시스템" --apply --log eval_out/move_dlv.json
               python scripts/move_knowledge_to_system.py --revert eval_out/move_dlv.json
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")

# (표, 컬렉션 id 칸) — 지식·원문은 FK, 나머지는 FK 없는 칸
MOVES = (("rag_knowledge", "namespace_id"), ("rag_source_document", "namespace_id"),
         ("rag_import_page", "final_namespace_id"), ("rag_import_batch", "default_namespace_id"),
         ("rag_import_rule", "namespace_id"))
JOB_DONE = ["completed", "failed", "cancelled"]


async def invalidate_all_cache() -> None:
    # 트랜잭션 밖, 실패해도 TTL로 사라진다(로그로 남김)
    try:
        from shared import cache as sem_cache
        print("답변 캐시 비움:", await sem_cache.invalidate_all(), "건")
    except Exception as e:   # noqa: BLE001
        print("캐시 비우기 실패(백엔드 재시작 또는 TTL로 해소):", e)


async def check(conn, src: dict, dst: dict) -> list[str]:
    why = []
    if src["kind"] != "domain" or src["area"] != "policy":
        why.append("보내는 쪽이 정책 컬렉션이 아님")
    if dst["kind"] != "domain" or dst["area"] != "system":
        why.append("받는 쪽이 시스템 컬렉션이 아님")
    if await conn.fetchval("SELECT count(*) FROM rag_ingestion_job WHERE namespace_id = $1 AND NOT (status = ANY($2::text[]))",
                           src["id"], JOB_DONE):
        why.append("진행 중인 적재 작업")
    if await conn.fetchval("SELECT count(*) FROM rag_import_batch b WHERE b.status IN ('previewing', 'loading') AND EXISTS "
                           "(SELECT 1 FROM rag_import_page p WHERE p.batch_id = b.id AND p.final_namespace_id = $1)", src["id"]):
        why.append("진행 중인 팀 적재 배치")
    return why


async def ids_of(conn, ns_id: int) -> dict[str, list[int]]:
    return {f"{t}.{c}": [r["id"] for r in await conn.fetch(f"SELECT id FROM {t} WHERE {c} = $1 ORDER BY id", ns_id)]
            for t, c in MOVES}


async def set_ns(conn, moved: dict[str, list[int]], ns_id: int) -> None:
    await conn.execute("ALTER TABLE rag_knowledge DISABLE TRIGGER trg_knowledge_updated_at")
    try:
        for key, ids in moved.items():
            t, c = key.split(".")
            if ids:
                await conn.execute(f"UPDATE {t} SET {c} = $1 WHERE id = ANY($2::int[])", ns_id, ids)
    finally:
        await conn.execute("ALTER TABLE rag_knowledge ENABLE TRIGGER trg_knowledge_updated_at")


async def revert(conn, path: str) -> None:
    log = json.loads(Path(path).read_text(encoding="utf-8"))
    if "moved" not in log:   # --apply가 기록 전에 멈춤(빈 자리 파일) — 옮긴 것이 없다(재리뷰 L2)
        raise SystemExit(f"{path}: 옮기기가 끝나지 않은 기록 — 되돌릴 것 없음")
    async with conn.transaction():
        # 옮긴 뒤 다른 곳으로 또 옮겨진 행은 건드리지 않는다(받는 쪽에 그대로 있는 것만)
        still = {}
        for key, ids in log["moved"].items():
            t, c = key.split(".")
            still[key] = [r["id"] for r in await conn.fetch(
                f"SELECT id FROM {t} WHERE id = ANY($1::int[]) AND {c} = $2", ids, log["dst_id"])]
        # 옮긴 뒤 받는 쪽에서 다시 조각낸 조각(새 id, 같은 원문)도 원문을 따라 돌린다 — 안 그러면 원문·조각이 갈린다(리뷰 M2)
        docs = still["rag_source_document.namespace_id"]
        extra = [r["id"] for r in await conn.fetch(
            "SELECT id FROM rag_knowledge WHERE source_document_id = ANY($1::int[]) AND namespace_id = $2 AND NOT (id = ANY($3::int[]))",
            docs, log["dst_id"], still["rag_knowledge.namespace_id"])]
        still["rag_knowledge.namespace_id"] += extra
        await set_ns(conn, still, log["src_id"])
        split = await conn.fetchval("SELECT count(*) FROM rag_knowledge k JOIN rag_source_document d ON d.id = k.source_document_id "
                                    "WHERE (d.id = ANY($1::int[]) OR k.id = ANY($2::int[])) AND k.namespace_id <> d.namespace_id",
                                    docs, still["rag_knowledge.namespace_id"])   # 되돌린 조각 쪽도(그 사이 원문만 옮겨진 경우, 재리뷰 L1)
        if split:
            raise RuntimeError(f"되돌린 원문의 조각 {split}개가 다른 컬렉션에 있음 — 되돌리지 않음")
        if extra:
            print(f"옮긴 뒤 받는 쪽에서 새로 만든 조각 {len(extra)}개도 원문을 따라 돌림")
    print(f"되돌림 {log['dst']} → {log['src']}:", {k: len(v) for k, v in still.items()},
          "· 받는 쪽에 없어 건너뜀:", {k: len(log["moved"][k]) - len(v) for k, v in still.items() if len(log["moved"][k]) != len(v)})


async def main() -> None:
    from core.database import get_conn, init_pool
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", help="정책 컬렉션 이름")
    ap.add_argument("--dst", help="시스템 컬렉션 이름")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--log", help="--apply: 옮긴 id를 적을 파일(되돌리기용)")
    ap.add_argument("--revert", help="--apply가 남긴 파일 — 그 id만 원래 컬렉션으로")
    a = ap.parse_args()
    await init_pool()
    async with get_conn() as conn:
        if a.revert:
            await revert(conn, a.revert)
            await invalidate_all_cache()
            return
        if not (a.src and a.dst):
            raise SystemExit("--src와 --dst가 필요(또는 --revert)")
        if a.apply and not a.log:
            raise SystemExit("--apply에는 --log가 필요(되돌리기용)")
        rows = {r["name"]: dict(r) for r in await conn.fetch(
            "SELECT id, name, kind, area FROM ops_namespace WHERE name = ANY($1::text[])", [a.src, a.dst])}
        if a.src not in rows or a.dst not in rows:
            raise SystemExit(f"없는 컬렉션: {[n for n in (a.src, a.dst) if n not in rows]}")
        src, dst = rows[a.src], rows[a.dst]
        moving = await ids_of(conn, src["id"])
        active = await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1 AND status = 'active'", src["id"])
        print(f"{a.src}(#{src['id']}) → {a.dst}(#{dst['id']}) · 옮길 행:", {k: len(v) for k, v in moving.items()}, f"· 활성 지식 {active}")
        why = await check(conn, src, dst)
        if why:
            raise SystemExit("멈춤: " + " · ".join(why))
        if not a.apply:
            print("미리보기 — 바꾸지 않음(--apply --log <파일>로 실행)")
            return
        log = Path(a.log)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("{}", encoding="utf-8")   # 옮기기 전에 쓸 수 있는지 확인 — 옮긴 뒤 기록 실패면 되돌릴 수 없다(리뷰 L1)
        async with conn.transaction():
            await conn.execute("SELECT id FROM ops_namespace WHERE id = ANY($1::int[]) FOR UPDATE", [src["id"], dst["id"]])
            why = await check(conn, src, dst)   # 잠근 뒤 한 번 더(미리보기와 실행 사이 틈)
            if why:
                raise SystemExit("멈춤(잠근 뒤): " + " · ".join(why))
            moving = await ids_of(conn, src["id"])
            before = {k: len(v) for k, v in (await ids_of(conn, dst["id"])).items()}
            await set_ns(conn, moving, dst["id"])
            after_dst = {k: len(v) for k, v in (await ids_of(conn, dst["id"])).items()}
            after_src = {k: len(v) for k, v in (await ids_of(conn, src["id"])).items()}
            bad = {k: (before[k], len(moving[k]), after_dst[k]) for k in before if after_dst[k] != before[k] + len(moving[k])}
            bad.update({k: ("보내는 쪽 남음", v) for k, v in after_src.items() if v})
            if bad:
                raise RuntimeError(f"점검 실패: {bad}")
        log.write_text(json.dumps({"src": a.src, "src_id": src["id"], "dst": a.dst, "dst_id": dst["id"], "moved": moving},
                                          ensure_ascii=False), encoding="utf-8")
        print("옮김 —", {k: len(v) for k, v in moving.items()}, "· 기록:", a.log)
    await invalidate_all_cache()


if __name__ == "__main__":
    asyncio.run(main())
