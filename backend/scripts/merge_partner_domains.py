"""④ 업무구분 폐지 1·2단계(10/22 사용자 승인) — 백업 테이블 + 외부서비스를 제휴서비스로 합치기.

설계: docs/tech/category-removal-design.md §3 "마이그레이션 순서" 1·2, 승인 목록 #1·#2, 롤백 R1.
합치기 본체는 restructure_domains.py의 ns_tables·counts·merge를 그대로 쓴다(10/20 딜리버스 DB 합치기와 같은 검증된 경로).

하는 일(멱등 — 다시 돌려도 같은 결과):
  1. 백업(추가만): 업무구분 표 통째 · 지식마다 (id, 영역, 업무구분)(트리 이동 되돌림 매핑 겸용) · 업무구분 추천 프롬프트 ·
     합칠 영역의 표별 행 id + 두 영역 행 자체(이름·설명·담당). 이미 있으면 다시 만들지 않는다(첫 백업이 원본).
  2. 합치기: 외부서비스 → 제휴서비스. 담당 파트는 받는 쪽이 비어 있으면 옮긴다. 점검 = 표마다 두 영역 합 = 합친 뒤, 옛 id 가리키는 행 0
     (실패면 트랜잭션 전체 되돌림). 의미 캐시는 두 이름 모두 비운다.

실행(컨테이너): python scripts/merge_partner_domains.py            # 미리보기(바꾸지 않음)
               python scripts/merge_partner_domains.py --apply    # 백업 + 합치기, 트랜잭션 하나
되돌리기(R1): python scripts/merge_partner_domains.py --rollback  # 백업의 이름·담당으로 영역을 다시 만들고 백업한 행만 되돌린다
"""
import argparse
import asyncio
import sys

sys.path.insert(0, "/app")

from restructure_domains import counts, merge, ns_tables  # noqa: E402  (scripts/가 실행 경로)

SRC, DST = "외부서비스", "제휴서비스"
TAG = "20261022"
B_CAT = f"rag_knowledge_category_backup_{TAG}"         # 업무구분 표 통째
B_VAL = "rag_knowledge_category_value_backup"          # 지식마다 (id, namespace_id, category) — 업무구분 칸 삭제(C6) 뒤 복원용
B_PROMPT = "ops_prompt_category_suggest_backup"
B_ROWS = f"ns_merge_backup_{TAG}"                       # 합칠 영역의 (표, 행 id)
B_NS = f"ns_merge_backup_ns_{TAG}"                      # 두 영역 행 자체(이름·설명·담당 — 되돌릴 때 양쪽 담당을 원래대로)


async def table_exists(conn, name: str) -> bool:
    return await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", name)


async def backup(conn, src_id: int, dst_id: int, tables: list[str]) -> dict:
    """백업 테이블을 만든다(이미 있으면 그대로 — 첫 백업이 원본). 만든 표·행 수를 돌려준다."""
    made = {}
    if not await table_exists(conn, B_CAT):
        await conn.execute(f"CREATE TABLE {B_CAT} AS SELECT * FROM rag_knowledge_category")
        made[B_CAT] = await conn.fetchval(f"SELECT count(*) FROM {B_CAT}")
    if not await table_exists(conn, B_VAL):
        await conn.execute(f"CREATE TABLE {B_VAL} AS SELECT id, namespace_id, category FROM rag_knowledge")
        made[B_VAL] = await conn.fetchval(f"SELECT count(*) FROM {B_VAL}")
    if not await table_exists(conn, B_PROMPT):
        await conn.execute(f"CREATE TABLE {B_PROMPT} AS SELECT * FROM ops_prompt WHERE func_key = 'category_suggest'")
        made[B_PROMPT] = await conn.fetchval(f"SELECT count(*) FROM {B_PROMPT}")
    if not await table_exists(conn, B_ROWS):
        await conn.execute(f"CREATE TABLE {B_ROWS} (tbl TEXT NOT NULL, row_id BIGINT NOT NULL, PRIMARY KEY (tbl, row_id))")
        for t in tables:
            await conn.execute(f"INSERT INTO {B_ROWS} (tbl, row_id) SELECT $1, id FROM {t} WHERE namespace_id = $2", t, src_id)
        made[B_ROWS] = await conn.fetchval(f"SELECT count(*) FROM {B_ROWS}")
    if not await table_exists(conn, B_NS):
        await conn.execute(f"CREATE TABLE {B_NS} AS SELECT * FROM ops_namespace WHERE id = ANY($1::int[])", [src_id, dst_id])
        made[B_NS] = await conn.fetchval(f"SELECT count(*) FROM {B_NS}")
    return made


