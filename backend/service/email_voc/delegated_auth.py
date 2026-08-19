"""VOC 이메일 — Delegated Permission(사용자 위임 권한) 로그인 상태 관리.

Application 권한(Track B) 승인 전, Delegated 권한(본인 메일함)으로도 관리자 화면의
기존 폴링 토글/수동실행 버튼이 그대로 동작하게 만드는 모듈 — 즉 "버튼 한 번"으로
전체 플로우(수집→분석→라우팅→Teams발송)를 시연할 수 있게 한다.

사람이 최소 1회(또는 refresh token 만료 시) 브라우저로 로그인을 완료해야 하는 걸
피할 방법은 없다 — Microsoft의 위임 권한 보안 모델 자체가 그렇게 설계돼 있다
(에이전트/서버가 사용자를 대신해 로그인할 수 없어야 함). 이 모듈이 하는 건 "그
1회 로그인 이후"를 완전히 자동화하는 것이다 — 로그인 완료 시 refresh token을
암호화해 DB에 저장해두고, 이후 폴링 사이클마다 acquire_token_silent()로 사람 개입
없이 access token을 갱신한다(단, refresh token 자체가 만료/폐기되면 다시 1회
로그인이 필요하다 — 완전 무인인 Application 권한과의 근본적인 차이).

인증 흐름: Authorization Code Flow (PKCE) — 로그인 링크 클릭 → Microsoft 로그인 →
이 서버의 콜백(router.py의 /delegated-auth/callback)으로 리다이렉트 → 토큰 교환.
Device Code Flow(코드를 사람이 손으로 옮겨 입력하는 방식) 대신 이 방식을 쓰는 이유:
Device Code Flow는 "Device Code Phishing"이라는 알려진 공격 기법의 대상이라(공격자가
코드를 발급받아 피해자에게 입력시켜 토큰을 가로챔) 보안팀에서 앱의 "Allow public
client flows" 설정 자체를 비권장 사유로 거부했다. Authorization Code Flow는 이
리스크가 없다(사람이 코드를 옮겨 입력하는 과정 자체가 없음).

Public Client(시크릿 없이 PKCE만으로 토큰 교환) vs Confidential Client(시크릿 사용):
원래 설계는 Public Client였으나, 리다이렉트 URI가 Azure AD에 "Web" 플랫폼으로
등록된 경우 PKCE만으론 토큰 교환이 거부되는 사례가 실측으로 확인됐다
(AADSTS7000218 — docs/tech/voc-email-handoff.md §3 10~11단계). client_secret이
설정돼 있으면 Confidential Client로, 없으면 기존과 동일하게 Public Client로
동작하도록 `_build_app()`에서 분기한다 — 리다이렉트 URI 플랫폼 유형이 나중에
정정되면 시크릿 없이도 원래 방식으로 돌아갈 수 있게 하기 위함. client_secret은
기존 Graph API 자격증명(routing_service.set_graph_credentials)과 동일하게
Fernet 암호화(core.security.encrypt_dict)로 DB에 저장하고, 조회 API(get_status)는
설정 여부(client_secret_configured)만 반환하며 값 자체는 절대 응답에 포함하지
않는다 — 실제 값은 이 코드베이스 밖(Admin UI 입력 → 암호화 DB 저장)에만 존재하며,
git으로 관리되는 어떤 문서/설정 파일에도 평문으로 남기지 않는다.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import msal

from core.database import get_conn
from core.security import decrypt_dict, encrypt_dict

logger = logging.getLogger(__name__)

_CONFIG_KEY = "email_graph_delegated"
_SCOPES = ["Mail.Read"]
# 로그인 시작 후 콜백이 영영 안 오는 경우(사용자가 탭을 닫거나 Microsoft가 리다이렉트
# URL 불일치로 자체 에러 페이지를 띄운 경우 등) — 이런 경우 우리 서버는 실패 사실을
# 알 방법이 없어 _pending이 영원히 "진행 중"으로 남는다. 그러면 재로그인 시도 자체가
# 막히므로(start_login의 동시성 가드), 일정 시간 지난 pending은 방치된 것으로 보고
# 새 로그인 시도를 허용한다.
_PENDING_STALE_SECONDS = 600

# 단일 관리자가 시연/검증용으로 쓰는 임시 기능이라 인메모리로 충분하다 — 로그인
# "진행 상태"만 담고, 완료되면 즉시 DB에 영속화되므로 워커 재시작 시 유실돼도
# 로그인만 다시 시작하면 된다(그 뒤 이미 완료된 로그인 자체는 DB에 남아 안전).
_pending: Optional[dict] = None  # {"status": "pending"|"success"|"error", "message": str, "account": Optional[str], "started_at": datetime}
# initiate_auth_code_flow()가 반환하는 flow(PKCE code_verifier, state 등 포함) —
# 콜백이 올 때까지 들고 있어야 하는 1회용 값. 콜백 처리 후 즉시 비운다.
_auth_flow: Optional[dict] = None


def _is_pending_stale() -> bool:
    if _pending is None or _pending["status"] != "pending":
        return False
    started_at = _pending.get("started_at")
    if started_at is None:
        return True
    return (datetime.now(timezone.utc) - started_at).total_seconds() > _PENDING_STALE_SECONDS


async def _load_config() -> Optional[dict]:
    async with get_conn() as conn:
        encrypted = await conn.fetchval("SELECT value FROM ops_system_config WHERE key = $1", _CONFIG_KEY)
    if not encrypted:
        return None
    try:
        return decrypt_dict(encrypted)
    except ValueError:
        return None


async def _save_config(data: dict) -> None:
    encrypted = encrypt_dict(data)
    async with get_conn() as conn:
        await conn.execute(
            """INSERT INTO ops_system_config (key, value, updated_at)
               VALUES ($1, $2, NOW())
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
            _CONFIG_KEY, encrypted,
        )


