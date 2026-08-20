# Ops-Navigator 시스템 아키텍처 (v2.46)

## 개요

Ops-Navigator는 IT 운영팀의 반복적인 조회·확인 업무를 자동화하는 **지능형 운영 보조 에이전트 플랫폼**이다.
사용자는 에이전트를 선택해 목적에 맞는 AI를 사용한다: 지식 기반 Q&A(KnowledgeRAG) 또는 자연어 → SQL 쿼리 실행(Text-to-SQL).

**주요 이력 요약** (스키마 변경 상세는 `table-definition.md` §20 마이그레이션 이력 참조)
- v2.46: VOC 이메일 — 인하우스 LLM 게이트웨이가 프롬프트에 IP·이메일·전화번호가 섞이면 "민감 정보 포함"으로 응답을 통째로 거부하는 정책이 실사용 중 확인됨(호스트 IP·CC 목록·서명란 연락처가 거의 모든 실 메일에 있어, 사실상 대부분의 메일이 분석되지 못하던 상태). `service.py`에 `_mask_pii()` 추가 — LLM 프롬프트에 넣기 직전에만 IP/이메일/전화번호를 마스킹(DB 저장·Teams 알림은 원본 유지). 아울러 관련성 게이트 개선안으로 `shared/reranker.py`(기존 chat 전용 CrossEncoder 리랭커)에 점수 노출용 `score()`/`is_available()`을 추가해뒀으나, 이 개발 환경은 huggingface.co 접속이 막혀 모델을 못 받아 게이트 연동·실측은 보류(상세: `docs/tech/voc-email-handoff.md` §7-11, §7-12, git 비추적).
- v2.45: VOC 이메일 관련지식 필터 무력화 버그 수정 — `retrieval.search_knowledge()`가 반환하는 `final_score`는 검색 랭킹용으로 `(가중합)*(1+base_weight)`가 곱해져 있는데(base_weight 기본값 1.0), `service.check_relevance()`가 이 부풀려진 값을 그대로 관련성 게이트(`email_relevance_min_score`)와 비교해와 무관한 메일도 임계치를 가볍게 넘어 Teams 알림 노이즈를 유발하던 것이 실사용 중 확인됨. 게이트 판단은 base_weight 부스팅 없는 원점수(`w_vector*v_score + w_keyword*k_score`)로 계산하도록 수정(지식 인용 랭킹은 기존 `final_score` 유지 — 목적이 다른 두 계산을 분리). 실측 재조정으로 임계치 0.35 → 0.38.
- v2.44: VOC 이메일 3건 개선 — ① **메일 폴더 범위 제한**: `graph_client.list_mail_folders()`/`GET /mail-folders`로 실제 Outlook 폴더 목록을 조회해 라우팅에서 선택 가능(`ops_voc_routing.mail_folder_id`) — 지정 시 그 폴더만 폴링. ② **이력 필터링**: `GET /history`에 심각도/상태/오배치여부/키워드 쿼리 파라미터 추가. ③ **Teams 카드 개선**: 제목/내용/해결방안/참고지식(근거+유사도) 섹션 구조화, 심각도 4단계(low/medium/high/urgent)를 각각 다른 색으로 구분(기존엔 사실상 2단계로만 시각 구분됨).
- v2.43: VOC 이메일 Delegated 로그인 실사용 성공 + 실사용 중 발견한 버그 2건 수정. ① **Confidential Client 지원**: 등록된 리다이렉트 URI가 Azure AD "Web" 플랫폼으로 등록된 경우 PKCE만으로 토큰 교환이 거부되는(AADSTS7000218) 사례가 실측 확인돼, `delegated_auth.py`의 `_build_app()`이 `client_secret` 설정 여부에 따라 `PublicClientApplication`/`ConfidentialClientApplication`을 분기하도록 변경(시크릿 값은 Fernet 암호화로 DB 저장, API 응답엔 `client_secret_configured` bool만 노출). ② **키워드 검색 tsquery 크래시 수정(공용 검색 엔진 영향)**: `agents/knowledge_rag/knowledge/retrieval.py`의 `search_knowledge()`가 lexeme을 `string_agg`로 이어붙여 `to_tsquery`에 그대로 넘기던 것을, URL 등에서 추출된 lexeme에 짝 안 맞는 괄호 등 tsquery 특수문자가 섞이면 `syntax error in tsquery`로 죽던 버그(VOC 실메일 fetch 중 실측 재현) — `quote_literal()`로 각 lexeme을 감싸 해결. 채팅 KnowledgeRAG 검색도 동일 함수를 쓰므로 함께 수정됨. ③ **폴링 성능 최적화**: `pipeline.run_manual_collection()`이 재조회 윈도우(lookback_days) 안의 메일을 매 사이클 다시 fetch하면서, 이미 처리된 메일까지 관련지식 검색+LLM 분석을 먼저 돌리고서야 DB `ON CONFLICT`로 중복임을 알던 구조를 — fetch 직후 `(namespace_id, source_message_id)` 배치 조회로 먼저 걸러내도록 변경. 실측: 144건 재조회 시 190초(전량 재분석) → 1.3초(전량 사전 스킵)로 개선.
- v2.42: VOC 이메일 Delegated 로그인 방식 교체 — Device Code Flow(코드를 사람이 손으로 옮겨 입력) 대신 **Authorization Code Flow(PKCE, Public Client)**로 변경. 사유: Device Code Flow는 "Device Code Phishing"(공격자가 자기 코드를 발급받아 피해자에게 입력시켜 토큰을 가로채는 공격) 리스크가 있어, 필요 설정("Allow public client flows")을 보안팀이 비권장 사유로 거부. Authorization Code Flow는 사람이 코드를 옮겨 입력하는 과정 자체가 없어 이 리스크가 없고, Azure AD 쪽엔 정식 지원 필드인 리다이렉트 URL만 등록하면 된다. `service/email_voc/delegated_auth.py`의 `start_login()`을 재작성하고 `complete_login()`을 신규 추가, `router.py`에 콜백 엔드포인트(`GET /delegated-auth/callback`, 인증 불필요 — MSAL의 PKCE/state 검증으로 CSRF 방어) 추가. `ops_system_config.email_graph_delegated`에 `redirect_uri` 필드 추가(프론트가 `window.location.origin` 기준으로 자동 계산). 로컬 CLI 스크립트(`email_voc_local_test.py`)는 터미널 환경 특성상 Device Code Flow를 그대로 유지(관리자 화면과는 별개 경로).
- v2.41: VOC 이메일 채널 2건 추가 — ① **관련지식 사전 필터**: `service/email_voc/service.py`의 `analyze_email()`을 `check_relevance()`(임베딩+검색)와 LLM 분석 단계로 분리, 등록된 지식과의 최고 유사도가 관리자 설정 임계치(`email_relevance_min_score`, 기본 0.35) 미만이면 LLM 호출·Teams 발송 없이 `status='skipped_relevance'`로만 기록해 무관한 메일(스팸·사내공지 등)의 비용·알림 노이즈를 억제. ② **Delegated Permission 로그인**(`service/email_voc/delegated_auth.py`, Device Code Flow): Application 권한(Track B) 승인 전에도 관리자가 본인 계정으로 1회 로그인하면 이후 기존 폴링 토글/수동실행이 그 세션으로 자동 대체 동작 — `pipeline.run_manual_collection()`에 `access_token`/`skip_credential_resolution` 파라미터 추가로 연결(운영 경로인 Application 권한 흐름은 변경 없음). 로컬 검증용 `backend/scripts/email_voc_local_test.py` 추가.
- v2.40: VOC 이메일 분석 채널(Track A) 신규 구현 — `service/email_voc/` 모듈(수집·RAG 분석·라우팅·Teams 알림·백그라운드 폴링 스케줄러·30일 보관정책). 파트별 공용 메일함을 Microsoft Graph API(msal client_credentials)로 수집해 기존 하이브리드 검색·LLM 파이프라인으로 분석 후 담당 파트 Teams 채널에 자동 알림. Graph API 실 연동(Track B)은 M365 보안성 검토·API 제공 등 조직 승인 대기 중 — 승인 전까지는 텍스트 직접 입력 테스트/수동 자격증명 미설정 상태로 동작. 상세 설계는 `docs/email-analysis-channel-plan.md` 참조.
- v2.39: 로그인 브루트포스 방어 — Redis 기반 rate limiting(사용자명+IP 조합, 5분 내 5회 실패 시 5분 잠금, `shared/rate_limit.py`, Redis 미연결 시 제한 없이 통과) + 아이디 존재 여부와 비밀번호 오류를 구분하지 않는 통합 에러 메시지로 계정 열거(enumeration) 오라클 제거.
- v2.38: 백엔드 전수 감사 2차(auth/admin/teams/text2sql) — 관리자 파트로 셀프 회원가입 가능하던 구멍, 파트 삭제 시 소유 네임스페이스가 조용히 "공통 파트"(전원 허용)로 전환되던 문제, `PUT /api/llm/config` 관리자 권한 누락, 네임스페이스 rename/delete 시 시맨틱 캐시 미무효화, 캐시 Redis glob 인젝션, text2sql SQL 안전성 검사 우회(pg_read_file/dblink 등), text2sql 크로스테넌트 쓰기 등 Critical/High 다수 수정. 1차 감사 잔여 Medium/Low 10건(no_knowledge 판정 보강, 대화요약 tie-breaker, 대량등록 배치 내 상호중복검사, fewshot 상태 응답 버그, TOCTOU 완화 등)도 함께 정리. 지식조회 화면에 가중치 정렬(높은순/낮은순) 추가.
- v2.37: 백엔드 전수 감사 1차(fewshot/chat/knowledge_rag/mcp_tool) — 나빠요 피드백 후 지식 등록 시 오답 원인 지식의 가중치가 오히려 올라가던 버그를 계기로 같은 클래스(플래그 오버로딩, stale 데이터, 소유권 검사 누락)의 버그를 다른 모듈에서도 탐색. MCP 도구 승인 카드가 미입력 필수 파라미터를 example 힌트값으로 몰래 채워 승인을 통과시키던 것, 시맨틱 캐시가 agent_type 구분 없이 knowledge_rag/mcp_tool 답변을 서로 새게 하던 것 등 Critical 2건 포함.
- v2.36: 나빠요 피드백 → 지식등록/지식공백 해결 흐름 버그 수정 — `is_positive` 플래그를 상태 판정과 가중치 방향 두 용도로 겹쳐 쓰던 게 원인. `ops_query_log.resolved_knowledge_id` 컬럼 추가로 해결 처리된 질의를 실제 등록 지식과 연결(통계 화면이 원래 AI 오답 대신 등록 내용을 보여줌), 피드백/해결 매칭을 질문 텍스트 대신 message_id로 정밀화, 통계 페이지 "승인" 원클릭 버튼이 raw INSERT로 중복검사·업무구분 없이 등록하던 것을 `create_knowledge()` 재사용으로 교체, 등록 폼들의 하드코딩된 "없음(파트 공통)" 카테고리 옵션 제거.
- v2.35: 파이프라인 디버그 탭 UX — 업무구분 필터와 MCP 도구 사용 토글을 같은 행(좌/우)에 배치, 업무구분 드롭다운의 "전체"를 다른 항목과 동일한 체크박스로 바꿔 상호배타 선택 명확화.
- v2.34: 지식 중복 등록 방지 — 등록 시점에 청크 단위로 기존 활성 지식과 유사도 비교(`duplicate_min_similarity`, 기본 0.88), 임계값 이상이면 `rag_knowledge.status='pending_review'`로 저장해 검색에서 숨기고 승인 대기 큐로 전환. 관리자가 승인(그대로 인정)/반려(status='rejected', 감사 기록 보존)/덮어쓰기(기존 지식의 content를 새 내용으로 교체) 중 선택. 대량 업로드는 배치 단위 병렬(`asyncio.gather`) 유사도 검사로 대규모 등록에도 지연 없음. 매칭된 기존 지식 후보는 `rag_knowledge_duplicate_match` 테이블에 top-N 기록, 리뷰 화면에서 좌(신규)/우(기존, 아코디언으로 펼쳐 전문 확인) 비교 후 처리.
- v2.33: 지식 등록/수정 폼 UX 개선 — 업무구분(category)을 모든 등록·수정 폼(수정 모달, 직접입력, 파일업로드, 텍스트분할, URL/Confluence, Teams)의 최상단 필드로 재배치. 가장 먼저 결정해야 하는 값인데 폼마다 위치가 달라 놓치기 쉬웠음.
- v2.32: 지식 공백(no_knowledge) 판정 보정 — 검색 문서가 점수 임계값은 넘었지만 실제로 무관해 LLM이 "관련 지식을 찾지 못했습니다"로 답한 경우도 지식 공백으로 분류하도록 `create_query_log`에 답변 문구 기반 게이트 추가, 기존 오분류 데이터 소급 보정.
- v2.31: 어드민 지식 테이블 일괄 수정 — 다건 선택 후 업무구분/소스유형을 한 번에 변경(`POST /api/knowledge/bulk-update`, 값을 지정한 필드만 변경). 기존 다건 삭제와 동일한 선택 UI 재사용.
- v2.30: 실사용 피드백 반영 3건 — (1) 멀티턴 검색 관련성 게이트(무관한 주제로 전환된 질문이 직전 맥락에 오염되지 않도록 임베딩 유사도 확인 후 결합), (2) 에이전트별 대화방 분리(`ops_conversation.agent_type` 실제 저장 + 목록 필터링 + 다른 에이전트로 이어쓰기 시 409 거부), (3) 채팅 업무구분 필터를 단일 선택(사이드바)에서 입력창 위 다중 선택 드롭다운으로 교체 — `ChatRequest.categories: string[]`, `search_knowledge`가 `k.category = ANY(...)`로 다중 필터.
- v2.29: 대용량 등록 진행률 표시 + 중지/롤백 — `bulk_create_knowledge` 백그라운드(asyncio.create_task) + 배치(50건) 처리로 재구성, job_id 즉시 반환 + 배치마다 진행률 갱신. 신규 API `ingestion-jobs/{id}`(폴링) / `.../cancel`(중지 시 등록분 롤백). 청크 검토 모달에 업무구분 필드 보완.
- v2.28: 업무구분 기본값 `'공통지식'` 지정 + 기존 미분류 지식 일괄 백필, 카테고리 없는 네임스페이스 자동 생성.
- v2.27: 업무구분(category) 전 등록 경로 필수화 — 서비스 레벨 공통 검증(`_require_category`), `ValueError`→400 글로벌 핸들러 추가.
- v2.26: AI 용어추천 데이터소스 선택(미매핑 질문/등록된 지식) + 용어집 중복 등록 방지(대소문자 무시 체크, 프롬프트+응답 이중 필터).
- v2.25: 어드민 UI 개선 — 등록자 노출, 토글 knob 위치 버그 수정, 업무유형 분포 라벨을 용어집 설명 기반으로 개선, 미구현 placeholder 카드 제거.
- v2.24: 엑셀 임포트 샘플 템플릿(xlsx) 다운로드 API/버튼 추가.
- v2.23: Text2SQL 스키마 엑셀 임포트 — 헤더 퍼지 매핑, preview/confirm 2단계, 중복 skip, 임베딩 자동 생성.
- v2.22: 지식 공백(no_knowledge) 대시보드 시각화 — 카드 클릭 → 질문 목록 → 등록 폼 팝업 흐름.
- v2.21: 런타임 안정성 버그 3건 수정(namespace FK 오류, 요약 파싱 오류, null byte ingestion 실패).
- v2.20: EUC-KR 인코딩 지원(CSV/MD/TXT), PDF 리소스 누수 수정.
- v2.19: CrossEncoder 리랭커, 지식 신선도 Decay 옵션, 지식 갭(no_knowledge) 자동 감지.
- v2.18: 안정성 버그 수정 배치(URL preview, HTML 파싱, 청킹, 캐시 키 충돌 등).
- v2.17: DevX LLM OAuth2 전환(사용자별 자격증명 Fernet 암호화) + Confluence BFS 일괄 등록.
- v2.16: 어드민 테이블 텍스트/벡터 검색 + 체크박스 다건 삭제.
- v2.15: Teams 메시지 수집(OpsNavHelper.exe 기반 토큰 캡처) → 지식베이스 등록.
- v2.14: ChunkReviewModal, LLM Analyzer 자동 청킹, 레거시 Streamlit 삭제.
- v2.13: URL/Confluence 인제스천(httpx+BeautifulSoup, 등록 전 확인 모달).
- v2.12: 지식 인제스천 고도화 — 텍스트 분할, 파일 업로드, 자동 태깅/Q&A 생성.
- v2.9~v2.11: Text2SQL 고도화 — Oracle 지원 + Dialect 패턴 리팩터링, 스키마 스캔 diff 개선, `ops_prompt` 에이전트별 분리.
- v2.5~v2.8: Text2SQL 에이전트 도입(7단계 파이프라인) + ERD + MCP 도구, `domain/`→`service/` 구조 재편.
- v2.0~v2.4: 초기 구조 — DDD, JWT 인증, AgentRegistry 패턴, Semantic Cache.

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
| **AgentSelect** (로그인 직후) | 에이전트 선택 화면 — 지식베이스 AI / Text-to-SQL 카드 선택. `selectedAgent=null`이면 이 화면 표시 (사이드바 없음) |
| **Chat** (`/`) | 에이전트별 채팅 — SSE 스트리밍, 결과 카드, 피드백(👍→few-shot/base_weight), 대화 메모리(요약+리콜), Markdown 답변, SQL블록+결과테이블+SVG차트 (text2sql), MCP 도구 토글 |
| **Admin** (`/admin`) | 에이전트별 관리 화면 — `agentScope` 필드로 탭 필터링. knowledge_rag: 네임스페이스·지식·용어집·Few-shot·MCP도구·캐시현황·통계·디버그. text2sql: 대상DB·스키마·ERD·용어사전·SQL Few-shot·파이프라인·감사로그. 공통: 시스템설정·사용자관리. (에이전트현황 탭 제거 — AgentSelect 화면에 헬스배지로 대체) |

