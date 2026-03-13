# Ops-Navigator 개발 핸드오프

> **규칙**: 작업 종료 시 이 문서 업데이트 후 커밋·푸시

---

## 바톤 받기

```bash
git pull origin main
docker compose up --build -d
# http://localhost:8501  /  http://localhost:8000/docs
# 초기 로그인: admin / (ADMIN_DEFAULT_PASSWORD in .env)
```

---

## 현재 작업 상태

> **마지막 업데이트**: 2026-03-13
> **브랜치**: dev_0
> **최근 변경**: HTTP 도구 에이전트 안정화 + ToolRequestCard UX 개선

### 진행 중 / 미완료

- [ ] **ToolRequestCard 레이아웃 확인**: 세로 레이아웃으로 수정 후 배포 완료 — Ctrl+F5 후 동작 확인 필요
- [ ] **400 에러 원인 확인**: 유저가 400 에러 보고했으나 백엔드 로그는 모두 200 OK. 브라우저 Network 탭에서 재현 확인 필요
- [ ] **HTTP 도구 E2E 테스트**: 도구 선택 → 파라미터 입력/승인 → HTTP 호출 → LLM 답변 전체 플로우 검증

### 백로그

- [ ] **HttpToolManager 캐시 문제**: useState+fetch 패턴이라 백엔드 변경 즉시 반영 안 됨. react-query 전환 고려
- [ ] **Step 2: 디렉토리 재편 + 프론트엔드** (두 번째 에이전트 추가 시): `domain/` → `platform/` + `agents/`, DB prefix rename, 에이전트 선택 UI → [platform-expansion-strategy.md](platform-expansion-strategy.md) Step 2 참조
- [ ] **플랫폼 어드민 확장**: 에이전트 디렉토리 UI, 파트-에이전트 접근 제어, 통합 대시보드 agent_type 필터 → [platform-expansion-strategy.md](platform-expansion-strategy.md) §9 참조
- [ ] Docker 이미지 레지스트리 push
- [ ] 사용자별 검색 설정 저장 (현재 세션 단위)
- [ ] 테스트 코드 (pytest) — conftest에 인증 fixture 필요, 현재 TC 01~06은 인증 미포함 상태
- [ ] CI/CD 파이프라인
- [ ] 데이터 이관 가이드 작성 (릴리즈 확정 후 — 스키마 변동 중이므로 보류)
- [ ] 사용자 매뉴얼 작성 (최종 UI 확정 후 — 기능 변동 중이므로 보류)

---

## 아키텍처 핵심 결정사항

> 코드 작업 전 반드시 숙지할 비자명한 설계들

| 항목 | 내용 |
|------|------|
| **AgentRegistry 패턴** | `chat_stream` → `AgentRegistry.get("knowledge_rag").stream_chat()` 위임. 새 에이전트 추가 시 `agents/` 하위 모듈 + Registry 등록만으로 완결 |
| **HTTP 도구 에이전트 플로우** | Case 1(첫 진입): tool_fetch → tool_select(LLM) → tool_params → `tool_request` SSE 이벤트 → 프론트 ToolRequestCard 표시 → 사용자 승인/거절. Case 2(승인 후): tool_load → http_call → llm_answer → done. `tool_request` 이벤트로 스트림 일시 중단, 프론트에서 재요청 시 `approved_tool` 포함 |
| **SSE tool_request 상태 유지** | 스트림 종료 시 `toolRequest`가 대기 중이면 `clearStreamState()` 호출 안 함. DB에는 `[추가 정보 입력 대기 중]` 등 placeholder 저장 → `convertMessages`에서 필터링 |
| **프롬프트 관리** | `ops_prompt` 테이블 (key, content, description). `domain/prompt/loader.py`의 `get_prompt(key, fallback)` → DB 우선, 없으면 fallback. Admin UI에서 편집 가능 |
| **공유 헬퍼** | `domain/chat/helpers.py` — 에이전트·라우터 양쪽에서 사용하는 DB 헬퍼 (메시지 업데이트, 쿼리로그, 클린업 등) |
| **resolve_namespace_id** | `core/database.py` — NS name→id 변환 공통 헬퍼. 모든 도메인에서 이 함수 사용 (인라인 쿼리 금지) |
| **agent_type 컬럼** | `ops_conversation`, `ops_query_log`, `ops_feedback`에 `agent_type VARCHAR(50) DEFAULT 'knowledge_rag'` + `ops_feedback.meta JSONB` |
| 네임스페이스 권한 | `owner_part = NULL` → 전체 CRUD / 같은 파트 → CRUD / 다른 파트 → 읽기 전용 |
| 파트 비정규화 | `created_by_part`, `owner_part`는 FK가 아닌 문자열 → `rename_part` 시 4개 테이블 수동 cascade |
| 검색 설정 단일 소스 | `config.py` 기본값 → `GET/PUT /api/llm/search-defaults` → 프론트엔드 fetch |
| 슈퍼어드민 파트 | `GET /auth/parts`는 admin 소속 파트 제외 (회원가입 노출 차단) / `GET /auth/parts/all` admin 전용 |
| debug 모드 퓨샷 | `min_similarity=0.0`으로 전체 조회, context 빌드는 실제 임계값으로 별도 필터링 |
| slate 색상 주의 | `[data-theme="light"]`에서 slate 팔레트 역전 → 고정색 필요 시 zinc 사용 |
| LLM SSE 파서 | DevX 비표준: `data:` JSON 안에 `event` 포함, `401/403`도 서버 도달로 판정 |
| 멀티턴 검색 | 직전 user+assistant 각 80자를 현재 질문에 결합해 임베딩 |

