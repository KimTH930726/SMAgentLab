"""운영 반입용(10/23 사용자 지시) — 업무 컬렉션 "팀 공통"을 그 팀의 조직 컬렉션(팀 공통, `org:team:<id>`)으로 옮긴다.

배경: 조직 컬렉션(v2.160, #83)이 생기기 전엔 팀 노하우를 업무 컬렉션 "팀 공통"에 넣어 썼다. 이제 같은 자리가 조직 컬렉션이라 옮기고 빈 업무 컬렉션은 지운다.
합치기 본체는 restructure_domains.merge(표마다 namespace_id 옮김 · 연결 키 · 용어 수집 상태 · 남은 행 0 확인 · 원래 행 삭제)를 그대로 쓴다.

멈추는 경우(아무것도 안 바꿈) — 미리보기와, 잠근 뒤 트랜잭션 안에서 한 번 더 본다:
  · 조직 컬렉션이 못 담는 것(정책·VOC·참조데이터, 정책·참고표 정정 항목)이 들어 있음 — 조직 컬렉션은 지식 문서·용어만(#83). 사람이 먼저 업무 컬렉션으로 옮긴다.
  · 하위 컬렉션이 있음 · 진행 중인 적재 작업이 있음 · 받을 팀을 정할 수 없음(팀이 여럿인데 --team 없음).
담당: 조직 팀 공통은 담당 줄을 두지 않는다(트리거) — 그 팀 모든 파트가 담당. 원래 담당 줄은 백업 후 지운다.
지식 updated_at: 옮기는 UPDATE 동안 트리거를 끈다(트랜잭션 안 — 실패하면 같이 되돌아감). 안 끄면 옮긴 지식 전부가 "방금 바뀜"이 되어
  원문 재등록 확인(source_ledger "사용 중이 된 뒤 빠진 조각")이 거짓으로 뜬다(리뷰 10/23). 그동안 rag_knowledge 표가 잠긴다(수 초).
점검: 표마다 받는 쪽 = 옮기기 전 두 쪽 합(다르면 트랜잭션 전체 되돌림). 답변 캐시는 전부 비운다(1회성 — 조직 범위 설정이 켜져 있으면 모든 업무 답이 낡음).

실행(컨테이너): python scripts/move_team_common_to_org.py [--src 팀 공통] [--team <팀 id>]           # 미리보기
               python scripts/move_team_common_to_org.py ... --apply                               # 백업 + 옮기기, 트랜잭션 하나
되돌리기:      python scripts/move_team_common_to_org.py --rollback   # 백업의 컬렉션 행·담당·용어 수집 상태로 다시 만들고 백업한 행만 되돌린다
               (연결 표 ops_link의 키는 되돌리지 않는다 — 옮긴 뒤 조직 컬렉션에서 고친 지식의 새 버전도 조직 쪽에 남는다)
"""
import argparse
import asyncio
import sys

sys.path.insert(0, "/app")

from restructure_domains import counts, merge, ns_tables  # noqa: E402  (scripts/가 실행 경로)

TAG = "20261023"
B_ROWS = f"ns_move_org_backup_{TAG}"        # 옮긴 (표, 행 id)
B_NS = f"ns_move_org_backup_ns_{TAG}"       # 원래 업무 컬렉션 행 + 받는 쪽 id
B_OWN = f"ns_move_org_backup_owner_{TAG}"   # 원래 담당 줄
B_CFG = f"ns_move_org_backup_cfg_{TAG}"     # 원래 용어 수집 상태(ops_system_config, 키에 컬렉션 id — merge가 지운다)
BACKUPS = (B_ROWS, B_NS, B_OWN, B_CFG)
# 조직 컬렉션이 못 담는 것 — API도 조직 컬렉션이면 400으로 막는다(정책 가져오기·VOC 메일함·참조데이터, v2.160)
NOT_FOR_ORG = ("policy_item", "policy_doc_overview", "policy_review_log",
               "ops_voc_routing", "ops_email_analysis", "ops_voc_cluster",
               "ref_table", "ref_db_column", "ref_common_code")
