"""개선 원장 서비스 — 근거 정정(correction)·지식 누락(missing_knowledge) 신호의 접수·승인·반려.

출처(source): 채팅 "답변 틀림"·평가 게이트 "이상해요" + 정책서 화면의 직접 수정·AI 수정 제안(policy_direct/policy_ai, 10/21) —
정책 내용 변경은 전부 이 원장의 제안이고 정정 요청에서 승인할 때만 새 버전이 된다(정책 테이블에 대기 행을 만들지 않는다).

원칙(2026-10-01, docs: AIOps/지식관리_고도화방안.md 방안 4): 사용자는 고치지 않고 신호만 준다. 사용자 입력과
AI 수정안은 **이 원장에만** 저장되고, 지식/정책 테이블은 담당자 승인 순간에만 바뀐다(버전 교체, 이전 버전
보존). 그래서 "승인 전 검색 미노출"이 상태 필터가 아니라 구조로 보장된다.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from core.config import settings
from core.database import get_conn, resolve_namespace_id
from shared.embedding import embedding_service
from service.improvement import draft as draft_mod
from service.policy.visibility import (
    KIND_SOURCE_CHECK, PIPELINE_WHOLE, POLICY_TARGETS, SOURCE_JSON_IMPORT, is_searchable, open_correction_sql, searchable_sql,
)

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
# 정책서 화면에서 올린 수정 제안(2026-10-21, v0 재구축 2단계) — 내용 변경은 모두 이 원장의 제안이고 정정 요청에서만 승인한다.
# 직접 수정 = 담당자가 쓴 원문 그대로(AI 호출 없음), AI 제안 = 한 줄 요청 → AI 초안. 승인하면 같은 _apply_policy로 새 버전.
SOURCE_POLICY_DIRECT = "policy_direct"
SOURCE_POLICY_AI = "policy_ai"
POLICY_SOURCES = (SOURCE_POLICY_DIRECT, SOURCE_POLICY_AI)
# 정책서 JSON 가져오기(10/21, v0 재구축 3단계 — service/policy/json_io.py): 바뀐 정책마다 직접 수정과 같은 제안(AI 호출 없음).
# 화면의 POST /api/corrections로는 못 만든다(관리자 전용 가져오기 API만) — 그래서 POLICY_SOURCES(요청 스키마가 받는 값)엔 안 넣는다.
# 값(SOURCE_JSON_IMPORT)은 json_io와 같이 쓰려고 visibility.py에 둔다(10/21 리뷰 C6).
_PROPOSAL_MODES = POLICY_SOURCES + (SOURCE_JSON_IMPORT,)
_AS_IS_MODES = (SOURCE_POLICY_DIRECT, SOURCE_JSON_IMPORT)   # 받은 수정안 그대로 저장(AI 없음)
# 원문 확인(10/21, service/policy/source_check.py): 정책서 원문에 대한 질문·문제 — 내용 정정이 아니라 정정 요청 목록·배지·내 신고·
# 정정 처리 API에 섞이지 않게 모든 정정 조회에서 이 kind를 뺀다. 값(KIND_SOURCE_CHECK)은 visibility.py 한 곳(10/21 리뷰 C6).
_POLICY_TARGETS = POLICY_TARGETS   # 정책 화면(catalog)과 같은 목록(10/21 리뷰 L10)
# pg_advisory_xact_lock(이 값, 정책 항목 id) — 같은 정책에 대기 정정 두 개가 동시에 생기지 않게. 정책을 대상으로 정하는 모든 길
# (정책 화면 제안·채팅 신고·AI 추정·대상 변경)이 이 잠금 + _pending_policy_correction 확인을 거친다(10/21 리뷰 M1)
# 잠금 순서는 어느 길이든 "자기 원장 행(FOR UPDATE) → 이 잠금" — 순서가 엇갈리면 교착(10/21 재리뷰 1).
# 쓰는 advisory 키(겹치면 남의 잠금을 기다린다, 10/21 재리뷰 6): 7301 = 원문 원장(ingestion/source_ledger.py),
# 7302 = 이것, 1개 인자 = 지식 단건 등록(knowledge/service.py, 네임스페이스 id)
_POLICY_LOCK_KEY = 7302


class ConflictError(Exception):
    """이미 검토 중인 같은 신고 등 — 라우터가 409로 변환. existing_id = 이미 검토 중인 정정(화면이 그 카드로 안내)."""

    def __init__(self, message: str, existing_id: Optional[int] = None):
        super().__init__(message)
        self.existing_id = existing_id


def _fallback_chunk_text(policy_name: str, category_path: list[str], raw_body: str) -> str:
    # 옛 엑셀 경로(v1)가 서술 없는 항목에 만들던 대체 조각 형식("정책명 (분류 / …): 원문") — 만들던 코드(_chunk_texts)는 10/21 그 경로와
    # 함께 지웠지만, 남은 v1 데이터의 대체 조각을 알아보려면(원문이 바뀌면 같이 다시 쓰기) 이 형식 그대로여야 한다. v0는 안 쓴다.
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
            "SELECT id, policy_name, category_path, raw_body, remark FROM policy_item "
            f"WHERE id = $1 AND namespace_id = $2 AND {searchable_sql('policy_item')}", target_id, ns_id)   # 검색 대상과 같은 기준 — v0 검토 대기 버전은 정정 대상이 못 된다(10/21)
        if not item:
            raise ValueError("정정 대상 정책을 찾을 수 없거나 이미 바뀌었습니다.")
        base = {"policy_name": item["policy_name"], "category_path": list(item["category_path"] or []),
                "raw_body": item["raw_body"], "remark": item.get("remark")}   # 비고도 고칠 수 있다(10/21) — 수정안에 비고가 없으면 그대로
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


def _stored_candidates(candidates: list[dict]) -> list[dict]:
    """원장 candidates 칸에 저장하는 모양 — 원문 스냅샷 대신 미리보기만(신고 접수·"답변 틀림" 신호가 같은 모양을 쓴다, 10/21 리뷰 C2)."""
    return [{k: c[k] for k in ("key", "target_type", "target_id", "target_sub_id", "label")}
            | {"preview": _preview(c["target_type"], c["original"])} for c in candidates]


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
    stored_candidates = _stored_candidates(candidates)

    def _values() -> tuple:
        return (KIND_BY_TARGET[target_type], SOURCE_AUTO if auto else SOURCE_DIRECT, ns_id, message_id,
                user.get("id"), target_type, target_id, target_sub_id, user_input,
                json.dumps(original, ensure_ascii=False),
                json.dumps(proposed, ensure_ascii=False) if proposed is not None else None,
                json.dumps(stored_candidates, ensure_ascii=False),
                json.dumps(verdict, ensure_ascii=False) if verdict is not None else None)
    conflict = False
    busy = None   # 이 정책을 이미 검토 중인 다른 정정(직접 지정이면 409로 그 번호를 알린다)
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
            # 정책 대상이면 "정책당 대기 정정 1건"(10/21 리뷰 M1) — 신호 건 자신(AI 추정이 이미 이 정책을 골라 둔 경우)은 빼고 본다.
            # 직접 지정(근거 카드)은 사용자가 고른 대상이라 409로 알리고(신호 건은 그대로 — "답변 틀림" 기록은 남김),
            # 자동(AI가 고름)은 사용자가 대상을 고른 적이 없으니 신고는 받되 대상을 비워(auto) 둔다 — 두 번째 대기 정정을 만들지 않는다.
            existing = await _claim_policy_slot(conn, target_type, target_id, signal_id)
            if existing is not None and not auto:
                busy = existing
            else:
                if existing is not None:
                    verdict = _park_on_existing(verdict, existing)
                    target_type, target_id, target_sub_id, original, proposed = "auto", None, None, context, None
                try:
                    item_id = await _write_item(conn, _values(), signal_id)
                except asyncpg.UniqueViolationError:
                    # 같은 근거로 이미 대기 중인 신고가 있다 — 방금 생긴 신호 건은 그 신고와 중복이라 지운다(데이터 손실 없음)
                    conflict = True
                    if signal_id is not None:
                        await conn.execute("DELETE FROM ops_improvement_item WHERE id = $1", signal_id)
    if busy is not None:
        raise ConflictError(f"이 정책은 이미 다른 정정(#{busy})으로 검토 중이에요 — 담당자가 처리하면 반영돼요.", existing_id=busy)
    if conflict:  # 커밋한 뒤에 알린다(트랜잭션 안에서 raise하면 중복 신호 삭제까지 되돌려짐)
        raise ConflictError("이 근거에 대해 이미 검토 중인 정정 신고가 있습니다.")

    if proposed is None and target_type not in ("answer", "auto"):
        proposed = await draft_mod.draft_correction(
            target_type, original, user_input, None if target_type == "missing" else context)
        if proposed is not None:
            async with get_conn() as conn:
                await conn.execute("UPDATE ops_improvement_item SET proposed = $1::jsonb WHERE id = $2",
                                   json.dumps(proposed, ensure_ascii=False), item_id)
    return {"id": item_id, "status": "pending", "kind": KIND_BY_TARGET[target_type], "target_type": target_type,
            "ai_verdict": verdict, "original": original, "proposed": proposed}


def _norm_remark(v) -> Optional[str]:
    s = str(v or "").strip()
    return s or None


def _remark_changed(original: dict, proposed: Optional[dict]) -> bool:
    """수정안이 비고를 바꾸는가 — 비고 키가 없으면 "그대로"(AI 초안·채팅 정정은 비고를 안 건드린다)."""
    return bool(proposed) and "remark" in proposed and _norm_remark(proposed.get("remark")) != _norm_remark((original or {}).get("remark"))


def _clean_proposal(target_type: str, proposed: dict, *, whole: bool) -> dict:
    """저장할 수정안 — 그 대상의 칸 + (정책이면) 비고만 남긴다(모르는 키는 버림). 원문 통째(v0) 정책은 서술 조각 = 원문이라
    둘을 같은 글로 맞춘다(따로 놀면 _policy_new_raw가 어느 쪽을 쓸지 골라야 한다)."""
    allowed = draft_mod._FIELDS[target_type] + (("remark",) if target_type in _POLICY_TARGETS else ())
    out = {k: (None if proposed.get(k) is None else str(proposed.get(k))) for k in allowed if k in proposed}
    if whole and target_type == "policy_narrative":
        body = out["raw_body"] if out.get("raw_body") is not None else out.get("chunk_text")
        if body is not None:
            out["raw_body"] = out["chunk_text"] = body
    return out


def _clean_policy_draft(target_type: str, drafted: dict, ref_raw: Optional[str], *, whole: bool) -> dict:
    """AI 초안 → 저장할 수정안. 원문 통째(v0)에서 AI가 조각 쪽만 고쳤으면(원문 칸은 출발점 그대로) 그게 새 원문(_policy_new_raw와 같은 규칙).
    ref_raw = AI에게 준 출발점의 원문(처음 초안 = 원문, 다시 쓰기 = 현재 수정안). 정책 화면 AI 제안과 다시 쓰기가 같이 쓴다(10/21 리뷰 M2)."""
    if (whole and target_type == "policy_narrative" and drafted.get("raw_body") == ref_raw
            and drafted.get("chunk_text") not in (None, ref_raw)):
        drafted = {**drafted, "raw_body": drafted["chunk_text"]}
    return _clean_proposal(target_type, drafted, whole=whole)


def _unchanged(target_type: str, original: dict, proposed: dict) -> bool:
    return draft_mod.is_unchanged(target_type, original, proposed) and not _remark_changed(original, proposed)


async def _pending_policy_correction(conn, item_id: int, exclude_id: Optional[int] = None) -> Optional[int]:
    """이 정책 항목을 겨눈 대기 중 정정(출처 무관) — 정책 화면의 "정정 #N 검토 중"과 중복 제안 막기가 같은 기준을 쓴다.
    exclude_id = 자기 자신(신호 건을 채우거나 대상을 바꾸는 그 건)은 빼고 본다."""
    return await conn.fetchval(
        open_correction_sql("$1", "$2", extra=" AND o.id IS DISTINCT FROM $3::int"),
        item_id, list(_POLICY_TARGETS), exclude_id)


async def _claim_policy_slot(conn, target_type: Optional[str], target_id: Optional[int],
                             exclude_id: Optional[int] = None) -> Optional[int]:
    """정책 대상이면 그 정책 항목 잠금을 잡고, 이미 대기 중인 다른 정정 id를 돌려준다(없으면 None = 써도 됨). 정책이 아니면 None.
    트랜잭션 안에서 부르고 같은 트랜잭션에서 쓴다 — 잠금이 커밋까지 유지돼 확인과 쓰기 사이에 다른 길이 끼어들지 못한다.
    왜(10/21 리뷰 M1): 유니크 인덱스는 "같은 신고자" 단위라, 정책 화면 제안 #10이 있는데 채팅 신고 #11이 같은 정책에 생기면
    #11 승인 때 _close_superseded가 #10을 자동 종료했다(담당자가 쓴 수정안이 말없이 사라짐)."""
    if target_type not in _POLICY_TARGETS or target_id is None:
        return None
    await conn.execute("SELECT pg_advisory_xact_lock($1, $2)", _POLICY_LOCK_KEY, target_id)
    return await _pending_policy_correction(conn, target_id, exclude_id)


