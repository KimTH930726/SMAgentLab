"""평가 게이트 › 검색 미리보기(10/27 밤, docs/tech/self-improvement-loop-design.md §3-5) — 질문 하나를 채팅과 같은 검색으로 돌려 본다. LLM 0.

막힌 일: 옛 즉석 질의는 지식·정책·참조데이터를 각자 따로 검색해 화면에서 합쳤다 — 용어 매핑·분류 목록·되묻기·근거 판정·목록 거르기가 빠져
채팅과 다른 결과를 보였다(사용자 10/27 "즉석 질의도 필요한가"). 업계(Dify·RAGFlow 검색 테스트)처럼 실제 경로 그대로를 보여 주고,
이상한 질문은 바로 시험지에 넣어 다음 측정부터 잰다(실사용 층 — 정정 승인 문항과 같은 줄, source='preview').
같은 함수(agent.build_chat_context)를 쓰므로 "미리보기 = 채팅 검색"(답 캐시·멀티턴 보강·리랭커 후보 수만 다름 — 리랭커는 같은 설정을 따른다).
"""
from __future__ import annotations

import json
from core.database import get_conn

SNIPPET = 240   # 지식 조각 미리보기 글자 수 — 원문 전체는 지식 문서 화면에서


def verdict(cc) -> dict:
    """채팅이 이 질문에 무엇을 할지(순수 함수) — 되묻기 > 근거 없음 > 목록 답 > 답. 채팅 stream_chat의 분기 순서와 같다."""
    if cc.clarify:
        return {"key": "clarify", "name": "되묻기", "detail": cc.clarify.get("message"), "options": [o.get("label") for o in cc.clarify.get("options", [])]}
    if cc.abstain:
        return {"key": "abstain", "name": "근거 없음 — LLM 없이 \"관련 지식을 찾지 못했습니다\"", "detail": None, "options": []}
    if cc.list_answer:
        la = cc.list_answer
        return {"key": "list", "name": f"목록 답 — {la['total']}건 중 {la['shown']}건을 문맥에", "detail": " · ".join(la["names"][:3]) or None, "options": []}
    if not cc.llm_context.strip():
        return {"key": "empty", "name": "문맥 없음 — LLM이 거절할 가능성이 큼", "detail": None, "options": []}
    return {"key": "answer", "name": "답 — 아래 근거를 문맥으로 LLM 호출", "detail": None, "options": []}


def items(cc, adopted_ids: set[int], relevance) -> list[dict]:
    """문맥 후보를 갈래별로(순수 함수). 정책은 logical_id(시험지 정답 고르기), 지식은 id·채택 여부·앞부분만. relevance = retrieval.relevance_score."""
    out: list[dict] = []
    for i, p in enumerate(cc.policy_result.params):
        out.append({"kind": "policy", "how": "정책 파라미터 · 글자 일치", "rank": i + 1, "logical_id": p.logical_id, "name": p.policy_name,
                    "path": " > ".join(p.category_path or []), "score": round(p.score, 4), "label": f"{p.param_name} = {p.value}" if getattr(p, "param_name", None) else None})
    for i, n in enumerate(cc.policy_result.narratives):
        out.append({"kind": "policy", "how": "정책 서술 · 의미 유사도", "rank": i + 1, "logical_id": n.logical_id, "name": n.policy_name,
                    "path": " > ".join(n.category_path or []), "score": round(n.score, 4), "label": None})
    for i, r in enumerate(cc.results):
        out.append({"kind": "knowledge", "how": "지식 · 하이브리드(의미+키워드)", "rank": i + 1, "id": r.id, "name": " > ".join(r.heading_path or []) or f"문서 #{r.id}",
                    "path": r.source_file or "", "score": round(relevance(r), 4), "adopted": r.id in adopted_ids,
                    "label": (r.content or "")[:SNIPPET]})
    for c in cc.common_codes:
        out.append({"kind": "refdata", "how": "공통코드 · 키워드", "name": str(c.get("code_name") or c.get("name") or c.get("code") or "공통코드"), "path": "", "score": None, "label": None})
    for c in cc.db_columns:
        out.append({"kind": "refdata", "how": "DB 칼럼 · 키워드", "name": str(c.get("column_name") or c.get("name") or "칼럼"), "path": str(c.get("table_name") or ""), "score": None, "label": None})
    if cc.refs:
        for t in cc.refs.tables:
            out.append({"kind": "ref", "how": "참고표 · 요약 유사도", "name": t.title, "path": "", "score": round(t.score, 4), "label": None})
        for o in cc.refs.overviews:
            out.append({"kind": "ref", "how": "문서 개요 · 요약 유사도", "name": o.title, "path": "", "score": round(o.score, 4), "label": None})
    return out


