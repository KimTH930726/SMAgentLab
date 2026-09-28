---
name: verify-and-ship
description: "SMAgentLab에서 코드를 고치고 나서 '완료했다'고 보고하기 전에 거치는 절차 — 실 dev 스택에 재반영하고, 실제로 동작을 재현해서 확인하고, 규모 있는 변경은 리뷰 패스까지 거친 뒤에만 커밋한다."
---

# 검증 후 완료 보고 (Verify-and-Ship)

이 세션에서 같은 시퀀스를 하루에도 여러 번 손으로 반복해와서 절차로 고정한다. work-os
쪽의 `smagent-verify` 스킬이 "보고를 받은 뒤 밖에서 재검증"하는 절차라면, 이건 그 반대 —
**보고를 하기 전에 이 세션 스스로 거치는 절차**다. 둘은 짝이지 같은 게 아니다.

## 0. 원칙
- "빌드된다"는 "동작한다"의 증거가 아니다 — 실 dev 스택에서 실제로 눌러/불러서 확인한다.
- "코드가 논리적으로 맞다"와 "지금 서비스 중인 프로세스가 그 코드로 동작 중이다"는 다른
  질문 — 재반영(재시작/재빌드)까지 끝나야 검증을 시작할 수 있다.
- 200 OK는 검증이 아니다 — 상태 전이·가중치 변경 등 부작용 있는 로직은 실제 전/후 값을
  비교한다.
- 자동화 도구(정적분석/서브에이전트 리뷰 등)의 결과도 검증 후 반영한다 — 그대로 신뢰하지 않는다.

## 1. 재반영 — 백엔드와 프론트가 다르다
이 프로젝트의 dev 구성은 비대칭이다(`docker-compose.dev.yml` 주석에도 명시):
- **백엔드**: `./backend:/app` 소스 바인드 마운트 → 코드 수정 후 `docker compose -f
  docker-compose.yml -f docker-compose.dev.yml restart backend`만으로 반영. 재빌드 불필요.
- **프론트엔드**: nginx 정적 빌드라 dev에서도 소스 마운트가 없다 → 반드시
  `docker compose -f docker-compose.yml -f docker-compose.dev.yml build frontend` +
  `up -d --no-deps frontend`까지 해야 반영된다. **restart만으로는 안 바뀐다** —
  이걸 놓쳐서 프론트 작업이 몇 시간 동안 화면에 하나도 반영 안 된 채 진행된 실사고가
  있었음(2026-09-24).

둘 다 해당하는 변경이면 백엔드 먼저(재시작은 빠르니 문제 있으면 여기서 먼저 걸러진다),
그다음 프론트 재빌드.

## 2. 정적 검증
- `cd frontend-react && npx tsc --noEmit` — 프론트 변경마다.
- `docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend
  python -m pytest tests/ -q` — 변경한 모듈 타겟 실행 먼저, 그다음 전체 재실행해서
  회귀 없는지 확인. 새 로직엔 그걸 검증하는 테스트를 같이 추가.
- 화면 흐름(등록 경로, 탭 구조 등)을 재설계했으면 `py backend/scripts/
  check_route_reachability.py`와 `py backend/scripts/check_orphaned_api_functions.py`
  (호스트에서 실행 — 백엔드 컨테이너엔 프론트 소스가 없어서 실행 안 됨)도 같이 돌려서
  화면 개편 중 조용히 끊긴 라우트/API가 없는지 확인. 후보가 뜨면 그대로 지우지 말고
  하나씩 실제로 호출부를 확인(오탐 있음).

## 3. 실 동작 검증
- **백엔드만 바뀐 경우**: `curl`로 실제 로그인(`admin`/`1111`) 후 관련 엔드포인트를
  직접 호출 — 응답 값이 기대한 그대로인지 확인. 상태를 바꾸는 API는 DB를 직접 조회해
  (`docker compose ... exec -T postgres psql -U ops -d opsdb -c "..."`) 전/후 값 비교.
- **화면이 바뀐 경우**: 스크래치패드에 1회용 Playwright 스크립트(`chromium.launch()` →
  로그인 → 해당 화면까지 클릭 → 스크린샷)를 짜서 실제로 확인. 스크린샷은 `Read`로 직접
  보고 판단한다(스크립트가 통과했다고만 보지 않는다). 확인 끝나면 스크립트는 지운다
  (영구 자산이 아님 — 영구 회귀 커버리지가 필요하면 §4의 E2E 스펙으로 승격).
- 검증 중 실제 DB 데이터를 바꿨다면(반려/승인/값 수정 등), 테스트 목적이 아니었던
  상태는 원래대로 되돌린다 — 실사용 데이터를 검증 부산물로 어지르지 않는다.

## 4. 영구 회귀 커버리지
`frontend-react/e2e/`에 이미 두 스펙(지식 등록, 정책 검토)이 있다. §3에서 1회용으로
확인하고 끝내지 말고, **다음에 이 코드를 건드려도 자동으로 잡아줘야 하는 흐름**이면
스펙으로 남긴다 — 이번 세션에 "일회성으로 검증만 하고 회귀 스위트엔 안 남겼다"는
지적을 받은 적이 있음. 새 스펙도 기존 스펙처럼 실 dev 스택 그대로 쓰고 목킹하지 않는다.
`npx playwright test`로 전체 스펙이 여전히 통과하는지 확인.

## 5. 리뷰 패스 (규모 있는 변경만)
수정 파일 5개 이상이거나 새 화면/워크플로 신설이면, 커밋 전에 `/code-review`를 그
diff 범위에 실제로 돌린다(예: `/code-review <base>..<head>`). 나온 발견은 심각도와
무관하게 검토 후 반영하거나(고칠 만하면 고치고 재검증), 왜 지금은 안 고치는지 명시
— 조용히 무시하지 않는다. 단순 버그수정·문서정리는 생략 가능(YAGNI).

## 6. 커밋 전 마지막 확인
- `git status --short` + `git diff --stat` — 의도한 파일만 있는지, 스크래치 스크립트나
  임시 산출물이 안 섞였는지.
- 문서 동기화가 필요한 변경(아키텍처/스키마/흐름)이면 `docs/architecture.md` 버전 bump
  + changelog, 필요시 `docs/table-definition.md`/`docs/flow.md`도.
- 그다음에만 커밋 — 관련 없는 변경을 한 커밋에 묶지 않는다.

## 7. 원격 반영 (매번 확인 후에만)
이 세션에서 확립된 순서 — 매번 사용자에게 명시적으로 확인받고 실행, 자동 실행 금지:
1. `git push origin dev_0`
2. `git checkout main && git merge --ff-only dev_0`
3. `git push github main`
4. (사용자가 별도로 요청했을 때만) `git push origin main`
5. `git checkout dev_0`
