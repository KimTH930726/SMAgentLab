"""④ 업무구분 폐지 5단계(10/22 사용자 승인) — 제휴서비스 아래 하위 영역 2개 + 지식·정책 이동.

설계: docs/tech/category-removal-design.md §3 "마이그레이션 순서" 4·5, 승인 목록 #3·#4·#5, 롤백 R2.
전제: 외부서비스 → 제휴서비스 합치기(merge_partner_domains.py)와 트리 코드(C1, 상위를 고르면 하위 포함 검색)가 먼저 들어가 있어야 한다.

하는 일(멱등):
  1. 하위 영역 생성: 제휴서비스 > 배달의민족, 쿠팡이츠(담당 비움 = 상위 담당 상속, 종류 domain).
  2. 지식 이동: 업무구분 값이 그 제휴사와 **정확히 같은** 행 — 모든 상태(컨플 페이지 교체·중복 판정이 영역 단위라 옛 버전도 같이 간다).
     그 업무구분 행(rag_knowledge_category)도 같이.
  3. 정책 이동: 분류 경로 1단이 그 제휴사와 정확히 같은 정책의 **logical_id 전체**(옛 버전 deprecated 포함 — 버전 이력·원문 확인이
     영역 + logical_id로 찾으므로 버전이 두 영역으로 갈라지면 안 된다, 리뷰 M2) + 그 정책의 검토 기록(policy_review_log)·정정 원장 항목.
점검: 하위별 사용 중 행 수 = 기대값(지식 전체 배민 44·쿠팡 41, 사용 중 정책 16·16 — 10/22 실측), 다르면 아무것도 안 바꾸고 멈춘다.

실행(컨테이너): python scripts/split_partner_subdomains.py            # 미리보기
               python scripts/split_partner_subdomains.py --apply    # 트랜잭션 하나
               python scripts/split_partner_subdomains.py --check    # --apply와 똑같이 돌린 뒤 되돌림(점검만, 흔적 없음)
되돌리기(R2): python scripts/split_partner_subdomains.py --rollback  # 이 스크립트가 만든 하위 2개의 모든 행을 상위로 되돌리고 하위 삭제
"""
import argparse
import asyncio
import sys

sys.path.insert(0, "/app")

from restructure_domains import ns_tables  # noqa: E402  (scripts/가 실행 경로)

PARENT = "제휴서비스"
# 하위 영역 이름 → (업무구분·분류 경로 1단 값 — 정확히 같은 것만, 기대 지식 행 수(모든 상태), 기대 사용 중 정책 수)
SUBS = {"배달의민족": ("1. 배달의민족", 44, 16), "쿠팡이츠": ("2. 쿠팡이츠", 41, 16)}
# 정정 원장에서 정책을 가리키는 대상: 정책 정정(narrative·param)은 target_id = 정책 행 id, 원문 확인(policy)은 target_id = logical_id
ROW_TARGETS = ["policy_param", "policy_narrative"]


async def plan(conn, parent_id: int) -> dict:
    out = {}
    for sub, (value, _, _) in SUBS.items():
        k = await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1 AND category = $2", parent_id, value)
        p = await conn.fetchval("""SELECT count(*) FROM policy_item WHERE namespace_id = $1 AND status <> 'deprecated'
                                   AND category_path[1] = $2""", parent_id, value)
        out[sub] = (k, p)
    return out


async def invalidate_cache(names: list[str]) -> None:
    try:
        from shared import cache as sem_cache
        for n in names:
            await sem_cache.invalidate_namespace(n)
    except Exception as e:   # noqa: BLE001
        print("캐시 비우기 실패(백엔드 재시작 또는 TTL로 해소):", e)


async def rollback(conn, parent_id: int) -> None:
    subs = await conn.fetch("SELECT id, name FROM ops_namespace WHERE parent_id = $1", parent_id)
    mine = [s for s in subs if s["name"] in SUBS]
    others = [s["name"] for s in subs if s["name"] not in SUBS]
    if others:   # 이 스크립트가 만들지 않은 하위(관리자가 따로 만든 것)는 건드리지 않는다 — 섞어 지우면 그 데이터·담당·연결이 사라진다(리뷰 M1)
        raise SystemExit(f"이 스크립트가 만들지 않은 하위 영역이 있습니다: {others} — 사람 확인 후 따로 처리(아무것도 안 바꿈)")
    if not mine:
        raise SystemExit("하위 영역 없음 — 되돌릴 것 없음")
    tables = await ns_tables(conn)
    ids = [s["id"] for s in mine]
    per = {t: await conn.fetchval(f"SELECT count(*) FROM {t} WHERE namespace_id = ANY($1::int[])", ids) for t in tables}
    print("상위로 되돌릴 행:", {t: n for t, n in per.items() if n})
    async with conn.transaction():
        # 하위의 모든 행(이동 뒤 새로 들어온 지식·대화·질의 기록 포함)을 상위로 — 영역을 지우면 CASCADE로 사라지므로 먼저 옮긴다
        for t in tables:
            await conn.execute(f"UPDATE {t} SET namespace_id = $1 WHERE namespace_id = ANY($2::int[])", parent_id, ids)
        await conn.execute("DELETE FROM ops_namespace WHERE id = ANY($1::int[])", ids)
    await invalidate_cache([PARENT, *[s["name"] for s in mine]])
    print("되돌림 완료 — 백엔드 재시작(프로세스 안 색인 캐시)")