async def _is_whole_policy(conn, target_type: str, target_id: Optional[int]) -> bool:
    """정정 대상이 원문 통째(v0) 정책인가 — 수정안 정리(_clean_proposal whole=)가 쓴다. 정책이 아니면 False(10/21 리뷰 C2)."""
    if target_type not in _POLICY_TARGETS:
        return False
    return (await conn.fetchval("SELECT pipeline_version FROM policy_item WHERE id = $1", target_id)) == PIPELINE_WHOLE


def _same_target_sql(first: int) -> str:
    """UPDATE 가드 "읽은 뒤 대상이 그대로일 때만" — $first·$first+1·$first+2 = 읽어 둔 (target_type, target_id, target_sub_id).
    직접 수정·대상 변경·다시 쓰기가 같은 조각을 쓴다(10/21 리뷰 C2). 0건이면 그 사이 다른 사람이 대상을 바꾼 것 → 409."""
    return (f"target_type = ${first} AND target_id IS NOT DISTINCT FROM ${first + 1} "
            f"AND target_sub_id IS NOT DISTINCT FROM ${first + 2}")


def _duplicate_message(existing_id: int) -> str:
    return f"이 정책은 이미 정정 #{existing_id} 검토 중이에요 — 품질 › 정정 요청에서 그 건을 처리한 뒤 다시 올리세요."


