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
playwright install chromium
```

## 사용

```bash
# 사용자명 + 비밀번호로 Ops-Navigator에 로그인
python teams_desktop_login.py --api-url http://localhost:8000

# 이미 가진 JWT 토큰으로 바로 진행
python teams_desktop_login.py --api-url http://localhost:8000 --jwt eyJhbGc...
```

실행하면 크로미엄 창이 열립니다. Teams에 로그인하세요. 채팅방 목록이 로드되면
자동으로 창이 닫히고 토큰이 백엔드로 전송됩니다.

## 보안

- 토큰은 Ops-Navigator 백엔드의 메모리에만 저장됩니다 (DB/디스크 저장 없음).
- API URL이 http(s)이 아닌 경우, 또는 HTTP로 localhost 외부에 접속하는 경우 경고를 띄웁니다.
"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import sys
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


# ── Playwright 토큰 캡처 ────────────────────────────────────────────────────

def capture_teams_tokens(timeout: int = 150) -> dict | None:
    """Teams 웹에 로그인하여 IC3/CSA 토큰 + 채팅방 목록을 캡처."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "ERROR: playwright 패키지가 없습니다. 다음 명령으로 설치하세요:\n"
            "  pip install playwright\n"
            "  playwright install chromium",
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
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
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

        logger.info("Waiting for token capture (max %ds)...", timeout)
        try:
            page.wait_for_function(
                """() => {
                    return document.querySelector('[data-tid="chat-list"]')
                        || document.querySelector('[class*="chatList"]')
                        || document.querySelectorAll('[data-tid]').length > 10;
                }""",
                timeout=timeout * 1000,
            )
            page.wait_for_timeout(3000)
        except Exception:
            logger.warning("UI detection timed out, checking captured tokens...")

        browser.close()

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
        print(f"ERROR: --api-url 은 http:// 또는 https:// 로 시작해야 합니다 (받은 값: {api_url})", file=sys.stderr)
        sys.exit(2)

    host = (parsed.hostname or "").lower()
    is_localhost = host in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme == "http" and not is_localhost:
        print(
            f"WARNING: HTTP로 외부 호스트({host})에 접속합니다. "
            "Teams 토큰이 평문으로 전송됩니다. HTTPS 사용을 권장합니다.",
            file=sys.stderr,
        )


# ── main ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Teams 데스크톱 로그인 헬퍼 — Playwright 로그인 → Ops-Navigator 백엔드에 토큰 전송",
    )
    parser.add_argument("--api-url", required=True,
                        help="Ops-Navigator 백엔드 URL (예: http://localhost:8000)")
    parser.add_argument("--username", help="Ops-Navigator 계정명 (--jwt 미지정 시 필요)")
    parser.add_argument("--jwt", help="이미 가진 JWT access_token (--username 대신 사용)")
    parser.add_argument("--timeout", type=int, default=150,
                        help="Teams 로그인 대기 타임아웃(초) — 기본 150")
    args = parser.parse_args()

    warn_if_insecure(args.api_url)

    # JWT 확보
    if args.jwt:
        jwt = args.jwt
    else:
        username = args.username or input("Ops-Navigator 계정: ").strip()
        if not username:
            print("ERROR: 사용자명이 필요합니다.", file=sys.stderr)
            return 2
        password = getpass.getpass("비밀번호: ")
        print("Ops-Navigator 로그인 중...", file=sys.stderr)
        try:
            jwt = login_ops_navigator(args.api_url, username, password)
        except RuntimeError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print("OK", file=sys.stderr)

    # Teams 로그인 (브라우저 창)
    print(f"Teams 브라우저 로그인 시작 (최대 {args.timeout}초)...", file=sys.stderr)
    print("→ 열리는 크로미엄 창에서 Teams에 로그인하세요.", file=sys.stderr)
    tokens = capture_teams_tokens(args.timeout)
    if not tokens:
        print("ERROR: 토큰 캡처 실패", file=sys.stderr)
        return 1

    # 백엔드로 전송
    print("토큰을 백엔드로 전송 중...", file=sys.stderr)
    try:
        result = post_teams_tokens(args.api_url, jwt, tokens)
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    chat_count = result.get("chat_count", len(tokens.get("chats", [])))
    print(f"완료: 채팅방 {chat_count}개 등록. 이제 웹 관리자 UI에서 수집을 시작하세요.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