---

## 완료 이력 (최근순)

- **HTTP 도구 에이전트 안정화** (2026-03-13): ToolRequestCard SSE tool_request 이벤트 정상 처리 (clearStreamState 방지), DB placeholder 메시지 필터링, no_tool_needed 폴백 UX, asyncpg JSONB 문자열 파싱, SSE 401 토큰 리프레시, router finally 안전장치, 프롬프트 관리 시스템 (`backend/domain/prompt/`), LLM system_prompt 지원
- **HTTP 도구 시스템 MVP** (2026-03-12): `agents/http_tool/agent.py` HTTP Tool 에이전트, `ops_http_tool` 테이블, HttpToolManager 관리 UI, DebugPanel HTTP 도구 통합, ToolRequestCard 승인/거절 UI
- **백엔드 클린 코드 리팩토링**: `inhouse.py` critical 버그 수정 (`_extract_ext_conversation_id` 미정의 → `_extract_session`), `resolve_namespace_id` 공통 헬퍼 통합 (중복 11곳 제거), 응답 형식 통일 (`ok`→`status`), 중복 except 정리, DB 인덱스 6개 추가
- **디버그 패널 용어 매핑 색상 수정**: `bg-slate-100` → `bg-zinc-100` (라이트모드 slate 역전 이슈)
- **AgentBase/AgentRegistry 도입**: `agents/base.py` 추상 클래스 + 레지스트리 싱글턴, `agents/knowledge_rag/agent.py` RAG 에이전트 구현, `domain/chat/helpers.py` 공유 헬퍼 추출, `chat_stream` 에이전트 위임, `agent_type` DB 컬럼 추가
- **rename_part cascade 완전 수정**: `ops_namespace.owner_part` + `ops_knowledge/glossary/fewshot.created_by_part` 4개 테이블 cascade UPDATE
- **지식 베이스 배지 cyan·amber**: 컨테이너명 cyan / 테이블명 amber, 레이블+배지 `<span>` 그룹 포함관계 시각화
- **사용자 목록 파트 필터**: `UserManager > UserSection` 파트별 필터 버튼 + 인원 수 표시
- **디버그 패널 퓨샷 모달화**: 검색 결과와 동일 UI 패턴 (border-l-4 색상, 컨텍스트 제외 뱃지, 클릭→모달)
- **context_preview 버그**: debug 시 미달 퓨샷이 포함되던 문제 → context 빌드용 퓨샷 별도 임계값 필터
- **LLM 컨텍스트 미리보기 버튼 항상 표시**: 빈 컨텍스트 시 모달에서 "컨텍스트 없음" 안내
- **파트 관리 UI 재설계**: chip → 카드 그리드 (Building2 아이콘, 인라인 편집, user_count 뱃지, Modal 삭제)
- **파트 삭제 보호**: 소속 사용자 있으면 삭제 불가 (400 응답 + UI 안내)
- **파트 사용자 수 API**: `list_parts()` LEFT JOIN COUNT, `PartOut.user_count`, `Part.user_count?`
- **통계 수정 후 등록 모달화**: `KnowledgeRegisterModal` (AI 답변 미리채움, TagInput, 우선순위)
- **Namespace 자동 선택 개선**: `lastHandledUserPartRef` 파트당 1회 자동 전환
- **공통 namespace (v2.1)**: admin 생성 namespace `owner_part = NULL` → 전체 CRUD
- **검색 설정값 중앙화**: `config.py` 단일 소스 → API → 프론트엔드 fetch
- **다크/라이트 테마**: CSS 변수 기반 slate 팔레트 재정의, Sun/Moon 토글
- **멀티턴 검색 보강**: 직전 대화 맥락 결합 임베딩
- **사내 LLM (DevX MCP API) 연동**: SSE 비표준 파서, per-user Fernet 암호화 API Key
- **백엔드 DDD 구조**: domain별 schemas/service/router, JWT 인증, SSE 스트리밍

---

## 알려진 이슈

| # | 증상 | 우선도 |
|---|------|--------|
| 1 | 라이트모드 `bg-slate-N` 다크 네이비로 렌더링 | 낮음 (zinc로 우회 완료) |

---

## 빠른 명령어

```bash
# 재빌드
docker compose build backend && docker compose up -d --force-recreate backend

# 타입 체크
cd frontend-react && npx tsc --noEmit

# 로그
docker compose logs -f backend --tail=50

# DB
docker exec -it ops-postgres psql -U ops opsdb

# 백업/복원
docker exec ops-postgres pg_dump -U ops opsdb > backup.sql
docker exec -i ops-postgres psql -U ops opsdb < backup.sql
```
