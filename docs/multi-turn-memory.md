# 멀티턴 대화 메모리 — ConversationSummaryBuffer + Semantic Recall

## 개요

긴 대화에서 전체 히스토리를 LLM에 넣으면 토큰 한계 + 비용 문제가 발생한다.
이를 해결하기 위해 **요약 + 의미 검색** 기반의 2단계 메모리 전략을 사용한다.

```
[과거 요약 (Semantic Recall)] + [최근 raw 교환 (Working Memory)] + [현재 질문]
```

## 아키텍처 흐름

```
사용자 질문
     │
     ▼
① 질문 임베딩 생성 (query_vec)
     │
     ├────────────────────────┐
     ▼                        ▼
② Semantic Recall          ③ Working Memory
   ops_conv_summary에서       ops_message에서
   query_vec 코사인 유사도    최근 2회 교환
   ≥ 0.45인 요약 최대 2개     (user+assistant × 2)
     │                        │
     └──────┬─────────────────┘
            ▼
④ LLM 컨텍스트 조립
   messages = [
     { role: "system", content: "이 대화의 관련 과거 맥락입니다:\n[과거 맥락 1]\n..." },  ← ②
     { role: "user",   content: "이전 질문1" },   ← ③ 최근 raw
     { role: "assistant", content: "이전 답변1" },
     { role: "user",   content: "이전 질문2" },
     { role: "assistant", content: "이전 답변2" },
   ]
   + system prompt + [참고 문서] + [현재 질문]     ← 하이브리드 검색 결과
            │
            ▼
⑤ LLM 답변 생성 (스트리밍)
            │
            ▼
⑥ 백그라운드 후처리: maybe_summarize()
   교환수가 4의 배수 도달 시
   → 오래된 교환을 LLM으로 요약
   → ops_conv_summary에 임베딩과 함께 저장
```

## 튜닝 파라미터

| 파라미터 | 값 | 설명 |
|---|---|---|
| `SUMMARY_TRIGGER` | 4 | N회 교환마다 요약 실행 |
| `RECENT_EXCHANGES` | 2 | 항상 raw로 유지하는 최근 교환 수 (요약 대상 제외) |
| `MAX_RECALL` | 2 | Semantic Recall로 가져올 과거 요약 최대 수 |
| `RECALL_THRESHOLD` | 0.45 | 유사도 이 이상인 요약만 LLM 컨텍스트에 포함 |

## DB 테이블

### ops_conv_summary
| 컬럼 | 타입 | 설명 |
|---|---|---|
| id | SERIAL PK | |
| conversation_id | INT FK | ops_conversation 참조, CASCADE 삭제 |
| summary | TEXT | LLM이 생성한 3~5문장 요약 |
| embedding | VECTOR(768) | summary의 임베딩 (Semantic Recall용) |
| turn_start | INT | 요약 범위의 첫 message.id |
| turn_end | INT | 요약 범위의 마지막 message.id |
| created_at | TIMESTAMPTZ | |

## 요약 트리거 로직 (maybe_summarize)

```
전체 교환 수 = user 메시지 수 (ops_message WHERE role='user')

1. 이미 요약된 마지막 message.id 조회 (MAX(turn_end))
2. 최근 RECENT_EXCHANGES × 2개 메시지는 제외 (working memory 보호)
3. 그 사이의 미요약 교환을 SUMMARY_TRIGGER 단위로 묶어서 LLM 요약
4. 각 요약 → 임베딩 → ops_conv_summary 저장
```

### 예시 시나리오 (대화 8회 교환)

| 시점 | 동작 |
|---|---|
| 교환 1~3 | 아직 trigger 미도달, 요약 없음 |
| 교환 4 완료 | 4회 도달 → 교환 1~2를 LLM 요약 → 벡터 저장 (교환 3~4는 working memory) |
| 교환 5~7 | 미요약 교환 축적 중 |
| 교환 8 완료 | 8회 도달 → 교환 3~6을 요약 → 저장 (교환 7~8은 working memory) |

## Semantic Recall 동작

새 질문이 들어오면:
1. 질문을 임베딩 (query_vec)
2. `ops_conv_summary`에서 같은 conversation_id 내 유사도 검색
3. `1 - (embedding <=> query_vec) >= 0.45`인 요약만 선택 (최대 2개)
4. 관련 요약을 system 메시지로 LLM에 전달

→ 전체 대화를 넣는 게 아니라 **현재 질문과 의미적으로 관련 있는 과거 맥락만 선별적으로 리콜**

## 요약 프롬프트

```
다음은 IT 운영 지원 챗봇과의 대화 기록입니다.
핵심 질문, 파악된 원인, 제시된 해결책, 주요 기술 사실을
3~5문장으로 간결하게 요약해 주세요.

[대화 기록]
사용자: ...
어시스턴트: ...
...

요약:
```

## 관련 코드

- `backend/services/memory.py` — 전체 메모리 로직
- `backend/routers/chat.py` — `memory.build_context_history()` 호출
- `backend/services/llm/base.py` — `build_messages()`에서 history 조립
- `init/01-init.sql` — `ops_conv_summary` 테이블 정의
