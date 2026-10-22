"""VOC 재처리 — 옛 규칙(관련도 입구 필터)으로 건너뛴 메일을 새 순서(반복 묶기 먼저)로 다시 처리한다(10/22).

대상: 지정 영역에서 최근 N시간 안에 들어와 status='skipped_relevance'로 남고 아직 묶이지 않은 메일(키워드로 더 좁힐 수 있음).
이미 분석된 메일은 다시 처리하지 않는다 — 그대로 반복 묶기의 비교 대상이 된다(중복으로 세지 않게).
분석·묶기·발송은 파이프라인과 같은 함수(pipeline.analyze_message · cluster_and_notify)를 쓴다.

  기본(미리보기): 분석 → 메모리에서 묶어 보기 → 묶음마다 주제 이름(LLM, 요약 문구만 넘김) → 발송될 건수. DB·팀즈 안 건드림.
  --send: 분석 결과를 저장하고 받은 순서대로 반복 묶기 → 다 묶은 뒤 기준(3건) 넘은 묶음마다 최종 건수로 팀즈 1건(밀린 메일이라 배수마다 보내지 않음).
  --llm local: 이 실행만 로컬 LLM(서버 설정 그대로). 분석 결과는 eval_out/에 저장해 미리보기 → --send에서 LLM을 다시 부르지 않는다.
출력은 숫자·주제 이름만(메일 원문 없음).

실행(컨테이너): python scripts/voc_reprocess.py --namespace 딜리버스 --since-hours 24 --keyword 프리퀀시 --llm local [--send]
"""
import argparse
import asyncio
import collections
import pickle
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/app")
sys.path.insert(0, "/app/scripts")
import logging  # noqa: E402
logging.disable(logging.INFO)

CACHE_DIR = Path("/app/eval_out")
# 분석 규칙이 바뀌면 올린다 — 옛 규칙으로 만든 캐시(예: 관련 지식 없는데 무관한 근거가 든 분석)를 다시 쓰지 않게(10/22 리뷰)
CACHE_VERSION = 2


def _cos(a, b) -> float:
    a, b = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1.0))


def preview_clusters(items: list[dict], threshold: float) -> tuple[list[list[int]], int]:
    """pattern_detection과 같은 규칙을 메모리에서: 묶음 중심과 유사도 ≥ 기준이면 합류(같은 제목 = 같은 스레드는 안 셈),
    아니면 단독끼리 기준을 넘는 둘이 새 묶음. 기존 DB 묶음·이전 분석 메일은 미리보기에 넣지 않는다(근사)."""
    from service.email_voc.pattern_detection import _weighted_average_normalize
    clusters, singles = [], []   # clusters: {"c": 중심, "m": [i], "s": {제목}}
    for i, it in enumerate(items):
        best = max(((_cos(cl["c"], it["vec"]), cl) for cl in clusters), key=lambda x: x[0], default=(0.0, None))
        if best[1] is not None and best[0] >= threshold:
            cl = best[1]
            if it["subj"] in cl["s"]:
                continue
            cl["c"] = _weighted_average_normalize(np.asarray(cl["c"], dtype=np.float32), len(cl["m"]), it["vec"])
            cl["m"].append(i)
            cl["s"].add(it["subj"])
            continue
        cand = sorted(((_cos(items[j]["vec"], it["vec"]), k) for k, j in enumerate(singles) if items[j]["subj"] != it["subj"]), reverse=True)
        if not cand or cand[0][0] < threshold:
            singles.append(i)
            continue
        j = singles.pop(cand[0][1])
        clusters.append({"c": _weighted_average_normalize(np.asarray(items[j]["vec"], dtype=np.float32), 1, it["vec"]),
                         "m": [j, i], "s": {items[j]["subj"], it["subj"]}})
    return [cl["m"] for cl in sorted(clusters, key=lambda c: -len(c["m"]))], len(singles)