# 정정 원장 중 정책·참고표 대상 — 조직 컬렉션엔 정책 정정을 못 낸다(improvement/router.py). 정책 행이 이미 지워졌어도 남는다
POLICY_IMPROVEMENT_TYPES = ["policy", "policy_narrative", "policy_param", "ref_table"]
JOB_DONE = ["completed", "failed", "cancelled"]
CFG_KEYS = ("glossary_mining_last_id:{}", "glossary_gap_done:{}")


async def table_exists(conn, name: str) -> bool:
    return await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", name)


async def invalidate_all_cache() -> None:
    # 트랜잭션 밖, 실패해도 TTL로 사라진다(로그로 남김)
    try:
        from shared import cache as sem_cache
        print("답변 캐시 비움:", await sem_cache.invalidate_all(), "건")
    except Exception as e:   # noqa: BLE001
        print("캐시 비우기 실패(백엔드 재시작 또는 TTL로 해소):", e)


async def pick_team(conn, team_id: int | None) -> int:
    teams = await conn.fetch("SELECT id, name FROM ops_team ORDER BY id")
    if team_id is not None:
        if not any(t["id"] == team_id for t in teams):
            raise SystemExit(f"팀 id {team_id} 없음 — 있는 팀: {[(t['id'], t['name']) for t in teams]}")
        return team_id
    if len(teams) != 1:
        raise SystemExit(f"팀이 {len(teams)}개 — --team <id>로 받을 팀을 정하세요: {[(t['id'], t['name']) for t in teams]}")
    return teams[0]["id"]


async def blockers(conn, src_id: int, src_counts: dict[str, int]) -> dict:
    """옮기면 안 되는 이유 — 비어 있으면 옮겨도 된다. 미리보기와 잠근 뒤에 같은 걸로 본다(리뷰 10/23: 잠금 전 숫자로만 판정하던 틈)."""
    b = {t: n for t in NOT_FOR_ORG if (n := src_counts.get(t))}
    if n := await conn.fetchval("SELECT count(*) FROM ops_improvement_item WHERE namespace_id = $1 AND target_type = ANY($2::text[])",
                                src_id, POLICY_IMPROVEMENT_TYPES):
        b["정책·참고표 정정 항목"] = n
    if n := await conn.fetchval("SELECT count(*) FROM ops_namespace WHERE parent_id = $1", src_id):
        b["하위 컬렉션"] = n
    if n := await conn.fetchval("SELECT count(*) FROM rag_ingestion_job WHERE namespace_id = $1 AND NOT (status = ANY($2::text[]))",
                                src_id, JOB_DONE):
        b["진행 중인 적재 작업"] = n
    return b


async def move_rows(conn, fn) -> None:
    """지식 updated_at 트리거를 끈 채 fn을 돌린다 — 같은 트랜잭션 안이라 실패하면 트리거 끄기도 같이 되돌아간다."""
    await conn.execute("ALTER TABLE rag_knowledge DISABLE TRIGGER trg_knowledge_updated_at")
    await fn()
    await conn.execute("ALTER TABLE rag_knowledge ENABLE TRIGGER trg_knowledge_updated_at")


