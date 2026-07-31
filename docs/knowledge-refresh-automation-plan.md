# 지식 현행화 자동화 — 기획/설계 문서

> 작성일: 2026-07-29 (범위 확정: 2026-07-29)
> 현재 버전: v2.39 기준
> 상태: **기획 단계 (개발 미착수)** — dev_0 브랜치에서 검토
> 목적: Confluence 등 외부 소스에서 등록한 지식이 원본 수정 후에도 방치되는 문제(현행화 지연)를 해소하기 위해, 원본 변경을 감지해 자동/반자동으로 재수집·재청킹하는 구조의 실현 가능성과 단계별 실행 계획을 정리한다. [email-analysis-channel-plan.md](./email-analysis-channel-plan.md)와 함께 슬라이드 06 "가까운 다음 단계"의 두 축 중 하나([slide/06_기대효과와_다음단계.md](./slide/06_기대효과와_다음단계.md))
> 범위 확정 (2026-07-29): 사내 Confluence는 **온프레미스(Data Center/Server)**. PAT는 자동 발급 대신 **관리자가 수기로 등록/갱신**. 재수집 대상은 **자동 전수 추적이 아닌, 등록 시 사용자가 명시적으로 지정한 페이지만** 추적. 대량 변경 임계값은 하드코딩하지 않고 **관리자 설정 화면에서 조정 가능**하게 한다. (§7 결정사항 참고)

---

## 1. 배경 및 요구사항

현재 지식 등록은 전부 **1회성 수동 트리거**다 — 관리자가 파일 업로드/URL 입력/Confluence 페이지 선택 후 등록 버튼을 눌러야만 `rag_knowledge`에 반영된다. 원본 Confluence 페이지가 등록 이후 수정되어도 시스템은 이를 알 방법이 없어, 시간이 지날수록 **RAG가 검색해주는 지식이 원본과 괴리**되는(outdated) 문제가 생긴다.

요구사항 초안:
1. Confluence 등 외부 소스의 문서가 수정되면 이를 감지
2. 실제로 내용이 바뀐 문서만 골라 재청킹 + 재임베딩 (해시가 같으면 스킵)
3. 대량 변경(사이트 개편 등 이상 신호)은 자동 반영하지 말고 사람 검토로 전환
4. 기존 Human-in-the-loop 원칙(청킹/카테고리 등은 LLM 제안 + 사람 확정)과 정합

---

## 2. 현재 자산 감사 — 재사용 가능한 것 vs 신규 구축 필요한 것

코드베이스 전수 조사 결과:

