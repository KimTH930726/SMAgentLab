# 사내 이메일 크롤링 기반 선제적 오류 분석 채널 — 기획/설계 문서

> 작성일: 2026-07-08 (Graph API 기술 조사 갱신: 2026-07-09 / Teams 발송 방식 기술 조사 추가: 2026-07-22)
> 현재 버전: v2.29 기준
> 상태: **Track A/B 모두 구현 완료** — Track B(M365 Graph API 실연동)는 2026-08-19 실 로그인·실
> 메일함 fetch까지 성공, 최신 진행상황·트러블슈팅은 `docs/tech/voc-email-handoff.md` 참고. 이 문서는
> 최초 설계/의사결정 기록으로 유지(§12, §13은 착수 시점 기준 스냅샷)
> 목적: 채팅과 별개의 새 "채널"로, 특정 기간 동안의 사내 이메일(공용 접수함)을 수집해 건별로 RAG+LLM 오류 분석을 수행하고 결과를 **Teams로 선제 통보**하는 기능의 실현 가능성과 단계별 실행 계획을 정리한다
> 발송 채널: **Teams로 확정** (2026-07-22) — 메일 발송은 후순위로 내림 (§8 참고)
> 우선순위: **시급** (2026-07-29) — 지식 현행화 자동화([knowledge-refresh-automation-plan.md](./knowledge-refresh-automation-plan.md))보다 먼저 진행
> 구조 재정리 (2026-07-29): 이 기능을 **1단계(메일 수집) → 2단계(RAG 지식 파이프라인) → 3단계(담당자 알림/에스컬레이션)**의 3단계 파이프라인으로 재정리한다. 이는 §5의 Phase 1~3(구현 롤아웃 순서)과는 다른 축이다 — Phase는 "얼마나 자동화할지"의 시간순 로드맵이고, 1~3단계는 "데이터가 어떤 순서로 처리되는지"의 파이프라인 구조다. 이번 갱신에서 **1단계와 3단계의 설계가 상대적으로 미흡해 보강**했다(§9, §10 신규 추가). 2단계는 §2에서 이미 확인한 대로 기존 RAG 파이프라인 재사용만으로 충분해 추가 설계가 필요 없다.
> Q6·Q7 결정 (2026-07-29): **온콜 자동 전화는 1차 범위에서 제외** — 심각도 "높음/긴급"은 Teams 긴급 멘션+강조 표시까지만 자동화하고, 실제 전화는 이를 본 담당자가 수동으로 건다(향후 고도화 대상, §10 참고). **담당자 메일함을 파트별로 물리적으로 분리**하는 방향으로 라우팅을 단순화 — LLM은 담당자 판단이 아니라 "메일함이 잘못됐는지" 오탐지용 보조 역할로 축소(§10 참고).
> 착수 전략 (2026-07-29): 조직 승인(Q1/Q2/Q10)이 병목이므로, **승인 없이 지금 구현 가능한 것과 승인을 기다려야 하는 것을 분리한 투트랙 전략**으로 진행한다 (§11 참고). 조직적 블로커는 지금 바로 요청부터 넣어 승인 대기 시간을 개발과 병렬로 흘려보낸다.

---

## 1. 배경 및 요구사항

현재 시스템은 **사용자가 채팅으로 질문 → RAG 근거 기반 답변**을 받는 pull 방식만 존재한다.

추가로 검토 중인 것은 push 방식의 새 채널이다:

1. 관리자가 **특정 기간**(예: 최근 1주)을 지정
2. 그 기간 동안의 **사내 이메일을 크롤링**
3. 이메일을 건별로 구분 — 대부분 시스템 오류 관련 문의(VOC 성격)로, 실제 원인이 **사용자 실수인지 시스템 문제인지** 판단이 필요
4. 각 이메일 내용에 대해 **RAG(지식 검색) + LLM**으로 원인 분석 및 (오류가 맞다면) 해결 방안을 도출
5. 분석 결과를 **Teams로 발송** — 선제 대응 (§8에서 방식 확정)

즉 "채팅 채널"과 별도로 "이메일 배치 분석 채널"을 새로 만드는 구조다.

---

## 2. 현재 자산 감사 — 재사용 가능한 것 vs 신규 구축 필요한 것

코드베이스 전수 조사 결과:

| 구성 요소 | 현황 | 재사용 가능 여부 |
|---|---|---|
| 이메일 읽기(크롤링) | 관련 코드/의존성 전무. **Microsoft Graph API로 신규 구축하기로 결정**(§7 참고) | ❌ 처음부터 구축 (단, 정식 Microsoft API라 경로는 확정) |
| 이메일 발송 | SMTP 등 발송 코드 없음 | ❌ 신규 구축 |
| Teams 발송 | 없음. 기존 "Teams 연동"(`backend/service/teams/`, `backend/agents/knowledge_rag/ingestion/teams_crawler.py`)은 OpsNavHelper.exe가 사용자의 실제 로그인 브라우저 세션에서 단명 토큰(IC3)을 캡처해 **읽기 전용으로 대화 이력을 크롤링**하는 구조. 서버 자체 서비스 계정이 없고(`teams/router.py`: "서버사이드 Playwright/subprocess는 사용하지 않는다"), 무인 자동 발송에 재사용 불가. **구체적 발송 방식은 §8에서 확정** — Workflows 웹훅 URL만 발급받으면 기존 `_execute_http_call`(MCP 도구 실행기)에 그대로 등록해 호출 가능 | ❌ 발송 채널 자체는 신규 구축(Teams "Workflows" 앱에서 채널 소유자가 웹훅 URL 발급) 필요하나, 발급 후 **호출 로직은 기존 MCP 실행기 재사용** 가능 |
| 배치/스케줄링 | cron·APScheduler·Celery 등 주기 실행 인프라 **전무**. `main.py`의 `lifespan`은 서버 기동 시 1회성 초기화(DB pool, 임베딩/리랭커 모델 로드, LLM health check)만 수행 | ❌ 신규 구축 필요 (단, "관리자가 기간 입력 후 수동으로 분석 시작 버튼 클릭" 방식이면 스케줄러 없이 v1 구현 가능) |
| 이메일 건별 RAG 분석 | `backend/agents/knowledge_rag/knowledge/retrieval.py`의 `search_knowledge()` + `backend/service/llm/base.py`의 `LLMProvider.generate(context, question, ...)` — 채팅 SSE/DB 스키마와 분리된 순수 비동기 함수라 "텍스트 입력 → 근거 기반 답변" 용도로 그대로 재사용 가능 | ✅ 거의 그대로 재사용 |
| 외부 API 호출 실행기 | `backend/agents/mcp_tool/agent.py`의 `_execute_http_call` — 관리자가 등록한 외부 HTTP API(메서드/URL/헤더/파라미터)를 호출하는 범용 실행기, 타임아웃/응답 크기 제한/감사 로그 포함. 현재는 LLM 도구 선택 흐름에서만 호출되지만 함수 자체는 독립 호출 가능 | ✅ 사내에 메일 발송 API나 Teams Webhook URL이 이미 존재한다면 이를 MCP 도구처럼 등록해 배치 잡에서 직접 호출하는 방식으로 재사용 가능 |

