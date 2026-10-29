# 용어집 관리 — 업계식 재구성(제안 → 근거 → 승격) (설계, 2026-10-28)

> 상태: **1단계 구현(출처 표시)**, 2단계 이후는 설계 — 컨플루언스 재적재 날 측정 후 결정. 사용자 10/28: "잘하면 좋겠지만 못하면 노이즈가 많아질 수도",
> "LLM 답 생성엔 효용 있을 것 — 사람이 쓴 말을 시스템 용어로 컨버팅", "업계식으로 재구성".

## 1. 지금 막힌 일
- 용어집은 두 곳에 쓰인다: **검색**(질문에 나온 용어·동의어를 키워드 검색어에 붙임) · **답 생성**(질문에 나온 용어의 뜻을 문맥 맨 앞에 — `glossary_terms.definitions_block`).
  틀린 용어는 두 곳 다 노이즈다(엉뚱한 문서를 끌어옴 · LLM이 문서보다 틀린 뜻을 따름).
- 들어오는 길 셋 중 **지식 등록 때 LLM 자동 추출**만 업계와 다르다: 문서에서 뽑아 검수·점수·측정 없이 바로 사용, 출처 표시도 없었다(10/28 전 약 200개가 사람 등록과 섞임).
- 실측(10/28, 두 문장 지식 1건): 용어 6개가 바로 생김 — 시험 표지("E2E출처표시 시험")·UI 조각("재배차 버튼")까지. 용어마다 동의어 LLM 1회 → **등록 1건에 LLM 7회**.

## 2. 업계 방식 → 우리
| 사례 | 요지 | 우리 |
|---|---|---|
| Algolia AI Synonyms | **검색 기록**에서 AI가 제안, 확신 점수와 함께 "제안" 탭 → 관리자가 추가/거절해야 반영 | 질의 기록 공백 수집(하루 1회, `llm_gap`)이 이미 같은 결 — 같은 실패 질문이 여러 번 반복될 때만 LLM을 부르고, 채팅 검색으로 다시 돌려 새 근거가 잡히면 바로 사용(옛 `llm_query` 수집분만 근거 문항 수가 쌓여야 사용) |
| Microsoft Purview · Collibra · OpenMetadata · Informatica 용어집 | 초안 → 검토 중 → 승인, 용어마다 담당 영역·담당자 | 정책서 용어 = 정리된 원천(승인된 것). 자동 추출에는 상태가 없음 ← **여기를 고침** |
| Glean | 직원 **사용 패턴**에서 동의어 학습(이름 바뀐 프로젝트·약어) | 사용 신호(질의 기록)로 승격하는 근거로 씀 |
| RAG 질의 고쳐 쓰기(Intel Enterprise RAG 등) | 도메인 사전으로 약어 풀기. 위험 = 의미가 틀어짐 → 허용 용어로 검증, 원래·고친 질문 기록, 효과 측정 | 측정(정확도 게이트, 검색 + 답) 없이 켜지 않는다 |

공통: 용어는 주로 **사람들이 실제로 쓴 말**에서 오고, 쓰기 전에 **승인 또는 근거**를 거치며, 효과를 잰다.

## 3. 하는 것(MVP, 단계별)
1. **출처 표시(10/28 구현, 동작 그대로)** — 자동 추출 용어 `rag_glossary.source = 'auto_extract'`, 용어집 화면에 "자동 추출" 표시. 측정에서 따로 넣고 뺄 수 있게.
   사람이 고친 자동 용어도 `auto_extract`로 남는다(출처는 "처음 어디서 왔나" — 측정 때 고친 것을 따로 보려면 수정 시각으로). 관리자 화면의 AI 추천 용어 적용은 사람이 승인해 넣는 길이라 NULL(사람 등록)로 둔다.
2. **재적재 날 측정** — 같은 데이터로 자동 용어 포함/제외, **검색(LLM 0) + 답(로컬 LLM ~1시간)** 둘 다, 같은 날 채점(`eval_gate.py`).
   떨어뜨리면 끔 · 효과만 있으면 그대로 · 효과와 노이즈가 섞이면 3으로.
3. **후보 → 근거 → 자동 승격**(측정 결과가 3이면) — 자동 추출 용어는 `status = 'candidate'`(검색·답에 안 씀). 승격 = 질의 기록에 그 말이 나오고,
   채팅 검색으로 다시 돌려 근거가 확인되면 자동(질의 기록 공백 수집과 같은 검증 재사용). 사람은 후보 목록에서 [버리기]만(선택). 후보는 동의어 LLM을 부르지 않는다(비용).
   + LLM 없는 거르기: 본문에 그 글자가 실제로 있나 · 기존 용어와 겹치나 · 시험 표지·버튼 이름 같은 조각인가.

## 4. 안 하는 것 + 다시 볼 조건
- 사람 승인 필수(Purview식 초안 → 승인) — 팀별 적재마다 승인 부담. 자동 승격이 틀린 용어를 올린 사례가 나오면.
- 용어마다 담당자 지정 — 지금 용어 담당 = 컬렉션 담당. 용어를 여러 컬렉션이 같이 쓰기 시작하면.
- LLM 질의 고쳐 쓰기(약어를 LLM이 풀기) — 매 질문 LLM 1회 추가. 글자 매칭으로 못 잡는 약어 사례가 쌓이면.
- 확신 점수(Algolia식) — 승격 규칙(근거 확인)이 점수 역할. 후보가 많아 정렬이 필요해지면.

## 5. 출처
- Algolia dynamic synonym suggestions: https://www.algolia.com/doc/guides/algolia-ai/dynamic-synonym-suggestions
- Microsoft Purview 용어 승인 워크플로: https://learn.microsoft.com/en-gb/purview/how-to-workflow-business-terms-approval
- OpenMetadata 용어집 승인: https://docs.open-metadata.org/v1.6.x/how-to-guides/data-governance/glossary/approval
- Collibra 승인 프로세스: https://cdn-university.collibra.com/wp-content/COOK/Approval-Process-Walk-Through_2327211.html
- Informatica 용어 상태: https://docs.informatica.com/data-quality-and-governance/data-quality/10-5/business-glossary-guide/glossary-content-management/asset-state.html
- Glean(사용 패턴 동의어): https://glean.com:443/blog/semantic-search-productivity
- Intel Enterprise RAG 질의 고쳐 쓰기: https://hub.docker.com/r/intel/enterprise-rag-query-rewrite
- 질의 고쳐 쓰기 위험(의미 틀어짐): https://www.besthub.dev/articles/how-query-rewriting-boosts-retrieval-in-rag-systems-03cacfa62523
