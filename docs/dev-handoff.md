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

> **마지막 업데이트**: 2026-03-08 (v2 커밋)
> **작업 PC**: PC-A
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

### 진행 중 / 미완료 작업

- [ ] (없음 — 현재 안정 상태)

### 다음에 할 수 있는 작업 (백로그)

- [ ] Docker 이미지 레지스트리 push (다른 PC에서 빌드 없이 사용)
- [ ] 검색 임계값 UI 튜닝 (Admin > LLM 설정 탭에 이미 있음, 실사용 피드백 반영)
- [ ] 테스트 코드 작성 (pytest)
- [ ] CI/CD 파이프라인
- [ ] 사용자별 검색 설정 저장 (현재 세션 단위)

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