- **Agent-centric 라우팅**: `useAppStore.selectedAgent: 'knowledge_rag' | 'text2sql' | null`. null이면 AgentSelect 표시, 설정 시 에이전트별 UI로 전환. 로그아웃 시 null로 리셋
- **MCP 도구 토글**: ChatContainer 내 `useHttpTool` boolean — ON 시 `agentType='mcp_tool'`, OFF 시 `selectedAgent` 값 사용. 에이전트가 아닌 도구
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
│   │   ├── knowledge/   #   지식/용어집 CRUD + 하이브리드 검색 (retrieval.py)
│   │   ├── ingestion/   #   지식 인제스천 파이프라인 (Tier 1~3)
│   │   │   ├── adapters.py      #   파일 파싱 (.txt/.md/.pdf → ParsedDocument)
│   │   │   ├── chunker.py       #   청킹 엔진 (section/paragraph/fixed/auto)
│   │   │   ├── analyzer.py      #   LLM Analyzer Agent (doc_type, chunk_strategy 자동 결정)
│   │   │   ├── tagger.py        #   LLM 자동 태깅 + 용어 추출
│   │   │   ├── qa_gen.py        #   LLM Q&A 자동 생성 → fewshot candidate
│   │   │   ├── web_crawler.py   #   URL/Confluence 수집 (httpx + BeautifulSoup + Confluence REST API)
│   │   │   └── utils.py         #   공통 JSON 파싱 헬퍼
│   │   └── fewshot/     #   Few-shot CRUD (status: active/candidate)
│   ├── mcp_tool/
│   │   └── agent.py     #   McpToolAgent — 3-case 플로우 + RAG + 감사 로그
│   ├── text2sql/
│   │   ├── agent.py     #   Text2SqlAgent (startup 병렬화, _cache_hit)
│   │   ├── admin/       #   Text2SQL 어드민 API (대상DB·스키마·ERD·용어사전·Few-shot·파이프라인·감사로그)
│   │   │   └── excel_importer.py  #   엑셀 스키마 임포터 (헤더 퍼지 매핑, parse_excel, rows_to_tables, build_sample_workbook)
│   │   └── pipeline/    #   7단계: parse→rag→generate→validate→fix→execute→summarize
│   └── http_tool/       #   HttpToolAgent (레거시)
├── service/             # 플랫폼 공통 레이어 (was domain/, platform/ 명칭 stdlib 충돌로 service/ 확정)
│   ├── auth/            #   인증/계정 (JWT, bcrypt, Fernet API Key 암호화)
│   ├── chat/            #   채팅 라우터·헬퍼·메모리 (AgentRegistry 위임)
│   ├── feedback/        #   피드백 기록 + base_weight 조정
│   ├── admin/           #   네임스페이스·통계·LLM 설정
│   ├── mcp_tool/        #   MCP 도구 CRUD + 감사 로그
│   ├── prompt/          #   프롬프트 관리 (get_prompt: DB 우선, fallback)
│   ├── llm/             #   LLM Provider 추상화 (ollama / inhouse)
│   └── email_voc/       #   VOC 이메일 분석 채널 (v2.40 신규)
│       ├── graph_client.py    #   Microsoft Graph API 클라이언트 (msal 토큰 발급, 메일 조회, 페이지네이션/재시도)
│       ├── service.py         #   check_relevance()(관련지식 사전 필터, v2.41) + analyze_email() — 기존 RAG 파이프라인 재사용 분류/심각도/오배치 판정
│       ├── pipeline.py        #   수집→관련지식필터→분석→중복제거→알림 오케스트레이션, 이력 조회
│       ├── routing_service.py #   파트별 메일함 라우팅 CRUD, 폴링 설정(관련지식 임계치 포함), Graph 자격증명(Fernet 암호화) CRUD
│       ├── delegated_auth.py  #   Delegated Permission 로그인 상태 관리 (Authorization Code Flow/PKCE, v2.41 신규·v2.42 인증방식 교체)
│       ├── teams_notify.py    #   Teams Workflows 웹훅 발송
│       ├── scheduler.py       #   백그라운드 폴링 루프 (asyncio.create_task, lifespan 등록)
│       ├── retention.py       #   30일 고정 보관정책 자동 정리
│       ├── schemas.py         #   Pydantic 스키마
│       └── router.py          #   /api/email-voc/* 엔드포인트
├── core/
│   ├── config.py        # pydantic-settings, JWT·Fernet 키
│   ├── database.py      # asyncpg 풀 + resolve_namespace_id() 헬퍼
│   ├── security.py      # JWT, bcrypt, Fernet
│   └── dependencies.py  # get_current_user, get_current_admin, check_namespace_ownership
└── shared/
    ├── embedding.py     # Sentence-Transformers 싱글톤
    └── cache.py         # Semantic Cache (Redis, 유사도 0.88, TTL 30분, graceful degradation)
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
- **수정 시 작성자 갱신**: 지식/용어/퓨샷 수정 시 `created_by_part`/`created_by_user_id`가 최종 수정자로 갱신됨
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

