"""반복 VOC 패턴 탐지 — 임베딩 유사도 + 시간창 + 임계치, LLM 호출 없음.

배경: "IT와 상관없는 배달/음료 자체 불만까지 너무 많이 온다"는 피드백 이후,
"그럼 반복되는 유형인지는 알려줄 수 없냐"는 요구로 이어짐. 매 건마다 LLM을
태워 유형화하면 비용이 커진다는 우려에 외부 사례(Sentry 지문 방식, Drain
로그 템플릿 마이닝, 고객지원 티켓 중복탐지, PagerDuty/BigPanda류 알림
deduplication)를 조사한 결과, 이미 관련성 게이트(service.check_relevance())가
계산하는 임베딩을 그대로 재사용해 "과거 VOC와의 유사도"를 pgvector 코사인
연산으로만 비교하는 방식으로 결정 — 추가 LLM 호출이 전혀 없다.

임계치 검증(2026-08-25): 실제 VOC 데이터(과거 4주 이력 + 그 시점 VOC 폴더
현재 데이터 54건)로 여러 조합을 시뮬레이션 — 0.90은 거의 안 잡히고(같은
유형이어도 표현이 다르면 못 넘음), 0.80은 무관한 것끼리도 묶여버림(예: 배달
지연/파손/오배송을 전부 "배달 관련"으로 뭉뚱그림). **0.85 유사도 / 7일 창 /
3건 이상**이 노이즈 없이 진짜 반복 신호(예: 오배송·미도착 계열 VOC 7건이
하나의 클러스터로 정확히 묶임)를 잡아내는 지점으로 확인됐다.

스레드 답장 중복제거: 같은 대화의 RE:/Re: 답장을 "새로운 반복 발생"으로
세면 안 된다(한 사람이 계속 얘기하는 것과 여러 건이 반복 발생하는 것은
다르다) — 제목에서 답장 접두어를 벗겨 같은 제목이면 후보에서 제외한다.
이 보정만으로 시뮬레이션에서 트리거 건수가 42% 줄었다.

알림 정책: 클러스터가 처음 임계치(min_count)를 넘는 순간에만 Teams 알림을
1회 발송한다(ops_voc_cluster.notified_at으로 가드) — 그 뒤로 같은 클러스터에
멤버가 계속 늘어도 재알림하지 않는다(이력에는 계속 기록됨). "멤버 하나당
알림 1번"이 되면 결국 매 건 알림으로 되돌아가 노이즈가 커지기 때문.
"""
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from core.database import get_conn, resolve_namespace_id

logger = logging.getLogger(__name__)

_REPLY_PREFIX_RE = re.compile(r"^\s*(re|fwd?|회신|답장|전달)\s*[:：]\s*", re.IGNORECASE)
# rag_knowledge와 코사인 유사도가 이 이상이면 "이 반복 유형에 대한 해결방안이
# 이미 등록돼 있다"로 판단 — knowledge_min_score(관련지식 인용 임계치)보다
# 엄격하게 잡는다: 여긴 "그냥 관련 있음"이 아니라 "이 반복 패턴 자체를 다루는
# 지식이 있는가"를 묻는 것이라 오탐(느슨하게 커버됐다고 오판)의 대가가 더 크다.
_COVERAGE_MIN_SIMILARITY = 0.75


def _normalize_subject(subject: str) -> str:
    """RE:/Re:/Fwd:/회신:/전달: 접두어를 전부 벗겨 "같은 대화(스레드)"인지 비교 가능하게 만든다."""
    s = subject or ""
    while True:
        new_s = _REPLY_PREFIX_RE.sub("", s)
        if new_s == s:
            break
        s = new_s
    return s.strip().lower()


async def detect_and_update_cluster(
    ns_id: int, analysis_id: int, subject: str, embedding: list[float],
    occurred_at: datetime, settings: dict,
) -> Optional[dict]:
    """이 VOC를 기존 반복 클러스터에 합류시키거나, 조건이 맞으면 새 클러스터를 만든다.

    Returns:
        None — 반복 신호 없음(유사한 과거 VOC가 없거나 전부 같은 스레드 답장뿐).
        {"cluster_id", "member_count", "trigger": dict | None} — trigger는 이번에
        처음 min_count를 넘겨 Teams 알림을 발송해야 하는 경우에만 채워진다.
    """
    threshold = settings["email_pattern_similarity_threshold"]
    window_days = settings["email_pattern_window_days"]
    min_count = settings["email_pattern_min_count"]
    norm_subject = _normalize_subject(subject)
    window_start = occurred_at - timedelta(days=window_days)

    async with get_conn() as conn:
        candidates = await conn.fetch(
            """
            SELECT id, subject, voc_cluster_id
            FROM ops_email_analysis
            WHERE namespace_id = $1 AND id != $2 AND embedding IS NOT NULL
              AND created_at >= $3
              AND 1 - (embedding <=> $4::vector) >= $5
            ORDER BY embedding <=> $4::vector
            """,
            ns_id, analysis_id, window_start, str(embedding), threshold,
        )
        candidates = [c for c in candidates if _normalize_subject(c["subject"]) != norm_subject]
        if not candidates:
            return None

        existing_cluster_id = next((c["voc_cluster_id"] for c in candidates if c["voc_cluster_id"]), None)
        if existing_cluster_id is not None:
            cluster = await conn.fetchrow(
                """
                UPDATE ops_voc_cluster SET member_count = member_count + 1, last_seen_at = $2
                WHERE id = $1
                RETURNING id, member_count, notified_at, representative_subject
                """,
                existing_cluster_id, occurred_at,
            )
        else:
            # 신규 클러스터 — 가장 유사한 이전 건(candidates[0])과 함께 2건으로 시작
            best = candidates[0]
            cluster = await conn.fetchrow(
                """
                INSERT INTO ops_voc_cluster
                    (namespace_id, representative_subject, representative_embedding,
                     member_count, first_seen_at, last_seen_at)
                VALUES ($1, $2, $3::vector, 2, $4, $5)
                RETURNING id, member_count, notified_at, representative_subject
                """,
                ns_id, best["subject"], str(embedding), occurred_at, occurred_at,
            )
            await conn.execute(
                "UPDATE ops_email_analysis SET voc_cluster_id = $1 WHERE id = $2",
                cluster["id"], best["id"],
            )

        await conn.execute(
            "UPDATE ops_email_analysis SET voc_cluster_id = $1 WHERE id = $2",
            cluster["id"], analysis_id,
        )

        trigger = None
        if cluster["member_count"] >= min_count and cluster["notified_at"] is None:
            await conn.execute("UPDATE ops_voc_cluster SET notified_at = NOW() WHERE id = $1", cluster["id"])
            trigger = {
                "member_count": cluster["member_count"],
                "representative_subject": cluster["representative_subject"],
                "sample_subjects": [c["subject"] for c in candidates[:3]],
            }

    return {"cluster_id": cluster["id"], "member_count": cluster["member_count"], "trigger": trigger}


