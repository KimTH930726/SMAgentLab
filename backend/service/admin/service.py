"""관리 도메인 — 네임스페이스 CRUD 서비스."""
from __future__ import annotations

import logging
from typing import Optional

from core.database import get_conn

logger = logging.getLogger(__name__)


async def list_namespaces() -> list[str]:
    """고를 수 있는 업무 영역 — 상위 묶음(kind='group')은 데이터가 없어 빼고 이름만(2026-10-20)."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT name FROM ops_namespace WHERE kind <> 'group' ORDER BY name"
        )
    return [r["name"] for r in rows]


async def list_namespaces_detail() -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.name, n.description, n.kind, n.parent_id, pn.name AS parent_name,
                   p.name AS owner_part,
                   n.created_at::text,
                   n.created_by_user_id,
                   u.username AS created_by_username,
                   COALESCE(k.cnt, 0) AS knowledge_count,
                   COALESCE(g.cnt, 0) AS glossary_count
            FROM ops_namespace n
            LEFT JOIN ops_part p ON n.owner_part_id = p.id
            LEFT JOIN ops_namespace pn ON pn.id = n.parent_id
            LEFT JOIN ops_user u ON n.created_by_user_id = u.id
            LEFT JOIN (
                SELECT namespace_id, COUNT(*) AS cnt FROM rag_knowledge WHERE status = 'active' GROUP BY namespace_id
            ) k ON n.id = k.namespace_id
            LEFT JOIN (
                SELECT namespace_id, COUNT(*) AS cnt FROM rag_glossary GROUP BY namespace_id
            ) g ON n.id = g.namespace_id
            WHERE n.kind <> 'group'
            ORDER BY n.name
            """
        )
    return [dict(r) for r in rows]


async def create_namespace(
    name: str, description: str = "",
    owner_part: str | None = None, created_by_user_id: int | None = None,
) -> dict | None:
    async with get_conn() as conn:
        # owner_part name → id 변환
        owner_part_id = None
        if owner_part:
            owner_part_id = await conn.fetchval(
                "SELECT id FROM ops_part WHERE name = $1", owner_part
            )
        row = await conn.fetchrow(
            """
            INSERT INTO ops_namespace (name, description, owner_part_id, created_by_user_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (name) DO NOTHING
            RETURNING id, name, description, created_at::text
            """,
            name, description, owner_part_id, created_by_user_id,
        )
        if row is None:   # 같은 이름(숨은 상위 묶음 포함)의 설명을 조용히 덮어쓰던 것(리뷰 L1)
            return None
        # owner_part name 보충
        result = dict(row)
        result["owner_part"] = owner_part
    return result


class NamespaceNotFound(Exception):
    """업무 영역이 없음(라우터가 404로) — LookupError를 쓰면 KeyError와 섞인다."""


async def _forget_cached_answers(*names: str) -> None:
    """이름이 바뀌거나 지워진 영역의 답변 캐시를 비운다 — 캐시 키가 영역 이름이라, 그 이름이 나중에 다른 영역에 다시 쓰이면
    옛 답이 새 영역에 샌다(이름 바꾸기·지우기 공통)."""
    from shared import cache as sem_cache
    for n in names:
        await sem_cache.invalidate_namespace(n)


async def name_taken_reason(name: str) -> str:
    async with get_conn() as conn:
        kind = await conn.fetchval("SELECT kind FROM ops_namespace WHERE name = $1", name)
    # 상위 묶음은 목록에 안 보여 "이미 있다"만으론 이유를 알 수 없다(리뷰 L8)
    return "상위 업무 영역(묶음) 이름과 같습니다." if kind == "group" else "이미 있는 업무 영역 이름입니다."


async def rename_namespace(old_name: str, new_name: str) -> tuple[int, str]:
    """(상태, 사유). 없는 영역 404, 같은 이름·이미 있는 이름 409 — 예전엔 없는 영역도 409 "이미 있음"이었다."""
    import asyncpg
    async with get_conn() as conn:
        if not await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops_namespace WHERE name = $1)", old_name):
            return 404, "업무 영역을 찾을 수 없습니다."
        if old_name == new_name:
            return 409, "지금 이름과 같습니다."
        try:
            result = await conn.execute("UPDATE ops_namespace SET name = $2 WHERE name = $1", old_name, new_name)
        except asyncpg.exceptions.UniqueViolationError:   # 확인과 변경 사이 동시 요청(막 쓰기 테스트: 500)
            result = "UPDATE 0"
    if result != "UPDATE 1":
        return 409, await name_taken_reason(new_name)
    await _forget_cached_answers(old_name, new_name)
    return 200, ""


