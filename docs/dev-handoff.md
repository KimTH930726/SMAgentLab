# Ops-Navigator 개발 핸드오프 문서

> **목적**: 2대의 PC에서 번갈아 개발할 때, 이 문서를 읽고 현재 상태를 파악하여 바로 이어서 작업할 수 있도록 한다.
> **규칙**: 작업 종료 시 반드시 이 문서를 업데이트한 뒤 커밋·푸시한다.

---

## 바톤 받기 체크리스트

새 PC에서 작업 시작 시 아래 순서를 따른다:

```bash
# 1. 최신 코드 pull
cd SMAgent
git pull origin main

# 2. 이 문서 읽기 → "현재 작업 상태" 섹션 확인

# 3. 환경 실행
docker compose up --build -d
# (코드 변경 없으면 --build 생략 가능)

# 4. 동작 확인
# http://localhost:8501  (Frontend)
# http://localhost:8000/docs  (Swagger)

# 5. 초기 로그인
# admin / 1111 (서버 최초 기동 시 자동 생성됨, .env ADMIN_DEFAULT_PASSWORD)
```

---

## 현재 작업 상태

> **마지막 업데이트**: 2026-03-09
> **작업 PC**: PC-B (Windows 11)
> **브랜치**: main

### 완료된 작업

- [x] DDD 구조 전환 (domain별 schemas/service/router)
- [x] JWT 인증/인가 (Access Token 30분, Refresh Token 7일)
- [x] 파트 기반 네임스페이스 권한 제어
  - owner_part NULL → admin만 수정/삭제 가능
  - 같은 파트 → CRUD 가능, 다른 파트 → 읽기 전용
- [x] React UI 전환 (Streamlit → React + TailwindCSS + nginx)
- [x] SSE 스트리밍 챗 (백그라운드 생성, 중단 시 부분 저장)
- [x] 대화 메모리 (ConversationSummaryBuffer + Semantic Recall)
- [x] LLM Provider 패턴 (ollama / inhouse 런타임 전환)
- [x] Few-shot CRUD + 피드백 자동 축적
- [x] 통계 대시보드 (3-state: pending/resolved/unresolved)
- [x] 파이프라인 디버그 (5단계 미리보기)
- [x] 사용자 관리 (admin CRUD, 파트 관리)
- [x] created_by_username 표시 (모든 관리 항목)
- [x] 수정 시 최종 수정자로 작성자 갱신
- [x] 네임스페이스 삭제 시 TanStack Query 캐시 즉시 동기화
- [x] 문서 현행화 (api-specification, architecture, user-manual, table-definition)
- [x] namespace FK CASCADE (고아 데이터 방지)
- [x] 통계 질의 로그 권한: admin 전용 → 네임스페이스 파트 기반으로 변경
- [x] 프론트엔드 mutation 에러 표시 전반 보강 (Knowledge/Glossary/Fewshot/Stats)
- [x] 권한 없는 사용자에게 CUD 버튼 숨김 + 안내 메시지 (StatsPanel)
- [x] **사내 LLM (DevX MCP API) 연동**
  - `agent_code` → `usecase_code` 필드명 변경
  - `inputs.model` 파라미터로 모델 선택 (GPT 5.2 / Claude Sonnet 4.5 / Gemini 3.0 Pro)
  - `response_mode` (streaming/blocking) 설정 지원
  - SSE 파서 수정: DevX 비표준 형식 대응 (`data:` JSON 안에 `event` 포함)
  - health_check: 401/403도 서버 도달 가능으로 판정
- [x] **per-user API Key 지원**
  - DB에 Fernet 암호화 저장, 요청 시 복호화하여 Authorization 헤더 전송
  - 프론트엔드: API Key 마스킹 표시 (읽기 전용)
- [x] **LLM 모델 선택 UI** (Admin > LLM 설정)
  - 3종 모델 카드 (아이콘 + 색상), 토글 방식 선택/해제
  - 미선택 시 Agent 기본 모델 사용
- [x] **apiFetch headers 덮어쓰기 버그 수정**
- [x] **회원가입 시 '기본' 파트 드롭다운 제외**
- [x] **UserManager 파트 변경 시 즉시 UI 반영** (TanStack Query invalidation)
- [x] **UI 개선 일괄**
  - 사이드바: 로고 SVG 교체, 현재 LLM 모델명 표시, 백엔드 상태 호버/툴팁
  - 시스템 설정: LLM 프로바이더 / 검색 임계값 서브탭 분리
  - 사용자 관리: 파트 관리 / 사용자 목록 서브탭 분리
  - LLM 프로바이더 아이콘: OpenAI/Claude/Gemini 실제 로고 SVG
  - 역할/상태 뱃지, 삭제 버튼 호버 효과 + 커서 + 툴팁
  - 관리 탭 전체 `refetchOnMount: 'always'` (stale 캐시 방지)
  - 피드백 후 fewshot/knowledge 쿼리 무효화
