"""컨플루언스 청크 구조 3방식 비교 — 재정제 때 어떤 구조로 넣을지 정하는 근거 (2026-10-01).

  A 현재       : 본문만 임베딩, 상위 맥락(heading_path)은 검색 후 LLM 컨텍스트에만 붙음(v2.98).
  B 맥락 임베딩 : "상위 맥락: … \\n본문"을 임베딩 — 같은 문장이라도 소속에 따라 벡터가 달라짐.
  C 부모 확장   : 검색은 A 그대로, 채택된 청크와 같은 직계 상위(heading_path[:-1])를 공유하는
                 형제 청크를 컨텍스트에 덧붙임(small-to-big 근사).

원본 데이터는 바꾸지 않는다 — B는 대상 네임스페이스의 활성 지식을 임시 네임스페이스에 복사하면서
임베딩만 다시 만들고, **운영과 같은 search_knowledge()**로 검색한 뒤 임시 네임스페이스를 지운다.
용어 매핑(질문 보강)은 원본 네임스페이스 기준으로 한 번 계산해 세 방식에 똑같이 쓴다.

질문 세트: 컨플루언스 원문은 이 세션이 직접 읽지 않는 규칙이라 로컬 LLM이 청크별로 1문항씩
생성한 JSONL({"chunk_id", "question"})을 받는다. 출력은 지표와 id만 — 질문·본문·답변 미출력.

실행 (컨테이너 안):
    python scripts/eval_confluence_structure.py /tmp/conf_questions.jsonl [--skip-llm]
"""
import argparse
import asyncio
import json
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, "/app")

from core.database import close_pool, get_conn, init_pool
from shared.embedding import embedding_service
from service.llm.base import resolve_system_prompt
from service.llm.factory import get_llm_provider
from service.chat.helpers import NO_KNOWLEDGE_MARKER
from agents.knowledge_rag.knowledge import retrieval
from agents.knowledge_rag.agent import POLICY_CONTEXT_TOP_K, _build_rrf_context
from service.policy import search as policy_search

TMP_SUFFIX = "__eval_ctxemb"
C_MAX_SIBLINGS = 6  # 부모 확장 시 채택 청크 하나당 덧붙일 형제 수 상한(컨텍스트 폭주 방지)


def heading_text(path) -> str:
    return " > ".join(path or [])


def b_embed_text(path, content: str) -> str:
    return f"상위 맥락: {heading_text(path)}\n{content}" if path else content


async def make_ctx_embedding_copy(conn, ns_name: str) -> tuple[str, dict[int, int]]:
    """원본 네임스페이스 활성 지식을 임시 네임스페이스로 복사(임베딩만 B 방식). 반환: (임시 ns, 임시id→원본id)."""
    src_id = await conn.fetchval("SELECT id FROM ops_namespace WHERE name=$1", ns_name)
    tmp_name = f"{ns_name}{TMP_SUFFIX}"
    await conn.execute("DELETE FROM ops_namespace WHERE name=$1", tmp_name)
    tmp_id = await conn.fetchval(
        "INSERT INTO ops_namespace (name, description) VALUES ($1, 'eval_confluence_structure 임시') RETURNING id", tmp_name)
    rows = await conn.fetch("""SELECT id, content, base_weight, category, heading_path, source_type, embedding_model
                               FROM rag_knowledge WHERE namespace_id=$1 AND status='active'""", src_id)
    vecs = await embedding_service.embed_batch([b_embed_text(r["heading_path"], r["content"]) for r in rows])
    id_map = {}
    for r, v in zip(rows, vecs):
        new_id = await conn.fetchval("""
            INSERT INTO rag_knowledge (namespace_id, content, embedding, base_weight, category, heading_path,
                                       source_type, embedding_model, status)
            VALUES ($1, $2, $3::vector, $4, $5, $6, $7, $8, 'active') RETURNING id""",
            tmp_id, r["content"], str(v), r["base_weight"], r["category"], r["heading_path"],
            r["source_type"], r["embedding_model"])
        id_map[new_id] = r["id"]
    return tmp_name, id_map


async def siblings_for(conn, ns_name: str, hits) -> list[dict]:
    """C: 채택된 각 청크와 같은 직계 상위(heading_path[:-1])를 가진 형제 청크(자기 자신·이미 포함 제외)."""
    ns_id = await conn.fetchval("SELECT id FROM ops_namespace WHERE name=$1", ns_name)
    seen = {h.id for h in hits}
    out = []
    for h in hits:
        if not h.heading_path:
            continue
        parent = list(h.heading_path[:-1]) if len(h.heading_path) > 1 else list(h.heading_path)
        rows = await conn.fetch("""
            SELECT id, content, heading_path FROM rag_knowledge
            WHERE namespace_id=$1 AND status='active' AND heading_path[1:$2] = $3::text[] AND id <> ALL($4::int[])
            ORDER BY abs(id - $5) LIMIT $6""",
            ns_id, len(parent), parent, list(seen), h.id, C_MAX_SIBLINGS)
        for r in rows:
            seen.add(r["id"])
            out.append(dict(r))
    return out