async def invalidate_cache(names: list[str]) -> None:
    # 캐시는 영역 이름이 키 — 트랜잭션 밖, 실패해도 TTL로 사라진다(로그로 남김)
    try:
        from shared import cache as sem_cache
        for n in names:
            await sem_cache.invalidate_namespace(n)
    except Exception as e:   # noqa: BLE001
        print("캐시 비우기 실패(백엔드 재시작 또는 TTL로 해소):", e)


async def rollback(conn, tables: list[str]) -> None:
    """R1 — 백업의 이름·설명·담당으로 외부서비스를 새 id로 다시 만들고, 백업에 적힌 행만 그 id로 되돌린다.
    합친 뒤 새로 들어온 행은 백업에 없어 제휴서비스에 남는다(개수를 먼저 보여 주고 사람이 판단)."""
    if not (await table_exists(conn, B_ROWS) and await table_exists(conn, B_NS)):
        raise SystemExit("백업이 없습니다 — 되돌릴 것 없음")
    old = await conn.fetchrow(f"SELECT name, description, owner_part_id, kind FROM {B_NS} WHERE name = $1", SRC)
    dst = await conn.fetchval("SELECT id FROM ops_namespace WHERE name = $1", DST)
    if old is None or dst is None:
        raise SystemExit(f"백업에 {SRC} 행이 없거나 {DST} 영역이 없습니다 — 되돌릴 수 없음(아무것도 안 바꿈)")
    dst_owner = await conn.fetchval(f"SELECT owner_part_id FROM {B_NS} WHERE name = $1", DST)
    if await conn.fetchval("SELECT 1 FROM ops_namespace WHERE name = $1", old["name"]):
        raise SystemExit(f"{old['name']} 영역이 이미 있습니다 — 이미 되돌렸거나 합치지 않음")
    # 백업한 행이 그 사이 제휴서비스 밖(하위 영역 등)으로 옮겨졌으면 일부만 되돌리고 백업을 지우게 된다 → 먼저 R2(하위 되돌리기) 하라고 멈춘다(리뷰 M3)
    elsewhere = {t: n for t in tables if (n := await conn.fetchval(
        f"SELECT count(*) FROM {t} WHERE namespace_id <> $1 AND id IN (SELECT row_id FROM {B_ROWS} WHERE tbl = $2)", dst, t))}
    if elsewhere:
        raise SystemExit(f"백업한 행 일부가 {DST} 밖에 있습니다 {elsewhere} — 하위 영역이면 split_partner_subdomains.py --rollback 먼저(아무것도 안 바꿈)")
    per = {r["tbl"]: r["n"] for r in await conn.fetch(f"SELECT tbl, count(*) AS n FROM {B_ROWS} GROUP BY tbl")}
    print("되돌릴 행(백업 기준):", per)
    async with conn.transaction():
        new_id = await conn.fetchval("""INSERT INTO ops_namespace (name, description, owner_part_id, kind)
                                        VALUES ($1, $2, $3, $4) RETURNING id""",
                                     old["name"], old["description"], old["owner_part_id"], old["kind"])
        moved = {}
        for t in tables:
            moved[t] = int((await conn.execute(f"""UPDATE {t} SET namespace_id = $1
                                   WHERE namespace_id = $2 AND id IN (SELECT row_id FROM {B_ROWS} WHERE tbl = $3)""", new_id, dst, t)).split()[-1])
        print("되돌린 행(실제):", {t: n for t, n in moved.items() if n}, "— 백업보다 적은 표는 그 사이 지워진 행(질의 기록 정리 등)")
        await conn.execute("UPDATE ops_namespace SET owner_part_id = $2 WHERE id = $1", dst, dst_owner)
        await conn.execute(f"DROP TABLE {B_ROWS}")
        await conn.execute(f"DROP TABLE {B_NS}")
    await invalidate_cache([SRC, DST])
    print(f"되돌림 완료 — {old['name']} = id {new_id}. 백엔드 재시작(프로세스 안 색인 캐시)")


