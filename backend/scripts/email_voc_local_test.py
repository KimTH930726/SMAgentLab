"""VOC 이메일 로컬 시연 — Delegated Permission(사용자 위임 권한)으로 본인 메일함을
대상으로 운영 파이프라인을 그대로 실행한다.

배경 (docs/email-analysis-channel-plan.md §7):
운영은 Application 권한(client_credentials)으로 무인 폴링하도록 설계돼 있는데,
지금 조직에서 승인받은 건 Delegated 권한뿐이라 그 방식으로는 로그인 자체가 안 된다
— Delegated는 반드시 사람이 로그인한 세션이 있어야 한다.

이 스크립트가 하는 일은 딱 하나 — **토큰 발급 방식만 Delegated(device code flow)로
바꾸고, 그 뒤(수집→분석→중복제거→라우팅매칭→Teams발송→이력저장)는 전부 운영
파이프라인(`service/email_voc/pipeline.run_manual_collection`)을 그대로 호출**하는
것이다. 별도로 메일 조회를 재구현하지 않는다 — `graph_client.fetch_messages()`가
호출하는 `/users/{mailbox_upn}/messages`는 그대로고, mailbox_upn이 로그인한 본인
메일 주소와 같으면 Delegated 토큰으로도 정상 동작한다(Graph API가 자기 자신에
대한 명시적 접근을 허용하는 규칙 — `/me/messages`와 동일하게 취급됨). 즉 운영과
로컬 시연의 차이는 "어느 메일함을 보는가"와 "토큰을 어떻게 얻는가" 둘 뿐이고,
그 이후 코드 경로는 100% 동일하다.

운영 코드 변경 범위: `pipeline.run_manual_collection()`에 `access_token` 파라미터를
옵션으로 하나 추가했다(기본값 None — 기존 호출부는 전부 영향 없음). 값을 넘기면
Application 권한 토큰 발급(`graph_client.get_access_token`)을 건너뛰고 그 토큰을
그대로 쓴다. `graph_client.py` 자체는 안 건드림 — Application 권한이 승인되면
운영 경로(스케줄러/관리자 수동실행)는 이 파라미터를 안 넘기므로 그대로 동작한다.

사전 준비:
- Admin 화면(VOC 이메일 > 라우팅)에 대상 네임스페이스로 라우팅 행을 만들고
  `mailbox_upn`을 로그인할 본인 메일 주소로 설정. `teams_webhook_url`까지 채우면
  실제 Teams 발송까지 확인 가능(비워두면 분석·이력저장까지만 진행 — §10 참고)
- Azure AD 앱 등록: Delegated permission `Mail.Read` 동의 완료, Authentication 블레이드
  → "Allow public client flows" = Yes (꺼져 있으면 device code flow 요청 시
  AADSTS7000218 류 에러 발생), Redirect URI는 불필요(device code flow는 안 씀)

실행 (컨테이너 안 — DB/임베딩모델/LLM이 이미 떠 있는 환경):
    docker compose exec backend python scripts/email_voc_local_test.py \\
        --tenant-id <tenant-id> --client-id <client-id> --namespace coupon --days 7

매번 --tenant-id/--client-id 치기 귀찮으면 환경변수로:
    GRAPH_TEST_TENANT_ID=... GRAPH_TEST_CLIENT_ID=...

로그인은 첫 실행 때만 필요 — 실행하면 "https://microsoft.com/devicelogin 에서 코드
XXXXXXX 입력" 안내가 뜨고, 아무 브라우저에서나 완료하면 됨. 이후 실행부턴
`.email_voc_token_cache.json`에 저장된 refresh token으로 조용히 갱신된다.

⚠️ `.email_voc_token_cache.json`은 refresh token이 그대로 든 파일이라 자격증명과
동급으로 취급할 것 — 커밋 금지(.gitignore 처리됨), 공유 금지.
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import msal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("email_voc_local_test")

_SCOPES = ["Mail.Read"]
_TOKEN_CACHE_PATH = Path(__file__).parent / ".email_voc_token_cache.json"


def _load_token_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if _TOKEN_CACHE_PATH.exists():
        cache.deserialize(_TOKEN_CACHE_PATH.read_text(encoding="utf-8"))
    return cache


def _save_token_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        _TOKEN_CACHE_PATH.write_text(cache.serialize(), encoding="utf-8")
        logger.info("토큰 캐시 저장: %s (다음 실행부턴 재로그인 불필요)", _TOKEN_CACHE_PATH)


def acquire_token_device_code(tenant_id: str, client_id: str) -> str:
    """Device Code Flow로 위임(delegated) 토큰 발급.

    운영 코드의 acquire_token_for_client()(Application 권한 전용)와 달리, 이건
    반드시 사람이 최소 1회 브라우저로 로그인해야 하는 흐름이다.
    """
    cache = _load_token_cache()
    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=cache,
    )

    result = None
    accounts = app.get_accounts()
    if accounts:
        logger.info("캐시된 계정 발견 (%s) — 조용히 토큰 갱신 시도", accounts[0].get("username"))
        result = app.acquire_token_silent(_SCOPES, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=_SCOPES)
        if "user_code" not in flow:
            raise RuntimeError(
                f"device flow 시작 실패: {flow.get('error_description', flow)}\n"
                "→ Azure AD 앱 등록의 Authentication에서 'Allow public client flows'가 "
                "켜져 있는지 확인하세요."
            )
        print("\n" + "=" * 70)
        print(flow["message"])
        print("=" * 70 + "\n")
        result = app.acquire_token_by_device_flow(flow)  # 사용자가 로그인 완료할 때까지 블로킹

    _save_token_cache(cache)

    if not result or "access_token" not in result:
        err = (result or {}).get("error_description", "알 수 없는 오류")
        raise RuntimeError(f"토큰 발급 실패: {err}")
    return result["access_token"]


async def run_full_flow(access_token: str, namespace: str, days: int) -> None:
    """운영 파이프라인(pipeline.run_manual_collection)을 그대로 실행한다 —
    수집→분석→중복제거→라우팅매칭→Teams발송→이력저장까지 전부 운영 코드 경로다.
    유일한 차이는 토큰을 Application 권한 대신 Delegated 권한으로 얻었다는 것뿐.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # backend/ 를 import root로
    from core.database import close_pool, init_pool
    from service.email_voc.pipeline import run_manual_collection
    from shared.embedding import embedding_service

    logger.info("DB 풀 초기화 중...")
    await init_pool()
    logger.info("임베딩 모델 로딩 중 (최초 실행 시 다소 걸릴 수 있음)...")
    embedding_service.load()

    try:
        result = await run_manual_collection(
            namespace, date.today() - timedelta(days=days), date.today(),
            access_token=access_token,
        )
        print(f"\n{'─' * 70}")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        for mb in result["mailboxes"]:
            if not mb["ok"]:
                logger.warning("메일함 %s 실패: %s", mb["mailbox_upn"], mb["error"])
    finally:
        await close_pool()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="VOC 이메일 로컬 시연 (Delegated Permission + 운영 파이프라인 그대로 실행)",
    )
    parser.add_argument("--tenant-id", default=os.environ.get("GRAPH_TEST_TENANT_ID"))
    parser.add_argument("--client-id", default=os.environ.get("GRAPH_TEST_CLIENT_ID"))
    parser.add_argument("--namespace", required=True, help="대상 네임스페이스 (라우팅이 이 네임스페이스로 등록돼 있어야 함)")
    parser.add_argument("--days", type=int, default=7, help="최근 며칠 이내 메일 조회 (기본 7일)")
    args = parser.parse_args()

    if not args.tenant_id or not args.client_id:
        parser.error(
            "--tenant-id / --client-id 가 필요합니다 "
            "(또는 GRAPH_TEST_TENANT_ID / GRAPH_TEST_CLIENT_ID 환경변수)"
        )

    logger.info("로그인 시작 (캐시된 세션 없으면 디바이스 코드 로그인 필요)...")
    access_token = acquire_token_device_code(args.tenant_id, args.client_id)
    logger.info("토큰 발급 완료. 운영 파이프라인 실행...")

    asyncio.run(run_full_flow(access_token, args.namespace, args.days))


if __name__ == "__main__":
    main()
