# PyInstaller spec — OpsNavHelper.exe
#
# 빌드:
#   pyinstaller OpsNavHelper.spec
#
# 결과:
#   dist/OpsNavHelper.exe
#
# 이 spec 은 scripts/ 디렉토리에서 실행되는 것을 전제로 한다.

# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

block_cipher = None

HERE = Path(SPECPATH).resolve()
ENTRY = str(HERE / "opsnav_helper_entry.py")
SIBLING_MODULES = [
    str(HERE / "teams_desktop_login.py"),
    str(HERE / "install_url_handler.py"),
]

# Playwright 의 Node.js 드라이버(node.exe, cli.js 등)와 .js 소스를 전부 번들.
# PyInstaller 가 playwright 패키지용 자동 훅을 제공하지 않으므로 명시 수집 필요.
pw_datas, pw_binaries, pw_hiddenimports = collect_all("playwright")

a = Analysis(
    [ENTRY],
    pathex=[str(HERE)],
    binaries=pw_binaries,
    datas=pw_datas,
    hiddenimports=[
        # sibling 모듈 — Analysis 가 entry 에서 동적 import 를 추적하지 못할 수 있어 명시
        "teams_desktop_login",
        "install_url_handler",
        # Playwright sync API
        "playwright.sync_api",
        "playwright._impl._api_types",
    ] + pw_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 불필요한 대형 모듈 제외 (크기 감소)
        "tkinter",
        "unittest",
        "pydoc",
        "pytest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="OpsNavHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX 압축 비활성화 (AV 오탐 원인, 속도 거의 차이 없음)
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,        # 콘솔 창 — 진행 상황/에러 확인용
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
