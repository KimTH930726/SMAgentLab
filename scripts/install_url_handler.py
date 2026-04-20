"""opsnav:// URL 프로토콜 핸들러 등록 스크립트 (크로스 플랫폼).

관리자 UI에서 "Teams 로그인" 버튼을 누르면 브라우저가 opsnav://teams-login?...
URL을 열고, OS가 이 스크립트로 등록한 핸들러 (teams_desktop_login.py)를 실행한다.

## 사용

```bash
# 설치
python scripts/install_url_handler.py

# 제거
python scripts/install_url_handler.py --uninstall
```

## 플랫폼별 동작

- **Windows**: HKCU\\Software\\Classes\\opsnav 레지스트리 키 등록.
  관리자 권한 불필요 (사용자 범위).
- **macOS**: Ops-Navigator Helper.app 번들을 ~/Applications 에 생성하고
  LaunchServices에 등록.
- **Linux**: ~/.local/share/applications/opsnav-helper.desktop 생성하고
  xdg-mime으로 opsnav scheme 기본 핸들러 지정.

## 제거 시 주의

설치 시 기록된 로그 파일(~/.ops-navigator/url_handler.json)을 읽어 정확한
역순 제거를 수행한다. 이 파일이 없으면 수동 정리가 필요할 수 있다.
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SCHEME = "opsnav"
APP_NAME = "Ops-Navigator Helper"
BUNDLE_ID = "com.opsnavigator.helper"
STATE_DIR = Path.home() / ".ops-navigator"
STATE_FILE = STATE_DIR / "url_handler.json"


# ── 공통 유틸 ───────────────────────────────────────────────────────────────

def _is_frozen() -> bool:
    """PyInstaller 로 빌드된 .exe 로 실행되는지 여부."""
    return bool(getattr(sys, "frozen", False))


def _script_path() -> Path:
    """teams_desktop_login.py 의 절대 경로 (개발 모드 전용)."""
    p = Path(__file__).resolve().parent / "teams_desktop_login.py"
    if not p.exists():
        raise RuntimeError(f"헬퍼 스크립트를 찾을 수 없음: {p}")
    return p


def _python_path() -> Path:
    """현재 실행 중인 Python 인터프리터 경로 (존재 확인 포함)."""
    p = Path(sys.executable)
    if not p.exists():
        raise RuntimeError(f"Python 실행 파일을 찾을 수 없음: {p}")
    return p


def _command_template() -> str:
    """레지스트리/Info.plist/.desktop 에 기록할 실행 명령.

    - Frozen(.exe) 모드: 자기 자신(`sys.executable`) 만 호출, %1 은 URL 인자
    - 개발 모드: 파이썬 + 스크립트 경로 호출
    """
    if _is_frozen():
        return f'"{sys.executable}" "%1"'
    return f'"{_python_path()}" "{_script_path()}" "%1"'


def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# ── Windows ─────────────────────────────────────────────────────────────────

def install_windows() -> None:
    """HKCU\\Software\\Classes\\opsnav 에 URL 핸들러 등록."""
    import winreg  # type: ignore[import-not-found]

    command = _command_template()
    icon_target = sys.executable  # .exe 또는 python.exe

    base_key_path = rf"Software\Classes\{SCHEME}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base_key_path) as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, f"URL:{APP_NAME}")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base_key_path}\DefaultIcon") as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, f'"{icon_target}",0')

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base_key_path}\shell\open\command") as k:
        winreg.SetValueEx(k, None, 0, winreg.REG_SZ, command)

    _save_state({
        "platform": "windows",
        "scheme": SCHEME,
        "registry_key": f"HKEY_CURRENT_USER\\{base_key_path}",
        "command": command,
        "frozen": _is_frozen(),
    })
    print(f"OK: opsnav:// 스킴을 레지스트리에 등록했습니다 → {sys.executable}")


def uninstall_windows() -> None:
    import winreg  # type: ignore[import-not-found]

    base_key_path = rf"Software\Classes\{SCHEME}"
    # 하위 키부터 재귀 삭제
    def _delete_recursive(root, path: str):
        try:
            with winreg.OpenKey(root, path, 0, winreg.KEY_READ) as k:
                i = 0
                subkeys = []
                while True:
                    try:
                        subkeys.append(winreg.EnumKey(k, i))
                        i += 1
                    except OSError:
                        break
            for sub in subkeys:
                _delete_recursive(root, f"{path}\\{sub}")
            winreg.DeleteKey(root, path)
        except FileNotFoundError:
            pass

    _delete_recursive(winreg.HKEY_CURRENT_USER, base_key_path)
    print("OK: opsnav:// 레지스트리 항목을 제거했습니다.")


# ── macOS ───────────────────────────────────────────────────────────────────

def _mac_app_dir() -> Path:
    return Path.home() / "Applications" / f"{APP_NAME}.app"


def install_macos() -> None:
    app_dir = _mac_app_dir()
    contents = app_dir / "Contents"
    macos_dir = contents / "MacOS"
    resources_dir = contents / "Resources"

    macos_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)

    # launcher 쉘 스크립트 (앱 번들 실행 파일)
    launcher = macos_dir / "launcher"
    if _is_frozen():
        launcher_script = f"""#!/bin/bash