**결론**: "이메일을 분석하는" 핵심 로직은 기존 RAG 파이프라인을 그대로 타면 되므로 기술적으로 제일 쉬운 부분이다. 반면 "이메일을 읽는 것"과 "결과를 내보내는 것"은 완전히 새로운 외부 연동이며, 이 두 가지는 **사내 인프라/보안 정책에 의존**하므로 코드 작업보다 조직적 확인이 선행되어야 한다.

---

## 3. 미해결 질문 (구현 착수 전 확인 필요)

| # | 질문 | 왜 중요한가 |
|---|------|------|
| Q1 | 읽을 메일함이 **개인 메일함**인가 **공용 접수함(alias)**인가? | Graph API 애플리케이션 권한 + Application Access Policy로 공용 접수함 하나만 최소 권한으로 스코프 지정 가능 — 최종 대상 메일함 확정 필요 |
| Q2 | 사내 메일 시스템이 **Exchange Online(M365)** 인가 **온프레미스 Exchange**인가? | **M365로 가정하고 Microsoft Graph API로 확정**(§7). 온프레미스라면 별도 검토 필요 — 확인 필요 |
| Q3 | ~~발송 채널로 어떤 게 이미 사내에 구축돼 있는가?~~ → **해결(§8)**: 옛 Incoming Webhook(Office 365 Connectors)은 이미 폐지되어 사용 불가. **Teams "Workflows" 앱의 웹훅 템플릿**으로 대체 확정 — IT팀 승인 없이 대상 채널 소유자가 직접 발급 가능 | Workflows 웹훅은 발급 후 기존 MCP 도구 실행기(`_execute_http_call`)로 그대로 호출 가능해 개발 범위가 크게 줄어듦 |
| Q4 | 분석 결과를 보낼 **대상 Teams 팀/채널**은 어디인가? 담당 파트별로 채널을 나눌 것인가, 단일 알림 채널로 모을 것인가? 요약만 보낼지 근거 지식·원문 링크까지 포함할지? | 채널마다 별도 웹훅 URL이 발급되므로, 파트별 분리 여부에 따라 웹훅 URL을 몇 개 관리해야 하는지가 정해짐. 형식(요약 vs 원문)은 메시지 payload 설계에 직결 |
| Q5 | 오분류(사용자 실수를 시스템 오류로 잘못 판단 등) 시 리스크 허용 수준은? | 자동 발송 범위(전체 자동 vs 사람 확인 후 발송)를 결정하는 핵심 기준 |
| Q6 | ~~온콜(전화) 에스컬레이션에 이미 구축된 사내 시스템(PagerDuty류)이 있는가?~~ → **해결(2026-07-29)**: 1차 범위에서 자동 전화 자체를 제외. 심각도 "높음/긴급"은 Teams 긴급 멘션+강조 표시까지만 자동화, 실제 전화는 담당자가 수동으로 건다 | 코드베이스에 온콜/전화 발신 연동이 0건이라 신규 도입 비용·승인 부담이 컸음 — 범위를 좁혀 Phase 3 착수 조건에서 제거. 실제 온콜 시스템 연동은 §10 "향후 고도화"로 이동 |
| Q7 | ~~공용 접수함이 파트별로 분리되어 있는가?~~ → **해결(2026-07-29)**: 접수함을 **파트별로 물리적으로 분리**하는 방향으로 채택(예: voc-billing@, voc-network@) | 라우팅이 100% 결정론적이 됨 — LLM이 담당자를 추측할 필요가 없어져 §10 설계가 단순해짐. 대신 §7의 RBAC 스코프를 메일함 1개가 아니라 **파트 수만큼** 설정해야 해 Q10 IT 승인 요청 범위가 넓어짐(§7 참고) |

---

## 4. 초안 아키텍처 (Phase 1 기준)

```
[관리자 어드민 화면]
  기간 선택 (from ~ to) → "분석 시작" 클릭
        │
        ▼
[이메일 수집기 — 신규]
  Microsoft Graph API (애플리케이션 권한)로 지정 기간 이메일 조회
  건별로 제목/본문 전체/발신자/수신시각 파싱
        │
        ▼
[건별 RAG 분석 — 기존 파이프라인 재사용]
  각 이메일 본문을 질문처럼 취급
    → retrieval.search_knowledge(namespace, embedding)  (기존 함수)
    → LLMProvider.generate(context=검색결과, question=이메일 본문)  (기존 함수)
    → 분류: 시스템 오류 / 사용자 실수 / 판단 보류
    → 오류로 판단 시 해결 방안 초안 생성
        │
        ▼
[결과 저장 — 신규 테이블]
  이메일별 원문 + 분류 + 근거 지식 + 해결 방안 초안을 DB에 저장
        │
        ▼
[어드민 결과 화면 — 신규 UI]
  분석 결과 리스트 (건별 카드/테이블)
  Phase 1: 여기서 종료 — 발송 없음, 사람이 눈으로 확인
  Phase 2: 관리자가 "발송" 버튼 클릭 → Teams Workflows 웹훅 호출(MCP 실행기 재사용)
  Phase 3: 확신도 높은 케이스 자동 발송 + 스케줄러로 주기 실행 (필요 시 Teams Bot으로 전환)
```

---

## 5. 단계별 실행 계획