async def name_topic(llm, signatures: list[str]) -> str:
    """묶음 주제 이름 — 메일 원문이 아니라 분석이 뽑은 짧은 요약 문구만 넘긴다."""
    prompt = "다음은 같은 묶음으로 모인 고객 문의의 요약 문구들이다. 공통 주제를 한국어 한 줄(20자 안팎)로만 답하라.\n- " + "\n- ".join(signatures[:15])
    try:
        ans, _ = await llm.generate("", prompt, None, system_prompt="너는 고객 문의 묶음에 짧은 주제 이름을 붙인다. 한 줄만 출력한다.")
        return (ans or "").strip().splitlines()[0][:60] if ans else "(이름 없음)"
    except Exception as e:  # noqa: BLE001
        return f"(이름 실패: {type(e).__name__})"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--since-hours", type=int, default=24)
    ap.add_argument("--keyword", default="", help="제목·본문에 이 글자가 든 메일만")
    ap.add_argument("--llm", choices=["system", "local"], default="system")
    ap.add_argument("--send", action="store_true", help="실제로 저장·묶기·팀즈 발송(없으면 미리보기)")
    a = ap.parse_args()

    from core.database import get_conn, init_pool, resolve_namespace_id
    from shared.embedding import embedding_service
    from service.email_voc import pipeline, routing_service, service as voc_service
    from service.email_voc.pattern_detection import _normalize_subject
    from service.llm.factory import get_llm_provider
    await init_pool()
    embedding_service.load()
    if a.llm == "local":
        from eval_answers import local_llm
        llm = await local_llm()
        voc_service.get_llm_provider = lambda: llm   # 분석도 이 프로세스에서만 로컬 LLM
    else:
        llm = get_llm_provider()
    settings = await routing_service.get_settings()
    routing = next((r for r in await routing_service.list_routing(a.namespace) if r["is_active"]), None)
    if routing is None:
        raise SystemExit(f"{a.namespace}: 활성 메일함 연결 없음")
    async with get_conn() as c:
        ns_id = await resolve_namespace_id(c, a.namespace)
        rows = await c.fetch(
            """SELECT id, subject, sender, body, received_at FROM ops_email_analysis
               WHERE namespace_id = $1 AND status = 'skipped_relevance' AND voc_cluster_id IS NULL
                 AND created_at > now() - make_interval(hours => $2)
                 AND ($3 = '' OR subject ILIKE '%' || $3 || '%' OR body ILIKE '%' || $3 || '%')
               ORDER BY received_at, id""", ns_id, a.since_hours, a.keyword)
    print(f"재처리 대상 {len(rows)}건 · 묶기 기준 {settings['email_pattern_similarity_threshold']} · {settings['email_pattern_min_count']}건마다 발송")
    if not rows:
        return 0

    cache_path = CACHE_DIR / f"voc_reprocess_{ns_id}.pkl"
    saved = pickle.loads(cache_path.read_bytes()) if cache_path.exists() else {}
    cache = saved.get("items", {}) if isinstance(saved, dict) and saved.get("version") == CACHE_VERSION else {}
    items = []
    for r in rows:
        msg = {"subject": r["subject"], "sender": r["sender"], "body": r["body"] or "",
               "received_at": r["received_at"].isoformat() if r["received_at"] else None}
        if r["id"] not in cache:
            analysis, vec, kn = await pipeline.analyze_message(a.namespace, routing["part"], msg, settings["email_relevance_min_score"])
            cache[r["id"]] = {"analysis": analysis, "vec": list(vec), "kn": kn}
            cache_path.write_bytes(pickle.dumps({"version": CACHE_VERSION, "items": cache}))   # 건마다 저장 — 중간에 끊겨도 이어서
        items.append({"id": r["id"], "msg": msg, "subj": _normalize_subject(r["subject"]), **cache[r["id"]]})
    cats = collections.Counter(it["analysis"]["category"] for it in items)
    print(f"분석: {dict(cats)} · 관련 지식 없음 {sum(not it['kn'] for it in items)}건")

    if not a.send:
        groups, n_single = preview_clusters(items, settings["email_pattern_similarity_threshold"])
        min_count = settings["email_pattern_min_count"]
        for g in groups:
            sigs = [items[i]["analysis"].get("issue_signature") or "" for i in g]
            topic = await name_topic(llm, [s for s in sigs if s])
            sends = len(g) // min_count
            print(f"  묶음 {len(g)}건 · 주제: {topic} · 팀즈 {sends}건 · 관련 지식 없음 {sum(not items[i]['kn'] for i in g)}")
        print(f"  단독(안 묶임) {n_single}건 — 미리보기, 바뀐 것 없음(--send로 실제 처리)")
        return 0

    # 받은 순서대로 저장·묶기만 하고(발송 안 함), 다 묶은 뒤 묶음마다 최종 건수로 팀즈 1건 — 밀린 메일에 "3·6·9건째"를 그대로 쓰면
    # 같은 주제 카드가 연달아 여러 장 나간다(10/22 미리보기: 17건 묶음 → 5장).
    outcomes = collections.Counter()
    processed = []   # 이번 실행이 실제로 저장한 메일만 — 다른 실행이 처리한 메일의 묶음까지 보내면 카드가 두 장 나간다(리뷰)
    for it in items:
        an = it["analysis"]
        async with get_conn() as c:
            done = await c.execute(
                """UPDATE ops_email_analysis SET category = $2, severity = $3, mismatch_flagged = $4, knowledge_ref_ids = $5,
                          resolution_draft = $6, reasoning = $7, status = 'analyzed', embedding = $8::vector
                   WHERE id = $1 AND status = 'skipped_relevance'""",
                it["id"], an["category"], an["severity"], an["mismatch_flagged"], an["knowledge_ref_ids"],
                an["resolution_draft"], an["reasoning"], str(it["vec"]))
        if done != "UPDATE 1":   # 다른 실행이 이미 처리함 — 다시 묶으면 같은 메일을 두 번 센다(리뷰 3)
            outcomes["already_processed"] += 1
            continue
        processed.append(it)
        outcomes[await pipeline.cluster_and_notify(ns_id, routing, it["msg"], it["id"], an, it["vec"], it["kn"], settings, send=False)] += 1
    print(f"묶기 결과: {dict(outcomes)}")
    by_id = {it["id"]: it for it in processed}
    if not by_id:
        print("발송 결과: 이번 실행이 처리한 메일 없음")
        return 0
    async with get_conn() as c:
        clusters = await c.fetch(
            """SELECT k.id AS cluster_id, k.member_count, max(a.id) FILTER (WHERE a.id = ANY($1::int[])) AS last_id
               FROM ops_voc_cluster k JOIN ops_email_analysis a ON a.voc_cluster_id = k.id
               GROUP BY k.id, k.member_count HAVING bool_or(a.id = ANY($1::int[])) AND k.member_count >= $2""",
            list(by_id), settings["email_pattern_min_count"])
    sent = collections.Counter()
    for cl in clusters:   # 묶음당 1건: 가장 늦게 들어온 재처리 메일을 대표로, 최종 건수로
        it = by_id[cl["last_id"]]
        r = await pipeline.notify_cluster(ns_id, routing, it["msg"], it["id"], it["analysis"], cl["cluster_id"],
                                          cl["member_count"], it["kn"], settings)
        sent[r] += 1
        print(f"  묶음 {cl['cluster_id']}: {cl['member_count']}건 → {r}")
    print(f"발송 결과: {dict(sent) or '보낼 묶음 없음'}")
    return 0 if not sent.get("notify_failed") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