# Ops-Navigator Helper — opsnav:// URL 수신 시 .exe(.app 내부 바이너리) 실행
exec {shlex.quote(str(sys.executable))} "$1"
"""
    else:
        python_exe = _python_path()
        script = _script_path()
        launcher_script = f"""#!/bin/bash
# Ops-Navigator Helper — opsnav:// URL 수신 시 Python 헬퍼 실행
exec {shlex.quote(str(python_exe))} {shlex.quote(str(script))} "$1"
"""
    launcher.write_text(launcher_script, encoding="utf-8")
    launcher.chmod(0o755)

    # Info.plist
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleDisplayName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>{BUNDLE_ID}</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSUIElement</key>
    <true/>
    <key>CFBundleURLTypes</key>
    <array>
        <dict>
            <key>CFBundleURLName</key>
            <string>{APP_NAME}</string>
            <key>CFBundleURLSchemes</key>
            <array>
                <string>{SCHEME}</string>
            </array>
        </dict>
    </array>
</dict>
</plist>
"""
    (contents / "Info.plist").write_text(plist, encoding="utf-8")

    # LaunchServices 재등록 (lsregister)
    lsregister = (
        "/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/"
        "LaunchServices.framework/Versions/A/Support/lsregister"
    )
    if os.path.exists(lsregister):
        subprocess.run([lsregister, "-f", str(app_dir)], check=False)

    _save_state({
        "platform": "macos",
        "scheme": SCHEME,
        "app_path": str(app_dir),
    })
    print(f"OK: {app_dir} 생성 및 LaunchServices 등록 완료")


def uninstall_macos() -> None:
    app_dir = _mac_app_dir()
    if app_dir.exists():
        shutil.rmtree(app_dir)
        print(f"OK: {app_dir} 삭제 완료")
    else:
        print("이미 제거된 상태입니다.")

    # LaunchServices DB 리빌드
    lsregister = (
        "/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/"
        "LaunchServices.framework/Versions/A/Support/lsregister"
    )
    if os.path.exists(lsregister):
        subprocess.run([lsregister, "-kill", "-r", "-domain", "local", "-domain", "user"], check=False)


# ── Linux ───────────────────────────────────────────────────────────────────

def _linux_desktop_file() -> Path:
    return Path.home() / ".local" / "share" / "applications" / "opsnav-helper.desktop"


def install_linux() -> None:
    desktop_file = _linux_desktop_file()
    desktop_file.parent.mkdir(parents=True, exist_ok=True)

    if _is_frozen():
        exec_line = f"{shlex.quote(str(sys.executable))} %u"
    else:
        python_exe = _python_path()
        script = _script_path()
        exec_line = f"{shlex.quote(str(python_exe))} {shlex.quote(str(script))} %u"

    entry = f"""[Desktop Entry]
Type=Application
Name={APP_NAME}
Comment=Ops-Navigator Teams 로그인 헬퍼
Exec={exec_line}
Terminal=true
NoDisplay=true
MimeType=x-scheme-handler/{SCHEME};
Categories=Utility;
"""
    desktop_file.write_text(entry, encoding="utf-8")
    desktop_file.chmod(0o644)

    # desktop 데이터베이스 업데이트 + scheme 연결
    if shutil.which("update-desktop-database"):
        subprocess.run(
            ["update-desktop-database", str(desktop_file.parent)], check=False,
        )
    if shutil.which("xdg-mime"):
        subprocess.run(
            ["xdg-mime", "default", "opsnav-helper.desktop", f"x-scheme-handler/{SCHEME}"],
            check=False,
        )

    _save_state({
        "platform": "linux",
        "scheme": SCHEME,
        "desktop_file": str(desktop_file),
    })
    print(f"OK: {desktop_file} 등록 완료")


def uninstall_linux() -> None:
    desktop_file = _linux_desktop_file()
    if desktop_file.exists():
        desktop_file.unlink()
        print(f"OK: {desktop_file} 삭제 완료")
    else:
        print("이미 제거된 상태입니다.")

    if shutil.which("update-desktop-database"):
        subprocess.run(
            ["update-desktop-database", str(desktop_file.parent)], check=False,
        )


# ── 엔트리 포인트 ──────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="opsnav:// URL scheme 등록/제거")
    parser.add_argument("--uninstall", action="store_true", help="등록 해제")
    args = parser.parse_args()

    platform = sys.platform
    try:
        if args.uninstall:
            if platform.startswith("win"):
                uninstall_windows()
            elif platform == "darwin":
                uninstall_macos()
            elif platform.startswith("linux"):
                uninstall_linux()
            else:
                print(f"ERROR: 지원하지 않는 플랫폼: {platform}", file=sys.stderr)
                return 2

            if STATE_FILE.exists():
                STATE_FILE.unlink()
        else:
            if platform.startswith("win"):
                install_windows()
            elif platform == "darwin":
                install_macos()
            elif platform.startswith("linux"):
                install_linux()
            else:
                print(f"ERROR: 지원하지 않는 플랫폼: {platform}", file=sys.stderr)
                return 2

            print("")
            print("다음 단계: 웹 관리자 UI의 'Teams 메시지' 카드에서 'Teams 로그인' 버튼 클릭.")
            print("브라우저가 'Ops-Navigator Helper를 여시겠습니까?' 라고 물어보면 허용하세요.")
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