def _park_on_existing(verdict: Optional[dict], existing_id: int) -> dict:
    """AI가 고른 정책에 이미 검토 중 정정이 있을 때 — 신고(사용자 한 줄·AI 판단)는 남기되 대상은 비운다(target='auto').
    정정 #N과 같은 정책이라 담당자가 그 건에서 함께 보면 되고, 굳이 이 건을 그 정책으로 옮기려 하면 대상 변경이 409로 막는다."""
    meta = dict(verdict or {})
    note = f"이 정책은 이미 정정 #{existing_id} 검토 중이라 대상을 비워 뒀어요 — 그 건에서 함께 확인하세요."
    meta["reason"] = f"{meta['reason']} · {note}" if meta.get("reason") else note
    meta["duplicate_of"] = existing_id
    meta.pop("fix_summary", None)   # 고칠 대상을 비웠으니 "이렇게 고치면 돼요"는 이 건의 안이 아니다(10/21 재리뷰 4)
    return meta


async def create_policy_proposal(namespace: str, user: dict, *, item_id: int, mode: str,
                                 proposed: Optional[dict], user_input: Optional[str]) -> dict:
    """정책서 화면의 수정 제안 → 원장에 1건(정책 테이블은 그대로 — 승인 때만 새 버전). 대상 = 그 정책의 서술 조각(v0는 조각 1개 = 원문).
    직접 수정(mode=policy_direct)은 받은 원문·비고를 그대로 수정안으로(AI 호출 없음), AI 제안(policy_ai)은 한 줄 요청으로 AI 초안.
    같은 정책에 대기 중 정정이 있으면(채팅 신고 포함) 만들지 않고 ConflictError(existing_id) — 한 정책에 안이 둘이면 승인 때 하나가
    다른 하나를 덮는다. 확인·저장은 정책 항목 단위 잠금 안에서(동시에 두 번 눌러도 1건)."""
    if mode not in _PROPOSAL_MODES:
        raise ValueError("알 수 없는 수정 방식입니다.")
    user_input = (user_input or "").strip() or None
    if mode == SOURCE_POLICY_AI and not user_input:
        raise ValueError("AI에게 어떻게 고칠지 한 줄로 적어 주세요.")
    if mode in _AS_IS_MODES and not proposed:
        raise ValueError("수정한 원문이 없습니다.")
    async with get_conn() as conn:
        ns_id = await resolve_namespace_id(conn, namespace)
        if ns_id is None:
            raise ValueError(f"업무 영역을 찾을 수 없습니다: {namespace}")
        row = await conn.fetchrow(
            "SELECT i.id, i.pipeline_version, (SELECT c.id FROM policy_chunk c WHERE c.policy_item_id = i.id "
            "ORDER BY c.chunk_idx, c.id LIMIT 1) AS chunk_id FROM policy_item i "
            f"WHERE i.id = $1 AND i.namespace_id = $2 AND {searchable_sql('i')}", item_id, ns_id)
        if not row:
            raise ValueError("고칠 정책을 찾을 수 없거나 그 사이 바뀌었어요 — 새로고침하세요.")
        if row["chunk_id"] is None:
            raise ValueError("이 정책엔 검색 조각이 없어 수정 제안을 만들 수 없어요.")
        chunk_id = row["chunk_id"]
        original = await _load_original(conn, ns_id, "policy_narrative", item_id, chunk_id, None)
    whole = row["pipeline_version"] == PIPELINE_WHOLE
    stored = None
    if mode in _AS_IS_MODES:
        stored = _clean_proposal("policy_narrative", proposed or {}, whole=whole)
        _validate_proposed("policy_narrative", stored, editing=True)
        if _unchanged("policy_narrative", original, stored):
            raise ValueError("바뀐 내용이 없어요 — 원문이나 비고를 고친 뒤 올리세요.")
    values = ("correction", mode, ns_id, None, user.get("id"), "policy_narrative", item_id, chunk_id, user_input,
              json.dumps(original, ensure_ascii=False),
              json.dumps(stored, ensure_ascii=False) if stored is not None else None, "[]", None)
    new_id = existing = None
    gone = False
    async with get_conn() as conn:
        async with conn.transaction():
            existing = await _claim_policy_slot(conn, "policy_narrative", item_id)
            if existing is None:
                # 위의 "검색 대상인가" 확인은 잠금 전 — 그 사이 다른 정정 승인으로 이 버전이 은퇴했으면 낡은 버전에 제안이 붙는다.
                # 잠금 안에서 다시 본다(10/21 재리뷰 3). 진행 중인 승인은 아직 대기 건이라 위 확인에서 먼저 걸린다.
                gone = await conn.fetchval(
                    f"SELECT NOT EXISTS (SELECT 1 FROM policy_item i WHERE i.id = $1 AND i.namespace_id = $2 AND {searchable_sql('i')})",
                    item_id, ns_id)
            if existing is None and not gone:
                try:
                    new_id = await _write_item(conn, values, None)
                except asyncpg.UniqueViolationError:   # 같은 사람의 같은 대상 대기 건(유니크 인덱스) — 위 확인과 같은 뜻
                    existing = await _pending_policy_correction(conn, item_id)
    if gone:
        raise ValueError("고칠 정책을 찾을 수 없거나 그 사이 바뀌었어요 — 새로고침하세요.")
    if new_id is None:
        if existing is None:
            raise ConflictError("이 정책에 이미 검토 중인 정정이 있어요 — 정정 요청에서 확인하세요.")
        raise ConflictError(_duplicate_message(existing), existing_id=existing)

    ai_failed = False
    if mode == SOURCE_POLICY_AI:
        drafted = await draft_mod.draft_correction("policy_narrative", original, user_input, None)
        if drafted is None:
            ai_failed = True   # 제안은 남긴다 — 정정 요청에서 [AI 초안 만들기]나 [직접 수정]으로 이어 간다(화면이 알림)
        else:
            stored = _clean_policy_draft("policy_narrative", drafted, original.get("raw_body"), whole=whole)
            async with get_conn() as conn:
                # AI가 쓰는 30초~2분 사이 담당자가 [직접 수정]·[AI 초안 만들기]로 안을 넣었으면 덮어쓰지 않는다(리뷰 M2 10/21)
                res = await conn.execute(
                    "UPDATE ops_improvement_item SET proposed = $1::jsonb WHERE id = $2 AND status = 'pending' AND proposed IS NULL",
                    json.dumps(stored, ensure_ascii=False), new_id)
            if res.endswith(" 0"):
                stored = None   # 화면엔 "이미 다른 안이 있음" — 정정 요청에서 확인
    return {"id": new_id, "status": "pending", "kind": "correction", "target_type": "policy_narrative", "source": mode,
            "original": original, "proposed": stored, "ai_failed": ai_failed}