**권한 모델 (네임스페이스 기반):** 상세 규칙은 `api-specification.md § 3. 인증 및 권한` 참조.
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
ops_feedback          -- 👍/👎 피드백 로그 (agent_type, meta JSONB)
ops_query_log         -- 질의 로그 (status: pending/resolved/unresolved, agent_type)
ops_mcp_tool          -- MCP 도구 정의 (hub_base_url, tool_path, param_schema JSONB, agent_type)
ops_mcp_tool_log      -- MCP 도구 감사 로그
ops_prompt            -- 프롬프트 관리 (agent_type별 에이전트 스코핑, Admin 시스템설정 탭에서 편집)
ops_system_config     -- 시스템 설정 key-value (캐시 임계값/TTL 등 영속화, VOC 폴링 정책/Graph 자격증명도 여기 저장)

-- VOC 이메일 분석 채널 전용 (v2.40 신규)
ops_voc_routing       -- 파트별 담당 메일함 ↔ Teams 웹훅 ↔ 온콜 연락처 매핑
ops_email_analysis    -- 이메일 건별 분석 결과 (source_message_id UNIQUE로 중복 수집 방지, 30일 보관)
ops_email_poll_cycle  -- 폴링 사이클(스케줄러 실행 회차)별 성공/실패 이력 (30일 보관)

