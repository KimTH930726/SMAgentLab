# 자가 개선 루프 — 평가 게이트 · AI 감사 로그 · 적용 이력 (2026-10-27 밤)

> 상태: 구현(v2.180, 10/27 밤 구현 · 10/28 커밋).
> 사용자 10/27: "평가 게이트랑 감사 로그는 자가 개선을 위한 과정", "감사 로그는 사용자가 기능을 이용할 때 AI가 자동 추천한 걸 사람이 번복했을 때의 로그 —
> 그걸 기준으로 자가 개선 루프를 돌리는 게 목적", "업계 대표 사례·표준을 참고해 우리 것으로 맞춰서". 목업: claude.ai artifact 8muYpFtiuagwAK2TkYQZ4o(일곱 장).

## 1. 지금 막힌 일
- 번복 기록(ops_ai_decision)은 쌓이는데 자가 개선 진단(`scripts/eval_improve.py`)이 읽지 않는다 — 시험지(골든) 틀린 문항만 본다. 운영에서 사람이 고친 신호가 개선으로 안 이어진다.
- 번복 이유를 받는 곳이 실제 기능엔 없고 감사 로그 화면에만 있었다(사용자 "그 위치에서 AI가 틀림 어쩌구 하는 게 아니라").
- 위치 판정이 확신 없이 후보를 미리 골라 둔 경우, 사람이 바꿔도 번복으로 세지 않았다(AI 값이 비어 있어서).
- 품질 › RAG 파이프라인은 읽기만 하는 목록 — 할 일이 없는 화면. 즉석 질의는 채팅과 다른 경로로 검색해 실제와 다르게 보일 수 있다.

## 2. 업계 표준·대표 사례 → 우리 것으로
| 사례(출처) | 요지 | 우리 적용 |
|---|---|---|
| Override monitoring(실무 글·용어집) | 언제·어디서·왜 바꿨나를 표준 이유 코드로, 신뢰도·적용 규칙·모델 버전과 함께 남긴다. 결정 종류별 번복률을 보고 기대보다 높으면 기준(임계·가중)이 틀렸다는 신호 | 이유 코드 3개(AI가 틀림·둘 다 맞음·상황이 바뀜) + 원장에 이미 있는 신호(이웃 다수결·확신·프롬프트 버전). 종류별 번복률을 감사 로그 요약·진단에 |
| 형식적 승인(rubber-stamping) 경고(같은 글들) | 번복이 거의 0이거나 수락이 95%를 넘으면 사람이 실제로 안 보고 있을 수 있다 | 표본이 충분할 때(20건+) 수락 95% 초과면 감사 로그에 "형식적 승인 의심" 한 줄 |
| Google PAIR Feedback + Control | 명시적·암묵적 피드백을 구분하고, 묻는다면 필요한 만큼만·쉽게 건너뛸 수 있게, 무엇에 쓰는지 알린다 | 번복 자체 = 암묵 피드백(항상 기록), 이유 = 명시 피드백(선택·비차단, "이유를 고르면 AI가 배워요") |
| Microsoft HAX G9 효율적 정정(분류 결정 바꾸기) | 정정은 결정을 바꾸는 그 자리에서 짧게 | 지식 등록의 넣을 곳 카드에서 바로(따로 화면 없음) |
| Langfuse 오류 분석·버전 이력 | 실패 묶음 → 건수 → 할 일, 버전·차이·되돌리기 | 리포트 원인별(이미) + "운영 번복" 묶음, 평가 게이트 [적용 이력] 탭 |
| Dify·RAGFlow 검색 테스트 | 실제 검색 설정 그대로 미리 보고 조정 | 검색 미리보기 — 채팅과 같은 검색 함수, LLM 없음. 조정은 안 하고(바꾸는 길은 게이트 하나) 시험지 추가로 루프에 연결 |

