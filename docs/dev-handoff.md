# Ops-Navigator 개발 핸드오프

> **규칙**: 작업 종료 시 이 문서 업데이트 후 커밋·푸시

---

## 바톤 받기

```bash
git pull origin dev_0
docker compose up --build -d
# http://localhost:8501  /  http://localhost:8000/docs
# 초기 로그인: admin / (ADMIN_DEFAULT_PASSWORD in .env)
```

---

## 현재 작업 상태

> **마지막 업데이트**: 2026-03-20
> **브랜치**: `dev_0` (테스트 완료 후 `main` 머지 예정)
> **최근 변경**: 스키마 스캔 diff 개선 + ERD/용어 고아 정리 + 스캔 리포트 모달 + ERD 검색·양방향 싱크 (v2.10)

### 진행 중 / 미완료

없음 — 클린 상태

### 최근 완료: 스키마 스캔 diff 개선 + ERD/용어 UX (v2.10)

- 스키마 스캔을 diff 방식으로 개선 — 테이블/컬럼 추가·삭제·변경 감지, 변경분만 임베딩 (전체 재임베딩 불필요)
- 스캔 시 ERD 고아 관계 자동 정리 — 삭제된 테이블/컬럼 관련 `sql_relation` 레코드 자동 삭제
- 스캔 시 용어사전 고아 자동 삭제 — 삭제된 컬럼을 참조하는 `sql_synonym` 자동 삭제
- 스캔 완료 리포트 모달 — 변경 상세(추가/삭제/변경 테이블·컬럼) + ERD/용어 탭 이동 유도 (human-in-the-loop)
- AI 관계 추천(`/relations/suggest-ai`) 및 용어 자동생성(`/synonyms/generate-ai`)을 변경 테이블 대상으로만 제한 (토큰 절약)
- ERD 검색 기능 — 테이블명 검색 → 해당 테이블 포커스 + 자동 스크롤
- ERD 관계 목록 ↔ SVG 양방향 싱크 — 클릭 시 상호 스크롤 + 하이라이트
- `docker-compose.yml`에서 `devx-mcp-api` 서비스의 `extra_hosts` 하드코딩 제거

### 이전 완료: 프롬프트 에이전트별 분리 (v2.9)

- `ops_prompt` 테이블에 `agent_type VARCHAR(50) DEFAULT 'all'` 컬럼 추가 (마이그레이션 자동 적용)
- Text2SQL 파이프라인 프롬프트 8개 `ops_prompt`로 통합 (`sql2_parse/generate/fix/summarize` × system/user)
  - 파이프라인 단계(`parse/generate/fix/summarize`)가 `get_prompt('sql2_*')` 사용으로 전환
  - `sql_pipeline_stage`의 `prompt`/`system_prompt` 컬럼은 유지하되 더 이상 사용하지 않음
- Admin 파이프라인 탭에서 프롬프트 편집 UI 제거 → 시스템설정 탭 프롬프트 관리로 통합
  - `PUT /api/text2sql/pipeline/{id}/prompts` 엔드포인트 제거
- Admin 시스템설정 탭 프롬프트 목록: `selectedAgent`에 따라 해당 에이전트 + `all` 항목만 표시
- `{var}` 및 `{{var}}` 플레이스홀더 모두 경고 배지 표시

### 이전 완료: 백엔드 디렉터리 리팩터링 (v2.8)

- `backend/domain/` → `backend/service/` (플랫폼 공통: auth, admin, chat, feedback, llm, mcp_tool, prompt)
  - ⚠️ `platform/` 명칭은 Python stdlib `platform` 모듈과 충돌 → `service/` 로 확정
- `backend/domain/knowledge/` → `backend/agents/knowledge_rag/knowledge/`
- `backend/domain/fewshot/` → `backend/agents/knowledge_rag/fewshot/`
- `backend/domain/text2sql/` → `backend/agents/text2sql/admin/`
- `backend/domain/http_tool/` → `backend/agents/http_tool/admin/`
- DB 테이블 이름 변경 (마이그레이션 자동 적용): `ops_knowledge` → `rag_knowledge`, `ops_glossary` → `rag_glossary`, `ops_fewshot` → `rag_fewshot`, `ops_knowledge_category` → `rag_knowledge_category`, `ops_conv_summary` → `rag_conv_summary`

### 백로그