async def delete_namespace(name: str) -> tuple[int, str]:
    """(상태, 사유). 하위 영역이 있는 묶음은 지우지 않는다 — 지우면 하위 영역이 조용히 최상위로 빠졌다(막 쓰기 테스트 10/20)."""
    async with get_conn() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT id FROM ops_namespace WHERE name = $1 FOR UPDATE", name)
            if row is None:
                return 404, "업무 영역을 찾을 수 없습니다."
            children = await conn.fetchval("SELECT count(*) FROM ops_namespace WHERE parent_id = $1", row["id"])
            if children:
                return 409, f"하위 업무 영역 {children}개가 있어 지울 수 없습니다. 하위 영역을 먼저 옮기세요."
            # 연결 표는 영역을 글자 키로 가리켜 FK로 같이 안 지워진다 — 남으면 정책 범위가 조용히 "전부"로 바뀐다(리뷰 L6)
            await conn.execute("""DELETE FROM ops_link WHERE (from_type = 'domain' AND from_key = $1) OR (to_type = 'domain' AND to_key = $1)
                                  OR (from_type = 'section' AND from_key LIKE $1 || '|%') OR (to_type = 'section' AND to_key LIKE $1 || '|%')""",
                               str(row["id"]))
            # 나머지는 ON DELETE CASCADE로 자식 테이블 자동 삭제
            await conn.execute("DELETE FROM ops_namespace WHERE id = $1", row["id"])
    await _forget_cached_answers(name)
    return 204, ""


async def list_groups() -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch("SELECT id, name FROM ops_namespace WHERE kind = 'group' ORDER BY name")
    return [dict(r) for r in rows]


async def update_namespace_settings(name: str, body) -> dict:
    """담당 파트·상위 영역 지정. 묶음엔 담당을 두지 않고(데이터 없음), 상위는 묶음만 고를 수 있다(한 단계 트리)."""
    async with get_conn() as conn:
        async with conn.transaction():
            ns = await conn.fetchrow("SELECT id, kind FROM ops_namespace WHERE name = $1 FOR UPDATE", name)
            if ns is None:
                raise NamespaceNotFound("업무 영역을 찾을 수 없습니다.")
            if body.clear_owner or body.owner_part is not None:   # 빈 문자열 = 해제(스키마 설명과 같게, 재리뷰 L1)
                if ns["kind"] == "group":
                    raise ValueError("상위 업무 영역(묶음)에는 담당 파트를 두지 않습니다.")
                owner = None
                if body.owner_part and not body.clear_owner:
                    owner = await conn.fetchval("SELECT id FROM ops_part WHERE name = $1 FOR KEY SHARE", body.owner_part)
                    if owner is None:
                        raise ValueError(f"없는 파트입니다: {body.owner_part}")
                await conn.execute("UPDATE ops_namespace SET owner_part_id = $2 WHERE id = $1", ns["id"], owner)
            if body.clear_parent or body.parent is not None:
                if ns["kind"] == "group":
                    raise ValueError("상위 업무 영역(묶음)은 다른 묶음 아래에 둘 수 없습니다.")
                parent = None
                if body.parent and not body.clear_parent:
                    parent = await conn.fetchval("SELECT id FROM ops_namespace WHERE name = $1 AND kind = 'group'", body.parent)
                    if parent is None:
                        raise ValueError(f"상위 업무 영역(묶음)이 아닙니다: {body.parent}")
                await conn.execute("UPDATE ops_namespace SET parent_id = $2 WHERE id = $1", ns["id"], parent)
            row = await conn.fetchrow("""SELECT n.name, p.name AS owner_part, g.name AS parent_name FROM ops_namespace n
                                         LEFT JOIN ops_part p ON p.id = n.owner_part_id LEFT JOIN ops_namespace g ON g.id = n.parent_id
                                         WHERE n.id = $1""", ns["id"])
    return dict(row)


