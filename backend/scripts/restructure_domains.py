"""업무 영역·조직 재구성 (2026-10-20, 사용자 결정) — 지식은 업무 영역 중심, 파트는 사람 조직(팀 > 파트)·권한만.

하는 일(PLAN 그대로, 멱등):
  1. 같은 업무 영역 합치기: 지식 공간("온라인스토어 DB")을 정책서 영역("온라인스토어")으로 — namespace_id를 가진 모든 표의 행을 옮긴다.
     충돌: 업무구분(카테고리) 같은 이름은 하나로(지식의 category는 이름이라 그대로 맞음), VOC 메일함·분석 중복은 중단(사람 확인).
     연결 표(ops_link)의 영역 id 글자 키, 용어 수집 상태(ops_system_config 키에 영역 id)도 옮긴다.
  2. 영역 이름 정리("팀 공통 DB" → "팀 공통" 등) — DB는 모두 id로 가리켜 이름만 바뀐다(캐시는 이름 키라 비운다).
  3. 상위 영역(kind='group', 데이터 없음)과 트리(parent_id).
  4. 조직: 팀 > 파트, 사용자 소속 이동, 영역 담당 파트, VOC 담당 연결. 옛 파트(영역 이름이던 것·슈퍼어드민)는 비운 뒤 지운다.
점검(적용 전·후): 표마다 (합칠 두 영역 행 수 합) = (합친 뒤 행 수) — 카테고리 병합으로 줄어든 수만 예외. 옛 영역을 가리키는 행 0.

실행(컨테이너): python scripts/restructure_domains.py            # 미리보기(바꾸지 않음)
               python scripts/restructure_domains.py --apply    # 트랜잭션 하나, 점검 실패면 전부 되돌림
"""
import argparse
import asyncio
import json
import sys

sys.path.insert(0, "/app")

MERGE = {"온라인스토어 DB": "온라인스토어", "딜리버스 DB": "딜리버스"}          # 지식 공간 → 같은 업무 영역(정책서)
RENAME = {"팀 공통 DB": "팀 공통", "외부서비스DB": "외부서비스"}
GROUPS = {   # 상위 영역 → 업무 영역(초안, 10/20 — 관리자 화면에서 슈퍼어드민이 바꾼다)
    "고객": ["회원", "멤버십 서비스"],
    "결제": ["스타벅스카드"],
    "혜택": ["리워드", "쿠폰", "챌린지"],
    "주문": ["주문 서비스", "제휴 주문 서비스"],
    "온라인 서비스": ["온라인스토어", "딜리버스", "외부서비스"],
    "제휴": ["제휴서비스"],
    "매장·영업": ["영업시스템", "영업연계시스템", "POS"],
    "공통": ["팀 공통"],
}
TEAMS = {"스타벅스CSP팀": ["온라인서비스 파트", "온라인스토어 파트"]}
OWNERS = {"딜리버스": "온라인서비스 파트", "외부서비스": "온라인서비스 파트", "온라인스토어": "온라인스토어 파트"}
MOVE_USERS = {"딜리버스": "온라인서비스 파트", "외부서비스": "온라인서비스 파트"}   # 옛 파트 → 새 파트
DROP_PARTS = ["딜리버스", "외부서비스", "슈퍼어드민"]                               # 옛 파트(영역 이름·권한이던 것)
NO_PART_ROLES = ("admin",)                                                        # 슈퍼어드민 = 역할, 소속 없음


async def ns_tables(conn) -> list[str]:
    rows = await conn.fetch("""
        SELECT c.conrelid::regclass::text AS tbl FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey)
        WHERE c.contype = 'f' AND c.confrelid = 'ops_namespace'::regclass AND a.attname = 'namespace_id' ORDER BY 1""")
    return [r["tbl"] for r in rows]


async def counts(conn, tables, ids) -> dict:
    return {t: {i: await conn.fetchval(f"SELECT count(*) FROM {t} WHERE namespace_id = $1", i) for i in ids} for t in tables}


