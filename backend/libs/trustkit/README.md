# trustkit — 신뢰도 레이어

검색 기반 답변 시스템이 **믿을 수 있는 답**을 내게 하는 순수 계산 모음. 표준 라이브러리만 쓰고, DB·벡터 저장소·임베딩·LLM 호출은
쓰는 쪽이 맡는다. OpsLens(SMAgentLab)에서 실측으로 고른 기법만 담았다. 버전 0.x — 두 번째 실사용처 검증 전까지 인터페이스가 바뀔 수 있다.

## 단계별 기법

| 단계 | 모듈 | 기법 | 출처 | OpsLens 실측 |
|---|---|---|---|---|
| 적재 | `ingest.quality.assess` | 저장 청크 구조 검사(이웃 중복·제목 매달림·제목만) | 자체(10/7 장애) | 2주간 지표에 안 보이던 20쌍 중복을 잡음 |
| 적재 | `ingest.invariants.check_chunks` | 분할 불변식(유실·중복·잘림·크기·상위 맥락) | 성질 기반 테스트 | 분할기 변경의 완료 기준 |
| 색인 | `retrieval.chunk_header.contextual_header` | 맥락 머리(임베딩 입력에만) | Anthropic Contextual Retrieval(2024) | Recall@10 88.6→92.0% |
| 검색 | `text.ko.tokenize` | 조사·어미 제거, 금액 정규화 | 규칙 기반 | 키워드 채널 hit@10 34.8→73.9% |
| 검색 | `retrieval.bm25.BM25Index` | BM25 | Robertson & Zaragoza(2009) | 드문 단어 36위→상위 |
| 검색 | `retrieval.fusion.best_per_doc` | 문서 단위 묶기(MaxP) | Dai & Callan(2019) | 상위 자리 중복 제거 |
| 검색 | `retrieval.fusion.rescue_order` | 구제 합치기(주 채널 순서 유지) | RRF(Cormack 2009)의 변형 | Recall@10 →95.5%, 나빠진 문항 0 |
| 질문 | `query.intent` | 탐색·조건 질문 판별(규칙) | — | 정밀도 96% / 조건형 18/18 |
| 문맥·답 | `answer.shaping.list_scoped` | 범위 질문 문맥 거르기 | self-query / metadata filtering | 탐색형 62→75% |
| 문맥·답 | `answer.shaping.CONDITION_ANSWER_HINT_KO` | 조건 질문 답 구조 | — | 조건형 58→78% |
| 평가 | `eval.stats` | 군집 표준오차·t 구간·짝 차이·카파 | Miller, "Adding Error Bars to Evals"(2024) | 1회 측정 오차 ±8~10%p 확인 |
| 평가 | `eval.gate` | 전/후 판정, McNemar | McNemar(1947) | 변경마다 통과/실패 |

## 사용 예

```python
from trustkit.retrieval.bm25 import BM25Index
from trustkit.retrieval.fusion import best_per_doc, rescue_order
from trustkit.eval.gate import answer_gate

idx = BM25Index([(1, "SKU 구매 제한 투고백 2세트"), (2, "배송비 30,000원 이상 무료")])
kw_ids = [i for i, _ in idx.score("3만 원 넘으면 배송비?", top_k=30)]          # → [2]

vec_hits = best_per_doc(chunk_hits_sorted_by_score, key=lambda h: h.doc_id)     # 내 벡터 검색 결과
order = rescue_order([h.doc_id for h in vec_hits], kw_ids, top_k=10)            # 문맥에 넣을 문서 순서

verdict = answer_gate(before_rows, after_rows)   # 행: {"qid", "label", "type", "cluster", "rep"}
print(verdict["pass"], verdict["groups"]["전체"])
```

## 설치
- 같은 저장소: `PYTHONPATH`에 `libs/trustkit` 추가(OpsLens Dockerfile 방식)
- 다른 프로젝트: `pip install "git+<저장소>#subdirectory=backend/libs/trustkit"` 또는 wheel(`pip wheel backend/libs/trustkit`) 반입

## 규칙
- 패키지 안에서 쓰는 쪽 코드(`core`·`service`·`shared`·`agents`)를 import하지 않는다 — `tests/test_trustkit.py::test_independence_no_app_imports`가
  강제(외부 패키지 금지는 리뷰로 — 지금은 전부 표준 라이브러리).
- 기법을 바꾸면 위 표의 실측 칸과 출처를 같이 고친다.
