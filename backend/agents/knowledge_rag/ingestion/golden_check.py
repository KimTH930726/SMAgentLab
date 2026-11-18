"""게시 전 골든셋 확인(11/17 태훈 "시안 그대로 진행") — 이 배치를 게시하면 **이미 있는 골든셋 문항**의 검색이 나빠지는가.

[검색 확인](team_import.publish_check)은 이번 조각으로 새로 만든 질문만 본다 — 옛 버전을 내리면서 다른 문항의 정답이 사라지거나
새 조각이 정답을 밀어내는 건 못 본다. 여기서는 이 배치가 닿는 컬렉션의 골든셋 문항을
  지금 게시본(active)  vs  게시했다면(active − 게시 때 내려갈 옛 버전 + 이 배치 staging)
두 쪽에서 같은 벡터 검색(상위 SHOW_K)으로 찾아 정답 순위를 비교한다. 정답은 닻(trustkit.eval.anchors)으로 **각 쪽 조각에서 다시** 찾는다 —
같은 페이지를 다시 넣어도 조각 번호와 상관없이 정답을 알아본다. 내려갈 옛 버전 = 작업 held_plan의 replacing_pages·replacing_docs
(_switch_staged가 게시 때 내리는 것과 같은 조건).

판정: 나빠짐 = 지금 상위 TOP_K에 들던 문항이 게시하면 빠짐(평가 게이트의 "검색 나빠짐"과 같은 기준) · 좋아짐 = 반대 ·
정답 사라짐 = 지금은 정답 조각이 있는데 게시하면 닻이 안 맞음(내용이 바뀜 — 골든셋을 사람이 다시 봐야 함, 나빠짐과 따로 센다).
게시를 막지 않는다 — 사람이 [그래도 게시]로 정한다(11/16 결정: 게이트 떨어지면 보류 + 사람이 게시 여부).

한계(보이게): 벡터만(키워드·다시 정렬 없음 — [검색 확인]과 같음), 문항이 물은 컬렉션 안에서만 찾는다(하위·연결 컬렉션 검색은 안 봄).
게시 때 그 작업이 실패할 경우(원문이 그사이 교체됨·같은 원본이 먼저 확정됨·사람 손 닿음 — _switch_staged ValueError)는 모델에 없다 —
그 작업도 게시된 것으로 센다(실제 게시 실패는 배치 오류 칸에 보임, 재리뷰 11/17 낮음).
LLM 0 — 질문은 골든셋에 이미 있고, 임베딩만 한다.
골든셋 = 이 서버 eval_out/의 닻 파일(운영 이미지엔 없음, .dockerignore) + DB의 실사용 골든셋 문항. 둘 다 없으면 건너뜀으로 기록.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional

from trustkit.eval.anchors import anchor_hashes, resolve_hashed

logger = logging.getLogger(__name__)

APP = Path(__file__).resolve().parents[3]   # /app (backend)
GOLDEN_FILES = ("eval_out/confluence_synth_anchors.jsonl", "eval_out/cx_synth_anchors.jsonl")
TOP_K = 3      # 찾음 기준 — 검색 측정 Recall@3과 같게
SHOW_K = 10    # 순위를 이만큼까지 본다(리포트에 "2위 → 7위")
EMBED_CHUNK = 32   # 임베딩은 잠금을 쥐고 돈다 — 한 번에 수백 개를 넘기면 그동안 채팅 임베딩이 기다린다(리뷰 11/17)


def load_file_items(app: Path = APP) -> tuple[list[dict], list[str]]:
    """닻 파일 문항(정답 있는 것만) + 읽은 파일 이름. 파일이 없으면 빈 목록."""
    items, used = [], []
    for rel in GOLDEN_FILES:
        p = app / rel
        if not p.exists():
            continue
        used.append(p.name)
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                items.append(json.loads(line))
    return items, used


def usable(items: list[dict]) -> list[dict]:
    """닻이 있는 정답 문항만, 같은 컬렉션·같은 질문은 하나(앞의 것). 범위 밖(expect abstain)·id 정답(닻 없음)은 뺀다 — 재적재 뒤 못 찾는다."""
    out, seen = [], set()
    for it in items:
        key = (it.get("ns"), (it.get("question") or "").strip())
        if it.get("expect") == "abstain" or not it.get("gold_anchors") or not key[1] or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def rank_of(ids: list[int], gold: set[int]) -> Optional[int]:
    return next((i for i, x in enumerate(ids, 1) if x in gold), None)


def verdict(before: Optional[int], after: Optional[int], k: int = TOP_K) -> str:
    """worse(상위 k에서 빠짐) · better(새로 듦) · down(상위 k 안에서 내려감) · same."""
    b, a = before is not None and before <= k, after is not None and after <= k
    if b and not a:
        return "worse"
    if a and not b:
        return "better"
    if b and a and after > before:
        return "down"
    return "same"


class _Side:
    """한 쪽(지금 / 게시했다면) 조각 — 페이지별로 미리 묶어 닻마다 전체를 다시 훑지 않는다(리뷰 11/17 성능)."""

    def __init__(self, chunks: list[dict], hashed: dict[int, set]):
        self.all = [(c["id"], hashed[c["id"]]) for c in chunks if c["id"] in hashed]
        self.by_page: dict = {}
        self.by_md5: dict = {}
        self.md5_of: dict = {}
        for c in chunks:
            if c["id"] in hashed and c["page"]:
                self.by_page.setdefault(c["page"], []).append((c["id"], hashed[c["id"]]))
            if c["md5"]:
                self.by_md5.setdefault(c["md5"], set()).add(c["id"])
                self.md5_of[c["id"]] = c["md5"]

    def gold(self, anchors: list[dict]) -> set[int]:
        """닻으로 정답 id — 닻의 페이지 안에서(페이지 없으면 이 쪽 전체), 글자까지 같은 사본도 정답."""
        ids: set[int] = set()
        for a in anchors:
            pool = self.by_page.get(a["page"], []) if a.get("page") else self.all
            ids.update(resolve_hashed(a["lines"], pool, a.get("need", 1)))
        out = set(ids)
        for i in ids:
            if i in self.md5_of:
                out |= self.by_md5[self.md5_of[i]]
        return out


async def publish_effect(conn, jobs) -> tuple[set[int], list[int]]:
    """게시하면 생길 일 — (게시 뒤 검색에 없을 조각 id, 조각이 active가 될 작업 id). _switch_staged와 같은 조건이어야 한다
    (시험: 실제 _switch_staged를 돌린 결과와 같은 집합인지 — test_realdb_golden_check 무작위 40배치).
    게시는 작업 id 순(publish_batch)이라, 뒤 작업이 같은 페이지를 교체하면 앞 작업이 막 올린 조각도 내린다 — 그 조각도 excluded에."""
    excluded: set[int] = set()
    new_jobs: list[int] = []
    for j in sorted(jobs, key=lambda r: r["id"]):   # 게시 때 내려갈 것 — _switch_staged와 같은 조건(자기 staging은 아직 안 걸림)
        plan = j["held_plan"]
        plan = json.loads(plan) if isinstance(plan, str) else dict(plan or {})
        if not plan.get("force_pending"):   # 강제 승인 대기면 이 배치 조각은 게시해도 검색에 안 나온다
            new_jobs.append(j["id"])
        # 게시 순간 _switch_staged가 다시 계산하는 대로 — 적어 둔 replacing_docs가 아니라 지금 기준(리뷰 11/17 낮음)
        pages = list(plan.get("replacing_pages") or [])
        src = list(plan.get("source_doc_ids") or [])
        docs = list(plan.get("replacing_docs") or [])
        if src or pages:
            from agents.knowledge_rag.ingestion import source_ledger as _ledger
            docs = await _ledger.replacing_documents(conn, j["namespace_id"], await _ledger.valid_documents(conn, j["namespace_id"], src), pages)
        if pages or docs:
            # active + 앞서 게시될 작업의 staging(그때는 active) — 같은 컬렉션·같은 조건
            excluded.update(r["id"] for r in await conn.fetch(
                "SELECT id FROM rag_knowledge WHERE namespace_id = $1 AND (status = 'active' OR (status = 'staging' AND ingestion_job_id = ANY($5::int[]))) "
                "AND (confluence_page_id = ANY($2::text[]) OR (source_document_id = ANY($3::int[]) AND ingestion_job_id IS DISTINCT FROM $4))",
                j["namespace_id"], pages, docs, j["id"], [x for x in new_jobs if x != j["id"]]))
    return excluded, new_jobs


async def run_check(conn, job_ids: list[int], items: list[dict], embed: Callable[[list[str]], Awaitable[list[list[float]]]],
                    progress: Optional[Callable[[int, int], Awaitable[None]]] = None) -> dict:
    """비교 본체(연결·임베딩을 받아 시험에서 실 DB로 돈다). 반환 = 수 + 나빠짐·좋아짐·정답 사라짐 문항 상세."""
    jobs = await conn.fetch("SELECT j.id, j.namespace_id, n.name, j.held_plan FROM rag_ingestion_job j JOIN ops_namespace n ON n.id = j.namespace_id "
                            "WHERE j.id = ANY($1::int[]) AND j.status = 'held'", job_ids)
    ns_of = {r["name"]: r["namespace_id"] for r in jobs}
    todo = [it for it in usable(items) if it.get("ns") in ns_of]
    out = {"items": len(todo), "worse": 0, "better": 0, "down": 0, "gold_lost": 0, "both_miss": 0, "details": []}
    if not todo:
        return out
    excluded, new_jobs = await publish_effect(conn, jobs)
    excl = sorted(excluded)
    # 정답 찾기용 조각(지금 + 이 배치) — 내용은 닻 페이지 것만 읽는다(페이지 없는 닻이 있는 컬렉션만 전체)
    ns_ids = sorted({ns_of[it["ns"]] for it in todo})
    rows = [dict(r) for r in await conn.fetch(
        "SELECT id, namespace_id AS ns, confluence_page_id AS page, content_norm_md5 AS md5, status = 'staging' AS new FROM rag_knowledge "
        "WHERE namespace_id = ANY($1::int[]) AND ((status = 'active') OR (status = 'staging' AND ingestion_job_id = ANY($2::int[])))",
        ns_ids, new_jobs)]
    pages = sorted({a["page"] for it in todo for a in it["gold_anchors"] if a.get("page")})
    whole = sorted({ns_of[it["ns"]] for it in todo if any(not a.get("page") for a in it["gold_anchors"])})
    hashed = {r["id"]: set(anchor_hashes(r["content"])) for r in await conn.fetch(
        "SELECT id, content FROM rag_knowledge WHERE namespace_id = ANY($1::int[]) "
        "AND ((status = 'active') OR (status = 'staging' AND ingestion_job_id = ANY($2::int[]))) "
        "AND (confluence_page_id = ANY($3::text[]) OR namespace_id = ANY($4::int[]))", ns_ids, new_jobs, pages, whole)}
    by_ns: dict[int, list[dict]] = {}
    for r in rows:
        by_ns.setdefault(r["ns"], []).append(r)
    sides = {n: (_Side([c for c in cs if not c["new"]], hashed), _Side([c for c in cs if c["id"] not in excluded], hashed))
             for n, cs in by_ns.items()}
    empty = _Side([], hashed)
    qs = [it["question"] for it in todo]
    vecs = [v for i in range(0, len(qs), EMBED_CHUNK) for v in await embed(qs[i:i + EMBED_CHUNK])]
    for n, (it, vec) in enumerate(zip(todo, vecs), 1):
        ns_id = ns_of[it["ns"]]
        before_side, after_side = sides.get(ns_id, (empty, empty))
        gb, ga = before_side.gold(it["gold_anchors"]), after_side.gold(it["gold_anchors"])
        v = str(vec)
        top_b = await conn.fetch("SELECT id FROM rag_knowledge WHERE namespace_id = $1 AND status = 'active' AND embedding IS NOT NULL "
                                 "ORDER BY embedding <=> $2::vector LIMIT $3", ns_id, v, SHOW_K)
        top_a = await conn.fetch(
            "SELECT k.id, COALESCE(d.title, k.source_file) AS title, k.status = 'staging' AS new FROM rag_knowledge k "
            "LEFT JOIN rag_source_document d ON d.id = k.source_document_id WHERE k.namespace_id = $1 AND k.embedding IS NOT NULL "
            "AND k.id <> ALL($4::int[]) AND (k.status = 'active' OR (k.status = 'staging' AND k.ingestion_job_id = ANY($5::int[]))) "
            "ORDER BY k.embedding <=> $2::vector LIMIT $3", ns_id, v, SHOW_K, excl, new_jobs)
        rb, ra = rank_of([r["id"] for r in top_b], gb), rank_of([r["id"] for r in top_a], ga)
        lost = bool(gb) and not ga
        kind = verdict(rb, ra)
        if lost:   # 정답이 없어져 순위도 없다 — 나빠짐과 겹쳐 세지 않는다(화면은 둘을 더해 보여 준다)
            out["gold_lost"] += 1
        elif kind != "same":
            out[kind] += 1
        out["both_miss"] += not lost and (rb is None or rb > TOP_K) and (ra is None or ra > TOP_K)   # 지금도 못 찾던 문항(참고)
        if kind in ("worse", "better") or lost:
            out["details"].append({"question": it["question"], "ns": it["ns"], "qid": it.get("qid"), "kind": kind, "gold_lost": lost,
                                   "before": rb, "after": ra,
                                   "got": [{"title": r["title"], "new": r["new"]} for r in top_a[:TOP_K]]})
        if progress:
            await progress(n, len(todo))
    out["details"].sort(key=lambda d: (d["kind"] != "worse", not d["gold_lost"], d["question"]))
    return out


async def claim(conn, batch_id: int) -> bool:
    """도는 중 표시 잡기 — 이미 도는 중이면 False. 재시작으로 끊긴 표시는 기동 때 close_interrupted가 내린다."""
    return bool(await conn.fetchval(
        "UPDATE rag_import_batch SET golden_check = jsonb_build_object('running', true, 'checked_at', to_char(NOW() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')) "
        "WHERE id = $1 AND COALESCE(golden_check->>'running', '') <> 'true' RETURNING id", batch_id))


async def golden_check(batch_id: int) -> dict:
    """뒤에서 도는 [골든셋 확인] — 진행(running·done·total)과 결과를 배치 golden_check에. 끝 저장(finally)이 running을 지운다."""
    from datetime import datetime, timezone
    from core.database import get_conn

    async def save(doc: dict) -> None:
        async with get_conn() as conn:
            await conn.execute("UPDATE rag_import_batch SET golden_check = $2::jsonb WHERE id = $1", batch_id, json.dumps(doc, ensure_ascii=False))

    out: dict = {"checked_at": datetime.now(timezone.utc).isoformat()}
    try:   # 여기서부터 무엇이 실패해도 finally가 도는 중 표시를 지운다(가져오기 실패 포함 — 리뷰 11/17 중간)
        from shared.embedding import embedding_service
        from service.eval.questions import real_knowledge_golden
        items, used = load_file_items()
        async with get_conn() as conn:
            jobs = list(await conn.fetchval("SELECT job_ids FROM rag_import_batch WHERE id = $1", batch_id) or [])
            real = await real_knowledge_golden(conn)
        out.update({"files": used, "real": len(usable(real))})   # real = 전 컬렉션 실사용 골든셋 수(이 배치 몫은 items)
        if not items and not real:
            out["skipped"] = "이 서버에 골든셋이 없어 건너뜀(닻 파일·실사용 골든셋 문항 0)"
            return out
        await save({**out, "running": True})

        async def tick(done: int, total: int) -> None:
            if done == 1 or done % 10 == 0 or done == total:
                await save({**out, "running": True, "done": done, "total": total})
        async with get_conn() as conn:
            out.update(await run_check(conn, jobs, items + real, embedding_service.embed_batch, tick))
        if not out["items"]:
            out["skipped"] = "이 배치 컬렉션의 골든셋 문항이 없음"
    except Exception as e:   # noqa: BLE001 — 뒤에서 도는 일이라 화면은 이유를 모른다 — 사유를 남긴다(로그)
        logger.warning("게시 전 골든셋 확인 중단 batch=%s", batch_id, exc_info=True)
        out["error"] = f"골든셋 확인이 중간에 멈췄어요({type(e).__name__}) — 다시 눌러 주세요"
    finally:
        await save(out)
    return out
