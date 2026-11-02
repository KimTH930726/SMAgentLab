"""지식베이스 RAG 에이전트 — AgentBase 구현."""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from agents.base import AgentBase
from core.database import get_conn, knowledge_scope_extra_ids, namespace_scope_ids
from core.config import settings
from service.chat import memory
from service.chat.helpers import (
    LLM_UNAVAILABLE_MSG, LLM_EMPTY_MSG, NO_KNOWLEDGE_MARKER, SYSTEM_ERROR_MSG, is_llm_failure,
    results_to_json, results_to_payload,
    update_assistant_message, update_inhouse_conv_id,
    create_query_log, post_save_tasks,
)
from agents.knowledge_rag.knowledge import retrieval, glossary_terms
from service.policy import search as policy_search
from service.policy import category_list
from service.policy import ref_search
from service.chat import collection_suggest
from service.policy import scope as policy_scope
from service.policy.query_type import looks_like_condition_query, looks_like_navigation_query
from trustkit.answer.shaping import CONDITION_ANSWER_HINT_KO, list_scoped as _list_scoped, with_note as _with_note
from service.refdata import service as refdata_search
from service.llm.base import REPLACE_PREFIX, resolve_system_prompt
from service.llm.factory import get_llm_provider
from shared.embedding import embedding_service
from shared import cache as sem_cache
from shared import reranker as reranker_svc
from shared.rrf import rrf_score

logger = logging.getLogger(__name__)

_FLUSH_INTERVAL = 20
DEGRADED_POLICY_SOURCES = 3   # AI가 답을 못 냈을 때 보여 줄 정책 근거 수(검색 상위) — 근거만 보여 주기(2026-10-20)

# 정책 검색 후보 수(param·서술 각각) — 5에서 10으로(2026-09-28, scripts/diagnose_no_knowledge.py
# 골든셋 89문항 실측): 정답이 6~20위에 있어 잘리던 문항이 8→2건, 정상 답변 69→75건(77.5%→84.3%),
# 컨텍스트가 길어져도 "정답 있는데 거절"은 5→5건으로 늘지 않음. 평가 스크립트도 이 상수를 참조해
# 운영과 측정 조건이 어긋나지 않게 한다.
POLICY_CONTEXT_TOP_K = 10
# 채팅 기본(11/02 게이트 #71 업무지식·#72 정책서 — 정책 컬렉션 안 운영 지식을 시스템으로 옮긴 것과 한 묶음):
# 지식 범위 = 고른 컬렉션 + 관련 정책 연결(시스템 ↔ 정책, 양방향) · 문맥 = 한 줄 순위 위에서부터 1만 2천 자.
# 옮긴 뒤 linked 없이 정책에서 물으면 운영 지식 검색 0%(측정) — 둘 중 하나만 끄지 말 것.
CHAT_KNOWLEDGE_SCOPE = "linked"
CHAT_CONTEXT_BUDGET = 12000


def scope_overrides(scope: Optional[str], budget: Optional[int]) -> dict:
    """측정 스크립트 플래그 → build_chat_context 인자. 안 주면 채팅 기본 그대로, scope "none"·budget 0 = 끔."""
    out: dict = {}
    if scope:
        out["knowledge_scope"] = None if scope == "none" else scope
    if budget is not None:
        out["context_budget"] = budget or None
    return out


def effective_scope_label(scope: Optional[str], budget: Optional[int]) -> str:
    """측정 줄에 남길 실제 설정 — 플래그가 아니라 쓴 값(플래그 없음 = 채팅 기본이 바뀌어도 옛 파일과 섞이지 않게, 리뷰 M1)."""
    eff = {"knowledge_scope": CHAT_KNOWLEDGE_SCOPE, "context_budget": CHAT_CONTEXT_BUDGET, **scope_overrides(scope, budget)}
    return f"scope={eff['knowledge_scope'] or 'none'},budget={eff['context_budget'] or 0}"


async def _safe_post_save(conv_id: int, namespace: str) -> None:
    try:
        await post_save_tasks(conv_id, namespace)
    except Exception as e:
        logger.warning("post_save_tasks 실패: %s", e)


def _part_and_path(part, path) -> str:
    """정책 근거 머리 — 정책 범위가 여러 업무 영역이면 어느 영역 정책인지(같은 제목이 여러 영역에 있다, 2026-10-20)."""
    return (f" [업무 영역: {part}]" if part else "") + (f" [분류: {' > '.join(path)}]" if path else "")


