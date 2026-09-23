"""관리 도메인 — 네임스페이스 CRUD 서비스."""
from __future__ import annotations

from typing import Optional

from core.database import get_conn


async def list_namespaces() -> list[str]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT name FROM ops_namespace ORDER BY name"
        )
    return [r["name"] for r in rows]


async def list_namespaces_detail() -> list[dict]:
    async with get_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT n.id, n.name, n.description,
                   p.name AS owner_part,
                   n.created_at::text,
                   n.created_by_user_id,
                   u.username AS created_by_username,
                   COALESCE(k.cnt, 0) AS knowledge_count,
                   COALESCE(g.cnt, 0) AS glossary_count
            FROM ops_namespace n
            LEFT JOIN ops_part p ON n.owner_part_id = p.id
            LEFT JOIN ops_user u ON n.created_by_user_id = u.id
            LEFT JOIN (
                SELECT namespace_id, COUNT(*) AS cnt FROM rag_knowledge GROUP BY namespace_id
            ) k ON n.id = k.namespace_id
            LEFT JOIN (
                SELECT namespace_id, COUNT(*) AS cnt FROM rag_glossary GROUP BY namespace_id
            ) g ON n.id = g.namespace_id
            ORDER BY n.name
            """
        )
    return [dict(r) for r in rows]


async def create_namespace(
    name: str, description: str = "",
    owner_part: str | None = None, created_by_user_id: int | None = None,
) -> dict:
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
            ON CONFLICT (name) DO UPDATE SET description = EXCLUDED.description
            RETURNING id, name, description, created_at::text
            """,
            name, description, owner_part_id, created_by_user_id,
        )
        # owner_part name 보충
        result = dict(row)
        result["owner_part"] = owner_part
    return result


async def rename_namespace(old_name: str, new_name: str) -> bool:
    async with get_conn() as conn:
        existing = await conn.fetchval("SELECT EXISTS(SELECT 1 FROM ops_namespace WHERE name = $1)", new_name)
        if existing:
            return False
        result = await conn.execute(
            "UPDATE ops_namespace SET name = $2 WHERE name = $1", old_name, new_name,
        )
    renamed = "UPDATE 1" in result
    if renamed:
        # 시맨틱 캐시는 namespace를 문자열로 키에 박아두므로, 이름이 바뀌면 이전 이름의
        # 캐시가 아무도 무효화하지 않는 채로 남는다 — 그 옛 이름이 나중에 다른(전혀
        # 무관한) 네임스페이스로 재사용되면 옛 캐시 답변이 새 네임스페이스에 그대로 샐 수
        # 있어, 이름 양쪽 다 비운다
        from shared import cache as sem_cache
        await sem_cache.invalidate_namespace(old_name)
        await sem_cache.invalidate_namespace(new_name)
    return renamed


async def delete_namespace(name: str) -> bool:
    async with get_conn() as conn:
        # ON DELETE CASCADE로 자식 테이블 자동 삭제됨
        result = await conn.execute("DELETE FROM ops_namespace WHERE name = $1", name)
    deleted = "DELETE 1" in result
    if deleted:
        # 삭제된 이름이 나중에 새 네임스페이스로 재사용될 때, 옛 지식베이스 기반의
        # 캐시 답변이 새 네임스페이스에 새어 들어가는 것을 방지
        from shared import cache as sem_cache
        await sem_cache.invalidate_namespace(name)
    return deleted


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
        answer, _ = await get_llm_provider().generate(context="", question=prompt)
        suggested = answer.strip().strip('"').strip("'").strip()
        if suggested not in categories:
            matched = next((c for c in categories if c in suggested or suggested in c), None)
            suggested = matched
    except Exception:
        suggested = None
    return suggested


UNSORTED_CATEGORY = "미분류"


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
    그대로 쓰고, 없으면 LLM이 기존 목록 중에서 추천, 그마저 실패하면 "미분류"를 자동
    생성해서 쓴다. 어떤 경로로 등록하든(수동 입력/파일 업로드/텍스트 분할/Teams) 사람이
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
