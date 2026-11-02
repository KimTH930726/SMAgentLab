"""시스템 축 개편(11/02, 사용자 "조직은 로그인·권한 뼈대만, 컬렉션은 시스템 축으로") — 조직 컬렉션(org:part:<id> / org:team:<id>)의
내용을 시스템 컬렉션(업무 영역, area='system', #105)으로 옮긴다. 조직 컬렉션은 폐지(#106이 빈 행을 지움) — 내용이 남아 #106이
남겨 둔 행(기동 로그 "[migrate #106] … 내용이 있어 남김")을 사람이 옮길 곳을 정한 뒤 이걸로 옮긴다.

합치기 본체는 restructure_domains.merge(표마다 namespace_id 옮김 · 연결 키 · 용어 수집 상태 · 남은 행 0 확인 · 원래 행 삭제)를 그대로 쓴다.
더해서 FK가 없는 컬렉션 id 칸(팀 적재 페이지의 넣은 곳 · 배치 기본 위치)도 옮긴다 — 안 옮기면 배치 롤백이 지운 컬렉션 이름으로 캐시를 비운다.
담당: 원래 담당 줄은 지운다(받는 시스템의 담당은 사람이 정한 값 그대로 — 겹치면 기본키 충돌).
지식 updated_at: 옮기는 동안 트리거를 끈다(안 끄면 옮긴 지식 전부가 "방금 바뀜"이 되어 원문 재등록 확인이 거짓으로 뜬다).

멈추는 경우(아무것도 안 바꿈): 받는 쪽이 시스템이 아님 · 보내는 쪽이 조직 컬렉션이 아님 · 진행 중인 적재 작업·팀 적재 배치 ·
  조직 컬렉션이 못 담는 것(정책·VOC·참조데이터)이 들어 있음.
점검: 표마다 받는 쪽(후) = 받는 쪽(전) + 보내는 쪽(전), 보내는 쪽(후) = 0 — 다르면 트랜잭션 전체 되돌림.

실행(컨테이너): python scripts/move_org_to_system.py --src org:part:902 --dst "홈페이지 시스템"           # 미리보기
               python scripts/move_org_to_system.py --src org:part:902 --dst "홈페이지 시스템" --apply   # 트랜잭션 하나
"""
import argparse
import asyncio
import sys

sys.path.insert(0, "/app")

from restructure_domains import counts, merge, ns_tables  # noqa: E402

# 조직 컬렉션이 못 담던 것(정책·VOC·참조데이터) — 들어 있으면 멈춘다(시스템 컬렉션으로 옮기면 안 되는 내용)
NOT_FOR_ORG = ("policy_item", "policy_doc_overview", "policy_review_log",
               "ops_voc_routing", "ops_email_analysis", "ops_voc_cluster",
               "ref_table", "ref_db_column", "ref_common_code")
JOB_DONE = ["completed", "failed", "cancelled"]

async def invalidate_all_cache() -> None:
    # 트랜잭션 밖, 실패해도 TTL로 사라진다(로그로 남김)
    try:
        from shared import cache as sem_cache
        print("답변 캐시 비움:", await sem_cache.invalidate_all(), "건")
    except Exception as e:   # noqa: BLE001
        print("캐시 비우기 실패(백엔드 재시작 또는 TTL로 해소):", e)


# FK 없이 컬렉션 id를 들고 있는 칸 — (표, 칸)
LOOSE_REFS = (("rag_import_page", "final_namespace_id"), ("rag_import_page", "ai_namespace_id"),
              ("rag_import_page", "proposed_namespace_id"), ("rag_import_page", "sample_ns"),
              ("rag_import_batch", "default_namespace_id"))   # AI 분포·원장 통계가 지운 id(#id)를 가리키지 않게(리뷰 L4)