async def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true")
    g.add_argument("--rollback", action="store_true")
    g.add_argument("--check", action="store_true")
    args = ap.parse_args()
    from core.database import get_conn, init_pool
    await init_pool()
    async with get_conn() as conn:
        parent = await conn.fetchrow("SELECT id FROM ops_namespace WHERE name = $1", PARENT)
        if parent is None:
            raise SystemExit(f"{PARENT} 영역 없음 — 합치기(merge_partner_domains.py) 먼저")
        pid = parent["id"]
        if args.rollback:
            await rollback(conn, pid)
            return
        existing = {r["name"]: r["parent_id"] for r in await conn.fetch(
            "SELECT name, parent_id FROM ops_namespace WHERE name = ANY($1::text[])", list(SUBS))}
        clash = {n: p for n, p in existing.items() if p != pid}
        if clash:
            raise SystemExit(f"같은 이름의 영역이 다른 곳에 있음: {clash} — 사람 확인")
        got = await plan(conn, pid)
        print(f"{PARENT}(id {pid}) 아래로 옮길 행 (지식 모든 상태, 사용 중 정책):", got)
        want = {s: (k, p) for s, (_, k, p) in SUBS.items()}
        if existing:   # 이미 일부·전부 적용됨 — 남은 이동만(점검은 트랜잭션 안에서 합계로)
            print("하위 영역이 이미 있음:", sorted(existing))
        elif got != want:
            raise SystemExit(f"기대와 다름 {want} — 데이터가 바뀌었으면 SUBS 기대값을 실측으로 고친 뒤 다시(아무것도 안 바꿈)")
        if not (args.apply or args.check):
            print("미리보기 — 바꾸지 않음(--apply)")
            return
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("SELECT id FROM ops_namespace WHERE id = $1 FOR UPDATE", pid)
            for sub, (value, k_want, p_want) in SUBS.items():
                sid = await conn.fetchval("""INSERT INTO ops_namespace (name, description, kind, parent_id) VALUES ($1, '', 'domain', $2)
                                             ON CONFLICT (name) DO UPDATE SET parent_id = EXCLUDED.parent_id RETURNING id""", sub, pid)
                k = int((await conn.execute("UPDATE rag_knowledge SET namespace_id = $1 WHERE namespace_id = $2 AND category = $3",
                                            sid, pid, value)).split()[-1])
                await conn.execute("UPDATE rag_knowledge_category SET namespace_id = $1 WHERE namespace_id = $2 AND name = $3", sid, pid, value)
                # 정책: 사용 중 행으로 logical_id를 정하고, 그 logical_id의 모든 버전·검토 기록·정정 항목을 같이 옮긴다
                lids = [r["logical_id"] for r in await conn.fetch(
                    """SELECT DISTINCT logical_id FROM policy_item WHERE namespace_id = $1 AND status <> 'deprecated'
                       AND category_path[1] = $2 AND logical_id IS NOT NULL""", pid, value)]
                p = int((await conn.execute("UPDATE policy_item SET namespace_id = $1 WHERE namespace_id = $2 AND logical_id = ANY($3::int[])",
                                            sid, pid, lids)).split()[-1])
                await conn.execute("UPDATE policy_review_log SET namespace_id = $1 WHERE namespace_id = $2 AND logical_id = ANY($3::int[])",
                                   sid, pid, lids)
                await conn.execute("""UPDATE ops_improvement_item SET namespace_id = $1 WHERE namespace_id = $2 AND (
                                          (target_type = 'policy' AND target_id = ANY($3::int[]))
                                       OR (target_type = ANY($4::text[]) AND target_id IN (SELECT id FROM policy_item WHERE logical_id = ANY($3::int[]))))""",
                                   sid, pid, lids, ROW_TARGETS)
                # 점검: 하위 영역 = 기대(다시 돌린 경우엔 이미 옮긴 것까지 합계로), 상위에 그 값이 남지 않음
                k_all = await conn.fetchval("SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1 AND category = $2", sid, value)
                p_all = await conn.fetchval("SELECT count(*) FROM policy_item WHERE namespace_id = $1 AND status <> 'deprecated'", sid)
                left = await conn.fetchval("""SELECT (SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1 AND category = $2)
                                                   + (SELECT count(*) FROM policy_item WHERE namespace_id = $1 AND category_path[1] = $2)""", pid, value)
                if (k_all, p_all, left) != (k_want, p_want, 0):
                    raise RuntimeError(f"점검 실패 {sub}: 지식 {k_all}≠{k_want} · 정책 {p_all}≠{p_want} · 상위 잔여 {left} — 되돌림")
                print(f"  {sub}(id {sid}): 이번에 지식 {k}·정책 행 {p}(버전 포함) 이동 → 지식 {k_all}·사용 중 정책 {p_all}")
            print(f"  상위 {PARENT}에 남은 지식 {await conn.fetchval('SELECT count(*) FROM rag_knowledge WHERE namespace_id = $1', pid)}")
        except BaseException:
            await tx.rollback()
            raise
        if args.check:
            await tx.rollback()
            print("점검 통과 — 되돌림(아무것도 안 바뀜)")
            return
        await tx.commit()
    await invalidate_cache([PARENT, *SUBS])
    print("적용 완료 — 백엔드 재시작(프로세스 안 색인 캐시)")


if __name__ == "__main__":
    asyncio.run(main())
