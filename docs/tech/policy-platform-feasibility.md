# 정책 플랫폼(policy-platform-design) 통합 가능성 검토

**검토일**: 2026-08-25
**대상 문서**: `D:\MD자료\AIOps\policy-platform-design\` (Excel 기반 정책서를 의미검색+결정론적 실행+이력추적 플랫폼으로 전환하는 설계, ADR 12건 확정)
**질문**: 이 설계를 지금 Ops-Navigator 프로젝트에 녹일 수 있는가?

## 결론

**"녹인다"(기존 코드/로직에 섞어 넣기)는 부적합. "같은 레포에 별도 도메인으로 얹기"는 기술적으로 가능.**
단, "에이전트 하나 추가" 수준이 아니라 VOC 이메일 채널을 만들 때와 같은 규모(백그라운드 서비스+관리자 승인화면+에이전트)의 별도 서브시스템이다.

## 겹치는 부분 (재사용 가능)

- Canonical Store = PostgreSQL, Dense Projection = pgvector → Ops-Navigator가 이미 쓰는 정확히 같은 인프라. 같은 Postgres 컨테이너 재사용 가능.
- `agents/` 디렉토리 + AgentRegistry 패턴 자체는 이 설계의 "Retrieval Pipeline(질의응답)" 부분과 구조적으로 맞아떨어짐.

## 안 겹치는 부분 (신규 구축 필요, 이질적)

| 항목 | 내용 |
|---|---|
| Lexical Projection | OpenSearch + nori 형태소분석기 — Ops-Navigator는 지금 Postgres GIN(`to_tsvector`)만 씀. 신규 인프라 컨테이너 필요 |
| Canonical Store 스키마 | Bitemporal 4컬럼 + JSONB — 기존 `rag_*`/`ops_*` 테이블은 단순 `created_at`/`updated_at`뿐, 완전히 다른 패턴 |
| 거버넌스(ACL prefilter, 결정론적 위험도 판정, 승인/활성화, 롤백, 감사·계보) | Ops-Navigator에 대응 개념 자체가 없음. 지금은 승인 없이 즉시 반영되는 구조 |
| Excel 전용 파이프라인 | 변경감지(Webhook+Reconciler) → 5패턴 파싱 → 정규화 → 12종 diff → quarantine — 기존 지식 인제스천(txt/md/pdf/URL/텍스트분할)과 목적이 다름 |

## 스택 전제 불일치

설계 문서 §1이 "기존 스택: Java / Spring Boot / PostgreSQL 중심"을 전제로 명시하고 있음 — 조직 표준 스택이 Python/FastAPI가 아닐 가능성. Python 구현 자체는 막히지 않으나, 설계 원칙(사내 표준 스택 재사용)과는 어긋나는 선택이 된다는 점은 인지 필요.

## 규모

MVP 범위 자체가:
- 11개 컴포넌트 (Ingest 6 + Canonical Core 1 + Serve 5)
- 신규 인프라(OpenSearch) 프로비저닝
- Gold Dataset 400문항 + Evaluation Harness (E0~E5 실험)
- 정책 5,000~20,000건, 일 변경 10~100건 규모의 PoC 전제

→ "기능 하나 추가"가 아니라 별도 프로젝트급 공수.

## 거버넌스 = 공통 모듈로 뽑아야 하나?

**패턴은 공통이지만 판정 로직은 정책 전용이라 그대로 재사용은 못 한다.**

- 공통화 가치가 있는 부분: `lifecycle_state: draft→staged→active→superseded` 상태전이 + 활성화 전 검증 게이트 + 위험도별 승인 라우팅 + 감사로그 + Break-glass 예외 경로 — 순수 "변경→검증→승인→활성화" 범용 상태기계.
- Ops-Navigator에 이미 이 패턴의 조각이 **3군데 따로** 존재: 지식 중복등록(`pending_review`→승인/반려/덮어쓰기, v2.34), few-shot 승인(`candidate`→`active`), SQL few-shot(`pending`→`approved`/`rejected`). 셋 다 코드·스키마가 완전히 별개고 감사로그·위험도등급·롤백은 없음.
- 공통화가 안 되는 부분: 위험도 판정(`06-governance/01-risk-classification.md`)은 정책 Canonical 필드명(`obligations[]`, `prohibitions[]`, `exceptions[]`, `policy_id` 등)에 하드코딩된 화이트리스트 방식이라 정책 도메인 밖에서 못 씀.

**지금 뽑을지 여부 — 추천: 지금은 뽑지 않는다.**
기존 3개 승인 플로우가 실제로 문제를 일으키고 있지 않고, 정책 플랫폼은 아직 착수 결정도 안 된 설계 단계라 소비자가 없는 상태에서 추상화를 먼저 설계하면 나중에 실제 요구와 안 맞는 모양으로 굳어질 위험이 크다(YAGNI). 실제 페인(버그/불일치/4번째 소비자 등장)이 생기거나 정책 플랫폼이 실제로 그린라이트 받을 때 통합 리팩터링을 다시 꺼내는 게 낫다.

## 실행한다면 — 구조 매핑

"에이전트 하나 추가"가 아니라 **에이전트 + VOC 스타일 백그라운드 서비스**, 두 덩어리로 나뉜다.

| 정책 플랫폼 요소 | Ops-Navigator 대응 위치 | 근거 |
|---|---|---|
| Retrieval Pipeline (ACL prefilter + Hybrid검색 + 답변구성) | `agents/policy/agent.py` (신규 에이전트, AgentRegistry 등록) | 사용자가 자연어로 질의→답변 받는 구조라 `KnowledgeRagAgent`와 동일 shape |
| Excel 감지·파싱·정규화·diff·위험도판정·승인라우팅·Outbox·색인 | `service/policy/` (신규 서비스 모듈, 관리자 화면+백그라운드 스케줄러) | 채팅으로 트리거되는 게 아니라 백그라운드 파이프라인+승인 큐 — `service/email_voc/`와 동일 shape (스케줄러+관리자 CRUD+이력) |

에이전트는 `service/policy/`가 만들어놓은 색인 결과(pgvector/OpenSearch)를 읽어 답변만 생성하고, 실제 공수의 대부분(Excel 파싱, 거버넌스, 색인)은 서비스 쪽에 있다.

## 미결 / 다음 결정 필요 시점

- 정책 플랫폼 착수 여부 자체가 아직 미정 (이 검토는 가능성 판별용).
- 착수가 결정되면: (1) Java/Spring Boot 대신 Python/FastAPI로 갈지 스택 재확인, (2) OpenSearch 도입 여부, (3) 거버넌스 공통모듈 리팩터링을 이 시점에 함께 할지 여부를 다시 논의.