def _build_rrf_context(
    results: list[retrieval.RetrievalResult], policy_result: policy_search.PolicySearchResult,
    common_codes: Optional[list[dict]] = None, db_columns: Optional[list[dict]] = None,
    parent_expansions: Optional[list[dict]] = None, budget: Optional[int] = None,
) -> str:
    """budget(글자 수, 측정 옵션 11/02 — 업계 방식 "전체 한 줄 순위 + 문맥 예산"): 한 줄로 세운 순위 위에서부터 예산 안에서만 싣는다
    (첫 항목은 늘 — 근거 0이 되지 않게). 상위 섹션 보충은 순위 항목 뒤, 남은 예산 안에서만. None = 지금처럼 전부.
    일반지식(코사인)·정책 파라미터(RDB ts_rank)·정책 서술(코사인)·구조화 참조데이터
    (공통코드/DB스키마, ts_rank)를 RRF로 합쳐 하나의 LLM 컨텍스트로 만든다(2026-09-18,
    참조데이터 축은 09-18 추가). 기존엔 retrieval.build_context() + policy_search.
    build_policy_context()를 그냥 이어붙였는데(항상 "일반지식 먼저, 정책 나중"), 축마다
    점수 스케일이 달라(코사인 0~1 vs ts_rank 0~5+) 실제로 더 관련성 높은 쪽이 항상 뒤에
    깔리는 구조적 문제가 있었음. 평가 게이트 즉석 질의(UnifiedAdhocSearch, 프론트에서
    RRF 적용)와 별개 백엔드 검증 스크립트(scripts/compare_rrf_context.py)로 실제 질문
    3건씩 두 라운드 비교한 결과 사실관계 왜곡은 없었고, 이 방식이 실제 순위 구조를 더
    정확히 반영해 채팅에도 동일하게 적용.

    참조데이터 축 추가 배경(예전 — 업무구분 키워드 전용 분기는 10/22 폐지): rag_knowledge의 "DB"/"공통코드" 카테고리(_KEYWORD_ONLY_
    CATEGORIES)는 벡터 채널과 같은 테이블 안에서 final_score로 경쟁하다 보니, ts_rank
    스케일이 코사인보다 항상 작아 ORDER BY ... LIMIT top_k 단계에서부터 후보 풀에도 못
    들어가는 문제가 실측 확인됐다("DS14가 뭐야?"가 자기 매칭 대상인 id=20을 top_k=5
    후보에서 완전히 배제 — 27건 중 27등). CMDB 데이터도 앞으로 같은 성격(정확 조회용
    RDB 데이터)이라 이 문제가 반복될 것으로 예상돼, rag_knowledge 안에서 SQL을 더
    복잡하게 만드는 대신 원래 이 목적으로 만들어졌던(§table-definition.md #43) 별도
    구조화 테이블(ref_common_code/ref_db_column, 이미 구현돼 있었지만 chat에서 호출을
    안 하고 있었음)로 완전히 분리 — 처음부터 독립된 RRF 축으로 둬서 스케일 경쟁 자체를
    피한다. id=20은 이 축으로 이전(마이그레이션 스크립트, rag_knowledge 쪽은 deprecated).

    retrieval.build_context()/policy_search.build_policy_context()는 각각 디버그
    검색(service/chat/router.py)·이메일 VOC 파이프라인·기존 정책 편입 로직 등 다른
    화면에서 그대로 쓰이고 있어(테스트도 그 출력 형식에 매여 있음) 건드리지 않고,
    여기서만 항목 단위로 다시 포맷한다 — 포맷 문자열이 일부 중복되지만 공유 함수를
    바꿔 여러 화면에 영향이 번지는 것보다 안전하다."""
    th = retrieval.get_thresholds()
    relevant = [r for r in results if retrieval.is_adopted(r, th)]

    items: list[tuple[float, str]] = []
    for i, r in enumerate(relevant):
        rel = retrieval.relevance_score(r)
        confidence = (
            "높음" if rel >= th["knowledge_high_score"]
            else "보통" if rel >= th["knowledge_mid_score"]
            else "낮음"
        )
        heading_str = f"\n상위 맥락: {' > '.join(r.heading_path)}" if r.heading_path else ""
        # 문서 식별자를 반드시 포함 — 없으면 시스템 프롬프트의 "📎 문서 N 참고" 지시를
        # 따를 근거가 컨텍스트에 없어, LLM이 옆에 보이는 점수(float)를 N으로 오인해
        # "문서 0.3925 참고"처럼 잘못 인용하는 실사고가 있었다(2026-09-22). #{id} 포맷은
        # 프론트 SearchResultCard.tsx의 `문서 #${result.id}` 표기와 동일해 사용자가
        # 위쪽 검색 결과 패널과 답변 하단 인용을 바로 대조할 수 있다.
        items.append((rrf_score(i), f"--- 문서 #{r.id} (점수: {rel:.4f}, 신뢰도: {confidence}) ---{heading_str}\n내용:\n{r.content}"))
    for i, p in enumerate(policy_result.params):
        value_str = f"{p.value}{p.unit or ''}" if p.value else "값 없음"
        condition_str = f" ({p.condition})" if p.condition else ""
        category_str = _part_and_path(p.part, p.category_path)
        items.append((rrf_score(i), f"[정책 파라미터 · 정확 일치: {p.policy_name}{condition_str}]{category_str} {p.param_name} = {value_str}"))
    for i, n in enumerate(policy_result.narratives):
        confidence = "높음" if n.score >= 0.6 else "보통" if n.score >= 0.4 else "낮음"
        category_str = _part_and_path(n.part, n.category_path)
        # chunk_text 대신 raw_body(원문 전체) 사용 이유는 service/policy/search.py의
        # build_policy_context()와 동일 — 여러 조각으로 쪼개진 항목의 적용범위 조건 유실 방지.
        body = _with_note(n.raw_body or n.chunk_text, n.remark)
        items.append((rrf_score(i), f"[정책 서술: {n.policy_name}]{category_str} (신뢰도: {confidence})\n{body}"))
    for i, c in enumerate(common_codes or []):
        items.append((rrf_score(i), f"[공통코드 · 정확 매칭: {c['group_code_name']}] {c['code_id']} = {c['code_name']}"))
    for i, d in enumerate(db_columns or []):
        items.append((rrf_score(i), f"[DB 스키마 · 정확 매칭: {d['table_name']}.{d['column_name']}] {d.get('column_comment') or ''} ({d.get('data_type') or ''})"))

    items.sort(key=lambda x: x[0], reverse=True)
    blocks: list[str] = []
    used = 0

    def fits(text: str) -> bool:   # 예산 안이면 싣는다 — 첫 블록은 늘
        nonlocal used
        if budget is not None and blocks and used + len(text) > budget:
            return False
        blocks.append(text)
        used += len(text) + 2
        return True

    for _, text in items:
        if not fits(text):
            break
    # 부모 섹션 보충(retrieval.expand_parent_sections) — 순위 경쟁에 넣지 않고 맨 뒤에, 보충임을 표시해서
    # 붙인다. 실측에서 확장 컨텍스트가 단일 질문 4/51건에서 정답과 어긋난 답을 만들어(이웃 내용 혼입 추정),
    # 검색된 문서를 우선 근거로 삼도록 구분한다.
    for e in parent_expansions or []:
        heading_str = f"\n상위 맥락: {' > '.join(e['heading_path'])}" if e.get("heading_path") else ""
        if not fits(f"--- 문서 #{e['id']} (같은 상위 섹션 보충 — 위 문서를 우선 근거로) ---{heading_str}\n내용:\n{e['content']}"):
            break
    return "\n\n".join(blocks)


@dataclass
class ChatContext:
    """채팅 한 턴의 검색·문맥 조립 결과 — 채팅과 측정 스크립트(scripts/eval_chat_retrieval.py)가 같은 함수를 써서
    "측정한 경로 = 실제 채팅 경로"가 되게 한다(v2.128: 예전 측정은 용어 매핑을 안 거쳐 채팅과 달랐다)."""
    results: list
    policy_result: policy_search.PolicySearchResult
    common_codes: list
    db_columns: list
    llm_context: str
    enriched_query: str
    mapped_term: Optional[str]
    term_matches: list = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    abstain: bool = False
    clarify: Optional[dict] = None   # 재질문(목록이 너무 넓거나 업무가 여럿) — 있으면 답 대신 선택지를 보낸다(v2.134)
    # 목록 답이었나 {total, names} — 화면이 근거 카드(1건 역추적) 위에 "목록 n건에서 답함"을 붙인다(10/23: 카드 1건 vs 답 36줄이 어긋나 보였다)
    list_answer: Optional[dict] = None
    refs: Optional[ref_search.RefResult] = None   # 참고표·문서 개요(v0 #12, 설정 policy_ref_chat이 켜졌을 때만)
    context_policy: Optional[policy_search.PolicySearchResult] = None   # 실제로 LLM 문맥에 실린 정책(목록 답이면 거른 것) — 운영 기록용(10/28)
    adopted_ids: set = field(default_factory=set)      # 채택돼 문맥에 실린 지식 id(문맥을 만든 그 판정 그대로 — 기록이 다시 계산하지 않게, 리뷰 L2)
    parent_ids: list = field(default_factory=list)     # 부모 섹션 보충으로 문맥에 붙은 지식 id(리뷰 M2)
    list_ids: list = field(default_factory=list)       # 분류 목록으로 문맥에 실린 정책 항목 id(리뷰 M2)


