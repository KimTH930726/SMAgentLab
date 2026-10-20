"""정책서 v0 적재 (2026-10-20, docs/tech/policy-v0-design.md §5). 팀원 구조화 데이터 → 정책 파트 14개·정책·용어·참고표·개요·관계.

  (기본) 미리보기 — DB에 쓰지 않는다. 변환 결과·변환이 한 일·사람 확인 목록·불변식 위반을 출력. 위반이 있으면 종료 코드 1.
  --apply      불변식이 통과하면 임베딩 후 트랜잭션 하나로 적재(실패하면 아무것도 안 남음). 한 번만 — ops_data_marker
               'policy_v0_load'가 있으면 거부.

LLM 없음(임베딩만, 로컬). 실행(컨테이너 안): python scripts/load_policy_v0.py --src eval_out/v0full [--apply]
적재 후: 새 파트라 정책 색인 캐시는 비어 있어 재시작 불필요. 파라미터 추출·동의어는 별도 단계.
"""
import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/app")

from service.policy.v0_convert import (PIPELINE_WHOLE, GLOSSARY_SOURCE, build_plan, check_plan,  # noqa: E402
                                       compressed_view, load_source)

MARKER = "policy_v0_load"
PIPELINE = PIPELINE_WHOLE        # policy_item.pipeline_version — LLM 분해 안 함(통째)


def summarize(plan) -> None:
    print(f"파트 {len(plan.parts)} · 정책 {len(plan.policies)} · 용어 {len(plan.terms)} · 참고표 {len(plan.tables)}"
          f"(원천 표 {sum(len(t.source_keys) for t in plan.tables)}) · 개요 {len(plan.overviews)} · 관계 {len(plan.links)}")
    print("파트:", ", ".join(f"{p.doc_id}={p.name}" for p in plan.parts))
    print("문서별 정책·용어:", ", ".join(
        f"{p.name} {sum(1 for r in plan.policies if r.doc_id == p.doc_id)}/{sum(1 for t in plan.terms if t.doc_id == p.doc_id)}"
        for p in plan.parts))
    print("관계 종류:", dict(Counter(f"{l.from_type}-{l.relation}-{l.to_type}" for l in plan.links)))
    print("비고 있는 정책:", sum(1 for r in plan.policies if r.remark))
    for t in plan.tables:
        dec = [c["name"] for c in t.columns if c.get("decisive")]
        cv = compressed_view(t)
        print(f"  참고표 '{t.title}' {len(t.rows)}행 · 열 {len(t.columns)} · 절 {' > '.join(t.category_path) or '(없음)'}"
              + (f" · 결정 열 {dec} → 압축 {len(cv)}행" if dec else " · 통째"))
    for o in plan.overviews:
        print(f"  개요 {o.doc_id}: 문단 {len(o.body.splitlines())} · {len(o.body)}자")
    print("변환이 한 일:")
    for n in plan.notes:
        print("  -", n)
    print(f"사람 확인 {len(plan.review)}건:")
    for n in plan.review:
        print("  -", n)