| 구성 요소 | 현황 | 재사용 가능 여부 |
|---|---|---|
| Confluence 단건 페이지 fetch | [`web_crawler.py`](../backend/agents/knowledge_rag/ingestion/web_crawler.py)의 `fetch_confluence_by_id()` — page_id로 본문 재조회 가능, `verify=False`로 사내 자체 서명 인증서 대응 중 | ✅ 재수집 시 그대로 재사용 |
| 청킹 | `chunker.py`의 `chunk_document(doc, strategy=...)` | ✅ 재청킹 시 그대로 재사용 |
| **페이지 단위 추적(lineage)** | **없음.** 단건 URL 등록(`/import/url`)만 `rag_knowledge.source_file`에 URL이 남아 역추적 가능([router.py:675](../backend/agents/knowledge_rag/knowledge/router.py#L675)). 트리 일괄 등록(`/import/url/bulk-pages`)은 여러 페이지의 청크를 **단일 `ingestion_job`**으로 합치고 `source_file`엔 `"Confluence bulk (N pages)"` 같은 고정 문자열만 기록([router.py:1012](../backend/agents/knowledge_rag/knowledge/router.py#L1012)) — 페이지 제목(`container_name`)만 남아 유일 식별자가 아님 | ❌ 신규 구축 필요 — 자동화의 최우선 선행 조건 |
| 변경 감지(해시/버전) | `rag_knowledge` 테이블에 `content_hash`, `source_url`, `page_id` 컬럼 없음([table-definition.md §6](./table-definition.md)) | ❌ 신규 컬럼/테이블 필요 |
| Confluence 인증 | `get_user_confluence_pat()`([core/security.py:97](../backend/core/security.py#L97))로 **개인 사용자 계정(`ops_user.encrypted_confluence_pat`)**에 저장된 PAT를 사용 — 요청 컨텍스트의 로그인 사용자 전제 | ⚠️ 배치 전용 서비스 PAT 저장 슬롯은 신규 구축하되, 발급/갱신은 **자동화하지 않고 관리자가 수기로 입력**(§7 Q2 결정) — 기존 PAT 암호화 저장 로직(`encrypt_api_key`)만 재사용, OAuth/서비스 계정 자동 발급 절차는 불필요 |
| 배치/스케줄링 | cron·APScheduler 등 주기 실행 인프라 전무(main.py `lifespan`은 서버 기동 1회성 초기화만 수행) — [email-analysis-channel-plan.md §2](./email-analysis-channel-plan.md)에서도 동일하게 확인된 공통 공백 | ❌ 신규 구축이나, **이메일 분석 채널과 공유 가능**(아래 §5 참고) |
| 이상 변경 시 검토 상태 | `rag_knowledge.status`에 이미 `pending_review`(승인 대기, 검색에서 숨김)가 존재([table-definition.md:188](./table-definition.md#L188)), 리뷰 UI(`rag_knowledge_duplicate_match` 기반)도 이미 구축됨 | ✅ 신규 상태값/UI 없이 기존 승인 플로우 재사용 가능 |
| 청크 리뷰 UI | 최근 커밋(`6d017fc`)으로 "청크 검토 화면에서 청킹 전략 수동 재선택" 지원 추가됨 | ✅ 재수집 시 신규 청크 미리보기 화면으로 그대로 재사용 가능 |

**결론**: "재수집·재청킹" 로직 자체는 기존 함수 재사용만으로 충분하지만, **그 앞단의 "이 지식이 어느 원본 페이지에서 왔는가"를 추적하는 데이터 모델이 아예 없다** — 이게 이번 조사에서 발견한 가장 중요한 선행 과제다. 이 없이는 웹훅이든 폴링이든 트리거를 뭘 붙여도 "무엇을 갱신할지" 특정할 수 없다.

---

## 3. 시장 조사 요약 — 웹훅 하나로 되는 게 아니다

경쟁 제품/업계 사례 조사 결과, "지식 현행화"는 아래 3단 구조로 이뤄진다는 게 공통 패턴이었다:

| 계층 | 업계 관행 | 근거 |
|---|---|---|
| **트리거** | 웹훅(실시간) + 폴링(정합성 백업)을 병행 — 웹훅 단독은 딜리버리 실패 리스크가 있어 반드시 폴링으로 보강 | Glean 커넥터 프레임워크: 소스가 지원하면 웹훅, 아니면 폴링으로 최신성 유지 |
| **변경 감지** | 트리거와 무관하게, 콘텐츠 해시를 저장해두고 실제로 바뀐 문서만 재임베딩 — 트리거 방식보다 이 계층이 핵심 | LangChain Indexing API / LlamaIndex ingestion pipeline의 해시 기반 Record Manager 패턴 |
| **안전장치** | 대량 변경(예: 문서의 40%+ 동시 변경)은 사이트 개편 등 이상 신호로 보고 자동 반영 대신 사람 검토로 전환 | RAG 프로덕션 운영 사례(staleness 관리) |

즉 "Confluence Webhook"이라는 한 줄짜리 계획([slide/06:35](./slide/06_기대효과와_다음단계.md#L35))은 트리거 계층 하나만 가리키고 있고, 변경 감지·안전장치 계층 설계가 빠져 있었다. 아래 §4~5는 이 세 계층을 이 프로젝트의 기존 자산에 맞춰 구체화한다.

---

## 4. 초안 아키텍처

```
[추적 대상 지정 — 등록 시점, opt-in]
  단건/트리 일괄 등록 화면에 "현행화 추적 대상으로 지정" 체크박스 추가
  체크된 페이지만 rag_knowledge_source에 등록 (전수 자동 추적 아님 — §7 Q3 결정)
        │
        ▼
[트리거 계층]
  Phase 2: 야간 폴링 — Confluence REST API version.when(마지막 수정시각) 조회
    · 인증: 관리자가 수기 등록/갱신한 배치 전용 서비스 PAT 사용 (§7 Q2 결정)
    · PAT 만료(401/403) 감지 시 폴링 중단하지 않고, 해당 사이클만 skip + 관리자에게
      "Confluence 인증 실패 — PAT 갱신 필요" 알림 (기존 _translate_httpx_error의 401/403
      구분 로직 재사용)
  Phase 3: 온프레미스 Confluence Data Center/Server의 웹훅 지원 여부 재조사 후 도입 여부 결정
        │
        ▼
[변경 감지 계층 — 신규]
  rag_knowledge_source 테이블에서 대상 페이지의 content_hash 조회
  fetch_confluence_by_id()로 재조회 (기존 함수 재사용) → 새 raw_text 해시 계산
  해시 동일 → 스킵 (재임베딩 비용 없음)
  해시 다름 → 다음 단계
        │
        ▼
[재청킹 — 기존 파이프라인 재사용]
  chunk_document(doc, strategy=...) (기존 함수)
        │
        ▼
[반영 — 기존 승인 플로우 재사용]
  변경분이 소수 → rag_knowledge.status='pending_review'로 등록, 기존 리뷰 UI에서 사람이 신규/구 청크 비교 후 승인
  변경분이 namespace 내 대량(관리자 설정 임계값 초과) → 자동 반영 보류, 관리자에게 별도 알림만
  (임계값은 하드코딩하지 않고 관리자 설정 화면에서 조정 — 기존 get_thresholds/set_thresholds
  패턴(core/config.py, PUT /api/llm/thresholds) 재사용, 사이트 개편 등 이상 신호 대응)
```

---

## 5. 단계별 실행 계획

### Phase 1 — 추적 데이터 모델 + 설정 화면 구축 (자동화 없음)
- 목표: "지식 행 ↔ 원본 페이지" 매핑을 DB에 남기고, Phase 2가 필요로 하는 관리자 설정값을 먼저 마련한다.
- 범위:
  - 신규 테이블 `rag_knowledge_source` (namespace_id, source_type='confluence', external_page_id, external_url, content_hash, last_synced_at, **tracked BOOLEAN DEFAULT false**)
  - `rag_knowledge`에 nullable FK `source_ref_id INT REFERENCES rag_knowledge_source(id)` 추가 (기존 `source_file`은 표시용으로 유지, 하위 호환)
  - 단건 등록(`/import/url`) 및 트리 일괄 등록(`/import/url/bulk-pages`) 화면에 **"현행화 추적 대상으로 지정"** opt-in 체크박스 추가 — 체크된 페이지만 `tracked=true`로 `rag_knowledge_source`에 등록되고, 이후 Phase 2 폴링 대상이 됨 ([router.py:942](../backend/agents/knowledge_rag/knowledge/router.py#L942) 로직 보강)
  - 관리자 설정 화면(기존 `/api/llm/thresholds`와 같은 위치)에 **배치 전용 Confluence 서비스 PAT 입력/갱신 필드** 추가 — 개인 계정 PAT(`ops_user.encrypted_confluence_pat`)와 분리된 전역 슬롯, 기존 `encrypt_api_key` 재사용해 암호화 저장, 자동 발급 절차 없이 관리자가 수기로 입력·교체
  - 같은 관리자 설정 화면에 **대량 변경 알림 임계값**(예: `bulk_change_alert_ratio`, 0.0~1.0) 필드 추가 — 기존 `ThresholdUpdate`/`get_thresholds`/`set_thresholds` 패턴에 키 하나만 추가하는 방식으로 재사용
  - 지식 상세 화면: "어느 Confluence 페이지에서 왔는지, 마지막 동기화가 언제인지" 표시 + "지금 재수집" 수동 버튼 (기존 `fetch_confluence_by_id`/`chunk_document` 재사용, 스케줄러 불필요)
- 이 단계만으로도 "원본이 바뀐 것 같은데 뭘 고쳐야 할지 몰라 방치" 문제의 상당 부분이 완화된다 (수동이지만 추적 가능해짐)

### Phase 2 — 폴링 기반 반자동 재수집
- 목표: 사람이 매번 확인하지 않아도, 야간 배치가 **추적 대상으로 지정된 페이지만** 순회해 변경분을 찾아 승인 대기 큐에 올려준다.
- 전제조건: Phase 1의 추적 테이블·설정 화면, 스케줄러 인프라(§6 참고 — 이메일 분석 채널과 공유)
- 범위: 야간 배치가 `rag_knowledge_source WHERE tracked=true`를 순회 → 관리자가 등록한 서비스 PAT로 Confluence API 호출, `version.when` 비교 → 변경된 페이지만 재fetch+해시 비교 → 실제 콘텐츠 변경 시 새 청크를 `status='pending_review'`로 삽입(기존 상태값·리뷰 UI 재사용) → 관리자가 검토 후 승인하면 구 청크는 비활성화
  - 서비스 PAT가 만료(401/403)되면 배치를 죽이지 않고 해당 사이클만 skip, 관리자에게 "PAT 갱신 필요" 알림만 발송 — 수기 갱신 운영 방식(§7 Q2)을 전제로 한 설계
- 대량 변경 감지: 한 번의 배치 실행에서 같은 namespace 내 변경 페이지 비율이 **관리자 설정 임계값**(Phase 1에서 만든 `bulk_change_alert_ratio`, 기본값 예: 0.3~0.4)을 초과하면, 자동 pending_review 등록을 보류하고 "이상 변경 감지" 알림만 발송 (사이트 개편 등 오탐 방지)

### Phase 3 — 웹훅 실시간화 (온프레미스 지원 여부 재확인 후 도입)
- 목표: 폴링 주기(예: 1일) 대신 실시간에 가깝게 반영
- 전제조건: 사내 Confluence가 **온프레미스(Data Center/Server)로 확정**됐으므로(§7 Q1), Cloud처럼 기본 제공되지 않을 수 있는 웹훅 기능이 현재 설치된 버전/Marketplace 앱에서 지원되는지 별도 조사 필요 — 미지원 시 이 Phase는 보류하고 Phase 2 폴링을 계속 정식 운영 경로로 유지
- 범위: (지원 확인되면) 웹훅 수신 엔드포인트 신규 구현(이메일 채널의 Teams 발송과 달리 이건 **인바운드** 요청이라 기존 `_execute_http_call`—아웃바운드 호출기—는 재사용 대상이 아님) → 수신 시 Phase 2의 "변경 감지" 로직을 즉시 트리거. 폴링은 웹훅 누락 대비 정합성 백업으로 계속 유지

---

## 6. 참고 — 이메일 분석 채널과의 인프라 공유 지점

[email-analysis-channel-plan.md](./email-analysis-channel-plan.md)도 동일하게 "배치/스케줄링 인프라 전무"를 지적했다. 두 기획 모두 무인 주기 실행이 필요하므로, Phase 3(이메일 채널) / Phase 2(현행화 자동화) 시점에 **동일한 스케줄러(APScheduler 등)를 공유**해 인프라를 이중 구축하지 않는 것을 권장한다.

---

## 7. 결정 사항 (2026-07-29 확정)

| # | 질문 | 결정 | 설계 반영 |
|---|------|------|-----------|
| Q1 | 사내 Confluence가 Cloud인가 Data Center/Server인가? | **온프레미스(Data Center/Server)** | Phase 3(웹훅) 전제조건이 "Cloud라 웹훅 기본 지원"에서 "설치된 버전/앱의 웹훅 지원 여부 별도 조사"로 변경. 확인 전까지 Phase 2 폴링을 정식 경로로 운영 |
| Q2 | 재수집 배치용 PAT를 서비스 계정 자동 발급으로 할 것인가? | **아니오 — 관리자가 수기로 등록/갱신** | 자동 발급/OAuth 절차 불필요. 대신 관리자 설정 화면에 PAT 입력 필드 + 만료(401/403) 시 자동 알림만 필요(§4, §5 Phase 1~2) |
| Q3 | 추적 대상 스코프 — 전체 자동 추적 vs 선택 지정? | **선택 지정(opt-in)** — 등록 시 사용자가 명시적으로 체크한 페이지만 추적 | `rag_knowledge_source.tracked` 플래그 + 등록 화면 체크박스로 반영(§5 Phase 1) |
| Q4 | 대량 변경 임계값을 어디서 정하나? | **하드코딩 금지 — 관리자 설정 화면에서 조정 가능하게** | 기존 threshold 설정 패턴(`core/config.py` get/set_thresholds, `PUT /api/llm/thresholds`)에 `bulk_change_alert_ratio` 키 추가로 재사용(§5 Phase 1) |

이 네 가지가 모두 확정되었으므로, §5 Phase 1(추적 데이터 모델 + 설정 화면) 구현에 바로 착수 가능하다. 남은 미확인 사항은 Phase 3 전제조건인 "온프레미스 Confluence의 웹훅 기능 지원 여부"뿐이며, 이는 Phase 1~2 구현·운영 이후에 확인해도 무방하다.
