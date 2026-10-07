# OpsLens 시스템 아키텍처 (v2.128)

## 개요

OpsLens(구 Ops-Navigator, 2026-10-06 이름 변경)는 정책서·매뉴얼·운영 지식·공통코드처럼 흩어진 IT 운영 지식을 한곳에
모아 근거와 함께 답하는 **IT 운영 지식 통합 AI 플랫폼**이다. 틀린 답은 사용자 신고 → 담당자 검토로 바로잡는다.
사용자는 에이전트를 선택해 목적에 맞는 AI를 사용한다: 지식 기반 Q&A(KnowledgeRAG).

> Text-to-SQL 에이전트는 v2.51에서 `dev_0`/`main`에서 분리·제거됐다(현재 과업 범위 아님) — 코드는
> `archive/with-text2sql` 브랜치(2026-09-03 시점 스냅샷)에 형상관리용으로 보존돼 있다.
> MCP 도구 에이전트는 v2.67에서 완전 제거됐다(Text2SQL과 달리 브랜치 보존 없음 — 배경은
> `docs/archive/architecture-history-v2.0-v2.99.md` v2.67 항목).

**주요 이력 요약** (스키마 변경 상세는 `table-definition.md` §20 마이그레이션 이력 참조)
- v2.128 *(2026-10-07 배포 — 절차 `docs/tech/v2.128-runbook.md`, 근거 없음 판정 0.50/0.05 켬)*: **용어집 활용
  재설계 + 실패 가시화 + 측정 도구.** ① 용어: 질문 임베딩↔용어 설명 최근접 1개를 붙이던 방식이 골든 79/88에 붙였지만 질문에 실제로
  있던 건 6건, 무관 질문 21/60에도 붙어("오늘 날씨"→"출고송신진행") 무관 질문에 근거가 생기고 LLM이 지어낸 원인이었다. 실무 방식
  (시맨틱 모델의 동의어+설명, 동의어 맵, 엔터티 연결)대로 질문에 글자 그대로 나온 용어·동의어만 찾아 키워드 검색만 넓히고 설명은
  LLM 문맥에(`glossary_terms.py`, `GLOSSARY_MATCH_MODE=lexical`). 동의어는 사람이 등록하지 않음 — 용어 등록 시 LLM 생성 + 하루 1회
  질문 기록 배치(`glossary_mining.py`), 둘 다 임베딩 유사도 품질 게이트(0.65 / 0.85, 틀린 쌍 14개 실측으로 결정), 화면에선 지우기만
  (#69 `rag_glossary_synonym`). 실측(골든 88·무관 60, 채팅 경로): 지금 방식 Recall@10 89.8%·맨 앞 묶음 60.2%·무관 오염 21 →
  새 방식 88.6%·60.2%·0. ② 근거 없음 즉시 판정(`POLICY_ABSTAIN_MIN_SCORE`, 기본 꺼짐): 지식·공통코드 0, 정책 파라미터 약함, 서술
  최고점 < 하한이면 LLM 없이 "관련 지식을 찾지 못했습니다"(캐시 안 함). 후보 (0.50, 0.05) = 골든 거짓 거절 0/88, 무관 35/60 차단.
  ③ 실패 가시화: 예외 없이 토큰 0개면 빈 말풍선 대신 "AI가 빈 답변을 보냈습니다" + 경고 로그, 게이트웨이 `agent_message`·
  `message_replace`·`error` 이벤트 처리(예전엔 `message`만 읽어 나머지 무시), 앱 로그 출력 설정(예전엔 INFO가 안 나옴 — 켜기 전에
  응답 본문을 찍던 로그를 길이만으로), 게이트웨이 대기 120→180초, 채팅 진행 표시에 경과 시간·"응답이 느립니다". ④ 측정: 채팅과
  같은 `build_chat_context`를 쓰는 `scripts/eval_chat_retrieval.py`(예전 Recall 87.6%는 용어 매핑을 안 거친 정책 단독 경로), 답 없는
  질문 세트 `tests/fixtures/eval/negative_v1.jsonl`, 검증 전용 질문 후보 생성 `scripts/gen_holdout_questions.py`(LLM 생성 → 사람이 거름). ⑤ 보안: 용어 일괄 삭제
  (`POST /api/knowledge/glossary/bulk-delete`)에 파트 권한 검사가 없어 조회 전용·다른 파트 사용자도 아무 용어나 지울 수 있었다 — 지식 일괄 삭제(10/02)와 같은
  기준으로 대상 용어의 파트마다 소유권 확인(하나라도 막히면 전체 거부).
  ⑥ 코드 리뷰 반영(2026-10-07): 용어집 벡터 검색 화면 오류(동의어가 문자열로), 시크릿 로그(새 DB 설치 시 관리자 비밀번호),
  게이트웨이 검열 교체를 답 교체로(이어 붙이던 것), 용어 경계(짧은 영문·띄어쓰기 건너뛰기), 대표 용어 1개(통계 조인),
  질문 근거를 서로 다른 질문 수로(#69 evidence_questions), 실패 배치 재처리, 검토 초안 무변경 승인 차단.
  ⑦ 질문 기록 수집 개편(2026-10-07, 사용자 제안): 모든 질문을 매일 LLM에 보내던 방식은 첫 실행 168개 → 3개(1개 의심)로 효과는
  없고 오추출 위험만 있었다(과설계 판단). 이제 **같은 질문이 14일 안에 3번 이상 답을 못 찾은 것만**(SQL로 확인, 없으면 LLM 안 부름),
  관련 용어 10개만 보내고(프롬프트 약 80%↓), 품질 게이트 후 **효과 확인** — 그 동의어를 넣고 실패 질문을 채팅 경로로 다시 검색해
  새 근거가 잡힐 때만 붙임(source='llm_gap'). 실데이터 dry-run: 반복 공백 2개("오늘 날씨"·게이트웨이 불안정으로 실패했던 질문)
  → 후보 0(정상). 설정 GLOSSARY_GAP_MIN_REPEATS=3·GLOSSARY_GAP_WINDOW_DAYS=14. 옛 방식이 남긴 llm_query 3건은 근거 1건이라 비활성.
  ⑧ 최종 답변 정확도 측정(2026-10-07): 지금까지 지표는 검색 단계뿐이었다. `scripts/eval_answers.py` — collect(골든 문항을 채팅과 같은
  경로로 답, 대화·질의 기록·캐시 안 만듦, 이어 쓰기; before/after/no_defs 버전) → judge(로컬 LLM이 정책 원문·기대 답과 대조해
  정답/부분/오답/거절, 빈 답·지식 없음은 규칙으로) → report(유형별·같은 문항 비교·사람 확인 표본 5개, --save로 #70 이력).
  평가 게이트에 "답변 정확도" 탭(읽기 전용 이력).
  ⑨ 탐색형 질문 분류 목록(2026-10-07): "○○ 관련 정책 다 보여줘"는 검색(상위 몇 개)으로 분류 전체가 안 와 정답 4/24·부분 18이었다
  (정답 분류 정책의 문맥 포함률 평균 42%). 탐색형(기존 판별기, 24/24 탐지)이면 질문이 가리키는 분류(이름 글자 매칭 → 없으면 의미 1위
  0.58 이상)의 정책을 DB에서 전부(최대 40개, 정책명+본문 첫 줄) 문맥 앞에 넣는다(`service/policy/category_list.py`, 새 테이블·LLM 호출
  없음). 포함률 42%→91%. 최종 답(88문항): 정답 56→62(64%→70%), 탐색형 정답 4→9·거절 2→0, 비탐색 52→53(회귀 없음). 남은 탐색형
  부분 15는 목록은 맞지만 세부 내용이 짧다는 판정 — "전부 보여줘"에 목록+요약을 정답으로 볼지 채점 기준 확인 필요.
  ⑩ 주제 전체 판별(2026-10-07): 키워드 판별기(전체·모든·목록)는 골든셋 말투에 맞춰져 실제 질문 기록에서 재현 1/10·정밀도 33%였다
  ("재고 정책 좀", "쿠폰에 대해서 알려줘" 놓침). 질문이 "분류 이름 + 일반 표현"뿐이면 주제 전체로 보는 규칙을 더함(`is_topic_overview`,
  두 분류면 함께 속한 정책만) → 실제 질문(사람 라벨 103개): 분류가 있는 주제 전체 6/6에 목록, 오탐 1(1개짜리 목록). 골든 비탐색 오탐 0.
  남은 문제: 검증 후보(단건) 40개 중 4개에 키워드 경로 오탐("전체 수량을 정해둘 수 있어?" 등) — 답에 해가 되는지 검증 문항 확정 후 측정.
  ⑪ 거절 원인 분석(2026-10-07, 적용 안 함): 거절 7건 중 5건은 정답 근거가 문맥 상위에 있고 질문 핵심어도 본문에 있었는데 LLM이
  "관련 지식을 찾지 못했습니다" — 검색이 아니라 답변 단계. 거절 규칙만 바꾼 후보 프롬프트("핵심 단어·조건이 나오는 문서가 있으면
  답하고 없는 부분만 명시")를 88문항에 돌려 보니(`eval_answers.py collect --prompt-file`, 운영 DB 프롬프트 불변) 거절 7→2지만
  정답 62→61·부분 18→23·오답 1→2, 좋아짐 7/나빠짐 8 — 순이득 없어 운영 프롬프트 유지. 나머지 2건(조건형 "3만 원 넘을 때만"
  "투고백에만")은 검색 누락.
- v2.127.1 (2026-10-07, 시연 전 반영): **정정 검토 — 의견 없는 신고에도 "AI 검토 초안".** 의견 없는 신고(이관된 옛 검토 표시 등)는
  초안을 안 만들어(맞는 값을 모름) "AI 초안 만들기"를 눌러도 아무 일이 없었다(서버 200·내용 없음, 화면 안내 없음). 이제 값은 그대로
  두고 질문과 견줘 의심 구간에 【확인 필요: 이유】만 단 검토 초안(`draft.draft_review_template`) — 표시를 지운 본문이 원문과 다르면
  버리고 원문 복사 양식으로(값을 몰래 바꾸지 않게), LLM 실패에도 양식은 항상 나옴. 표시가 남으면 승인 불가(서버 `_validate_proposed`·
  화면). 빠진 내용 양식엔 질문만 적고 틀렸다는 답변은 옮기지 않음. 의견이 있는데 AI가 초안을 못 만들면 화면에 알림.
- v2.127: **정책서 시트 붙여넣기(보안 엑셀 기본 경로).** v2.124의 안내("담당자 PC에서 `python excel_to_policy_json.py`")는
  파이썬을 제공하지 않아 담당자가 쓸 수 없었다(사용자 지적). DRM 엑셀은 서버·브라우저 어디서도 못 열지만 엑셀에서 복사는 되므로,
  정책서 올리기 화면의 기본을 **시트 붙여넣기**로: 파트·정책서(이 파트의 기존 원본 파일 목록 `GET /api/policy/sources`에서 고르거나
  새 이름)·시트 이름(기존 시트 자동완성, 없던 이름이면 "새 시트로 들어갑니다")을 고르고 Ctrl+A → Ctrl+C 한 내용을 붙여넣는다
  (`POST /api/policy/import-paste`). 탭 구분 텍스트를 csv 규칙(칸 안 줄바꿈은 큰따옴표)으로 읽어 엑셀 업로드와 같은
  `parse_sheet_rows`(헤더 인식·병합 칸 채우기·엑셀 행 번호)와 같은 임포트 본체(`_import_sheets`)를 탄다. 바뀐 시트만 올리면 되고
  (안 올린 시트는 v2.123 규칙상 그대로) 처음 20여 개 시트 일괄 적재는 개발 쪽이 변환기로. **일부만 복사한 실수 방지**: 붙여넣기에서
  시트의 기존 정책 30% 초과(최소 3건)가 빠지면 그 시트는 "원본에서 사라짐" 표시를 하지 않고 경고(검토 큐 폭주 방지). 보안 없는
  엑셀·변환기 JSON은 "파일 올리기"로 계속 받는다. 반영 버튼이 막히면 이유를 옆에 표시. 실측(dev, 온라인스토어 리워드정책 35건):
  그대로 붙여넣기 → 변경 없음 35·DB 변화 0, 앞 10행만 → 경고 + 사라짐 표시 0·DB 변화 0.
- v2.126: **사내 게이트웨이 민감정보 오탐 임시 대응(시연용) + 거부 응답을 장애로 분류.** 게이트웨이(DevX)가 컨플루언스
  매뉴얼의 목차·버전 번호(`3.1.2.4`)를 IP로, 긴 숫자를 ID로 보고 질문 전체를 거부(외부서비스DB 활성 42조각 중 32개 해당 —
  이 공간 매뉴얼 질문이 거의 항상 막힘). `service/llm/gateway_text.py`가 **근거 문서 본문과 이전 답에서만** 숫자 형태를 바꿔
  보내고(점 4묶음 이상 → 점을 `．`, 8자리 이상 → 4자리마다 `·`), 답은 원래 형태로 되돌린다(스트리밍은 숫자 중간에서 잘린
  토큰을 붙잡아 두었다가 원복). 문자는 게이트웨이 실측으로 고름(`·`/`．`/`。`/`-` 모두 통과, `-`는 전화번호·날짜와 섞여 원복이
  모호해 제외). **사용자 질문·사용자 이력은 그대로** 보내 사용자가 직접 넣은 민감정보는 게이트웨이가 계속 막는다. 이건 보안
  필터를 우회하는 장치라 임시책 — 근본 해결은 게이트웨이 담당에 오탐 예외·규칙 조정 요청. DB·근거 카드·화면은 그대로.
  함께: 게이트웨이 거부 응답이 "답변"으로 집계되고 **시맨틱 캐시에 저장돼 원인을 고쳐도 TTL 동안 거부가 계속 나가던** 문제 —
  `is_llm_failure()`로 연결 실패와 같이 `system_error` + 캐시 제외, 기존 기록은 #68로 재분류.
- v2.125: **제품명 OpsLens로 변경 + 정체성 "IT 운영 지식 통합 AI 플랫폼".** 로그인·사이드바·에이전트 선택·채팅 빈 화면·브라우저
  탭·API 문서 제목·답변 프롬프트 자기소개(DB #67, 기본값 포함)·문서·스크립트 문구. 에이전트 카드를 지금 기능으로 현행화(정책서·
  매뉴얼·운영 지식 통합 검색, 근거 표시, 공통코드·정책 값 정확 조회, "답변 틀림" 신고 → 검토 반영). 정책 JSON 형식 식별자도
  `opslens/policy/v1`(당일 신설이라 기존 파일 없음). 컨테이너·이미지·DB·저장소 이름은 배포 절차·데이터 연속성을 위해 유지.
- v2.100 ~ v2.124: `docs/archive/architecture-history-v2.100-v2.124.md`로 옮김(2026-10-07 경량화 — 판단 근거 기록은 그대로 보존).
- v2.0 ~ v2.99: `docs/archive/architecture-history-v2.0-v2.99.md`로 옮김(2026-10-06 경량화 — 판단 근거 기록은 그대로 보존).

---

## 전체 구성도

```
┌─────────────────────────────────────────────────────────────┐
│                        Host Machine                         │
│                                                             │
│   ┌──────────────────────────────────────────────────────┐  │
│   │              Docker Compose Network                  │  │
│   │                                                      │  │
│   │  ┌─────────────┐    ┌─────────────┐                 │  │
│   │  │  Frontend   │───▶│   Backend   │                 │  │
│   │  │  React+nginx│    │   FastAPI   │                 │  │
│   │  │  :8501      │    │   :8000     │                 │  │
│   │  └─────────────┘    └──────┬──────┘                 │  │
│   │                            │                         │  │
│   │                    ┌───────┴────────┐                │  │
│   │                    │   PostgreSQL   │                │  │
│   │                    │  + pgvector    │                │  │
│   │                    │   :5432        │                │  │
│   │                    └───────────────┘                 │  │
│   └──────────────────────────────────────────────────────┘  │
│                                                             │
│   ┌──────────────────────┐                                  │
│   │  Ollama (호스트 직접)  │  ◀── Backend이 host.docker.      │
│   │  exaone3.5:2.4b      │       internal:11434 으로 호출   │
│   │  :11434              │                                  │
│   └──────────────────────┘                                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 컴포넌트별 역할

### 1. Frontend — React + Nginx (`:8501`)

| 페이지 | 역할 |
|--------|------|
| **Login** (`/login`) | JWT 로그인 — Access Token + Refresh Token 발급 |
| **Register** (`/register`) | 회원가입 — 부서 선택 + 선택적 LLM API Key 등록 |
| **AgentSelect** (로그인 직후) | 에이전트 선택 화면 — 지식베이스 AI 카드 선택. `selectedAgent=null`이면 이 화면 표시 (사이드바 없음) |
| **Chat** (`/`) | 에이전트별 채팅 — SSE 스트리밍, 근거 카드, "답변 틀림" 신고(정정 검토로), 대화 메모리(요약+리콜), Markdown 답변 |
| **Admin** (`/admin`) | 에이전트별 관리 화면 — `agentScope` 필드로 탭 필터링. knowledge_rag: 기준정보·지식 베이스(정정 검토 포함)·VOC·용어집·평가 게이트·정책·캐시현황·통계. 공통: 시스템설정·사용자관리. (에이전트현황 탭 제거 — AgentSelect 화면에 헬스배지로 대체) |

- **Agent-centric 라우팅**: `useAppStore.selectedAgent: 'knowledge_rag' | null`. null이면 AgentSelect 표시, 설정 시 에이전트별 UI로 전환. 로그아웃 시 null로 리셋
- **ProtectedRoute**: 로그인되지 않은 사용자는 `/login`으로 리다이렉트
- **useAuthStore** (Zustand): localStorage에 토큰 저장, 자동 Bearer 토큰 주입
- **401 Auto-refresh**: Access Token 만료 시 Refresh Token으로 자동 갱신, 실패 시 로그아웃
- **부서 기반 UI**: 지식/용어집/Q&A 테이블에 부서 배지 표시, 같은 부서만 수정/삭제 버튼 노출
- **어드민 테이블 검색**: 지식베이스·용어집·Q&A 세 테이블 모두 텍스트 검색(즉시 필터) + 벡터 유사도 검색([문자열|벡터] 토글) 지원. 벡터 검색은 Enter 또는 검색 버튼으로 실행하고 유사도 % 배지 표시
- **어드민 테이블 다건 삭제**: 전체 선택 체크박스 + 행별 체크박스, N개 선택 시 액션 바 표시 → 일괄 삭제
- Sidebar: 에이전트 배지 + 에이전트 변경 버튼, 사용자 정보 + 로그아웃, 네임스페이스 선택, 대화 목록, 검색 설정 슬라이더, 헬스 표시기
- Backend REST API만 호출 (직접 DB 접근 없음)
- 검색 비중(벡터/키워드 비율), Top-K를 사이드바 슬라이더로 실시간 조정 (개인 설정은 localStorage에 저장, DB 저장 없음)
- nginx 정적 빌드 서빙 + `/api/*` 요청을 Backend(`:8000`)로 프록시

### 2. Backend — FastAPI (`:8000`)

```
backend/
├── main.py              # 앱 진입점, 라이프사이클 (DB풀·임베딩·LLM·에이전트 초기화)
├── agents/              # 에이전트 레이어 (AgentBase + AgentRegistry 패턴)
│   ├── base.py          #   AgentBase 추상 클래스 + AgentRegistry 싱글톤
│   ├── knowledge_rag/
│   │   ├── agent.py     #   KnowledgeRagAgent — 하이브리드 검색 + LLM 스트리밍
│   │   ├── knowledge/   #   지식/용어집 CRUD + 하이브리드 검색 (retrieval.py, DB/공통코드 카테고리는 벡터 점수 0 처리 v2.49)
│   │   ├── ingestion/   #   지식 인제스천 파이프라인 (Tier 1~3)
│   │   │   ├── adapters.py      #   파일 파싱 (.txt/.md/.pdf → ParsedDocument)
│   │   │   ├── chunker.py       #   청킹 엔진 (section/paragraph/fixed/auto)
│   │   │   ├── analyzer.py      #   LLM Analyzer Agent (doc_type, chunk_strategy 자동 결정)
│   │   │   ├── tagger.py        #   LLM 자동 태깅 + 용어 추출
│   │   │   └── web_crawler.py   #   URL/Confluence 수집 (httpx + BeautifulSoup + Confluence REST API)
├── service/             # 플랫폼 공통 레이어 (was domain/, platform/ 명칭 stdlib 충돌로 service/ 확정)
│   ├── auth/            #   인증/계정 (JWT, bcrypt, Fernet API Key 암호화)
│   ├── chat/            #   채팅 라우터·헬퍼·메모리 (AgentRegistry 위임)
│   ├── feedback/        #   "답변 틀림" 신고 접수 → 개선 원장 (v2.121부터 가중치·질의 상태 안 바꿈)
│   ├── improvement/     #   개선 원장(정정 검토) — 신고·AI 판정/수정안(draft.py)·승인 시 버전 교체 (v2.118/120)
│   ├── refdata/         #   공통코드·DB 컬럼 참조 데이터 검색
│   ├── admin/           #   네임스페이스·통계·LLM 설정
│   ├── prompt/          #   프롬프트 관리 (get_prompt: DB 우선, fallback)
│   ├── llm/             #   LLM Provider 추상화 (ollama / inhouse)
│   ├── email_voc/       #   VOC 이메일 분석 채널 (v2.40 신규)
│   │   ├── graph_client.py    #   Microsoft Graph API 클라이언트 (msal 토큰 발급, 메일 조회, 페이지네이션/재시도)
│   │   ├── service.py         #   check_relevance()(관련지식 사전 필터, v2.41) + analyze_email() — 기존 RAG 파이프라인 재사용 분류/심각도/오배치 판정 + issue_signature(정규화 이슈요약) 출력 (v2.49)
│   │   ├── pattern_detection.py #   반복 VOC 클러스터링(centroid 기반, issue_signature 임베딩 비교, LLM 호출 없음) + 커버리지 LLM 검증 게이트(DB/공통코드 카테고리 제외, v2.49) (v2.47 신규)
│   │   ├── pipeline.py        #   수집→관련지식필터→분석→반복패턴탐지→중복제거→알림 오케스트레이션, 이력 조회
│   │   ├── routing_service.py #   파트별 메일함 라우팅 CRUD, 폴링 설정(관련지식 임계치 포함), Graph 자격증명(Fernet 암호화) CRUD
│   │   ├── delegated_auth.py  #   Delegated Permission 로그인 상태 관리 (Authorization Code Flow/PKCE, v2.41 신규·v2.42 인증방식 교체)
│   │   ├── teams_notify.py    #   Teams Workflows 웹훅 발송
│   │   ├── scheduler.py       #   백그라운드 폴링 루프 (asyncio.create_task, lifespan 등록)
│   │   ├── retention.py       #   30일 고정 보관정책 자동 정리
│   │   ├── schemas.py         #   Pydantic 스키마
│   │   └── router.py          #   /api/email-voc/* 엔드포인트
│   └── policy/           #   정책서 데이터화 파이프라인 v1 (v2.52 신규, docs/policy-doc-pipeline-plan.md)
│       ├── excel_parser.py    #   시트 판별(용어집/정책) + 헤더 퍼지매핑 + 동적 깊이 감지(category_path)
│       ├── decompose.py       #   LLM segment 분해 — narrative/param/unresolved 3분류
│       ├── service.py         #   버전 관리(logical_id/version/supersedes_id, INSERT-only) + LLM 분해 동시성(세마포어5) + policy_item/param/chunk 적재
│       ├── search.py          #   파라미터(RDB tsquery)+서술(벡터) 검색. 전용 엔드포인트(/api/policy/search)로도, agent.py 채팅 흐름에 has_policy_data()/build_policy_context()/build_policy_citations()로도 사용 (v2.64 편입 1단계, v2.65 인용 카드 2단계)
│       ├── unresolved_report.py #   unresolved/partial 항목 system_key별 집계 (v2.53 신규)
│       ├── browse.py          #   item 단위 브라우저 — param/chunk 자식 포함, q는 실제 검색 재사용(matched_via) (v2.59 신규, v2.63 q 개선)
│       ├── track2.py          #   Track 2 A(지식-only)/B(하이브리드) 비교를 API로 실행 (v2.63 신규)
│       ├── risk.py · auto_review.py · review.py · edit.py  #   검토 큐 위험도 분류·낮음 자동 통과·승인/반려·반려 후 수정 (v2.119)
│       ├── schemas.py         #   Pydantic 스키마
│       └── router.py          #   POST /api/policy/import, GET /api/policy/search, GET /api/policy/unresolved-summary, GET /api/policy/items, POST /api/policy/track2/run
├── core/
│   ├── config.py        # pydantic-settings, JWT·Fernet 키
│   ├── database.py      # asyncpg 풀 + resolve_namespace_id() 헬퍼
│   ├── security.py      # JWT, bcrypt, Fernet
│   └── dependencies.py  # get_current_user, get_current_admin, check_namespace_ownership
└── shared/
    ├── embedding.py     # Sentence-Transformers 싱글톤
    ├── cache.py         # Semantic Cache (Redis, 유사도 0.88, TTL 30분, graceful degradation)
    └── http_client.py   # 아웃바운드 HTTP 호출 공용 레이어 (call_http, v2.67 — VOC Teams 발송이 사용)
```

**주요 설계 원칙:**
- **AgentRegistry 패턴**: `chat_stream` → `AgentRegistry.get(agent_type).stream_chat()` 위임. 새 에이전트 추가 시 `agents/` 하위 모듈 + Registry 등록만으로 완결. 플랫폼(인증/세션/피드백)과 에이전트(파이프라인)를 분리.
- **DDD 구조**: 도메인별 디렉토리로 schemas/service/router를 응집 — 플랫 구조 대비 코드 탐색·확장 용이
- **비동기 전용**: asyncpg + httpx async — 블로킹 없는 I/O
- **임베딩 싱글톤**: 앱 시작 시 모델 1회 로드, 이후 thread executor로 재사용
- **LLM Provider 패턴**: `ollama` / `inhouse` 환경변수 하나로 교체 가능
- **LLM별 프롬프트 형식**: Ollama는 `build_messages()` messages 배열, InHouse(DevX MCP API)는 `_build_query()`로 단일 query 문자열 생성
- **대화 맥락**: ConversationSummaryBuffer + Semantic Recall — 오래된 교환을 LLM으로 요약·벡터 저장, 현재 질문과 유사한 과거 요약 + 최근 2회 raw 교환을 history로 LLM에 전달
- **멀티턴 검색 보강**: 직전 Q+A(각 80자)를 현재 질문에 결합하여 임베딩/검색 — 짧은 후속 질문에서도 이전 대화 맥락이 반영되어 유사도 향상 (추가 LLM 호출 없음)
- **마크다운 답변**: 시스템 프롬프트에 Markdown 형식 지시 포함, 프론트엔드에서 `react-markdown` + `remark-gfm` + `rehype-raw`로 테이블/코드/리스트/HTML 태그 렌더링
- **JWT 인증/인가**: Access Token(30분) + Refresh Token(7일), FastAPI Depends로 라우터 수준 보호
- **네임스페이스 소유 파트 기반 권한**: 네임스페이스의 `owner_part`와 동일한 부서 구성원만 해당 네임스페이스의 데이터 CRUD 가능, 타 부서는 읽기 전용. `owner_part` NULL이면 **모든 사용자(파트 무관)**가 CRUD 가능 (공통 namespace). Admin이 생성한 namespace는 자동으로 `owner_part = NULL`. Admin은 모든 권한 보유
- **수정 시 작성자 갱신**: 지식/용어 수정 시 `created_by_part`/`created_by_user_id`가 최종 수정자로 갱신됨
- **Graceful Degradation**: LLM 연결 실패 시 검색 결과는 정상 반환, 안내 메시지 출력

### 3. 인증/인가 시스템 (v2.0.0 신규)

```
┌──────────┐     POST /api/auth/register     ┌──────────────┐
│  사용자   │  ──────────────────────────────▶ │  auth/service │
│          │     (username, password,         │              │
│          │      part_id, api_key?)          │  bcrypt hash │
│          │                                  │  Fernet enc  │
│          │  ◀────────────────────────────── │              │
│          │     201 Created                  └──────┬───────┘
│          │                                         │
│          │     POST /api/auth/login                │
│          │  ──────────────────────────────▶        │
│          │  ◀──────────────────────────────        │
│          │     {access_token, refresh_token}       │
│          │                                         │
│          │     GET /api/chat/stream                │
│          │     Authorization: Bearer <access>      │
│          │  ──────────────────────────────▶ ┌──────┴───────┐
│          │                                  │ dependencies │
│          │                                  │ get_current_ │
│          │                                  │ user()       │
└──────────┘                                  └──────────────┘
```

**핵심 구성 요소:**

| 모듈 | 역할 |
|------|------|
| `core/security.py` | JWT 토큰 발급/검증 (HS256), bcrypt 비밀번호 해싱, Fernet 대칭 암호화 (LLM API Key + Confluence PAT) |
| `core/dependencies.py` | `get_current_user` — Bearer 토큰 검증 후 사용자 반환 |
| | `get_current_admin` — admin 역할 검증 |
| | `check_namespace_ownership` — 네임스페이스의 `owner_part`와 요청자 부서 일치 확인 |
| `service/auth/service.py` | 회원가입 (중복 체크, bcrypt 해싱, Fernet API Key 암호화), 로그인, 토큰 갱신 |
| `service/auth/router.py` | `/api/auth/*` 엔드포인트 |

**권한 모델 (네임스페이스 기반):**
- Admin은 모든 리소스 CRUD 가능. 일반 사용자는 `owner_part` 일치 시에만 CRUD (불일치 시 읽기 전용). `owner_part = NULL` (공통 namespace)는 모든 사용자 CRUD 가능.
- 대화 소유권: `ops_conversation.user_id` FK로 사용자별 대화 격리.

**사용자별 LLM API Key:**
- 회원가입 또는 계정 설정에서 사내 LLM API Key 등록 (선택사항)
- Fernet 대칭 암호화로 DB에 저장 → 요청 시 복호화하여 InHouse LLM Provider에 전달
- 개인 키가 없으면 시스템 기본 키(`INHOUSE_LLM_API_KEY`) 사용

**사용자별 Confluence PAT (v2.14 신규):**
- 계정 설정 또는 URL 수집 폼에서 Confluence Personal Access Token 등록 (선택사항)
- LLM API Key와 동일한 Fernet 암호화 방식으로 `ops_user.encrypted_confluence_pat`에 저장
- URL 수집 시 PAT를 명시하지 않으면 DB에 저장된 개인 PAT 자동 로드 → Confluence 인증 자동 처리

### 4. PostgreSQL + pgvector (`:5432`)

```sql
-- 플랫폼 공통 (ops_* prefix)
ops_part              -- 부서 레지스트리
ops_user              -- 사용자 (role, part_id FK, encrypted_llm_api_key, encrypted_confluence_pat)
ops_namespace         -- 네임스페이스 (owner_part_id FK, created_by_user_id)
ops_conversation      -- 대화방 (namespace_id FK, user_id FK, agent_type)
ops_message           -- 대화 메시지 (role, content, results JSONB, metadata JSONB)
ops_improvement_item  -- 개선 원장(정정 검토): 답변 틀림·근거 정정·빠진 내용·검색 노이즈 + AI 수정안, 승인 전 검색 미노출 (v2.118/120)
ops_query_log         -- 질의 로그 (status: pending=답변 / no_knowledge=지식 공백 / system_error=LLM 연결 실패(통계 밖),
                      --   공백을 메우면 resolved_knowledge_id — v2.121)
ops_prompt            -- 프롬프트 관리 (agent_type별 에이전트 스코핑, Admin 시스템설정 탭에서 편집)
ops_system_config     -- 시스템 설정 key-value (캐시 임계값/TTL 등 영속화, VOC 폴링 정책/Graph 자격증명도 여기 저장)

-- VOC 이메일 분석 채널 전용 (v2.40 신규)
ops_voc_routing       -- 파트별 담당 메일함 ↔ Teams 웹훅 ↔ 온콜 연락처 매핑
ops_email_analysis    -- 이메일 건별 분석 결과 (source_message_id UNIQUE로 중복 수집 방지, 30일 보관, v2.47: embedding/voc_cluster_id FK 추가 — 벡터는 1024차원)
ops_email_poll_cycle  -- 폴링 사이클(스케줄러 실행 회차)별 성공/실패 이력 (30일 보관)
ops_voc_cluster       -- 반복 VOC 클러스터 (representative_embedding=centroid, member_count, coverage_knowledge_id/coverage_verified — 해결방안 LLM 검증 캐시) (v2.47 신규)

-- KnowledgeRAG 전용 (rag_* prefix, v2.8에서 ops_*→rag_* 변경)
rag_knowledge         -- 지식 베이스 (HNSW + GIN FTS, base_weight(v2.121부터 입력 없음·전부 1.0), source_file/chunk_idx 추적,
                      --   status: active/pending_review/rejected/deleted(v2.68 소프트삭제),
                      --   logical_document_id/version/supersedes_id: 근거 정정 승인 시 버전 교체에 사용(v2.118))
rag_knowledge_duplicate_match -- 중복탐지 매칭 후보 (v2.34)
rag_knowledge_history -- 병합(merge) 시 덮어써지기 전 content/embedding 보존 (v2.68)
rag_knowledge_category -- 카테고리 목록
rag_glossary          -- 용어집 (질문에 글자로 나온 용어만 매칭 — v2.128, 동의어는 rag_glossary_synonym)
rag_conv_summary      -- 대화 요약 (embedding VECTOR(1024), Semantic Recall용)
rag_ingestion_job     -- 인제스천 작업 이력 (source_type, status, auto_glossary/fewshot 수, analyzer_result JSONB)

-- Text-to-SQL 전용 (sql_* prefix) — v2.51에서 에이전트 제거, 마이그레이션 호출도 제거됨
-- (기존 설치엔 테이블이 남아있지만 더 이상 갱신되지 않음. 스키마 상세는 archive/with-text2sql 브랜치 참고)
```

- **HNSW 인덱스** (`vector_cosine_ops`): 벡터 근사 최근접 이웃 검색
- **GIN 인덱스** (`to_tsvector('simple', content)`): 전문 검색(FTS)
- **pg_trgm**: 트리그램 유사도 지원 (활성화됨)
- **namespace_id integer FK**: 모든 테이블에서 도메인 격리. namespace 이름 변경 시 cascade 업데이트 불필요
- **CASCADE 삭제**: `ops_conversation.user_id` → 사용자 삭제 시 대화 자동 삭제

### 5. Ollama — LLM 추론 (`:11434`)

- 호스트 머신에서 직접 실행 (컨테이너 외부)
- 모델: `exaone3.5:2.4b`
- Backend에서 `host.docker.internal:11434`로 접근
- **`/api/chat` 엔드포인트** 사용 (GPT 방식 messages 배열, multi-turn 지원)

---

## 임베딩 모델

| 항목 | 값 |
|------|-----|
| 모델명 | `nlpai-lab/KURE-v1` (v2.72부터, 이전 `paraphrase-multilingual-mpnet-base-v2`) |
| 벡터 차원 | 1024 |
| 한국어 지원 | O |
| 실행 위치 | Backend 컨테이너 내 (CPU) |
| 캐시 볼륨 | `model-cache:/root/.cache/huggingface` |

- Docker 빌드 시 이미지에 모델 사전 다운로드 (컨테이너 시작 지연 없음)
- `normalize_embeddings=True` 적용 → 코사인 유사도 = 내적
- `EmbeddingService`는 `asyncio.Lock` 1개로 `embed()`/`embed_batch()`/`embed_long()`의 모델·토크나이저 접근을 직렬화(v2.49) — HuggingFace fast tokenizer(Rust, GIL 해제)가 동시 요청 시 내부 상태가 깨지는 `RuntimeError: Already borrowed`를 동시성 부하 실측 중 확인해 추가
- 같은 텍스트라도 **용도에 따라 다른 임베딩을 쓰는 경우가 있다**(v2.49) — VOC는 지식 검색(RAG)엔 원문 임베딩을, 반복 유형 클러스터링엔 LLM이 뽑은 정규화 요약(`issue_signature`)의 임베딩을 쓴다: 정밀 매칭은 문맥 보존이 유리하고, 표현 차이를 넘어선 그룹핑은 정규화가 유리하다는 기준

---

## API 엔드포인트

손으로 관리하던 엔드포인트 목록은 제거했다(2026-10-02) — 실제와 계속 어긋났다(없어진 `/api/feedback` 가중치 조정,
`/api/stats` 해결 처리 등이 남아 있었음). 현재 스펙은 FastAPI 자동 문서 `http://<backend>:8000/docs`가 정확하다.

## LLM Provider 확장 구조

```python
# domain/llm/base.py
def build_messages(context, question, history=None) -> list[dict]:
    # [system: 시스템프롬프트+참고문서] + [history...] + [user: 질문]

class LLMProvider(ABC):
    async def generate(context, question, history=None, api_key=None) -> str: ...
    async def generate_stream(context, question, history=None, api_key=None) -> AsyncIterator[str]: ...
    async def health_check() -> bool: ...

# 현재 구현체
OllamaProvider     # LLM_PROVIDER=ollama — /api/chat (messages 배열, multi-turn)
InHouseLLMProvider # LLM_PROVIDER=inhouse — DevX MCP API (usecase_code, query, response_mode)
                   #   inputs.model로 모델 선택 (GPT 5.2 / Claude Sonnet 4.5 / Gemini 3.0 Pro)
                   #   SSE: data JSON 안에 event 필드 포함하는 비표준 형식 대응
                   #   api_key: per-user 키 우선, 없으면 시스템 기본 키 사용
```

**`api_key` 파라미터 (v2.0.0 신규):**
- `generate()`, `generate_stream()`에 선택적 `api_key` 파라미터 추가
- 사용자가 개인 LLM API Key를 등록한 경우, Fernet 복호화 후 이 파라미터로 전달
- 개인 키가 없으면 `None` → Provider가 시스템 기본 키(`INHOUSE_LLM_API_KEY`) 사용
- OllamaProvider는 `api_key` 무시 (로컬 모델이므로 불필요)

**대화 맥락 전달 방식 (ConversationSummaryBuffer + Semantic Recall):**
```
messages = [
  {"role": "system",    "content": "시스템 프롬프트\n\n[참고 문서]\n{검색 결과}"},
  {"role": "system",    "content": "이 대화의 관련 과거 맥락:\n[과거 맥락 1]\n{요약}"},  ← Semantic Recall (유사도 0.45 이상)
  {"role": "user",      "content": "이전 질문"},   ← 최근 2회 raw 교환
  {"role": "assistant", "content": "이전 답변"},
  {"role": "user",      "content": "현재 질문"},
]
```

**메모리 동작 원리:**
- 4회 교환마다 오래된 대화를 LLM으로 요약 → `ops_conv_summary`에 임베딩 저장 (백그라운드)
- 새 질문 시 현재 질문 벡터로 과거 요약 검색 → 유사도 0.45 이상 최대 2개 추출
- 최근 2회 raw 교환은 항상 포함 (working memory)

**런타임 전환 (재시작 없음):**
- Admin → LLM 설정 탭에서 프로바이더 선택/설정 후 "저장 및 적용"
- `switch_provider(config)` 호출 → 싱글톤 교체, `_runtime_config` 전역 저장
- 컨테이너 재시작 시 `.env` 설정으로 복귀
- `get_runtime_config()`: 런타임 override 여부(`is_runtime_override`), 연결 상태(`is_connected`) 포함 반환

새 LLM 추가 시: `LLMProvider` 상속 → 3개 메서드 구현 → `service/llm/factory.py`에 등록

---

## 환경변수 설정

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `DATABASE_URL` | `postgresql://ops:ops1234@postgres:5432/opsdb` | DB 연결 문자열 |
| `LLM_PROVIDER` | `inhouse` | `ollama` 또는 `inhouse` |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434` | Ollama 서버 주소 |
| `OLLAMA_MODEL` | `exaone3.5:7.8b` | 사용할 Ollama 모델명 |
| `OLLAMA_TIMEOUT` | `900` | CPU 추론 최대 대기 시간(초), httpx read timeout에 적용 |
| `INHOUSE_LLM_URL` | (없음) | DevX MCP API 엔드포인트 URL |
| `INHOUSE_LLM_API_KEY` | (없음) | 사내 LLM 시스템 기본 API 키 (Bearer 토큰) |
| `INHOUSE_LLM_MODEL` | (없음) | inputs.model 파라미터 (gpt-5.2, claude-sonnet-4.5, gemini-3.0-pro) |
| `INHOUSE_LLM_AGENT_CODE` | `playground` | DevX usecase_code |
| `INHOUSE_LLM_RESPONSE_MODE` | `streaming` | 응답 방식 (`streaming` \| `blocking`) |
| `EMBEDDING_MODEL` | `nlpai-lab/KURE-v1` (v2.72부터) | 임베딩 모델명 |
| `VECTOR_DIM` | `1024` | 벡터 차원 수 |
| `DEFAULT_TOP_K` | `5` | 기본 검색 결과 수 |
| `DEFAULT_W_VECTOR` | `0.7` | 기본 벡터 검색 비중 |
| `DEFAULT_W_KEYWORD` | `0.3` | 기본 키워드 검색 비중 |
| `BACKEND_URL` | `http://backend:8000` | Frontend → Backend 주소 |
| `JWT_SECRET_KEY` | (필수) | JWT 서명 비밀 키 (HS256) |
| `FERNET_SECRET_KEY` | (필수) | Fernet 대칭 암호화 키 (사용자 API Key 암호화용) |
| `ADMIN_DEFAULT_PASSWORD` | (필수) | 초기 admin 계정 비밀번호 |

---

## pgvector 데이터 구성

### 벡터가 쓰이는 테이블

| 테이블 | 벡터 컬럼 | 용도 |
|--------|----------|------|
| `rag_knowledge` | `embedding VECTOR(1024)` | 문서 내용 임베딩 → 질문과 코사인 유사도로 관련 문서 검색 |
| `rag_glossary` | `embedding VECTOR(1024)` | 용어집 화면의 벡터 검색·동의어 품질 게이트용(v2.128부터 채팅 용어 매칭은 글자 매칭, `GLOSSARY_MATCH_MODE=embedding`이면 예전 방식) |
| `rag_conv_summary` | `embedding VECTOR(1024)` | 과거 대화 요약 임베딩 → 현재 질문과 유사한 과거 맥락 Semantic Recall (유사도 0.45 이상) |
| `policy_chunk` · `ops_email_analysis` · `ops_voc_cluster` | `VECTOR(1024)` | 정책 서술 검색 · VOC 메일 유사도/클러스터 |

### 검색 점수 공식

```
final_score = (w_vec × v_score + w_kw × k_score) × (1 + base_weight)
               └벡터 유사도    └BM25 키워드 점수    └문서 자체 가중치
```

- **v_score**: 코사인 유사도 (0~1) — HNSW 인덱스로 근사 탐색
- **k_score**: `ts_rank` BM25 점수 — GIN 인덱스로 전문 검색
- **base_weight**: 문서별 가중치 — v2.121부터 입력 경로가 없고 전부 1.0(상수). 채팅 채택 게이트는 이 값을 걷어낸 원점수로 판단.
  식에서 빼려면 메일 VOC 인용 임계값 재측정이 먼저(컬럼 삭제 때 같이)

### 비벡터 주요 테이블

`ops_part`, `ops_user`, `ops_namespace`, `rag_knowledge_category`, `ops_improvement_item`, `ops_query_log`, `ops_conversation`, `ops_message`, `ops_prompt`, `ops_system_config`

전체 스키마 정의는 `docs/table-definition.md` 참조.
