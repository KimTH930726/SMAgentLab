"""적재 검수 — 설계서의 완료 기준을 적재 계약으로 보고 지금 DB가 지키는지 잰다 (2026-10-26, 사용자 "지금 있는 데이터 구조가 잘 적재돼 있는지
자체적으로 검수하는 개선 루프"). LLM 0, 숫자·id만 출력.

계기: 정책 v0 설계(§1·§8 #9)는 "원문 통째가 기준, 파라미터는 원문에서 파생해 적재 직후 별도 단계로" 였는데 그 단계가 실행되지 않아
현행 파라미터 0건 — 채팅의 파라미터 검색 경로가 빈 표로 돌고 있었는데 아무 데서도 알려 주지 않았다. 업계 방식(데이터 계약·기대값 검사:
데이터별 기대값 → 자동 검사 → 담당·조치)을 우리 설계서 완료 기준에 맞춘 것.

검사마다: 무엇(설계 근거) · 어긋난 수 / 전체 · 예시 id · 이 항목을 정답으로 쓰는 골든 문항의 정답률(정확도와 연결 — 기준선 판정의 답 결과 파일).
실행(컨테이너 안): python scripts/check_ingest.py [--run 판정id(기본 기준선)]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/libs/trustkit")
import logging  # noqa: E402
logging.disable(logging.INFO)

from trustkit.eval.gate import per_question  # noqa: E402

GOLDEN_DIR = Path("/app/eval_out/golden_v0/reviewed")
NUM = re.compile(r"\d")



# (키, 저장소, 이름, 설계 근거, SQL — 어긋난 행의 (id, source_key 또는 NULL)과 전체 수를 돌려줌)
CHECKS: list[tuple[str, str, str, str, str, str]] = [
    # 파생을 돌렸는데 값이 없는 정책(번호만 있는 등)과 아예 안 돌린 것을 가르려고 실행 표시(ops_data_marker policy_param_derive = 시각)를 본다 —
    # 표시가 없으면 숫자 있는 정책 전부, 있으면 그 뒤에 생긴 정책 중 파라미터 없는 것만(리뷰 M3: 다 돌려도 640건이 남아 고쳤는지 알 수 없었다)
    ("policy_param_derived", "정책", "파라미터 파생(숫자 있는 정책)", "policy-v0-design §1·§8 #9 — 원문에서 파생해 적재 직후 별도 단계",
     """SELECT i.id, i.source_key FROM policy_item i WHERE i.status = 'active' AND i.raw_body ~ '[0-9]'
        AND NOT EXISTS (SELECT 1 FROM policy_param p WHERE p.policy_item_id = i.id)
        AND (NOT EXISTS (SELECT 1 FROM ops_data_marker WHERE key = 'policy_param_derive')
             OR i.created_at > (SELECT value::timestamptz FROM ops_data_marker WHERE key = 'policy_param_derive'))""",
     "SELECT count(*) FROM policy_item WHERE status = 'active' AND raw_body ~ '[0-9]'"),
    ("policy_one_current", "정책", "정책마다 현행 하나", "policy-v0-design §7 — 승인 = 옛 버전 은퇴",
     """SELECT i.id, i.source_key FROM policy_item i WHERE i.status = 'active' AND i.logical_id IN
        (SELECT logical_id FROM policy_item WHERE status = 'active' GROUP BY logical_id HAVING count(*) > 1)""",
     "SELECT count(DISTINCT logical_id) FROM policy_item WHERE status = 'active'"),
    ("policy_key_unique", "정책", "원천 키 하나에 현행 하나", "policy-v0-design §5 — 골든·JSON이 원천 키로 찾음",
     """SELECT i.id, i.source_key FROM policy_item i WHERE i.status = 'active' AND i.source_key IN
        (SELECT source_key FROM policy_item WHERE status = 'active' AND source_key IS NOT NULL GROUP BY source_key HAVING count(*) > 1)""",
     "SELECT count(*) FROM policy_item WHERE status = 'active'"),
    ("policy_has_key", "정책", "현행 정책에 원천 키", "policy-lifecycle-design §3 — 키 없으면 골든·내려받기에서 빠짐",
     "SELECT id, NULL FROM policy_item WHERE status = 'active' AND source_key IS NULL",
     "SELECT count(*) FROM policy_item WHERE status = 'active'"),
    ("policy_body", "정책", "원문이 비어 있지 않음", "policy-v0-design §5-3",
     "SELECT id, source_key FROM policy_item WHERE status = 'active' AND length(coalesce(raw_body, '')) < 10",
     "SELECT count(*) FROM policy_item WHERE status = 'active'"),
    ("policy_chunk", "정책", "검색 조각·임베딩 있음", "policy-v0-design §5-3 — 정책 적재 + 임베딩",
     """SELECT i.id, i.source_key FROM policy_item i WHERE i.status = 'active'
        AND NOT EXISTS (SELECT 1 FROM policy_chunk c WHERE c.policy_item_id = i.id AND c.embedding IS NOT NULL)""",
     "SELECT count(*) FROM policy_item WHERE status = 'active'"),
    # 머리("정책명 (분류 > …): 조각")는 임베딩 입력에만 붙는다 — 저장 조각·답 문맥은 그대로(service/policy/service.py). 그래서 저장 글이 아니라
    # "지금 머리 규칙으로 다시 임베딩했나"(버전 표시)를 본다 — 첫 판은 저장 글을 봐서 688건을 잘못 잡았다(10/26 확인)
    ("policy_embed_header", "정책", "조각 임베딩에 정책 이름 머리(맥락)", "service/policy/service.py CHUNK_EMBED_VERSION — 제목만으론 뜻이 안 통함(v0 §4)",
     """SELECT 0, NULL WHERE coalesce((SELECT value FROM ops_data_marker WHERE key = 'policy_chunk_embed'), '') <> 'header1'""",
     "SELECT 1"),
    ("policy_parse", "정책", "변환 때 못 읽은 조각 없음", "policy-v0-design §4 변환기 불변식",
     """SELECT id, source_key FROM policy_item WHERE status = 'active'
        AND (coalesce(parse_status, 'ok') NOT IN ('ok', 'parsed') OR coalesce(jsonb_array_length(to_jsonb(unresolved_segments)), 0) > 0)""",
     "SELECT count(*) FROM policy_item WHERE status = 'active'"),
    ("ref_rows", "참고표", "참고표에 행이 있음", "policy-v0-design §8 #8 — 원표·압축·요약",
     """SELECT t.id, t.source_key FROM ref_table t WHERE t.status = 'active'
        AND NOT EXISTS (SELECT 1 FROM ref_table_row r WHERE r.ref_table_id = t.id)""",
     "SELECT count(*) FROM ref_table WHERE status = 'active'"),
    ("ref_embedding", "참고표", "참고표 임베딩", "policy-v0-design §8 #8",
     "SELECT id, source_key FROM ref_table WHERE status = 'active' AND embedding IS NULL",
     "SELECT count(*) FROM ref_table WHERE status = 'active'"),
    # 공백·줄바꿈만 다른 것도 같은 내용(11/04 중복 판정 2단 ② — 등록 때 거부하는 기준과 같게, trustkit.ingest.dedupe.norm_text)
    ("knowledge_dup", "지식", "같은 내용 조각이 한 컬렉션에 두 번(공백만 다른 것 포함)", "개발 규율 — 적재는 중복 0(10/07 실사고: 도입부가 두 조각에 15일)",
     r"""SELECT k.id, NULL FROM rag_knowledge k JOIN (
          SELECT namespace_id, md5(btrim(regexp_replace(content, '[\s ]+', ' ', 'g'))) h FROM rag_knowledge WHERE coalesce(status, 'active') = 'active'
          GROUP BY 1, 2 HAVING count(*) > 1) d ON d.namespace_id = k.namespace_id AND d.h = md5(btrim(regexp_replace(k.content, '[\s ]+', ' ', 'g')))
        WHERE coalesce(k.status, 'active') = 'active'""",
     "SELECT count(*) FROM rag_knowledge WHERE coalesce(status, 'active') = 'active'"),
    ("knowledge_current", "지식", "문서 조각마다 현행 하나", "원문 원장(source ledger) — 원문 = 기준, 조각 = 파생",
     """SELECT k.id, NULL FROM rag_knowledge k WHERE coalesce(k.status, 'active') = 'active' AND k.logical_document_id IS NOT NULL
        AND (k.logical_document_id, coalesce(k.source_chunk_idx, -1)) IN (   -- 직접 입력은 조각 번호 NULL — NULL끼리도 같은 조각(리뷰 L4)
          SELECT logical_document_id, coalesce(source_chunk_idx, -1) FROM rag_knowledge WHERE coalesce(status, 'active') = 'active'
          AND logical_document_id IS NOT NULL GROUP BY 1, 2 HAVING count(*) > 1)""",
     "SELECT count(*) FROM rag_knowledge WHERE coalesce(status, 'active') = 'active'"),
    # 제목 같은 줄만 있고 본문 줄이 없는 조각(11/04 ①A) — 규칙은 chunker._is_heading_only와 같게: 모든 줄이 # 제목 또는 60자 미만 번호 제목
    # (문장 끝 없음)이고 # 아닌 번호 줄은 하나까지. 줄 앞뒤 공백은 nbsp(컨플)까지 지운다 — Python strip과 같게(리뷰 L2)
    ("knowledge_heading_only", "지식", "제목만 있는 조각 없음", "chunk-structure-dedupe-design ①A — 혼자선 검색·답에 쓸모없고 중복 의심만 늘림",
     r"""WITH l AS (SELECT k.id, btrim(regexp_replace(ln, '[\s ]+', ' ', 'g')) t FROM rag_knowledge k
                  CROSS JOIN LATERAL regexp_split_to_table(k.content, E'\n') ln WHERE coalesce(k.status, 'active') = 'active')
        SELECT id, NULL FROM l WHERE t <> '' GROUP BY id
        HAVING bool_and(t ~ '^#' OR (length(t) < 60 AND t ~ '^(\(?[0-9]+([.-][0-9]+)*[.)]|\(참고\)|\[참고\])\s*\S' AND t !~ '(\.|다|요)$'))
           AND count(*) FILTER (WHERE t !~ '^#') <= 1""",
     "SELECT count(*) FROM rag_knowledge WHERE coalesce(status, 'active') = 'active'"),
    ("knowledge_embedding", "지식", "임베딩 있음·같은 모델", "임베딩 KURE-v1(1024) 한 모델",
     """SELECT id, NULL FROM rag_knowledge WHERE coalesce(status, 'active') = 'active'
        AND (embedding IS NULL OR coalesce(embedding_model, '') <> (SELECT mode() WITHIN GROUP (ORDER BY embedding_model)
             FROM rag_knowledge WHERE coalesce(status, 'active') = 'active'))""",
     "SELECT count(*) FROM rag_knowledge WHERE coalesce(status, 'active') = 'active'"),
    ("knowledge_context", "지식", "문서에서 온 조각에 문서 위치(제목 경로)", "청크 머리 — 맥락 없는 조각 방지(직접 입력 제외)",
     """SELECT id, NULL FROM rag_knowledge WHERE coalesce(status, 'active') = 'active'
        AND coalesce(array_length(heading_path, 1), 0) = 0 AND source_file IS NULL
        AND coalesce(source_type, '') <> 'manual'""",   # 직접 입력 지식은 문서 위치가 원래 없다(10/26 확인: 걸린 18건 중 18건이 직접 입력)
     "SELECT count(*) FROM rag_knowledge WHERE coalesce(status, 'active') = 'active'"),
    ("glossary_embedding", "용어", "용어 임베딩", "v2.128 — 용어집 화면 벡터 검색·동의어 게이트",
     "SELECT id, NULL FROM rag_glossary WHERE embedding IS NULL", "SELECT count(*) FROM rag_glossary"),
]


# 쓰임 검사 — 적재는 됐는데 채팅이 안 쓰는 데이터(10/26 사용자 "적재한 데이터가 채팅에서 실제로 쓰이나"). 정답이 그 데이터인 골든 문항을
# 실제 채팅 문맥 조립(build_chat_context, LLM 0)에 넣어, 정답 데이터의 제목이 문맥에 들어가는지 센다. 코드를 읽어 "연결 안 됨"이라 적지 않고 잰다.
USAGE_KINDS = {
    "ref_table": ("참고표", "SELECT source_key, title FROM ref_table WHERE status = 'active'", "policy-v0-design §8 #12 — 참고표 채팅 연결"),
    "overview": ("개요", "SELECT source_key, title FROM policy_doc_overview WHERE status = 'active'", "policy-v0-design §8 #12 — 개요 채팅 연결"),
}


async def usage_checks(conn) -> list[dict]:
    from shared.embedding import embedding_service
    from agents.knowledge_rag.agent import build_chat_context
    from agents.knowledge_rag.knowledge import retrieval
    await retrieval.load_runtime_overrides_from_db(conn)
    d = retrieval.get_search_defaults()
    embedding_service.load()
    gold = [g for f in sorted(GOLDEN_DIR.glob("*.jsonl")) for g in _rows(f) if g.get("verdict") == "approved"]
    out = []
    for kind, (label, sql, ref) in USAGE_KINDS.items():
        titles = {r["source_key"]: r["title"] for r in await conn.fetch(sql)}
        items = [g for g in gold if g.get("gold") and {x["kind"] for x in g["gold"]} == {kind}]
        hit, n, no_ns, missed = 0, 0, 0, []
        for g in items:
            ns = (g.get("asked_from") if g.get("layer") == "cross" else None) or g.get("doc")   # eval_golden_v0·eval_answers와 같은 규칙
            names = [titles.get(x["source_key"]) for x in g["gold"] if titles.get(x["source_key"])]
            if not ns or not names:
                no_ns += 1
                continue
            vec = await embedding_service.embed(g["query"])
            cc = await build_chat_context(ns, g["query"], vec, top_k=int(d["default_top_k"]), w_vector=d["default_w_vector"],
                                          w_keyword=d["default_w_keyword"])
            if cc.clarify:   # 되물으면 "전부 보기"를 고른 문맥으로(eval_answers와 같게)
                cc = await build_chat_context(ns, g["query"], vec, top_k=int(d["default_top_k"]), w_vector=d["default_w_vector"],
                                              w_keyword=d["default_w_keyword"], clarify_scope="*", list_question=g["query"])
            n += 1
            used = any(t in cc.llm_context for t in names)
            hit += used
            if not used:
                missed.append(g["qid"])
        out.append({"key": f"usage_{kind}", "store": label, "name": f"{label}가 채팅 문맥에 쓰임(정답이 {label}인 문항)", "ref": ref,
                    "bad": n - hit, "total": n, "sample": [], "keys": set(), "qids": sorted(missed),
                    "note": f"정답 데이터를 못 찾은 문항 {no_ns}" if no_ns else ""})
    return out


async def param_text_check(conn) -> dict:
    """파라미터 값이 원문에 있나 — 파생 규칙(param_derive.value_in_text)과 같은 기준으로(SQL position은 쉼표 차이를 오탐, 리뷰 L3)."""
    from service.policy.param_derive import value_in_text
    rows = await conn.fetch("""SELECT p.id, p.value, i.raw_body, i.source_key FROM policy_param p JOIN policy_item i ON i.id = p.policy_item_id
                               WHERE i.status = 'active' AND p.value IS NOT NULL""")
    bad = [r for r in rows if not value_in_text(r["value"], r["raw_body"])]
    return {"key": "policy_param_in_text", "store": "정책", "name": "파라미터 값이 원문에 글자로 있음", "ref": "policy-v0-design §8 #9 완료 기준",
            "bad": len(bad), "total": len(rows), "sample": [r["id"] for r in bad[:5]], "keys": {r["source_key"] for r in bad if r["source_key"]}}


def _rows(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, help="정답률을 붙일 판정 기록(기본: 기준선)")
    ap.add_argument("--no-usage", action="store_true", help="쓰임 검사(채팅 문맥 조립, 임베딩 필요 — 1~2분) 건너뛰기")
    ap.add_argument("--save", action="store_true", help="검사 기록 저장(평가 게이트 [적재 검수]·다음 eval_improve.py 후보가 읽음)")
    a = ap.parse_args()
    from core.database import close_pool, get_conn, init_pool
    from service.eval import files

    await init_pool()
    try:
        async with get_conn() as conn:
            run = await conn.fetchrow("SELECT id, label, summary FROM eval_gate_run WHERE " + ("id = $1" if a.run else "is_baseline AND axis = 'policy'"),   # 기준선은 정책서 축(#96)
                                      *([a.run] if a.run else []))
            results = []
            for key, store, name, ref, bad_sql, total_sql in CHECKS:
                try:
                    bad = await conn.fetch(bad_sql)
                    total = await conn.fetchval(total_sql)
                except Exception as e:  # noqa: BLE001 — 칸이 없는 환경(옛 DB)은 그 검사만 건너뛴다(표시)
                    results.append({"key": key, "store": store, "name": name, "ref": ref, "error": type(e).__name__})
                    continue
                results.append({"key": key, "store": store, "name": name, "ref": ref, "bad": len(bad), "total": total or 0,
                                "sample": [r[0] for r in bad[:5]], "keys": {r[1] for r in bad if r[1]}})
            results.append(await param_text_check(conn))
            if not a.no_usage:
                results += await usage_checks(conn)
        # 정확도 연결 — 어긋난 정책을 정답으로 쓰는 골든 문항의 정답률 vs 나머지
        acc = {}
        if run:
            summary = json.loads(run["summary"]) if isinstance(run["summary"], str) else run["summary"]
            ans_p = files.find_result((summary.get("inputs") or {}).get("answers_after"))
            if ans_p:
                from trustkit.eval.summary import scored_rows   # 판정불가·장애(게이트웨이 거부 등) 제외 — 평가 게이트와 같은 규칙(10/29)
                ans = [{"qid": r["qid"], "label": r["judge"]["label"], "type": r.get("type"), "rep": r.get("rep", 0)}
                       for r in scored_rows(_rows(ans_p))]
                # 잠근 시험지(사람·실사용)는 빼고 — 이 정답률·영향 문항이 개선 후보를 만든다(고칠 때 잠근 문항을 보지 않는 원칙, 리뷰 M1)
                from service.eval.improve import split_locked
                ans, _ = split_locked(ans)
                acc, _ = per_question(ans)
        gold_keys: dict[str, set] = {}
        for f in sorted(GOLDEN_DIR.glob("*.jsonl")):
            for g in _rows(f):
                if g.get("verdict") == "approved":
                    gold_keys[g["qid"]] = {x.get("source_key") for x in g.get("gold", [])}
        from core.config import settings
        from service.eval.improve import LATEST_INGEST_DECISIONS_SQL, decided_off, feature_on
        async with get_conn() as conn:   # 꺼 둔 결정 = 개선 후보의 사람 결정(기각·보류) — 코드에 적지 않는다(10/29 사용자 "하드코딩이니?")
            decisions = {r["key"]: dict(r) for r in await conn.fetch(LATEST_INGEST_DECISIONS_SQL)}
        print(f"적재 검수 — 정답률은 판정 #{run['id'] if run else '-'} 기준 · 검사 {len(results)}개(적재 {len(CHECKS)} + 쓰임)")
        for r in results:
            if "error" in r:
                print(f"  [건너뜀] {r['store']} · {r['name']} — {r['error']}")
                continue
            dec = decided_off(r["key"], decisions, feature_on=feature_on(r["key"], settings))
            if dec and r["bad"]:
                r["decided"] = dec   # 저장에도 남겨 개선 후보·화면이 어긋남으로 세지 않게
            ok = "통과" if r["bad"] == 0 else ("꺼 둠(결정)" if dec else "어긋남")
            line = f"  [{ok}] {r['store']} · {r['name']}: {r['bad']} / {r['total']}"
            if r["keys"] and acc:
                hit = [q for q, ks in gold_keys.items() if ks & r["keys"] and q in acc]
                # 비교는 정답 정책이 있는 다른 문항끼리 — 답 없는 질문(정답 정책 없음)을 섞으면 비교가 뜻이 없다
                rest = [q for q, ks in gold_keys.items() if q in acc and ks - {None} and q not in hit]
                # 후보가 겨냥할 문항 = 영향 문항 중 틀린 것(맞힌 문항까지 "고치려는 문항"으로 보이던 것, 리뷰 L1). 나머지가 없으면 비교 불가로 저장
                r["qids"] = sorted(q for q in hit if acc[q] < 0.5)
                r["accuracy"] = {"n": len(hit), "acc": sum(acc[q] for q in hit) / len(hit) if hit else None,
                                 "rest_n": len(rest), "rest_acc": sum(acc[q] for q in rest) / len(rest) if rest else None}
                if hit:
                    line += f" · 이 항목을 정답으로 쓰는 문항 {len(hit)}개 정답률 {sum(acc[q] for q in hit) / len(hit) * 100:.1f}%"
                    line += (f" (나머지 {len(rest)}개 {sum(acc[q] for q in rest) / len(rest) * 100:.1f}%)" if rest
                             else " (비교할 나머지 없음 — 전부 해당)")
            if r["bad"] and r["sample"]:
                line += f" · 예시 id {r['sample']}"
            if r.get("note"):
                line += f" · {r['note']}"
            print(line)
            print(f"           근거: {r['ref']}")
        if a.save:   # 원문 없이 — 키(source_key 집합)는 빼고 개수·qid·정답률만
            rows = [{k: v for k, v in r.items() if k != "keys"} for r in results]
            async with get_conn() as conn:
                rid = await conn.fetchval(
                    "INSERT INTO ingest_check_run (created_by, gate_run_id, results, n_bad) VALUES ($1, $2, $3::jsonb, $4) RETURNING id",
                    "script", run["id"] if run else None, json.dumps(rows, ensure_ascii=False, default=list),
                    sum(1 for r in rows if (r.get("bad") and not r.get("decided")) or r.get("error")))   # 검사 못 한 것도 "볼 것"으로(통과로 숨기지 않게, 리뷰 M2) · 결정으로 꺼 둔 건 빼고
            print(f"적재 검수 #{rid} 저장")
        return 0
    finally:
        await close_pool()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
