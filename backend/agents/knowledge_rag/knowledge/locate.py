"""지식 등록 위치 판정 — 엔드포인트용 조립(판정 locator + 쓸 수 있는 곳 + AI 판단 원장 기록). 화면이 바로 쓰는 모양으로 돌려준다."""
from typing import Optional

from core.database import OWNER_ROWS_SQL, get_conn
from core.dependencies import edit_denied_reason
from core.security import get_user_llm_credentials
from agents.knowledge_rag.ingestion import locator
from service.decision import ledger


_local_llm = None   # 로컬 LLM 클라이언트는 한 번만(모델 이름 자동 감지 호출을 요청마다 하지 않게)


def _locator_llm():
    """(LLM, 원장 모델 칸, 사용자 자격 증명을 쓰나). 설정 locator_llm=local이고 로컬 주소가 있으면 로컬, 아니면 시스템 LLM —
    어느 쪽이었는지 원장 모델 칸에 남는다(대체가 보이게)."""
    from core.config import settings
    from service.llm.factory import get_llm_provider, get_runtime_config
    global _local_llm
    if settings.locator_llm == "local" and settings.openai_compat_base_url:
        from service.llm.openai_compat import OpenAICompatProvider
        if _local_llm is None:
            _local_llm = OpenAICompatProvider()
        return _local_llm, f"local:{settings.openai_compat_model or 'auto'}", False
    cfg = get_runtime_config()
    p = cfg.get("provider", "")
    sub = cfg.get(p) if isinstance(cfg.get(p), dict) else {}
    # 게이트웨이(inhouse)는 모델 칸이 비어 있기도 해 에이전트 코드로 구분(리플레이 때 "어느 모델의 판단인가")
    tag = f"{p}:{sub.get('model') or sub.get('active_model') or sub.get('agent_code') or ''}"
    if settings.locator_llm == "local":
        tag += " (로컬 주소 없음 → 시스템 LLM)"
    return get_llm_provider(), tag, True


async def locate_for_user(text: str, user: dict, *, preset_namespace: Optional[str] = None) -> dict:
    async with get_conn() as conn:
        cands = await locator.load_candidates(conn)
        profiles = await locator.load_profiles(conn, [c.id for c in cands])
        team_id = await conn.fetchval("SELECT team_id FROM ops_part WHERE id = $1", user["part_id"]) if user.get("part_id") else None
    by_id = {c.id: c for c in cands}
    preset = next((c for c in cands if c.name == preset_namespace), None) if preset_namespace else None
    preset_missing = bool(preset_namespace) and preset is None   # 없는 이름·하위 컬렉션 — AI 판정으로 바뀐 걸 노란 줄로
    # LLM은 연결을 잡지 않은 채 — 판정이 수십 초 걸려도 연결 풀을 막지 않는다
    llm, model_tag, uses_creds = _locator_llm()
    llm_out = await locator.judge_llm(cands, text, llm, user_credentials=get_user_llm_credentials(user) if uses_creds else None,
                                      profiles=profiles, user_team_id=team_id)
    embedding = await locator.embed_head(text)
    async with get_conn() as conn:
        r = await locator.finish(conn, cands, llm_out, embedding, preset_id=preset.id if preset else None, user_team_id=team_id,
                                 preset_missing=preset_missing, text=text)
        owners: dict[int, list[int]] = {}
        for row in await conn.fetch(f"SELECT namespace_id, part_id FROM {OWNER_ROWS_SQL} o WHERE namespace_id = ANY($1::int[])",
                                    list(by_id)):
            owners.setdefault(row["namespace_id"], []).append(row["part_id"])
        decision_id = await ledger.record(
            conn, locator.DECISION_KIND,
            ai_value={"namespace_id": r.ai_id, "doc_type": r.doc_type},
            confident=r.confident,
            signals={"neighbors": r.neighbors, "vote": [r.vote_top, r.vote_n, locator.NEIGHBOR_K], "preset_id": r.preset_id,
                     "proposed_id": r.proposed_id, "ask": r.ask, "suggest_move": r.suggest_move, "fallback": r.fallback,
                     "llm_fail": r.llm_fail, "preset_missing": preset_missing, "text_len": len(text)},
            user_id=user.get("id"), model=model_tag, prompt_version=locator.PROMPT_VERSION)

    def card(ns_id: Optional[int]) -> Optional[dict]:
        c = by_id.get(ns_id) if ns_id is not None else None
        if c is None:
            return None
        return {"name": c.name, "label": c.label, "kind": c.kind,
                "writable": edit_denied_reason(owners.get(c.id, []), user) is None}

    nb_counts: dict[int, int] = {}
    for ns_id, _ in r.neighbors:
        nb_counts[ns_id] = nb_counts.get(ns_id, 0) + 1
    return {
        "decision_id": decision_id,
        "doc_type": r.doc_type,
        "ai": card(r.ai_id),
        "reason": r.reason,
        "confident": r.confident,
        "proposed": card(r.proposed_id),
        "ask": r.ask,
        "options": [o for o in (card(x) for x in r.options) if o],
        "preset": card(r.preset_id),
        "suggest_move": r.suggest_move,
        "neighbors": [{**c, "n": n} for ns_id, n in sorted(nb_counts.items(), key=lambda kv: -kv[1]) if (c := card(ns_id))],
        "neighbor_total": len(r.neighbors),
        "fallback": r.fallback,
    }