-- KnowledgeRAG 전용 (rag_* prefix, v2.8에서 ops_*→rag_* 변경)
rag_knowledge         -- 지식 베이스 (HNSW + GIN FTS, base_weight, source_file/chunk_idx 추적)
rag_knowledge_category -- 카테고리 목록
rag_glossary          -- 용어집 (HNSW, 유사도 0.5+ 매핑)
rag_fewshot           -- Few-shot Q&A (HNSW, status: active/candidate)
rag_conv_summary      -- 대화 요약 (embedding VECTOR(768), Semantic Recall용)
rag_ingestion_job     -- 인제스천 작업 이력 (source_type, status, auto_glossary/fewshot 수, analyzer_result JSONB)

-- Text-to-SQL 전용 (sql_* prefix)
sql_target_db         -- 대상 DB 연결 설정 (암호화 저장, schema_name: PG schema / Oracle owner)
sql_schema_table      -- 테이블 메타데이터 (pos_x/pos_y ERD 위치)
sql_schema_column     -- 컬럼 메타데이터 (is_pk, fk_reference)
sql_relation          -- FK 관계 정의
sql_synonym           -- 자연어 → SQL 표현 매핑 (embedding VECTOR(768))
sql_fewshot           -- Q&A Few-shot (embedding VECTOR(768), status: pending/approved/rejected)
sql_pipeline_stage    -- 파이프라인 단계 설정 (is_enabled/order_num 등 메타. 프롬프트는 ops_prompt sql2_* 키 사용)
sql_audit_log         -- 쿼리 실행 감사 로그
sql_cache             -- 쿼리 결과 캐시
sql_schema_vector     -- 스키마 벡터 인덱스
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
| 모델명 | `paraphrase-multilingual-mpnet-base-v2` |
| 벡터 차원 | 768 |
| 한국어 지원 | O |
| 실행 위치 | Backend 컨테이너 내 (CPU) |
| 캐시 볼륨 | `model-cache:/root/.cache/huggingface` |