def _build_app(config: dict, cache: msal.SerializableTokenCache):
    """msal 앱 생성자(Public/Confidential 공통)는 authority(tenant)의 OIDC discovery
    문서를 네트워크로 조회한다 — `validate_authority=False`로도 이 조회 자체는
    막히지 않는다(실측 확인: 잘못된 tenant_id로 생성만 해도 0.5초 뒤 예외 발생).
    즉 이 생성자 호출은 항상 동기 블로킹 HTTP 호출을 동반한다고 봐야 한다.
    그래서 이 함수는 절대 이벤트 루프에서 직접 부르면 안 되고, 항상
    run_in_executor로 감싸서 호출해야 한다(호출부에서 강제) — 안 그러면 tenant_id가
    잘못됐거나 네트워크가 느릴 때 이 호출 하나가 서버 전체 요청 처리를 막는다
    (실제로 이 문제를 겪고 나서 이 방식으로 고쳤다 — 아래 각 호출부의 executor
    래핑 참고).

    client_secret이 설정돼 있으면 ConfidentialClientApplication, 없으면 기존과
    동일하게 PublicClientApplication을 쓴다(모듈 docstring 참고) — PKCE 기반
    Authorization Code Flow 자체는 두 경우 모두 동일하게 동작하므로 이 분기 밖의
    호출부(start_login/complete_login 등)는 수정할 필요가 없다.
    """
    authority = f"https://login.microsoftonline.com/{config['tenant_id']}"
    client_secret = config.get("client_secret")
    if client_secret:
        return msal.ConfidentialClientApplication(
            config["client_id"],
            client_credential=client_secret,
            authority=authority,
            token_cache=cache,
            validate_authority=False,
        )
    return msal.PublicClientApplication(
        config["client_id"],
        authority=authority,
        token_cache=cache,
        validate_authority=False,
    )