### Phase 1 — 수집 + 분석 (발송 없음, 사람 검토 전제)
- 목표: 새 인프라(스케줄러/발송 채널) 없이, 기존 RAG 파이프라인 재사용만으로 "이메일 건별 분석 결과를 어드민 화면에서 확인"할 수 있게 한다.
- 범위 (1단계 + 2단계, 3단계는 화면 표시만):
  - **1단계**: 이메일 수집기 신규 구현 (Microsoft Graph API, 애플리케이션 권한 방식) — 우선 §9의 **수동 1회성 실행**(관리자가 from~to 지정)만 구현, 폴링 자동화는 Phase 2로 미룸
  - **2단계**: 이메일 건별 분석 배치 함수 (`search_knowledge` + `generate` 재사용), §10의 RAG 사전 필터 + LLM 분류·심각도 판단 로직 포함
  - 결과 저장용 신규 테이블 (예: `ops_email_analysis` — 원문, 분류, 심각도, 근거 지식 ID, 해결 방안, 상태, `source_message_id UNIQUE`(§9 중복 방지 키))
  - 관리자 화면: 기간 입력 → 수동 트리거 → 결과 리스트/상세 모달 (담당자·심각도 표시는 여기서부터, 발송은 아직 없음)
- 이 단계는 Q4~Q7(발송 대상·정책·라우팅)이 확정되지 않아도 착수 가능

### Phase 2 — 반자동 발송
- 목표: 분석 결과 중 관리자가 확인 후 승인한 건만 실제로 Teams로 발송, **1단계 폴링 자동화**도 이 단계에서 함께 켠다
- 전제조건: Q4(대상 채널·형식) 확정. Q3는 §8에서 해결(Workflows 웹훅). Q7(접수함 파트별 분리)은 이미 결정됐으므로(§3) 착수 조건 아님 — 실제로는 각 파트 메일함의 RBAC 승인(§7 Q10) 완료 여부가 전제조건
- 범위 (1단계 + 3단계):
  - **1단계**: §9의 폴링 자동화 적용 — `email_collection_enabled`/`email_polling_interval_minutes`/`email_lookback_days`/`email_target_mailboxes` 관리자 설정 화면 추가, 재조회+중복방지 키 기반 폴링 배치를 **파트별 메일함마다** 구현
  - **3단계**: 대상 채널에서 Workflows 웹훅 URL 발급(수동, 1회성) → `ops_voc_routing` 매핑 테이블 구축(§10, 메일함↔파트↔웹훅 1:1) → 웹훅 URL을 MCP 도구로 등록 → 기존 `_execute_http_call` 재사용해 승인된 건만 POST 호출, 어드민 화면에 "발송" 액션 추가. 심각도 "높음/긴급"의 긴급 멘션+강조 표시 포맷도 이 단계에서 함께 구현(온콜 자동 전화는 범위 밖 — §10 참고)

### Phase 3 — 완전 자동화
- 목표: 확신도 높은 케이스에 한해 사람 개입 없이 Teams 알림까지 자동 발송(온콜 전화는 이번 범위에서 항상 사람이 수동으로 건다 — §10 결정)
- 전제조건: Phase 2 운영 데이터로 오분류율 측정 후 허용 가능한 임계값 확보 (Q5)
- 범위 (3단계 완성):
  - §10의 심각도 기반 채널 분기 완전 자동화 — 확신도 높은 "낮음/보통" 심각도는 일반 Teams 알림 자동 발송, "높음/긴급"은 긴급 멘션+강조 표시로 자동 발송(둘 다 시스템이 전화를 걸지는 않음)
  - 오배치 감지(§10 `mismatch_flagged`)도 이 단계에서 활성화 — 자동 재라우팅은 하지 않고 리뷰 큐에 표시만
  - 배치 스케줄러는 §9에서 이미 도입했으므로 재사용, APScheduler 등 신규 인프라 불필요

---

## 6. 참고 — 기존 Human-in-the-loop 원칙과의 정합성

이 시스템은 지식 등록 시에도 "LLM이 청킹/카테고리를 제안하되 최종 확정은 사람이 한다"는 원칙을 일관되게 적용하고 있다(`docs/architecture.md` 참고). 이메일 분석 결과를 검증 없이 즉시 조직 전체에 발송하는 것은 이 원칙과 배치되므로, Phase 1~2에서 사람 확인 단계를 두는 것을 권장한다.

---

## 7. 이메일 접근 방식 결정 — Microsoft Graph API (2026-07-08, 기술 조사 2026-07-09 갱신)

> blossomai(사내 AI 업무비서)가 이메일+AI요약 기능을 이미 갖고 있다는 걸 우연히 확인했으나,
> 이는 **비공식 내부 엔드포인트**(브라우저 개발자도구로 관찰, 공식 문서/계약 없음)라
> 채택 대상에서 제외함. 대신 Microsoft가 공식 지원하는 **Microsoft Graph API**를
> 이메일 읽기의 기본 경로로 확정한다. (다른 경로로 "Graph API로 되더라"는 이야기를
> 추가로 확인해 아래 조사로 세부 사항을 보강함 — 여전히 Q1/Q2/Q10 조직적 확인은 미완료.)

### 선택 이유

| 요구사항 | Graph API로 해결되는가 |
|---|---|
| 본문 전체 필요 (요약/미리보기 아님) | ✅ `message.body` 필드로 전체 제공. 단 **`Mail.ReadBasic`/`Mail.ReadBasic.All` 권한은 본문을 제외**하므로 반드시 `Mail.Read` 이상이 필요(공식 문서로 확인) |
| 공용 접수함(장애 VOC함) 접근 | ✅ 애플리케이션 권한(`Mail.Read`, client_credentials 인증)으로 사람 로그인 없이 접근. 최소 권한 스코핑 방식은 아래 "권한 스코핑 방식" 참고 |
| 완전 무인 배치 실행 (Phase 3) | ✅ 사람 세션 불필요 — 스케줄러가 임의 시각에 client_credentials로 호출 가능 |
| 기간 필터링 | ✅ OData `$filter=receivedDateTime ge ... and receivedDateTime le ...` |

