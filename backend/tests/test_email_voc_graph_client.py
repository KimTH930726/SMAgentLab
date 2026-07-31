"""Tests for service/email_voc/graph_client.py — 실제 자격증명 없이 mock으로 검증.

Q10(IT 승인) 전이라 실제 Microsoft Graph API에 붙일 수 없다 — msal/httpx 호출을
mock으로 대체해 인증 실패/페이지네이션/조회 실패 분기 로직만 검증한다.
"""
import importlib.util as _ilu
import sys
from pathlib import Path

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

# conftest.py가 격리 목적으로 sys.modules["service"] 전체를 MagicMock으로 치환해두므로
# (shared.json_utils와 동일한 문제), graph_client는 core/shared에 의존하지 않는
# 순수 httpx/msal 모듈이라 파일 경로 기반으로 직접 로드해 실제 코드를 검증한다.
_backend_dir = Path(__file__).resolve().parent.parent
_spec = _ilu.spec_from_file_location(
    "email_voc_graph_client_under_test", str(_backend_dir / "service" / "email_voc" / "graph_client.py"),
)
graph_client = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = graph_client
_spec.loader.exec_module(graph_client)


class TestGetAccessToken:
    @pytest.mark.asyncio
    async def test_success(self):
        with patch("msal.ConfidentialClientApplication") as MockApp:
            MockApp.return_value.acquire_token_for_client.return_value = {"access_token": "tok123"}
            token = await graph_client.get_access_token("tenant", "client", "secret")
        assert token == "tok123"

    @pytest.mark.asyncio
    async def test_auth_failure_raises(self):
        with patch("msal.ConfidentialClientApplication") as MockApp:
            MockApp.return_value.acquire_token_for_client.return_value = {
                "error": "invalid_client", "error_description": "잘못된 클라이언트 시크릿",
            }
            with pytest.raises(graph_client.GraphAuthError, match="잘못된 클라이언트 시크릿"):
                await graph_client.get_access_token("tenant", "client", "bad-secret")


def _fake_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


class TestFetchMessages:
    @pytest.mark.asyncio
    async def test_single_page(self):
        page = {
            "value": [
                {
                    "id": "msg-1", "subject": "결제 오류",
                    "from": {"emailAddress": {"address": "user@example.com"}},
                    "receivedDateTime": "2026-07-29T00:00:00Z",
                    "body": {"content": "본문 내용"},
                },
            ],
        }
        mock_client = AsyncMock()
        mock_client.get.return_value = _fake_response(200, page)
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False

        with patch("httpx.AsyncClient", return_value=mock_client):
            messages = await graph_client.fetch_messages(
                "voc@example.com", "tok", datetime(2026, 7, 22),
            )

        assert len(messages) == 1
        assert messages[0] == {
            "id": "msg-1", "subject": "결제 오류", "sender": "user@example.com",
            "received_at": "2026-07-29T00:00:00Z", "body": "본문 내용",
        }
        # 페이지네이션 없을 때 GET은 정확히 1번만 호출
        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_pagination_follows_next_link(self):
        page1 = {
            "value": [{"id": "msg-1", "subject": "1건째", "from": {}, "receivedDateTime": "t1", "body": {}}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/next-page",
        }
        page2 = {
            "value": [{"id": "msg-2", "subject": "2건째", "from": {}, "receivedDateTime": "t2", "body": {}}],
        }
        mock_client = AsyncMock()
        mock_client.get.side_effect = [_fake_response(200, page1), _fake_response(200, page2)]
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False

        with patch("httpx.AsyncClient", return_value=mock_client):
            messages = await graph_client.fetch_messages(
                "voc@example.com", "tok", datetime(2026, 7, 22),
            )

        assert [m["id"] for m in messages] == ["msg-1", "msg-2"]
        assert mock_client.get.call_count == 2
        # 두 번째 호출은 nextLink URL을 그대로 사용해야 함 (params 재전달 금지)
        second_call = mock_client.get.call_args_list[1]
        assert second_call.args[0] == "https://graph.microsoft.com/v1.0/next-page"
        assert second_call.kwargs["params"] is None

    @pytest.mark.asyncio
    async def test_http_error_raises(self):
        mock_client = AsyncMock()
        mock_client.get.return_value = _fake_response(401, {}, text="Unauthorized")
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(graph_client.GraphApiError, match="401"):
                await graph_client.fetch_messages("voc@example.com", "tok", datetime(2026, 7, 22))
