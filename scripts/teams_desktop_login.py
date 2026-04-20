"""Teams 데스크톱 로그인 헬퍼 — 사용자 PC에서 실행.

Ops-Navigator 백엔드(Docker 등)는 브라우저를 띄울 수 없으므로, 이 스크립트를
사용자의 로컬 PC에서 실행하여:
  1) Playwright로 Teams 웹에 로그인
  2) 네트워크 요청에서 IC3/CSA 토큰과 채팅방 목록 캡처
  3) Ops-Navigator 백엔드 API에 토큰을 POST (사용자 JWT로 인증)

백엔드는 토큰을 인메모리 스토어에 저장하며, 이후 사용자는 관리자 UI에서 바로
Teams 채팅방/메시지를 조회할 수 있다.

## 사전 준비 (1회)

```bash
pip install playwright
```

Chrome이 설치돼 있다는 전제이므로 번들 Chromium은 다운로드하지 않는다.

추가로 `opsnav://` URL 프로토콜을 등록하면 관리자 UI의 "Teams 로그인" 버튼
한 번으로 이 스크립트가 자동 실행됩니다:
```bash
python scripts/install_url_handler.py
```

## 사용

### URL 모드 (opsnav:// scheme — 브라우저 버튼에서 자동 실행)

    opsnav://teams-login?api_url=<URL>&jwt=<JWT>

이 형태가 argv[1]로 전달되면 CLI 인자 대신 URL에서 파라미터를 꺼내 바로 실행.

### CLI 모드 (수동 실행 / 고급 사용자)

```bash
# 사용자명 + 비밀번호로 Ops-Navigator에 로그인
python teams_desktop_login.py --api-url http://localhost:8501

# 이미 가진 JWT로 바로 진행
python teams_desktop_login.py --api-url http://localhost:8501 --jwt eyJhbGc...
```

실행하면 사용자의 Chrome 창이 열립니다.
Teams에 로그인하세요. 채팅방 목록이 로드되면 자동으로 창이 닫히고 토큰이 백엔드로 전송됩니다.

## 보안

- 토큰은 Ops-Navigator 백엔드의 메모리에만 저장됩니다 (DB/디스크 저장 없음).
- API URL이 http(s)이 아닌 경우, 또는 HTTP로 localhost 외부에 접속하는 경우 경고를 띄웁니다.
- URL 모드에서 JWT가 argv로 전달되므로, 공유 환경에서는 OS 프로세스 목록에서 잠시 보일 수 있습니다.
  개인 PC 환경에서 사용 권장.
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("teams_desktop_login")


# ── HTTP 헬퍼 (stdlib only) ─────────────────────────────────────────────────

def _http_json(
    url: str,
    method: str = "GET",
    data: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
) -> dict:
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read()).get("detail", "")
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code} {url}: {detail or e.reason}") from e
    except URLError as e:
        raise RuntimeError(f"네트워크 오류 ({url}): {e.reason}") from e


def login_ops_navigator(api_url: str, username: str, password: str) -> str:
    """Ops-Navigator에 로그인하여 JWT access_token을 반환."""
    url = f"{api_url.rstrip('/')}/api/auth/login"
    result = _http_json(url, method="POST", data={"username": username, "password": password})
    token = result.get("access_token")
    if not token:
        raise RuntimeError("로그인 응답에 access_token이 없습니다.")
    return token


def post_teams_tokens(api_url: str, jwt: str, tokens: dict) -> dict:
    url = f"{api_url.rstrip('/')}/api/teams-collect/auth/tokens"
    return _http_json(
        url,
        method="POST",
        data={
            "ic3_token": tokens.get("ic3_token", ""),
            "csa_token": tokens.get("csa_token", ""),
            "chats": tokens.get("chats", []),
        },
        headers={"Authorization": f"Bearer {jwt}"},
    )


# ── Playwright 브라우저 런칭 (channel 폴백) ────────────────────────────────

def _launch_browser(p):
    """사용자 Chrome 실행. 실패 시 명확한 에러."""
    try:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        logger.info("Browser launched — Chrome")
        return browser
    except Exception as e:
        raise RuntimeError(
            "Chrome을 실행할 수 없습니다. Chrome이 설치돼 있는지 확인하세요.",
        ) from e


# ── Playwright 토큰 캡처 ────────────────────────────────────────────────────

def capture_teams_tokens(timeout: int = 150) -> dict | None:
    """Teams 웹에 로그인하여 IC3/CSA 토큰 + 채팅방 목록을 캡처."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "ERROR: playwright 패키지가 없습니다. 다음 명령으로 설치하세요:\n"
            "  pip install playwright",
            file=sys.stderr,
        )
        sys.exit(2)

    captured: dict = {"ic3_token": "", "csa_token": "", "chats": []}

    def on_request(request):
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return
        url = request.url
        if "/api/chatsvc/" in url and not captured["ic3_token"]:
            captured["ic3_token"] = auth[7:]
            logger.info("IC3 token captured from: %s", url[:80])
        elif "/api/csa/" in url and not captured["csa_token"]:
            captured["csa_token"] = auth[7:]
            logger.info("CSA token captured from: %s", url[:80])

    def on_response(response):
        if "/api/csa/" not in response.url:
            return
        if captured["chats"]:
            return
        try:
            body = response.body()
            data = json.loads(body)
            chats_data = data.get("chats", [])
            if not chats_data:
                return
            for chat in chats_data:
                chat_id = chat.get("id", "")
                if chat_id.startswith("48:"):
                    continue
                title = chat.get("title")
                is_one_on_one = chat.get("isOneOnOne", False)
                members = chat.get("members", [])
                member_count = len(members) if members else 0

                last_msg = chat.get("lastMessage", {})
                if title:
                    label = title
                elif is_one_on_one:
                    label = last_msg.get("imDisplayName") or chat_id[:40]
                else:
                    sender = last_msg.get("imDisplayName") or ""
                    if sender and member_count > 1:
                        short_name = sender.split("(")[0].strip()
                        label = f"{short_name} 외 {member_count - 1}명"
                    else:
                        label = f"그룹 채팅 ({member_count}명)"

                captured["chats"].append({
                    "id": chat_id,
                    "label": label,
                    "members": str(member_count),
                    "has_custom_title": title is not None,
                    "is_one_on_one": is_one_on_one,
                })
            logger.info(
                "Chat list captured: %d chats from %s",
                len(captured["chats"]), response.url[:80],
            )
        except Exception as e:
            logger.debug("CSA response parsing failed: %s", e)

    with sync_playwright() as p:
        browser = _launch_browser(p)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="ko-KR",
        )
        page = context.new_page()
        page.on("request", on_request)
        page.on("response", on_response)

        logger.info("Navigating to Teams...")
        page.goto("https://teams.microsoft.com", wait_until="domcontentloaded")
        page.bring_to_front()

        logger.info("Waiting for token capture (max %ds)... 로그인을 진행하세요.", timeout)
        # Python-side 폴링 — wait_for_function 은 login 리다이렉트 시 execution context 파괴로
        # 예외가 나서 브라우저가 즉시 닫히는 문제가 있음. captured 딕셔너리를 직접 확인한다.
        deadline = time.time() + timeout
        token_logged_at: float | None = None

        while time.time() < deadline:
            try:
                page.wait_for_timeout(500)  # 500ms 대기 (블로킹 sleep 대신 playwright 이벤트 처리)
            except Exception:
                # 사용자가 브라우저를 수동으로 닫았거나 페이지가 파괴됨
                logger.info("Browser closed by user or navigation, exiting poll loop")
                break

            if captured["ic3_token"] and token_logged_at is None:
                token_logged_at = time.time()
                logger.info("IC3 token captured — 채팅방 목록 도착까지 최대 15초 대기")

            # 양쪽 다 잡힘 → 완료
            if captured["ic3_token"] and captured["chats"]:
                try:
                    page.wait_for_timeout(1000)  # 추가 메시지 수집용 grace period
                except Exception:
                    pass
                logger.info("Both tokens captured — closing browser")
                break

            # 토큰만 잡히고 chats 가 15초 동안 안 오면 그대로 진행
            if token_logged_at and (time.time() - token_logged_at) > 15:
                logger.warning("Chat list not captured in 15s, proceeding with tokens only")
                break

        try:
            browser.close()
        except Exception:
            pass

    if captured["ic3_token"]:
        logger.info(
            "Tokens captured: IC3=%s, CSA=%s, Chats=%d",
            "OK" if captured["ic3_token"] else "MISSING",
            "OK" if captured["csa_token"] else "MISSING",
            len(captured["chats"]),
        )
        return captured

    logger.error("No tokens captured")
    return None


