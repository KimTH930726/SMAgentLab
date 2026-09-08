# MCP 도구 에이전트 제거 계획 (완료 — 2026-09-08 실행)

> 작성일: 2026-09-07(분석/스코핑) → 2026-09-08 실제 제거 실행 완료.
> 배경: 발표자료 작업 중 사용자가 "MCP 부분 너무 잡다하다, 로직에서 다 빼버리자"고 판단. 이후
> MCP 에이전트가 RAG 검색 로직을 내부에서 직접 재구현해 안고 있는 것까지 확인되어("짬뽕된 구조")
> 제거 근거가 더 명확해짐.
> Text2SQL 제거 때와 달리 **브랜치 보존(archive) 없이** 이력 없이 완전 삭제로 진행함.
>
> **실행 결과 — 계획과 달라진 점**:
> - `_execute_http_call`은 계획대로 별도 파일로 이관하되, 이름은 `shared/http_executor.py`가
>   아니라 `shared/http_client.py`로, 함수명도 `call_http()`로 일반화(도구 레지스트리용
>   param_schema 타입 변환 로직은 MCP 전용 개념이라 가져오지 않음 — VOC는 이미 타입이 맞는
>   dict를 그대로 넘기므로 불필요)
> - DB의 `ops_mcp_tool`/`ops_mcp_tool_log`/`ops_prompt`의 mcp_tool 관련 3행은 계획대로 **삭제
>   마이그레이션 없이 방치**(Text2SQL 전례와 동일 — 기존 설치엔 남지만 무해)
> - 프론트 `PromptManager.tsx`의 agent_type 라벨맵도 text2sql과 동일하게 mcp_tool 항목을 빼서,
>   남은 레거시 프롬프트 행은 라벨 없이 원본 문자열로만 표시(기존 text2sql 처리와 동일 패턴)
> - 검증: 백엔드 전체 테스트 343개 통과 + `shared/http_client.py` 실제 HTTP 호출 런타임 스모크
>   테스트 + `npx tsc --noEmit` + `npm run build` + 프론트/백엔드 이미지 재빌드 후 컨테이너
>   기동 확인까지 전부 완료

---

## 1. 왜 제거하나

- 사용자 판단: MCP 도구(HTTP API 연동 에이전트)가 코드베이스를 "잡다하게" 만듦
- 관리자 화면·채팅 UI 양쪽에 걸쳐 있어 안 쓰는 기능이 표면적으로 계속 노출됨
- Text2SQL 제거 전례가 있음(`project_text2sql_archived` 메모리) — 단, 그때는 `archive/with-text2sql`
  브랜치로 보존했지만, 이번엔 **보존 없이 완전 삭제**로 결정됨(사용자 명시)

---

## 2. 백엔드 제거 대상

### 2-1. 통째로 삭제할 디렉토리
```
backend/agents/mcp_tool/        # McpToolAgent, HTTP 실행기(_execute_http_call 포함)
backend/service/mcp_tool/       # router.py, schemas.py — CRUD/테스트/자동완성 엔드포인트
```

### 2-2. 수정이 필요한 파일
| 파일 | 수정 내용 |
|---|---|
| `backend/main.py` | ①`from service.mcp_tool.router import ...`, `from agents.mcp_tool.agent import ...` import 제거 ②`mcp_tool_router` 등록 제거(L40) ③`AgentRegistry.register(McpToolAgent())` 제거(L991) ④`close_mcp_http_client()` 호출 제거(L1001, lifespan shutdown) ⑤`ops_mcp_tool`/`ops_mcp_tool_log` 테이블 마이그레이션 함수(L339-396) 및 관련 `ALTER TABLE`(L247) 제거 ⑥`ops_prompt` 시드에서 `mcp_tool` 관련 3행(tool_select/tool_answer/autocomplete, L451-453) 및 L522 UPDATE문 제거 — **단 `ops_part_agent_access` 테이블 마이그레이션(같은 함수 안에 있지만 MCP 전용 아님, 범용 파트-에이전트 접근권한 테이블)은 그대로 유지** |
| `backend/service/__init__.py` | 모듈 docstring의 "mcp_tool" 언급(L1) 제거 |
| `backend/shared/cache.py` | L114 주석의 "mcp_tool 에이전트" 언급 — agent_type별 캐시 분리 로직 자체는 유지(다른 에이전트에도 유효한 설계), 주석 예시만 정리 |

### 2-3. ⚠️ 핵심 의존성 — 먼저 이관해야 할 것

**`backend/service/email_voc/teams_notify.py`가 `agents/mcp_tool/agent.py`의 `_execute_http_call()`을
그대로 import해서 재사용 중** (L27: `from agents.mcp_tool.agent import _execute_http_call`).
VOC 이메일 채널의 Teams 웹훅 발송이 이 함수에 의존한다 — **MCP 디렉토리를 삭제하기 전에 이
함수를 먼저 공용 위치(예: `shared/http_executor.py` 신설, 또는 `service/email_voc/` 내부로 직접
이식)로 옮겨야 VOC 알림이 안 깨진다.** 이걸 놓치면 VOC 발송이 임포트 에러로 즉시 죽는다 —
이번 제거 작업에서 실수하기 가장 쉬운 지점.

---

## 3. 프론트엔드 제거 대상

### 3-1. 통째로 삭제할 파일
```
frontend-react/src/api/mcpTools.ts                      # API 클라이언트
frontend-react/src/components/admin/McpToolManager.tsx  # 관리자 화면 MCP 도구 관리 탭
frontend-react/src/components/chat/ToolRequestCard.tsx  # 채팅 내 도구 승인 카드 UI
```