def _hash(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def table_summary(t) -> str:
    """표 단위 검색용 요약 — 제목·절·열 이름과 열별 값 앞부분(결정표면 조건/결과 구분)."""
    lines = [f"[참고표] {t.title}", f"위치: {' > '.join(t.category_path)}"]
    for c in t.columns:
        if c.get("role") == "id":
            continue
        vals = list(dict.fromkeys(r["cells"].get(c["name"], "") for r in t.rows if r["cells"].get(c["name"], "") not in ("", "-")))
        tag = {"condition": "조건", "result": "결과"}.get(c.get("role") or "", "열")
        lines.append(f"{tag} {c['name']}: {', '.join(v.replace(chr(10), ' ') for v in vals[:15])}{' 외' if len(vals) > 15 else ''}")
    return "\n".join(lines)


async def apply(plan) -> None:
    from core.database import get_conn, init_pool
    from service.policy.service import chunk_embed_text
    from shared.embedding import embedding_service
    from trustkit.retrieval.chunk_header import contextual_header

    await init_pool()
    async with get_conn() as conn:
        if await conn.fetchval("SELECT value FROM ops_data_marker WHERE key = $1", MARKER):
            sys.exit("이미 적재됨(ops_data_marker policy_v0_load) — 다시 넣으려면 먼저 사람이 정리 계획을 세울 것")
        taken = await conn.fetch("SELECT name FROM ops_namespace WHERE name = ANY($1::text[])", [p.name for p in plan.parts])
        if taken:
            sys.exit(f"같은 이름 파트가 이미 있음: {[r['name'] for r in taken]}")

    embedding_service.load()

    async def embed_all(texts):
        out = []
        for i in range(0, len(texts), 32):
            out += await embedding_service.embed_batch(texts[i:i + 32])
            print(f"\r  임베딩 {min(i + 32, len(texts))}/{len(texts)}", end="", flush=True)
        print()
        return out

    part_of = {p.doc_id: p.name for p in plan.parts}
    print("정책 임베딩(머리 + 본문, 비고 제외)")
    pol_emb = await embed_all([chunk_embed_text(r.policy_name, r.category_path, r.raw_body) for r in plan.policies])
    print("용어 임베딩(설명)")
    term_emb = await embed_all([t.description for t in plan.terms])
    print("참고표 요약·개요 임베딩")
    tab_emb = await embed_all([table_summary(t) for t in plan.tables])
    ov_emb = await embed_all([contextual_header(o.title, [part_of[o.doc_id]], o.body) for o in plan.overviews])

    async with get_conn() as conn:
        async with conn.transaction():
            ns = {}
            for p in plan.parts:
                ns[p.doc_id] = await conn.fetchval(
                    "INSERT INTO ops_namespace (name, description, kind) VALUES ($1, $2, 'domain') RETURNING id",
                    p.name, f"정책서: {p.source_file}")
            pol_logical = {}
            for r, e in zip(plan.policies, pol_emb):
                row = await conn.fetchrow(
                    """INSERT INTO policy_item (namespace_id, system_key, category_path, policy_name, raw_body, remark,
                           source_file, source_row, content_hash, status, parse_status, pipeline_version, source_key,
                           review_source, reviewed_at)
                       VALUES ($1,'',$2,$3,$4,$5,$6,$7,$8,'active','parsed',$9,$10,'v0_system',NOW()) RETURNING id, logical_id""",
                    ns[r.doc_id], r.category_path, r.policy_name, r.raw_body, r.remark, r.source_file, r.source_row,
                    _hash(r.category_path, r.policy_name, r.raw_body, r.remark), PIPELINE, r.source_key)
                await conn.execute(
                    "INSERT INTO policy_chunk (policy_item_id, chunk_text, embedding, chunk_idx) VALUES ($1, $2, $3::vector, 0)",
                    row["id"], r.raw_body, str(e))
                pol_logical[r.source_key] = row["logical_id"]
            term_id = {}
            for t, e in zip(plan.terms, term_emb):
                term_id[t.source_key] = await conn.fetchval(
                    """INSERT INTO rag_glossary (namespace_id, term, description, embedding, source)
                       VALUES ($1, $2, $3, $4::vector, $5) RETURNING id""",
                    ns[t.doc_id], t.term, t.description, str(e), GLOSSARY_SOURCE)
            table_id = {}
            for t, e in zip(plan.tables, tab_emb):
                tid = await conn.fetchval(
                    """INSERT INTO ref_table (namespace_id, title, category_path, columns, source_key, source_file, content_hash, embedding)
                       VALUES ($1,$2,$3,$4::jsonb,$5,$6,$7,$8::vector) RETURNING id""",
                    ns[t.doc_id], t.title, t.category_path, json.dumps(t.columns, ensure_ascii=False), t.source_key, t.source_file,
                    _hash(t.columns, t.rows), str(e))
                await conn.execute("UPDATE ref_table SET logical_id = id WHERE id = $1", tid)
                await conn.executemany("INSERT INTO ref_table_row (ref_table_id, row_no, cells) VALUES ($1, $2, $3::jsonb)",
                                       [(tid, r["row_no"], json.dumps(r["cells"], ensure_ascii=False)) for r in t.rows])
                table_id[t.source_key] = tid
            for o, e in zip(plan.overviews, ov_emb):
                await conn.execute(
                    """INSERT INTO policy_doc_overview (namespace_id, title, body, source_key, source_file, embedding)
                       VALUES ($1,$2,$3,$4,$5,$6::vector)""", ns[o.doc_id], o.title, o.body, o.doc_id, o.source_file, str(e))

            def key(typ, ref):
                if typ == "policy":
                    return str(pol_logical[ref])
                if typ == "term":
                    return str(term_id[ref])
                if typ == "ref_table":
                    return str(table_id[ref])
                if typ == "domain":
                    return str(ns[ref])
                doc, path = ref.split("|", 1)        # 절: "업무 영역 id|L1 > L2"
                return f"{ns[doc]}|{path}"
            await conn.executemany(
                """INSERT INTO ops_link (from_type, from_key, relation, to_type, to_key, source, status)
                   VALUES ($1,$2,$3,$4,$5,'v0_system',$6) ON CONFLICT DO NOTHING""",
                [(l.from_type, key(l.from_type, l.from_ref), l.relation, l.to_type, key(l.to_type, l.to_ref), l.status)
                 for l in plan.links])
            # 적재 점검 — 트랜잭션 안에서 DB로 다시 센다(쓴 것 = 계획한 것). 어긋나면 예외로 전부 되돌린다 — 커밋 뒤에 세면
            # 틀린 적재가 남고 표시(marker)가 재실행까지 막았다(리뷰 10/20 M5)
            ids = list(ns.values())
            got = {
                "policy": await conn.fetchval("SELECT count(*) FROM policy_item WHERE namespace_id = ANY($1)", ids),
                "chunk": await conn.fetchval("SELECT count(*) FROM policy_chunk c JOIN policy_item i ON i.id = c.policy_item_id "
                                             "WHERE i.namespace_id = ANY($1)", ids),
                "term": await conn.fetchval("SELECT count(*) FROM rag_glossary WHERE namespace_id = ANY($1)", ids),
                "table_rows": await conn.fetchval("SELECT count(*) FROM ref_table_row r JOIN ref_table t ON t.id = r.ref_table_id "
                                                  "WHERE t.namespace_id = ANY($1)", ids),
                "overview": await conn.fetchval("SELECT count(*) FROM policy_doc_overview WHERE namespace_id = ANY($1)", ids),
                "link": await conn.fetchval("SELECT count(*) FROM ops_link WHERE source = 'v0_system'"),
            }
            want = {"policy": len(plan.policies), "chunk": len(plan.policies), "term": len(plan.terms),
                    "table_rows": sum(len(t.rows) for t in plan.tables), "overview": len(plan.overviews), "link": len(plan.links)}
            print("적재 점검(DB 재계산 / 계획):", {k: f"{got[k]}/{want[k]}" for k in want})
            if got != want:
                raise RuntimeError("적재 점검 불일치 — 아무것도 저장하지 않고 되돌림")
            await conn.execute(
                "INSERT INTO ops_data_marker (key, value) VALUES ($1, $2)", MARKER,
                f"policy={len(plan.policies)} term={len(plan.terms)} table={len(plan.tables)} overview={len(plan.overviews)}")
    print("적재 완료")


async def add_suggested_links(plan) -> None:
    """이미 적재된 DB에 제안 연결(status=suggested)만 보탠다 — 10/20 적재 뒤 추가된 규칙용. 멱등(ON CONFLICT)."""
    from core.database import get_conn, init_pool
    await init_pool()
    sug = [l for l in plan.links if l.status == "suggested"]
    async with get_conn() as conn:
        ns = {p.doc_id: await conn.fetchval("SELECT id FROM ops_namespace WHERE name = $1", p.name)
              for p in plan.parts}
        rows = []
        for l in sug:
            frm = await conn.fetchval("SELECT logical_id FROM policy_item WHERE source_key = $1 AND status <> 'deprecated'", l.from_ref)
            doc, path = l.to_ref.split("|", 1)
            if frm is None or ns.get(doc) is None:
                sys.exit(f"적재된 대상 없음: {l.from_ref} / {doc}")
            rows.append(("policy", str(frm), l.relation, "section", f"{ns[doc]}|{path}"))
        async with conn.transaction():
            await conn.executemany(
                """INSERT INTO ops_link (from_type, from_key, relation, to_type, to_key, source, status)
                   VALUES ($1,$2,$3,$4,$5,'v0_rule','suggested') ON CONFLICT DO NOTHING""", rows)
        n = await conn.fetchval("SELECT count(*) FROM ops_link WHERE status = 'suggested'")
    print(f"제안 연결 {len(rows)}건 반영(현재 suggested {n}건)")


async def refresh_overviews(plan) -> None:
    """적재된 문서 개요를 지금 규칙으로 다시 쓴다(본문이 바뀐 것만, 다시 임베딩) — 10/20 리뷰 L1(서문 한 줄 누락) 수정분 반영용."""
    from core.database import get_conn, init_pool
    from shared.embedding import embedding_service
    from trustkit.retrieval.chunk_header import contextual_header
    await init_pool()
    part_of = {p.doc_id: p.name for p in plan.parts}
    async with get_conn() as conn:
        cur = {r["source_key"]: r["body"] for r in await conn.fetch(
            "SELECT source_key, body FROM policy_doc_overview WHERE status = 'active'")}
        changed = [o for o in plan.overviews if cur.get(o.doc_id) != o.body]
        if not changed:
            print("바뀐 개요 없음")
            return
        embedding_service.load()
        embs = await embedding_service.embed_batch([contextual_header(o.title, [part_of[o.doc_id]], o.body) for o in changed])
        async with conn.transaction():
            for o, e in zip(changed, embs):
                n = await conn.execute("UPDATE policy_doc_overview SET body = $1, embedding = $2::vector WHERE source_key = $3",
                                       o.body, str(e), o.doc_id)
                if n != "UPDATE 1":
                    raise RuntimeError(f"개요 {o.doc_id} 갱신 대상이 1건이 아님({n}) — 되돌림")
    print(f"개요 갱신 {len(changed)}건: {[o.doc_id for o in changed]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="팀원 산출물 폴더(policies.jsonl·sources/ 등)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--add-suggested-links", action="store_true", help="적재된 DB에 제안 연결만 보탬")
    ap.add_argument("--refresh-overviews", action="store_true", help="적재된 문서 개요를 지금 규칙으로 갱신")
    args = ap.parse_args()
    src = load_source(Path(args.src))
    plan = build_plan(src)
    summarize(plan)
    errs = check_plan(src, plan)
    print(f"불변식 위반 {len(errs)}건")
    for e in errs[:50]:
        print("  !", e)
    if errs:
        sys.exit(1)
    if args.apply:
        asyncio.run(apply(plan))
    elif args.add_suggested_links:
        asyncio.run(add_suggested_links(plan))
    elif args.refresh_overviews:
        asyncio.run(refresh_overviews(plan))


if __name__ == "__main__":
    main()
