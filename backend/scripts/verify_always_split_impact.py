"""always_split 규칙의 실측 전/후 비교 (2026-09-22).

confluence-chunking-spec.md §3의 방법론(id=68 실 문서, 실 임베딩 모델, 실제 활성
지식과 함께 코사인 유사도·순위 비교)을 오늘 실제로 짠 `_chunk_by_sections(always_split=)`
코드로 재현한다 — 스펙 문서의 수치는 당시 수동 시뮬레이션이었고, 이번엔 실제 구현
코드를 직접 호출해 같은 방식으로 재측정한다.

실행: docker compose exec backend python scripts/verify_always_split_impact.py
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "/app")

from core.database import init_pool, get_conn
from shared.embedding import embedding_service
from agents.knowledge_rag.ingestion.chunker import _chunk_by_sections
from agents.knowledge_rag.ingestion.adapters import ParsedDocument

# id=68 원문("팥빙수/컵빙수 출시 보고서")을 실제 저장된 content에서 그대로 재구성한
# 섹션 구조 — level은 번호의 점(.) 개수로 근사(backfill_heading_path.py와 동일 관례).
SECTIONS = [
    {"title": "팥빙수/컵빙수 출시 관련 판매 제어 및 전시 로직 고도화 보고서", "level": 0, "content": ""},
    {"title": "1. 개요 (Overview)", "level": 1, "content": (
        "작업 목적 : 신규 메뉴(팥빙수)의 SKU별 판매 시간대(시작~종료) 제어 로직 구현 "
        "컵빙수(음료 카테고리)의 일회용품 노출 영역 확장 (foodtargetYN 파라미터 활용) "
        "기존 Oracle Function 수정에 따른 전방위 API 영향도 파악 및 검증\n"
        "관련 티켓/이슈 : Jira aaa818e1-4d47-3bd1-b64b-bd312edfea2a AMSB1-23810\n"
        "작업 기간 : 2026-04-16"
    )},
    {"title": "2. 주요 변경 사항 (Key Changes)", "level": 1, "content": ""},
    {"title": "2.1 아키텍처 및 로직", "level": 2, "content": (
        "판매 시간 제어 : SKU별 시작/종료 시간을 체크하여 판매 가능 시간 외에는 N 플래그 반환\n"
        "일회용품 노출 로직 : 음료 카테고리인 컵빙수를 공통코드 SKU로 관리, 신규 파라미터 "
        "foodtargetYN 을 통해 화면단에서 일회용품 선택 영역 강제 노출 제어"
    )},
    {"title": "2.2 데이터베이스 (DB Schema)", "level": 2, "content": (
        "Oracle Functions : FC_XO_DLVS_STOCK_CART : 장바구니 단계 재고/상태 체크 로직 수정, "
        "FC_XO_DLVS_STOCK_MENU : 전시/카테고리/검색 단계 재고/상태 체크 로직 수정\n"
        "Logic Details : sku_stock_msg 내 2번째 세그먼트( arrStock[1] )를 판매 상태 값( Y/N/T )으로 활용"
    )},
    {"title": "2.3 API 및 인터페이스", "level": 2, "content": (
        "Cart /delivers/cartView.do : allSaleFlag 반영하여 미판매 표시\n"
        "Cart /delivers/cartViewCalc.do : impossibleKey 발생 시 구매 차단 (DELIVERY_033)\n"
        "Menu /delivers/categoryList.do : showSkuCnt 계산 시 시간대 미판매 상품 제외\n"
        "Menu /delivers/skuList.do : orderFlag = N 처리로 목록 미노출\n"
        "Detail /delivers/skuDetailV2.do : 사이즈 목록 제외 및 원두 UX 미판매 문구 노출"
    )},
    {"title": "3. 참고 자료 및 비고 (References & Notes)", "level": 1, "content": (
        "영향도 요약 : 총 17개 쿼리 중 12건 확인 완료 (Java 수정 불필요), "
        "4건(원두 예외 처리 및 홈 화면 SQL) 추가 검토 필요\n"
        "향후 작업 : 컵빙수 SKU 공통코드 등록 및 foodtargetYN 프론트엔드 연동 테스트, "
        "Oracle Function 배포 시점과 앱/웹 반영 시점 동기화 확인"
    )},
]

QUESTIONS = [
    ("2.1 아키텍처 하위 항목", "foodtargetYN 파라미터는 어떤 기능을 하나요?"),
    ("2.2 DB Schema 하위 항목", "장바구니 단계 재고 체크하는 함수 이름이 뭐예요?"),
    ("2.3 API 하위 항목", "skuDetailV2 엔드포인트는 뭘 처리하나요?"),
    ("1 개요 하위 항목", "이 작업 관련 Jira 이슈 번호가 뭔가요?"),
]


def _doc() -> ParsedDocument:
    return ParsedDocument(source_type="confluence", source_name="test", raw_text="", sections=SECTIONS)


def cos_sim(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


async def main() -> None:
    await init_pool()
    embedding_service.load()

    merged = _chunk_by_sections(_doc(), 2000, 50, always_split=False)
    split = _chunk_by_sections(_doc(), 2000, 50, always_split=True)
    print(f"병합(old, always_split=False): {len(merged)}개 청크")
    print(f"분리(new, always_split=True):  {len(split)}개 청크\n")

    async with get_conn() as conn:
        distractor_rows = await conn.fetch(
            "SELECT content FROM rag_knowledge WHERE status='active' AND id != 68"
        )
    distractors = [r["content"] for r in distractor_rows]
    print(f"실제 활성 지식(id=68 제외) distractor 풀: {len(distractors)}건\n")

    merged_texts = [c.text for c in merged]
    split_texts = [c.text for c in split]
    all_texts = list(dict.fromkeys(merged_texts + split_texts + distractors))
    embeddings = {}
    for t in all_texts:
        embeddings[t] = await embedding_service.embed(t)

    print(f"{'질문':30s} | {'병합(순위/유사도)':22s} | {'분리(순위/유사도)':22s}")
    print("-" * 90)
    for label, q in QUESTIONS:
        qvec = await embedding_service.embed(q)

        def rank_and_score(candidate_texts: list[str]) -> tuple[int, float]:
            pool = candidate_texts + distractors
            scored = sorted(((cos_sim(qvec, embeddings[t]), t) for t in pool), reverse=True)
            for rank, (score, text) in enumerate(scored, 1):
                if text in candidate_texts:
                    return rank, score
            return -1, 0.0

        m_rank, m_score = rank_and_score(merged_texts)
        s_rank, s_score = rank_and_score(split_texts)
        print(f"{label:30s} | {m_rank:>3}위 ({m_score:.4f})        | {s_rank:>3}위 ({s_score:.4f})")


if __name__ == "__main__":
    asyncio.run(main())