def _build_app_and_get_accounts(config: dict, cache: msal.SerializableTokenCache):
    """_build_app()의 블로킹 network 호출과 그 직후 계정 조회를 한 번에 묶어
    executor 안에서 실행하기 위한 동기 헬퍼. (app, accounts) 튜플을 반환한다."""
    app = _build_app(config, cache)
    return app, app.get_accounts()


async def get_status() -> dict:
    config = await _load_config()
    if not config or not config.get("tenant_id") or not config.get("client_id") or not config.get("redirect_uri"):
        return {
            "configured": False, "logged_in": False, "account": None, "pending": False,
            "login_error": None, "redirect_uri": None, "client_secret_configured": False,
        }

    # client_secret 값 자체는 절대 응답에 포함하지 않는다 — 설정 여부(bool)만 노출.
    client_secret_configured = bool(config.get("client_secret"))

    cache = msal.SerializableTokenCache()
    if config.get("cache"):
        cache.deserialize(config["cache"])
    try:
        loop = asyncio.get_running_loop()
        _app, accounts = await loop.run_in_executor(None, _build_app_and_get_accounts, config, cache)
    except Exception as e:
        # tenant_id/client_id가 잘못됐거나(ValueError) 네트워크 문제로 authority 조회
        # 자체가 실패한 경우 — 조회 API가 500으로 죽는 대신 "설정은 있으나 검증
        # 실패"로 알려준다(로그인 여부만 False). 상태 조회는 관리자 화면이 10초
        # 간격으로 폴링하는 경로라 여기서 예외가 새면 화면이 계속 깨진다.
        logger.warning("[VOC Delegated] 상태 조회 중 authority 확인 실패: %s", e)
        return {
            "configured": True, "logged_in": False, "account": None, "pending": False,
            "login_error": None, "redirect_uri": config["redirect_uri"],
            "client_secret_configured": client_secret_configured,
        }

    account = accounts[0]["username"] if accounts else None
    pending = _pending is not None and _pending["status"] == "pending" and not _is_pending_stale()
    # 로그인 시도가 실패로 끝났을 때(예: 사용자가 승인 거부, 콜백 오류) pending만
    # False로 떨어지면 프론트가 "로그인 완료"와 구분을 못 한다 — 실패 사유를 같이 내려준다.
    login_error = _pending["message"] if _pending is not None and _pending["status"] == "error" else None
    return {
        "configured": True, "logged_in": account is not None, "account": account,
        "pending": pending, "login_error": login_error, "redirect_uri": config["redirect_uri"],
        "client_secret_configured": client_secret_configured,
    }