async def update_proposed(item_id: int, editor: dict, proposed: dict) -> dict:
    """정정 요청 카드의 [직접 수정] — 담당자가 고친 안으로 저장된 수정안을 바꾼다(10/21 사용자 정정: 아는 사람이 직접 고치는 게 빠를 수 있다).
    반영은 여전히 [승인]에서만(승인은 화면에 보인 안을 보내 그 사이 바뀌었으면 409). 검사는 승인과 같다(필수 칸·【확인 필요】·원문과 같음).
    누가 언제 고쳤는지 ai_verdict.edits에 남긴다. 대기 중이고 대상이 그대로일 때만."""
    async with get_conn() as conn:
        item = await conn.fetchrow("SELECT * FROM ops_improvement_item WHERE id = $1", item_id)
        if not item or item["status"] != "pending":
            raise ValueError("대기 중인 신고가 아닙니다.")
        target_type = item["target_type"]
        if target_type not in _REDRAFT_TARGETS:
            raise ValueError("고칠 대상(근거)이 정해진 신고만 직접 수정할 수 있어요 — 먼저 정정 대상을 고르세요.")
        whole = await _is_whole_policy(conn, target_type, item["target_id"])
    original = _json(item["original"]) or {}
    cleaned = _clean_proposal(target_type, proposed or {}, whole=whole)
    if "remark" not in original:
        # 10/21 이전 신고는 원문 스냅샷에 비고가 없다 — 그 비고를 모르는 채 비고 칸을 받으면 승인 때 실제 비고를 지울 수 있어 버린다
        cleaned.pop("remark", None)
    _validate_proposed(target_type, cleaned, editing=True)
    if _unchanged(target_type, original, cleaned):
        raise ValueError("기존 내용과 같아요 — 고칠 게 없으면 반려(종료)하세요.")
    verdict = dict(_json(item["ai_verdict"]) or {})
    verdict["edits"] = [*verdict.get("edits", []), {"by": editor.get("id"), "by_name": editor.get("username"),
                                                    "at": datetime.now(timezone.utc).isoformat()}]
    async with get_conn() as conn:
        res = await conn.execute(f"""
            UPDATE ops_improvement_item SET proposed = $2::jsonb, ai_verdict = $3::jsonb
            WHERE id = $1 AND status = 'pending' AND {_same_target_sql(4)}
        """, item_id, json.dumps(cleaned, ensure_ascii=False), json.dumps(verdict, ensure_ascii=False),
            target_type, item["target_id"], item["target_sub_id"])
    if res.endswith(" 0"):
        raise ConflictError("그 사이 처리됐거나 정정 대상이 바뀐 신고예요 — 새로고침해서 확인하세요.")
    return {"id": item_id, "proposed": cleaned}


