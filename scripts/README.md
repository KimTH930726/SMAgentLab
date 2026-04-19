# Ops-Navigator 운영 스크립트

사용자 PC에서 실행하는 헬퍼 도구 모음. Docker 컨테이너의 백엔드와는 HTTP로만 통신한다.

## teams_desktop_login.py

Teams 수집 기능에 필요한 IC3/CSA 토큰을 사용자 브라우저에서 캡처하여 백엔드로 전송한다.

### 1회 설치

```bash
pip install -r scripts/requirements.txt
playwright install chromium
```

### 실행

```bash
# 사용자명 + 비밀번호로 로그인
python scripts/teams_desktop_login.py --api-url http://localhost:8000

# 이미 가진 JWT로 바로 진행
python scripts/teams_desktop_login.py --api-url http://localhost:8000 --jwt eyJhbGc...
```

크로미엄 창이 열리면 Teams에 로그인하세요. 채팅방 목록이 보이는 순간 자동으로 닫히고 토큰이 전송됩니다.

### 주의

- 토큰은 백엔드 프로세스 메모리에만 저장됩니다. 백엔드 재시작 또는 토큰 만료 시 재실행하세요.
- HTTP로 외부 호스트에 접속하면 경고가 표시됩니다 — production에서는 반드시 HTTPS를 사용하세요.