**EWS(Exchange Web Services)는 사용하지 않음** — Microsoft가 Exchange Online용 EWS를 단계적으로 폐지 중이라 신규 도입 시 권장되지 않음. IMAP도 기술적으로 가능하지만 Modern Auth 강제 정책 때문에 Graph API보다 다루기 번거로워 후순위.

### 7.1 기술 조사 상세

**엔드포인트 / 권한**
- 대상 메일함의 메시지 목록: `GET /users/{공용메일함 UPN 또는 id}/messages` (개인 메일함 전용인 `/me/messages`가 아니라 이 형태를 사용 — 애플리케이션 권한으로 타 메일함 접근 시 필수 형태)
- 필요 권한(애플리케이션): 최소 권한은 `Mail.ReadBasic.All`이지만 **본문이 빠지므로 이 프로젝트 요구사항(본문 전체로 RAG 분석)에는 `Mail.Read`가 필요** — "최소 권한 원칙"은 개별 메일함으로 범위를 좁히는 쪽으로 충족(아래 참고)
- 기간 필터: `?$filter=receivedDateTime ge 2026-07-01T00:00:00Z and receivedDateTime le 2026-07-08T00:00:00Z`
- 본문을 HTML 대신 텍스트로 받고 싶으면 요청 헤더 `Prefer: outlook.body-content-type="text"` 추가 (기존 웹 크롤러의 BeautifulSoup HTML 파싱을 재사용하지 않아도 됨)
- 페이지네이션: 기본 10건, `$top`으로 최대 1000건까지 조정 가능하나 `$select`로 필요한 필드만 받지 않으면 대량 조회 시 504(Gateway Timeout) 위험 — 응답의 `@odata.nextLink`를 그대로 따라가며 반복 조회

**권한 스코핑 방식 — 기존 계획 문서의 "Application Access Policy" 정정**
- 기존 §7에 적었던 `New-ApplicationAccessPolicy` 기반 Application Access Policy는 **Microsoft가 신규 생성을 권장하지 않는 legacy 방식**으로 확인됨(공식 문서: "Don't create new App Access Policies"). 후속 도입된 **RBAC for Applications**로 대체됨.
- RBAC for Applications 방식 절차(Exchange Online PowerShell, IT팀 작업):
  1. Entra ID 앱 등록 후 `New-ServicePrincipal -AppId <클라이언트ID> -ObjectId <서비스주체ID>`로 Exchange에 포인터 등록
  2. `New-ManagementRoleAssignment -App <서비스주체> -Role "Application Mail.Read" -CustomResourceScope <메일함 필터>` 로 해당 앱이 **지정한 공용 메일함에서만** Mail.Read를 쓸 수 있도록 스코프 제한
  3. `Test-ServicePrincipalAuthorization`으로 스코프가 의도대로 걸렸는지 사전 검증 가능
  4. 주의: Entra ID(Azure AD) 쪽에서 조직 전체 범위로 `Mail.Read`를 동의(consent)해버리면 RBAC 스코프와 무관하게 전체 메일함 접근이 합집합으로 허용되므로, **Entra ID 동의는 그대로 두고 Exchange RBAC만 추가하는 방식이 아니라 최종적으로 Entra 동의 범위 자체를 이 앱 용도로만 좁혀 관리해야 함** — IT팀 승인 요청(Q10) 시 이 조합을 함께 명시해야 함
  5. **메일함 분리 결정(§3 Q7) 반영**: 2단계(`New-ManagementRoleAssignment`)를 파트별 메일함마다 반복 실행 — 앱 등록(1단계)은 1회로 공용, RBAC 스코프만 메일함 수만큼 늘어남. 파트가 추가될 때마다 IT팀에 동일 절차 재요청이 필요하므로, 관리자 화면(§9)에서 "추적 대상 메일함 목록"을 UI로 관리해 어떤 메일함이 이미 RBAC 승인됐는지 추적하는 것을 권장
- 결론: Q10 승인 요청 항목에 "Azure AD 앱 등록 + `Mail.Read` 권한 동의"뿐 아니라 **"Exchange Online RBAC for Applications로 특정 공용 메일함에 한정하는 설정"**까지 포함해서 IT/보안팀에 요청해야 함 (이 작업 자체는 IT팀의 Exchange 관리자 권한이 필요해 우리 쪽에서 대신 실행 불가).

**구현 방식 후보 (Phase 1 착수 시)**
- 인증: `msal` 패키지의 `ConfidentialClientApplication.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])`로 client_credentials 토큰 발급 — 이미 DevX LLM OAuth2 연동에서 유사한 client_credentials 패턴을 쓰고 있어 구조적으로 익숙함
- 호출: 공식 `msgraph-sdk` 대신 기존 `web_crawler.py`(Confluence REST API 연동)와 동일하게 **`httpx`로 REST 엔드포인트 직접 호출**하는 방향을 권장 — 무거운 SDK 의존성 추가 없이 프로젝트 기존 스타일과 일관성 유지

### 남은 선행 조건

| # | 확인/조치 사항 | 비고 |
|---|---|---|
| Q2 | 사내 메일 시스템이 실제로 **M365/Exchange Online**인지 확인 | Graph API는 M365 전제. 온프레미스면 재검토 필요 |
| Q1 | 분석 대상 공용 메일함(alias) 확정 — **§3 Q7 결정(2026-07-29)에 따라 파트별로 N개** (예: voc-billing@, voc-network@ ...) | RBAC for Applications의 `CustomResourceScope`를 메일함마다 반복 지정해야 함(아래 참고) |
| Q10 | Azure AD(Entra ID) **앱 등록 + `Mail.Read` 애플리케이션 권한** 동의 + **Exchange Online RBAC for Applications로 파트별 공용 메일함 각각으로 스코프 제한**까지 포함한 IT/보안팀 승인 | 정식 Microsoft 지원 경로라 blossomai 건보다 승인 수월할 것으로 예상되나, 여전히 조직적 절차 필요 — client_id/secret/tenant_id 발급 + **메일함 수만큼의 RBAC 스코프 설정**이 이 승인에 포함됨(메일함 분리 결정으로 단일 메일함 대비 승인 범위가 넓어짐) |

Q1·Q2·Q10이 확정되면 §5 Phase 1의 "이메일 수집기" 구현에 바로 착수 가능. 나머지(Q4·Q5, 발송 대상·정책)는 여전히 Phase 2 진입 전 확인 사항으로 유지.

