"""§9 폴링 설정 + §10 담당자 라우팅 매핑 — 관리자 CRUD (Track A #4)."""
from typing import Optional

import asyncpg

from core.database import get_conn, resolve_namespace_id
from core.security import encrypt_dict, decrypt_dict

_GRAPH_CREDENTIALS_KEY = "email_graph_credentials"

_SETTINGS_KEYS = (
    "email_collection_enabled", "email_polling_interval_minutes", "email_lookback_days",
    "email_relevance_min_score",
)
_MIN_POLLING_INTERVAL_MINUTES = 1  # §9 — Graph API 호출 한도 보호용 하한


# ─── §9 폴링 설정 (ops_system_config 재사용) ──────────────────────────────────

async def get_settings() -> dict:
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT key, value FROM ops_system_config WHERE key = ANY($1::text[])",
            list(_SETTINGS_KEYS),
        )
    values = {r["key"]: r["value"] for r in rows}
    return {
        "email_collection_enabled": values.get("email_collection_enabled", "false").lower() == "true",
        "email_polling_interval_minutes": int(values.get("email_polling_interval_minutes", 5)),
        "email_lookback_days": int(values.get("email_lookback_days", 7)),
        "email_relevance_min_score": float(values.get("email_relevance_min_score", 0.35)),
    }


