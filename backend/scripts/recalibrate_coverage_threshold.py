"""_COVERAGE_MIN_SIMILARITY(pattern_detection.py) 정밀 재보정 — LLM 대량 검증.

배경: 2026-09-15 임베딩 교체(mpnet-768→KURE-v1) 후 이 값이 재검증 없이 방치돼 있던 걸
발견. 당시 DevX 크레딧이 월간 초과(403)라 대량 검증이 불가해 수동 8쌍(직접 판단, LLM
미사용)만으로 0.70→0.55 잠정 하향해뒀다. 크레딧 복구 확인(2026-09-15 오후)됐으니 이제
`_verify_coverage_with_llm`(pattern_detection.py, DevX 경유)로 실제 대량 검증을 돌려
Youden's J로 최적 컷오프를 다시 잡는다. VOC 임계치 재보정(email_pattern_similarity_
threshold, 0.58→0.80) 때 쓴 것과 동일한 방법론.

표본: ops_voc_cluster(현재 120건) × rag_knowledge(활성 임베딩 보유 24건)의 코사인
유사도 0.30~0.90 구간 전체를 0.05 폭 bin으로 나눠 각 bin에서 최대 5쌍씩 층화추출
(전체 다 돌리면 2880쌍이라 과함, 임계치 근처 분포만 촘촘히 보면 충분).

실행 (컨테이너 안):
    docker compose exec backend python scripts/recalibrate_coverage_threshold.py
"""
import asyncio
import random
import sys

sys.path.insert(0, "/app")

from core.database import init_pool, close_pool, get_conn
from service.email_voc.pattern_detection import _verify_coverage_with_llm, _COVERAGE_MIN_SIMILARITY

BIN_WIDTH = 0.05
BIN_MIN = 0.30
BIN_MAX = 0.90
SAMPLES_PER_BIN = 5
SEED = 42


async def fetch_candidate_pairs() -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT c.id AS cluster_id, c.representative_subject AS subject,
                   k.id AS knowledge_id, LEFT(k.content, 800) AS content,
                   1 - (c.representative_embedding <=> k.embedding) AS similarity
            FROM ops_voc_cluster c
            CROSS JOIN rag_knowledge k
            WHERE k.namespace_id = c.namespace_id
              AND (k.status IS NULL OR k.status = 'active') AND k.embedding IS NOT NULL
              AND 1 - (c.representative_embedding <=> k.embedding) BETWEEN $1 AND $2
            """,
            BIN_MIN, BIN_MAX,
        )
    return [dict(r) for r in rows]


def stratified_sample(pairs: list[dict]) -> list[dict]:
    random.seed(SEED)
    bins: dict[int, list[dict]] = {}
    for p in pairs:
        idx = int((p["similarity"] - BIN_MIN) / BIN_WIDTH)
        bins.setdefault(idx, []).append(p)
    sampled = []
    for idx, members in sorted(bins.items()):
        chosen = random.sample(members, min(SAMPLES_PER_BIN, len(members)))
        sampled.append((BIN_MIN + idx * BIN_WIDTH, chosen))
    return sampled


def youden_j(labeled: list[tuple[float, bool]]) -> tuple[float, float, float, float]:
    """(최적 threshold, J, TPR, FPR) 반환 — 관측된 유사도 값들을 후보 threshold로 스윕."""
    thresholds = sorted({round(s, 3) for s, _ in labeled})
    pos = [s for s, v in labeled if v]
    neg = [s for s, v in labeled if not v]
    best = (0.0, -1.0, 0.0, 0.0)
    for t in thresholds:
        tpr = sum(1 for s in pos if s >= t) / len(pos) if pos else 0.0
        fpr = sum(1 for s in neg if s >= t) / len(neg) if neg else 0.0
        j = tpr - fpr
        if j > best[1]:
            best = (t, j, tpr, fpr)
    return best


async def main() -> None:
    await init_pool()
    try:
        pairs = await fetch_candidate_pairs()
        print(f"[fetch] 후보쌍 {len(pairs)}건 (유사도 {BIN_MIN}~{BIN_MAX})")
        binned = stratified_sample(pairs)
        total_sampled = sum(len(m) for _, m in binned)
        print(f"[sample] {len(binned)}개 bin, 총 {total_sampled}쌍 LLM 검증 예정\n")

        labeled: list[tuple[float, bool]] = []
        for bin_start, members in binned:
            for p in members:
                verdict = await _verify_coverage_with_llm(p["subject"], p["content"])
                labeled.append((p["similarity"], verdict))
                print(f"  sim={p['similarity']:.3f} cluster={p['cluster_id']:>4} "
                      f"kb={p['knowledge_id']:>4} -> {'RELEVANT' if verdict else 'irrelevant'}  "
                      f"[{p['subject'][:30]}]")

        n_true = sum(1 for _, v in labeled if v)
        n_false = len(labeled) - n_true
        print(f"\n[결과] 총 {len(labeled)}쌍 — true={n_true}, false={n_false}")

        if n_true == 0 or n_false == 0:
            print("[경고] 한쪽 클래스가 0건이라 Youden's J 계산 불가 — bin 범위/표본 재검토 필요")
            return

        best_t, j, tpr, fpr = youden_j(labeled)
        print(f"\n[Youden's J] 최적 threshold={best_t:.3f}  J={j:.3f}  TPR={tpr:.3f}  FPR={fpr:.3f}")

        cur_tpr = sum(1 for s, v in labeled if v and s >= _COVERAGE_MIN_SIMILARITY) / n_true
        cur_fpr = sum(1 for s, v in labeled if not v and s >= _COVERAGE_MIN_SIMILARITY) / n_false
        print(f"[현재값 {_COVERAGE_MIN_SIMILARITY:.2f}] TPR={cur_tpr:.3f}  FPR={cur_fpr:.3f}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
