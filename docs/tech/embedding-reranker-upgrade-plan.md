# 임베딩·리랭커 모델 교체 계획 (v2.72 — 둘 다 실측·확정 완료)

> 작성일: 2026-09-07. 발표자료(RAG 코어) 준비 중 "지금 임베딩 모델이 최선이었나"라는 질문에서
> 출발한 조사. 2026-09-11, 사내 개발망 huggingface.co 접근이 열려 실측 A/B를 실행했고,
> 임베딩은 KURE-v1로 확정 후 전체 재색인까지 완료, 리랭커는 dragonkue/bge-reranker-v2-m3-ko로
> 기본 모델을 교체(`reranker_enabled`는 아직 False)했다(§8).

---

## 1. 왜 다시 보게 됐나

- 현재 임베딩 모델(`paraphrase-multilingual-mpnet-base-v2`)은 프로젝트 최초 커밋(2026-03-04)부터
  고정값이었고, 이후 한 번도 비교·재검토된 적이 없음(git 히스토리 확인)
- 실제 사유는 **로컬(Mac) 개발환경에서 가볍게 돌아갈 CPU 모델이 필요했기 때문** — 다국어 지원/
  외부유출없음/API비용없음은 결과적으로 따라온 이점이지, 애초에 비교해서 고른 이유는 아니었음
- 리랭커(`cross-encoder/ms-marco-MiniLM-L-6-v2`)도 마찬가지로 영어 전용 모델을 그대로 쓰는 중이며
  현재 `reranker_enabled=False`로 비활성 상태(`core/config.py:63-64`)

---

## 2. 조사된 사실 — 임베딩 후보

### 2-1. MTEB-ko-retrieval 리더보드 실측 점수 (nDCG@10)

| 모델 | 점수 | 비고 |
|---|---|---|
| **KURE-v1** (고려대 nlpai-lab) | **0.7616** | BAAI/bge-m3를 한국어 검색 데이터(약 200만 쌍)로 파인튜닝, MIT 라이선스 |
| dragonkue/BGE-m3-ko | 0.7547 | BGE-M3의 또 다른 한국어 파인튜닝 버전 |
| BAAI/bge-m3 (원본) | 0.7509 | 다국어 범용, 8192토큰 지원 |
| KoE5 | 0.7337 | |
| paraphrase-multilingual-mpnet-base-v2 (현재) | 리더보드에 없음 | 최신 한국어 특화 벤치마크 비교 대상에서 아예 빠짐 — 구형 취급 |

- 상위권끼리(KURE-v1 vs BGE-M3)는 격차가 1%p대로 크지 않음
- 다만 현재 모델은 이 비교 자체에 낄 만큼도 안 쳐준다는 점이 신호 — 실제 격차는 이보다 클 가능성

### 2-2. 스펙 비교

| | 현재 모델 | BGE-M3 / KURE-v1 계열 |
|---|---|---|
| 파라미터 | 2.78억 | 5.6억 |
| 임베딩 차원 | **768** | **1024** |
| 최대 컨텍스트 | 128토큰 | 8192토큰 |
| 추론 메모리(대략) | ~1.1GB | ~2.5GB |
| 라이선스 | Apache 2.0 | MIT |

- **차원이 768→1024로 바뀌는 게 실질적인 걸림돌** — DB 컬럼(`VECTOR(768)`) 스키마 변경 +
  기존 등록 지식·정책서·용어집 전량 재임베딩 필요. "서버 갖춰지면"이라는 표현은 사실 GPU가
  필요해서가 아니라(목표 서버 스펙 4vCore/8GB RAM도 1024차원 모델 충분히 감당 — `docs/infra-brief.md`),
  재임베딩 작업 규모를 감안해 인프라 전환 시점에 같이 묶어서 하는 게 낫다는 의미에 가까움
- 8192토큰 컨텍스트는 부수 효과로 VOC 긴 이메일이 128토큰에서 잘리는 기존 알려진 문제
  (`shared/embedding.py`의 `embed_long()` 우회 로직 필요했던 원인)도 근본적으로 해결됨

## 3. 조사된 사실 — 리랭커 후보

