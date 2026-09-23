"""백엔드 라우트 도달 가능성 검사 (2026-09-24, 정적분석 도구).

배경: 미리보기+검토 2단계 흐름으로 등록 UX를 재설계하면서, import/csv·file·url·
text-split 직접등록 라우트와 그 안의 LLM 자동 태깅/용어 자동추출 호출이 프론트
어디서도 호출되지 않게 조용히 끊긴 채 남아있었다(2026-09-24 발견). 유닛테스트는
"함수가 올바르게 동작하는가"만 검증하고 "실제로 호출되는가"는 검증하지 않아서
이런 종류의 회귀를 못 잡는다 — 이 스크립트가 그 빈틈을 메운다.

방법: 백엔드의 모든 @router.get/post/put/patch/delete 데코레이터에서 경로를 뽑고,
프론트 전체 소스(frontend-react/src)에서 그 경로의 고정 세그먼트가 리터럴로
등장하는지 확인한다. {param} 같은 동적 세그먼트는 무시하고 마지막 고정 세그먼트
(너무 짧으면 마지막 2개)를 검색어로 쓴다 — 완벽한 정적분석은 아니고(문자열 조합
등은 놓칠 수 있음) 후보를 추려주는 용도. 후보로 잡힌 건 실제로 사람이 확인해야
한다 — 데스크톱 헬퍼(teams_desktop_login.py)처럼 프론트가 아닌 다른 곳에서
호출되는 정상 케이스도 있다(오탐 가능, 2026-09-24 실측: /auth/tokens 사례).

실행: 호스트에서 직접(`py scripts/check_route_reachability.py`) — 백엔드 컨테이너엔
frontend-react/ 소스가 마운트돼 있지 않아 컨테이너 안에서는 못 돌린다. DB/LLM 등
런타임 의존성이 전혀 없는 순수 파일 스캔이라 도커 없이도 그대로 동작한다.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent
FRONTEND_SRC = REPO_ROOT / "frontend-react" / "src"

_ROUTE_RE = re.compile(r'@router\.(get|post|put|patch|delete)\(\s*"([^"]*)"')


def _iter_routes() -> list[tuple[str, str, Path]]:
    routes = []
    for py_file in BACKEND_DIR.rglob("*.py"):
        if "__pycache__" in py_file.parts or py_file.name.startswith("test_"):
            continue
        text = py_file.read_text(encoding="utf-8", errors="ignore")
        for m in _ROUTE_RE.finditer(text):
            method, path = m.group(1).upper(), m.group(2)
            if path:
                routes.append((method, path, py_file))
    return routes


def _search_key(path: str) -> str | None:
    segments = [s for s in path.split("/") if s and not s.startswith("{")]
    if not segments:
        return None
    key = segments[-1]
    if len(key) < 4 and len(segments) >= 2:
        key = "/".join(segments[-2:])
    return key


def _referenced_in_frontend(key: str) -> bool:
    if not FRONTEND_SRC.exists():
        return True  # 프론트 없는 환경(운영 컨테이너 등)에서는 판단 보류 취급
    result = subprocess.run(
        ["grep", "-rl", key, str(FRONTEND_SRC)],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
    routes = _iter_routes()
    candidates = []
    for method, path, source_file in routes:
        key = _search_key(path)
        if key is None:
            continue
        if not _referenced_in_frontend(key):
            candidates.append((method, path, source_file, key))

    print(f"전체 라우트 {len(routes)}개 중 프론트 미참조 후보 {len(candidates)}건:\n")
    for method, path, source_file, key in candidates:
        rel = source_file.relative_to(REPO_ROOT)
        print(f"  {method} {path}  ({rel}, 검색어: {key!r})")

    if candidates:
        print(
            "\n주의: 이건 후보 목록이지 확정된 죽은 코드가 아님 — 데스크톱 헬퍼처럼 "
            "프론트가 아닌 다른 클라이언트가 호출하는 정상 케이스가 섞여 있을 수 있다. "
            "하나씩 실제로 호출부를 확인한 뒤 판단할 것."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