---

## 8. 결과 발송 방식 결정 — Teams (2026-07-22)

### 배경 — 기존 계획(Incoming Webhook)이 이미 무효화됨

§2/§3 초안 작성 시점(2026-07-08)에는 "Teams Incoming Webhook 신규 등록"을 발송 채널 후보로 적어뒀으나, 이는 더 이상 유효하지 않다:

- Microsoft는 Teams용 Office 365 Connectors(Incoming Webhook 포함)를 단계적으로 폐지해왔고, 최종 비활성화 시점이 **2026-05-18~22**로 확정되어 있었다. 오늘(2026-07-22) 기준 이미 지난 시점이라, **옛 방식의 "채널 커넥터 → Incoming Webhook URL 발급" 경로는 현재 사용 불가능하다고 봐야 한다.**
- Microsoft의 공식 대체 경로는 **Teams "Workflows" 앱**(Power Automate 기반)이다.

### 발송 방식 후보 비교

| 방식 | 무인(스케줄러) 발송 가능 여부 | 구축 주체 / 승인 필요 여부 | 비고 |
|---|---|---|---|
| ~~Incoming Webhook (Office 365 Connector)~~ | - | - | **폐지됨 — 채택 불가** |
| **Workflows 웹훅** ("~webhook request is received" 템플릿) | ✅ 발급 후에는 순수 HTTP POST — 스케줄러가 무인 호출 가능 | 대상 채널의 소유자/멤버가 Teams UI에서 직접 발급 (IT/Azure AD 승인 불요로 파악됨) | Phase 1~2에 권장. §2의 기존 MCP 도구 실행기(`_execute_http_call`)로 바로 호출 가능 |
| Graph API `ChannelMessage.Send` (앱 전용 토큰) | ❌ **불가** — 이 권한은 위임 권한(로그인 사용자 컨텍스트) 전용이며, 애플리케이션 권한으로는 지원되지 않음(공식 확인) | - | Q1~Q2/Q10처럼 client_credentials로 무인 처리하려던 원래 구상과 충돌 — 채택 불가 |
| Teams Bot (Azure Bot Framework 앱 등록) | ✅ 봇 앱을 대상 팀에 설치하면 사람 로그인 없이 proactive message 발송 가능 | Azure Bot 리소스 등록 + 앱 카탈로그 설치 + (조직에 따라) 관리자 승인 필요 — Workflows 웹훅보다 구축 비용 큼 | Phase 3 이후, 채널 수가 늘거나 Adaptive Card 승인 버튼 등 상호작용이 필요해지면 재검토 |

### 선택 — Phase 1~2: Workflows 웹훅

- 이유: 승인 절차 없이(§7의 Q10처럼 IT/보안팀 승인이 필요한 이메일 읽기 경로와 대비됨) 대상 채널 소유자가 몇 분 안에 웹훅 URL을 발급할 수 있고, 발급된 URL은 순수 HTTP 엔드포인트라 **§2에서 이미 확인한 기존 MCP 도구 실행기(`backend/agents/mcp_tool/agent.py`의 `_execute_http_call`)에 그대로 등록해 재사용** 가능하다. 신규 발송 모듈을 처음부터 만들 필요가 없다.
- 발급 절차(Phase 2 착수 시, 코드 작업 아님 — 대상 채널 소유자가 수행):
  1. 대상 Teams 채널에서 "More options" → "Workflows" 선택
  2. "Post to a channel when a webhook request is received" 템플릿 선택 (조직 유형에 따라 이 템플릿이 안 보일 수 있음 — 이 경우 IT 문의 필요)
  3. 게시 대상 팀/채널 지정 후 웹훅 URL 발급
  4. 발급된 URL을 이 프로젝트의 MCP 도구(HTTP 도구)로 등록 — namespace/파트별로 여러 채널에 보내야 하면 채널별로 반복
- 확인이 더 필요한 부분(구현 착수 시 실증 필요, 이 시점엔 문서만으로 확정 불가):
  - Workflows 웹훅이 기대하는 JSON 요청 본문 스키마 — 트리거 생성 시 커스텀 스키마를 지정할 수 있는 것으로 보이나, Adaptive Card 형태로 "분류/근거 지식/해결 방안"을 구조화해서 보낼 수 있는지는 실제 발급 후 확인 필요
  - 조직에 Power Automate 라이선스/정책상 제약이 있는지 (일반 라이선스로 가능하다는 문서상 근거는 있으나 사내 정책은 별도 확인 필요)

### Phase 3+ 확장 시 재검토 — Teams Bot

담당 파트가 늘어나 채널이 많아지거나, 대시보드의 기존 Human-in-the-loop 패턴(예: MCP 도구 호출 전 "승인 카드" 표시 후 사용자가 승인)처럼 **Teams 메시지 안에서 바로 승인/반려 버튼을 누르게 하고 싶다면**, 이 시점에는 Workflows 웹훅(단방향 POST)로는 부족하고 Teams Bot(Bot Framework) 등록이 필요하다. Phase 1~2 운영 경험을 쌓은 뒤 Phase 3 설계 시점에 재검토 대상으로 남겨둔다.

---

## 9. 1단계 — 메일 수집 정책 (관리자 설정, 2026-07-29 추가)

### 배경

§5 Phase 1은 원래 "관리자가 기간을 입력하고 수동으로 분석 시작 버튼을 클릭"하는 1회성 트리거만 전제했다. 여기에 **주기적 폴링 자동화**를 추가하려면, 폴링 간격·조회 범위·on/off 여부를 관리자가 조정할 수 있어야 한다. [knowledge-refresh-automation-plan.md §5 Phase 1](./knowledge-refresh-automation-plan.md)에서 이미 같은 문제(대량 변경 임계값)를 "하드코딩 대신 관리자 설정 화면"으로 푼 전례가 있어 동일 패턴(`core/config.py`의 `get_thresholds`/`set_thresholds`류, 관리자 전용 PUT 엔드포인트)을 재사용한다.

### 설정 항목