| 모델 | 특징 |
|---|---|
| **dragonkue/bge-reranker-v2-m3-ko** | BAAI/bge-reranker-v2-m3를 한국어로 파인튜닝 — dragonkue가 임베딩(BGE-m3-ko)과 리랭커를 같은 계열로 둘 다 냄 |
| BAAI/bge-reranker-v2-m3 | 다국어 원본, 빠른 연산 |
| Dongjin-kr/ko-reranker | 구형 bge-reranker-large 기반 한국어 파인튜닝 |
| cross-encoder/ms-marco-MiniLM-L-6-v2 (현재) | 영어 전용 — 한국어 텍스트엔 애초에 안 맞는 선택 |

- 임베딩을 KURE-v1(BGE-M3 계열)로 간다면, 리랭커도 같은 BGE-M3 계열인 `dragonkue/bge-reranker-v2-m3-ko`로
  맞추는 게 자연스러움 — 같은 벡터공간 가정 위에서 만들어진 조합이라 실측 검증도 한 번에 묶어서 할 수 있음

## 4. 이번 세션에서 막힌 지점

- `huggingface.co`가 사내 개발망에서 도메인 레벨로 막혀 있음(2026-09-07 확인):
  - 일반 인터넷(google.com, pypi.org)은 정상
  - `huggingface.co`는 TCP 핸드셰이크는 되지만 HTTP 응답 단계에서 연결 끊김
  - `cdn-lfs.huggingface.co`는 DNS 조회부터 실패
  - 호스트(Windows)·`ops-backend` 컨테이너 둘 다 동일하게 막힘 — 컨테이너 네트워크 우회 안 됨
  - 프록시 환경변수(`HTTP_PROXY`/`HTTPS_PROXY`)도 미설정 — 사내 HF 미러가 있는지는 확인 안 됨
- 그래서 BGE-M3/KURE-v1/dragonkue 계열 모델을 실제로 내려받아 **우리 데이터로 실측 A/B는
  못 돌렸음** — 지금까지의 추천은 전부 공개 벤치마크 수치 기반이지 우리 프로젝트 데이터 기준 검증은 아님

## 5. 준비해둔 것 — 실측 스크립트

`backend/scripts/bench_embedding_models.py` — §9(저장소 실험실) Track2의 Track A(지식-only
벡터검색) 방식을 그대로 재현해, 골든셋 89문항 + 실제 policy_item 378건으로 후보 임베딩
모델들의 hit@10을 자동 비교한다. 네트워크(huggingface.co 접근)만 확보되면 바로 실행 가능:

```bash
docker exec -it ops-backend python scripts/bench_embedding_models.py
```

지금 스크립트에 들어있는 후보: 현재 모델 / BGE-M3 / dragonkue/BGE-m3-ko / KURE-v1.
리랭커 비교(hit@10 → hit@1 개선폭 측정)는 아직 스크립트에 없음 — 임베딩 모델 1차 확정 후 추가 예정.

## 6. 1차 추천 (실측 전 잠정)

- **임베딩**: KURE-v1 — 공개 벤치마크 1위 + 한국어 검색 특화 파인튜닝 + MIT 라이선스, 8192토큰으로
  VOC 긴 메일 이슈도 같이 해결
- **리랭커**: dragonkue/bge-reranker-v2-m3-ko — 같은 BGE-M3 계열로 임베딩과 짝 맞춤, 한국어 특화

**단, 이건 실측 전 잠정 추천이다.** §9(실험실)에서 이미 "1차 측정에서 가설이 기각된 전례"(파라미터
질문 34.8%→82.6%로 뒤집힌 사례)가 있었던 만큼, 공개 리더보드 1위가 우리 데이터에서도 1위라는
보장은 없음 — 반드시 골든셋 실측 후 확정할 것.

## 7. 다음 세션 시작 지점 (완료 — §8 참고)

1. ~~huggingface.co 접근 방법 확보~~ — 2026-09-11 사내망 접근 확인, 완료
2. ~~`backend/scripts/bench_embedding_models.py` 실행 → 임베딩 모델 1차 확정~~ — 완료, KURE-v1
3. 리랭커 후보 비교(`backend/scripts/bench_reranker_models.py`) — 완료, §8 참고
4. ~~DB 마이그레이션(`VECTOR(768)`→`VECTOR(1024)`, 전량 재임베딩)~~ — 완료

## 8. 실측 결과 (2026-09-11, 확정·적용 완료)

### 8-1. 임베딩 — KURE-v1 확정, 전량 재색인 완료

89문항 골든셋, Track A 스타일(순수 벡터, `policy_item.raw_body` 378건) hit@10:

