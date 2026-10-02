"""개선 원장 서비스 — 근거 정정(correction)·지식 누락(missing_knowledge) 신호의 접수·승인·반려.

원칙(2026-10-01, docs: AIOps/지식관리_고도화방안.md 방안 4): 사용자는 고치지 않고 신호만 준다. 사용자 입력과
AI 수정안은 **이 원장에만** 저장되고, 지식/정책 테이블은 담당자 승인 순간에만 바뀐다(버전 교체, 이전 버전
보존). 그래서 "승인 전 검색 미노출"이 상태 필터가 아니라 구조로 보장된다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import asyncpg

from core.config import settings
from core.database import get_conn, resolve_namespace_id
from shared.embedding import embedding_service
from service.improvement import draft as draft_mod

logger = logging.getLogger(__name__)

KIND_BY_TARGET = {
    "knowledge": "correction", "policy_param": "correction", "policy_narrative": "correction",
    "missing": "missing_knowledge",
    "answer": "answer_quality",  # 근거는 맞는데 답변이 잘못 읽음 — 지식은 안 건드리고 답변 품질 신호로만
    "auto": "answer_signal",     # "답변 틀림"만 누르고 의견은 아직 없음 — 대상 미지정(담당자가 정하거나 한 줄이 오면 채움)
}
# 옛 리뷰 신호(rag_knowledge_review_flag)를 이 원장으로 합침(2026-10-02) — 같은 "답변 틀림" 한 번이 리뷰 신호(근거 전부)와
# 정정 검토(AI가 고른 근거)로 두 번 쌓이던 중복을 없앤다. 평가 게이트 "이상해요"도 여기로(kind='search_noise').
SOURCE_SIGNAL = "chat_answer_wrong"
SOURCE_EVAL_GATE = "eval_gate"
# 진입점 구분 — AI 판정이 얼마나 맞았는지(담당자가 대상을 바꾼 비율)를 재려면 직접 지정과 나눠 둬야 한다
SOURCE_DIRECT = "chat_evidence_card"
SOURCE_AUTO = "chat_answer_auto"


class ConflictError(Exception):
    """이미 검토 중인 같은 신고 등 — 라우터가 409로 변환."""


def _fallback_chunk_text(policy_name: str, category_path: list[str], raw_body: str) -> str:
    # service/policy/service.py _chunk_texts()의 폴백 청크와 같은 형식이어야 재생성 판정이 맞는다
    return f"{policy_name} ({' / '.join(category_path or [])}): {raw_body}"


async def _load_original(conn, ns_id: int, target_type: str, target_id: Optional[int],
                         target_sub_id: Optional[int], message_id: Optional[int]) -> dict:
    """신고 대상의 현재 원문 스냅샷. 대상이 이 네임스페이스의 현행 데이터가 아니면 ValueError."""
    if target_type == "knowledge":
        r = await conn.fetchrow(
            "SELECT id, content, category, heading_path FROM rag_knowledge "
            "WHERE id = $1 AND namespace_id = $2 AND status = 'active'", target_id, ns_id)
        if not r:
            raise ValueError("정정 대상 지식을 찾을 수 없거나 이미 바뀌었습니다.")
        return {"content": r["content"], "category": r["category"], "heading_path": list(r["heading_path"] or [])}

    if target_type in ("policy_param", "policy_narrative"):
        item = await conn.fetchrow(
            "SELECT id, policy_name, category_path, raw_body FROM policy_item "
            "WHERE id = $1 AND namespace_id = $2 AND status NOT IN ('deprecated', 'rejected')", target_id, ns_id)
        if not item:
            raise ValueError("정정 대상 정책을 찾을 수 없거나 이미 바뀌었습니다.")
        base = {"policy_name": item["policy_name"], "category_path": list(item["category_path"] or []),
                "raw_body": item["raw_body"]}
        if target_type == "policy_param":
            p = await conn.fetchrow(
                "SELECT name, condition, value, unit FROM policy_param WHERE id = $1 AND policy_item_id = $2",
                target_sub_id, target_id)
            if not p:
                raise ValueError("정정 대상 파라미터를 찾을 수 없습니다.")
            return {**base, "param": dict(p)}
        c = await conn.fetchrow(
            "SELECT chunk_text FROM policy_chunk WHERE id = $1 AND policy_item_id = $2", target_sub_id, target_id)
        if not c:
            raise ValueError("정정 대상 서술을 찾을 수 없습니다.")
        return {**base, "chunk_text": c["chunk_text"]}

    # missing/answer — 고칠 원문이 없으니 그때의 질문·답변을 맥락으로 남긴다
    question = answer = None
    if message_id is not None:
        m = await conn.fetchrow("SELECT conversation_id, content FROM ops_message WHERE id = $1", message_id)
        if m:
            answer = m["content"]
            question = await conn.fetchval(
                "SELECT content FROM ops_message WHERE conversation_id = $1 AND role = 'user' AND id < $2 "
                "ORDER BY id DESC LIMIT 1", m["conversation_id"], message_id)
    return {"question": question, "answer": answer}


def _json(v):
    return json.loads(v) if isinstance(v, str) else v


def _key(target_type: str, target_id: Optional[int], target_sub_id: Optional[int]) -> str:
    """프론트 근거 카드와 같은 key — 관리자 대상 변경·카드 배지가 같은 식별자를 쓴다."""
    return {"knowledge": f"k-{target_id}", "policy_param": f"pp-{target_sub_id}",
            "policy_narrative": f"pn-{target_sub_id}"}.get(target_type, target_type)


def _preview(target_type: str, original: dict) -> str:
    if target_type == "knowledge":
        text = original.get("content") or ""
    elif target_type == "policy_param":
        p = original.get("param") or {}
        val = f"{p.get('value') or ''}{p.get('unit') or ''}"
        text = " · ".join(str(x) for x in (p.get("name"), p.get("condition"), val) if x)
    else:
        text = original.get("chunk_text") or ""
    return text[:200]


async def _load_candidates(conn, ns_id: int, message_id: int) -> list[dict]:
    """그 답변이 실제로 쓴 근거 — 클라이언트가 보낸 id가 아니라 저장된 답변 기록(ops_message)에서만 가져온다.
    이미 바뀐(현행 아닌) 근거는 고칠 대상이 아니라 뺀다. 예전 메시지라 정책 인용에 id가 없으면 정책은 후보 제외."""
    row = await conn.fetchrow("SELECT results, metadata FROM ops_message WHERE id = $1", message_id)
    if not row:
        return []
    refs = [("knowledge", r.get("id"), None) for r in (_json(row["results"]) or []) if isinstance(r, dict)]
    for c in (_json(row["metadata"]) or {}).get("policy_citations") or []:
        if c.get("kind") == "param":
            refs.append(("policy_param", c.get("item_id"), c.get("param_id")))
        else:
            refs.append(("policy_narrative", c.get("item_id"), c.get("chunk_id")))
    out, seen = [], set()
    for target_type, target_id, sub_id in refs:
        if not isinstance(target_id, int) or (target_type != "knowledge" and not isinstance(sub_id, int)):
            continue
        key = _key(target_type, target_id, sub_id)
        if key in seen:
            continue
        seen.add(key)
        try:
            original = await _load_original(conn, ns_id, target_type, target_id, sub_id, None)
        except ValueError:
            continue
        label = f"문서 #{target_id}" if target_type == "knowledge" else f"정책 · {original['policy_name']}"
        out.append({"key": key, "target_type": target_type, "target_id": target_id, "target_sub_id": sub_id,
                    "label": label, "original": original})
    return out


async def _nearest_candidate(candidates: list[dict], user_input: str) -> Optional[int]:
    """LLM 판정 실패 시 대체 — 사용자 의견과 임베딩이 가장 가까운 근거. 틀린 근거와 맞는 근거가 비슷하게 나와
    정확도가 낮으므로 담당자에게 '추정'으로 표시한다."""
    emb = str(await embedding_service.embed(user_input))
    k_ids = [c["target_id"] for c in candidates if c["target_type"] == "knowledge"]
    chunk_ids = [c["target_sub_id"] for c in candidates if c["target_type"] == "policy_narrative"]
    param_items = [c["target_id"] for c in candidates if c["target_type"] == "policy_param"]
    async with get_conn() as conn:
        k = {r["id"]: r["s"] for r in await conn.fetch(
            "SELECT id, 1 - (embedding <=> $1::vector) AS s FROM rag_knowledge WHERE id = ANY($2::int[])", emb, k_ids)}
        ch = {r["id"]: r["s"] for r in await conn.fetch(
            "SELECT id, 1 - (embedding <=> $1::vector) AS s FROM policy_chunk WHERE id = ANY($2::int[])", emb, chunk_ids)}
        it = {r["id"]: r["s"] for r in await conn.fetch(
            "SELECT policy_item_id AS id, MAX(1 - (embedding <=> $1::vector)) AS s FROM policy_chunk "
            "WHERE policy_item_id = ANY($2::int[]) GROUP BY policy_item_id", emb, param_items)}
    scores = [k.get(c["target_id"]) if c["target_type"] == "knowledge"
              else ch.get(c["target_sub_id"]) if c["target_type"] == "policy_narrative"
              else it.get(c["target_id"]) for c in candidates]
    return max((i for i, s in enumerate(scores) if s is not None), key=lambda i: scores[i], default=None)


async def _identify(candidates: list[dict], context: dict, user_input: str) -> tuple:
    """"답변 틀림" 자동 판정 → (target_type, target_id, target_sub_id, original, proposed, ai_verdict)."""
    if not candidates:
        return "missing", None, None, context, None, {
            "verdict": "missing", "method": "no_evidence", "reason": "답변에 근거가 없어 빠진 내용으로 접수"}
    ident = await draft_mod.identify_and_draft(context.get("question"), context.get("answer"), candidates, user_input)
    if ident is not None:
        verdict, idx, proposed = ident["verdict"], ident["index"], ident["proposed"]
        meta = {"verdict": verdict, "method": "llm", "user_claim": ident["user_claim"], "reason": ident["reason"],
                "wrong_part": ident["wrong_part"], "fix_summary": ident["fix_summary"]}
    else:
        # 대체 경로도 임베딩 서비스를 부른다 — 여기까지 실패하면 신고 자체가 500으로 사라지므로(원칙: AI가 실패해도
        # 신고는 남는다) "빠진 내용"으로 접수하고 담당자가 대상을 지정하게 한다(/code-review).
        try:
            idx = await _nearest_candidate(candidates, user_input)
            reason = "AI 판정 실패 — 의견과 가장 비슷한 근거로 추정"
        except Exception:
            logger.warning("정정 대상 유사도 추정도 실패 — 빠진 내용으로 접수, 담당자 지정", exc_info=True)
            idx, reason = None, "AI 판정을 못 했어요 — 담당자가 대상을 지정해요"
        verdict, proposed = ("evidence" if idx is not None else "missing"), None
        meta = {"verdict": verdict, "method": "similarity", "reason": reason}
    if verdict == "evidence":
        c = candidates[idx]
        meta |= {"key": c["key"], "label": c["label"], "preview": _preview(c["target_type"], c["original"])}
        return c["target_type"], c["target_id"], c["target_sub_id"], c["original"], proposed, meta
    if verdict == "missing":
        return "missing", None, None, context, proposed, meta
    return "answer", None, None, context, None, meta


async def _write_item(conn, values: tuple, signal_id: Optional[int]) -> int:
    """신고 저장 — 신호 건이 있으면 그 건을 채우고, 없으면 새로 만든다. savepoint 안에서 실행해 중복(유니크 위반)이
    나도 바깥 트랜잭션은 이어갈 수 있게 한다."""
    async with conn.transaction():
        if signal_id is not None:
            return await conn.fetchval("""
                UPDATE ops_improvement_item
                SET kind = $1, source = $2, namespace_id = $3, message_id = $4, reporter_user_id = $5,
                    target_type = $6, target_id = $7, target_sub_id = $8, user_input = $9,
                    original = $10::jsonb, proposed = $11::jsonb, candidates = $12::jsonb, ai_verdict = $13::jsonb
                WHERE id = $14 RETURNING id
            """, *values, signal_id)
        return await conn.fetchval("""
            INSERT INTO ops_improvement_item
                (kind, source, namespace_id, message_id, reporter_user_id, target_type, target_id,
                 target_sub_id, user_input, original, proposed, candidates, ai_verdict)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11::jsonb, $12::jsonb, $13::jsonb)
            RETURNING id
        """, *values)


async def create_item(namespace: str, user: dict, *, target_type: str, target_id: Optional[int],
                      target_sub_id: Optional[int], message_id: Optional[int], user_input: str) -> dict:
    """신고 접수. 직접 지정(근거 카드)이면 원장 저장 → AI 초안. 자동(auto, "답변 틀림")이면 판정+초안을 한 번에
    받은 뒤 저장. 어느 쪽이든 초안이 실패해도 신고는 남고(담당자가 직접 작성), 승인 전엔 아무 데도 반영되지 않는다."""
    auto = target_type == "auto"
    if auto and message_id is None:
        raise ValueError("어느 답변에 대한 신고인지 알 수 없습니다.")
    if not auto and target_type != "missing" and target_id is None:
        raise ValueError("정정 대상이 지정되지 않았습니다.")
    if target_type in ("policy_param", "policy_narrative") and target_sub_id is None:
        raise ValueError("정정할 파라미터/서술이 지정되지 않았습니다.")
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")
        # message_id는 클라이언트 입력 — 본인 대화의 이 네임스페이스 메시지만 허용. 안 그러면 "빠진 내용"
        # 신고 응답(original)에 남의 대화 질문·답변이 실려 id만 바꿔가며 열람할 수 있다(/code-review 지적).
        if message_id is not None:
            owned = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM ops_message m JOIN ops_conversation c ON c.id = m.conversation_id "
                "WHERE m.id = $1 AND c.user_id = $2 AND c.namespace_id = $3)", message_id, user.get("id"), ns_id)
            if owned is not True:
                raise ValueError("이 답변에 대해서는 신고할 수 없습니다.")
        # 후보는 담당자 대상 변경용으로 직접 지정 신고에도 저장한다
        candidates = await _load_candidates(conn, ns_id, message_id) if message_id is not None else []
        context = await _load_original(conn, ns_id, "missing", None, None, message_id)
        original = None if auto else await _load_original(conn, ns_id, target_type, target_id, target_sub_id, message_id)

    proposed, verdict = None, None
    if auto:
        target_type, target_id, target_sub_id, original, proposed, verdict = await _identify(candidates, context, user_input)
    stored_candidates = [{k: c[k] for k in ("key", "target_type", "target_id", "target_sub_id", "label")}
                         | {"preview": _preview(c["target_type"], c["original"])} for c in candidates]
    values = (KIND_BY_TARGET[target_type], SOURCE_AUTO if auto else SOURCE_DIRECT, ns_id, message_id,
              user.get("id"), target_type, target_id, target_sub_id, user_input,
              json.dumps(original, ensure_ascii=False),
              json.dumps(proposed, ensure_ascii=False) if proposed is not None else None,
              json.dumps(stored_candidates, ensure_ascii=False),
              json.dumps(verdict, ensure_ascii=False) if verdict is not None else None)
    conflict = False
    async with get_conn() as conn:
        async with conn.transaction():
            # "답변 틀림"을 누른 순간 같은 답변으로 신호 건이 먼저 생겨 있다(record_answer_signal) —
            # 한 줄이 들어오면 새로 만들지 않고 그 건을 채운다(신고 1번 = 검토 1건).
            signal_id = None
            if message_id is not None:
                signal_id = await conn.fetchval(
                    "SELECT id FROM ops_improvement_item WHERE message_id = $1 AND reporter_user_id = $2 "
                    "AND kind = 'answer_signal' AND status = 'pending' ORDER BY id DESC LIMIT 1 FOR UPDATE",
                    message_id, user.get("id"))
            try:
                item_id = await _write_item(conn, values, signal_id)
            except asyncpg.UniqueViolationError:
                # 같은 근거로 이미 대기 중인 신고가 있다 — 방금 생긴 신호 건은 그 신고와 중복이라 지운다(데이터 손실 없음)
                conflict = True
                if signal_id is not None:
                    await conn.execute("DELETE FROM ops_improvement_item WHERE id = $1", signal_id)
    if conflict:  # 커밋한 뒤에 알린다(트랜잭션 안에서 raise하면 중복 신호 삭제까지 되돌려짐)
        raise ConflictError("이 근거에 대해 이미 검토 중인 정정 신고가 있습니다.")

    if proposed is None and target_type != "answer":
        proposed = await draft_mod.draft_correction(
            target_type, original, user_input, None if target_type == "missing" else context)
        if proposed is not None:
            async with get_conn() as conn:
                await conn.execute("UPDATE ops_improvement_item SET proposed = $1::jsonb WHERE id = $2",
                                   json.dumps(proposed, ensure_ascii=False), item_id)
    return {"id": item_id, "status": "pending", "kind": KIND_BY_TARGET[target_type], "target_type": target_type,
            "ai_verdict": verdict, "original": original, "proposed": proposed}


async def record_answer_signal(ns_id: int, message_id: int, reporter_id: Optional[int]) -> Optional[int]:
    """"답변 틀림"을 누른 순간 원장에 1건 — 한 줄 의견이 이어서 오면 create_item이 이 건을 채운다. 같은 사람의 같은
    답변에 대기 건이 이미 있으면 그대로 둔다(여러 번 눌러도 1건). best-effort: 실패해도 피드백 자체는 막지 않는다."""
    try:
        async with get_conn() as conn:
            existing = await conn.fetchval(
                "SELECT id FROM ops_improvement_item WHERE message_id = $1 AND reporter_user_id IS NOT DISTINCT FROM $2 "
                "AND status = 'pending'", message_id, reporter_id)
            if existing:
                return existing
            candidates = await _load_candidates(conn, ns_id, message_id)
            context = await _load_original(conn, ns_id, "missing", None, None, message_id)
            stored = [{k: c[k] for k in ("key", "target_type", "target_id", "target_sub_id", "label")}
                      | {"preview": _preview(c["target_type"], c["original"])} for c in candidates]
            item_id = await conn.fetchval("""
                INSERT INTO ops_improvement_item
                    (kind, source, namespace_id, message_id, reporter_user_id, target_type, original, candidates)
                VALUES ('answer_signal', $1, $2, $3, $4, 'auto', $5::jsonb, $6::jsonb) RETURNING id
            """, SOURCE_SIGNAL, ns_id, message_id, reporter_id, json.dumps(context, ensure_ascii=False),
                json.dumps(stored, ensure_ascii=False))
    except Exception:
        logger.warning("답변 틀림 신호 기록 실패 (message=%s)", message_id, exc_info=True)
        return None
    if candidates:
        # 응답을 막지 않도록 백그라운드로 — 사용자가 이어서 한 줄을 보내면 그쪽이 우선(analyze_signal이 덮어쓰지 않음)
        task = asyncio.create_task(analyze_signal(item_id, ns_id, message_id))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
    return item_id


_BACKGROUND: set = set()  # 백그라운드 분석 태스크 참조 유지(가비지 컬렉션으로 중간에 사라지지 않게)


async def analyze_signal(item_id: int, ns_id: int, message_id: int) -> bool:
    """의견 없는 "답변 틀림" — AI가 질문·답변·근거의 어긋남으로 원인을 추정해 대상을 미리 골라 둔다(사실은 모르므로
    "추정"으로 표시, 수정안은 확실할 때만). 아직 의견이 없을 때만 갱신 — 그 사이 사용자 한 줄이 오면 손대지 않는다."""
    try:
        async with get_conn() as conn:
            candidates = await _load_candidates(conn, ns_id, message_id)
            context = await _load_original(conn, ns_id, "missing", None, None, message_id)
        if not candidates:
            return False
        out = await draft_mod.analyze_without_opinion(context.get("question"), context.get("answer"), candidates)
        if out is None:
            return False
        meta = {"verdict": out["verdict"], "method": "llm_no_opinion", "reason": out["reason"],
                "wrong_part": out["wrong_part"], "fix_summary": out["fix_summary"]}
        target_type, target_id, sub_id, original = "auto", None, None, context
        if out["verdict"] == "evidence":
            c = candidates[out["index"]]
            target_type, target_id, sub_id, original = c["target_type"], c["target_id"], c["target_sub_id"], c["original"]
            meta |= {"key": c["key"], "label": c["label"], "preview": _preview(c["target_type"], c["original"])}
        elif out["verdict"] == "answer_error":
            target_type = "answer"
        async with get_conn() as conn:
            res = await conn.execute("""
                UPDATE ops_improvement_item
                SET kind = 'answer_signal', target_type = $2, target_id = $3, target_sub_id = $4, original = $5::jsonb,
                    proposed = $6::jsonb, ai_verdict = $7::jsonb
                WHERE id = $1 AND status = 'pending' AND user_input IS NULL
            """, item_id, target_type, target_id, sub_id, json.dumps(original, ensure_ascii=False),
                json.dumps(out["proposed"], ensure_ascii=False) if out["proposed"] else None,
                json.dumps(meta, ensure_ascii=False))
        return res.endswith(" 1")
    except Exception:
        logger.warning("의견 없는 답변 틀림 추정 실패 (item=%s)", item_id, exc_info=True)
        return False


async def analyze_item(item_id: int) -> bool:
    """담당자가 "AI로 원인 추정"을 누름 — 이관된 옛 신호처럼 클릭 시점 분석을 못 받은 의견 없는 건용."""
    async with get_conn() as conn:
        item = await conn.fetchrow(
            "SELECT namespace_id, message_id, user_input, status FROM ops_improvement_item WHERE id = $1", item_id)
    if not item or item["status"] != "pending":
        raise ValueError("대기 중인 신고가 아닙니다.")
    if item["user_input"]:
        raise ValueError("사용자 의견이 있는 신고는 이미 그 의견으로 판정돼 있습니다.")
    if item["message_id"] is None:
        raise ValueError("어느 답변의 신고인지 알 수 없어 추정할 수 없습니다(대화가 삭제됨).")
    return await analyze_signal(item_id, item["namespace_id"], item["message_id"])


async def record_search_noise(namespace: str, knowledge_id: int, reporter_id: Optional[int],
                              query: Optional[str]) -> int:
    """평가 게이트 즉석 질의에서 "이 결과 이상하다"고 표시한 지식 — 같은 지식의 대기 건이 있으면 중복 생성 안 함."""
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"네임스페이스를 찾을 수 없습니다: {namespace}")
        existing = await conn.fetchval(
            "SELECT id FROM ops_improvement_item WHERE kind = 'search_noise' AND target_id = $1 AND status = 'pending'",
            knowledge_id)
        if existing:
            return existing
        original = await _load_original(conn, ns_id, "knowledge", knowledge_id, None, None)
        note = f"평가 게이트 질의 \"{query}\" 결과에서 '이상해요'로 표시" if query else "평가 게이트에서 '이상해요'로 표시"
        return await conn.fetchval("""
            INSERT INTO ops_improvement_item
                (kind, source, namespace_id, reporter_user_id, target_type, target_id, user_input, original)
            VALUES ('search_noise', $1, $2, $3, 'knowledge', $4, $5, $6::jsonb) RETURNING id
        """, SOURCE_EVAL_GATE, ns_id, reporter_id, knowledge_id, note, json.dumps(original, ensure_ascii=False))


async def is_part_owned_by(namespace: str, user: dict) -> bool:
    """소유 파트가 지정돼 있고 그게 이 사용자의 파트인가(공용 네임스페이스는 False)."""
    async with get_conn() as conn:
        return bool(await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM ops_namespace n JOIN ops_user u ON u.part_id = n.owner_part_id "
            "WHERE n.name = $1 AND u.id = $2 AND n.owner_part_id IS NOT NULL)", namespace, user.get("id")))


async def item_namespace(item_id: int) -> Optional[str]:
    """권한 확인용 — 이 신고가 속한 파트(처리 권한 = 그 파트 담당자 + 관리자)."""
    async with get_conn() as conn:
        return await conn.fetchval(
            "SELECT n.name FROM ops_improvement_item i JOIN ops_namespace n ON n.id = i.namespace_id WHERE i.id = $1",
            item_id)


async def retarget(item_id: int, approver: dict, key: str) -> dict:
    """담당자가 AI 판정 대상을 바꾼다(다른 근거 / missing / answer) — 원문 다시 읽고 그 대상 기준으로 초안 재생성."""
    async with get_conn() as conn:
        item = await conn.fetchrow("SELECT * FROM ops_improvement_item WHERE id = $1", item_id)
        if not item or item["status"] != "pending":
            raise ValueError("대기 중인 신고가 아닙니다.")
        ns_id = item["namespace_id"]
        context = await _load_original(conn, ns_id, "missing", None, None, item["message_id"])
        if key in ("missing", "answer"):
            target_type, target_id, sub_id, original = key, None, None, context
        else:
            c = next((c for c in (_json(item["candidates"]) or []) if c["key"] == key), None)
            if c is None:
                raise ValueError("이 답변의 근거가 아닙니다.")
            target_type, target_id, sub_id = c["target_type"], c["target_id"], c["target_sub_id"]
            original = await _load_original(conn, ns_id, target_type, target_id, sub_id, None)
    # 사용자 의견이 없으면 맞는 내용 정보가 없다 — 자리표시 문구로 초안을 만들면 원문 그대로이거나(무의미한 새 버전),
    # "빠진 내용"이면 틀렸다고 신고된 답변을 지식으로 옮겨 적는다(/code-review). 담당자가 직접 수정한다.
    proposed = None if target_type == "answer" or not item["user_input"] else await draft_mod.draft_correction(
        target_type, original, item["user_input"], None if target_type == "missing" else context)
    # 변경 이력은 AI 판정이 있던 신고에서 대상이 실제로 바뀔 때만 남긴다 — 같은 대상 재생성("초안 다시 만들기")을
    # 변경으로 세면 "담당자가 AI 판정을 뒤집은 비율" 측정이 부풀고, 직접 지정 신고엔 판정 자체가 없다(/code-review).
    verdict = _json(item["ai_verdict"])
    old_key = _key(item["target_type"], item["target_id"], item["target_sub_id"])
    if verdict and old_key != key:
        verdict = dict(verdict) | {"retargeted": {"from": old_key, "to": key, "by": approver.get("id")}}
    async with get_conn() as conn:
        try:
            await conn.execute("""
                UPDATE ops_improvement_item
                SET kind = $2, target_type = $3, target_id = $4, target_sub_id = $5, original = $6::jsonb,
                    proposed = $7::jsonb, ai_verdict = $8::jsonb
                WHERE id = $1 AND status = 'pending'
            """, item_id, KIND_BY_TARGET[target_type], target_type, target_id, sub_id,
                json.dumps(original, ensure_ascii=False),
                json.dumps(proposed, ensure_ascii=False) if proposed is not None else None,
                json.dumps(verdict, ensure_ascii=False) if verdict else None)
        except asyncpg.UniqueViolationError:
            raise ConflictError("같은 신고자의 같은 근거 신고가 이미 대기 중입니다.")
    return {"id": item_id, "target_type": target_type, "original": original, "proposed": proposed}


async def pending_status(knowledge_ids: list[int], policy_item_ids: list[int]) -> dict:
    """근거별 "정정 검토 중" 여부 — 채팅 카드 배지용(실시간·재조회·캐시 응답 모두 이 경로).
    정책은 항목(item)이 아니라 파라미터/서술 단위로 돌려준다 — 항목 단위면 한 파라미터 신고가 같은 항목의
    다른 카드까지 전부 "검토 중"으로 덮어 다른 부분을 신고할 수 없게 된다(/code-review 지적)."""
    out = {"knowledge": [], "policy_param": [], "policy_narrative": []}
    if not knowledge_ids and not policy_item_ids:
        return out
    async with get_conn() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT target_type, target_id, target_sub_id FROM ops_improvement_item
            WHERE status = 'pending' AND kind = 'correction'
              AND ((target_type = 'knowledge' AND target_id = ANY($1::int[]))
                OR (target_type IN ('policy_param', 'policy_narrative') AND target_id = ANY($2::int[])))
        """, knowledge_ids or [], policy_item_ids or [])
    for r in rows:
        out[r["target_type"]].append(r["target_id"] if r["target_type"] == "knowledge" else r["target_sub_id"])
    return out