## 3. 하는 것(MVP)
1. **번복 기록**: 위치 판정 = AI 추천(확신 없으면 후보)과 다른 곳에 넣으면 번복(`ledger.location_outcome`). 이유는 넣을 곳 카드에서 선택(`override_reason`), "AI가 틀림·상황이 바뀜"은 원장 ai_verdict에도.
2. **진단에 운영 번복**: `service/eval/override.py` — 종류별 번복률·이유·패턴(AI 추천 → 사람) + 위치 판정은 남은 신호로 제안 규칙을 LLM 없이 다시 계산(현행 / 이웃 기준 5중 3 / AI 먼저 / 이웃 먼저) →
   "맞은 제안 수 · 확신인데 틀림" 비교, 현행보다 낫고 새로 틀리는 게 없으면 "해 볼 만함". `eval_improve.py --save`가 후보로 저장(원인 `override_location`, 대상 = 번복 판정 D-id).
3. **감사 로그 = 기록만**: "AI가 틀렸나요?" 버튼 제거, 번복 이유·이어진 후보, 요약 3칸(번복 수·AI가 틀렸다고 한 것·번복으로 만든 후보), 형식적 승인 경고. E2E 기록은 남기지 않음.
4. **평가 게이트 요약 + 적용 이력**: 맨 위 단계 흐름(틀림 = 시험지 원인 → 단계, 번복 = 감사 로그 종류 → 단계). [적용 이력] = 후보 결정(운영 반영·안 함·보류)·측정 + 기법 대장 근거를 날짜순.
   RAG 파이프라인 메뉴는 내림(기대던 것 → 갈 곳: 기법 목록 → 적용 이력 · 항목 설명 → 설명 창 · 단계 숫자 → 게이트 요약 · 감사 로그 기준 → 대장 그대로).
5. **검색 미리보기**(즉석 질의 대체 — 10/28 "AI가 본 근거"로 이름을 바꾸고 탭 대신 정정 요청 신고 카드·지식 상세에 붙임, v2.182): 채팅 검색 함수를 그대로(LLM 없음) — 넘길 근거 순서·용어 보강·근거 판정·답 모양, 정답 근거를 골라 시험지(실사용 문항)에 추가.

## 4. 안 하는 것 + 다시 볼 조건
- 되묻기·원문 확인·다른 컬렉션에서 이유 묻기 — 고른 값이 곧 이유. 이유 없이는 판단이 안 되는 사례가 쌓이면.
- 번복이 쌓이면 자동으로 기준 바꾸기 — 사람 승인 그대로(사용자 원칙). 같은 후보가 3번 연속 "해 볼 만함"이고 사람이 매번 승인하면 다시.
- 검색 미리보기에서 설정 잠깐 바꿔 재 보기(Dify식) — 바꾸는 길을 하나로. 후보 실험이 느려 불편하다는 말이 나오면.
- 위치 판정 "AI 이름 본문 등장(ai_named)" 규칙 리플레이 — 신호를 원장에 안 남겨 다시 계산 불가. 리플레이 결과가 실제와 다르면 신호 추가.

## 5. 출처
- Override monitoring: https://www.hyperbots.com/glossary/override-monitoring-ai-decisions · https://hackernoon.com/the-override-problem-what-happens-after-your-system-makes-a-recommendation · https://arafattehsin.com/the-pragmatic-guide-to-human-in-the-loop-ai/
- Google PAIR Feedback + Control: https://pair.withgoogle.com/chapter/feedback-controls/
- Microsoft HAX Toolkit(G9 Support efficient correction): https://www.microsoft.com/en-us/haxtoolkit/library/pattern/
- Langfuse 오류 분석·버전: https://langfuse.com/blog/2025-08-29-error-analysis-to-evaluate-llm-applications · https://langfuse.com/docs/prompt-management/features/prompt-version-control
- Dify·RAGFlow 검색 테스트: https://docs.dify.ai/guides/knowledge-base/retrieval-test-and-citation · https://ragflow.io/docs/run_retrieval_test
