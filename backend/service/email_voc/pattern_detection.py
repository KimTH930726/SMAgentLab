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

알림 정책(2026-08-25 수정): 처음엔 "클러스터가 처음 min_count를 넘는 순간에만
표시"였다 — 반복 패턴이 완전히 별도의 Teams 메시지였을 때는 "멤버 하나당
알림 1번"이 되면 매 건 알림으로 되돌아가 노이즈가 커지는 문제였기 때문.
그런데 이후 별도 메시지를 만들지 않고 이미 발송 중인 개별 VOC 카드에 한 줄
얹는 방식으로 바뀌면서(teams_notify.py 참고) 이 제약의 전제가 사라졌다 —
메시지 자체는 어차피 매번 나가므로, 반복 표시를 매번 넣어도 "추가 알림"이
생기는 게 아니다. 오히려 "처음 한 번만" 표시하면 4번째·5번째 발생 때는
반복 중이라는 맥락이 안 보이는 문제가 실사용 중 발견돼(20건짜리 클러스터의
멤버 대부분이 표시 없이 나감) — 이제는 min_count를 넘긴 "이후 모든 건"에
매번 표시한다. notified_at은 이제 표시 여부 가드가 아니라 "이 클러스터가
언제 처음 패턴으로 인지됐는지" 기록용으로만 남는다.