_LIST_SQL = """
    SELECT i.id, i.kind, n.name AS namespace, i.message_id, i.target_type, i.target_id, i.target_sub_id,
           i.user_input, i.original, i.proposed, i.status, i.reject_reason, i.applied_target_id,
           i.created_at, i.decided_at, i.reporter_seen_at, i.source, i.candidates, i.ai_verdict,
           ru.username AS reporter, du.username AS decided_by,
           -- 근거를 대상으로 한 건도 어떤 질문·답변에서 나온 신고인지 카드에 보이도록(원문은 근거 스냅샷뿐이라)
           m.content AS answer_text,
           (SELECT u.content FROM ops_message u WHERE u.conversation_id = m.conversation_id AND u.role = 'user'
              AND u.id < m.id ORDER BY u.id DESC LIMIT 1) AS question_text
    FROM ops_improvement_item i
    JOIN ops_namespace n ON n.id = i.namespace_id
    LEFT JOIN ops_message m ON m.id = i.message_id
    LEFT JOIN ops_user ru ON ru.id = i.reporter_user_id
    LEFT JOIN ops_user du ON du.id = i.decided_by
"""


def _row(r) -> dict:
    d = dict(r)
    for k in ("original", "proposed", "candidates", "ai_verdict"):
        if isinstance(d.get(k), str):
            d[k] = json.loads(d[k])
    for k in ("created_at", "decided_at", "reporter_seen_at"):
        if d.get(k) is not None:
            d[k] = d[k].isoformat()
    return d