| 설정 키 | 기본값 | 설명 |
|---|---|---|
| `email_collection_enabled` | `false` | 폴링 자동화 on/off. 꺼져 있으면 수동 실행만 가능 |
| `email_polling_interval_minutes` | `5` | 폴링 주기. **하한 1분** 강제(Graph API 호출 한도 보호) |
| `email_lookback_days` | `7` | 매 폴링마다 "현재 - N일" 범위를 다시 조회하는 재조회 윈도우 — 단순 델타(마지막 폴링 이후)가 아니라 **겹치는 범위를 반복 조회**하는 방식으로 설계 권장(아래 근거 참고) |
| `email_target_mailboxes` | (없음, 필수 입력) | **파트별 대상 공용 접수함 UPN 목록**(§3 Q7 결정 반영 — 메일함 1개가 아니라 파트 수만큼 등록). 각 항목은 §10의 `ops_voc_routing`에서 담당 파트와 1:1로 연결되며, 어느 메일함이 RBAC 승인 완료됐는지도 이 화면에서 함께 추적(§7.1 참고) |

**재조회(overlap) 방식을 권장하는 이유**: 5분 간격 델타 폴링만 쓰면 서버 재시작·API 일시 장애로 한 사이클이 유실될 경우 그 구간의 메일을 영영 놓친다. `knowledge-refresh-automation-plan.md`에서 도입한 "웹훅 + 폴링 정합성 백업" 하이브리드와 같은 원리로, 매번 최근 N일을 다시 훑고 이미 처리한 메일은 중복 처리 방지 키로 걸러내는 편이 훨씬 견고하다.

### 추가로 필요한 것 (권장)

- **중복 처리 방지 키**: 재조회 윈도우가 겹치므로, Graph API `message.id`를 신규 테이블(예: `ops_email_analysis.source_message_id UNIQUE`)에 저장해 이미 분석한 메일을 재분석하지 않도록 한다. §4 초안 아키텍처의 "결과 저장 — 신규 테이블"에 이 컬럼을 포함시킨다.
- **수동 1회성 실행**: 관리자가 임의의 from~to 날짜를 지정해 즉시 실행하는 기능. §5 Phase 1에서 원래 계획했던 기능과 동일하며, 폴링 자동화 이전에 먼저 구현 가능(선행 개발 대상).
- **폴링 상태 가시성**: 마지막 폴링 성공 시각·처리 건수·최근 에러 메시지를 보여주는 관리자 화면. on/off 토글만 있으면 "조용히 꺼진 채 방치"되는 리스크가 있어 필수로 권장.
- **재시도/백오프 정책**: Graph API 일시 장애(5xx, 429) 시 해당 사이클만 skip하고 다음 사이클에 재시도(§10과 마찬가지로 배치를 죽이지 않는 원칙 적용).

---

## 10. 3단계 — 담당자 라우팅 및 심각도 기반 알림 체계 (2026-07-29 작성, 2026-07-29 Q6·Q7 결정 반영)

### 배경

기존 §4 아키텍처는 "분석 결과를 Teams로 발송"까지만 다뤘고, **어느 채널의 누구에게** 보낼지는 미설계 상태였다. 이 프로젝트의 핵심 목표는 "AI가 사람보다 먼저 VOC를 확인하고, 심각도를 판단해 적절한 채널로 선제 통보"하는 것이므로, 이 라우팅 체계가 실질적으로 이 기능의 핵심이다. §3 Q6·Q7 결정에 따라 아래처럼 범위를 단순화한다.

### 담당자 라우팅 — 메일함 분리를 1차 라우팅 키로 채택 (§3 Q7 결정)

애초 검토했던 "수신주소 기반 vs LLM 판단" 하이브리드 대신, **파트별로 접수함 자체를 물리적으로 분리**하는 쪽으로 결정했다(예: voc-billing@ → 결제팀 메일함, voc-network@ → 네트워크팀 메일함). §1 이메일 수집기가 어느 메일함에서 가져왔는지를 그대로 담당 파트로 매핑하므로:

- **라우팅에 LLM 판단이 필요 없다** — 100% 결정론적, §2에서 우려했던 "정확도가 생명"인 담당자 분류 문제가 애초에 사라진다
- 대신 LLM은 **오배치 감지(보조 역할)** 로 축소한다: VOC 본문 내용이 수신 메일함의 담당 영역과 명백히 다르면(예: 결제팀 메일함으로 왔는데 내용은 네트워크 장애) `mismatch_flagged=true`로 표시만 하고 **자동으로 재라우팅하지 않는다** — 사람이 검토 후 수동으로 옮긴다(§6 Human-in-the-loop 원칙 적용). 이 판정은 2단계 LLM 분석에 곁가지로 붙는 항목이라 별도 LLM 호출을 추가할 필요는 없다(같은 호출에서 "분류/심각도"와 함께 산출)
- **트레이드오프**: 사람이 애초에 VOC를 엉뚱한 메일함으로 보낸 경우 완전 자동 정정은 안 되지만(위 오배치 플래그로 사람이 잡음), 대신 IT 승인 부담이 늘어난다(§7 Q1/Q10 — RBAC 스코프를 메일함 수만큼 반복 설정)

### 담당자 매핑 테이블 (신규)

`ops_voc_routing` (namespace_id, part, mailbox_upn, teams_webhook_url, oncall_contact_name, oncall_contact_phone) — §7의 `email_target_mailboxes`(§9)와 1:1로 연결되는 파트별 라우팅 정보. `oncall_contact_*`는 **자동 발신에는 쓰이지 않고**, 아래 알림 메시지에 "누구에게 전화하면 되는지"를 사람이 보도록 표시하는 용도다. 관리자 화면에서 파트 추가/메일함 등록/웹훅 URL 등록/온콜 연락처 등록을 지원한다.

### 심각도 기반 알림 — 온콜 자동 전화는 1차 범위에서 제외 (§3 Q6 결정)

```
[LLM 분류 결과] (2단계에서 산출, §10 전용 추가 호출 없음)
  분류: 시스템 오류 / 사용자 실수 / 판단 보류
  + 심각도: 낮음 / 보통 / 높음 / 긴급
  + 오배치 여부: mismatch_flagged (위 라우팅 절 참고)
        │
        ▼
  낮음/보통 → 담당 파트 Teams 채널로 일반 알림 (§8 Workflows 웹훅)
  높음/긴급 → 담당 파트 Teams 채널로 **긴급 멘션 + 강조 표시**(예: Adaptive Card 빨간 배경 + 온콜 담당자 @멘션)
              실제 전화는 이 알림을 본 담당자가 **수동으로** 건다 — 시스템은 전화를 걸지 않는다
```