async def record_answer_signal(ns_id: int, message_id: int, reporter_id: Optional[int]) -> Optional[int]:
    """"답변 틀림"을 누른 순간 원장에 1건 — 한 줄 의견이 이어서 오면 create_item이 이 건을 채운다. 같은 사람의 같은
    답변에 대기 건이 이미 있으면 그대로 둔다(여러 번 눌러도 1건). 실패하면 None(로그 남김) — 이 건이 신고의 유일한
    기록이라 호출측(피드백 라우터)이 사용자에게 실패를 알린다."""
    try:
        async with get_conn() as conn:
            existing = await conn.fetchval(
                "SELECT id FROM ops_improvement_item WHERE message_id = $1 AND reporter_user_id IS NOT DISTINCT FROM $2 "
                "AND status = 'pending'", message_id, reporter_id)
            if existing:
                return existing
            candidates = await _load_candidates(conn, ns_id, message_id)
            context = await _load_original(conn, ns_id, "missing", None, None, message_id)
            stored = _stored_candidates(candidates)
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
        proposed = out["proposed"]
        async with get_conn() as conn:
            async with conn.transaction():
                # 자기 행을 먼저 잠근다 — create_item(한 줄 의견)은 신호 행 → 정책 잠금 순서라, 여기서 정책 잠금부터 잡으면
                # "답변 틀림" 직후 한 줄을 보낼 때 서로 기다려 교착(한 줄 유실)이 났다(10/21 재리뷰 1). 이미 의견·판정이 있으면 손대지 않는다.
                own = await conn.fetchrow(
                    "SELECT status, user_input, ai_verdict FROM ops_improvement_item WHERE id = $1 FOR UPDATE", item_id)
                if not own or own["status"] != "pending" or own["user_input"] is not None or own["ai_verdict"] is not None:
                    return False
                # 추정이 고른 정책에 다른 대기 정정이 있으면 대상을 비워 둔다 — 이 건도 승인될 수 있는 대기 건이라 정책당 1건을 지킨다(10/21 리뷰 M1)
                existing = await _claim_policy_slot(conn, target_type, target_id, item_id)
                if existing is not None:
                    meta = _park_on_existing(meta, existing)
                    target_type, target_id, sub_id, original, proposed = "auto", None, None, context, None
                res = await conn.execute("""
                    UPDATE ops_improvement_item
                    SET kind = 'answer_signal', target_type = $2, target_id = $3, target_sub_id = $4, original = $5::jsonb,
                        proposed = $6::jsonb, ai_verdict = $7::jsonb
                    WHERE id = $1 AND status = 'pending' AND user_input IS NULL AND ai_verdict IS NULL
                """, item_id, target_type, target_id, sub_id, json.dumps(original, ensure_ascii=False),
                    json.dumps(proposed, ensure_ascii=False) if proposed else None,
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


async def item_namespace(item_id: int) -> Optional[str]:
    """권한 확인용 — 이 신고가 속한 파트(처리 권한 = 그 파트 담당자 + 관리자)."""
    async with get_conn() as conn:
        return await conn.fetchval(
            "SELECT n.name FROM ops_improvement_item i JOIN ops_namespace n ON n.id = i.namespace_id WHERE i.id = $1 AND i.kind <> $2",
            item_id, KIND_SOURCE_CHECK)   # 원문 확인 항목은 정정 처리 API(승인·반려·다시 쓰기…)로 못 집는다 — 404


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
        elif (item.get("target_type") in _REDRAFT_TARGETS
              and key == _key(item["target_type"], item.get("target_id"), item.get("target_sub_id"))):
            # 같은 대상으로 초안만 다시(정책 화면 제안처럼 답변 근거 후보가 없는 건도 [AI 초안 만들기]가 되게, 10/21)
            target_type, target_id, sub_id = item["target_type"], item["target_id"], item["target_sub_id"]
            original = await _load_original(conn, ns_id, target_type, target_id, sub_id, None)
        else:
            c = next((c for c in (_json(item["candidates"]) or []) if c["key"] == key), None)
            if c is None:
                raise ValueError("이 답변의 근거가 아닙니다.")
            target_type, target_id, sub_id = c["target_type"], c["target_id"], c["target_sub_id"]
            original = await _load_original(conn, ns_id, target_type, target_id, sub_id, None)
        # 다른 정책으로 옮길 때만 "정책당 대기 정정 1건" 확인(10/21 리뷰 M1) — 같은 정책 안(초안 다시·파라미터↔서술)은 이미 이 건이 그 자리.
        # AI 초안(수십 초)을 쓰기 전에 먼저 한 번 보고, 실제 저장 때 잠금 안에서 다시 본다.
        moves_policy = target_type in _POLICY_TARGETS and not (
            item.get("target_type") in _POLICY_TARGETS and item.get("target_id") == target_id)
        if moves_policy and (busy := await _pending_policy_correction(conn, target_id, item_id)) is not None:
            raise ConflictError(_duplicate_message(busy), existing_id=busy)
    # 사용자 의견이 없으면 맞는 내용 정보가 없다 — 값을 지어내지 않고 의심 구간에 【확인 필요】만 단 "검토 초안"을 만든다
    # (2026-10-07, 사용자 지적 "양식은 만들어줘야"). 표시가 남으면 승인이 막혀 확인 안 된 초안이 반영되지 않는다.
    if target_type == "answer":
        proposed = None
    elif not item["user_input"]:
        proposed = await draft_mod.draft_review_template(target_type, original, context)
    else:
        proposed = await draft_mod.draft_correction(
            target_type, original, item["user_input"], None if target_type == "missing" else context)
    # 변경 이력은 AI 판정이 있던 신고에서 대상이 실제로 바뀔 때만 남긴다 — 같은 대상 재생성("초안 다시 만들기")을
    # 변경으로 세면 "담당자가 AI 판정을 뒤집은 비율" 측정이 부풀고, 직접 지정 신고엔 판정 자체가 없다(/code-review).
    verdict = dict(_json(item["ai_verdict"]) or {})
    # 앞선 다시 쓰기 요청은 옛 초안에 대한 것 — 새 초안을 만들면 다음 다시 쓰기에 넘기지 않는다(다른 근거에 "3번째 문단 빼 주세요"가
    # 적용되는 사고, 리뷰 10/21). 누가 무엇을 요청했는지는 지난 요청으로 남긴다. 요청만 있던 직접 지정 신고는 AI 판정이 없는 것으로 본다.
    if old_requests := verdict.pop("redrafts", None):
        verdict["redrafts_before_retarget"] = [*verdict.get("redrafts_before_retarget", []), *old_requests]
    old_key = _key(item["target_type"], item["target_id"], item["target_sub_id"])
    if set(verdict) - {"redrafts_before_retarget", "edits"} and old_key != key:
        verdict = dict(verdict) | {"retargeted": {"from": old_key, "to": key, "by": approver.get("id")}}
    busy, stale = None, False
    read_target = (item["target_type"], item["target_id"], item["target_sub_id"])   # moves_policy·이력은 이 값 기준으로 계산했다
    async with get_conn() as conn:
        try:
            async with conn.transaction():
                # 자기 행 → 정책 잠금 순서(create_item·analyze_signal과 같게 — 엇갈리면 교착, 10/21 재리뷰 1). AI 초안을 쓰는 사이
                # 다른 사람이 대상을 바꿨으면 처음 읽은 대상으로 계산한 moves_policy·변경 이력이 낡았으니 쓰지 않는다(10/21 재리뷰 2)
                own = await conn.fetchrow(
                    "SELECT status, target_type, target_id, target_sub_id FROM ops_improvement_item WHERE id = $1 FOR UPDATE",
                    item_id)
                stale = (not own or own["status"] != "pending"
                         or (own["target_type"], own["target_id"], own["target_sub_id"]) != read_target)
                if not stale and moves_policy:
                    busy = await _claim_policy_slot(conn, target_type, target_id, item_id)
                if not stale and busy is None:
                    res = await conn.execute(f"""
                        UPDATE ops_improvement_item
                        SET kind = $2, target_type = $3, target_id = $4, target_sub_id = $5, original = $6::jsonb,
                            proposed = $7::jsonb, ai_verdict = $8::jsonb
                        WHERE id = $1 AND status = 'pending' AND {_same_target_sql(9)}
                    """, item_id, KIND_BY_TARGET[target_type], target_type, target_id, sub_id,
                        json.dumps(original, ensure_ascii=False),
                        json.dumps(proposed, ensure_ascii=False) if proposed is not None else None,
                        json.dumps(verdict, ensure_ascii=False) if verdict else None, *read_target)
                    stale = res.endswith(" 0")
        except asyncpg.UniqueViolationError:
            raise ConflictError("같은 신고자의 같은 근거 신고가 이미 대기 중입니다.")
    if stale:
        raise ConflictError("그 사이 처리됐거나 다른 사람이 정정 대상을 바꾼 신고예요 — 새로고침해서 확인하세요.")
    if busy is not None:   # AI 초안을 쓰는 사이 다른 길로 그 정책에 정정이 생겼다
        raise ConflictError(_duplicate_message(busy), existing_id=busy)
    return {"id": item_id, "target_type": target_type, "original": original, "proposed": proposed}


_REDRAFT_TARGETS = ("knowledge", "missing", "policy_param", "policy_narrative")


async def redraft(item_id: int, approver: dict, feedback: str) -> dict:
    """담당자의 한 줄 요청으로 AI가 수정안을 다시 쓴다(2026-10-21 — 직접 고치려면 update_proposed).
    의견 없는 신고의 검토 초안(【확인 필요】 표시)도 담당자가 "맞는 값은 3일" 같은 요청을 주면 이걸로 표시 없는 수정안이 된다.
    요청은 신고 의견 뒤에 붙여 같은 초안 생성기에 넘기고, 누가 무엇을 요청했는지 ai_verdict.redrafts에 남긴다. 신고는 대기 그대로."""
    async with get_conn() as conn:
        item = await conn.fetchrow("SELECT * FROM ops_improvement_item WHERE id = $1", item_id)
        if not item or item["status"] != "pending":
            raise ValueError("대기 중인 신고가 아닙니다.")
        if item["target_type"] not in _REDRAFT_TARGETS:
            raise ValueError("고칠 대상(근거)이 정해진 신고만 다시 쓸 수 있어요 — 먼저 틀린 근거를 고르세요.")
        context = await _load_original(conn, item["namespace_id"], "missing", None, None, item["message_id"])
        whole = await _is_whole_policy(conn, item["target_type"], item["target_id"])
    target_type = item["target_type"]
    original = _json(item["original"]) or {}
    stored = _json(item["proposed"])
    verdict = dict(_json(item["ai_verdict"]) or {})
    # 출발점 = 지금 저장된 수정안(10/21 리뷰 M2) — 원문부터 다시 쓰면 정책 화면 [직접 수정]·카드 [직접 수정]으로 사람이 고친 본문·비고가
    # 사라졌다. 단 【확인 필요】 표시가 남은 안은 AI 검토 초안(사람이 고친 안은 저장 때 표시가 막힌다)이라 원문에서 출발한다.
    base = None
    if stored and not draft_mod.has_review_marks(stored):
        base = {k: stored[k] for k in draft_mod._FIELDS[target_type] if k in stored}
    # 앞선 다시 쓰기 요청도 함께 넘긴다(무엇을 이미 고쳤는지 맥락) — 출발점이 현재 수정안이라 프롬프트가 "마지막 요청만 반영"을 지시한다
    requests = [r["feedback"] for r in verdict.get("redrafts", []) if r.get("feedback")] + [feedback]
    combined = "\n\n".join([*([item["user_input"]] if item["user_input"] else []),
                              *(f"[담당자 요청 {i}] {r}" for i, r in enumerate(requests, 1))])
    drafted = await draft_mod.draft_correction(target_type, original, combined,
                                               None if target_type == "missing" else context, base)
    if drafted is None:   # AI 실패는 숨기지 않는다 — 옛 수정안을 그대로 두고 알린다
        raise ValueError("AI가 수정안을 다시 쓰지 못했어요(사내 AI 응답 실패일 수 있음) — 잠시 후 다시 시도하세요.")
    ref_raw = (base or original).get("raw_body")
    proposed = _clean_policy_draft(target_type, drafted, ref_raw, whole=whole) if target_type in _POLICY_TARGETS else drafted
    if stored and "remark" in stored and "remark" in original:
        proposed["remark"] = stored["remark"]   # 비고는 AI가 안 쓴다(초안 칸에 없음) — 사람이 고친 비고를 그대로 이어 간다
    # 덮어쓰기 전 안을 이력에 남긴다 — 다시 쓰기가 사람이 고친 안을 망쳐도 되돌릴 수 있게(직접 수정 이력 edits와 짝)
    verdict["redrafts"] = [*verdict.get("redrafts", []), {"by": approver.get("id"), "feedback": feedback,
                                                          "at": datetime.now(timezone.utc).isoformat(), "previous": stored}]
    async with get_conn() as conn:
        res = await conn.execute(f"""
            UPDATE ops_improvement_item SET proposed = $2::jsonb, ai_verdict = $3::jsonb
            WHERE id = $1 AND status = 'pending' AND {_same_target_sql(4)}
              AND proposed IS NOT DISTINCT FROM $7::jsonb
        """, item_id, json.dumps(proposed, ensure_ascii=False), json.dumps(verdict, ensure_ascii=False),
            item["target_type"], item["target_id"], item["target_sub_id"],
            json.dumps(stored, ensure_ascii=False) if stored is not None else None)
        # AI가 쓰는 동안 대상이 바뀌었거나(리뷰 L4) 누가 [직접 수정]으로 안을 바꿨으면(출발점이 낡음 — 리뷰 M2) 덮어쓰지 않는다
    if res.endswith(" 0"):   # "그 사이 바뀜"은 직접 수정·대상 변경·승인과 같이 409(10/21 리뷰 5b — 예전엔 여기만 400)
        raise ConflictError("그 사이 처리됐거나 수정안·정정 대상이 바뀐 신고예요 — 새로고침해서 확인하세요.")
    return {"id": item_id, "proposed": proposed, "marked": draft_mod.has_review_marks(proposed)}


async def pending_status(knowledge_ids: list[int], policy_item_ids: list[int]) -> dict:
    """근거별 "정정 검토 중" 여부 — 채팅 카드 배지용(실시간·재조회·캐시 응답 모두 이 경로).
    정책은 "정책당 대기 정정 1건" 잠금(_claim_policy_slot → open_correction_sql)과 같은 기준(10/21 리뷰 5a): 대기 중이고 그 정책 항목을
    겨눈 건이면 종류 무관(AI 추정이 대상을 골라 둔 "답변 틀림" 신호 포함 — 원문 확인은 대상 종류가 달라 안 걸린다). 그래서 정책은
    항목 단위(policy_item)로도 돌려준다 — 예전엔 파라미터/서술 단위만 줘서(다른 부분 신고를 막지 않으려고, /code-review) 배지는 없는데
    신고하면 409가 났다. 잠금이 항목 단위가 된 지금은 그 전제가 없다. policy_param/policy_narrative는 옛 화면 호환으로 남긴다.
    지식은 그대로 kind='correction'(지식엔 대상당 1건 잠금이 없고, 같은 신고자 중복만 유니크 인덱스가 막는다)."""
    out = {"knowledge": [], "policy_item": [], "policy_param": [], "policy_narrative": []}
    if not knowledge_ids and not policy_item_ids:
        return out
    async with get_conn() as conn:
        rows = await conn.fetch("""
            SELECT DISTINCT target_type, target_id, target_sub_id FROM ops_improvement_item
            WHERE status = 'pending'
              AND ((kind = 'correction' AND target_type = 'knowledge' AND target_id = ANY($1::int[]))
                OR (target_type = ANY($3::text[]) AND target_id = ANY($2::int[])))
        """, knowledge_ids or [], policy_item_ids or [], list(_POLICY_TARGETS))
    for r in rows:
        if r["target_type"] == "knowledge":
            out["knowledge"].append(r["target_id"])
            continue
        if r["target_id"] not in out["policy_item"]:
            out["policy_item"].append(r["target_id"])
        if r["target_sub_id"] is not None:
            out[r["target_type"]].append(r["target_sub_id"])
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
    conds, args = [f"i.kind <> '{KIND_SOURCE_CHECK}'"], []
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
            "WHERE i.status = 'pending' AND i.kind <> $1 GROUP BY n.name", KIND_SOURCE_CHECK)
    by_ns = {r["name"]: int(r["cnt"]) for r in rows}
    return {"count": sum(by_ns.values()), "by_namespace": by_ns}