- [ ] **Text2SQL 네임스페이스 선택 UI**: 채팅 시 text2sql 전용 DB 네임스페이스 선택 UI (현재는 Admin에서만 설정)
- [ ] **통합 대시보드 agent_type 필터**: 통계 탭에 에이전트별 필터 추가
- [ ] 테스트 코드 (pytest)
- [ ] CI/CD 파이프라인

---

## 아키텍처 핵심 결정사항

> 코드 작업 전 반드시 숙지할 비자명한 설계들

| 항목 | 내용 |
|------|------|
| **Agent-centric UI** | 로그인 → `AgentSelect` 페이지 → 에이전트별 채팅+어드민. `useAppStore.selectedAgent: 'knowledge_rag' \| 'text2sql' \| null`. `null` = 선택 화면 |
| **Admin 탭 에이전트 스코핑** | `TABS` 배열의 `agentScope: 'knowledge_rag' \| 'text2sql' \| 'all'` 으로 필터링. 에이전트 전환 시 탭 자동 리셋 |
| **AgentRegistry 패턴** | `chat_stream` → `AgentRegistry.get(agent_type).stream_chat()` 위임. 에이전트 추가 = `agents/` 모듈 + `main.py`에 `register()` 1줄 |
| **generate_once() api_key** | 사내 LLM 모든 호출에 `api_key=get_user_api_key(user)` 필수. 누락 시 401. Admin 엔드포인트도 동일 (`require_admin` Depends 사용) |
| **Text2SQL 파이프라인** | `parse→rag→generate→validate→fix→execute→summarize` 7단계. `pipeline_ctx` dict 공유. `sql_pipeline_stage` 테이블에 설정 영속화 |
| **Text2SQL 벡터 저장** | asyncpg INSERT/UPDATE 시 반드시 `$N::vector` 캐스트 + `str(emb)` 변환. list 직접 전달 시 DataError |
| **MCP 도구 플로우** | Case 3(첫 진입): 도구 선택 SSE. Case 2(`selected_tool_id`): 파라미터 폼. Case 1(`approved_tool`): HTTP+RAG 병렬 → 답변. 매 호출 감사 로그 |
| **Semantic Cache** | `shared/cache.py`. 유사도 임계값 **0.88**, TTL 30분. `ops_system_config`에 영속화. graceful degradation |
| **캐시 쿼리 정규화** | `normalize_query()` — 연속공백·한글자모간공백 제거, 소문자. 캐시 벡터만 적용, RAG 검색은 원본 사용 |
| **MCP JSONB 파싱** | asyncpg JSONB → `str` 반환. `json.loads()` 필수. `isinstance(dict)` 체크만 하면 항상 `{}` 반환 버그 |
| **SSE tool_request 유지** | 스트림 종료 시 `toolRequest` 대기 중이면 `clearStreamState()` 호출 안 함. DB placeholder → `convertMessages`에서 필터링 |
| **프롬프트 관리** | `ops_prompt` 테이블. `agent_type` 컬럼으로 에이전트 스코핑. `get_prompt(key, fallback)` → DB 우선, 없으면 fallback. Text2SQL 파이프라인도 `sql2_*` 키로 동일하게 관리 |
| **Fernet 암호화 키** | `settings.fernet_secret_key` 우선, 없으면 `settings.jwt_secret_key` fallback |
| **Ollama num_ctx** | `generate_once()`에서 `num_ctx: 8192`. 기본 2048은 스키마 포함 SQL 프롬프트에 부족 |
| **resolve_namespace_id** | `core/database.py` — NS name→id 변환 공통 헬퍼. 인라인 쿼리 금지 |
| **네임스페이스 권한** | `owner_part = NULL` → 전체 CRUD / 같은 파트 → CRUD / 다른 파트 → 읽기 전용 |
| **SQL Few-shot 상태** | `status: pending→approved→rejected`. 채팅 👍 → `POST /fewshots/from-feedback` (일반 사용자) → pending 등록 → 관리자 승인. text2sql 에이전트만 해당 |
| **MCP 도구 에이전트 분리** | `ops_mcp_tool.agent_type` 컬럼. `listMcpTools(ns, agentType)` 시 필터. 도구 생성 시 `agent_type` 필수 |

---

## 완료 이력 (최근순)

