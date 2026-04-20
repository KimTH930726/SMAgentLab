"""OpsNavHelper 통합 진입점 — PyInstaller 빌드용.

argv 에 따라 동작 분기:
  - argv[1] 이 "opsnav://..." → Teams 로그인 플로우 (URL 모드)
  - argv[1] 이 "--uninstall" → URL 핸들러 제거
  - 그 외 (더블클릭 포함) → 설치 모드 (URL 핸들러 등록)

PyInstaller 빌드:
  pyinstaller --onefile --name OpsNavHelper scripts/opsnav_helper_entry.py

동일 스크립트는 개발 모드(Python 직접 실행)에서도 동작한다.
"""
from __future__ import annotations

import sys


# Windows 콘솔의 기본 인코딩(cp949 등)이 한국어 출력을 깨뜨리므로 UTF-8 로 강제.
# Python 3.7+ 에서만 동작하는 reconfigure 사용.
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _warmup_playwright_node() -> None:
    """Playwright 의 node.exe 드라이버가 spawn 가능해질 때까지 대기한다.

    PyInstaller onefile 빌드는 매 실행마다 node.exe(~80MB)를 %TEMP%\\_MEIxxx
    에 추출하는데, Windows Defender 가 이 신규 실행파일을 스캔하는 약 1초 동안
    `CreateProcessW` 가 ACCESS_DENIED 로 반환된다. Playwright 는 이 창에
    걸려 PermissionError (WinError 5) 로 죽는다.

    해결: node.exe 를 우리가 먼저 `--version` 으로 실행해 보고, 성공할 때까지
    짧은 간격으로 재시도한다. 성공 시점이 곧 AV 스캔 완료 시점.
    """
    if sys.platform != "win32":
        return
    if not getattr(sys, "frozen", False):
        return

    import os
    import subprocess
    import time

    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return
    node_path = os.path.join(meipass, "playwright", "driver", "node.exe")

    # 파일 크기 안정화 대기 (추출 완료 확인)
    prev_size = -1
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            size = os.path.getsize(node_path)
        except OSError:
            time.sleep(0.1)
            continue
        if size == prev_size and size > 0:
            break
        prev_size = size
        time.sleep(0.1)

    # AV 스캔이 끝나 spawn 가능해질 때까지 재시도 (최대 10초)
    CREATE_NO_WINDOW = 0x08000000
    warmup_deadline = time.time() + 10.0
    while time.time() < warmup_deadline:
        try:
            r = subprocess.run(
                [node_path, "--version"],
                capture_output=True,
                timeout=5,
                creationflags=CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                return
        except PermissionError:
            pass
        except Exception:
            return
        time.sleep(0.2)


_warmup_playwright_node()


def _import_siblings():
    """같은 디렉토리에 있는 teams_desktop_login / install_url_handler 모듈 import.

    PyInstaller onefile 빌드에서는 번들 내부 경로에서 import 가능하고,
    개발 모드에서는 sys.path 에 스크립트 디렉토리가 포함돼 있어 import 가능하다.
    """
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import teams_desktop_login as tdl
    import install_url_handler as iuh
    return tdl, iuh


def _pause_before_exit():
    """콘솔 창이 바로 닫히지 않도록 Enter 대기. tty가 아니면 skip."""
    if not sys.stdin.isatty():
        return
    try:
        input("\n종료하려면 Enter를 누르세요...")
    except Exception:
        pass


def main() -> int:
    tdl, iuh = _import_siblings()

    # ── URL 모드: opsnav:// argv 수신 ──────────────────────────────
    if len(sys.argv) == 2 and sys.argv[1].startswith("opsnav://"):
        try:
            params = tdl.parse_opsnav_url(sys.argv[1])
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            _pause_before_exit()
            return 2

        if params["action"] != "teams-login":
            print(f"ERROR: 지원하지 않는 action: {params['action']}", file=sys.stderr)
            _pause_before_exit()
            return 2
        if not params["api_url"] or not params["jwt"]:
            print("ERROR: URL에 api_url 또는 jwt 파라미터가 없습니다.", file=sys.stderr)
            _pause_before_exit()
            return 2

        code = tdl.run(
            params["api_url"], params["jwt"], None, params["timeout"], is_url_mode=True,
        )
        if code != 0:
            _pause_before_exit()
        return code

    # ── 제거 모드 ──────────────────────────────────────────────────
    if len(sys.argv) > 1 and sys.argv[1] in ("--uninstall", "/uninstall"):
        print("=" * 50)
        print("Ops-Navigator Helper 제거")
        print("=" * 50)
        sys.argv = [sys.argv[0], "--uninstall"]
        rc = iuh.main()
        _pause_before_exit()
        return rc

    # ── 설치 모드 (더블클릭 포함, 기본 동작) ───────────────────────
    print("=" * 50)
    print("Ops-Navigator Helper 설치")
    print("=" * 50)
    print()
    print("opsnav:// URL 프로토콜을 OS에 등록합니다...")
    print()
    sys.argv = [sys.argv[0]]
    rc = iuh.main()
    if rc == 0:
        print()
        print("=" * 50)
        print("설치 완료!")
        print()
        print("이제 웹 관리자 UI의 \"Teams 로그인\" 버튼이 작동합니다.")
        print("처음 클릭 시 브라우저가 \"Ops-Navigator Helper 애플리케이션을 여시겠습니까?\"")
        print("라고 물어보면 \"허용\" 을 선택하세요 (1회성).")
        print("=" * 50)
    _pause_before_exit()
    return rc


if __name__ == "__main__":
    sys.exit(main())