async def update_settings(updates: dict) -> dict:
    """updates에 없는(None) 키는 변경하지 않는다."""
    interval = updates.get("email_polling_interval_minutes")
    if interval is not None and interval < _MIN_POLLING_INTERVAL_MINUTES:
        raise ValueError(f"폴링 주기는 최소 {_MIN_POLLING_INTERVAL_MINUTES}분 이상이어야 합니다.")
    lookback = updates.get("email_lookback_days")
    if lookback is not None and lookback < 1:
        raise ValueError("재조회 기간은 최소 1일 이상이어야 합니다.")
    relevance = updates.get("email_relevance_min_score")
    if relevance is not None and not (0.0 <= relevance <= 1.0):
        raise ValueError("관련지식 임계치는 0.0 ~ 1.0 사이여야 합니다.")

    async with get_conn() as conn:
        for key in _SETTINGS_KEYS:
            value = updates.get(key)
            if value is None:
                continue
            str_value = str(value).lower() if isinstance(value, bool) else str(value)
            await conn.execute(
                """INSERT INTO ops_system_config (key, value, updated_at)
                   VALUES ($1, $2, NOW())
                   ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
                key, str_value,
            )
    return await get_settings()


# ─── Graph API 자격증명 (§7 Q10 승인 후 관리자가 수기 입력) ──────────────────
# ops_system_config에 encrypt_dict()로 암호화한 JSON 문자열 한 덩어리로 저장 —
# 기존 Confluence PAT/LLM 자격증명과 동일한 Fernet 암호화 패턴 재사용.

async def get_graph_credentials_status() -> dict:
    async with get_conn() as conn:
        encrypted = await conn.fetchval(
            "SELECT value FROM ops_system_config WHERE key = $1", _GRAPH_CREDENTIALS_KEY,
        )
    if not encrypted:
        return {"configured": False, "tenant_id": None, "client_id": None}
    try:
        data = decrypt_dict(encrypted)
    except ValueError:
        return {"configured": False, "tenant_id": None, "client_id": None}
    return {"configured": True, "tenant_id": data.get("tenant_id"), "client_id": data.get("client_id")}


async def set_graph_credentials(tenant_id: str, client_id: str, client_secret: str) -> dict:
    # IT 회신 등에서 복사할 때 앞뒤 공백이 같이 끌려오는 사고가 실제로 있었다
    # (delegated_auth.save_config()에서 동일 문제 실측 확인) — 항상 strip해서 저장.
    tenant_id, client_id, client_secret = tenant_id.strip(), client_id.strip(), client_secret.strip()
    encrypted = encrypt_dict({"tenant_id": tenant_id, "client_id": client_id, "client_secret": client_secret})
    async with get_conn() as conn:
        await conn.execute(
            """INSERT INTO ops_system_config (key, value, updated_at)
               VALUES ($1, $2, NOW())
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()""",
            _GRAPH_CREDENTIALS_KEY, encrypted,
        )
    return await get_graph_credentials_status()


async def get_graph_credentials_decrypted() -> Optional[dict]:
    """내부 전용(파이프라인 실행용) — API로 노출하지 않는다."""
    async with get_conn() as conn:
        encrypted = await conn.fetchval(
            "SELECT value FROM ops_system_config WHERE key = $1", _GRAPH_CREDENTIALS_KEY,
        )
    if not encrypted:
        return None
    try:
        return decrypt_dict(encrypted)
    except ValueError:
        return None


# ─── §10 담당자 라우팅 매핑 (ops_voc_routing) ─────────────────────────────────

_ROUTING_COLUMNS = """id, part, mailbox_upn, teams_webhook_url, oncall_contact_name,
                      oncall_contact_phone, mail_folder_id, mail_folder_name,
                      is_active, created_at::text, updated_at::text"""


async def list_routing(namespace: str) -> list[dict]:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return []
        rows = await conn.fetch(
            f"SELECT {_ROUTING_COLUMNS} FROM ops_voc_routing WHERE namespace_id = $1 ORDER BY part",
            ns_id,
        )
    return [{**dict(r), "namespace": namespace} for r in rows]


async def create_routing(namespace: str, data: dict) -> dict:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"존재하지 않는 namespace: {namespace}")
        existing = await conn.fetchval(
            "SELECT 1 FROM ops_voc_routing WHERE namespace_id = $1 AND mailbox_upn = $2",
            ns_id, data["mailbox_upn"],
        )
        if existing:
            raise ValueError(f"이미 등록된 메일함입니다: {data['mailbox_upn']}")
        try:
            row = await conn.fetchrow(
                f"""INSERT INTO ops_voc_routing
                        (namespace_id, part, mailbox_upn, teams_webhook_url, oncall_contact_name,
                         oncall_contact_phone, mail_folder_id, mail_folder_name)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING {_ROUTING_COLUMNS}""",
                ns_id, data["part"], data["mailbox_upn"], data.get("teams_webhook_url"),
                data.get("oncall_contact_name"), data.get("oncall_contact_phone"),
                data.get("mail_folder_id"), data.get("mail_folder_name"),
            )
        except asyncpg.exceptions.UniqueViolationError:
            # 위 사전 체크와 실제 INSERT 사이의 경합(TOCTOU) — 동시에 같은 메일함을
            # 등록하려는 요청이 겹치면 DB 제약이 최종 방어선이 되므로 여기서도 같은
            # 친절한 메시지로 변환한다 (그대로 두면 500 UniqueViolationError로 샘).
            raise ValueError(f"이미 등록된 메일함입니다: {data['mailbox_upn']}")
    return {**dict(row), "namespace": namespace}


async def update_routing(routing_id: int, namespace: str, updates: dict) -> Optional[dict]:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return None
        # namespace_id까지 WHERE에 포함해야 한다 — routing_id만으로 조회/수정하면
        # 사용자가 자신이 권한을 가진 다른 namespace를 query param에 넣어 호출했을 때도
        # check_namespace_ownership(namespace)만 통과하면 실제로는 다른 namespace
        # 소유의 라우팅 행(다른 팀의 Teams 웹훅 등)을 수정할 수 있는 구멍이 생긴다.
        current = await conn.fetchrow(
            "SELECT * FROM ops_voc_routing WHERE id = $1 AND namespace_id = $2", routing_id, ns_id,
        )
        if not current:
            return None

        def _pick(key: str):
            v = updates.get(key)
            return v if v is not None else current[key]

        try:
            row = await conn.fetchrow(
                f"""UPDATE ops_voc_routing
                    SET part=$1, mailbox_upn=$2, teams_webhook_url=$3,
                        oncall_contact_name=$4, oncall_contact_phone=$5, is_active=$6,
                        mail_folder_id=$7, mail_folder_name=$8,
                        updated_at=NOW()
                    WHERE id=$9 AND namespace_id=$10
                    RETURNING {_ROUTING_COLUMNS}""",
                _pick("part"), _pick("mailbox_upn"), _pick("teams_webhook_url"),
                _pick("oncall_contact_name"), _pick("oncall_contact_phone"), _pick("is_active"),
                _pick("mail_folder_id"), _pick("mail_folder_name"),
                routing_id, ns_id,
            )
        except asyncpg.exceptions.UniqueViolationError:
            raise ValueError(f"이미 등록된 메일함입니다: {_pick('mailbox_upn')}")
    return {**dict(row), "namespace": namespace}


async def delete_routing(routing_id: int, namespace: str) -> bool:
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            return False
        # update_routing과 동일한 이유로 namespace_id를 반드시 WHERE에 포함
        result = await conn.execute(
            "DELETE FROM ops_voc_routing WHERE id = $1 AND namespace_id = $2", routing_id, ns_id,
        )
    return "DELETE 1" in result
