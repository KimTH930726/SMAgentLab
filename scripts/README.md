# Ops-Navigator 운영 스크립트

사용자 PC에서 실행하는 헬퍼 도구 모음. Docker 컨테이너의 백엔드와는 HTTP로만 통신한다.

## Teams 수집 — 배포 방식

### 사용자 관점

웹 관리자 UI의 **"Teams 메시지" 카드 → "헬퍼 다운로드" 버튼**으로 `OpsNavHelper.exe` 를 받는다.
다운로드한 `.exe` 를 더블클릭하면 자동으로 `opsnav://` URL 핸들러가 등록되고, 이후 **"Teams 로그인" 버튼**이 작동한다.

- **사전 조건**: Windows + Chrome 설치
- **Python 불필요** — `.exe` 안에 Python 인터프리터 + Playwright 번들됨
- **첫 실행 시 Windows SmartScreen 경고** 발생 가능 → "추가 정보" → "실행" 선택 (사내 도구, 서명 미적용)

### 개발자 관점 (`.exe` 빌드)

Windows 에서 PyInstaller 로 빌드한다.

```bash
# 1. 사전 준비 (최초 1회)
pip install playwright==1.49.1 pyinstaller

# 2. 빌드 (cmd)
cd scripts
build_exe.cmd

# 또는 PyInstaller 직접 실행
pyinstaller OpsNavHelper.spec
```

결과: `scripts/dist/OpsNavHelper.exe` (약 42MB).

이 파일은 `docker-compose.yml` 의 볼륨 마운트를 통해 백엔드 컨테이너의
`/app/helper_assets/dist/OpsNavHelper.exe` 로 노출되며,
`GET /api/teams-collect/helper/download` 엔드포인트가 그대로 반환한다.

### 개발 모드 실행 (Python 직접)

`.exe` 빌드 없이 Python 으로 바로 실행 가능:

```bash
pip install playwright==1.49.1
python scripts/install_url_handler.py   # URL 핸들러 등록
# 이후 웹에서 "Teams 로그인" 버튼 클릭 → teams_desktop_login.py 가 실행됨
```

## 파일 구성

| 파일 | 역할 |
|------|------|
| `opsnav_helper_entry.py` | PyInstaller 빌드 엔트리. argv 분기로 URL 모드/설치/제거 처리 |
| `teams_desktop_login.py` | Playwright 로 Teams 로그인 + 토큰 캡처 + 백엔드 POST |
| `install_url_handler.py` | `opsnav://` URL scheme 을 OS 에 등록/제거 |
| `OpsNavHelper.spec` | PyInstaller 빌드 설정 |
| `build_exe.cmd` | Windows 빌드 편의 스크립트 |
| `dist/OpsNavHelper.exe` | 빌드 결과 — **커밋됨** (백엔드가 이 경로 서빙) |
| `build/` | PyInstaller 중간 산출물 — `.gitignore` |
| `export-images.sh` / `import-and-run.sh` / `update-images.sh` | Docker 이미지 배포 (Teams 수집과 무관) |

## 제거

사용자 PC 에서:
```bash
# URL 핸들러 제거
OpsNavHelper.exe --uninstall

# .exe 파일 자체 삭제 — 원하는 곳에서 직접 삭제
```

## 주의

- Teams 토큰은 백엔드 프로세스 메모리에만 저장된다 (DB/디스크 영속화 없음).
- `opsnav://` URL 모드에서 JWT 가 argv 로 전달되므로 공유 PC 환경에서는 권장하지 않는다.
- `.exe` 는 서명되지 않음 → 외부 배포 시 SmartScreen 경고. 사내 배포에서는 IT 화이트리스트 등록을 권장.