- Docker 빌드 시 이미지에 모델 사전 다운로드 (컨테이너 시작 지연 없음)
- `normalize_embeddings=True` 적용 → 코사인 유사도 = 내적

---

## API 엔드포인트 목록

### 인증 (`/api/auth`) — v2.0.0 신규

| 메서드 | 경로 | 인증 | 설명 |
|--------|------|------|------|
| `POST` | `/api/auth/register` | 없음 | 회원가입 (부서 선택 + 선택적 LLM API Key) |
| `POST` | `/api/auth/login` | 없음 | 로그인 → Access Token(30min) + Refresh Token(7days) 발급 |
| `POST` | `/api/auth/refresh` | Refresh Token | Access Token 갱신 |
| `GET` | `/api/auth/me` | Bearer | 내 정보 조회 |
| `PUT` | `/api/auth/me/password` | Bearer | 비밀번호 변경 |
| `PUT` | `/api/auth/me/api-key` | Bearer | 개인 LLM API Key 등록/변경 (Fernet 암호화 저장) |
| `PUT` | `/api/auth/me/confluence-pat` | Bearer | 개인 Confluence PAT 등록/변경 (Fernet 암호화 저장) |
| `DELETE` | `/api/auth/me/confluence-pat` | Bearer | 개인 Confluence PAT 삭제 |
| `GET` | `/api/auth/me/confluence-pat/status` | Bearer | Confluence PAT 등록 여부 조회 |
| `GET` | `/api/auth/users` | Admin | 전체 사용자 목록 |
| `PUT` | `/api/auth/users/{id}` | Admin | 사용자 정보 수정 (역할 변경 등) |
| `DELETE` | `/api/auth/users/{id}` | Admin | 사용자 삭제 |
| `GET` | `/api/auth/parts` | 없음 | 부서 목록 조회 (회원가입용 — 슈퍼어드민 파트 자동 제외) |
| `GET` | `/api/auth/parts/all` | Admin | 부서 목록 전체 조회 (관리자용 — 슈퍼어드민 파트 포함) |
| `POST` | `/api/auth/parts` | Admin | 부서 생성 |
| `PATCH` | `/api/auth/parts/{id}` | Admin | 부서 이름 변경 (name 컬럼만 업데이트 — integer FK로 cascade 불필요) |
| `DELETE` | `/api/auth/parts/{id}` | Admin | 부서 삭제 (소속 사용자 없는 경우만) |

### 채팅/대화

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/health` | 서버·LLM 상태 확인 |
| `POST` | `/api/chat` | 하이브리드 검색 + LLM 답변 (JSON) |
| `POST` | `/api/chat/stream` | 하이브리드 검색 + LLM 답변 (SSE 스트리밍, 단계별 status 이벤트) |
| `POST` | `/api/chat/debug` | LLM 없이 검색 파이프라인 전 과정 반환 (v_score, k_score, 용어집 유사도, few-shot 목록, LLM 컨텍스트 미리보기 포함) |
| `GET` | `/api/conversations` | 네임스페이스별 대화방 목록 (최근 50개, 본인 소유만) |
| `POST` | `/api/conversations` | 대화방 신규 생성 (user_id 자동 연결) |
| `GET` | `/api/conversations/{id}/messages` | 대화방 전체 메시지 조회 (status 필드 포함) |
| `DELETE` | `/api/conversations/{id}` | 대화방 삭제 (메시지 cascade) |
| `PATCH` | `/api/chat/messages/{id}/content` | 메시지 부분 저장 (프론트엔드 스트림 중단 시) |
| `DELETE` | `/api/chat/messages/{id}` | Ghost 메시지 삭제 (빈 assistant + 짝 user + 빈 대화방) |

### 지식/용어집

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/api/knowledge` | 지식 목록 조회 (namespace 필터) |
| `POST` | `/api/knowledge` | 지식 신규 등록 (네임스페이스 소유 파트 검증, 임베딩 자동 생성) |
| `PUT` | `/api/knowledge/{id}` | 지식 수정 (네임스페이스 소유 파트 또는 admin만) |
| `DELETE` | `/api/knowledge/{id}` | 지식 삭제 (네임스페이스 소유 파트 또는 admin만) |
| `POST` | `/api/knowledge/bulk` | JSON 배열 벌크 등록 — job_id 즉시 반환, 실제 임베딩+등록은 백그라운드 배치 처리 (v2.29) |
| `POST` | `/api/knowledge/import/csv` | CSV 파일 업로드 → 컬럼 매핑 → 벌크 등록 |
| `POST` | `/api/knowledge/import/text-split` | 대량 텍스트 붙여넣기 → 자동 분할 → 벌크 등록 |
| `POST` | `/api/knowledge/import/text-split/preview` | 텍스트 분할 미리보기 (등록 없음) |
| `POST` | `/api/knowledge/import/file` | 파일 업로드(.txt/.md/.pdf) → 파싱 → 청킹 → 벌크 등록 (Analyzer·태깅·용어추출·Q&A 선택적) |
| `POST` | `/api/knowledge/import/file/preview` | 파일 파싱+청킹 미리보기 (등록 없음) |
| `POST` | `/api/knowledge/import/url` | URL/Confluence 페이지 수집 → 청킹 → 벌크 등록 (PAT 미전달 시 DB 저장 개인 PAT 자동 로드) |
| `POST` | `/api/knowledge/import/url/preview` | URL 수집 미리보기 — LLM Analyzer 자동 청킹 전략 결정 (등록 없음) |
| `GET` | `/api/knowledge/ingestion-jobs` | 인제스천 작업 이력 조회 |
| `GET` | `/api/knowledge/ingestion-jobs/{id}` | 인제스천 작업 진행률 조회 (폴링용) — v2.29 신규 |
| `POST` | `/api/knowledge/ingestion-jobs/{id}/cancel` | 진행 중인 인제스천 작업 중지 요청 — 다음 배치 경계에서 중단 + 이미 등록된 데이터 롤백 — v2.29 신규 |
| `GET` | `/api/knowledge/glossary` | 용어집 목록 |
| `POST` | `/api/knowledge/glossary` | 용어 신규 등록 (임베딩 자동 생성) |
| `PUT` | `/api/knowledge/glossary/{id}` | 용어 수정 (재임베딩 자동, 같은 부서 또는 admin만) |
| `DELETE` | `/api/knowledge/glossary/{id}` | 용어 삭제 (같은 부서 또는 admin만) |

