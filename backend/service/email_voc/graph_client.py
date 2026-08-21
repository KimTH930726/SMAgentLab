"""Microsoft Graph API 클라이언트 — VOC 메일 수집 (docs/email-analysis-channel-plan.md §7, §11 Track A #5).

§7 Q10(Azure AD 앱 등록 + Mail.Read 권한 + RBAC 스코프) IT 승인 전이라 실제 자격증명으로
연결 테스트는 불가능하다. 코드만 완성해두고 단위 테스트는 mock으로 검증한다 — §11
"두 트랙이 만나는 지점": 승인 후 client_id/secret/tenant_id만 꽂으면 바로 동작한다.

httpx로 REST 엔드포인트를 직접 호출(§7.1) — 기존 web_crawler.py(Confluence 연동)와
동일한 스타일을 유지하기 위해 무거운 msgraph-sdk 대신 이 방식을 택했다.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx
import msal

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPE = ["https://graph.microsoft.com/.default"]
# 대량 조회 시 504(Gateway Timeout) 방지 — 본문 분석에 필요한 필드만 선택(§7.1)
_SELECT_FIELDS = "id,subject,from,receivedDateTime,body"
_PAGE_SIZE = 50
FETCH_TIMEOUT = 30.0
# @odata.nextLink를 무한정 따라가는 걸 방지하는 안전장치 — 정상 경로라면 §9의
# date_from~date_to(최대 90일) 범위 내 메일이라 이 한도에 걸릴 일이 거의 없다.
_MAX_PAGES = 200
_MAX_RETRIES = 3


class GraphAuthError(Exception):
    """client_credentials 토큰 발급 실패."""


class GraphApiError(Exception):
    """Graph API 메시지 조회 실패."""


async def get_access_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """client_credentials 플로우로 앱 전용 토큰 발급.

    msal은 동기(blocking) API라 이벤트 루프를 막지 않도록 executor로 감싼다
    (shared/embedding.py의 embedding_service.embed()와 동일한 패턴).
    """
    def _acquire() -> dict:
        app = msal.ConfidentialClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
            client_credential=client_secret,
        )
        return app.acquire_token_for_client(scopes=_SCOPE)

    result = await asyncio.get_running_loop().run_in_executor(None, _acquire)
    if "access_token" not in result:
        raise GraphAuthError(result.get("error_description", "토큰 발급 실패 (원인 불명)"))
    return result["access_token"]


async def fetch_messages(
    mailbox_upn: str,
    access_token: str,
    received_after: datetime,
    received_before: Optional[datetime] = None,
    *,
    folder_id: Optional[str] = None,
) -> list[dict]:
    """지정 기간 동안 대상 메일함의 메시지를 조회한다 (§7.1 — $filter + nextLink 페이지네이션).

    개인 메일함 전용 /me/messages가 아니라 /users/{mailbox}/messages 형태를 쓴다 —
    애플리케이션 권한으로 타 메일함(공용 접수함)에 접근할 때 필수인 형태다.

    folder_id: 지정하면 메일함 전체가 아니라 해당 폴더(예: 관리자가 만든 "VOC" 폴더)만
    조회한다(`/mailFolders/{folder_id}/messages`) — 실사용 중 메일함 전체를 훑으면
    스팸/사내공지 등 무관한 메일까지 관련지식 필터를 태우게 되는 게 확인돼, 관리자가
    특정 폴더로 범위를 좁힐 수 있도록 추가(list_mail_folders()로 폴더 목록 조회 후
    선택). None이면 기존과 동일하게 메일함 전체 조회.

    Returns:
        [{"id", "subject", "sender", "received_at", "body"}, ...]
    """
    filter_parts = [f"receivedDateTime ge {received_after.isoformat()}"]
    if received_before:
        filter_parts.append(f"receivedDateTime le {received_before.isoformat()}")
    params: Optional[dict] = {
        "$filter": " and ".join(filter_parts),
        "$select": _SELECT_FIELDS,
        "$top": _PAGE_SIZE,
    }
    headers = {
        "Authorization": f"Bearer {access_token}",
        # HTML 대신 텍스트로 본문 수신 — 기존 BeautifulSoup HTML 파서를 재사용하지 않아도 됨
        "Prefer": 'outlook.body-content-type="text"',
    }

    messages: list[dict] = []
    base = f"{_GRAPH_BASE}/users/{mailbox_upn}"
    url: Optional[str] = f"{base}/mailFolders/{folder_id}/messages" if folder_id else f"{base}/messages"

    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
        for page_num in range(_MAX_PAGES):
            if url is None:
                break
            resp = await _get_with_retry(client, url, params, headers)
            if resp.status_code != 200:
                raise GraphApiError(f"Graph API 조회 실패 (HTTP {resp.status_code}): {resp.text[:300]}")
            data = resp.json()
            for m in data.get("value", []):
                messages.append({
                    "id": m["id"],
                    "subject": m.get("subject", ""),
                    "sender": (m.get("from") or {}).get("emailAddress", {}).get("address", ""),
                    "received_at": m.get("receivedDateTime"),
                    "body": (m.get("body") or {}).get("content", ""),
                })
            url = data.get("@odata.nextLink")
            params = None  # nextLink에 쿼리스트링이 이미 포함되어 있어 재전달하면 안 됨
        else:
            logger.warning("Graph API 페이지네이션이 %d페이지 한도에 도달 — 조회를 중단합니다: %s", _MAX_PAGES, mailbox_upn)

    logger.info("Graph API 메일 수집 완료: %s, %d건", mailbox_upn, len(messages))
    return messages


async def list_mail_folders(mailbox_upn: str, access_token: str) -> list[dict]:
    """관리자 화면에서 라우팅을 설정할 때 "이 메일함의 어느 폴더로 좁힐지" 선택할 수
    있도록 실제 폴더 목록을 조회한다.

    최상위 폴더 + 받은 편지함(Inbox) 바로 아래 하위 폴더까지 조회한다 — 애초에
    "VOC 전용 폴더는 보통 최상위에 만든다"고 가정해 최상위만 조회했었는데, 실사용
    중 "받은 메일함 하위에 VOC 폴더를 만들었는데 안 보인다"는 실제 사례로 이 가정이
    틀렸다는 게 확인됐다 — Outlook에서 새 폴더를 만들 때 기본 위치가 받은 편지함
    하위라 오히려 이쪽이 더 흔한 사용 패턴이었다. 더 깊은 하위 폴더(Inbox 하위의
    하위 등)까지 전부 재귀 조회하지는 않는다 — 그 정도로 깊이 중첩하는 경우는
    드물고, 필요해지면 그때 재귀 조회로 확장.

    Returns:
        [{"id", "display_name", "unread_count", "total_count"}, ...] — Inbox 하위
        폴더는 display_name을 "받은 편지함 / 폴더명"으로 표시해 최상위 폴더와 구분.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"$select": "id,displayName,unreadItemCount,totalItemCount", "$top": 100}
    top_level_url = f"{_GRAPH_BASE}/users/{mailbox_upn}/mailFolders"
    inbox_children_url = f"{_GRAPH_BASE}/users/{mailbox_upn}/mailFolders/inbox/childFolders"

    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT) as client:
        resp = await _get_with_retry(client, top_level_url, params, headers)
        if resp.status_code != 200:
            raise GraphApiError(f"폴더 목록 조회 실패 (HTTP {resp.status_code}): {resp.text[:300]}")
        top_level = resp.json().get("value", [])

        child_resp = await _get_with_retry(client, inbox_children_url, params, headers)
        # Inbox 하위 폴더가 하나도 없는 계정도 많다 — 실패해도 전체를 막지 않고
        # 최상위 폴더만이라도 반환한다(조회 자체가 실패해도 UX 저하는 최소화).
        inbox_children = child_resp.json().get("value", []) if child_resp.status_code == 200 else []

    folders = [
        {
            "id": f["id"], "display_name": f.get("displayName", ""),
            "unread_count": f.get("unreadItemCount", 0), "total_count": f.get("totalItemCount", 0),
        }
        for f in top_level
    ]
    folders += [
        {
            "id": f["id"], "display_name": f"받은 편지함 / {f.get('displayName', '')}",
            "unread_count": f.get("unreadItemCount", 0), "total_count": f.get("totalItemCount", 0),
        }
        for f in inbox_children
    ]
    return folders


async def _get_with_retry(
    client: httpx.AsyncClient, url: str, params: Optional[dict], headers: dict,
) -> httpx.Response:
    """429(rate limit)/5xx는 Graph API에서 흔히 발생 — Retry-After를 존중해 재시도한다."""
    last_resp: Optional[httpx.Response] = None
    for attempt in range(_MAX_RETRIES):
        resp = await client.get(url, params=params, headers=headers)
        if resp.status_code not in (429, 502, 503, 504):
            return resp
        last_resp = resp
        if attempt == _MAX_RETRIES - 1:
            break  # 마지막 시도 실패 — 더 재시도할 계획이 없으니 대기할 필요 없음
        wait_seconds = float(resp.headers.get("Retry-After", 2 ** attempt))
        logger.warning(
            "Graph API HTTP %s — %.1f초 후 재시도 (%d/%d)",
            resp.status_code, wait_seconds, attempt + 1, _MAX_RETRIES,
        )
        await asyncio.sleep(wait_seconds)
    return last_resp