체이닝 방지(2026-08-25): 처음엔 "클러스터 안 아무 멤버와 유사하면 합류"였는데,
A~B, B~C, C~D처럼 사슬로 이어지면 A와 D가 실제로는 안 닮았는데도 같은
클러스터에 묶이는 문제가 실 데이터에서 확인됐다(단일 링크 클러스터링의 전형적
결함 — "배달 오배송 불만" 클러스터가 37건까지 불어나며 category가 뒤섞임).
이제는 새 VOC를 "클러스터 전체의 대표(centroid)"와 비교해서 합류 여부를
판단하고, 합류할 때마다 centroid를 점증 갱신(가중 평균 후 재정규화)한다 —
클러스터가 커질수록 그 유형의 "평균적 의미"에서 벗어난 건 자연히 안 붙는다.
"""
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from core.database import get_conn, resolve_namespace_id

logger = logging.getLogger(__name__)

_REPLY_PREFIX_RE = re.compile(r"^\s*(re|fwd?|회신|답장|전달)\s*[:：]\s*", re.IGNORECASE)
# rag_knowledge와 코사인 유사도가 이 이상이면 "이 반복 유형에 대한 해결방안이
# 이미 등록돼 있다"로 판단한다.
#
# 실측 보정 히스토리(2026-08-25) — 두 번 틀렸다가 세 번째로 정착:
# ① 처음 0.75: 실측 없이 "다른 임계치보다 엄격해야 한다"는 추측만으로 정함 —
#    실제로 클러스터에 딱 맞는 해결방안을 등록해봐도 0.63~0.70이라 못 넘김(미탐).
# ② 클러스터 2개 vs 손으로 고른 "무관한 문장" 2~3개로 재보정해 0.50으로 내림 —
#    그런데 실 지식 베이스(6천여 건, 주제가 다양함)에 대고 돌려보니 전 클러스터가
#    "커버됨"으로 뜸(오탐 폭주). 원인: 대상 후보가 코너케이스 몇 개가 아니라 6천
#    건짜리 corpus 전체이다 보니, 완전 무관한 광고 메일("(광고) W컨셉 세일")도
#    "임직원 할인 적용" 문서와 0.62, 뉴스레터 스팸도 표 형식 문서와 0.56이 나옴 —
#    순수 코사인 유사도는 "같은 도메인 어휘를 쓴다"는 것만 감지하지 진짜 의미적
#    관련성을 못 가른다(§7-12에서 리랭커 필요성이 나왔던 것과 같은 근본 원인).
# ③ 진짜 관련 매칭(0.63)과 명백한 오탐(0.56~0.62)의 유사도 구간이 겹쳐서, 순수
#    코사인만으로는 완벽히 못 가른다 — "오탐(허위로 커버됐다고 오판)이 미탐보다
#    위험하다"는 원칙(관리자가 진짜 갭을 놓침)에 따라 0.70으로 보수적으로 설정,
#    오탐 사례를 확실히 걸러내는 쪽을 택함. 대가: 진짜 관련 지식도 종종 "등록
#    필요"로 잘못 뜰 수 있음(미탐) — 그래도 관리자가 직접 확인하면 되니 안전한
#    쪽의 실패 모드다. 근본 해결은 리랭커(현재 이 개발 환경에서 huggingface.co
#    접근 불가로 미검증) 도입 시 재검토.
_COVERAGE_MIN_SIMILARITY = 0.70


def _normalize_subject(subject: str) -> str:
    """RE:/Re:/Fwd:/회신:/전달: 접두어를 전부 벗겨 "같은 대화(스레드)"인지 비교 가능하게 만든다."""
    s = subject or ""
    while True:
        new_s = _REPLY_PREFIX_RE.sub("", s)
        if new_s == s:
            break
        s = new_s
    return s.strip().lower()


def _parse_vec(pgvector_text: str) -> np.ndarray:
    """asyncpg가 pgvector 컬럼을 파이썬 list가 아니라 텍스트('[0.1,0.2,...]')로
    반환하므로(코덱 미등록, 실측 확인) centroid 갱신처럼 파이썬에서 직접 벡터
    연산을 해야 할 때 파싱이 필요하다."""
    return np.array([float(x) for x in pgvector_text.strip("[]").split(",")], dtype=np.float32)


def _weighted_average_normalize(existing: np.ndarray, existing_weight: int, new_vec: list[float]) -> list[float]:
    """클러스터 대표 임베딩(centroid)을 멤버가 늘 때마다 점증적으로 갱신한다.

    existing은 이미 정규화(단위벡터)된 이전 centroid — 원래 합(sum)을 따로
    저장해두지 않으므로, "정규화 전 평균 크기가 대략 1"이라고 근사해
    existing_weight를 곱해 합을 복원한 뒤 새 벡터를 더해 다시 평균·정규화한다
    (온라인 k-means류 스트리밍 centroid 갱신에서 흔히 쓰는 근사). 여기서는
    임계치 기반 유사도 비교에만 쓰이므로 이 정도 근사 오차는 문제되지 않는다.
    """
    approx_sum = existing * existing_weight + np.array(new_vec, dtype=np.float32)
    mean = approx_sum / (existing_weight + 1)
    norm = np.linalg.norm(mean)
    if norm > 0:
        mean = mean / norm
    return mean.tolist()


async def detect_and_update_cluster(
    ns_id: int, analysis_id: int, subject: str, embedding: list[float],
    occurred_at: datetime, settings: dict,
) -> Optional[dict]:
    """이 VOC를 기존 반복 클러스터에 합류시키거나, 조건이 맞으면 새 클러스터를 만든다.

    합류 여부는 클러스터의 centroid(representative_embedding)와 비교해서
    판단한다(모듈 docstring의 "체이닝 방지" 참고) — 개별 멤버가 아니라 클러스터
    전체의 평균 의미와 비교해야 사슬처럼 안 닮은 것까지 묶이는 걸 막을 수 있다.

    Returns:
        None — 반복 신호 없음(유사한 클러스터/과거 VOC가 없거나 같은 스레드 답장뿐).
        {"cluster_id", "member_count", "pattern_info": dict | None} — pattern_info는
        이 클러스터가 min_count를 넘긴 "이후"라면 매번 채워진다(처음 넘긴 순간만이
        아님, 2026-08-25 변경). 반복 패턴 표시는 이제 별도 Teams 메시지가 아니라
        이미 발송 중인 개별 VOC 카드에 한 줄 얹는 것뿐이라(teams_notify.py의
        pattern_info 파라미터), "1번만 보여주면" 오히려 4번째·5번째 발생부터는
        "이거 반복되는 유형인데?"라는 맥락이 안 보이는 문제가 실사용 중 발견됨
        — 매 메시지에 표시해도 메시지 자체가 늘어나는 게 아니므로 노이즈가 안 커진다.
    """
    threshold = settings["email_pattern_similarity_threshold"]
    window_days = settings["email_pattern_window_days"]
    min_count = settings["email_pattern_min_count"]
    norm_subject = _normalize_subject(subject)
    window_start = occurred_at - timedelta(days=window_days)

    async with get_conn() as conn:
        best_cluster = await conn.fetchrow(
            """
            SELECT id, member_count, representative_embedding
            FROM ops_voc_cluster
            WHERE namespace_id = $1 AND last_seen_at >= $2
              AND 1 - (representative_embedding <=> $3::vector) >= $4
            ORDER BY representative_embedding <=> $3::vector
            LIMIT 1
            """,
            ns_id, window_start, str(embedding), threshold,
        )

        if best_cluster is not None:
            member_rows = await conn.fetch(
                "SELECT subject FROM ops_email_analysis WHERE voc_cluster_id = $1", best_cluster["id"],
            )
            if any(_normalize_subject(r["subject"]) == norm_subject for r in member_rows):
                return None  # 같은 스레드 답장 — 반복 발생으로 세지 않음

            new_centroid = _weighted_average_normalize(
                _parse_vec(best_cluster["representative_embedding"]), best_cluster["member_count"], embedding,
            )
            cluster = await conn.fetchrow(
                """
                UPDATE ops_voc_cluster
                SET member_count = member_count + 1, last_seen_at = $2, representative_embedding = $3::vector
                WHERE id = $1
                RETURNING id, member_count, notified_at, representative_subject
                """,
                best_cluster["id"], occurred_at, str(new_centroid),
            )
            await conn.execute(
                "UPDATE ops_email_analysis SET voc_cluster_id = $1 WHERE id = $2", cluster["id"], analysis_id,
            )
        else:
            # 아직 클러스터가 없는 단독 VOC들과 비교해 새 클러스터를 만들지 판단
            candidates = await conn.fetch(
                """
                SELECT id, subject, embedding
                FROM ops_email_analysis
                WHERE namespace_id = $1 AND id != $2 AND voc_cluster_id IS NULL AND embedding IS NOT NULL
                  AND created_at >= $3
                  AND 1 - (embedding <=> $4::vector) >= $5
                ORDER BY embedding <=> $4::vector
                """,
                ns_id, analysis_id, window_start, str(embedding), threshold,
            )
            candidates = [c for c in candidates if _normalize_subject(c["subject"]) != norm_subject]
            if not candidates:
                return None

            best = candidates[0]
            new_centroid = _weighted_average_normalize(_parse_vec(best["embedding"]), 1, embedding)
            cluster = await conn.fetchrow(
                """
                INSERT INTO ops_voc_cluster
                    (namespace_id, representative_subject, representative_embedding,
                     member_count, first_seen_at, last_seen_at)
                VALUES ($1, $2, $3::vector, 2, $4, $4)
                RETURNING id, member_count, notified_at, representative_subject
                """,
                ns_id, best["subject"], str(new_centroid), occurred_at,
            )
            await conn.execute(
                "UPDATE ops_email_analysis SET voc_cluster_id = $1 WHERE id = $2", cluster["id"], best["id"],
            )
            await conn.execute(
                "UPDATE ops_email_analysis SET voc_cluster_id = $1 WHERE id = $2", cluster["id"], analysis_id,
            )

        pattern_info = None
        if cluster["member_count"] >= min_count:
            # notified_at은 더 이상 "표시 여부"를 가리지 않는다 — 이 클러스터가
            # 처음 패턴으로 인지된 시각을 기록해두는 용도로만 남긴다(대시보드 등에서
            # "언제 처음 반복 패턴으로 잡혔는지" 참고 가능).
            if cluster["notified_at"] is None:
                await conn.execute("UPDATE ops_voc_cluster SET notified_at = NOW() WHERE id = $1", cluster["id"])
            # 흔한 문구("[파손] 음료 쏟아짐 불만" 등)는 서로 다른 고객이 똑같은 제목을
            # 쓰는 경우가 실제로 많다 — 중복 제목을 그대로 나열하면 "똑같은 내용이
            # 두 번 반복 표시"된 것처럼 보여 혼란만 준다(실사용 피드백). 대표 제목과
            # 겹치는 것도 제외 — 이미 위에서 별도로 보여주므로.
            sample_rows = await conn.fetch(
                "SELECT subject FROM ops_email_analysis WHERE voc_cluster_id = $1 LIMIT 6", cluster["id"],
            )
            unique_samples = list(dict.fromkeys(
                r["subject"] for r in sample_rows if r["subject"] != cluster["representative_subject"]
            ))
            pattern_info = {
                "member_count": cluster["member_count"],
                "representative_subject": cluster["representative_subject"],
                "sample_subjects": unique_samples[:3],
            }

    return {"cluster_id": cluster["id"], "member_count": cluster["member_count"], "pattern_info": pattern_info}


async def get_cluster_coverage(ns_id: int, cluster_id: int) -> dict:
    """반복 패턴 Teams 알림 발송 시점에 그 클러스터의 해결방안 등록 여부를 확인.

    list_clusters()의 LATERAL JOIN 커버리지 판정과 동일한 로직을 단일 클러스터에
    대해서만 수행 — 트리거는 클러스터당 1번만 발생하므로 비용 문제 없음. "반복
    발생했다"는 사실만 알리고 끝내면 받는 사람이 "그래서 뭘 어떻게 해야 하냐"를
    또 물어보게 된다는 게 실사용 피드백이라, 이미 등록된 해결방안이 있으면 카드에
    바로 보여주고 없으면 명시적으로 "없다"고 알린다.
    """
    async with get_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT best.knowledge_id, best.similarity, best.snippet
            FROM ops_voc_cluster c
            LEFT JOIN LATERAL (
                SELECT k.id AS knowledge_id,
                       1 - (k.embedding <=> c.representative_embedding) AS similarity,
                       LEFT(k.content, 200) AS snippet
                FROM rag_knowledge k
                WHERE k.namespace_id = c.namespace_id
                  AND (k.status IS NULL OR k.status = 'active') AND k.embedding IS NOT NULL
                ORDER BY k.embedding <=> c.representative_embedding
                LIMIT 1
            ) best ON true
            WHERE c.id = $1 AND c.namespace_id = $2
            """,
            cluster_id, ns_id,
        )
    if row is None:
        return {"covered": False, "snippet": None}
    covered = row["similarity"] is not None and row["similarity"] >= _COVERAGE_MIN_SIMILARITY
    return {"covered": covered, "snippet": row["snippet"] if covered else None}


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
