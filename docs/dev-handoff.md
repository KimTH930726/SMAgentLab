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

> **마지막 업데이트**: 2026-03-11
> **작업 PC**: PC-B (Windows 11)
> **브랜치**: main
> **최근 변경**: 라이트/다크 dual-mode 색상 개선 / 통계 수정 후 등록 모달화 / namespace 자동선택 개선 / Admin→Chat 대화목록 즉시 갱신

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
- [x] **UX 용어 개선 및 시각화 강화**
  - 벡터 가중치/키워드 가중치 → 의미 중심(문맥 유사도)/키워드 중심(단어 일치)
  - base_weight → 문서 우선순위 (높음/보통/기본 배지 + 색상 시각화 + 기준 설명)
  - 기본 topK 5 → 3으로 변경
  - LLM 컨텍스트 미리보기: 토글 → 모달(좌: raw text, 우: Markdown 렌더링, 스크롤 동기화)
  - 지식 베이스 카드 헤더에 우선순위 배지 + 최종 변경일(YYYY-MM-DD) 표시
  - 전반적 색상 대비 개선 (text-slate-600 → text-slate-500)
- [x] **다크/라이트 테마 전환**
  - CSS 변수 기반 Tailwind slate 팔레트 재정의 (tailwind.config.js)
  - `[data-theme="light"]` 시 slate 스케일 반전 + accent 색상 56개 오버라이드
  - 사이드바 하단 Sun/Moon 토글 아이콘 (localStorage 저장)
  - 하드코딩 hex(`bg-[#0F172A]`, `bg-[#1E293B]`) → slate 클래스로 전환 (6개 파일)
  - SVG 도넛 차트 fill 하드코딩 → CSS 변수 적용
  - prose-chat 마크다운 스타일 CSS 변수화
- [x] **검색 설정값 중앙화 (config.py → API → 프론트엔드)**
  - `config.py`의 `default_top_k`, `default_w_vector`, `default_w_keyword`가 설정의 단일 소스
  - 백엔드: `retrieval.py`에 런타임 오버라이드 패턴 추가 (`get/set_search_defaults`)
  - 백엔드: `GET/PUT /api/llm/search-defaults` 엔드포인트 추가
  - 백엔드: `ChatRequest` 스키마 Field default를 `settings` 참조로 변경 (`default_factory`)
  - 프론트엔드: `useAppStore` 하드코딩 제거, 앱 초기화 시 API로 기본값 fetch (`SearchConfigSync`)
  - 프론트엔드: `api/chat.ts` fallback 하드코딩(`?? 0.7`, `?? 5` 등) 제거
  - 프론트엔드: Admin > 시스템 설정 > 검색 임계값 탭에 검색 기본값 관리 UI 추가
- [x] **환경변수/설정값 역할 분리 정리**
  - `.env` / `docker-compose.yml`: 인프라·시크릿만 유지 (DB, LLM URL, JWT키, Fernet키, 관리자 비번)
  - `config.py`: 앱 로직 설정은 코드 기본값으로 관리 (임베딩, 모델명, 검색값, 임계값, JWT 만료 등)
  - 중복 환경변수 제거 (`EMBEDDING_MODEL`, `VECTOR_DIM`, `OLLAMA_MODEL`, `DEFAULT_TOP_K` 등)
- [x] **mutation 후 관련 쿼리 캐시 무효화 수정** (6개 파일)
- [x] **검색 설정 UI 개선**
  - 검색 설정 탭을 첫 번째로, LLM 프로바이더 탭을 두 번째로 배치
  - 검색 기본값/임계값 섹션을 CollapsibleSection 토글로 변경
  - 의미/키워드 가중치 슬라이더 연동 (합이 항상 1)
- [x] **백엔드 클린 코드 리팩토링**
  - API key 복호화 함수 통합 (`core/security.py` → `get_user_api_key`)
  - chat DB 저장 병렬화 (`_save_user_message` + `_save_assistant_message` gather)
  - 후처리 작업 병렬화 (`_post_save_tasks` gather)
  - cleanup 쿼리 통합 (DELETE + affected ID를 CTE 한 쿼리로)
  - 회원가입 검증 통합 (파트 존재 + 사용자 중복을 단일 쿼리로)
- [x] **앱 아이콘 교체** — 빨간 7잎 꽃 SVG (favicon + 사이드바 로고)
- [x] **공통 namespace 권한 (v2.1)**
  - admin이 생성한 namespace → `owner_part = NULL` → 모든 사용자 CRUD 가능
  - `check_namespace_ownership`: `owner_part IS NULL`이면 전체 허용
  - DB: 기존 admin 생성 namespace `owner_part` → NULL 업데이트