async def list_clusters(namespace: str) -> list[dict]:
    """관리자 화면 "반복 유형" 목록 — 클러스터별 커버리지(해결방안 등록 여부) 포함.

    멤버가 1건뿐인 "클러스터 후보"는 애초에 ops_voc_cluster에 만들지 않으므로
    (detect_and_update_cluster는 유사한 과거 건이 있을 때만 클러스터를 생성함)
    여기 나오는 건 전부 실제로 2건 이상 반복된 것들이다.

    커버리지 판정을 LATERAL 서브쿼리로 DB 안에서 끝낸다 — asyncpg가 pgvector
    컬럼을 파이썬 list가 아니라 텍스트 문자열('[0.1,0.2,...]')로 반환하므로
    (이 코드베이스에 벡터 컬럼 codec이 등록돼 있지 않음, 실측 확인), 클러스터별로
    벡터를 파이썬으로 꺼냈다가 다시 문자열로 넣는 왕복을 피하고 find_similar_
    active_knowledge()와 동일한 비교를 SQL 레벨에서 그대로 수행한다.
    """
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT c.id, c.representative_subject, c.member_count,
                   c.first_seen_at, c.last_seen_at, c.notified_at,
                   best.knowledge_id, best.similarity, best.snippet,
                   cat.primary_category, cat.breakdown AS category_breakdown,
                   sev.primary_severity
            FROM ops_voc_cluster c
            LEFT JOIN LATERAL (
                SELECT k.id AS knowledge_id,
                       1 - (k.embedding <=> c.representative_embedding) AS similarity,
                       LEFT(k.content, 100) AS snippet
                FROM rag_knowledge k
                WHERE k.namespace_id = c.namespace_id
                  AND (k.status IS NULL OR k.status = 'active') AND k.embedding IS NOT NULL
                ORDER BY k.embedding <=> c.representative_embedding
                LIMIT 1
            ) best ON true
            -- 같은 반복 유형인데도 LLM이 건마다 다른 category로 분류하면 분류
            -- 일관성 문제를 의심할 신호다 — 다수결(primary)은 필터링용, 전체
            -- 분포(breakdown)는 관리자가 그 불일치를 직접 확인할 근거로 노출한다.
            LEFT JOIN LATERAL (
                SELECT (array_agg(category ORDER BY cnt DESC))[1] AS primary_category,
                       json_agg(json_build_object('category', category, 'count', cnt) ORDER BY cnt DESC) AS breakdown
                FROM (
                    SELECT category, COUNT(*) AS cnt FROM ops_email_analysis
                    WHERE voc_cluster_id = c.id AND category IS NOT NULL
                    GROUP BY category
                ) t
            ) cat ON true
            LEFT JOIN LATERAL (
                SELECT (array_agg(severity ORDER BY cnt DESC))[1] AS primary_severity
                FROM (
                    SELECT severity, COUNT(*) AS cnt FROM ops_email_analysis
                    WHERE voc_cluster_id = c.id AND severity IS NOT NULL
                    GROUP BY severity
                ) t
            ) sev ON true
            WHERE c.namespace_id = $1
            ORDER BY c.last_seen_at DESC
            """,
            ns_id,
        )

    clusters = []
    for r in rows:
        covered = r["similarity"] is not None and r["similarity"] >= _COVERAGE_MIN_SIMILARITY
        clusters.append({
            "id": r["id"], "representative_subject": r["representative_subject"],
            "member_count": r["member_count"], "first_seen_at": r["first_seen_at"],
            "last_seen_at": r["last_seen_at"], "notified_at": r["notified_at"],
            "has_knowledge_coverage": covered,
            "matched_knowledge_id": r["knowledge_id"] if covered else None,
            "matched_knowledge_snippet": r["snippet"] if covered else None,
            "matched_knowledge_similarity": round(r["similarity"], 2) if r["similarity"] is not None else 0.0,
            "primary_category": r["primary_category"],
            "primary_severity": r["primary_severity"],
            "category_breakdown": json.loads(r["category_breakdown"]) if r["category_breakdown"] else [],
        })
    return clusters


async def get_cluster_members(namespace: str, cluster_id: int) -> list[dict]:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            """
            SELECT id, subject, sender, category, severity, reasoning, created_at
            FROM ops_email_analysis
            WHERE namespace_id = $1 AND voc_cluster_id = $2
            ORDER BY created_at ASC
            """,
            ns_id, cluster_id,
        )
    return [dict(r) for r in rows]
