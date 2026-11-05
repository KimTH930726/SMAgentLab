"""VOC 이메일을 정책 컬렉션에서 시스템 컬렉션으로 옮긴다(11/05 태훈 — 정책서 = 정책 컬렉션, VOC·운영 지식 = 시스템 컬렉션).

옮기는 것: 라우팅(ops_voc_routing) · 분석 이력(ops_email_analysis) · 반복 묶음(ops_voc_cluster) — 셋을 같이.
라우팅만 옮기면 안 된다: 이미 처리한 메일 확인이 (영역, 메일 id) 기준이라 재조회 기간의 메일을 전부 다시 분석·발송한다.
점검: 표마다 받는 쪽(후) = 받는 쪽(전) + 옮긴 수, 보내는 쪽 0 — 다르면 트랜잭션 전체 되돌림. 받는 쪽에 같은 메일함·메일이 이미 있으면 멈춤.
되돌리기: --apply가 옮긴 id를 --log 파일에 적고, --revert <파일>이 그 id만 원래 컬렉션으로 돌린다.
수집 중(스케줄러 사이클)에 옮기면 그 사이클이 옛 영역에 쓸 수 있다 — 옮기기 전 수집을 끄고, 확인 뒤 다시 켠다.

실행(컨테이너): python scripts/move_voc_to_system.py --src 딜리버스 --dst "딜리버스 시스템"                       # 미리보기
               python scripts/move_voc_to_system.py --src 딜리버스 --dst "딜리버스 시스템" --apply --log eval_out/move_voc.json
               python scripts/move_voc_to_system.py --revert eval_out/move_voc.json
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")

TABLES = ("ops_voc_routing", "ops_email_analysis", "ops_voc_cluster")


async def ns_row(conn, name: str):
    return await conn.fetchrow("SELECT id, name, kind, area FROM ops_namespace WHERE name = $1", name)


async def check(conn, src, dst) -> list[str]:
    why = []
    if src["kind"] != "domain" or src["area"] != "policy":
        why.append("보내는 쪽이 정책 컬렉션이 아님")
    if dst["kind"] != "domain" or dst["area"] != "system":
        why.append("받는 쪽이 시스템 컬렉션이 아님")
    # 고유 조건 (영역, 메일함) · (영역, 메일 id) — 받는 쪽에 이미 있으면 옮기다 깨진다
    if await conn.fetchval("SELECT count(*) FROM ops_voc_routing a JOIN ops_voc_routing b ON a.mailbox_upn = b.mailbox_upn "
                           "WHERE a.namespace_id = $1 AND b.namespace_id = $2", src["id"], dst["id"]):
        why.append("받는 쪽에 같은 메일함 라우팅이 이미 있음")
    if await conn.fetchval("SELECT count(*) FROM ops_email_analysis a JOIN ops_email_analysis b ON a.source_message_id = b.source_message_id "
                           "WHERE a.namespace_id = $1 AND b.namespace_id = $2", src["id"], dst["id"]):
        why.append("받는 쪽에 같은 메일 분석이 이미 있음")
    return why


async def ids_of(conn, ns_id: int) -> dict[str, list[int]]:
    return {t: [r["id"] for r in await conn.fetch(f"SELECT id FROM {t} WHERE namespace_id = $1 ORDER BY id", ns_id)] for t in TABLES}


async def counts(conn, ns_id: int) -> dict[str, int]:
    return {t: await conn.fetchval(f"SELECT count(*) FROM {t} WHERE namespace_id = $1", ns_id) for t in TABLES}


async def set_ns(conn, moved: dict[str, list[int]], ns_id: int) -> None:
    for t in TABLES:
        await conn.execute(f"UPDATE {t} SET namespace_id = $1 WHERE id = ANY($2::int[])", ns_id, moved[t])


async def move(conn, src, dst, apply: bool, log: str | None) -> None:
    why = await check(conn, src, dst)
    moved = await ids_of(conn, src["id"])
    print(f"{src['name']} → {dst['name']}: " + " · ".join(f"{t} {len(v)}" for t, v in moved.items()))
    if why:
        sys.exit("멈춤: " + ", ".join(why))
    if not apply:
        print("미리보기만(--apply로 옮김)")
        return
    async with conn.transaction():
        before = await counts(conn, dst["id"])
        await set_ns(conn, moved, dst["id"])
        after, left = await counts(conn, dst["id"]), await counts(conn, src["id"])
        bad = [t for t in TABLES if after[t] != before[t] + len(moved[t]) or left[t] != 0]
        if bad:
            raise RuntimeError(f"건수 어긋남 — 전체 되돌림: {bad} 전 {before} 후 {after} 남음 {left}")
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(log).write_text(json.dumps({"src": src["id"], "dst": dst["id"], "moved": moved}, ensure_ascii=False), encoding="utf-8")
    print("옮김 · 점검 통과(받는 쪽 = 전 + 옮긴 수, 보내는 쪽 0)" + (f" · 되돌리기 기록 {log}" if log else ""))


async def revert(conn, log: str) -> None:
    d = json.loads(Path(log).read_text(encoding="utf-8"))
    async with conn.transaction():
        await set_ns(conn, d["moved"], d["src"])
        back = {t: await conn.fetchval(f"SELECT count(*) FROM {t} WHERE id = ANY($1::int[]) AND namespace_id = $2", d["moved"][t], d["src"])
                for t in TABLES}
        bad = [t for t in TABLES if back[t] != len(d["moved"][t])]
        if bad:
            raise RuntimeError(f"되돌린 건수 어긋남 — 전체 되돌림: {bad}")
    print("되돌림: " + " · ".join(f"{t} {len(v)}" for t, v in d["moved"].items()))


async def main() -> None:
    import asyncpg
    from core.config import settings
    ap = argparse.ArgumentParser()
    ap.add_argument("--src")
    ap.add_argument("--dst")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--log")
    ap.add_argument("--revert")
    a = ap.parse_args()
    conn = await asyncpg.connect(settings.database_url)
    try:
        if a.revert:
            await revert(conn, a.revert)
            return
        src, dst = await ns_row(conn, a.src), await ns_row(conn, a.dst)
        if src is None or dst is None:
            sys.exit("컬렉션 이름을 못 찾음")
        await move(conn, src, dst, a.apply, a.log)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