이렇게 하면 §3 Q6에서 우려했던 "온콜 전화 오탐 시 사람을 잘못 깨우는 리스크"가 구조적으로 사라진다 — 최종 판단(전화를 걸지 말지)은 항상 사람이 하고, 시스템은 "먼저 확인하고 눈에 띄게 알려주는" 역할까지만 맡는다. §6의 Human-in-the-loop 원칙과도 자연스럽게 정합한다.

### 향후 고도화 — 온콜 자동 에스컬레이션 (범위 밖, 참고용)

Phase 3 이후 운영 데이터가 쌓이고 "높음/긴급" Teams 알림에 대한 담당자 반응 속도가 충분히 빠르다는 게 검증되면, 사내에 당직/인시던트 관리 시스템(PagerDuty/Opsgenie류 또는 자체 시스템)이 있는지 확인해 API 연동을 재검토할 수 있다. 없다면 Twilio 같은 외부 전화/SMS API 신규 도입이 필요하며, 이는 보안정책 승인·비용이 수반되는 별도 기획으로 분리한다. 이번 설계 범위에는 포함하지 않는다.

### 2단계와의 경계 — "문제로 볼 메일"을 거르는 방식

2단계(RAG 지식 파이프라인)에서 모든 메일에 LLM을 태우면 비용이 크므로, RAG 유사도로 명백히 무관한 메일(지식베이스와 전혀 안 겹치는 것)을 먼저 저비용으로 걸러내고, 남은 메일만 LLM이 분류+심각도+오배치 여부까지 함께 판단하는 **2단 필터(저비용 사전 필터 → LLM 정밀 판단)** 구조를 권장한다. 심각도 판단 자체가 LLM을 반드시 거쳐야 하므로, RAG 유사도 단독으로는 3단계에 필요한 정보를 만들 수 없다.

---

## 11. 구현 순서 재정렬 — 투트랙 전략 (2026-07-29)

### 배경

§7/§8에서 확인된 조직적 블로커(Q1/Q2/Q10 — Graph API 접근 승인)는 IT/보안팀 승인 절차가 필요해 리드타임을 예측하기 어렵다. 반면 §2 자산 감사에서 이미 확인했듯 2단계(RAG 분석)·3단계(알림 발송) 로직의 상당 부분은 실제 이메일 데이터 없이도 구현·테스트가 가능하다. 이를 활용해 **Track A(조직 승인 없이 지금 구현)**와 **Track B(조직 승인, 병렬 진행)**로 나눠 동시에 진행한다.

이는 §5의 Phase 1~3, §1의 1~3단계와 또 다른 축이다 — Phase/단계가 "무엇을, 얼마나 자동화하는지"를 나눈다면, Track A/B는 "Phase 1 안에서도 승인 없이 먼저 만들 수 있는 부분이 무엇인지"를 가른다.

### Track B — 지금 바로 요청부터 넣는다 (승인 대기가 병목이므로 최우선 착수)

| # | 항목 | 비고 |
|---|---|---|
| Q1 | 파트별 대상 공용 메일함 확정 | 각 파트에 "VOC 메일함이 이미 있는지/분리 가능한지" 확인 요청 |
| Q2 | M365(Exchange Online) 여부 확인 | IT팀에 1회 문의로 해결 가능, 리드타임 짧음 |
| Q10 | Azure AD 앱 등록 + `Mail.Read` 권한 + RBAC 스코프 IT/보안팀 승인 | **리드타임이 가장 긴 항목 — Track A 개발 완료를 기다리지 말고 오늘 요청부터 넣어야 시점이 맞는다** |

이 세 가지는 Track A 코드 완성 여부와 무관하게 지금 바로 요청을 시작한다. 승인이 몇 주 걸릴 수 있는데, Track A 개발이 끝난 뒤에야 요청을 넣으면 그만큼 전체 일정이 그대로 밀린다.

### Track A — 조직 승인 없이 지금 구현 가능 (구현 순서)

1. **DB 스키마**: `ops_email_analysis`(§5 Phase 1, `source_message_id UNIQUE` 포함), `ops_voc_routing`(§10) 테이블 생성 — 실제 메일함 값이 없어도 스키마는 확정 가능
2. **2단계 분석 로직**: `search_knowledge` + `generate` 재사용해 "이메일 본문 텍스트 → 분류/심각도/오배치 여부" 판정 함수 구현. 실제 Graph API 없이 **수동 붙여넣기 텍스트**로 프롬프트 개발·튜닝이 가능하므로, 관리자 화면에 "테스트 실행: 텍스트 직접 입력" 진입점을 만들어두면 개발 중 반복 테스트뿐 아니라 나중에 §5 Phase 1의 수동 실행 기능으로도 그대로 쓸 수 있다
3. **3단계 발송 로직**: `_execute_http_call` 재사용한 Teams POST 연동 + Adaptive Card 포맷(일반/긴급 멘션+강조, §10) 구현. **테스트용 Teams 채널**(아무 채널이나)에서 Workflows 웹훅을 즉시 발급받아(§8 — 채널 소유자면 누구나 가능, IT 승인 불요) 실제 POST까지 검증 가능 — Q4(공식 파트별 채널)가 확정되면 웹훅 URL만 교체하면 된다
4. **관리자 화면**: §9 폴링 설정(주기/기간/on-off) + §10 라우팅 매핑 UI 구현 — 메일함 UPN 입력란은 미리 만들어두고, Q1이 확정되는 대로 값만 채워 넣으면 바로 쓸 수 있는 상태로 준비
5. **Graph API 클라이언트 코드**: `msal` 인증 + REST 호출 함수(§7.1 설계) 작성 — 실제 client_id/secret 없이는 통합 테스트가 불가하므로 단위 테스트는 mock 응답으로 검증하고, 실제 연결 테스트는 Q10 승인 후로 미룬다

### 두 트랙이 만나는 지점

