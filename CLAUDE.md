# Ops-Navigator 프로젝트 가이드

## 빌드 & 실행
- `docker compose up --build` — 전체 서비스 시작
- `docker compose build backend frontend` — 특정 서비스만 빌드
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
- `backend/services/llm/` — LLM 프로바이더 (ollama/inhouse)
- `backend/services/retrieval.py` — 2단계 하이브리드 검색
- `backend/services/memory.py` — 대화 요약 + 시맨틱 리콜
- `frontend-react/src/components/` — React UI 컴포넌트
- `docs/` — architecture.md, flow.md, user-manual.md (변경 시 동기화)

## 아키텍처 핵심
- 검색: Glossary Term Mapping(0.5+) → Weighted Hybrid Search (vector+keyword)
- 메모리: 4회 교환마다 LLM 요약 → pgvector 저장, 새 질문과 유사 요약 리콜
- 임베딩: paraphrase-multilingual-mpnet-base-v2 (768차원)
- SSE 스트리밍: fetch 기반, AbortController로 중단 지원

## 배포 전 체크리스트
- `npx tsc --noEmit` 통과 확인
- `docker compose build` 성공 확인
- 아키텍처 변경 시 docs/ 3개 파일 동기화

## Allowed tools
- Bash(docker compose*)
- Bash(npx tsc*)
- Bash(cd /Users/kth/SMAgent*)
- Bash(cd /Users/kth/SMAgent* && *)
