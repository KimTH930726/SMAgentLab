# Ops-Navigator 프로젝트 가이드

## 빌드 & 실행
- 개발: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build`
  - dev override는 `./backend:/app` 소스 마운트 추가 (코드 수정 → `restart backend`만으로 반영)
- 운영(폐쇄망): `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-build`
  - 사전 `docker load` 로 이미지 반입 필요. 자세한 절차는 `docs/deployment-closed-network.md`
- 특정 서비스만 빌드: `docker compose build backend frontend`
- 이미지 버전 태그: `.env`의 `IMAGE_TAG` (예: `v2.16`) — compose가 자동 참조
- Backend: FastAPI (port 8000), Frontend: React+nginx (port 8501)
- DB: PostgreSQL + pgvector (ops-postgres 컨테이너)
- Ollama: 호스트에서 별도 실행 (`ollama serve`)

## 프론트엔드 빌드 (frontend-react/)
- `npx tsc --noEmit` — 타입 체크 (빌드 전 반드시 실행)
- `npm run build` — Vite 프로덕션 빌드
- Streamlit frontend/ 폴더는 레거시 — 사용하지 않음

## 코드 스타일
- Python: 타입힌트 사용, async/await 패턴
- TypeScript: strict mode, 함수형 컴포넌트 + hooks
- CSS: TailwindCSS v3 유틸리티 클래스 (커스텀 CSS 지양)
- 색상: bg #0F172A, card #1E293B, accent #6366F1

## 핵심 디렉토리
- `backend/service/llm/` — LLM 프로바이더 (ollama/inhouse)
- `backend/agents/knowledge_rag/knowledge/retrieval.py` — 하이브리드 검색(Glossary Mapping + Vector/Keyword)
- `backend/service/chat/memory.py` — 대화 요약 + 시맨틱 리콜
- `backend/service/policy/` — 정책서 임포트/검색 파이프라인 (v1, docs/policy-doc-pipeline-plan.md)
- `frontend-react/src/components/` — React UI 컴포넌트
- `docs/` — architecture.md, flow.md, table-definition.md, api-specification.md (변경 시 동기화)

## 아키텍처 핵심
- 검색: Glossary Term Mapping(0.5+) → Weighted Hybrid Search (vector+keyword)
- 메모리: 4회 교환마다 LLM 요약 → pgvector 저장, 새 질문과 유사 요약 리콜
- 임베딩: nlpai-lab/KURE-v1 (1024차원)
- SSE 스트리밍: fetch 기반, AbortController로 중단 지원

## 배포 전 체크리스트
- `npx tsc --noEmit` 통과 확인
- `docker compose build` 성공 확인
- 아키텍처 변경 시 docs/ 4개 파일(architecture.md/flow.md/table-definition.md/api-specification.md) 동기화

## 개발 규율 (이 프로젝트 특화)
공통 개발 규율(검증 원칙, 커밋 워크플로우, YAGNI 등)은 `~/.claude/CLAUDE.md`에 있음. 여기는
이 저장소에만 해당하는 것만. 실 사고 사례를 포함한 전체 상세는 `docs/development-conventions.md`.

- **`docker compose` 명령은 항상 `-f docker-compose.yml -f docker-compose.dev.yml`와 함께
  실행** (조회성 `ps`/`logs`는 예외). 안 붙이면 backend의 dev 소스 마운트가 조용히 사라짐
  (frontend만 지정해도 의존 서비스인 backend가 재생성되며 발생 가능 — 실사고 있었음).
- **규모 있는 신규 기능은 dev_0에서 완전 검증 후에만 main 병합.** main → dev_0 방향 동기화는
  수시로, 반대 방향은 검증 완료 후에만.
- **라이트/다크 모드**: `slate` 팔레트만 CSS 변수로 테마 자동 반전됨 — slate는 `dark:` 접두사
  **없이** 단일 클래스로(붙이면 이중 반전으로 깨짐). slate가 아닌 accent 색상(indigo/amber/
  emerald 등)은 반대로 `dark:` 쌍을 직접 명시해야 함(자동 반전 대상이 아님).
- **`useMutation`은 항상 `onError`를 갖는다** — 폼 컨텍스트가 있으면 기존 에러 상태 슬롯
  재사용, 없으면 `alert(err.message)` 최소 패턴.
- 관리자 화면 문구는 짧고 직관적으로 — 배경 설명은 `title` 툴팁으로.

## Allowed tools
- Bash(docker compose*)
- Bash(npx tsc*)
- Bash(cd /Users/kth/SMAgent*)
- Bash(cd /Users/kth/SMAgent* && *)