async def list_items(namespace: Optional[str], status: Optional[str], limit: int = 200) -> list[dict]:
    conds, args = [], []
    if namespace:
        args.append(namespace)
        conds.append(f"n.name = ${len(args)}")
    if status:
        args.append(status)
        conds.append(f"i.status = ${len(args)}")
    args.append(limit)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    async with get_conn() as conn:
        rows = await conn.fetch(f"{_LIST_SQL} {where} ORDER BY i.created_at DESC LIMIT ${len(args)}", *args)
    return [_row(r) for r in rows]


async def pending_count() -> dict:
    """전체 대기 건수(관리자 메뉴 배지) + 파트별(정정 검토 탭은 파트 단위라, 전체만 주면 배지는 3인데
    탭은 비어 보이는 불일치가 생긴다 — /code-review 지적)."""
    async with get_conn() as conn:
        rows = await conn.fetch(
            "SELECT n.name, COUNT(*) AS cnt FROM ops_improvement_item i JOIN ops_namespace n ON n.id = i.namespace_id "
            "WHERE i.status = 'pending' GROUP BY n.name")
    by_ns = {r["name"]: int(r["cnt"]) for r in rows}
    return {"count": sum(by_ns.values()), "by_namespace": by_ns}


async def list_mine(user_id: int, unseen_only: bool) -> list[dict]:
    """신고자에게 결과 알림 — 처리됐는데 아직 확인 안 한 것."""
    extra = "AND i.status <> 'pending' AND i.reporter_seen_at IS NULL" if unseen_only else ""
    async with get_conn() as conn:
        rows = await conn.fetch(f"{_LIST_SQL} WHERE i.reporter_user_id = $1 {extra} ORDER BY i.created_at DESC LIMIT 50",
                                user_id)
    return [_row(r) for r in rows]