async def merge(conn, src: int, dst: int, tables: list[str]) -> dict:
    report = {}
    # 업무구분 같은 이름: 옮길 쪽을 지운다(지식은 category 이름으로 가리켜 그대로 맞는다)
    report["카테고리 병합(같은 이름)"] = int((await conn.execute("""
        DELETE FROM rag_knowledge_category s USING rag_knowledge_category d
        WHERE s.namespace_id = $1 AND d.namespace_id = $2 AND s.name = d.name""", src, dst)).split()[-1])
    for t, key in (("ops_voc_routing", "mailbox_upn"), ("ops_email_analysis", "source_message_id")):
        dup = await conn.fetchval(f"""SELECT count(*) FROM {t} s JOIN {t} d ON d.namespace_id = $2 AND d.{key} = s.{key}
                                       WHERE s.namespace_id = $1""", src, dst)
        if dup:
            raise RuntimeError(f"{t}: 같은 {key} {dup}건 — 사람 확인 필요(합치지 않음)")
    for t in tables:
        await conn.execute(f"UPDATE {t} SET namespace_id = $2 WHERE namespace_id = $1", src, dst)
    # 연결 표: 영역 id가 글자 키로 들어 있다('domain' = id, 'section' = "id|경로"). 종류 이름은 마이그레이션 #78이 'part'→'domain'으로 바꾼 뒤
    # (기동이 먼저라 이 스크립트가 돌 땐 이미 'domain')
    # 두 영역에 같은 연결이 이미 있으면 키를 바꾸는 순간 유일 제약(종류·키·관계·대상)에 걸린다 → 받는 쪽에 없는 것만 옮기고 겹친 것은 지운다.
    # 합치며 자기 자신을 가리키게 된 연결(예: 두 영역 사이 '같은 업무')도 지운다 — scope.py가 연결 있음으로 보고 "전부" 범위를 꺼 버린다(리뷰 L3)
    for col_t, col_k, oth_t, oth_k in (("from_type", "from_key", "to_type", "to_key"), ("to_type", "to_key", "from_type", "from_key")):
        for typ, new_key, match in (("domain", "$2", "l.{k} = $1"), ("section", "$2 || substr(l.{k}, length($1) + 1)", "l.{k} LIKE $1 || '|%'")):
            nk, cond = new_key.replace("l.{k}", f"l.{col_k}"), match.replace("{k}", col_k)
            await conn.execute(f"""
                UPDATE ops_link l SET {col_k} = {nk.replace('l.' + col_k, col_k)}
                WHERE l.{col_t} = '{typ}' AND {cond}
                  AND NOT EXISTS (SELECT 1 FROM ops_link d WHERE d.{col_t} = '{typ}' AND d.{col_k} = {nk}
                                  AND d.relation = l.relation AND d.{oth_t} = l.{oth_t} AND d.{oth_k} = l.{oth_k})""", str(src), str(dst))
            await conn.execute(f"DELETE FROM ops_link l WHERE l.{col_t} = '{typ}' AND {cond}", str(src))
    await conn.execute("DELETE FROM ops_link WHERE from_type = to_type AND from_key = to_key")
    # 용어 수집 상태: 옮겨 온 질의 기록을 다시 보게 마지막 id는 작은 쪽, 처리한 질문은 합친다
    a = await conn.fetchval("SELECT value FROM ops_system_config WHERE key = $1", f"glossary_mining_last_id:{src}")
    b = await conn.fetchval("SELECT value FROM ops_system_config WHERE key = $1", f"glossary_mining_last_id:{dst}")
    if a is not None or b is not None:
        low = str(min(int(x) for x in (a, b) if x is not None))
        await conn.execute("""INSERT INTO ops_system_config (key, value) VALUES ($1, $2)
                              ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""", f"glossary_mining_last_id:{dst}", low)
    ga = json.loads(await conn.fetchval("SELECT value FROM ops_system_config WHERE key = $1", f"glossary_gap_done:{src}") or "{}")
    gb = json.loads(await conn.fetchval("SELECT value FROM ops_system_config WHERE key = $1", f"glossary_gap_done:{dst}") or "{}")
    if ga:
        await conn.execute("""INSERT INTO ops_system_config (key, value) VALUES ($1, $2)
                              ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value""",
                           f"glossary_gap_done:{dst}", json.dumps({**ga, **gb}, ensure_ascii=False))
    await conn.execute("DELETE FROM ops_system_config WHERE key = ANY($1::text[])",
                       [f"glossary_mining_last_id:{src}", f"glossary_gap_done:{src}"])
    left = {t: await conn.fetchval(f"SELECT count(*) FROM {t} WHERE namespace_id = $1", src) for t in tables}
    if any(left.values()):
        raise RuntimeError(f"옮기지 못한 행: {left}")
    await conn.execute("DELETE FROM ops_namespace WHERE id = $1", src)
    return report


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    from core.database import get_conn, init_pool
    await init_pool()
    async with get_conn() as conn:
        tables = await ns_tables(conn)
        ns = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM ops_namespace")}
        merges = [(s, d) for s, d in MERGE.items() if s in ns and d in ns]
        print("합칠 영역:", [f"{s} → {d}" for s, d in merges] or "없음(이미 끝남)")
        before = await counts(conn, tables, [ns[x] for pair in merges for x in pair])
        for s, d in merges:
            moved = {t: before[t][ns[s]] for t in tables if before[t][ns[s]]}
            print(f"  {s} → {d}: 옮길 행 {moved}")
        dup_terms = {}
        for s, d in merges:
            dup_terms[f"{s}→{d}"] = await conn.fetchval("""SELECT count(*) FROM rag_glossary a JOIN rag_glossary b ON lower(a.term) = lower(b.term)
                                                            WHERE a.namespace_id = $1 AND b.namespace_id = $2""", ns[s], ns[d])
        print("합친 뒤 같은 이름 용어(둘 다 남김, 출처 영역별 정의):", dup_terms)
        print("이름 정리:", {k: v for k, v in RENAME.items() if k in ns})
        print("상위 영역:", list(GROUPS), "· 조직:", TEAMS, "· 담당:", OWNERS)
        users = await conn.fetch("SELECT u.username, u.role, p.name AS part FROM ops_user u LEFT JOIN ops_part p ON p.id = u.part_id ORDER BY u.id")
        print("사용자 소속 변경:", [(u["username"], u["part"], MOVE_USERS.get(u["part"]) if u["role"] not in NO_PART_ROLES else None)
                                for u in users])
        if not args.apply:
            print("미리보기 — 바꾸지 않음(--apply)")
            return

        async with conn.transaction():
            # 합칠 영역 행을 잠그고 행 수를 트랜잭션 안에서 다시 센다 — 미리보기 뒤 들어온 채팅 기록이 옮겨지기 전에 지워지거나,
            # 점검이 거짓 실패하지 않게(리뷰 L4a). 백엔드는 켜 둔 채 돌려도 되지만 그 영역 쓰기는 끝날 때까지 기다린다.
            await conn.execute("SELECT id FROM ops_namespace WHERE id = ANY($1::int[]) FOR UPDATE",
                               [ns[x] for pair in merges for x in pair])
            before = await counts(conn, tables, [ns[x] for pair in merges for x in pair])
            reports = {}
            for s, d in merges:
                reports[f"{s}→{d}"] = await merge(conn, ns[s], ns[d], tables)
            for old, new in RENAME.items():
                await conn.execute("UPDATE ops_namespace SET name = $2 WHERE name = $1", old, new)
            # 상위 영역 + 트리
            for g, children in GROUPS.items():
                # 같은 이름의 데이터 있는 영역을 묶음으로 바꾸면 그 데이터가 목록·검색에서 사라진다(리뷰 L4b) — 그땐 멈춘다
                same = await conn.fetchrow("SELECT id, kind FROM ops_namespace WHERE name = $1", g)
                if same and same["kind"] != "group":
                    raise RuntimeError(f"상위 영역 이름 '{g}'과 같은 업무 영역이 이미 있음 — 묶음 이름을 바꾸거나 그 영역을 먼저 정리: 되돌림")
                gid = same["id"] if same else await conn.fetchval(
                    "INSERT INTO ops_namespace (name, description, kind) VALUES ($1, '상위 업무 영역', 'group') RETURNING id", g)
                await conn.execute("UPDATE ops_namespace SET parent_id = $1 WHERE name = ANY($2::text[])", gid, children)
            # 조직
            part_id = {}
            for team, parts in TEAMS.items():
                tid = await conn.fetchval("INSERT INTO ops_team (name) VALUES ($1) ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id", team)
                for p in parts:
                    part_id[p] = await conn.fetchval("""INSERT INTO ops_part (name, team_id) VALUES ($1, $2)
                                                        ON CONFLICT (name) DO UPDATE SET team_id = EXCLUDED.team_id RETURNING id""", p, tid)
            for old, new in MOVE_USERS.items():
                await conn.execute("""UPDATE ops_user SET part_id = $2 WHERE part_id = (SELECT id FROM ops_part WHERE name = $1)""",
                                   old, part_id[new])
                await conn.execute("""UPDATE ops_voc_routing SET part_id = $2 WHERE part_id = (SELECT id FROM ops_part WHERE name = $1)""",
                                   old, part_id[new])
            await conn.execute("UPDATE ops_user SET part_id = NULL WHERE role = ANY($1::text[])", list(NO_PART_ROLES))
            for dom, p in OWNERS.items():
                await conn.execute("UPDATE ops_namespace SET owner_part_id = $2 WHERE name = $1", dom, part_id[p])
            # 담당 파트 연결이 없는 VOC(옛 글자가 어느 파트와도 안 맞던 행) → 그 영역의 담당 파트
            await conn.execute("""UPDATE ops_voc_routing r SET part_id = n.owner_part_id FROM ops_namespace n
                                  WHERE r.namespace_id = n.id AND r.part_id IS NULL AND n.owner_part_id IS NOT NULL""")
            for p in DROP_PARTS:
                pid = await conn.fetchval("SELECT id FROM ops_part WHERE name = $1", p)
                if pid is None:
                    continue
                used = await conn.fetchval("""SELECT (SELECT count(*) FROM ops_user WHERE part_id = $1)
                                                   + (SELECT count(*) FROM ops_namespace WHERE owner_part_id = $1)
                                                   + (SELECT count(*) FROM ops_voc_routing WHERE part_id = $1)""", pid)
                if used:
                    raise RuntimeError(f"옛 파트 {p}를 아직 쓰는 곳 {used}건 — 되돌림")
                await conn.execute("DELETE FROM ops_part WHERE id = $1", pid)
            # 점검: 합친 영역의 행 수 = 두 영역 합(카테고리 병합만 예외)
            for s, d in merges:
                for t in tables:
                    want = before[t][ns[s]] + before[t][ns[d]] - (reports[f"{s}→{d}"]["카테고리 병합(같은 이름)"]
                                                                 if t == "rag_knowledge_category" else 0)
                    got = await conn.fetchval(f"SELECT count(*) FROM {t} WHERE namespace_id = $1", ns[d])
                    if got != want:
                        raise RuntimeError(f"점검 실패 {t} {d}: {got} != {want} — 되돌림")
            # 연결 표·설정 키 점검(리뷰 L3): 없는 영역을 가리키는 연결 0, 옮긴 영역의 설정 키 0, 합치며 생긴 자기 연결 0
            dangling = await conn.fetchval("""
                SELECT count(*) FROM ops_link l, LATERAL (VALUES (l.from_type, l.from_key), (l.to_type, l.to_key)) v(t, k)
                WHERE (v.t = 'domain' AND NOT EXISTS (SELECT 1 FROM ops_namespace n WHERE n.id::text = v.k))
                   OR (v.t = 'section' AND NOT EXISTS (SELECT 1 FROM ops_namespace n WHERE n.id::text = split_part(v.k, '|', 1)))""")
            src_keys = await conn.fetchval("SELECT count(*) FROM ops_system_config WHERE key = ANY($1::text[])",
                                           [f"{k}:{ns[s]}" for s, _ in merges for k in ("glossary_mining_last_id", "glossary_gap_done")])
            self_links = await conn.fetchval("SELECT count(*) FROM ops_link WHERE from_type = to_type AND from_key = to_key")
            if dangling or src_keys or self_links:
                raise RuntimeError(f"점검 실패 — 없는 영역 연결 {dangling} · 옛 설정 키 {src_keys} · 자기 연결 {self_links}: 되돌림")
            orphans = await conn.fetchval("SELECT count(*) FROM ops_namespace WHERE kind <> 'group' AND parent_id IS NULL AND kind <> 'policy_test'")
            print("합치기 결과:", reports, "· 상위 영역 없는 영역:", orphans)
        # 캐시는 영역 이름이 키 — 합친·바뀐 이름 전부 비운다(트랜잭션 밖, 실패해도 TTL로 사라짐)
        try:
            from shared import cache as sem_cache
            for name in list(MERGE) + list(MERGE.values()) + list(RENAME) + list(RENAME.values()):
                await sem_cache.invalidate_namespace(name)
        except Exception as e:   # noqa: BLE001
            print("캐시 비우기 실패(백엔드 재시작 또는 TTL로 해소):", e)
    print("적용 완료 — 백엔드 재시작(프로세스 안 색인 캐시)")


if __name__ == "__main__":
    asyncio.run(main())