TRACE_VERSION = 1


def retrieval_trace(cc: "ChatContext") -> dict:
    """운영 질의 기록(10/28, 사용자 "실사용 질의가 검색에서 맞는 걸 찾았나·답을 잘 만들었나를 알아야"): 이 답에 AI에게 넘긴 근거의
    종류·id·순위·점수 + 근거 판정 신호 + 용어. **원문·검색어 글자는 안 남긴다**(번호·숫자만) — 나중에 id로 다시 꺼내 표본 채점·진단한다.
    순수 함수(테스트). 종류: pp 정책 파라미터 · pn 정책 서술 · kn 지식(a = 채택돼 문맥에 실림) · rt 참고표 · ov 개요."""
    pol = cc.context_policy or cc.policy_result
    ctx: list[dict] = []
    ctx += [{"t": "pp", "id": p.item_id, "lid": p.logical_id, "r": i + 1, "s": round(p.score, 4)} for i, p in enumerate(pol.params)]
    ctx += [{"t": "pn", "id": n.item_id, "lid": n.logical_id, "r": i + 1, "s": round(n.score, 4)} for i, n in enumerate(pol.narratives)]
    ctx += [{"t": "kn", "id": r.id, "r": i + 1, "s": round(retrieval.relevance_score(r), 4), "a": r.id in cc.adopted_ids}
            for i, r in enumerate(cc.results)]
    ctx += [{"t": "px", "id": i} for i in cc.parent_ids]   # 부모 섹션 보충(순위 없이 문맥 끝에)
    if cc.refs:
        ctx += [{"t": "rt", "id": t.id, "s": round(t.score, 4)} for t in cc.refs.tables]
        ctx += [{"t": "ov", "id": o.id, "s": round(o.score, 4)} for o in cc.refs.overviews]
    return {"v": TRACE_VERSION, "terms": [m.term for m in cc.term_matches], "signals": cc.signals, "abstain": cc.abstain,
            "clarify": bool(cc.clarify), "list": (cc.list_answer or {}).get("total"), "list_ids": list(cc.list_ids), "codes": len(cc.common_codes),
            "columns": len(cc.db_columns), "ctx": ctx}


def should_abstain(signals: dict, min_score: Optional[float], min_param_rank: float) -> bool:
    """근거 없음 즉시 판정 — 지식 채택 0·공통코드/DB 0·정책 파라미터 약함·정책 서술 최고점 < 하한. min_score가 None이면 끔.
    실측(2026-10-06, 채팅 경로·새 용어 방식, 골든 88 vs 답 없는 질문 60): (서술 0.50, 파라미터 0.05)에서 골든 거짓 거절 0,
    답 없음 35/60 차단 — 점수만으론 다 못 거르므로(분포가 겹침) 나머지는 LLM 거절에 맡긴다. 정답 질문의 파라미터 rank도 대부분
    0.05 미만이라 파라미터 하한은 사실상 "강하게 걸린 파라미터"만 근거로 친다. 배포 후 다시 재서 켠다(그래서 기본 꺼짐)."""
    if min_score is None:
        return False
    return (signals.get("adopted", 0) == 0 and signals.get("codes", 0) == 0 and signals.get("columns", 0) == 0
            and signals.get("category_items", 0) == 0 and signals.get("refs", 0) == 0
            and signals.get("max_param_rank", 0.0) <= min_param_rank
            and signals.get("top_narrative", 0.0) < min_score)


def list_scoped(hits: list, keep: set[int]) -> list:
    """목록 답 문맥용 — 목록 안 정책 전부 + 첫 목록 안 정책보다 순위가 높은 목록 밖 정책(입력 순서 유지, trustkit)."""
    return _list_scoped(hits, keep, key=lambda h: h.item_id)


# 첫 판(10/13)은 "② 그 조건 전용 규정이 없으면 없다고 밝혀라"였는데, 거절 6→0 대신 "3만 원 넘을 때만"에 "30,000원 이상 무료"를 두고
# "따로 정한 규정은 없다"고 단정하는 오답이 1→4로 늘었다(조건을 글자 그대로 읽음) — "없다" 단정 지시를 빼고 해당 내용 찾기로.
CONDITION_ANSWER_HINT = CONDITION_ANSWER_HINT_KO   # 문구 본체는 trustkit.answer.shaping