async def suggest_category_for_content(ns_id: int, content: str) -> Optional[str]:
    """지식 내용을 읽고 기존 업무구분 목록 중 하나를 LLM으로 추천 (새 카테고리는 절대 만들지
    않음 — 제시된 목록 안에서만 고르도록 프롬프트가 강제, 매칭 실패 시 None).

    `service/admin/router.py`의 `POST /categories/suggest` 엔드포인트와 같은 로직을
    공유하려고 분리(2026-09-22, 지식 카테고리 자동화 — `docs/tech/knowledge-category-
    automation.md`). 컨플루언스 벌크 등록처럼 HTTP 컨텍스트 밖(요청 처리 도중)에서도
    호출해야 해서 라우트 핸들러 안에 있던 로직을 재사용 가능한 함수로 뽑았다.
    """
    from service.llm.factory import get_llm_provider
    from service.prompt.loader import get_prompt as load_prompt

    content = (content or "").strip()
    if not content:
        return None

    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT name FROM rag_knowledge_category WHERE namespace_id = $1 ORDER BY name", ns_id
        )
    categories = [r["name"] for r in rows]
    if not categories:
        return None

    categories_str = ", ".join(f'"{c}"' for c in categories)
    fallback = (
        "다음 지식 내용을 읽고, 제시된 업무구분 중 가장 적합한 하나를 골라주세요. "
        "반드시 제시된 업무구분 중 하나의 이름만 답하고, 다른 설명은 절대 하지 마세요.\n\n"
        "업무구분 목록: {categories}\n\n"
        "지식 내용:\n{content}\n\n"
        "가장 적합한 업무구분 이름:"
    )
    template = await load_prompt("category_suggest", fallback)
    # .format() 대신 replace: 지식 내용에 {테이블명} 같은 패턴이 흔해 KeyError 위험이 있음
    prompt = template.replace("{categories}", categories_str).replace("{content}", content[:600])
    if "{categories}" in prompt or "{content}" in prompt:
        prompt = fallback.replace("{categories}", categories_str).replace("{content}", content[:600])
    try:
        # 채팅 답변 규칙이 붙지 않게 분류 전용 시스템 프롬프트(2026-10-13 점검)
        answer, _ = await get_llm_provider().generate(
            context="", question=prompt,
            system_prompt="너는 분류기다. 제시된 업무구분 목록 중 가장 맞는 이름 하나만 그대로 답한다. 설명·기호·근거 표시를 붙이지 않는다.")
        suggested = answer.strip().strip('"').strip("'").strip()
        if suggested not in categories:
            matched = next((c for c in categories if c in suggested or suggested in c), None)
            suggested = matched
    except Exception as e:   # 등록은 계속("분류 확인 필요"로) — 원인은 로그로(10/20 대체 처리 점검)
        logger.warning("업무구분 추천 LLM 실패 — 분류 없이 진행: %s", e)
        suggested = None
    return suggested


# AI가 기존 업무구분 중 못 고르면 들어가는 칸 — 예전 이름 "미분류"(2026-10-08 사용자: 이름이 이상, 사람이 정해야 하는 상태가 드러나게)
UNSORTED_CATEGORY = "분류 확인 필요"


async def ensure_category_exists(ns_id: int, name: str) -> None:
    """카테고리가 없으면 만든다(`ON CONFLICT DO NOTHING`이라 이미 있어도 안전하게 재호출
    가능). 컨플루언스 전용이던 `_ensure_category_exists`(knowledge/router.py)와 동일 SQL —
    2026-09-24부로 모든 등록 경로가 카테고리를 자동 관리하게 되면서 공용 위치로 옮김."""
    async with get_conn() as conn:
        await conn.execute(
            "INSERT INTO rag_knowledge_category (namespace_id, name) VALUES ($1, $2) "
            "ON CONFLICT (namespace_id, name) DO NOTHING",
            ns_id, name,
        )


async def resolve_or_create_category(ns_id: int, category: Optional[str], content: str) -> str:
    """모든 지식 등록 경로 공용 카테고리 자동 관리(2026-09-24) — 사람이 값을 명시하면
    그대로 쓰고, 없으면 LLM이 기존 목록 중에서 추천, 그마저 실패하면 "분류 확인 필요"를 자동
    생성해서 쓴다. 어떤 경로로 등록하든(수동 입력/파일 업로드/텍스트 분할) 사람이
    카테고리를 반드시 골라야 등록이 되던 것을, 아무것도 안 골라도 항상 유효한 값을
    갖도록 뒤집는다 — 컨플루언스 벌크의 `_resolve_confluence_page_category()`와 같은
    철학이지만, 여기엔 페이지 트리 같은 구조 신호가 없어 그 1순위(구조 기반 자동 카테고리
    생성)는 적용 안 하고 LLM 추천 이후 폴백만 공유한다."""
    if category and category.strip():
        return category.strip()
    suggested = await suggest_category_for_content(ns_id, content)
    if suggested:
        return suggested
    await ensure_category_exists(ns_id, UNSORTED_CATEGORY)
    return UNSORTED_CATEGORY