| 모델 | 전체 | param | narrative | navigation | condition_filter |
|---|---|---|---|---|---|
| 현재(mpnet, 768차원) | 55.1% | 73.9% | 60.9% | 37.5% | 47.4% |
| BGE-M3 | 77.5% | 91.3% | 87.0% | 70.8% | 57.9% |
| dragonkue/BGE-m3-ko | 80.9% | 91.3% | 91.3% | 66.7% | 73.7% |
| **KURE-v1** | **82.0%** | 91.3% | 91.3% | 70.8% | 73.7% |

MTEB-ko-retrieval 공개 벤치마크 1위 순위와 실측 순위가 일치 — KURE-v1로 확정. `vector(768)`
보유 8개 테이블(`sql_*` 3개는 Text2SQL 미등록으로 제외) 전량 `vector(1024)`로 스키마 변경 +
재임베딩 완료(`backend/scripts/migrate_embedding_model.py`).

재색인 후 실 프로덕션 API(`POST /api/policy/track2/run`, v2.71 조사/어미 제거와 함께 작동)로
재검증한 하이브리드(RDB+벡터) 결과:

| | v2.71까지(조사제거만) | v2.72(+KURE-v1) |
|---|---|---|
| 전체 hit@10 | 82.0% | **88.8%**(+6.8%p) |
| narrative | 91.3% | **100%** |
| navigation | 66.7% | **79.2%** |
| condition_filter | 73.7% | **78.9%** |
| precision@10 | 8.9% | **10.8%**(잡음도 같이 감소) |

세션 시작 시점(v2.70) 대비로는 hit@10 77.5%→88.8%(+11.3%p) — 조사/어미 제거(v2.71)와
임베딩 모델 교체(v2.72)가 서로 다른 채널(RDB·벡터)에서 각자 기여해 누적됨.

**실행 중 겪은 문제와 원인**: 재임베딩 스크립트가 `rag_knowledge` 단계에서 매번 죽음(exit
137). asyncio 이벤트루프 문제로 의심하고 여러 차례 구조를 바꿔봤으나 무관했고, 진짜 원인은
2026-09-10 DB/공통코드 정리 때 "판단 대기"로 남겨뒀던 잔여물 id=17(22KB 마크다운 표 블롭)의
토큰 수가 12,061개로 KURE-v1의 max_seq_length(8192)를 넘은 것 — 임베딩 입력 텍스트를
안전하게 자르는 처리를 추가해 해결.

### 8-2. 리랭커 — dragonkue/bge-reranker-v2-m3-ko 확정 (기본 모델만 교체, 아직 비활성)

`backend/scripts/bench_reranker_models.py` — 실 프로덕션 하이브리드 검색(top-20 후보, 정답이
후보 안에 있는 81/89문항 대상)을 CrossEncoder 후보로 재정렬했을 때 hit@1:

| 모델 | hit@1 | 재정렬 없음 대비 |
|---|---|---|
| (재정렬 없음, baseline) | 53.1% | - |
| 현재 기본값(ms-marco-MiniLM, 영어전용) | 19.8% | **-33.3%p — 오히려 크게 악화** |
| **dragonkue/bge-reranker-v2-m3-ko** | **64.2%** | **+11.1%p — 1위** |
| BAAI/bge-reranker-v2-m3 | 63.0% | +9.9%p |
| Dongjin-kr/ko-reranker | 51.9% | -1.2%p — 사실상 무의미 |

예상대로 영어 전용 모델이 한국어 텍스트에 그대로 쓰이면 도움이 아니라 방해가 된다는 게
실측으로 확인됨(`reranker_enabled=False`로 꺼져 있던 게 옳은 판단이었음). 같은 BGE-M3
계열로 임베딩(KURE-v1)과 짝을 맞춘다는 §3의 잠정 추천대로 dragonkue/bge-reranker-v2-m3-ko가
1위 — `core/config.py`의 `reranker_model` 기본값과 `Dockerfile`의 `ARG RERANKER_MODEL`을
이걸로 교체(v2.72). **단, `reranker_enabled`는 아직 `False`로 유지** — 리랭킹은 정책 검색
뿐 아니라 VOC 관련성 게이트(`shared/reranker.py:score()`)에도 쓰여 앱 전체 동작에 영향을
주므로, 활성화 여부는 별도 검증 후 결정.