### 3-2. 수정이 필요한 파일
| 파일 | 수정 내용 |
|---|---|
| `frontend-react/src/pages/Admin.tsx` | `McpToolManager` import 제거, 탭 목록에서 `'mcp_tools'` 제거(L45), 탭 정의 항목 제거(L66), 렌더링 분기 제거(L122) |
| `frontend-react/src/pages/AgentSelect.tsx` | KnowledgeRAG 카드 features 목록의 "MCP 도구 연동 (선택)" 문구 제거(L26) |
| `frontend-react/src/store/useAppStore.ts` | `mcpEnabled` 상태 + `setMcpEnabled` 액션 제거(L37, L60-61) |
| `frontend-react/src/components/chat/ChatContainer.tsx` | `useHttpTool` 토글 상태·UI 전체 제거(L613 MCP 토글 버튼, L622/625/634 관련 텍스트), `agentType: useHttpTool ? 'mcp_tool' : ...` 분기(L478) 단순화, L547/L568의 `agentType: 'mcp_tool'` 하드코딩 지점(도구 승인/재시도 흐름으로 추정) 확인 후 제거 |
| `frontend-react/src/components/admin/DebugPanel.tsx` | **가장 손이 많이 감(42곳)** — `mcp_tools` 탭 자체가 이 컴포넌트 안에 통째로 내장돼 있음. `listMcpTools`/`testMcpTool` import, 관련 state(`httpTools`, `useMcpToolDebug`, `selectedMcpToolId`, `httpResults` 등), 탭 정의(L24, L77-81), 렌더링 블록 전부 제거 |
| `frontend-react/src/components/admin/LLMSettings.tsx` | 1곳 — 확인 후 제거(프롬프트 종류 목록에 `tool_select`/`tool_answer` 등이 있었다면 함께 정리) |
| `frontend-react/src/components/admin/PromptManager.tsx` | 2곳 — `mcp_tool` agent_type 프롬프트(tool_select/tool_answer/autocomplete) 관리 UI 부분 제거 |
| `frontend-react/src/types/index.ts` | `McpTool`/`McpToolParam`/`McpToolCreatePayload`/`McpToolUpdatePayload`/`SSEToolRequestEvent`/`SSEToolResultEvent`/`SSEToolErrorEvent` 등 MCP 전용 타입 제거(L365 이하) — `SSEEvent` 유니온 타입에서도 해당 이벤트 타입 제거 필요 |

---

## 4. DB 스키마 처리 방안

| 테이블 | 처리 |
|---|---|
| `ops_mcp_tool` | 신규 설치엔 생성 안 함(마이그레이션 제거). 기존 설치는 테이블이 남아있어도 무해 — Text2SQL 전례처럼 "더 이상 갱신 안 되는 채로 방치"할지, 별도 정리 마이그레이션(`DROP TABLE IF EXISTS`)을 만들지는 다음 세션에서 결정 |
| `ops_mcp_tool_log` | 위와 동일 |
| `ops_part_agent_access` | **MCP 전용 아님 — 유지.** 파트별로 어떤 에이전트에 접근 가능한지의 범용 테이블. 같은 마이그레이션 함수 안에 같이 있어서 헷갈리기 쉬우니 주의 |
| `ops_prompt`의 `agent_type='mcp_tool'` 행 3개 | 삭제 마이그레이션 추가 여부 결정 필요(안 지워도 그냥 안 쓰이는 행으로 남을 뿐, 기능엔 무해) |

---

## 5. 제거 순서 제안

1. **`teams_notify.py`의 `_execute_http_call` 의존성부터 해소** — 함수를 공용 위치로 이관, VOC 정상 동작 확인(실 E2E)
2. 백엔드: `main.py`에서 import·등록·마이그레이션 코드 제거 → `agents/mcp_tool/`, `service/mcp_tool/` 디렉토리 삭제 → 백엔드 기동 확인 + 전체 테스트 스위트 실행
3. 프론트엔드: `DebugPanel.tsx`(가장 복잡) 먼저 정리 → 나머지 파일 순차 정리 → `McpToolManager.tsx`/`ToolRequestCard.tsx`/`mcpTools.ts` 삭제 → `npx tsc --noEmit` + `npm run build`
4. DB 마이그레이션 정리 여부 최종 결정 후 반영
5. `docs/architecture.md`/`docs/api-specification.md`/`docs/table-definition.md`에서 MCP 관련 섹션 제거

---

## 6. 검증 계획 (다음 세션에서 실행 시)

- 백엔드 전체 테스트 스위트 통과 확인 (현재 343개 — `backend/tests`에 MCP 전용 테스트 파일은 없는 것으로
  확인됨(2026-09-07 grep 기준), 제거 직전 재확인만 하면 됨)
- **VOC Teams 알림 실 E2E 재확인 필수** (§3 의존성 때문에 회귀 위험 가장 큼)
- `npx tsc --noEmit` 통과
- `docker compose build` 성공
- 관리자 화면에서 "MCP 도구" 탭 자체가 안 보이는지, 채팅 화면에서 MCP 토글이 안 보이는지 육안 확인
- 신규 네임스페이스 생성 시 DB 마이그레이션이 에러 없이 도는지 확인(제거된 테이블 참조가 다른 곳에 안 남아있는지)

---

## 7. 완전성 재검증 방법 (다음 세션 시작 시)

작업 착수 전 아래로 재검색해서 이 문서 작성 이후 변경사항이 없는지 먼저 확인할 것:
```bash
grep -rln "mcp_tool\|McpTool\|mcp-tools" backend --include="*.py" | grep -v __pycache__
grep -rln "mcp" frontend-react/src -i --include="*.tsx" --include="*.ts"
```