async def rollback(conn, tables: list[str]) -> None:
    if not all([await table_exists(conn, t) for t in BACKUPS]):
        raise SystemExit("백업이 없습니다 — 되돌릴 것 없음")
    old = await conn.fetchrow(f"SELECT * FROM {B_NS}")
    dst = old["dst_id"]
    if await conn.fetchval("SELECT 1 FROM ops_namespace WHERE name = $1", old["name"]):
        raise SystemExit(f"'{old['name']}' 컬렉션이 이미 있습니다 — 이미 되돌렸거나 옮기지 않음")
    per = {r["tbl"]: r["n"] for r in await conn.fetch(f"SELECT tbl, count(*) AS n FROM {B_ROWS} GROUP BY tbl")}
    print("되돌릴 행(백업 기준):", per)
    async with conn.transaction():
        owners = [r["part_id"] for r in await conn.fetch(f"SELECT part_id FROM {B_OWN} ORDER BY part_id")]
        new_id = await conn.fetchval("""INSERT INTO ops_namespace (name, description, owner_part_id, kind, parent_id, created_by_user_id, created_at)
                                        VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
                                     old["name"], old["description"], owners[0] if owners else None, old["kind"],
                                     old["parent_id"], old["created_by_user_id"], old["created_at"])
        for p in owners:
            await conn.execute("INSERT INTO ops_namespace_owner (namespace_id, part_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", new_id, p)
        for r in await conn.fetch(f"SELECT key, value FROM {B_CFG}"):
            key = r["key"].rsplit(":", 1)[0] + f":{new_id}"
            await conn.execute("""INSERT INTO ops_system_config (key, value) VALUES ($1, $2)
                                  ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", key, r["value"])
        moved = {}

        async def _back():
            for t in tables:
                moved[t] = int((await conn.execute(f"""UPDATE {t} SET namespace_id = $1
                                       WHERE namespace_id = $2 AND id IN (SELECT row_id FROM {B_ROWS} WHERE tbl = $3)""", new_id, dst, t)).split()[-1])
        await move_rows(conn, _back)
        print("되돌린 행(실제):", {t: n for t, n in moved.items() if n}, "— 백업보다 적은 표는 그 사이 지워졌거나 다른 곳으로 옮겨진 행")
        for b in BACKUPS:
            await conn.execute(f"DROP TABLE {b}")
    await invalidate_all_cache()
    print(f"되돌림 완료 — '{old['name']}' = id {new_id}. 백엔드 재시작(프로세스 안 색인 캐시)")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="팀 공통", help="옮길 업무 컬렉션 이름")
    ap.add_argument("--team", type=int, help="받을 팀 id(팀이 하나면 생략)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true")
    g.add_argument("--rollback", action="store_true")
    args = ap.parse_args()
    from core.database import get_conn, init_pool
    await init_pool()
    async with get_conn() as conn:
        # 담당 줄은 따로 다룬다 — 받는 쪽(팀 공통)엔 담당 줄을 둘 수 없다(트리거)
        tables = [t for t in await ns_tables(conn) if t != "ops_namespace_owner"]
        if args.rollback:
            await rollback(conn, tables)
            return
        src = await conn.fetchrow("SELECT id, kind FROM ops_namespace WHERE name = $1", args.src)
        if src is None:
            print(f"'{args.src}' 컬렉션 없음 — 이미 옮겼거나 없음")
            return
        if src["kind"] != "domain":
            raise SystemExit(f"'{args.src}'는 업무 컬렉션이 아님(kind={src['kind']}) — 아무것도 안 바꿈")
        sid = src["id"]
        team_id = await pick_team(conn, args.team)
        dst_name = f"org:team:{team_id}"
        dst = await conn.fetchval("SELECT id FROM ops_namespace WHERE name = $1 AND kind = 'org_space'", dst_name)
        if dst is None:
            raise SystemExit(f"{dst_name} 조직 컬렉션 없음 — 백엔드를 한 번 기동해 #83이 만든 뒤 다시")
        before = await counts(conn, tables, [sid, dst])
        owners = [r["part_id"] for r in await conn.fetch(
            "SELECT part_id FROM ops_namespace_owner WHERE namespace_id = $1 ORDER BY part_id", sid)]
        links = await conn.fetchval("""SELECT count(*) FROM ops_link WHERE (from_type = 'domain' AND from_key = $1) OR (to_type = 'domain' AND to_key = $1)
                                       OR (from_type = 'section' AND from_key LIKE $1 || '|%') OR (to_type = 'section' AND to_key LIKE $1 || '|%')""", str(sid))
        print(f"'{args.src}'(id {sid}) → {dst_name}(id {dst}, 팀 {team_id})")
        print("  옮길 행:", {t: v[sid] for t, v in before.items() if v[sid]})
        print("  받는 쪽 행:", {t: v[dst] for t, v in before.items() if v[dst]})
        print("  같은 이름 용어:", await conn.fetchval("""SELECT count(*) FROM rag_glossary a JOIN rag_glossary b ON lower(a.term) = lower(b.term)
                                                     WHERE a.namespace_id = $1 AND b.namespace_id = $2""", sid, dst))
        print(f"  이 컬렉션을 가리키는 연결(같은 업무·부록 등 — 받는 쪽으로 따라감, 되돌리기는 안 함): {links}")
        print(f"  원래 담당 파트(백업 후 지움 — 팀 공통은 그 팀 모든 파트): {owners or '없음'}")
        if b := await blockers(conn, sid, {t: v[sid] for t, v in before.items()}):
            raise SystemExit(f"중단: {b} — 먼저 업무 컬렉션으로 옮기거나 끝난 뒤 다시(아무것도 안 바꿈)")
        if not args.apply:
            print("미리보기 — 바꾸지 않음(--apply)")
            return
        if any([await table_exists(conn, t) for t in BACKUPS]):
            raise SystemExit("백업 표가 이미 있습니다 — 전에 옮긴 적이 있음. 되돌리기(--rollback) 또는 백업 확인 뒤 다시(아무것도 안 바꿈)")
        async with conn.transaction():
            # 두 행을 잠그고 트랜잭션 안에서 다시 세고 다시 본다(미리보기 뒤 들어온 정책·하위 컬렉션·채팅 기록까지)
            await conn.execute("SELECT id FROM ops_namespace WHERE id = ANY($1::int[]) FOR UPDATE", [sid, dst])
            before = await counts(conn, tables, [sid, dst])
            if b := await blockers(conn, sid, {t: v[sid] for t, v in before.items()}):
                raise SystemExit(f"중단(미리보기 뒤 바뀜): {b} — 아무것도 안 바꿈")
            await conn.execute(f"CREATE TABLE {B_ROWS} (tbl TEXT NOT NULL, row_id BIGINT NOT NULL, PRIMARY KEY (tbl, row_id))")
            for t in tables:
                await conn.execute(f"INSERT INTO {B_ROWS} (tbl, row_id) SELECT $1, id FROM {t} WHERE namespace_id = $2", t, sid)
            await conn.execute(f"CREATE TABLE {B_NS} AS SELECT n.*, $2::int AS dst_id FROM ops_namespace n WHERE n.id = $1", sid, dst)
            await conn.execute(f"CREATE TABLE {B_OWN} AS SELECT * FROM ops_namespace_owner WHERE namespace_id = $1", sid)
            await conn.execute(f"CREATE TABLE {B_CFG} AS SELECT key, value FROM ops_system_config WHERE key = ANY($1::text[])",
                               [k.format(sid) for k in CFG_KEYS])
            await conn.execute("DELETE FROM ops_namespace_owner WHERE namespace_id = $1", sid)
            await move_rows(conn, lambda: merge(conn, sid, dst, tables))   # 돌려주는 값은 업무구분 병합 수뿐(표 #84 삭제로 항상 0)
            for t in tables:   # 점검: 받는 쪽 = 두 쪽 합
                want = before[t][sid] + before[t][dst]
                got = await conn.fetchval(f"SELECT count(*) FROM {t} WHERE namespace_id = $1", dst)
                if got != want:
                    raise RuntimeError(f"점검 실패 {t}: {got} != {want} — 되돌림")
            print("옮김 — 점검 통과 · 백업 행", await conn.fetchval(f"SELECT count(*) FROM {B_ROWS}"))
    await invalidate_all_cache()
    print("적용 완료 — 백엔드 재시작(프로세스 안 색인 캐시)")


if __name__ == "__main__":
    asyncio.run(main())