async def save_config(tenant_id: str, client_id: str, redirect_uri: str, client_secret: Optional[str] = None) -> dict:
    """tenant_id/client_id/redirect_uri(+선택적 client_secret)를 저장한다. tenant_id/
    client_id/redirect_uri 중 하나라도 바뀌면 기존 로그인 캐시는 다른 앱/경로 것이라
    의미가 없어지므로 비운다 — 그대로 두면 다음 로그인 시도가 옛 설정으로 조용히
    시도되다 실패하는 혼란스러운 상황이 생긴다. client_secret만 바뀌는 경우(예:
    만료 전 재발급)는 같은 앱의 시크릿 교체일 뿐이라 캐시(refresh token)는 그대로
    유효하므로 keep_cache 조건에 포함하지 않는다.

    client_secret은 빈 문자열/공백만 있는 값을 "설정 없음"과 구분하기 위해 strip 후
    비어있으면 None으로 정규화한다 — 프론트에서 빈 입력을 보내는 경우와 실제로
    시크릿을 지우려는 의도를 동일하게 취급한다(둘 다 Public Client로 되돌아감).

    IT 회신 메일 등에서 복사할 때 앞뒤 공백이 같이 끌려오는 경우가 실제로 있었다
    (예: "Application ID: <값>"에서 콜론 뒤 공백까지 복사) — 공백 포함 client_id로
    저장되면 authorize 요청은 통과해도(Microsoft가 관대하게 처리) 토큰 교환 단계에서
    거부되는 걸 실측으로 확인했다. 여기서 항상 strip() 해서 저장한다.
    """
    global _pending, _auth_flow
    tenant_id, client_id, redirect_uri = tenant_id.strip(), client_id.strip(), redirect_uri.strip()
    client_secret = client_secret.strip() or None if client_secret else None
    existing = await _load_config()
    keep_cache = bool(
        existing
        and existing.get("tenant_id") == tenant_id
        and existing.get("client_id") == client_id
        and existing.get("redirect_uri") == redirect_uri
    )
    await _save_config({
        "tenant_id": tenant_id, "client_id": client_id, "redirect_uri": redirect_uri,
        "client_secret": client_secret,
        "cache": existing.get("cache") if keep_cache else None,
    })
    # 설정을 다시 저장한다는 건 이전 시도를 포기하고 다시 하겠다는 의도다. 이전
    # 로그인이 pending에 갇혀 있으면(예: 잘못된 client_id로 Microsoft 로그인
    # 페이지 자체에서 에러가 나서 우리 콜백까지 온 적이 없는 경우 — 이 경우 우리
    # 서버는 실패 사실을 알 방법이 없어 pending이 10분 동안 안 풀림) 여기서 바로
    # 정리해줘야 새 설정으로 즉시 재시도할 수 있다.
    _pending = None
    _auth_flow = None
    return await get_status()


async def start_login() -> dict:
    """Authorization Code Flow 시작. 로그인 URL을 반환한다 — 관리자가 이 URL을
    열어 로그인하면 Microsoft가 router.py의 콜백 엔드포인트로 리다이렉트하고,
    거기서 complete_login()이 호출돼 로그인이 마무리된다.
    """
    global _pending, _auth_flow
    if _pending is not None and _pending["status"] == "pending" and not _is_pending_stale():
        # 이미 진행 중인 로그인이 있는데 또 시작하면 이전 flow(_auth_flow)가
        # 덮어써져서, 먼저 열어둔 로그인 탭이 나중에 완료돼도 콜백이 옛 flow를
        # 찾지 못해 실패한다. 오래된(방치된) pending은 위 조건에서 걸러져 재시작 허용.
        raise ValueError("이미 로그인이 진행 중입니다 — 완료되거나 만료될 때까지 기다려주세요.")

    config = await _load_config()
    if not config or not config.get("tenant_id") or not config.get("client_id") or not config.get("redirect_uri"):
        raise ValueError("tenant_id/client_id/redirect_uri가 먼저 설정돼야 합니다.")

    cache = msal.SerializableTokenCache()
    if config.get("cache"):
        cache.deserialize(config["cache"])

    def _start_flow() -> dict:
        app = _build_app(config, cache)
        return app.initiate_auth_code_flow(_SCOPES, redirect_uri=config["redirect_uri"])

    loop = asyncio.get_running_loop()
    try:
        flow = await loop.run_in_executor(None, _start_flow)
    except ValueError as e:
        raise ValueError(f"tenant_id 확인 실패 — Authority 조회에 실패했습니다: {e}")
    if "auth_uri" not in flow:
        raise ValueError(f"로그인 URL 생성 실패: {flow.get('error_description', flow)}")

    _auth_flow = flow
    _pending = {"status": "pending", "message": "", "account": None, "started_at": datetime.now(timezone.utc)}

    return {"auth_url": flow["auth_uri"]}


