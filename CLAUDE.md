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