- [x] **슈퍼어드민 파트 분리**
  - `ops_part`에서 '기본' → '슈퍼어드민'으로 rename (INSERT+cascade UPDATE+DELETE)
  - `main.py` 시드: '기본' 제거, '슈퍼어드민' 삽입 + '기본' 잔존 시 자동 마이그레이션
  - `GET /auth/parts`: `exclude_admin_parts=True` — admin 소속 파트 제외 (회원가입 노출 차단)
  - `GET /auth/parts/all`: admin 전용, 전체 파트 반환 (관리자 UI용)
  - `getAllParts()` API 함수 추가, `UserManager`에서 사용
  - `queryKey: ['parts-all']` — mutation invalidation 키 통일
- [x] **파트 이름 변경 (인라인 편집)**
  - `PATCH /api/auth/parts/{id}` 엔드포인트 추가
  - `rename_part()` 서비스: ops_user.part + ops_namespace.owner_part cascade 업데이트
  - `UserManager > PartSection`: 파트 chip 클릭 → 인라인 input 편집, Enter/✓ 저장, Esc 취소
- [x] **Namespace 이름 변경 (인라인 편집)**
  - `PATCH /api/namespaces/{name}` 엔드포인트 추가
  - `rename_namespace()` 서비스: 6개 관련 테이블 cascade 업데이트
  - `NamespaceManager`: 이름 hover 시 연필 아이콘, 클릭 → 인라인 편집
- [x] **LLM 설정 일반 사용자 개방**
  - `PUT /api/llm/config`: `get_current_admin` → `get_current_user`
- [x] **TagInput UI (지식 베이스 컨테이너명·관련 테이블)**
  - `KnowledgeTable`: 컨테이너명/관련 테이블 입력을 chip 형태 TagInput으로 개선
  - Enter/쉼표로 태그 추가, Backspace로 마지막 삭제, X로 개별 삭제
- [x] **내 파트 최우선 정렬**
  - `sortNamespacesByUserPart()` 유틸 추가 (`src/utils/sortNamespaces.ts`)
  - KnowledgeTable / GlossaryTable / FewshotTable / StatsPanel / Sidebar 전체 적용
- [x] **파트 생성 캐시 즉시 반영**
  - `NamespaceManager.createMutation.onSuccess`: `setQueryData(['namespaces'])` 옵티미스틱 업데이트
- [x] **UI 레이블 통일**
  - admin 역할 → '슈퍼어드민' 표시 (UserManager, Sidebar)
  - 탭 이름 '파트 관리' → '파트 DB 관리'
- [x] **문서 현행화** (architecture.md, user-manual.md, dev-handoff.md)
- [x] **라이트모드 UI 색상 대비 개선**
  - `tailwind.config.js`: `darkMode: ['selector', '[data-theme="dark"]']` 추가 — `dark:` prefix 클래스 실제 동작
  - `Badge.tsx`: 모든 색상에 라이트/다크 dual-mode 색상 적용. `slate` → `zinc` 고정색으로 교체 (CSS 변수 역전 방지)
  - `KnowledgeTable.tsx`: `weightClass` 기본값 `bg-slate-200` → `bg-zinc-100 text-zinc-600 dark:...`, 리스트 뷰 인라인 배지도 dual 색상
  - `TagInput.tsx` (신규 `src/components/ui/TagInput.tsx`): chip 색상 라이트/다크 dual 적용, `color: 'cyan' | 'indigo'` prop
  - 근본 원인: `index.css`에서 `[data-theme="light"]` 시 `--slate-200: 30 41 59` (다크 네이비)로 역전 → zinc로 회피
- [x] **통계 > 수정 후 등록 모달화**
  - `StatsPanel.tsx`: `KnowledgeRegisterModal` 컴포넌트 추가 — AI 답변 미리채움, TagInput(컨테이너명/테이블), 우선순위 슬라이더, 업무구분
  - `QueryLogModal` 인라인 폼 전체 제거 → "수정 후 등록" 버튼이 `KnowledgeRegisterModal` 트리거
  - KnowledgeTable의 인라인 TagInput → 공통 `TagInput` 컴포넌트로 교체
- [x] **Namespace 자동 선택 개선**
  - `lastHandledUserPartRef`로 파트당 1회만 자동 전환 (이후 수동 선택 보존)
  - localStorage 이전 세션 값이 남아있어도 내 파트 소유 namespace로 올바르게 교체
- [x] **Admin→Chat 전환 시 대화목록 즉시 갱신**
  - `Sidebar.tsx`: `chatRefreshKey` 구독 추가 — `bumpChatRefresh()` 신호로 대화목록 강제 re-fetch
  - 기존 `isChatPage` 변경 트리거 외에 추가 보험

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
| 1 | 라이트모드에서 `bg-slate-N` 클래스가 다크 네이비로 렌더링 | `index.css`의 `[data-theme="light"]` 블록에서 slate 팔레트 값이 역전됨. zinc 고정색으로 우회 | 낮음 (해결됨) |

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