async def mark_seen(user_id: int, ids: list[int]) -> int:
    async with get_conn() as conn:
        res = await conn.execute(
            "UPDATE ops_improvement_item SET reporter_seen_at = NOW() "
            "WHERE reporter_user_id = $1 AND id = ANY($2::int[]) AND status <> 'pending' AND reporter_seen_at IS NULL",
            user_id, ids)
    return int(res.split()[-1]) if res else 0


# ── 승인 = 반영(버전 교체 / 누락 지식 등록) ─────────────────────────────────────

def _validate_proposed(target_type: str, proposed: Optional[dict]) -> dict:
    if not proposed:
        raise ValueError("수정안이 비어 있습니다 — 수정안을 작성한 뒤 승인하세요.")
    for k in draft_mod._REQUIRED[target_type]:
        if not str(proposed.get(k) or "").strip():
            raise ValueError(f"수정안의 '{k}'가 비어 있습니다.")
    return proposed


async def _apply_knowledge(conn, ns_id: int, target_id: int, content: str, embedding: list[float]) -> int:
    old = await conn.fetchrow("SELECT * FROM rag_knowledge WHERE id = $1 AND namespace_id = $2 FOR UPDATE",
                              target_id, ns_id)
    if not old or old["status"] != "active":
        raise ValueError("원본 지식이 이미 바뀌었거나 현행이 아닙니다 — 이 신고는 반려하세요.")
    new_id = await conn.fetchval("""
        INSERT INTO rag_knowledge
            (namespace_id, content, embedding, base_weight, category, created_by_part, created_by_user_id,
             source_file, source_chunk_idx, source_type, ingestion_job_id, status,
             logical_document_id, version, supersedes_id, embedding_model, reviewed_at, owner,
             confluence_page_id, confluence_version, heading_path)
        VALUES ($1, $2, $3::vector, $4, $5, $6, $7, $8, $9, $10, $11, 'active',
                $12, $13, $14, $15, NOW(), $16, $17, $18, $19)
        RETURNING id
    """, ns_id, content, str(embedding), old["base_weight"], old["category"], old["created_by_part"],
        old["created_by_user_id"], old["source_file"], old["source_chunk_idx"], old["source_type"],
        old["ingestion_job_id"], old["logical_document_id"] or old["id"], (old["version"] or 1) + 1, old["id"],
        settings.embedding_model, old["owner"], old["confluence_page_id"], old["confluence_version"],
        old["heading_path"])
    await conn.execute("UPDATE rag_knowledge SET status = 'deprecated' WHERE id = $1", old["id"])
    return new_id