async def check(conn, src: dict, dst: dict) -> list[str]:
    why = []
    if src["kind"] != "org_space":
        why.append(f"보내는 쪽이 조직 컬렉션이 아님({src['kind']})")
    if dst["kind"] != "domain" or dst["area"] != "system":
        why.append("받는 쪽이 시스템 컬렉션이 아님")
    for t in NOT_FOR_ORG:
        if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", t) and \
                await conn.fetchval(f"SELECT count(*) FROM {t} WHERE namespace_id = $1", src["id"]):
            why.append(f"{t}에 행이 있음")
    if await conn.fetchval("SELECT count(*) FROM rag_ingestion_job WHERE namespace_id = $1 AND NOT (status = ANY($2::text[]))",
                           src["id"], JOB_DONE):
        why.append("진행 중인 적재 작업")
    if await conn.fetchval("SELECT count(*) FROM rag_import_batch b WHERE b.status IN ('previewing', 'loading') AND EXISTS "
                           "(SELECT 1 FROM rag_import_page p WHERE p.batch_id = b.id AND p.final_namespace_id = $1)", src["id"]):
        why.append("진행 중인 팀 적재 배치")
    return why


async def main() -> None:
    from core.database import get_conn, init_pool
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="조직 컬렉션 이름(org:part:<id> / org:team:<id>)")
    ap.add_argument("--dst", required=True, help="시스템 컬렉션 이름")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    await init_pool()
    async with get_conn() as conn:
        rows = {r["name"]: dict(r) for r in await conn.fetch(
            "SELECT id, name, kind, area FROM ops_namespace WHERE name = ANY($1::text[])", [a.src, a.dst])}
        if a.src not in rows or a.dst not in rows:
            raise SystemExit(f"없는 컬렉션: {[n for n in (a.src, a.dst) if n not in rows]}")
        src, dst = rows[a.src], rows[a.dst]
        tables = [t for t in await ns_tables(conn) if t != "ops_namespace_owner"]
        before = await counts(conn, tables, [src["id"], dst["id"]])
        loose = {f"{t}.{c}": await conn.fetchval(f"SELECT count(*) FROM {t} WHERE {c} = $1", src["id"]) for t, c in LOOSE_REFS}
        moving = {t: v[src["id"]] for t, v in before.items() if v[src["id"]]}
        print(f"{a.src}(#{src['id']}) → {a.dst}(#{dst['id']}) · 옮길 행: {moving or '없음'} · 느슨한 참조: {loose}")
        why = await check(conn, src, dst)
        if why:
            raise SystemExit("멈춤: " + " · ".join(why))
        if not a.apply:
            print("미리보기 — 바꾸지 않음(--apply로 실행)")
            return
        async with conn.transaction():
            await conn.execute("SELECT id FROM ops_namespace WHERE id = ANY($1::int[]) FOR UPDATE", [src["id"], dst["id"]])
            why = await check(conn, src, dst)   # 잠근 뒤 한 번 더(미리보기와 실행 사이 틈)
            if why:
                raise SystemExit("멈춤(잠근 뒤): " + " · ".join(why))
            before = await counts(conn, tables, [src["id"], dst["id"]])
            for t, c in LOOSE_REFS:
                await conn.execute(f"UPDATE {t} SET {c} = $2 WHERE {c} = $1", src["id"], dst["id"])
            await conn.execute("DELETE FROM ops_namespace_owner WHERE namespace_id = $1", src["id"])
            await conn.execute("ALTER TABLE rag_knowledge DISABLE TRIGGER trg_knowledge_updated_at")
            try:
                await merge(conn, src["id"], dst["id"], tables)   # 남은 행 0 확인 + 보내는 쪽 컬렉션 행 삭제
            finally:
                await conn.execute("ALTER TABLE rag_knowledge ENABLE TRIGGER trg_knowledge_updated_at")
            after = {t: await conn.fetchval(f"SELECT count(*) FROM {t} WHERE namespace_id = $1", dst["id"]) for t in tables}
            bad = {t: (before[t][dst["id"]], before[t][src["id"]], after[t]) for t in tables
                   if after[t] != before[t][dst["id"]] + before[t][src["id"]]}
            if bad:
                raise RuntimeError(f"점검 실패(받는 쪽 전·보내는 쪽 전·후): {bad}")
        print("옮김 —", {t: after[t] for t in tables if after[t]})
    await invalidate_all_cache()


if __name__ == "__main__":
    asyncio.run(main())