async def build_chat_context(
    namespace: str, search_question: str, query_vec: list[float], *,
    top_k: int, w_vector: float, w_keyword: float,
    glossary_mode: Optional[str] = None, glossary_entries: Optional[list] = None,
    policy_top_k: int = POLICY_CONTEXT_TOP_K, category_lists: bool = True, clarify_scope: Optional[str] = None,
    list_question: Optional[str] = None,
    knowledge_scope: Optional[str] = CHAT_KNOWLEDGE_SCOPE, context_budget: Optional[int] = CHAT_CONTEXT_BUDGET,
) -> ChatContext:
    """용어 → 지식/정책/참조데이터 검색 → RRF 문맥 조립. glossary_mode·glossary_entries는 측정용 덮어쓰기(기본은 설정값·DB)."""
    # 정책서 범위(2026-10-20, 사용자 결정): 지식은 고른 파트만, 정책서(정책·용어)는 정책 파트까지 — service/policy/scope.py.
    # 정책 데이터가 있는 파트만 돌려주므로 비어 있으면 정책 검색·분류 목록을 건너뛴다(예전 has_policy_data와 같은 역할).
    try:
        async with get_conn() as conn:
            policy_ns_ids = await policy_scope.policy_namespace_ids(conn, namespace)
    except Exception as e:
        logger.warning("정책서 범위 계산 실패(정책 없이 진행): %s", e)
        policy_ns_ids = []
    policy_available = bool(policy_ns_ids)
    extra_ns: list[int] = []
    # 지식 검색 범위(11/02): linked = 관련 정책 연결(채팅 기본), similar = linked + 질문과 가까운 컬렉션 2개(측정만), None = 고른 컬렉션만
    if knowledge_scope:
        async with get_conn() as conn:
            extra_ns = list(dict.fromkeys(await knowledge_scope_extra_ids(conn, namespace, knowledge_scope, query_vec)))
    term_matches, mapped_term, enriched_query = await glossary_terms.resolve_query_terms(
        namespace, search_question, query_vec, mode=glossary_mode, entries=glossary_entries, extra_ns_ids=policy_ns_ids)

    # 리랭커 활성화 시 더 많은 후보를 가져온 뒤 CrossEncoder로 재정렬
    candidate_k = settings.reranker_candidates if settings.reranker_enabled else top_k
    results_raw, refdata_available = await asyncio.gather(
        retrieval.search_knowledge(namespace, query_vec, enriched_query, w_vector, w_keyword, candidate_k,
                                   keyword_rescue=retrieval.KEYWORD_RESCUE, **({"extra_ns_ids": extra_ns} if extra_ns else {})),
        refdata_search.has_refdata(namespace),
    )
    if settings.reranker_enabled and len(results_raw) > top_k:
        results = await reranker_svc.rerank(enriched_query, results_raw, top_k)
    else:
        results = results_raw[:top_k]

    # 정책서 데이터 편입(2026-09-04 1단계, 2026-09-06 2단계, 2026-09-07 근거 1건 선별)
    # — Track 2로 하이브리드 스키마 우세 확정 후 rag_knowledge 검색과 별개로 정책
    # 데이터도 doc_context에 텍스트로 얹는다(1단계). policy_available=False인
    # 네임스페이스(정책 데이터 없음)는 이 블록 자체를 건너뛰어 매 턴 불필요한 쿼리를
    # 피한다. 2단계: "정책에서 온 답인지 기준정보에서 온 답인지 구분이 안 되고 원문도
    # 안 보인다"는 사용자 피드백으로 policy_citations를 별도로 만들어 화면에 "정책 근거"
    # 카드로 노출(§4-2/§6) — 기존 results(rag_knowledge 인용) 배열엔 안 섞는다
    # (FeedbackSection이 results[0].id를 rag_knowledge id로 쓰는 것과 충돌 방지).
    # 근거 1건 선별(2026-09-07): LLM 컨텍스트는 top_k 다중 후보를 그대로 유지해 재현율을 지키고,
    # 화면에 보여줄 근거 1건은 답변이 다 나온 "뒤에" select_cited_hit()으로 역추적한다.
    policy_result = policy_search.PolicySearchResult()
    if policy_available:
        try:
            policy_result = await policy_search.search_policy(
                namespace, enriched_query, top_k=policy_top_k, query_vec=query_vec, ns_ids=policy_ns_ids,
            )
        except Exception as e:
            logger.warning("정책 검색 실패(채팅 흐름은 계속 진행): %s", e)

    lq = list_question or search_question
    # 참고표 되묻기에서 고른 값("ref:…")은 분류 목록 범위가 아니다 — 목록 찾기엔 넘기지 않는다(고른 값은 질문 글에 붙어 온다)
    cat_scope = None if (clarify_scope or "").startswith(ref_search.REF_SCOPE) else clarify_scope
    # 탐색형("○○ 관련 정책 다 보여줘")·주제 전체("재고 정책 좀")는 검색(상위 몇 개)으로는 분류 전체가 안 온다 — 그 분류의 정책 목록을
    # DB에서 그대로(service/policy/category_list.py, 실측 포함률 42%→91%). 못 찾으면 지금처럼 검색 결과만. category_lists=False는 측정 비교용
    category = None
    if policy_available and category_lists:
        try:
            if policy_ns_ids:
                # 분류 목록·재질문은 "이번 질문"만으로 — 멀티턴 보강(직전 턴을 붙인 search_question)으로 고르면 앞 턴의 범위가 넘어온다
                # (2026-10-12 실측: "주문 > 장바구니" 다음 "주문 정책 전부"가 장바구니 10개만). 검색 재현율용 보강은 위 검색에만.
                lvec = query_vec if lq == search_question else await embedding_service.embed(lq)
                category = await category_list.find_category_list(
                    policy_ns_ids, lq, lvec, keyword_nav=looks_like_navigation_query(lq), scope=cat_scope)
        except Exception as e:
            logger.warning("분류 목록 조회 실패(검색 결과만으로 진행): %s", e)

    # 구조화 참조데이터(공통코드/DB스키마) 병행 검색(2026-09-18) — 정확 조회 전용이라 키워드(ts_rank)로만 찾는다.
    common_codes: list[dict] = []
    db_columns: list[dict] = []
    if refdata_available:
        try:
            common_codes, db_columns = await asyncio.gather(
                refdata_search.search_common_codes(namespace, enriched_query, top_k=5),
                refdata_search.search_db_columns(namespace, enriched_query, top_k=5),
            )
        except Exception as e:
            logger.warning("참조데이터 검색 실패(채팅 흐름은 계속 진행): %s", e)

    # 부모 섹션 확장 — 채택된 지식 청크의 같은 상위 섹션 이웃을 컨텍스트에 보충(실패해도 답변은 계속)
    th = retrieval.get_thresholds()
    adopted = [r for r in results if retrieval.is_adopted(r, th)]
    parent_expansions: list[dict] = []
    try:
        parent_expansions = await retrieval.expand_parent_sections(adopted)
    except Exception as e:
        logger.warning("부모 섹션 확장 실패(확장 없이 진행): %s", e)
    # 목록 답(목록 질문 알아보기·전체 명시 — category.is_listing + 분류 목록 있음)의 문맥에선 목록 밖 정책 검색 결과를 뺀다(v2.138) — 분류 목록은 정답과 개수까지 맞는데
    # (골든 탐색형 24문항 진단) 함께 실린 검색 결과(다른 분류 정책 최대 10개)를 LLM이 목록에 섞어 "다른 분류 섞임"이 탐색형 오답·부분의
    # 대부분이었다. 메타데이터로 범위를 정한 질문은 그 범위만 문맥에 두는 것이 업계 방식(self-query / metadata filtering).
    # 단 "전체 취소하면 쿠폰은?"처럼 "전체·모든"이 단어로만 든 구체 질문도 목록 답으로 잡히므로(리뷰 10/13 H1) 다 버리지 않고 순위로 거른다 —
    # 첫 목록 안 정책보다 위에 온 목록 밖 정책(그 질문의 진짜 정답일 수 있음)은 남기고, 그 아래 섞여 든 것만 뺀다.
    # 재질문에서 범위를 고른 경우(clarify_scope)도 목록 답이다. 인용 카드·근거 판정(signals)은 원래 검색 결과 그대로.
    ctx_policy = policy_result
    list_answer = None
    if category and category.item_ids and (category.is_listing or cat_scope):
        # shown = 문맥에 실린 줄 수(목록 상한 40) — 화면이 "n건 중 m건으로 답함"을 알 수 있게(리뷰 L3)
        list_answer = {"total": category.total, "names": list(category.names), "shown": min(category.total, len(category.lines))}
        ctx_policy = policy_search.PolicySearchResult(
            params=list_scoped(policy_result.params, set(category.item_ids)),
            narratives=list_scoped(policy_result.narratives, set(category.item_ids)))
    llm_context = _build_rrf_context(results, ctx_policy, common_codes, db_columns, parent_expansions, budget=context_budget)
    # 참고표·문서 개요(v0 #12) — 요약 벡터로 찾아 표 압축본·개요 본문을 문맥 끝에. 붙이는 규칙·하한은 ref_search 모듈 설명(10/26 측정)
    refs = None
    if settings.policy_ref_chat and policy_available:
        try:
            refs = await ref_search.search_refs(
                policy_ns_ids, query_vec, lq, top_narrative=max((n.score for n in policy_result.narratives), default=0.0),
                allow_clarify=not clarify_scope, overviews=settings.policy_ref_overview)
            block = ref_search.context_block(refs)
            if block:
                llm_context = f"{llm_context}\n\n{block}" if llm_context.strip() else block
        except Exception as e:
            logger.warning("참고표·개요 검색 실패(없이 진행): %s", e)
    # 조건 한정 질문("X에만 따로 …")의 답 구조(v2.138) — 근거가 문맥에 있는데도 "X 전용 규정"이 안 보이면 통째로 거절하거나
    # (조건형 18문항 중 거절 최대 6) 일반 규정을 X 전용처럼 섞어 답했다. 있는 것·없는 것·공통을 나눠 답하게 한다.
    if llm_context.strip() and looks_like_condition_query(lq):
        llm_context = f"{CONDITION_ANSWER_HINT}\n\n{llm_context}"
    # 용어 설명은 근거가 있을 때만 앞에 — 근거 없이 설명만 있으면 LLM이 설명으로 답을 지어낼 수 있다
    if category:
        llm_context = f"{category.block()}\n\n{llm_context}" if llm_context.strip() else category.block()
    definitions = glossary_terms.definitions_block(term_matches)
    if definitions and llm_context.strip():
        llm_context = f"{definitions}\n\n{llm_context}"

    signals = {
        "adopted": len(adopted), "codes": len(common_codes), "columns": len(db_columns),
        "params": len(policy_result.params),
        "max_param_rank": max((p.score for p in policy_result.params), default=0.0),
        "top_narrative": max((n.score for n in policy_result.narratives), default=0.0),
        "category_items": category.total if category else 0,
        "scope_missed": bool(category and category.scope_missed),
        "refs": len(refs.tables) + len(refs.overviews) if refs else 0,
    }
    return ChatContext(
        results=results, policy_result=policy_result, common_codes=common_codes, db_columns=db_columns,
        llm_context=llm_context, enriched_query=enriched_query, mapped_term=mapped_term,
        term_matches=term_matches, signals=signals,
        abstain=should_abstain(signals, settings.policy_abstain_min_score, settings.policy_abstain_min_param_rank),
        # 재질문 선택에서 온 질문(clarify_scope 있음)엔 다시 묻지 않는다 — 대화가 같은 질문으로 맴돌지 않게
        clarify=None if clarify_scope else (category_list.clarify_for(category, list_question or search_question) or (refs.clarify if refs and not list_answer else None)),
        list_answer=list_answer, refs=refs, context_policy=ctx_policy,
        adopted_ids={r.id for r in adopted}, parent_ids=[e["id"] for e in parent_expansions],
        # 분류 목록 블록이 문맥에 붙었으면 그 항목(목록 답이 아니어도 블록은 붙는다)
        list_ids=list(category.item_ids) if category and category.item_ids else [],
    )


