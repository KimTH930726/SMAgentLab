"""관리자 메뉴 노출 설정(2026-10-23, 사용자: "영역별 메뉴 노출을 관리 — 나중엔 권한별도") — 범위마다 숨길 메뉴 id 목록.

저장 = ops_system_config 한 줄(`menu_visibility`): {"hidden": {"kind:domain": [...], "kind:policy_test": [...]}}.
범위 키는 `kind:<컬렉션 종류>` — 나중에 권한별 노출은 `role:<역할>`을 같은 모양으로 더한다(지금은 화면·검사 없음).
여기엔 "숨길 것"만 있다 — 그 종류·구분이 담을 수 없는 메뉴는 화면 코드(adminMenu.ts)가 늘 숨기고 설정으로 켤 수 없다
(서버도 시스템 컬렉션엔 정책 가져오기를 400으로 막는다). 메뉴 id 목록은 화면 것이라 서버는 모양만 본다.
"""
import json
import logging
import re

from core.database import get_conn

logger = logging.getLogger(__name__)

KEY = "menu_visibility"
_SCOPE = re.compile(r"^(kind|role):[a-z_]{1,32}$")
_MENU_ID = re.compile(r"^[a-z][a-z.]{0,40}$")
MAX_IDS = 50


def normalize(hidden: dict) -> dict[str, list[str]]:
    """모양 검사 + 정리(중복 제거·정렬). 틀리면 ValueError(→ 400)."""
    if not isinstance(hidden, dict) or len(hidden) > 20:
        raise ValueError("hidden은 {범위: [메뉴 id…]} 모양이어야 합니다.")
    out: dict[str, list[str]] = {}
    for scope, ids in hidden.items():
        if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
            raise ValueError(f"범위 이름이 올바르지 않습니다: {scope!r} (kind:<종류> 또는 role:<역할>)")
        if not isinstance(ids, list) or len(ids) > MAX_IDS or not all(isinstance(i, str) and _MENU_ID.fullmatch(i) for i in ids):
            raise ValueError(f"{scope}: 메뉴 id 목록이 올바르지 않습니다.")
        out[scope] = sorted(set(ids))
    return out


def parse_stored(raw) -> dict[str, list[str]]:
    """저장 값 → 숨김. 깨졌으면(JSON 아님·객체 아님·모양 틀림) 아무것도 숨기지 않는다 — 메뉴가 통째로 사라지거나 설정 화면이 안 열리는 것보다
    낫고, 설정 화면에서 다시 저장하면 고쳐진다(리뷰 10/23: 객체가 아닌 JSON이면 500이었다). 로그로 남김."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("객체가 아님")
        return normalize(parsed.get("hidden", {}))
    except (ValueError, TypeError) as e:   # JSONDecodeError는 ValueError
        logger.warning("menu_visibility 설정 값이 깨져 무시함(모두 보임): %s", e)
        return {}


async def get_visibility() -> dict:
    async with get_conn() as conn:
        raw = await conn.fetchval("SELECT value FROM ops_system_config WHERE key = $1", KEY)
    return {"hidden": parse_stored(raw)}


async def set_visibility(hidden: dict) -> dict:
    value = {"hidden": normalize(hidden)}
    async with get_conn() as conn:
        await conn.execute("""INSERT INTO ops_system_config (key, value, updated_at) VALUES ($1, $2, NOW())
                              ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
                           KEY, json.dumps(value, ensure_ascii=False))
    return value
