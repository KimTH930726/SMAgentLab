"""답 기법 대장 (2026-10-23, eval-gate-redesign §9-4) — 검색·답을 다듬는 기법의 기준을 한 곳에(코드 상수에만 숨어 까먹지 않게).

항목 = 이름 · 단계 · 하는 일 · 기준값(설정 키가 있으면 운영값을 읽음) · 도입·보류 근거(날짜·숫자) · 코드 위치(파일:이름).
자가 개선 루프(scripts/eval_improve.py)가 "틀린 원인 → 볼 기법"을 이 키로 가리키고, 품질 › 답 기법 화면이 읽는다.
테스트(tests/test_eval_techniques.py): 코드 위치의 이름이 실제 그 파일에 있어야 하고, 원인마다 가리키는 기법이 대장에 있어야 한다.
대장은 trustkit이 아니라 앱 쪽 — 기법 본체는 trustkit에 있어도 기준값·근거·위치는 우리 앱의 것이라서(설계서 §9-4와 다름, 이식할 건 진단 엔진 쪽).
"""
from __future__ import annotations

import os
from typing import Optional

STAGES = ("검색", "근거 판정", "답 모양", "생성")

# where: "파일(backend 기준):이름" — 이름이 그 파일 글에 있어야 한다(테스트). 데이터로만 있는 기법은 None.
TECHNIQUES: list[dict] = [
    {"key": "glossary_match", "name": "용어집 글자 매칭", "stage": "검색",
     "does": "질문에 나온 용어·동의어만 골라 검색어에 붙인다(임베딩 근사 매칭은 뺌)",
     "values": {}, "evidence": [("2026-10-07", "v2.128 — 임베딩 매칭이 엉뚱한 용어를 붙이던 것을 글자 매칭으로")],
     "where": "agents/knowledge_rag/knowledge/retrieval.py:map_glossary_term"},
    {"key": "glossary_synonyms", "name": "v0 용어 동의어", "stage": "검색",
     "does": "정책서 용어 712개에 동의어 811건(Claude 생성 → 임베딩 유사도로 거름)",
     "values": {}, "evidence": [("2026-10-21", "판정 #3 통과 — 검색 나빠짐 0, 답 +0.9%p [-0.9, +2.7]", 3)],
     "where": None},
    {"key": "keyword_rescue", "name": "키워드 구제", "stage": "검색",
     "does": "벡터가 놓친 키워드 상위 정책을 아래 자리 몇 개에 끼운다(맨 앞 순서는 그대로)",
     "values": {"슬롯": ("POLICY_KEYWORD_RESCUE_SLOTS", "3")}, "evidence": [],
     "where": "service/policy/search.py:KEYWORD_RESCUE_SLOTS"},
    {"key": "policy_rrf", "name": "순위 합치기(RRF)", "stage": "검색",
     "does": "벡터·키워드 목록의 순위만으로 합친다(점수 단위가 달라도 됨, k=60)",
     "values": {}, "evidence": [("2026-09-18", "업계 표준(OpenSearch·Azure AI Search)")],
     "where": "service/policy/search.py:rrf_order"},
    {"key": "chunk_header", "name": "조각 머리글", "stage": "검색",
     "does": "임베딩할 글 앞에 정책명·분류 경로를 붙인다(맥락 없는 조각 방지)",
     "values": {}, "evidence": [], "where": "service/policy/service.py:contextual_header"},
    {"key": "policy_top_k", "name": "문맥 정책 수", "stage": "검색",
     "does": "AI에게 넘기는 정책 파라미터·서술 각 상위 n개",
     "values": {"n": (None, "10")}, "evidence": [("2026-10-21", "골든 v0 Recall@10 98.4%")],
     "where": "agents/knowledge_rag/agent.py:POLICY_CONTEXT_TOP_K"},
    {"key": "collection_suggest", "name": "다른 컬렉션에서 찾아보기(예정)", "stage": "검색",
     "does": "질문이 다른 컬렉션 것 같으면 버튼으로 제안 — 기본 검색 범위는 그대로(섞임 방지)",
     "values": {}, "evidence": [("2026-10-23", "사용자 요청 — 켜기 전에 측정, 제안·클릭을 원장에")], "where": None},
    {"key": "abstain_prefilter", "name": "근거 없음 즉시 거절", "stage": "근거 판정",
     "does": "지식·코드·분류 목록이 없고 정책 점수가 하한 미만이면 LLM을 부르지 않고 \"관련 지식을 찾지 못했습니다\"",
     "values": {"서술 하한": ("POLICY_ABSTAIN_MIN_SCORE", "꺼짐"), "파라미터 하한": ("POLICY_ABSTAIN_MIN_PARAM_RANK", "0.0")},
     "evidence": [("2026-10-06", "(0.50, 0.05) 골든 88 거짓 거절 0 · 답 없음 35/60 차단 — 배포 후 다시 재서 켜기로(기본 꺼짐)"),
                  ("2026-10-23", "골든 v0 리플레이 — 어느 하한에서도 고치는 문항 0, 0.60부터 맞던 답 깨짐 → 꺼 둠(10/26 결정)")],
     "where": "agents/knowledge_rag/agent.py:should_abstain"},
    {"key": "category_list", "name": "분류 목록 블록", "stage": "답 모양",
     "does": "목록 질문이면 고른 분류의 정책을 \"경로 > 정책명 — 요약\" 줄로 문맥에 넣는다",
     "values": {"분류 유사도 하한": (None, "0.58"), "최대 줄": (None, "40")}, "evidence": [],
     "where": "service/policy/category_list.py:find_category_list"},
    {"key": "clarify", "name": "되묻기", "stage": "답 모양",
     "does": "목록이 크면 \"어느 부분을 볼까요?\" — 선택지 최대 3 + 전부 보기(\"전부·모든\"이면 묻지 않음)",
     "values": {"묻는 기준(정책 수 초과)": ("CATEGORY_CLARIFY_MAX_ITEMS", "20"), "하위 분류 덮는 비율": (None, "0.8"), "선택지": (None, "3")},
     "evidence": [("2026-10-12", "v2.134 도입"), ("2026-10-23", "v2.167 \"목록\"은 \"전부\"가 아님 — 441문항 문맥·검색 차이 0")],
     "where": "service/policy/category_list.py:clarify_for"},
    {"key": "list_context_filter", "name": "목록 답 문맥 거르기", "stage": "답 모양",
     "does": "목록 답이면 목록 안 정책 + 그보다 순위 높은 정책만 남긴다(엉뚱한 정책 섞임 방지)",
     "values": {}, "evidence": [("2026-10-13", "v2.138 — 탐색형만 의미 있게 좋아짐")],
     "where": "agents/knowledge_rag/agent.py:list_scoped"},
    {"key": "list_line_format", "name": "목록 줄 형식 + 목록 질문 알아보기", "stage": "답 모양",
     "does": "목록 줄을 \"경로 > 정책명 — 요약\"으로, \"목록·리스트·정리해줘\"도 목록 질문으로",
     "values": {}, "evidence": [("2026-10-21", "판정 #4 통과 — 목록 30.6→52.8%, 전체 +0.3%p", 4)],
     "where": "service/policy/category_list.py:list_line_summary"},
    {"key": "param_derive", "name": "파라미터 파생(원문에서)", "stage": "검색",
     "does": "정책 원문에서 값(금액·개수·기간)을 뽑아 파라미터로 덧붙인다 — 원문을 대신하지 않고, 원문에 글자로 있는 값만",
     "values": {}, "evidence": [("2026-09-04", "파라미터만 남기면 34.8% vs 원문 통째 73.9% → 원문이 기준·파라미터는 파생(v0 §1)"),
                                ("2026-10-26", "적재 검수: 설계 #9 미실행 — 현행 파라미터 0건(파생 실험 중)")],
     "where": "service/policy/param_derive.py:validate_params"},
    {"key": "ref_table_chat", "name": "참고표·개요 채팅 연결(예정)", "stage": "검색",
     "does": "참고표(구간·조건 표)와 문서 개요를 채팅 문맥에 넣는다 — 적재는 돼 있음",
     "values": {}, "evidence": [("2026-10-26", "쓰임 검사: 정답이 참고표인 30문항·개요인 14문항 모두 문맥에 없음")], "where": None},
    {"key": "knowledge_dedupe", "name": "지식 중복 조각 정리", "stage": "검색",
     "does": "같은 내용 조각이 한 컬렉션에 둘 이상이면 하나만 남긴다(검색 자리 낭비·답 반복 방지)",
     "values": {}, "evidence": [("2026-10-26", "적재 검수: 제휴서비스 컨플루언스 적재분 4쌍")], "where": None},
    {"key": "ingest_contract", "name": "적재 계약(설계 완료 기준) 검사", "stage": "검색",
     "does": "설계서 완료 기준을 적재 계약으로 보고 DB가 지키는지 LLM 없이 검사 — 어긋나면 개선 후보",
     "values": {}, "evidence": [("2026-10-26", "첫 검사 18개 중 어긋남 4(파라미터·지식 중복·참고표·개요 쓰임)")],
     "where": "scripts/check_ingest.py:CHECKS"},
    {"key": "condition_hint", "name": "조건 질문 지시", "stage": "생성",
     "does": "\"3만 원 넘을 때만\"처럼 조건이 붙은 질문에 해당 내용을 찾아 답하라는 지시를 문맥 앞에",
     "values": {}, "evidence": [("2026-10-13", "\"없다\" 단정 지시는 오답 1→4라 뺌")],
     "where": "agents/knowledge_rag/agent.py:CONDITION_ANSWER_HINT"},
    {"key": "chat_prompt", "name": "채팅 답 지시(프롬프트)", "stage": "생성",
     "does": "답 생성 시스템 프롬프트 — 근거로만 답하기·형식·거절 문구",
     "values": {}, "evidence": [("2026-10-21", "판정 #2 통과 — 컨테이너명 지시 → 코드 블록, -1.0%p 오차 안", 2),
                                ("2026-10-23", "답 지시 3줄 후보 — 조건 질문 −8.7%p로 실패(판정 #10)", 10),
                                ("2026-10-23", "답 지시 2줄 후보 — 통과 +0.9%p 오차 안, 반영 검토 중(판정 #11)", 11)],
     "where": None},
]