### 피드백/Few-shot

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/feedback` | 피드백 기록 + base_weight 조정 + few-shot 저장(👍시) |
| `GET` | `/api/fewshots` | Few-shot 목록 조회 (namespace 필터) |
| `POST` | `/api/fewshots` | Few-shot 신규 등록 (임베딩 자동 생성) |
| `PUT` | `/api/fewshots/{id}` | Few-shot 수정 (질문 변경 시 재임베딩, 같은 부서 또는 admin만) |
| `DELETE` | `/api/fewshots/{id}` | Few-shot 삭제 (같은 부서 또는 admin만) |
| `POST` | `/api/fewshots/search` | 질문으로 few-shot 검색 테스트 (실제 검색 결과 + 프롬프트 섹션 미리보기) |
| `PATCH` | `/api/fewshots/{id}/status` | Few-shot 상태 전환 (`active` ↔ `candidate`) |

### 관리/설정

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/api/namespaces` | 등록된 네임스페이스 목록 (문자열 배열) |
| `GET` | `/api/namespaces/detail` | 네임스페이스 상세 목록 (지식 수, 용어집 수 포함) |
| `POST` | `/api/namespaces` | 네임스페이스 신규 생성 (admin이 생성하면 owner_part=NULL) |
| `PATCH` | `/api/namespaces/{name}` | 네임스페이스 이름 변경 (name 컬럼만 업데이트 — integer FK로 cascade 불필요) |
| `DELETE` | `/api/namespaces/{name}` | 네임스페이스 및 하위 데이터 전체 삭제 |
| `GET` | `/api/llm/config` | 현재 LLM 프로바이더 설정 + 연결 상태 조회 |
| `PUT` | `/api/llm/config` | LLM 프로바이더 런타임 전환 — Admin은 전체 시스템 저장, 일반 사용자는 브라우저 localStorage에만 저장 |
| `POST` | `/api/llm/test` | 설정값으로 연결 테스트 (실제 전환 없음) |
| `GET` | `/api/stats` | 네임스페이스별 통계 (전체 namespace, 지식/용어집 개수 포함) |
| `GET` | `/api/stats/namespace/{name}` | 네임스페이스 상세 통계 (업무 유형별 분포, 미해결 목록) |
| `DELETE` | `/api/stats/query-log/{id}` | 미해결 질의 로그 삭제 (지식 등록 후 처리 완료 표시) |
| `GET` | `/api/admin/cache/stats` | 네임스페이스 Semantic Cache 통계 (total_entries, total_hits, connected) |
| `GET` | `/api/admin/cache/entries` | 캐시 엔트리 목록 (히트 수 내림차순, 질문·TTL·hits 포함) |
| `DELETE` | `/api/admin/cache` | 네임스페이스 캐시 전체 무효화 |
| `DELETE` | `/api/admin/cache/entry` | 단일 캐시 엔트리 삭제 |
| `POST` | `/api/admin/glossary/suggest` | 미매핑 질문 LLM 분석 → 용어 후보 반환 (`limit` 파라미터로 조회 건수 설정, 기본 50, 최대 200) |
| `POST` | `/api/admin/glossary/suggest/apply` | 추천 용어 1-click 등록 (임베딩 자동 생성) |

