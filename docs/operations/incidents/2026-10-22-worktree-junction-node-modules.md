# 2026-10-22 worktree 강제 삭제가 연결(junction)을 따라가 프론트 node_modules를 지움

- 등급: 개발 환경 (서비스·데이터 영향 없음)
- 발견: 10/22 오후, 정책서 목록 화면 수정 뒤 `npx tsc --noEmit`가 react 모듈을 못 찾음 → `frontend-react/node_modules`가 비어 있었다(0개)
- 복구: `npm ci`(package-lock 기준, 222개) → `tsc` 통과. 소스·DB·git·골든셋(eval_out, 485문항)·.env·DB 백업은 영향 없음을 확인

## 무슨 일이 있었나
1. 트리 백엔드 작업을 격리 폴더(git worktree `../SMAgentLab_c1`)에서 서브에이전트가 했다. 그 폴더엔 node_modules가 없어, 에이전트가 타입 검사를 위해
   **원본 `frontend-react/node_modules`를 연결(Windows junction)**로 걸었다. 보고엔 "잠시 연결했다가 바로 지웠다"고 했지만 실제로는 남아 있었다.
2. 작업을 dev_0에 옮겨 커밋한 뒤, 정리하며 `git worktree remove --force ../SMAgentLab_c1`을 실행했다(미커밋 변경이 있어 --force).
3. 강제 삭제가 연결을 따라 들어가 **원본 node_modules의 내용을 지웠다.**

## 근본 원인
- 지우기 전에 **지울 폴더 안을 보지 않았다**(연결·심볼릭 링크 확인 없음). "다 반영됐다"는 파일 대조만 하고 삭제 대상 자체는 확인 안 함.
- 서브에이전트 보고("연결 지웠다")를 확인 없이 믿었다 — 개발 규율 "서브에이전트 결과도 반영 전에 재검증"을 삭제 작업에서 안 지킴.
- `--force`는 미커밋 변경 경고를 끄는 옵션이라, 위험 신호를 같이 꺼 버렸다.

## 재발 방지 점검표 (다시 돌릴 수 있는 확인)
1. worktree·폴더 삭제 전: `find <폴더> -maxdepth 3 -type l` 그리고 Windows에선 `cmd //c "dir /AL /S <폴더>"` — 연결·링크가 하나라도 있으면 그것부터 `rmdir`(연결만 지움)로 끊고, 다시 확인 후 삭제.
2. 삭제 후: `ls frontend-react/node_modules | wc -l` > 0 · `ls backend/eval_out/golden_v0/reviewed | wc -l` = 17 · `npx tsc --noEmit` 통과.
3. 서브에이전트에게 격리 폴더를 줄 때: "원본 폴더로 연결(junction·symlink) 만들지 말 것 — 필요하면 일회용 컨테이너나 복사"를 지시문에 넣는다.

## 남은 조치
- 없음(복구·확인 완료). 점검표 1·3은 개발 규율에 반영.