async def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true")
    g.add_argument("--rollback", action="store_true")
    args = ap.parse_args()
    from core.database import get_conn, init_pool
    await init_pool()
    async with get_conn() as conn:
        tables = await ns_tables(conn)
        if args.rollback:
            await rollback(conn, tables)
            return
        ns = {r["name"]: r for r in await conn.fetch("SELECT id, name, owner_part_id FROM ops_namespace")}
        if SRC not in ns:
            print(f"{SRC} 영역 없음 — 이미 합쳤음")
            return
        if DST not in ns:
            raise SystemExit(f"{DST} 영역 없음 — 받을 영역이 없어 합칠 수 없음")
        src, dst = ns[SRC]["id"], ns[DST]["id"]
        before = await counts(conn, tables, [src, dst])
        print(f"{SRC}(id {src}) → {DST}(id {dst})")
        print("  옮길 행:", {t: v[src] for t, v in before.items() if v[src]})
        print("  받는 쪽 행:", {t: v[dst] for t, v in before.items() if v[dst]})
        print("  같은 이름 용어:", await conn.fetchval("""SELECT count(*) FROM rag_glossary a JOIN rag_glossary b ON lower(a.term) = lower(b.term)
                                                     WHERE a.namespace_id = $1 AND b.namespace_id = $2""", src, dst))
        print(f"  담당 파트: {SRC}={ns[SRC]['owner_part_id']} · {DST}={ns[DST]['owner_part_id']}")
        if not args.apply:
            print("미리보기 — 바꾸지 않음(--apply)")
            return
        async with conn.transaction():
            # 두 영역 행을 잠그고 트랜잭션 안에서 다시 센다(미리보기 뒤 들어온 채팅 기록까지 — restructure_domains와 같은 이유)
            await conn.execute("SELECT id FROM ops_namespace WHERE id = ANY($1::int[]) FOR UPDATE", [src, dst])
            made = await backup(conn, src, dst, tables)
            print("백업:", made or "이미 있음(첫 백업 유지)")
            before = await counts(conn, tables, [src, dst])
            owner = ns[SRC]["owner_part_id"]
            report = await merge(conn, src, dst, tables)
            if owner is not None:
                await conn.execute("UPDATE ops_namespace SET owner_part_id = $2 WHERE id = $1 AND owner_part_id IS NULL", dst, owner)
            for t in tables:   # 점검: 합친 영역 = 두 영역 합(업무구분 같은 이름 병합만 예외)
                want = before[t][src] + before[t][dst] - (report["카테고리 병합(같은 이름)"] if t == "rag_knowledge_category" else 0)
                got = await conn.fetchval(f"SELECT count(*) FROM {t} WHERE namespace_id = $1", dst)
                if got != want:
                    raise RuntimeError(f"점검 실패 {t}: {got} != {want} — 되돌림")
            print("합치기 결과:", report)
    await invalidate_cache([SRC, DST])
    print("적용 완료 — 백엔드 재시작(프로세스 안 색인 캐시)")


if __name__ == "__main__":
    asyncio.run(main())