### Text-to-SQL (`/api/text2sql`) — v2.5 신규, v2.6 확장

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET/PUT` | `/api/text2sql/namespaces/{ns}/target-db` | 대상 DB 연결 설정 조회/저장 |
| `POST` | `/api/text2sql/namespaces/{ns}/target-db/test` | 연결 테스트 |
| `POST` | `/api/text2sql/namespaces/{ns}/target-db/scan` | 스키마 diff 스캔 — 테이블/컬럼 추가·삭제·변경 감지, 변경분만 임베딩. ERD 고아 관계 자동 정리(삭제 테이블/컬럼 관련 relation 삭제), 용어사전 고아 자동 삭제(삭제 컬럼 참조 용어 삭제). 변경 상세 리포트 반환 (v2.10) |
| `GET` | `/api/text2sql/namespaces/{ns}/schema` | 전체 스키마 (테이블+컬럼) 조회 |
| `PUT` | `/api/text2sql/namespaces/{ns}/schema/tables/{id}` | 테이블 설명 수정 |
| `PUT` | `/api/text2sql/namespaces/{ns}/schema/tables/{id}/toggle` | 테이블 RAG 포함 여부 토글 |
| `PUT` | `/api/text2sql/namespaces/{ns}/schema/columns/{id}` | 컬럼 설명 수정 |
| `POST` | `/api/text2sql/namespaces/{ns}/schema/reindex` | 스키마 벡터 재인덱싱 |
| `POST` | `/api/text2sql/namespaces/{ns}/schema/import/excel/preview` | 엑셀 파일 업로드 → 헤더 자동 매핑 + 파싱 결과 미리보기 (등록 없음) — v2.23 신규 |
| `POST` | `/api/text2sql/namespaces/{ns}/schema/import/excel/confirm` | 미리보기 결과 rows 전달 → DB 저장 + 임베딩 생성 (이미 등록된 테이블 skip) — v2.23 신규 |
| `GET` | `/api/text2sql/namespaces/{ns}/schema/import/excel/template` | 샘플 템플릿 xlsx 다운로드 — 권장 헤더 + 예시 데이터 4행. 헤더는 파서의 `_HEADER_CANDIDATES`에서 파생, 정적 바이트라 최초 생성 후 캐싱 — v2.24 신규 |
| `PUT` | `/api/text2sql/namespaces/{ns}/schema/positions` | ERD 테이블 위치 일괄 저장 (pos_x/pos_y) — v2.6 신규 |
| `GET/POST/DELETE` | `/api/text2sql/namespaces/{ns}/relations/{id?}` | FK 관계 CRUD |
| `POST` | `/api/text2sql/namespaces/{ns}/relations/suggest-ai` | AI 관계 추천 (LLM이 컬럼명 패턴 분석, v2.10: 변경 테이블 대상으로만 제한하여 토큰 절약) — v2.6 신규 |
| `GET/POST/DELETE` | `/api/text2sql/namespaces/{ns}/synonyms/{id?}` | 용어사전 CRUD |
| `POST` | `/api/text2sql/namespaces/{ns}/synonyms/reindex` | 용어사전 벡터 재인덱싱 |
| `POST` | `/api/text2sql/namespaces/{ns}/synonyms/generate-ai` | AI 용어 자동생성 (30+ 항목, SQL 키워드 필터, v2.10: 변경 테이블 대상으로만 제한하여 토큰 절약) — v2.6 신규 |
| `GET/POST/DELETE` | `/api/text2sql/namespaces/{ns}/fewshots/{id?}` | 예제 Q&A CRUD |
| `POST` | `/api/text2sql/namespaces/{ns}/fewshots/reindex` | 예제 벡터 재인덱싱 |
| `POST` | `/api/text2sql/namespaces/{ns}/fewshots/generate-ai` | AI 예제 자동생성 (20+ QA 쌍) — v2.6 신규 |
| `GET` | `/api/text2sql/pipeline` | 파이프라인 단계 목록 |
| `PUT` | `/api/text2sql/pipeline/{id}/toggle` | 단계 활성/비활성 |
| `GET` | `/api/text2sql/namespaces/{ns}/audit-logs` | 쿼리 감사 로그 (페이지네이션) |
| `GET/DELETE` | `/api/text2sql/namespaces/{ns}/cache/{id?}` | 쿼리 결과 캐시 조회/삭제 |

### Teams 수집 (`/api/teams-collect`) — v2.15 신규

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/api/teams-collect/auth/status` | Teams 인증 상태 조회 (토큰 유효성 검증 포함, 60초 캐시) |
| `POST` | `/api/teams-collect/auth/tokens` | 데스크톱 헬퍼가 캡처한 IC3/CSA 토큰 수신 → 인메모리 저장 |
| `POST` | `/api/teams-collect/auth/logout` | 인메모리 토큰·캐시 삭제 |
| `GET` | `/api/teams-collect/helper/download` | OpsNavHelper.exe 바이너리 다운로드 |
| `GET` | `/api/teams-collect/chats` | 캡처된 채팅방 목록 반환 |
| `POST` | `/api/teams-collect/messages` | syncState 페이징으로 채팅방 메시지 조회 (캐시 우선, 부족 시 Teams API 추가 로드) |
| `POST` | `/api/knowledge/import/teams` | 선택 메시지 스레드 → ParsedDocument → 청킹 → 벌크 등록 |

**Teams 인증 흐름:**
```
웹 UI "Teams 로그인" 버튼 (opsnav://teams-login?api_url=...&jwt=... 링크)
  → 사용자 PC의 OpsNavHelper.exe 자동 실행 (opsnav:// URL 스킴 등록 필요)
  → Playwright로 Teams 웹 로그인 화면 띄움
  → 사용자가 로그인하면 네트워크 후킹으로 IC3 토큰 + 채팅방 목록 캡처
  → POST /api/teams-collect/auth/tokens (JWT 인증)
  → 백엔드 인메모리 스토어에 저장 (DB 미저장, 재시작 시 소멸)
  → 프론트 2초 폴링으로 자동 감지 → 채팅방 목록 표시
```

**스크립트 구성 (`scripts/`):**
| 파일 | 역할 |
|------|------|
| `teams_desktop_login.py` | Playwright Teams 로그인·IC3 토큰 캡처 핵심 로직 |
| `install_url_handler.py` | `opsnav://` 커스텀 URL 스킴 OS 등록 (Windows 레지스트리) |
| `opsnav_helper_entry.py` | PyInstaller exe 진입점 (install/run/uninstall 모드 분기) |
| `dist/OpsNavHelper.exe` | PyInstaller 빌드 산출물 (docker-compose가 `/app/helper_assets`로 마운트) |

### VOC 이메일 수집 (`/api/email-voc`) — v2.40 신규, v2.44~v2.46 개선

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `POST` | `/api/email-voc/test-analyze` | 텍스트 직접 입력으로 분류/심각도/오배치 판정 프롬프트 테스트 (Graph API 연동 전에도 검증 가능) |
| `POST` | `/api/email-voc/test-notify` | 임의 Teams 웹훅 URL로 실제 알림 발송 테스트 (Admin) |
| `GET/PUT` | `/api/email-voc/settings` | 폴링 정책(활성화 여부/주기/조회기간) 조회·변경 (PUT은 Admin) |
| `GET/POST/PUT/DELETE` | `/api/email-voc/routing[/{id}]` | 파트별 메일함 라우팅 CRUD — PUT/DELETE는 `namespace` 쿼리로 소유 네임스페이스까지 검증(크로스 네임스페이스 변조 방지) |
| `GET/PUT` | `/api/email-voc/graph-credentials` | Microsoft Graph API 자격증명(Application 권한) 상태 조회·등록 (Admin, Fernet 암호화 저장, client_secret은 응답에 미포함) |
| `GET/PUT/POST` | `/api/email-voc/delegated-auth/{status,config,start}` | Delegated Permission(Authorization Code Flow, PKCE) 로그인 상태 조회·앱정보 저장·로그인 시작 (Admin, v2.41 신규·v2.42 인증방식 교체) — Application 권한 승인 전 임시 대체 경로 |
| `GET` | `/api/email-voc/delegated-auth/callback` | Microsoft 로그인 완료 후 리다이렉트 콜백 (인증 불필요 — 브라우저가 직접 호출, MSAL의 PKCE/state 검증으로 CSRF 방어, v2.42 신규) |
| `POST` | `/api/email-voc/collect/run` | 관리자가 기간(from~to, 최대 90일) 지정해 즉시 수집+분석+Teams 발송 1회 실행 (Admin) |
| `GET` | `/api/email-voc/history` | 이메일 분석+알림 이력 조회 (원본 메일 정보 + 분류결과 + 발송 성공/실패) |
| `GET` | `/api/email-voc/scheduler-status` | 백그라운드 폴링 스케줄러 실시간 상태 (동작 중 여부, 마지막 사이클 결과, 다음 예상 실행시각) |
| `GET` | `/api/email-voc/poll-cycles` | 폴링 사이클(스케줄러 실행 회차) 이력 조회 |
| `GET` | `/api/email-voc/mail-folders?mailbox_upn=` | 지정 메일함의 Outlook 폴더 목록 조회(Graph API 실조회, Admin) — 라우팅 등록 시 "전체 메일함" 대신 특정 폴더로 범위를 좁힐 때 사용(v2.44) |