BY_KEY = {t["key"]: t for t in TECHNIQUES}


def _setting(env: str) -> Optional[str]:
    """운영값 — 앱 설정(settings, .env 파일까지 읽음)에 같은 이름 칸이 있으면 그것, 없으면 환경 변수(모듈 상수가 읽는 값)."""
    try:
        from core.config import settings
        if hasattr(settings, env.lower()):
            v = getattr(settings, env.lower())
            return None if v is None else str(v)
    except ImportError:
        pass
    return os.environ.get(env)


def current_values(t: dict) -> dict[str, str]:
    """기준값 — 설정 키가 있으면 지금 서버의 운영값, 없으면 코드 기본값. 빈 값·None = 기본값(꺼짐 포함)."""
    out = {}
    for label, (env, default) in t["values"].items():
        v = _setting(env) if env else None
        out[label] = v if v not in (None, "") else default
    return out


def as_rows() -> list[dict]:
    """화면용 — 근거는 [{date, note}], 기준값은 운영값으로."""
    return [{**{k: t[k] for k in ("key", "name", "stage", "does", "where")},
             "values": [{"label": k, "value": v, "setting": t["values"][k][0]} for k, v in current_values(t).items()],
             "evidence": [{"date": e[0], "note": e[1], "gate_run_id": e[2] if len(e) > 2 else None} for e in t["evidence"]]}
            for t in TECHNIQUES]