async def list_mine(user_id: int, unseen_only: bool) -> list[dict]:
    """신고자에게 결과 알림 — 처리됐는데 아직 확인 안 한 것."""
    extra = "AND i.status <> 'pending' AND i.reporter_seen_at IS NULL" if unseen_only else ""
    async with get_conn() as conn:
        rows = await conn.fetch(f"{_LIST_SQL} WHERE i.reporter_user_id = $1 AND i.kind <> $2 {extra} ORDER BY i.created_at DESC LIMIT 50",
                                user_id, KIND_SOURCE_CHECK)
    return [_row(r) for r in rows]


async def mark_seen(user_id: int, ids: list[int]) -> int:
    async with get_conn() as conn:
        res = await conn.execute(
            "UPDATE ops_improvement_item SET reporter_seen_at = NOW() "
            "WHERE reporter_user_id = $1 AND id = ANY($2::int[]) AND status <> 'pending' AND reporter_seen_at IS NULL AND kind <> $3",
            user_id, ids, KIND_SOURCE_CHECK)
    return int(res.split()[-1]) if res else 0


# ── 승인 = 반영(버전 교체 / 누락 지식 등록) ─────────────────────────────────────

_FIELD_LABEL = {"content": "본문", "raw_body": "정책 원문", "chunk_text": "서술 단락", "name": "항목명"}