async def _apply_missing(conn, ns_id: int, content: str, embedding: list[float], category: str,
                         approver: dict) -> int:
    return await conn.fetchval("""
        INSERT INTO rag_knowledge (namespace_id, content, embedding, base_weight, category, created_by_part,
                                   created_by_user_id, source_type, status, embedding_model, reviewed_at, owner)
        VALUES ($1, $2, $3::vector, 1.0, $4, $5, $6, 'correction', 'active', $7, NOW(), $8)
        RETURNING id
    """, ns_id, content, str(embedding), category, approver.get("part"), approver.get("id"),
        settings.embedding_model, approver.get("username"))


def _policy_chunk_rewrites(old: dict, chunks: list, item: dict, proposed: dict) -> dict[int, str]:
    """새 버전에서 본문이 바뀌는 청크 {chunk_id: 새 텍스트} — 나머지는 임베딩째 복사.
    정정 대상 서술 청크 + (원문이 바뀌었으면) 원문 통째로 만든 폴백 청크(안 그러면 옛 값이 검색됨)."""
    new_raw = proposed.get("raw_body") or old["raw_body"]
    old_fallback = _fallback_chunk_text(old["policy_name"], list(old["category_path"] or []), old["raw_body"])
    out = {}
    for c in chunks:
        if item["target_type"] == "policy_narrative" and c["id"] == item["target_sub_id"]:
            out[c["id"]] = proposed["chunk_text"]
        elif new_raw != old["raw_body"] and c["chunk_text"] == old_fallback:
            out[c["id"]] = _fallback_chunk_text(old["policy_name"], list(old["category_path"] or []), new_raw)
    return out