`GET /api/email-voc/history`는 `severity`/`status`/`mismatch_only`/`keyword` 쿼리 파라미터로 필터링 가능(v2.44, keyword는 제목/발신자/본문 `ILIKE` 검색).

**수집 흐름 요약**: 백그라운드 스케줄러(`asyncio.create_task`, lifespan 등록)가 `email_polling_interval_minutes` 주기로 활성 라우팅 메일함을 Graph API로 조회(`mail_folder_id` 지정 시 그 폴더만, v2.44) → `check_relevance()`로 등록된 지식과의 최고 유사도 계산(base_weight 랭킹 부스팅이 섞이지 않은 원점수 기준, v2.45) → `email_relevance_min_score`(기본 0.38, v2.45에 0.35에서 재조정) 미만이면 LLM 호출 없이 `skipped_relevance`로 기록 후 다음 메일로(v2.41) → 이상이면 기존 하이브리드 검색+LLM 파이프라인 재사용해 분류(system_error/user_mistake/uncertain)·심각도·오배치 판정(LLM 프롬프트에 넣기 직전 IP/이메일/전화번호는 마스킹 — 인하우스 LLM 게이트웨이가 이런 패턴 포함 시 응답을 통째로 거부하는 정책이 있어 대응, v2.46) → `source_message_id` UNIQUE 제약으로 중복 스킵(fetch 직후 배치 사전 체크로 이미 처리된 메일은 관련지식 검색·LLM 분석 자체를 건너뜀, v2.43) → 담당 파트 Teams 채널에 Workflows 웹훅으로 알림(제목/내용/해결방안/참고지식 근거 섹션 구조화, 심각도별 4단계 색상, v2.44 — 자동 전화는 없음, 온콜 담당자명만 멘션). `ops_email_analysis`/`ops_email_poll_cycle`은 30일 고정 보관정책으로 자동 정리됨.

**Graph API 토큰 조달 우선순위(v2.41)**: `pipeline.run_manual_collection()`이 ① 호출부가 넘긴 `access_token` → ② Application 권한 자격증명(`graph-credentials`) → ③ Delegated 로그인 세션(`delegated_auth`, 본인 메일함 한정) 순으로 시도. 셋 다 없으면 메일함별로 "자격증명 없음" 에러만 기록하고 다른 메일함은 계속 처리(전체 실패 처리 안 함). 스케줄러는 namespace 순회 전 credentials/access_token을 한 번만 해석해 `skip_credential_resolution=True`로 넘겨 매 namespace마다 재시도하지 않음. 로컬 검증은 `backend/scripts/email_voc_local_test.py`(Device Code Flow로 본인 메일함 대상 전체 파이프라인 실행) 참고.

상세 설계·의사결정 배경은 `docs/email-analysis-channel-plan.md` 참조.

### MCP 도구

| 메서드 | 경로 | 설명 |
|--------|------|------|
| `GET` | `/api/mcp-tools` | 네임스페이스 MCP 도구 목록 |
| `POST` | `/api/mcp-tools` | 도구 등록 |
| `PATCH` | `/api/mcp-tools/{id}` | 도구 수정 |
| `PATCH` | `/api/mcp-tools/{id}/toggle` | 도구 활성/비활성 토글 |
| `DELETE` | `/api/mcp-tools/{id}` | 도구 삭제 |
| `POST` | `/api/mcp-tools/{id}/test` | 도구 테스트 실행 |
| `POST` | `/api/mcp-tools/autocomplete` | 자연어 입력 → 도구 JSON 자동완성 |
| `GET` | `/api/mcp-tools/logs` | 도구 호출 감사 로그 조회 |

---

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
| `EMBEDDING_MODEL` | `paraphrase-multilingual-mpnet-base-v2` | 임베딩 모델명 |
| `VECTOR_DIM` | `768` | 벡터 차원 수 |
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
| `rag_knowledge` | `embedding VECTOR(768)` | 문서 내용 임베딩 → 질문과 코사인 유사도로 관련 문서 검색 |
| `rag_glossary` | `embedding VECTOR(768)` | 용어 설명 임베딩 → 질문과 비교해 표준 용어 자동 매핑 (유사도 0.5 이상만 사용) |
| `rag_fewshot` | `embedding VECTOR(768)` | 과거 질문 임베딩 → 유사 Q&A를 LLM 프롬프트에 few-shot 삽입 (유사도 0.6 이상) |
| `rag_conv_summary` | `embedding VECTOR(768)` | 과거 대화 요약 임베딩 → 현재 질문과 유사한 과거 맥락 Semantic Recall (유사도 0.45 이상) |

### 검색 점수 공식

```
final_score = (w_vec × v_score + w_kw × k_score) × (1 + base_weight)
               └벡터 유사도    └BM25 키워드 점수    └문서 자체 가중치
```

- **v_score**: 코사인 유사도 (0~1) — HNSW 인덱스로 근사 탐색
- **k_score**: `ts_rank` BM25 점수 — GIN 인덱스로 전문 검색
- **base_weight**: `ops_knowledge` 행(문서)에 직접 붙는 가중치. 👍 피드백 시 +0.1, 👎 시 -0.1 자동 조정

### 비벡터 주요 테이블

`ops_part`, `ops_user`, `ops_namespace`, `rag_knowledge_category`, `ops_feedback`, `ops_query_log`, `ops_conversation`, `ops_message`, `ops_mcp_tool`, `ops_mcp_tool_log`, `ops_prompt`, `ops_system_config`

전체 스키마 정의는 `docs/table-definition.md` 참조.