def _validate_proposed(target_type: str, proposed: Optional[dict], *, editing: bool = False) -> dict:
    """승인·직접 수정 저장이 같은 검사를 쓴다(editing=True면 사람이 고친 안이라 안내 문구만 다르다)."""
    if not proposed:
        raise ValueError("수정안이 비어 있습니다 — 수정안을 작성한 뒤 승인하세요.")
    for k in sorted(draft_mod._REQUIRED[target_type], key=lambda k: k != "raw_body"):   # 원문부터 — v0는 원문 칸만 보인다
        if not str(proposed.get(k) or "").strip():
            raise ValueError(f"수정안의 '{_FIELD_LABEL.get(k, k)}'이(가) 비어 있습니다.")
    if draft_mod.has_review_marks(proposed):
        # 검토 초안(의견 없는 신고)의 표시가 남은 채 반영되면 "【확인 필요…】"가 지식·정책 본문에 그대로 들어간다
        if editing:
            raise ValueError("【확인 필요】 표시가 남아 있어요 — 맞는 내용으로 고치고 표시를 지운 뒤 저장하세요.")
        raise ValueError("【확인 필요】 표시가 남아 있어요 — [직접 수정]으로 고치거나 [AI 다시 쓰기]로 맞는 내용을 알려 주세요.")
    return proposed


async def _apply_knowledge(conn, ns_id: int, target_id: int, content: str, embedding: list[float]) -> int:
    old = await conn.fetchrow("SELECT * FROM rag_knowledge WHERE id = $1 AND namespace_id = $2 FOR UPDATE",
                              target_id, ns_id)
    if not old or old["status"] != "active":
        raise ValueError("원본 지식이 이미 바뀌었거나 현행이 아닙니다 — 이 신고는 반려하세요.")
    new_id = await conn.fetchval("""
        INSERT INTO rag_knowledge
            (namespace_id, content, embedding, base_weight, category, created_by_user_id,
             source_file, source_chunk_idx, source_type, ingestion_job_id, status,
             logical_document_id, version, supersedes_id, embedding_model, reviewed_at, owner,
             confluence_page_id, confluence_version, heading_path, source_document_id, content_edited_at)
        VALUES ($1, $2, $3::vector, $4, $5, $6, $7, $8, $9, $10, 'active',
                $11, $12, $13, $14, NOW(), $15, $16, $17, $18, $19, NOW())
        RETURNING id
    """, ns_id, content, str(embedding), old["base_weight"], old["category"],
        old["created_by_user_id"], old["source_file"], old["source_chunk_idx"], old["source_type"],
        old["ingestion_job_id"], old["logical_document_id"] or old["id"], (old["version"] or 1) + 1, old["id"],
        settings.embedding_model, old["owner"], old["confluence_page_id"], old["confluence_version"],
        old["heading_path"], old.get("source_document_id"))   # 원문 연결 유지 — 다시 분할이 이 정정본을 보고 멈추게(2026-10-08)
    await conn.execute("UPDATE rag_knowledge SET status = 'deprecated' WHERE id = $1", old["id"])
    # 이 지식으로 메운 지식 공백은 새 버전을 가리키게 — 안 그러면 통계 목록이 폐기된 옛 버전을 "메운 지식"으로 잡는다
    await conn.execute("UPDATE ops_query_log SET resolved_knowledge_id = $2 WHERE resolved_knowledge_id = $1",
                       old["id"], new_id)
    return new_id


async def _apply_missing(conn, ns_id: int, content: str, embedding: list[float], category: str,
                         approver: dict) -> int:
    return await conn.fetchval("""
        INSERT INTO rag_knowledge (namespace_id, content, embedding, base_weight, category,
                                   created_by_user_id, source_type, status, embedding_model, reviewed_at, owner)
        VALUES ($1, $2, $3::vector, 1.0, $4, $5, 'correction', 'active', $6, NOW(), $7)
        RETURNING id
    """, ns_id, content, str(embedding), category, approver.get("id"),
        settings.embedding_model, approver.get("username"))


def _policy_new_raw(old: dict, item: dict, proposed: dict) -> str:
    """정정된 새 원문. 원문 통째 항목(v0)은 서술 = 원문이라 서술 정정안만 왔어도 그게 새 원문이다(감사 10/20)."""
    if old.get("pipeline_version") == PIPELINE_WHOLE and item["target_type"] == "policy_narrative":
        # 정정안엔 "서술 조각"과 "원문"이 둘 다 온다(draft·검토 화면이 둘 다 채움) — 통째 항목은 둘이 같은 글이라 바뀐 쪽을 쓴다.
        # 원문 우선이면 검토자가 "서술"만 고쳤을 때 아무것도 안 바뀐 새 버전이 승인됐다(리뷰 10/20 M2). 둘 다 다르게 바뀌면 원문.
        raw, chunk = proposed.get("raw_body"), proposed.get("chunk_text")
        if raw and raw != old["raw_body"]:
            return raw
        if chunk and chunk != old["raw_body"]:
            return chunk
        return old["raw_body"]
    return proposed.get("raw_body") or old["raw_body"]


def _policy_chunk_rewrites(old: dict, chunks: list, item: dict, proposed: dict) -> dict[int, str]:
    """새 버전에서 본문이 바뀌는 청크 {chunk_id: 새 텍스트} — 나머지는 임베딩째 복사.
    정정 대상 서술 청크 + (원문이 바뀌었으면) 원문 통째로 만든 폴백 청크(안 그러면 옛 값이 검색됨).
    원문 통째 항목(v0)은 조각이 곧 원문 — 조각과 원문이 따로 놀지 않게 조각을 새 원문으로(정정안의 "서술 조각"을 따로 쓰지 않음)."""
    new_raw = _policy_new_raw(old, item, proposed)
    if old.get("pipeline_version") == PIPELINE_WHOLE:
        return {c["id"]: new_raw for c in chunks if c["chunk_text"] != new_raw}
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
    from service.policy.service import chunk_embed_text   # 머리 붙인 임베딩(가져오기와 같은 규칙)
    return {cid: (text, await embedding_service.embed(chunk_embed_text(old["policy_name"], old["category_path"], text)))
            for cid, text in rewrites.items()}