SUGGEST_TIMEOUT = 2.0   # 초 — 다른 컬렉션 제안 계산 상한(넘으면 제안 생략)
TERM_NOTES_TIMEOUT = 1.0   # 초 — 팀 간 용어 참고 상한(용어집 조회 한 번, 평소 ms 단위)


async def _term_notes(namespace: str, matches: list) -> list[dict]:
    """팀 간 용어 참고(10/29) — 질문에 나온 용어가 다른 영역 용어집에 다른 뜻으로 있으면. 답엔 영향 없음, 실패하면 없이(로그만)."""
    if not matches:
        return []

    async def run() -> list[dict]:
        async with get_conn() as conn:
            return await glossary_terms.other_meanings(conn, matches, await namespace_scope_ids(conn, namespace))
    try:   # 답 완료(done)를 늦추지 않게 상한 — DB가 붐비면 참고 없이(리뷰 L3)
        return await asyncio.wait_for(run(), TERM_NOTES_TIMEOUT)
    except asyncio.TimeoutError:
        logger.info("팀 간 용어 참고 %.1f초 초과 — 이번 답엔 참고 없음(부하)", TERM_NOTES_TIMEOUT)
        return []
    except Exception:   # noqa: BLE001
        logger.warning("팀 간 용어 참고 계산 실패(답엔 영향 없음)", exc_info=True)
        return []


async def _suggestion(namespace: str, question: str, query_vec: list[float], results: list, **kw) -> Optional[dict]:
    """다른 컬렉션에서 찾아보기 제안(C2, 기본 꺼짐 — collection_suggest.enabled). 실패해도 답엔 영향 없음(로그만).
    SUGGEST_TIMEOUT 안에 못 끝나면 제안 없이 — 답 완료를 늦추지 않는다(10/27 부하 측정: 동시 60건이면 제안이 최대 5초)."""
    try:
        return await asyncio.wait_for(collection_suggest.find(namespace, question, query_vec, results, **kw), SUGGEST_TIMEOUT)
    except asyncio.TimeoutError:
        logger.info("컬렉션 제안 %.1f초 초과 — 이번 답엔 제안 없음(부하)", SUGGEST_TIMEOUT)
        return None
    except Exception:   # noqa: BLE001
        logger.warning("컬렉션 제안 계산 실패(답엔 영향 없음)", exc_info=True)
        return None


async def _record_clarify(conversation_id: int, msg_id: int, clarify: dict, user: dict) -> None:
    """되묻기를 AI 판단 원장에 한 줄(E, 사용자 10/23 "재질문 관리") — 답에는 영향 없음, 실패해도 대화는 계속(로그만)."""
    try:
        from service.decision import ledger
        async with get_conn() as conn:
            await ledger.clarify_asked(conn, conversation_id=conversation_id, message_id=msg_id, clarify=clarify, user_id=user.get("id"))
    except Exception:   # noqa: BLE001
        logger.warning("되묻기 원장 기록 실패(대화는 계속)", exc_info=True)


async def _close_open_clarify(conversation_id: int, clarify_scope: Optional[str], user: dict) -> None:
    """이 대화에 열린 되묻기가 있으면 닫는다 — 선택지를 골랐으면(clarify_scope) 그 범위, 아니면 직접 입력. 실패해도 대화는 계속."""
    try:
        from service.decision import ledger
        async with get_conn() as conn:
            await ledger.clarify_answered(conn, conversation_id=conversation_id, picked_scope=clarify_scope, user_id=user.get("id"))
    except Exception:   # noqa: BLE001
        logger.warning("되묻기 원장 닫기 실패(대화는 계속)", exc_info=True)