- [x] **챗 응답 마크다운 렌더링**
  - `react-markdown` + `remark-gfm` + `rehype-raw` 도입
  - 다크 테마 `.prose-chat` CSS (테이블/코드/인용/리스트/링크)
  - 시스템 프롬프트에 "Markdown 형식으로 답변" 지시 추가
- [x] **멀티턴 검색 보강 (Contextual Search Enrichment)**
  - 직전 user 질문 + assistant 답변(각 80자)을 현재 질문에 결합하여 임베딩/검색
  - 후속 짧은 질문에서도 이전 대화 맥락이 검색에 반영되어 유사도 향상
  - 추가 비용: DB SELECT 1회 (< 1ms), LLM 호출 없음

### 진행 중 / 미완료 작업

- [ ] (없음 — 현재 안정 상태)

### 다음에 할 수 있는 작업 (백로그)

- [ ] Docker 이미지 레지스트리 push (다른 PC에서 빌드 없이 사용)
- [ ] 검색 임계값 UI 튜닝 (Admin > LLM 설정 탭에 이미 있음, 실사용 피드백 반영)
- [ ] 테스트 코드 작성 (pytest)
- [ ] CI/CD 파이프라인
- [ ] 사용자별 검색 설정 저장 (현재 세션 단위)
- [ ] **프롬프트 템플릿 동적 관리 (Admin UI)**
  - 현재 프롬프트가 코드에 하드코딩되어 있어 변경 시 배포 필요
  - 파트별로 요구하는 프롬프트가 다를 수 있으므로 네임스페이스/파트 단위로 프롬프트 템플릿을 DB 저장·편집할 수 있어야 함
  - Admin > 시스템 설정 또는 별도 탭에서 system prompt, retrieval prompt 등을 CRUD
  - 변수 치환 (예: `{context}`, `{question}`) 미리보기 기능 고려

---

## 알려진 이슈

| # | 증상 | 원인 / 상태 | 우선도 |
|---|------|------------|--------|
| - | (현재 알려진 이슈 없음) | - | - |

---

## 환경 설정 요약

> 필수 환경변수 목록은 `.env.example` 참조. 아키텍처 상세는 `docs/architecture.md` 참조.

### PC별 차이점

| 항목 | PC-A | PC-B |
|------|------|------|
| OS | (기재) | (기재) |
| Ollama 설치 | O / X | O / X |
| LLM_PROVIDER | ollama / inhouse | ollama / inhouse |
| INHOUSE_LLM_URL | (기재) | (기재) |
| 비고 | | |

---

## 주요 파일 맵

> 전체 디렉토리 구조 및 설명은 `docs/architecture.md` 참조.

### 문서

```
docs/
├── api-specification.md    # API 명세서 (권한 모델 정의 포함)
├── architecture.md         # 시스템 아키텍처 (파일 맵, 환경변수)
├── table-definition.md     # 테이블 정의서
├── user-manual.md          # 사용자 매뉴얼
├── multi-turn-memory.md    # 대화 메모리 설계
├── flow.md                 # 처리 흐름도
├── data-migration.md       # 데이터 이관 가이드 (로컬→릴리즈)
├── sse-streaming-manual.md # SSE 스트리밍 매뉴얼
└── dev-handoff.md          # ← 이 문서
```

---

## 바톤 넘기기 체크리스트

작업 종료 시 아래를 수행한다:

```bash
# 1. 이 문서의 "현재 작업 상태" 섹션 업데이트
#    - 완료 항목 체크
#    - 진행 중 항목에 현재 상태 기록
#    - 알려진 이슈 추가

# 2. 커밋 & 푸시
git add -A
git commit -m "작업 내용 요약"
git push origin main

# 3. 컨테이너 정리 (선택)
docker compose down
```

### "현재 작업 상태" 작성 가이드

```markdown
### 진행 중 / 미완료 작업

- [ ] **기능명**: 현재 상태 설명
  - 수정한 파일: `backend/domain/xxx/router.py`, `frontend-react/src/xxx.tsx`
  - 남은 작업: ~~~
  - 주의사항: ~~~
```

---

## 빠른 명령어 참고

```bash
# 전체 빌드 & 실행
docker compose up --build -d

# 특정 서비스만 재빌드
docker compose build --no-cache backend
docker compose up -d --force-recreate backend

# 프론트엔드 타입 체크
cd frontend-react && npx tsc --noEmit

# 백엔드 로그
docker compose logs -f backend --tail=50

# DB 접속
docker exec -it ops-postgres psql -U ops opsdb

# DB 백업/복원
docker exec ops-postgres pg_dump -U ops opsdb > backup.sql
docker exec -i ops-postgres psql -U ops opsdb < backup.sql
```