Q10이 승인되는 즉시, Track A에서 미리 완성해둔 Graph API 클라이언트에 실제 자격증명만 꽂으면 1단계가 살아나고, 이미 준비된 2·3단계 파이프라인에 실제 데이터가 흐르기 시작한다. Track A를 다 만들어놓고서야 승인을 요청하는 것과, 승인부터 넣고 그 사이 Track A를 만드는 것 사이의 일정 차이가 이 지점에서 갈린다.

---

---

## 12. Track A 구현 완료 현황 (2026-07-29)

§11에서 계획한 Track A 5개 항목을 전부 코드로 구현하고 실제 컨테이너에서 라이브로 검증했다 — 문법 검사에 그치지 않고 실제 DB 마이그레이션 적용, 실제 API 호출, 실제 HTTP 발송까지 확인했다.

| # | 구현 내용 | 코드 위치 | 검증 방법 |
|---|---|---|---|
| 1 | DB 스키마 | `main.py`의 `_migrate_email_voc_tables()` — `ops_email_analysis`, `ops_voc_routing`, `ops_system_config` 폴링 설정 시드 | 실제 컨테이너에 마이그레이션 적용 후 `psql \d`로 컬럼·FK·인덱스 확인. 기존 통합테스트 33개 회귀 없음 |
| 2 | 2단계 분석 로직 | `service/email_voc/service.py`의 `analyze_email()` — `search_knowledge`+`generate_once` 재사용, `POST /api/email-voc/test-analyze` | 실제 텍스트로 호출해 분류/심각도/오배치 판정 정상 응답 확인 |
| 3 | 3단계 Teams 발송 | `service/email_voc/teams_notify.py`의 `build_teams_card()`/`send_teams_notification()` — `agents/mcp_tool/agent.py`의 `_execute_http_call` 재사용, `POST /api/email-voc/test-notify` | 공개 echo 엔드포인트로 실제 POST 발송 → HTTP 200 확인 |
| 4 | 관리자 API | `service/email_voc/routing_service.py` + `router.py`의 `/settings`, `/routing` CRUD | GET/PUT/POST/PUT/DELETE 전체 흐름 + 검증 실패 케이스(중복 메일함, 폴링 주기 하한 위반) 확인 |
| 5 | Graph API 클라이언트 | `service/email_voc/graph_client.py`의 `get_access_token()`/`fetch_messages()` (msal + httpx) | `tests/test_email_voc_graph_client.py` — mock 기반 단위테스트 5개(인증 성공/실패, 단일/페이지네이션 조회, HTTP 오류) 전부 통과 |

**회귀 확인**: 통합테스트(`tests/`) 33개, 백엔드 단위테스트(`backend/tests/`) 157개(신규 5개 포함) 전부 통과 — 기존 기능 훼손 없음.

### 알려진 갭 — 이미지 리빌드 미완료 (네트워크 이슈, 코드와 무관)

`requirements.txt`에 `msal==1.31.1`을 추가했으나, 이미지 빌드 시 임베딩 모델(`paraphrase-multilingual-mpnet-base-v2`)을 huggingface.co에서 받는 단계가 SSL 에러(`SSL: UNEXPECTED_EOF_WHILE_READING`)로 4회 연속 실패했다. 진단 결과 **Docker뿐 아니라 호스트에서도 동일 도메인 HTTPS 접속이 막혀 있어**, 컨테이너/코드 문제가 아니라 **네트워크(사내 방화벽/프록시로 추정) 문제**로 확인된다.

- 현재 떠 있는 `ops-backend` 컨테이너에는 `msal`을 임시로 `pip install`해 위 5개 항목을 전부 검증했다 — 지금 당장은 모든 기능이 정상 동작하고 테스트 가능한 상태다.
- 다만 이 임시 설치는 컨테이너를 재생성(예: `docker compose up --build`, 서버 재기동 시 이미지 재생성 등)하면 사라진다. **huggingface.co 접속이 가능한 네트워크에서 `docker compose build backend`를 한 번 더 실행해 이미지에 `msal`을 정식으로 반영해야 한다.**

---

## 13. Track B — 아직 남은 조직적 확인 사항 (2026-07-29 기준)

Track A가 전부 완성·검증됐지만, 실제 운영 데이터가 흐르려면 여전히 아래 조직적 승인/확인이 필요하다. §11에서 이미 "Track A 개발을 기다리지 말고 지금 요청부터 넣으라"고 했던 항목들로, Track A 완료 시점에도 여전히 해결되지 않았다 — **이 세 가지가 현재 전체 파이프라인의 유일한 병목**이다.

| # | 항목 | 상태 |
|---|---|---|
| Q1 | 파트별 대상 공용 메일함 확정 | 미확인 — 각 파트에 문의 필요 |
| Q2 | M365(Exchange Online) 여부 확인 | 미확인 — IT팀 문의 필요 |
| Q10 | Azure AD 앱 등록 + `Mail.Read` 권한 + RBAC 스코프 IT/보안팀 승인 | 미확인 — 가장 리드타임이 긴 항목, 아직 요청 여부 불명 |

Q1·Q2·Q10이 확정되는 즉시 `graph_client.get_access_token()`에 실제 `tenant_id`/`client_id`/`client_secret`을, `routing_service`의 메일함 설정에 실제 UPN을 넣으면 별도 코드 변경 없이 1단계(메일 수집)부터 전체 파이프라인이 바로 살아난다 — §11에서 설계한 "두 트랙이 만나는 지점"이 정확히 이 상태다.

---

### Sources
- [Retirement of Office 365 connectors within Microsoft Teams](https://devblogs.microsoft.com/microsoft365dev/retirement-of-office-365-connectors-within-microsoft-teams/)
- [Migration update for Office 365 connectors retirement in Teams – webhook URL support](https://mc.merill.net/message/MC1181996)
- [Create incoming webhooks with Workflows for Microsoft Teams (Microsoft Support)](https://support.microsoft.com/en-us/teams/apps-service/create-incoming-webhooks-with-workflows-for-microsoft-teams)
- [ChannelMessage.Send — application permission not supported for sending (GitHub issue)](https://github.com/maester365/maester/issues/616)
- [Can a Service Principal send Teams messages via Graph API — Microsoft Q&A](https://learn.microsoft.com/en-au/answers/questions/5698744/can-a-service-principal-(app)-be-configured-to-sen)