class KnowledgeRagAgent(AgentBase):

    @property
    def agent_id(self) -> str:
        return "knowledge_rag"

    @property
    def metadata(self) -> dict:
        return {
            "display_name": "지식베이스 AI",
            "description": "운영 가이드 및 매뉴얼 기반 질의응답",
            "icon": "BookOpen",
            "color": "indigo",
            "output_type": "text",
            "welcome_message": "운영 관련 질문을 입력해주세요.",
            "supports_debug": True,
        }

    async def stream_chat(
        self,
        query: str,
        user: dict,
        conversation_id: int,
        context: dict,
    ) -> AsyncIterator[dict]:
        namespace: str = context["namespace"]
        msg_id: int = context["msg_id"]
        w_vector: float = context.get("w_vector", 0.7)
        w_keyword: float = context.get("w_keyword", 0.3)
        top_k: int = context.get("top_k", 5)
        user_credentials: Optional[dict] = context.get("user_credentials")
        inhouse_conv_id: Optional[str] = context.get("inhouse_conv_id")
        clarify_scope: Optional[str] = context.get("clarify_scope")
        suggest_of: Optional[int] = context.get("suggest_of")

        full_answer = ""
        token_count = 0
        mapped_term: Optional[str] = None
        llm_failed = False
        final_saved = False   # 답을 최종 저장했나 — 그 뒤 오류(질의 기록·캐시 등)로 이미 나간 답을 실패로 덮지 않게(리뷰 L2)
        msg_status = "completed"   # 저장한 상태 — 저장 뒤 오류에도 끝 신호를 이 값으로(거부도 failed, 재리뷰 D3)
        trace: Optional[dict] = None   # 운영 질의 기록 — 검색 뒤에 채운다(오류 경로도 남기게)

        try:
            await _close_open_clarify(conversation_id, clarify_scope, user)
            if suggest_of:   # "다른 컬렉션에서 찾아보기"를 눌러 온 질문 — 제안 줄을 "누름"으로(C2)
                await collection_suggest.picked(message_id=suggest_of, user_id=user.get("id"))
            yield {"type": "status", "step": "embedding", "message": "질문 임베딩 생성 중..."}

            # ── 멀티턴 검색 보강: 직전 턴과 관련 있을 때만 결합 ──
            # user_msg_id(현재 질문) 이전 메시지만 봐야 함 — msg_id(답변 placeholder)로
            # 필터링하면 라우터가 미리 저장해둔 현재 질문까지 "직전 맥락"으로 잘못 포함됨
            user_msg_id = context.get("user_msg_id", msg_id)
            search_question, query_vec = await memory.augment_query_for_search(
                conversation_id, query, exclude_message_id=user_msg_id,
            )
            # 캐시 키는 반드시 "이번에 실제로 물은 질문"만으로 계산한다(2026-09-18 실사고
            # 수정) — search_question은 멀티턴 보강이 직전 턴을 붙인 결과라 검색 재현율엔
            # 도움이 되지만, 캐시 키로 쓰면 완전히 다른 두 질문이 "직전 턴이 같다"는 이유로
            # 캐시 벡터가 서로 가까워져 오답이 재사용될 수 있다(실측: "쿠폰 회수 정책
            # 알려줘" 다음에 전혀 무관한 "정책적으로 최대 재고 갯수가 몇개?"를 물었는데
            # MULTITURN_RELEVANCE_THRESHOLD(0.35)를 "정책"이라는 단어 하나로 넘겨 직전
            # 턴이 붙었고, 그 오염된 병합 텍스트로 계산한 캐시 벡터가 1턴째와 코사인
            # 0.9309로 나와 캐시 임계치를 넘어서 완전히 무관한 답이 그대로 재사용됨).
            cache_vec = await embedding_service.embed(sem_cache.normalize_query(query))

            # ── Semantic Cache 조회 ──
            # 재질문에서 범위를 고른 요청은 캐시를 읽지도 쓰지도 않는다 — 범위 없이 답한 비슷한 질문의 답이 걸리면 고른 범위와 무관
            # 대화의 첫 질문만 캐시를 쓴다: 2턴째부터는 LLM이 대화 이력(직전 교환·요약)을 보고 답해 같은 글("수신자는요?")이라도 대화마다
            # 뜻이 다르다 — 다른 대화의 답이 그대로 나갔다(2026-10-13 멀티턴 실측: 텀블러 쿠폰 대화에 선물하기 수신자 답). 검색 보강
            # 여부로만 가르면 보강 기준(유사도 0.35)에 못 미친 짧은 후속 질문이 새어 나갔다(리뷰).
            contextual = search_question != query or await memory.has_previous_turns(conversation_id, exclude_message_id=user_msg_id)
            cached = None if (clarify_scope or contextual) else await sem_cache.get_cached(namespace, "knowledge_rag", cache_vec)
            if cached:
                cached_citations = cached.get("policy_citations", [])
                # 캐시 응답도 근거(results)를 메시지에 남긴다 — 안 남기면 다시 열었을 때 근거 카드가 사라지고,
                # "답변 틀림" 자동 식별이 그 답변의 근거를 못 찾아 "빠진 내용"으로 잘못 분류된다(2026-10-01 실측).
                async with get_conn() as conn:
                    await conn.execute(
                        "UPDATE ops_message SET mapped_term = $1, results = $2::jsonb WHERE id = $3",
                        cached.get("mapped_term"), json.dumps(cached.get("results", []), ensure_ascii=False), msg_id,
                    )
                cached_list = cached.get("list_answer")   # 목록 답 표시도 캐시에서(리뷰 L1) — 옛 캐시 항목엔 없음
                # 다른 컬렉션 제안은 캐시하지 않는다(설정·다른 컬렉션 지식이 그 사이 바뀜) — 캐시 답에도 지금 기준으로 계산(검색만, C2)
                cached_sug = None if suggest_of else await _suggestion(namespace, search_question, query_vec, None,
                                                                         w_vector=w_vector, w_keyword=w_keyword, top_k=top_k)
                cached_notes = cached.get("term_notes")   # 팀 간 용어 참고 — 캐시 답은 용어를 다시 안 찾으니 처음 답의 것(옛 캐시 항목엔 없음)
                cached_meta = {k: v for k, v in (("policy_citations", cached_citations), ("list_answer", cached_list),
                                                 ("collection_suggest", cached_sug), ("term_notes", cached_notes)) if v}
                # 캐시 답 — 이번엔 검색을 안 돌림. 근거는 처음 답한 메시지 기록(src)에(옛 캐시 항목엔 없음)
                cached_meta["trace"] = {"v": TRACE_VERSION, "cached": True, "src": cached.get("message_id")}
                await update_assistant_message(msg_id, cached["answer"], "completed", metadata=cached_meta)
                final_saved = True
                yield {
                    "type": "meta", "conversation_id": conversation_id, "message_id": msg_id,
                    "mapped_term": cached.get("mapped_term"),
                    "results": cached.get("results", []),
                    "policy_citations": cached_citations,
                    "list_answer": cached_list,
                }
                yield {"type": "token", "data": cached["answer"]}
                if cached_sug:
                    await collection_suggest.record(conversation_id=conversation_id, message_id=msg_id, suggestion=cached_sug,
                                                    user_id=user.get("id"))
                    yield {"type": "suggest", **cached_sug}
                if cached_notes:
                    yield {"type": "term_notes", "items": cached_notes}
                # 근거 유무는 캐시 저장 시점 값으로 — 예전엔 results(지식)만 봐서 정책·공통코드만으로 답한 캐시 응답이
                # 공백으로 잘못 세졌다(2026-10-02 실측: 정책 답변 3건). 옛 캐시 항목엔 값이 없어 지식·정책 근거로 대신 판단.
                had_context = cached.get("had_context", bool(cached.get("results") or cached_citations))
                await create_query_log(namespace, query, cached["answer"], cached.get("mapped_term"), msg_id,
                                       user_id=user.get("id"), had_context=had_context)
                yield {"type": "done", "message_id": msg_id, "status": "completed"}
                return

            yield {"type": "status", "step": "context", "message": "용어 확인 및 관련 문서 검색 중..."}
            cc, history = await asyncio.gather(
                build_chat_context(namespace, search_question, query_vec, top_k=top_k, w_vector=w_vector,
                                   w_keyword=w_keyword, clarify_scope=clarify_scope,
                                   list_question=query),
                memory.build_context_history(conversation_id, query_vec),
            )
            results, policy_result, llm_context, mapped_term = cc.results, cc.policy_result, cc.llm_context, cc.mapped_term
            # 운영 질의 기록(10/28) — 넘긴 근거 id·순위·점수·판정 신호. 되묻기·근거 없음·답 모두 메시지 metadata["trace"]에(원문 없음)
            try:
                trace = retrieval_trace(cc)
            except Exception:   # 진단용 보조 기록 — 실패해도 답은 계속(로그만, 리뷰 L1)
                logger.warning("운영 질의 기록 만들기 실패 msg=%s", msg_id, exc_info=True)
                trace = {"v": TRACE_VERSION, "error": True}
            if cc.signals.get("scope_missed"):
                yield {"type": "status", "step": "context", "message": "고른 범위를 찾지 못해 질문 내용으로 다시 찾았습니다"}
            policy_citations: list[dict] = []

            async with get_conn() as conn:
                # 기록(trace)을 여기서 먼저 — 이후 오류·사용자 중단으로 끝나도 남는다(최종 저장은 trace를 다시 넣어 덮음, 리뷰 M1)
                await conn.execute(
                    "UPDATE ops_message SET mapped_term = $1, results = $2::jsonb, metadata = jsonb_build_object('trace', $4::jsonb) WHERE id = $3",
                    mapped_term, results_to_json(results), msg_id, json.dumps(trace, ensure_ascii=False),
                )

            # 제안 버튼으로 다른 컬렉션에서 다시 물은 답 — 답한 컬렉션을 남겨 "답변 틀림"이 그 컬렉션 건으로 가게(보는 컬렉션 아님, 리뷰 M1)
            answered_ns = {"answered_namespace": namespace} if suggest_of else {}
            yield {
                "type": "meta", "conversation_id": conversation_id, "message_id": msg_id,
                "mapped_term": mapped_term, "results": results_to_payload(results),
                "policy_citations": policy_citations, **answered_ns,
            }

            # 근거 없음 즉시 판정(v2.128, 설정으로 켬) — LLM을 안 불러 지어낼 기회도, 게이트웨이 대기(100초+)도 없다.
            # 캐시에는 넣지 않는다(지식이 새로 들어오면 바로 답해야 하는데 고정 거절이 TTL 동안 남는다).
            # 재질문(v2.134): 목록이 너무 넓거나 같은 이름이 여러 업무에 있으면 답 대신 선택지 — LLM 호출 없음. 같은 대화에 남아 다음 선택이
            # 그대로 이어진다(메시지 metadata에 선택지 저장 → 다시 열어도 버튼). 질의 기록·캐시엔 안 남긴다(선택한 다음 질문이 남음)
            if cc.clarify:
                await update_assistant_message(msg_id, cc.clarify["message"], "completed", metadata={"clarify": cc.clarify, "trace": trace})
                final_saved = True
                await _record_clarify(conversation_id, msg_id, cc.clarify, user)
                yield {"type": "clarify", **cc.clarify}
                yield {"type": "token", "data": cc.clarify["message"]}
                yield {"type": "done", "message_id": msg_id, "status": "completed"}
                return

            if cc.abstain:
                suggestion = None if (suggest_of or clarify_scope) else await _suggestion(
                    namespace, search_question, query_vec, results, w_vector=w_vector, w_keyword=w_keyword, top_k=top_k)
                abstain_meta = {**({"collection_suggest": suggestion} if suggestion else {}), **answered_ns, "trace": trace}
                await update_assistant_message(msg_id, NO_KNOWLEDGE_MARKER, "completed", metadata=abstain_meta)
                final_saved = True
                yield {"type": "token", "data": NO_KNOWLEDGE_MARKER}
                if suggestion:
                    await collection_suggest.record(conversation_id=conversation_id, message_id=msg_id, suggestion=suggestion,
                                                    user_id=user.get("id"))
                    yield {"type": "suggest", **suggestion}
                await create_query_log(namespace, query, NO_KNOWLEDGE_MARKER, mapped_term, msg_id,
                                       user_id=user.get("id"), had_context=False)
                yield {"type": "done", "message_id": msg_id, "status": "completed"}
                return

            yield {"type": "status", "step": "llm", "message": "AI 답변 생성 중..."}

            new_inhouse_conv_id: Optional[str] = None

            def _capture_inhouse_conv_id(cid: str) -> None:
                nonlocal new_inhouse_conv_id
                new_inhouse_conv_id = cid

            chat_prompt = await resolve_system_prompt()

            try:
                async for token in get_llm_provider().generate_stream(
                    llm_context, query, history,
                    user_credentials=user_credentials,
                    ext_conversation_id=inhouse_conv_id,
                    on_ext_conversation_id=_capture_inhouse_conv_id,
                    system_prompt=chat_prompt,
                ):
                    if isinstance(token, str) and token.startswith(REPLACE_PREFIX):
                        # 게이트웨이 검열이 답을 통째로 교체 — 저장·캐시·화면 모두 교체문으로(지금까지 보낸 토큰은 버림)
                        full_answer = token[len(REPLACE_PREFIX):]
                        logger.warning("게이트웨이가 답변을 교체함(message_replace) — 교체문 %d자", len(full_answer))
                        await update_assistant_message(msg_id, full_answer)
                        yield {"type": "replace", "data": full_answer}
                        continue
                    full_answer += token
                    token_count += 1
                    if token_count == 1 or token_count % _FLUSH_INTERVAL == 0:
                        await update_assistant_message(msg_id, full_answer)
                    yield {"type": "token", "data": token}
            except Exception as e:
                logger.warning("LLM 스트리밍 실패: %s", e)
                llm_failed = True
                full_answer = LLM_UNAVAILABLE_MSG
                yield {"type": "token", "data": LLM_UNAVAILABLE_MSG}

            if not llm_failed and not full_answer.strip():   # 공백만 온 것도 빈 응답(리뷰 2026-10-07)
                # 예외 없이 토큰 0개로 끝남(2026-10-06 실측: 0.4초 만에 빈 응답 4건) — 예전엔 화면에 아무것도 안 보내 빈 말풍선만
                # 남았고 로그도 없었다. 게이트웨이 쪽 상세(받은 이벤트 종류·상태)는 provider가 경고로 남긴다.
                logger.warning("LLM 빈 응답(토큰 0개, 예외 없음): namespace=%s msg_id=%s context=%d자",
                               namespace, msg_id, len(llm_context))
                llm_failed = True
                full_answer = LLM_EMPTY_MSG
                yield {"type": "token", "data": LLM_EMPTY_MSG}

            final_answer = full_answer or LLM_UNAVAILABLE_MSG
            # 근거만 보여 주기(2026-10-20): AI가 답을 못 냈어도(연결 실패·빈 응답·게이트웨이 거부) 찾은 근거는 있다 — 일반 답처럼 보이는
            # 실패 문구 대신 "근거만" 표시로. 예전엔 정책 근거를 실패 문구와 겹치는 것으로 골라 사실상 늘 사라졌다(정책만으로 답하는
            # 질문은 근거가 0으로 보였다). 업계의 생성 실패 시 출처 목록으로 물러나기(graceful degradation)와 같다.
            degraded = llm_failed or is_llm_failure(final_answer)
            msg_status = "failed" if degraded else "completed"

            if policy_result.params or policy_result.narratives:
                try:
                    if degraded:   # 답과 겹치는 것을 고를 수 없으니 검색 상위 후보를 그대로
                        top = policy_search.PolicySearchResult(params=policy_result.params[:DEGRADED_POLICY_SOURCES],
                                                               narratives=policy_result.narratives[:DEGRADED_POLICY_SOURCES])
                        policy_citations = policy_search.build_policy_citations(top)
                    else:          # 정책 근거 1건 역추적(2026-09-07) — 답변이 나온 뒤에야 어떤 후보를 참고했는지 안다
                        cited = policy_search.select_cited_hit(policy_result, final_answer)
                        policy_citations = policy_search.build_policy_citations(cited)
                except Exception as e:
                    logger.warning("정책 근거 선택 실패(답변엔 영향 없음): %s", e)

            meta_out: dict = {}
            if policy_citations:
                meta_out["policy_citations"] = policy_citations
            if cc.list_answer and not degraded:
                meta_out["list_answer"] = cc.list_answer
            if degraded:
                meta_out["degraded"] = True
            # 다른 컬렉션에서 찾아보기(C2, 기본 꺼짐) — 답은 그대로, 더 맞는 컬렉션이 보이면 버튼으로만. 실패해도 답엔 영향 없음(로그만)
            suggestion = None if (degraded or suggest_of or clarify_scope) else await _suggestion(
                namespace, search_question, query_vec, results, w_vector=w_vector, w_keyword=w_keyword, top_k=top_k)
            if suggestion:
                meta_out["collection_suggest"] = suggestion
            term_notes = [] if degraded else await _term_notes(namespace, cc.term_matches)
            if term_notes:
                meta_out["term_notes"] = term_notes
            meta_out.update(answered_ns)
            meta_out["trace"] = trace
            await update_assistant_message(msg_id, final_answer, msg_status, metadata=meta_out)
            final_saved = True
            if suggestion:
                await collection_suggest.record(conversation_id=conversation_id, message_id=msg_id, suggestion=suggestion,
                                                user_id=user.get("id"))
                yield {"type": "suggest", **suggestion}
            if term_notes:
                yield {"type": "term_notes", "items": term_notes}
            if policy_citations or degraded or meta_out.get("list_answer"):
                yield {
                    "type": "meta", "conversation_id": conversation_id, "message_id": msg_id,
                    "mapped_term": mapped_term, "results": results_to_payload(results),
                    "policy_citations": policy_citations, "degraded": degraded,
                    "list_answer": meta_out.get("list_answer"),
                }
            if new_inhouse_conv_id and new_inhouse_conv_id != inhouse_conv_id:
                await update_inhouse_conv_id(conversation_id, new_inhouse_conv_id)
            had_context = bool(llm_context.strip())
            await create_query_log(namespace, query, final_answer, mapped_term, msg_id, user_id=user.get("id"),
                                   had_context=had_context)

            # ── Semantic Cache 저장 (LLM 정상 응답 시만, 결과 유무 무관 — 연결 실패·게이트웨이 거부는 제외) ──
            if not is_llm_failure(final_answer) and not clarify_scope and not contextual:
                await sem_cache.set_cached(namespace, "knowledge_rag", cache_vec, {
                    "answer": final_answer,
                    "message_id": msg_id,   # 캐시 답의 운영 기록이 처음 답한 메시지(근거 기록)를 가리키게(리뷰 L3)
                    "mapped_term": mapped_term,
                    "results": results_to_payload(results),
                    "policy_citations": policy_citations,
                    "list_answer": meta_out.get("list_answer"),
                    "term_notes": term_notes,
                    "query": query,
                    "had_context": had_context,
                })

            yield {"type": "done", "message_id": msg_id, "status": msg_status}

        except Exception as e:
            # LLM 밖(검색·DB·임베딩)의 오류 — LLM 탓으로 저장하지 않고, 실패 상태·장애 기록·끝 신호까지(2026-10-20 점검: 예전엔 "LLM 연결
            # 불가"·completed로 저장하고 done도 질의 기록도 없었다)
            logger.error("KnowledgeRagAgent 에러: %s", e, exc_info=True)
            if final_saved:   # 답은 이미 저장·전달됨 — 뒤처리 오류는 로그만
                yield {"type": "done", "message_id": msg_id, "status": msg_status}
                return
            answered = bool(full_answer)   # 스트리밍 도중 끊김이면 받은 데까지는 근거만 보여 주기로 표시(일반 답처럼 보이지 않게)
            if not answered:
                full_answer = SYSTEM_ERROR_MSG
            try:
                # 검색 뒤 오류면 그때 기록(trace)을 지키고 표시만 더한다(None이면 검색 전 오류 — 기록 없음)
                fail_meta = {**({"trace": trace} if trace else {}), **({"degraded": True} if answered else {})}
                await update_assistant_message(msg_id, full_answer, "failed", metadata=fail_meta or None)
                await create_query_log(namespace, query, SYSTEM_ERROR_MSG, mapped_term, msg_id,
                                       user_id=user.get("id"), had_context=False)
            except Exception as e2:
                logger.warning("오류 응답 저장 실패: %s", e2)
            if answered:
                # 근거 카드는 이미 화면에 있다 — 빈 목록을 보내면 화면이 지운다(재리뷰 D1). 표시만
                yield {"type": "meta", "conversation_id": conversation_id, "message_id": msg_id, "degraded": True}
            else:
                yield {"type": "token", "data": SYSTEM_ERROR_MSG}
            yield {"type": "done", "message_id": msg_id, "status": "failed"}
        finally:
            asyncio.create_task(_safe_post_save(conversation_id, namespace))
