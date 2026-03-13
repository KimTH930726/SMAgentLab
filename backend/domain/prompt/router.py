"""프롬프트 관리 CRUD 라우터."""
import logging

from fastapi import APIRouter, Depends, HTTPException

from core.database import get_conn
from core.dependencies import get_current_user
from domain.prompt.loader import invalidate_cache
from domain.prompt.schemas import PromptOut, PromptUpdate

logger = logging.getLogger(__name__)
router = APIRouter(tags=["prompts"])


def _row_to_out(row) -> dict:
    d = dict(row)
    d["updated_at"] = str(d["updated_at"])
    return d


@router.get("/api/prompts", response_model=list[PromptOut])
async def list_prompts(user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        rows = await conn.fetch("SELECT * FROM ops_prompt ORDER BY id")
    return [_row_to_out(r) for r in rows]


@router.get("/api/prompts/{func_key}", response_model=PromptOut)
async def get_prompt(func_key: str, user: dict = Depends(get_current_user)):
    async with get_conn() as conn:
        row = await conn.fetchrow("SELECT * FROM ops_prompt WHERE func_key = $1", func_key)
    if not row:
        raise HTTPException(status_code=404, detail="프롬프트를 찾을 수 없습니다.")
    return _row_to_out(row)


@router.patch("/api/prompts/{prompt_id}", response_model=PromptOut)
async def update_prompt(prompt_id: int, body: PromptUpdate, user: dict = Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="관리자만 프롬프트를 수정할 수 있습니다.")
    async with get_conn() as conn:
        existing = await conn.fetchrow("SELECT * FROM ops_prompt WHERE id = $1", prompt_id)
        if not existing:
            raise HTTPException(status_code=404, detail="프롬프트를 찾을 수 없습니다.")

        updates = body.model_dump(exclude_none=True)
        if not updates:
            return _row_to_out(existing)

        set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates.keys()))
        set_clauses += ", updated_at = now()"
        vals = list(updates.values())

        row = await conn.fetchrow(
            f"UPDATE ops_prompt SET {set_clauses} WHERE id = $1 RETURNING *",
            prompt_id, *vals,
        )
    invalidate_cache(row["func_key"])
    return _row_to_out(row)
