# Ops-Navigator 커스터마이징 가이드

> 팀 환경에 맞게 변경된 설정값, 커스텀 구현, 작업 내역 정리

---

## 1. LLM Provider — 사내 DevX API (InHouse)

| 항목 | 값 |
|------|-----|
| Provider 코드 | `inhouse` (`LLM_PROVIDER=inhouse` in `.env`) |
| API 형식 | DevX MCP API — `usecase_code`, `inputs.model`, SSE 스트리밍 |
| 스트림 방식 | 비표준 SSE — `data` JSON 안에 `event` 필드 포함 (`InHouseLLMProvider` 대응) |
| 기본 모델 | `.env`의 `INHOUSE_LLM_MODEL` (예: `claude-sonnet-4.5`, `gpt-5.2`, `gemini-3.0-pro`) |
| Agent 코드 | `INHOUSE_LLM_AGENT_CODE=playground` |
| per-user API Key | 사용자 등록 시 입력 → Fernet 암호화 저장 → 호출 시 복호화하여 Bearer 토큰으로 전달 |
| fallback | 개인 키 없으면 `INHOUSE_LLM_API_KEY` 시스템 키 사용 |

**주의**: Admin 엔드포인트에서 `generate_once()` 호출 시 반드시 `api_key=get_user_api_key(admin)` 전달 필요. 누락 시 401.

---

## 2. 임베딩 모델

| 항목 | 값 |
|------|-----|
| 모델 | `paraphrase-multilingual-mpnet-base-v2` |
| 벡터 차원 | 768 |
| 이유 | 한국어 지원, 사내 데이터(운영 가이드·SQL 메타데이터) 특성상 다국어 모델 채택 |
| 실행 위치 | Backend 컨테이너 내부 (CPU) |
| 정규화 | `normalize_embeddings=True` → 코사인 유사도 = 내적 |

---

## 3. Semantic Cache 설정

| 항목 | 기본값 | 조정 이유 |
|------|--------|-----------|
| 유사도 임계값 | **0.88** | 0.92는 너무 엄격, 의도 동일 질문도 미스 발생 → 하향 조정 |
| TTL | 30분 | 운영 데이터 변경 주기 고려 |
| 쿼리 정규화 | 한글 자모 간 공백 제거 + 연속 공백 제거 + 소문자 | 한국어 입력 패턴 대응 |
| 영속화 | `ops_system_config` 테이블 (재시작 후에도 유지) | |

---

## 4. Text-to-SQL 파이프라인 커스텀

### 4-1. Ollama num_ctx 확장
```python
# agents/text2sql/pipeline/generate.py 및 generate_once() 호출부
num_ctx: 8192  # 기본 2048 → 스키마 포함 프롬프트 처리 부족으로 확장
```

### 4-2. AI 자동생성 검증 규칙 (용어사전)
용어사전 AI 자동생성 시 아래 SQL 키워드가 `target`에 포함된 항목 자동 제외:
```
SELECT, FROM, JOIN, WHERE, GROUP BY, ORDER BY, LIMIT, HAVING
```
허용 형식: `table.column` / `SUM(table.column)` / `table.column = 'VALUE'`

### 4-3. ERD 위치 저장
- `sql_schema_table` 테이블의 `pos_x FLOAT`, `pos_y FLOAT` 컬럼 활용
- `PUT /api/text2sql/namespaces/{ns}/schema/positions` — 일괄 저장
- 모든 위치가 0이면 자동 격자 배치로 초기화

---

## 5. Admin UI 탭 구조

### agentScope 분류
| 탭 | agentScope |
|----|------------|
| 기준 정보 관리, 지식 베이스, 용어집, Few-shot, 캐시, 통계, 디버그 | `knowledge_rag` |
| 대상 DB, 스키마, ERD, 용어 사전, SQL Few-shot, 파이프라인, 감사 로그 | `text2sql` |
| MCP 도구, 시스템 설정, 사용자 관리 | `all` |

### MCP 도구 에이전트 분리
- `ops_mcp_tool.agent_type` 컬럼으로 에이전트별 도구 분리
- knowledge_rag 에이전트: `agent_type='knowledge_rag'` 도구만 조회/생성
- text2sql 에이전트: `agent_type='text2sql'` 도구만 조회/생성
- 기존 도구 default: `'knowledge_rag'`

---

## 6. 인증 커스텀

| 항목 | 설정 |
|------|------|
| Access Token 만료 | 30분 |
| Refresh Token 만료 | 7일 |
| 슈퍼어드민 파트 | 회원가입 목록에서 자동 제외 (`/api/auth/parts` vs `/api/auth/parts/all`) |
| 네임스페이스 권한 | `owner_part=NULL` → 전체 CRUD (Admin 생성 namespace 기본값) |

---

## 7. 알려진 한계 및 우회

| 한계 | 우회 방법 |
|------|-----------|
| Ollama 서버(10.149.172.233) ~1600자 이상 프롬프트 연결 끊김 | 사내 LLM(InHouse) 사용 권장 |
| Semantic Cache — 의도 변형 질문 히트 불가 | 임계값 추가 하향 또는 LLM 의도 추출 기반 캐시 전환 검토 |
| Text2SQL 네임스페이스 채팅 연동 | 현재 chat namespace를 그대로 사용. 전용 선택 UI 백로그 |

---

## 8. 환경변수 (.env) 필수값

```bash
# 필수
JWT_SECRET_KEY=<랜덤 32바이트 이상>
FERNET_SECRET_KEY=<Fernet.generate_key() 결과>
ADMIN_DEFAULT_PASSWORD=<초기 admin 비밀번호>

# 사내 LLM
INHOUSE_LLM_URL=<DevX MCP API 엔드포인트>
INHOUSE_LLM_API_KEY=<시스템 기본 API 키>
INHOUSE_LLM_MODEL=claude-sonnet-4.5
INHOUSE_LLM_AGENT_CODE=playground
INHOUSE_LLM_RESPONSE_MODE=streaming

# DB
DATABASE_URL=postgresql://ops:ops1234@postgres:5432/opsdb

# LLM Provider
LLM_PROVIDER=inhouse  # 또는 ollama
```