async def _apply_policy(conn, ns_id: int, item: dict, proposed: dict, approver: dict,
                        prepared: dict[int, tuple[str, list[float]]]) -> int:
    """정책 새 버전: policy_item 복사(raw_body=수정안) + param/chunk 복사(정정 대상만 교체) + 원본 deprecated.
    `prepared`는 트랜잭션 밖에서 미리 만든 {chunk_id: (새 텍스트, 임베딩)}.
    content_hash·pipeline_version·원천 키(source_key)·출처 위치는 원본 그대로 — 원천 키가 같아야 JSON 가져오기(json_io)·골든셋·재적재
    대조가 새 버전을 같은 정책으로 찾는다. content_hash는 처음 적재한 원문 기준 그대로다(10/21: 이 값으로 재업로드를 건너뛰던 옛 엑셀
    경로는 없어졌다 — 지금 원문과의 비교는 json_io가 원문·비고로 직접 한다)."""
    old = await conn.fetchrow("SELECT * FROM policy_item WHERE id = $1 AND namespace_id = $2 FOR UPDATE",
                              item["target_id"], ns_id)
    # 검색 대상과 같은 기준(10/21) — v0 검토 대기 버전을 원본 삼아 새 버전을 만들면 승인 전 수정안이 그대로 현행이 된다
    if not old or not is_searchable(old["status"], old.get("pipeline_version")):
        raise ValueError("원본 정책이 이미 바뀌었거나 현행이 아닙니다 — 이 신고는 반려하세요.")
    chunks = await conn.fetch(_POLICY_CHUNKS_SQL, old["id"])
    rewrites = _policy_chunk_rewrites(dict(old), chunks, item, proposed)
    # 잠금 후 다시 계산한 결과가 미리 임베딩한 것과 다르면(준비 사이에 원본 편집) 옛 임베딩을 쓰게 되므로 중단
    if rewrites != {cid: text for cid, (text, _) in prepared.items()}:
        raise ValueError("승인 준비 중 원본 정책이 바뀌었습니다 — 다시 승인해 주세요.")
    new_raw = _policy_new_raw(dict(old), item, proposed)
    # 비고: 수정안에 비고 칸이 있으면 그 값(빈 값 = 지움), 없으면 원본 그대로(AI 초안·채팅 정정은 비고를 안 건드린다, 10/21)
    new_remark = _norm_remark(proposed.get("remark")) if "remark" in proposed else old["remark"]
    # 새 버전 번호 = 같은 정책(logical_id)의 가장 큰 번호 + 1(10/21) — 원본+1이면 반려된 v2가 이미 있을 때 번호가 겹쳤다.
    # 원본 행을 FOR UPDATE로 잡고 있어 같은 정책의 다른 정정 승인과는 순서대로 돈다.
    next_version = await conn.fetchval(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM policy_item WHERE logical_id = $1", old["logical_id"])
    # 원본을 먼저 내리고 새 버전을 넣는다 — "정책당 사용 중 1개" 고유 인덱스(#80)는 즉시 검사라, 새 active를 먼저 넣으면
    # 정정 승인이 전부 고유 위반(500)으로 막혔다(리뷰 H1 10/21). 같은 트랜잭션이라 중간 상태는 밖에서 안 보인다.
    await conn.execute("UPDATE policy_item SET status = 'deprecated', updated_at = NOW() WHERE id = $1", old["id"])
    new_id = await conn.fetchval("""
        INSERT INTO policy_item
            (namespace_id, system_key, category_path, policy_name, raw_body, remark, source_file, source_sheet,
             source_row, content_hash, status, logical_id, version, supersedes_id, parse_status,
             unresolved_segments, reviewed_at, reviewed_by, pipeline_version, source_key)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'active', $11, $12, $13, $14, $15, NOW(), $16, $17, $18)
        RETURNING id
    """, ns_id, old["system_key"], old["category_path"], old["policy_name"], new_raw, new_remark,
        old["source_file"], old["source_sheet"], old["source_row"], old["content_hash"], old["logical_id"],
        next_version, old["id"], old["parse_status"], old["unresolved_segments"],
        approver.get("id"), old["pipeline_version"], old.get("source_key"))   # 원천 키 유지 — 골든셋 정답·재적재 대조가 새 버전에서도 찾게(리뷰 M1)

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


async def approve(item_id: int, approver: dict, expected: Optional[dict]) -> dict:
    """저장된 수정안(AI 초안 또는 [직접 수정]으로 고친 안)을 반영한다. expected = 승인한 사람이 화면에서 본 수정안 — 그 사이 다른 사람이
    다시 쓰기·직접 수정·대상 변경을 했으면 보지 못한 안이 반영되지 않게 409. 고친 안은 먼저 update_proposed로 저장한 뒤 승인한다
    (승인 요청이 안을 덮어쓰는 길은 없다 — 일괄 승인도 건마다 이 검사를 그대로 탄다)."""
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
    if expected is not None and expected != stored:
        raise ConflictError("그 사이 수정안이 바뀌었어요 — 새로고침해서 바뀐 안을 확인한 뒤 승인하세요.")
    proposed = _validate_proposed(item["target_type"], stored)
    if _unchanged(item["target_type"], _json(item["original"]) or {}, proposed):   # 비고만 바꾼 안은 바뀐 것(10/21)
        raise ValueError("수정안이 기존 내용과 같아요 — 고칠 게 없으면 반려(종료)하세요.")
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
            locked = await conn.fetchrow(
                "SELECT status, proposed, target_type, target_id, target_sub_id FROM ops_improvement_item WHERE id = $1 FOR UPDATE",
                item_id)
            if not locked or locked["status"] != "pending":
                # 처음 읽을 땐 대기였다 = 임베딩·분류를 기다리는 사이 다른 승인·반려가 먼저 끝남 — "그 사이 바뀜"과 같이 409(10/21 리뷰 5b)
                raise ConflictError("그 사이 다른 사람이 먼저 처리한 신고예요 — 새로고침해서 확인하세요.")
            # 임베딩·분류(LLM)를 기다리는 동안 다른 사람이 다시 쓰기·대상 변경을 했으면 보지 못한 상태로 반영하지 않는다(리뷰 10/21)
            if (_json(locked["proposed"]) != stored
                    or (locked["target_type"], locked["target_id"], locked["target_sub_id"])
                    != (item["target_type"], item["target_id"], item["target_sub_id"])):
                raise ConflictError("그 사이 수정안이 바뀌었어요 — 새로고침해서 바뀐 안을 확인한 뒤 승인하세요.")
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

    # 승인 반영 후 시맨틱 캐시엔 옛 답이 TTL 동안 남아 있다 — 캐시를 비운다(best-effort)
    if item["target_type"] in ("policy_param", "policy_narrative"):
        # 정책은 채팅이 여러 업무 영역에 걸쳐 찾는다(scope.py) — 고친 영역만 비우면 다른 영역 답에 옛 정책이 남는다(10/21).
        # 정책 색인(키워드·분류)도 함께(리뷰 10/13).
        # 일괄 승인은 건마다 이 승인을 부르므로 Redis 전체 훑기가 건수만큼 돈다(10/21 리뷰 L8) — 지금 규모(대기 수십 건)에선 감수,
        # 한 번만 비우게 바꿀 조건은 docs/tech/post-demo-backlog.md §4
        try:
            from service.policy.caches import invalidate_policy_caches
            await invalidate_policy_caches()
        except Exception:
            logger.warning("정정 승인 후 정책 캐시 비우기 실패 (item=%s)", item_id, exc_info=True)
    else:
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