async def complete_login(auth_response: dict) -> None:
    """콜백(router.py)이 Microsoft 리다이렉트에서 받은 쿼리 파라미터(code/state 등)로
    로그인을 완료한다. 결과는 _pending에 반영되고, 관리자 화면이 이를 폴링해 확인한다.
    """
    global _pending, _auth_flow
    flow = _auth_flow
    _auth_flow = None  # 1회용 — 재사용/재전송(replay) 방지

    if flow is None:
        _pending = {
            "status": "error", "account": None, "started_at": datetime.now(timezone.utc),
            "message": "로그인 세션을 찾을 수 없습니다(만료되었거나 이미 처리됨). 관리자 화면에서 다시 로그인해주세요.",
        }
        return

    config = await _load_config()
    if not config:
        _pending = {
            "status": "error", "account": None, "started_at": datetime.now(timezone.utc),
            "message": "앱 설정을 찾을 수 없습니다.",
        }
        return

    cache = msal.SerializableTokenCache()
    if config.get("cache"):
        cache.deserialize(config["cache"])

    def _acquire():
        app = _build_app(config, cache)
        return app, app.acquire_token_by_auth_code_flow(flow, auth_response)

    loop = asyncio.get_running_loop()
    try:
        app, result = await loop.run_in_executor(None, _acquire)
    except Exception as e:
        logger.warning("[VOC Delegated] 로그인 완료 처리 중 오류: %s", e)
        _pending = {"status": "error", "message": str(e), "account": None, "started_at": datetime.now(timezone.utc)}
        return

    if not result or "access_token" not in result:
        err = (result or {}).get("error_description", "알 수 없는 오류")
        logger.warning("[VOC Delegated] 로그인 실패: %s", err)
        _pending = {"status": "error", "message": err, "account": None, "started_at": datetime.now(timezone.utc)}
        return

    accounts = app.get_accounts()
    account = accounts[0]["username"] if accounts else None
    await _save_config({
        "tenant_id": config["tenant_id"], "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"], "client_secret": config.get("client_secret"),
        "cache": cache.serialize(),
    })
    logger.info("[VOC Delegated] 로그인 완료: %s", account)
    _pending = {"status": "success", "message": "", "account": account, "started_at": datetime.now(timezone.utc)}


async def get_access_token_silent() -> Optional[str]:
    """운영 파이프라인(pipeline.run_manual_collection/scheduler)이 Application 권한
    자격증명이 없을 때 마지막으로 시도하는 대체 경로. 로그인 세션이 없거나 refresh
    token이 만료됐으면 None을 반환한다(그러면 파이프라인은 기존과 동일하게 '자격증명
    없음' 에러로 처리) — 이 함수가 새로 사람에게 로그인을 요구하지는 않는다.

    이 함수는 절대 예외를 던지지 않는다(항상 None으로 폴백) — 폴링 사이클의 각
    namespace 처리 루프 안에서 호출되는데, 여기서 예외가 새면 그 namespace뿐
    아니라 사이클 전체가 죽는다(다른 메일함은 계속 처리한다는 설계 원칙 위반).
    네트워크 오류든 authority 오류든 뭐가 됐든 "지금은 토큰을 못 구했다"로 취급한다.
    """
    try:
        config = await _load_config()
        if not config or not config.get("cache"):
            return None

        cache = msal.SerializableTokenCache()
        cache.deserialize(config["cache"])

        loop = asyncio.get_running_loop()
        app, accounts = await loop.run_in_executor(None, _build_app_and_get_accounts, config, cache)
        if not accounts:
            return None

        result = await loop.run_in_executor(
            None, lambda: app.acquire_token_silent(_SCOPES, account=accounts[0]),
        )
        if cache.has_state_changed:
            await _save_config({
                "tenant_id": config["tenant_id"], "client_id": config["client_id"],
                "redirect_uri": config["redirect_uri"], "client_secret": config.get("client_secret"),
                "cache": cache.serialize(),
            })
        if not result or "access_token" not in result:
            return None
        return result["access_token"]
    except Exception:
        logger.warning("[VOC Delegated] silent 토큰 조회 실패 — 자격증명 없음으로 처리", exc_info=True)
        return None