_POLICY_CHUNKS_SQL = "SELECT id, chunk_text, chunk_idx FROM policy_chunk WHERE policy_item_id = $1 ORDER BY chunk_idx"


async def _prepare_policy(ns_id: int, item: dict, proposed: dict) -> dict[int, tuple[str, list[float]]]:
    """재임베딩은 트랜잭션 밖에서 미리 — 임베딩 호출 동안 원본 행 잠금을 쥐고 있지 않도록(지식 경로와 동일)."""
    async with get_conn() as conn:
        old = await conn.fetchrow("SELECT * FROM policy_item WHERE id = $1 AND namespace_id = $2",
                                  item["target_id"], ns_id)
        if not old:
            raise ValueError("원본 정책을 찾을 수 없습니다.")
        chunks = await conn.fetch(_POLICY_CHUNKS_SQL, old["id"])
    rewrites = _policy_chunk_rewrites(dict(old), chunks, item, proposed)
    return {cid: (text, await embedding_service.embed(text)) for cid, text in rewrites.items()}


async def _apply_policy(conn, ns_id: int, item: dict, proposed: dict, approver: dict,
                        prepared: dict[int, tuple[str, list[float]]]) -> int:
    """정책 새 버전: policy_item 복사(raw_body=수정안) + param/chunk 복사(정정 대상만 교체) + 원본 deprecated.
    `prepared`는 트랜잭션 밖에서 미리 만든 {chunk_id: (새 텍스트, 임베딩)}.
    content_hash·pipeline_version·source 위치는 원본 그대로 — 엑셀이 안 바뀐 채 재업로드되면 스킵돼 정정이 유지되고,
    엑셀 원본이 실제로 바뀌면 그 버전이 정정을 대체한다(원본 소유자 우선)."""
    old = await conn.fetchrow("SELECT * FROM policy_item WHERE id = $1 AND namespace_id = $2 FOR UPDATE",
                              item["target_id"], ns_id)
    if not old or old["status"] in ("deprecated", "rejected"):
        raise ValueError("원본 정책이 이미 바뀌었거나 현행이 아닙니다 — 이 신고는 반려하세요.")
    chunks = await conn.fetch(_POLICY_CHUNKS_SQL, old["id"])
    rewrites = _policy_chunk_rewrites(dict(old), chunks, item, proposed)
    # 잠금 후 다시 계산한 결과가 미리 임베딩한 것과 다르면(준비 사이에 원본 편집) 옛 임베딩을 쓰게 되므로 중단
    if rewrites != {cid: text for cid, (text, _) in prepared.items()}:
        raise ValueError("승인 준비 중 원본 정책이 바뀌었습니다 — 다시 승인해 주세요.")
    new_raw = proposed.get("raw_body") or old["raw_body"]
    new_id = await conn.fetchval("""
        INSERT INTO policy_item
            (namespace_id, system_key, category_path, policy_name, raw_body, remark, source_file, source_sheet,
             source_row, content_hash, status, logical_id, version, supersedes_id, parse_status,
             unresolved_segments, reviewed_at, reviewed_by, pipeline_version)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'active', $11, $12, $13, $14, $15, NOW(), $16, $17)
        RETURNING id
    """, ns_id, old["system_key"], old["category_path"], old["policy_name"], new_raw, old["remark"],
        old["source_file"], old["source_sheet"], old["source_row"], old["content_hash"], old["logical_id"],
        (old["version"] or 1) + 1, old["id"], old["parse_status"], old["unresolved_segments"],
        approver.get("id"), old["pipeline_version"])

    for p in await conn.fetch("SELECT * FROM policy_param WHERE policy_item_id = $1 ORDER BY id", old["id"]):
        src = proposed if (item["target_type"] == "policy_param" and p["id"] == item["target_sub_id"]) else p
        await conn.execute(
            "INSERT INTO policy_param (policy_item_id, name, condition, value, unit, external_source) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            new_id, src["name"] or "", src.get("condition"), src.get("value"), src.get("unit"), p["external_source"])

    for c in chunks:
        if c["id"] not in prepared:
            await conn.execute(
                "INSERT INTO policy_chunk (policy_item_id, chunk_text, embedding, chunk_idx) "
                "SELECT $1, chunk_text, embedding, chunk_idx FROM policy_chunk WHERE id = $2", new_id, c["id"])
            continue
        text, emb = prepared[c["id"]]
        await conn.execute(
            "INSERT INTO policy_chunk (policy_item_id, chunk_text, embedding, chunk_idx) VALUES ($1, $2, $3::vector, $4)",
            new_id, text, str(emb), c["chunk_idx"])

    await conn.execute("UPDATE policy_item SET status = 'deprecated', updated_at = NOW() WHERE id = $1", old["id"])
    return new_id


async def _close_superseded(conn, item: dict, approver: dict) -> int:
    """승인으로 원본이 deprecated가 되면, 같은 원본을 겨눈 다른 대기 신고는 더 이상 승인할 수 없다(대상이 현행 아님).
    관리자가 하나씩 반려하지 않아도 되게 같은 트랜잭션에서 자동 종료 — 신고자에겐 사유와 함께 결과 알림이 간다.
    정책은 항목 버전이 통째로 바뀌므로 같은 항목의 다른 파라미터/서술 신고도 함께 종료(새 버전에서 다시 신고 가능)."""
    if item["target_type"] in ("missing", "answer"):
        return 0
    types = ["knowledge"] if item["target_type"] == "knowledge" else ["policy_param", "policy_narrative"]
    res = await conn.execute("""
        UPDATE ops_improvement_item
        SET status = 'rejected', decided_by = $4, decided_at = NOW(),
            reject_reason = '같은 근거에 대한 다른 정정(#' || $5 || ')이 먼저 반영되어 자동 종료 — 반영된 내용에도 문제가 있으면 다시 신고해 주세요.'
        WHERE status = 'pending' AND id <> $1 AND target_id = $2 AND target_type = ANY($3::text[])
    """, item["id"], item["target_id"], types, approver.get("id"), str(item["id"]))
    return int(res.split()[-1]) if res else 0


async def approve(item_id: int, approver: dict, proposed_override: Optional[dict]) -> dict:
    async with get_conn() as conn:
        item = await conn.fetchrow("SELECT * FROM ops_improvement_item WHERE id = $1", item_id)
    if not item or item["status"] != "pending":
        raise ValueError("대기 중인 신고가 아닙니다.")
    item = dict(item)
    stored = json.loads(item["proposed"]) if isinstance(item["proposed"], str) else item["proposed"]
    if item["target_type"] == "answer":
        raise ValueError("답변 오류 판정은 반영할 지식이 없습니다 — 대상을 바꾸거나 반려(종료)하세요.")
    if item["target_type"] == "auto":
        raise ValueError("정정 대상을 먼저 정하세요 — '정정 대상'에서 근거를 고르거나 반려(종료)하세요.")
    proposed = _validate_proposed(item["target_type"], proposed_override or stored)
    # 지식 본문 임베딩은 트랜잭션 밖에서 미리(트랜잭션은 DB 쓰기만 짧게)
    embedding = None
    if item["target_type"] in ("knowledge", "missing"):
        embedding = await embedding_service.embed(proposed["content"])
    prepared = {}
    if item["target_type"] in ("policy_param", "policy_narrative"):
        prepared = await _prepare_policy(item["namespace_id"], item, proposed)
    category = None
    if item["target_type"] == "missing":
        # 카테고리 자동 결정은 LLM을 부를 수 있어 행 잠금을 쥔 트랜잭션 밖에서
        from service.admin.service import resolve_or_create_category
        category = await resolve_or_create_category(item["namespace_id"], None, proposed["content"])

    async with get_conn() as conn:
        async with conn.transaction():
            locked = await conn.fetchval(
                "SELECT status FROM ops_improvement_item WHERE id = $1 FOR UPDATE", item_id)
            if locked != "pending":
                raise ValueError("이미 처리된 신고입니다.")
            ns_id = item["namespace_id"]
            if item["target_type"] == "knowledge":
                new_id = await _apply_knowledge(conn, ns_id, item["target_id"], proposed["content"], embedding)
            elif item["target_type"] == "missing":
                new_id = await _apply_missing(conn, ns_id, proposed["content"], embedding, category, approver)
            else:
                new_id = await _apply_policy(conn, ns_id, item, proposed, approver, prepared)
            await conn.execute("""
                UPDATE ops_improvement_item
                SET status = 'approved', proposed = $2::jsonb, applied_target_id = $3, decided_by = $4, decided_at = NOW()
                WHERE id = $1
            """, item_id, json.dumps(proposed, ensure_ascii=False), new_id, approver.get("id"))
            superseded = await _close_superseded(conn, item, approver)
            ns_name = await conn.fetchval("SELECT name FROM ops_namespace WHERE id = $1", ns_id)

    # 승인 반영 후 시맨틱 캐시엔 옛 답이 TTL 동안 남아 있다 — 네임스페이스 캐시를 비운다(best-effort)
    try:
        from shared.cache import invalidate_namespace
        await invalidate_namespace(ns_name)
    except Exception:
        logger.warning("정정 승인 후 시맨틱 캐시 무효화 실패 (item=%s)", item_id, exc_info=True)
    return {"id": item_id, "status": "approved", "applied_target_id": new_id, "superseded": superseded}


async def reject(item_id: int, approver: dict, reason: str) -> dict:
    async with get_conn() as conn:
        res = await conn.execute("""
            UPDATE ops_improvement_item
            SET status = 'rejected', reject_reason = $2, decided_by = $3, decided_at = NOW()
            WHERE id = $1 AND status = 'pending'
        """, item_id, reason, approver.get("id"))
    if res.endswith(" 0"):
        raise ValueError("대기 중인 신고가 아닙니다.")
    return {"id": item_id, "status": "rejected"}