# ── URL 검증 ────────────────────────────────────────────────────────────────

def warn_if_insecure(api_url: str) -> None:
    parsed = urllib.parse.urlparse(api_url)
    if parsed.scheme not in ("http", "https"):
        print(
            f"ERROR: api_url은 http:// 또는 https:// 로 시작해야 합니다 (받은 값: {api_url})",
            file=sys.stderr,
        )
        sys.exit(2)

    host = (parsed.hostname or "").lower()
    is_localhost = host in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme == "http" and not is_localhost:
        print(
            f"WARNING: HTTP로 외부 호스트({host})에 접속합니다. "
            "Teams 토큰이 평문으로 전송됩니다. HTTPS 사용을 권장합니다.",
            file=sys.stderr,
        )


# ── URL 모드 파싱 (opsnav://teams-login?api_url=...&jwt=...) ────────────────

def parse_opsnav_url(url: str) -> dict:
    """opsnav:// URL을 파싱하여 action과 파라미터 반환.

    성공 시: {"action": str, "api_url": str, "jwt": str, "timeout": int}
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "opsnav":
        raise ValueError(f"opsnav:// 스킴이 아님: {parsed.scheme}")

    # action은 host 또는 path의 첫 세그먼트
    action = parsed.hostname or parsed.path.strip("/").split("/")[0] or ""
    qs = urllib.parse.parse_qs(parsed.query)
    api_url = qs.get("api_url", [""])[0]
    jwt = qs.get("jwt", [""])[0]
    timeout_str = qs.get("timeout", ["150"])[0]
    try:
        timeout = int(timeout_str)
    except ValueError:
        timeout = 150
    return {"action": action, "api_url": api_url, "jwt": jwt, "timeout": timeout}


# ── 에러 시 콘솔 유지 (URL 모드 전용) ───────────────────────────────────────

def _pause_on_error_if_url_mode(is_url_mode: bool) -> None:
    """URL 모드로 실행됐고 에러로 종료할 때, 콘솔이 바로 닫히면 사용자가 메시지를
    못 읽으니 Enter 입력을 대기한다. tty가 아니면 skip."""
    if not is_url_mode or not sys.stdin.isatty():
        return
    try:
        input("\n종료하려면 Enter를 누르세요...")
    except Exception:
        pass


# ── main ────────────────────────────────────────────────────────────────────

def run(api_url: str, jwt: str | None, username: str | None, timeout: int, is_url_mode: bool) -> int:
    warn_if_insecure(api_url)

    # JWT 확보
    if not jwt:
        username = username or input("Ops-Navigator 계정: ").strip()
        if not username:
            print("ERROR: 사용자명이 필요합니다.", file=sys.stderr)
            return 2
        password = getpass.getpass("비밀번호: ")
        print("Ops-Navigator 로그인 중...", file=sys.stderr)
        try:
            jwt = login_ops_navigator(api_url, username, password)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print("OK", file=sys.stderr)

    # Teams 로그인 (브라우저 창)
    print(f"Teams 브라우저 로그인 시작 (최대 {timeout}초)...", file=sys.stderr)
    print("→ 열리는 창에서 Teams에 로그인하세요.", file=sys.stderr)
    try:
        tokens = capture_teams_tokens(timeout)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not tokens:
        print("ERROR: 토큰 캡처 실패", file=sys.stderr)
        return 1

    # 백엔드로 전송
    print("토큰을 백엔드로 전송 중...", file=sys.stderr)
    try:
        result = post_teams_tokens(api_url, jwt, tokens)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    chat_count = result.get("chat_count", len(tokens.get("chats", [])))
    print(
        f"완료: 채팅방 {chat_count}개 등록. 이제 웹 관리자 UI에서 수집을 시작하세요.",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    # URL 모드: argv가 단일 opsnav:// URL
    if len(sys.argv) == 2 and sys.argv[1].startswith("opsnav://"):
        try:
            params = parse_opsnav_url(sys.argv[1])
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            _pause_on_error_if_url_mode(True)
            return 2

        if params["action"] != "teams-login":
            print(f"ERROR: 지원하지 않는 action: {params['action']}", file=sys.stderr)
            _pause_on_error_if_url_mode(True)
            return 2
        if not params["api_url"] or not params["jwt"]:
            print("ERROR: URL에 api_url 또는 jwt 파라미터가 없습니다.", file=sys.stderr)
            _pause_on_error_if_url_mode(True)
            return 2

        code = run(params["api_url"], params["jwt"], None, params["timeout"], is_url_mode=True)
        if code != 0:
            _pause_on_error_if_url_mode(True)
        return code

    # CLI 모드: argparse
    parser = argparse.ArgumentParser(
        description="Teams 데스크톱 로그인 헬퍼 — Playwright 로그인 → Ops-Navigator 백엔드에 토큰 전송",
    )
    parser.add_argument("--api-url", required=True,
                        help="Ops-Navigator 백엔드 URL (예: http://localhost:8501)")
    parser.add_argument("--username", help="Ops-Navigator 계정명 (--jwt 미지정 시 필요)")
    parser.add_argument("--jwt", help="이미 가진 JWT access_token (--username 대신 사용)")
    parser.add_argument("--timeout", type=int, default=150,
                        help="Teams 로그인 대기 타임아웃(초) — 기본 150")
    args = parser.parse_args()

    return run(args.api_url, args.jwt, args.username, args.timeout, is_url_mode=False)


if __name__ == "__main__":
    sys.exit(main())
