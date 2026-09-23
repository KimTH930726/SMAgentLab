"""프론트 API 함수 도달 가능성 검사 (2026-09-24, 정적분석 도구).

`check_route_reachability.py`(백엔드 라우트 → 프론트 참조 여부)의 짝 — 그 스크립트
혼자로는 "importCsv()가 여전히 자기 파일 안에 '/knowledge/import/csv' 문자열을
갖고 있다"는 이유만으로 백엔드 라우트가 "참조됨"으로 오판된다(2026-09-24 실측: 정작
importCsv() 자체를 부르는 화면이 하나도 없는데도 그렇게 나옴). 이 스크립트는 그
반대 방향 — "이 API 함수를 실제로 호출하는 컴포넌트가 있는가"를 직접 검사해서
그 빈틈을 메운다. 두 스크립트를 같이 돌려야 완전한 그림이 나온다:
  1. 이 스크립트로 호출부 없는 API 함수를 찾고
  2. 그 함수가 부르는 백엔드 라우트를 check_route_reachability.py로 교차 확인

방법: frontend-react/src/api/*.ts의 최상위 `export async function`/`export function`
선언을 전부 뽑아, api/ 디렉터리 "밖"에서 그 이름이 등장하는지 확인한다(자기 자신의
정의부·같은 api/ 파일 안에서의 재사용은 호출부로 안 침 — apiFetch류 저수준 헬퍼가
여기 걸리는 게 정상이라 그런 것들은 알려진 예외 목록으로 걸러둔다).

한계: 문자열 매칭 기반이라, 동적 호출(예: obj[funcName]())이나 매우 흔한 이름은
놓치거나 오탐할 수 있다 — 후보 목록일 뿐, 최종 판단은 사람이 grep으로 재확인할 것.

실행: 호스트에서 직접(`py scripts/check_orphaned_api_functions.py`) — DB/LLM 의존성 없음.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
API_DIR = REPO_ROOT / "frontend-react" / "src" / "api"
SRC_DIR = REPO_ROOT / "frontend-react" / "src"

_EXPORT_FN_RE = re.compile(r"^export (?:async )?function\*? ?([a-zA-Z0-9_]+)", re.MULTILINE)

# 저수준 공용 헬퍼 — api/ 내부에서만 쓰이는 게 정상이라 orphan 판정에서 제외
_KNOWN_INTERNAL_HELPERS = {"apiFetch", "apiFetchBlob", "streamSSE"}


def _iter_exported_functions() -> list[tuple[str, Path]]:
    functions = []
    for ts_file in API_DIR.glob("*.ts"):
        text = ts_file.read_text(encoding="utf-8", errors="ignore")
        for m in _EXPORT_FN_RE.finditer(text):
            functions.append((m.group(1), ts_file))
    return functions


def _used_outside_api_dir(name: str) -> bool:
    result = subprocess.run(
        ["grep", "-rlw", name, str(SRC_DIR)],
        capture_output=True, text=True,
    )
    # grep이 입력받은 경로 그대로(백슬래시)에 자기 내부 구분자(슬래시)를 이어붙여
    # 섞인 경로를 반환하는 경우가 있어(Windows, 2026-09-24 실측), 비교 전에 정규화한다.
    files = [f.replace("\\", "/") for f in result.stdout.splitlines() if f.strip()]
    api_dir_str = str(API_DIR).replace("\\", "/")
    return any(not f.startswith(api_dir_str) for f in files)


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    if not API_DIR.exists():
        print(f"frontend-react/src/api 디렉터리를 찾을 수 없음: {API_DIR}")
        return 1

    functions = _iter_exported_functions()
    orphans = []
    for name, source_file in functions:
        if name in _KNOWN_INTERNAL_HELPERS:
            continue
        if not _used_outside_api_dir(name):
            orphans.append((name, source_file))

    print(f"전체 API 함수 {len(functions)}개 중 호출부 없는 후보 {len(orphans)}건:\n")
    for name, source_file in orphans:
        rel = source_file.relative_to(REPO_ROOT)
        print(f"  {name}  ({rel})")

    if orphans:
        print(
            "\n주의: 이건 후보 목록 — 삭제 전에 반드시 (1) 정말 호출부가 없는지 grep으로 "
            "재확인 (2) 이 함수가 부르는 백엔드 라우트도 다른 곳에서 안 쓰이는지 "
            "check_route_reachability.py로 교차 확인 (3) LLM 자동태깅/용어추출처럼 "
            "화면 개편 중 조용히 끊긴 실기능은 아닌지(2026-09-24 실사고) 확인할 것."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