async def preview(namespace: str, question: str) -> dict:
    from agents.knowledge_rag.agent import build_chat_context
    from agents.knowledge_rag.knowledge import retrieval
    from shared.embedding import embedding_service
    d = retrieval.get_search_defaults()
    vec = await embedding_service.embed(question)
    cc = await build_chat_context(namespace, question, vec, top_k=int(d["default_top_k"]), w_vector=d["default_w_vector"],
                                  w_keyword=d["default_w_keyword"], list_question=question)
    th = retrieval.get_thresholds()
    adopted = {r.id for r in cc.results if retrieval.is_adopted(r, th)}
    return {"namespace": namespace, "question": question, "verdict": verdict(cc),
            "terms": [m.term for m in cc.term_matches], "mapped_term": cc.mapped_term, "signals": cc.signals,
            "context_chars": len(cc.llm_context), "items": items(cc, adopted, retrieval.relevance_score)}


async def add_question(*, namespace: str, question: str, logical_id: int, user_id: int) -> dict:
    """시험지에 추가 — 정답 = 고른 정책(현행 source_key). 실사용 층(eval_question_candidate status='added', source='preview').
    같은 컬렉션·같은 질문이 이미 시험지에 있으면 새로 만들지 않는다(측정 로더도 같은 질문은 하나만 쓴다 — questions.real_golden)."""
    q = question.strip()
    if not q:
        raise ValueError("질문이 비었어요")
    async with get_conn() as conn:
        ns_id = await conn.fetchval("SELECT id FROM ops_namespace WHERE name = $1", namespace)
        if ns_id is None:
            raise ValueError("컬렉션을 찾지 못했어요")
        p = await conn.fetchrow("SELECT logical_id, source_key FROM policy_item WHERE logical_id = $1 AND source_key IS NOT NULL "
                                "AND status = 'active' ORDER BY id DESC LIMIT 1", logical_id)
        if not p:
            raise ValueError("정답으로 고른 정책을 찾지 못했어요")
        async with conn.transaction():
            # 같은 질문 두 번 누름·두 사람 동시 — 컬렉션+질문 잠금으로 한 줄만(리뷰 관례: 동시 등록 중복)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"eval_q:{ns_id}:{q}")
            dup = await conn.fetchval("SELECT id FROM eval_question_candidate WHERE namespace_id = $1 AND question = $2 AND status = 'added'", ns_id, q)
            if dup:
                return {"id": dup, "qid": f"REAL-{dup}", "created": False}
            gold = {"target_type": "policy", "kind": "policy", "logical_id": p["logical_id"], "source_key": p["source_key"]}
            new_id = await conn.fetchval(
                """INSERT INTO eval_question_candidate (improvement_item_id, namespace_id, question, gold, status, decided_at, decided_by_user_id, source)
                   VALUES (NULL, $1, $2, $3::jsonb, 'added', NOW(), $4, 'preview') RETURNING id""",
                ns_id, q, json.dumps(gold, ensure_ascii=False), user_id)
    return {"id": new_id, "qid": f"REAL-{new_id}", "created": True}