- **v2.10 스키마 스캔 diff + ERD UX** (2026-03-20): ① 스키마 스캔 diff 방식 — 테이블/컬럼 추가·삭제·변경 감지, 변경분만 임베딩. ② ERD 고아 관계 자동 정리 + 용어사전 고아 자동 삭제 (삭제 테이블/컬럼 참조). ③ 스캔 완료 리포트 모달 (변경 상세 + ERD/용어 탭 이동 유도, human-in-the-loop). ④ AI 관계 추천·용어 자동생성을 변경 테이블 대상으로만 제한 (토큰 절약). ⑤ ERD 테이블명 검색 → 포커스+스크롤. ⑥ ERD 관계 목록 ↔ SVG 양방향 싱크 (클릭 시 상호 스크롤+하이라이트). ⑦ docker-compose.yml devx-mcp-api extra_hosts 하드코딩 제거

- **v2.7 UX 개선 + 피드백 연동** (2026-03-19): ① SQL Few-shot 피드백 연동 — 채팅에서 👍 클릭 시 질의-SQL 쌍이 `sql_fewshot`에 `status='pending'`으로 자동 등록, 관리자가 SQL Few-shot 탭에서 승인/반려. 상태 필터(전체/등록 후보/승인됨/반려됨) + 뱃지 UI. ② ERD 배경 드래그 → 캔버스 패닝 (스크롤 없이 자유 이동). ③ MCP 도구 에이전트 분리 — `ops_mcp_tool.agent_type` 컬럼 기반, 에이전트별 독립 도구 관리. Admin 탭 순서 재편 (SQL Few-shot → MCP 도구 → 파이프라인). ④ text2sql 부정 피드백 시 지식 등록 폼 미노출 (에이전트별 분기)

- **v2.6 Text-to-SQL 전면 도입** (2026-03-19): ① Text-to-SQL 에이전트 7단계 파이프라인 (parse/rag/generate/validate/fix/execute/summarize) + `sql_*` 테이블 10개. ② Agent-centric UI — 로그인 후 AgentSelect 화면, 에이전트별 채팅+어드민 분리. ③ Admin 탭 재편 — text2sql 서브탭(대상DB·스키마·ERD·용어사전·SQL Few-shot·파이프라인·감사로그) 메인탭 승격, 에이전트현황 제거, 캐시·통계 → knowledge_rag 전용. ④ ERD 고도화 — 위치 DB 저장, Ctrl+Z 되돌리기(20단계), 자동 정리, 줌, 회색 점선 관계선+화살표. ⑤ AI 자동생성 3종 (`/synonyms/generate-ai`, `/fewshots/generate-ai`, `/relations/suggest-ai`). ⑥ AgentSelect 헬스체크 뱃지. ⑦ 라이트모드 SQL 블록 oneLight 테마 전환. ⑧ 백엔드 최적화 — embed 병렬화, Redis pipeline, Fernet 싱글톤, UPDATE RETURNING

- **v2.4 MCP 도구 + Semantic Cache** (2026-03-17): `ops_http_tool` → `ops_mcp_tool` 리네임, `hub_base_url`+`tool_path` 분리, 감사로그 (`ops_mcp_tool_log`), 도구별 집계 모달. Semantic Cache — 유사도 임계값 0.88, TTL 30분, `ops_system_config` 영속화, 쿼리 정규화. 버그 수정: JSONB→str 파싱, TIMESTAMPTZ 변환, 파라미터 타입 강제변환

- **초기 개발** (2026-03-12~13): AgentBase/AgentRegistry 도입, DDD 구조 전환, JWT 인증, Few-shot 라이프사이클, Glossary AI 추천, 멀티턴 검색 보강, 사내 LLM DevX SSE 연동

---

## 알려진 이슈

| # | 증상 | 우선도 |
|---|------|--------|
| 1 | 시맨틱 캐시 의도 변형 히트 불가 | 낮음 — 임계값 낮추거나 LLM 의도 추출 기반 캐시 전환 필요 |
| 2 | Ollama 서버 ~1600자 이상 프롬프트 연결 끊김 | 중간 — 해당 서버 body size 제한 추정. 사내 LLM은 정상 |

---

## 빠른 명령어

```bash
# 재빌드
docker compose build backend frontend && docker compose up -d

# 타입 체크
cd frontend-react && npx tsc --noEmit

# 로그
docker compose logs -f backend --tail=50

# DB 접속
docker exec -it ops-postgres psql -U ops opsdb

# 백업/복원
docker exec ops-postgres pg_dump -U ops opsdb > backup.sql
docker exec -i ops-postgres psql -U ops opsdb < backup.sql
```