P2_CHAR_BUDGET = 6000  # P2(부모 확장)가 검색 청크 하나당 덧붙일 최대 글자 수 — 개수 대신 길이로 상한


async def exact_expand(conn, hits, level: str) -> list[dict]:
    """정확한 parent-child 확장(2026-10-01) — 근사(C: 같은 상위 문자열 + id 근접 6개)가 아니라 적재 때
    남은 구조로 경계를 복원한다: 같은 등록 묶음(ingestion_job_id) 안에서 문서 순서(source_chunk_idx)대로.

      level="section": heading_path가 완전히 같은 청크 = 같은 섹션 전체(상한 없음)
      level="parent" : 직계 상위(heading_path[:-1], 1단계면 페이지)가 같은 청크 — 검색 청크와 문서 순서상
                       가까운 것부터 P2_CHAR_BUDGET 글자까지(섹션이 아니라 부모 단위라 커질 수 있어 상한)
    컨플루언스 원본 없이도 되는 이유: 같은 job·같은 heading_path·source_chunk_idx만으로 경계와 순서가 결정된다.
    """
    if not hits:
        return []
    info = {r["id"]: r for r in await conn.fetch(
        "SELECT id, ingestion_job_id, source_chunk_idx, heading_path FROM rag_knowledge WHERE id = ANY($1::int[])",
        [h.id for h in hits])}
    seen = {h.id for h in hits}
    out = []
    for h in hits:
        m = info.get(h.id)
        if not m or m["ingestion_job_id"] is None or not m["heading_path"]:
            continue  # 구조 정보가 없으면 확장하지 않음(추정으로 채우지 않는다)
        hp = list(m["heading_path"])
        if level == "section":
            rows = await conn.fetch("""
                SELECT id, content, heading_path FROM rag_knowledge
                WHERE ingestion_job_id=$1 AND status='active' AND heading_path = $2::text[] AND id <> ALL($3::int[])
                ORDER BY source_chunk_idx""", m["ingestion_job_id"], hp, list(seen))
        else:
            parent = hp[:-1] if len(hp) > 1 else hp
            rows = await conn.fetch("""
                SELECT id, content, heading_path FROM rag_knowledge
                WHERE ingestion_job_id=$1 AND status='active' AND heading_path[1:$2] = $3::text[] AND id <> ALL($4::int[])
                ORDER BY abs(source_chunk_idx - $5), source_chunk_idx""",
                m["ingestion_job_id"], len(parent), parent, list(seen), m["source_chunk_idx"] or 0)
            budget, kept = P2_CHAR_BUDGET, []
            for r in rows:
                if len(r["content"]) > budget:
                    break
                budget -= len(r["content"])
                kept.append(r)
            rows = kept
        for r in rows:
            if r["id"] not in seen:
                seen.add(r["id"])
                out.append(dict(r))
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("questions")
    ap.add_argument("--skip-llm", action="store_true")
    args = ap.parse_args()

    qs = []
    for line in open(args.questions, encoding="utf-8"):
        line = line.strip().strip(",")
        if line and not line.startswith("`"):
            o = json.loads(line)
            if o.get("question"):
                qs.append(o)

    embedding_service.load()
    await init_pool()
    tmp_names = []
    try:
        async with get_conn() as conn:
            gold_ns = {r["id"]: r["name"] for r in await conn.fetch(
                "SELECT k.id, n.name FROM rag_knowledge k JOIN ops_namespace n ON n.id=k.namespace_id "
                "WHERE k.id = ANY($1::int[])", [q["chunk_id"] for q in qs])}
            copies = {}
            for ns in sorted(set(gold_ns.values())):
                tmp, id_map = await make_ctx_embedding_copy(conn, ns)
                tmp_names.append(tmp)
                copies[ns] = (tmp, id_map)

        llm = None if args.skip_llm else get_llm_provider()
        sp = None if args.skip_llm else await resolve_system_prompt()
        th = retrieval.get_thresholds()
        top_k = int(retrieval.get_search_defaults()["default_top_k"])
        d = retrieval.get_search_defaults()

        stats = {s: defaultdict(list) for s in "ABC"}
        per_q = []
        for i, q in enumerate(qs, 1):
            gold, ns = q["chunk_id"], gold_ns.get(q["chunk_id"])
            if ns is None:
                continue
            qvec = await embedding_service.embed(q["question"])
            glossary = await retrieval.map_glossary_term(ns, qvec)
            enriched = f"{q['question']} {glossary.term}" if glossary else q["question"]
            pr = policy_search.PolicySearchResult()
            if await policy_search.has_policy_data(ns):
                pr = await policy_search.search_policy(ns, enriched, top_k=POLICY_CONTEXT_TOP_K, query_vec=qvec)

            a_res = await retrieval.search_knowledge(ns, qvec, enriched, d["default_w_vector"], d["default_w_keyword"], top_k)
            tmp, id_map = copies[ns]
            b_raw = await retrieval.search_knowledge(tmp, qvec, enriched, d["default_w_vector"], d["default_w_keyword"], top_k)
            a_wide = await retrieval.search_knowledge(ns, qvec, enriched, d["default_w_vector"], d["default_w_keyword"], 20)
            b_wide = await retrieval.search_knowledge(tmp, qvec, enriched, d["default_w_vector"], d["default_w_keyword"], 20)
            rank = lambda res, m=None: next((j + 1 for j, r in enumerate(res) if (m.get(r.id) if m else r.id) == gold), None)

            a_adopted = [r for r in a_res if retrieval.is_adopted(r, th)]
            b_adopted = [r for r in b_raw if retrieval.is_adopted(r, th)]
            ctx_a = _build_rrf_context(a_res, pr)
            ctx_b = _build_rrf_context(b_raw, pr)
            async with get_conn() as conn:
                sibs = await siblings_for(conn, ns, a_adopted)
            ctx_c = ctx_a + "".join(
                f"\n\n--- 문서 #{s['id']} (같은 상위 섹션 확장) ---\n상위 맥락: {heading_text(s['heading_path'])}\n내용:\n{s['content']}"
                for s in sibs)

            in_ctx = {
                "A": any(r.id == gold for r in a_adopted),
                "B": any(id_map.get(r.id) == gold for r in b_adopted),
                "C": any(r.id == gold for r in a_adopted) or any(s["id"] == gold for s in sibs),
            }
            row = {"i": i, "gold": gold, "ns": ns, "rank_a": rank(a_wide), "rank_b": rank(b_wide, id_map)}
            for s, ctx in (("A", ctx_a), ("B", ctx_b), ("C", ctx_c)):
                stats[s]["in_ctx"].append(in_ctx[s])
                stats[s]["ctx_chars"].append(len(ctx))
                if llm is not None:
                    text, _ = await llm.generate(ctx, q["question"], None, system_prompt=sp)
                    nok = NO_KNOWLEDGE_MARKER in (text or "")
                    stats[s]["nok"].append(nok)
                    row[f"nok_{s}"] = nok
                row[f"ctx_{s}"] = in_ctx[s]
            stats["A"]["hit"].append(row["rank_a"] is not None and row["rank_a"] <= top_k)
            stats["B"]["hit"].append(row["rank_b"] is not None and row["rank_b"] <= top_k)
            per_q.append(row)
            if i % 10 == 0:
                print(f"  ... {i}/{len(qs)}", flush=True)

        n = len(per_q)
        print(f"\n=== 컨플루언스 청크 구조 비교 — {n}문항 (청크당 1문항, 운영 top_k={top_k}) ===")
        print(f"{'방식':<14}{'정답 top_k 검색':>14}{'정답 컨텍스트 포함':>18}{'지식없음 답변':>14}{'컨텍스트 평균 길이':>18}")
        for s, label in (("A", "A 현재"), ("B", "B 맥락임베딩"), ("C", "C 부모확장")):
            st = stats[s]
            hit = f"{sum(st['hit'])}/{n}" if st["hit"] else "(A와 동일)"
            nok = f"{sum(st['nok'])}/{n}" if st["nok"] else "-"
            print(f"{label:<14}{hit:>14}{sum(st['in_ctx']):>12}/{n}{nok:>14}{statistics.mean(st['ctx_chars']):>16.0f}자")

        ranks_a = [r["rank_a"] for r in per_q]
        ranks_b = [r["rank_b"] for r in per_q]
        better = sum(1 for a, b in zip(ranks_a, ranks_b) if (b or 99) < (a or 99))
        worse = sum(1 for a, b in zip(ranks_a, ranks_b) if (b or 99) > (a or 99))
        print(f"\n  B vs A 정답 순위: 개선 {better} / 악화 {worse} / 동일 {n - better - worse}")
        print(f"  정답이 20위 밖: A {sum(r is None for r in ranks_a)}, B {sum(r is None for r in ranks_b)}")
        print(f"  평균 순위(20위 내): A {statistics.mean([r for r in ranks_a if r]):.2f}, B {statistics.mean([r for r in ranks_b if r]):.2f}")
        by_ns = defaultdict(lambda: defaultdict(int))
        for r in per_q:
            for s in "ABC":
                by_ns[r["ns"]][s] += r[f"ctx_{s}"]
            by_ns[r["ns"]]["n"] += 1
        for ns, c in by_ns.items():
            print(f"  [{ns}] 정답 컨텍스트 포함 A {c['A']}/{c['n']}, B {c['B']}/{c['n']}, C {c['C']}/{c['n']}")
        changed = [r["gold"] for r in per_q if len({r["ctx_A"], r["ctx_B"], r["ctx_C"]}) > 1]
        print(f"  방식 간 결과가 갈린 정답 청크 id: {changed}")
    finally:
        async with get_conn() as conn:
            for tmp in tmp_names:
                await conn.execute("DELETE FROM ops_namespace WHERE name=$1", tmp)
            left = await conn.fetchval("SELECT COUNT(*) FROM ops_namespace WHERE name LIKE $1", f"%{TMP_SUFFIX}")
        print(f"\n[정리] 임시 네임스페이스 잔존: {left}")
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
