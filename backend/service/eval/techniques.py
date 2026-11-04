"""답 기법 대장 (2026-10-23, eval-gate-redesign §9-4) — 검색·답을 다듬는 기법의 기준을 한 곳에(코드 상수에만 숨어 까먹지 않게).

항목 = 이름 · 단계 · 하는 일 · 기준값(설정 키가 있으면 운영값을 읽음) · 도입·보류 근거(날짜·숫자) · 코드 위치(파일:이름).
자가 개선 루프(scripts/eval_improve.py)가 "틀린 원인 → 볼 기법"을 이 키로 가리키고, 품질 › 답 기법 화면이 읽는다.
테스트(tests/test_eval_techniques.py): 코드 위치의 이름이 실제 그 파일에 있어야 하고, 원인마다 가리키는 기법이 대장에 있어야 한다.
대장은 trustkit이 아니라 앱 쪽 — 기법 본체는 trustkit에 있어도 기준값·근거·위치는 우리 앱의 것이라서(설계서 §9-4와 다름, 이식할 건 진단 엔진 쪽).
"""
from __future__ import annotations

import os
from typing import Optional

STAGES = ("적재", "검색", "근거 판정", "답 모양", "생성")

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
    {"key": "parent_expansion", "name": "상위 섹션 보충", "stage": "검색",
     "does": "채택된 지식 조각의 같은 상위 섹션 이웃 조각을 문맥 뒤에 붙인다(조각 하나당 최대 6000자)",
     "values": {"조각당 최대 글자": (None, "6000")},
     "evidence": [("2026-10-30", "업무지식 106문항 문맥: 지식 2.9조각 + 보충 29.9 + 정책 서술 10 = 평균 1.8만 자 — 틀린 14건 지어냄 7·섞음 6")],
     "where": "agents/knowledge_rag/knowledge/retrieval.py:def expand_parent_sections"},
    {"key": "collection_suggest", "name": "다른 컬렉션에서 찾아보기", "stage": "검색",
     "does": "답 뒤 다른 업무 컬렉션에서 같은 검색 — 더 맞는 지식이 보이면 버튼으로만 제안(답은 안 섞음). 2초 넘으면 생략",
     "values": {"하한": (None, "0.48"), "켜기": (None, "설정 chat.collection_suggest(기본 꺼짐)")},
     "evidence": [("2026-10-23", "합성: 하한 0.45 거짓 제안 0/106 · 맞게 92%"),
                  ("2026-10-26", "실사용 174문항: 0.45면 맞음 8·틀림 2 → 0.48로 올려 맞음 8·틀림 0(합성 84%)"),
                  ("2026-10-27", "부하 300명 가정(동시 60): 다른 검색 지연 늘지 않음, 제안 최대 5초 → 2초 상한")],
     "where": "service/chat/collection_suggest.py:find",
     "example": {"where": "채팅", "lines": [["사용자", "배송 지연되면 보상 기준이 뭐야?"], ["답", "(답 본문) … 다른 컬렉션에 더 맞는 지식이 있어요"]],
                 "chips": ["배송 컬렉션에서 보기"]},
     "rules": ["답한 뒤 다른 업무 컬렉션에서 같은 질문으로 한 번 더 찾는다",
               "점수 {하한} 이상인 지식이 있으면 버튼으로만 제안 — 답에는 안 섞는다",
               "2초 넘게 걸리면 제안을 건너뛴다"]},
    {"key": "knowledge_location", "name": "지식 등록 위치 판정", "stage": "적재",
     "does": "새 지식이 어느 컬렉션인지 — LLM \"바꿔 넣기 시험\" + 가장 가까운 지식 5건의 컬렉션 다수결, 둘이 맞으면 확신(자동 후보), 아니면 사람에게 물음",
     "values": {"이웃": (None, "5건 중 4건")},
     "evidence": [("2026-10-22", "실측 73건: AI만 자동 71 중 틀림 15 / AI 일치 + 이웃 5중 4 → 자동 51 중 틀림 0")],
     "where": "agents/knowledge_rag/ingestion/locator.py:def decide",
     "example": {"where": "지식 등록", "lines": [["새 지식", "주문 취소 후 환불 처리 절차"], ["AI", "넣을 곳: 주문 컬렉션 — 확신(AI·이웃 일치)"]],
                 "chips": ["이대로", "다른 곳…"]},
     "rules": ["AI가 글을 읽고 고른 곳과, 가장 비슷한 기존 지식 5건이 많이 있는 곳을 비교한다",
               "둘이 같고 이웃 {이웃}이 그곳이면 확신 — 자동으로 후보에 둔다",
               "다르면 사람에게 묻는다(사람이 바꾸면 번복으로 기록)"]},
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
     "where": "service/policy/category_list.py:clarify_for",
     "example": {"where": "채팅", "lines": [["사용자", "쿠폰 정책 알려줘"], ["답", "어느 부분을 볼까요?"]],
                 "chips": ["발급 조건", "사용 제한", "환불", "전부 보기"]},
     "rules": ["목록이 정책 {묻는 기준(정책 수 초과)}개를 넘으면 되묻는다",
               "선택지는 최대 {선택지}개 + [전부 보기]",
               "질문에 \"전부·모든\"이 있으면 묻지 않고 다 보여 준다"]},
    {"key": "ref_clarify", "name": "참고표 되묻기", "stage": "답 모양",
     "does": "붙은 참고표의 결과를 정하는 조건(결정 열) 값이 질문에 하나도 없으면 그 열을 되묻는다 — 선택지 = 그 열 값, 고르면 \"질문 (열: 값)\"으로 다시. 표가 정책 서술보다 강할 때만, 목록 답이면 안 물음",
     "values": {"선택지 수": (None, "2~6개(넘으면 안 묻고 표로 답)"), "묻는 조건": (None, "표 점수 ≥ 정책 서술 최고점")},
     "evidence": [("2026-10-26", "붙이는 규칙 측정: 되묻기 여유 0 → 참고표 15/30 · 정책 질문에 엉뚱하게 1/455"),
                  ("2026-10-27", "Claude 채점: 결정 조건 빠진 질문 되묻기 73~83%")],
     "where": "service/policy/ref_search.py:def clarify_for_table",
     "example": {"where": "채팅", "lines": [["사용자", "적립률이 얼마야?"], ["답", "어느 등급 기준으로 볼까요?"]],
                 "chips": ["일반", "골드", "VIP"]},
     "rules": ["참고표의 답을 정하는 조건(결정 열) 값이 질문에 없으면 그 열을 되묻는다",
               "선택지 = 그 열의 값 {선택지 수}",
               "표가 정책 서술보다 질문에 더 맞을 때만 — 목록 답이면 안 묻는다"]},
    {"key": "source_check", "name": "원문 확인 검사", "stage": "적재",
     "does": "정책서 원문 자체의 문제(정책끼리 어긋남·참고표 오류·담당자 질문)를 적재 점검·골든셋 작성 때 짚어 원문 확인 카드로 — 사람이 해결 또는 [문제 없음](사유)",
     "values": {"짚는 곳": (None, "적재 점검 · 골든셋 작성"), "문제 없음 사유": (None, "원문이 맞음 · 이미 고쳐짐 · 중복 · 기타")},
     "evidence": [("2026-10-21", "v0 적재 점검 담당자 질문 4 · 골든셋 작성 어긋남 쌍"), ("2026-10-26", "C3 1단계 — 결론을 AI 판단 원장에(오탐률)")],
     "where": "service/policy/source_check.py:def decide_check",
     "example": {"where": "정책서 원문 확인", "lines": [["AI", "정책 A와 정책 B의 적립 한도가 서로 달라요"]],
                 "chips": ["해결", "문제 없음…"]},
     "rules": ["적재 점검·골든셋 작성 때 정책서 원문 자체의 문제를 짚는다",
               "정책끼리 어긋남 · 참고표 오류 · 담당자에게 물을 것",
               "사람이 [해결] 또는 [문제 없음](사유: {문제 없음 사유})"]},
    {"key": "improve_recommend", "name": "개선 후보 AI 추천", "stage": "생성",
     "does": "틀린 문항의 원인마다 고칠 후보를 만들고, LLM 없이 다시 계산(리플레이)해 고치는 문항이 있고 망가지는 문항이 0이면 \"해 볼 만함\", 고침 0·망가짐 있으면 \"안 하는 게 좋음\", 재 봐야 알면 \"재 봐야 앎\"",
     "values": {"해 볼 만함": (None, "고침 > 0 · 망가짐 0"), "안 하는 게 좋음": (None, "고침 0 또는 망가짐 > 0")},
     "evidence": [("2026-10-23", "근거 없음 하한: 어느 하한도 망가짐 없이 고치는 문항 0 → 안 하는 게 좋음(사람도 기각)")],
     "where": "service/eval/improve.py:def abstain_replay",
     "example": {"where": "평가 게이트", "lines": [["후보", "근거 없음 하한 0.5로 올리기"], ["AI 추천", "안 하는 게 좋음 — 고침 0 · 망가짐 3"]],
                 "chips": ["실험해 보기", "안 하기…"]},
     "rules": ["후보마다 LLM 없이 다시 계산(리플레이)해 고치는 문항과 망가지는 문항을 센다",
               "고침 있고 망가짐 0이면 \"해 볼 만함\", 고침 0이거나 망가짐 있으면 \"안 하는 게 좋음\"",
               "다시 계산으로 모르면 \"재 봐야 앎\" — 정하는 건 사람"]},
    {"key": "list_context_filter", "name": "목록 답 문맥 거르기", "stage": "답 모양",
     "does": "목록 답이면 목록 안 정책 + 그보다 순위 높은 정책만 남긴다(엉뚱한 정책 섞임 방지)",
     "values": {}, "evidence": [("2026-10-13", "v2.138 — 탐색형만 의미 있게 좋아짐")],
     "where": "agents/knowledge_rag/agent.py:list_scoped"},
    {"key": "list_line_format", "name": "목록 줄 형식 + 목록 질문 알아보기", "stage": "답 모양",
     "does": "목록 줄을 \"경로 > 정책명 — 요약\"으로, \"목록·리스트·정리해줘\"도 목록 질문으로",
     "values": {}, "evidence": [("2026-10-21", "판정 #4 통과 — 목록 30.6→52.8%, 전체 +0.3%p", 4)],
     "where": "service/policy/category_list.py:list_line_summary"},
    {"key": "param_derive", "name": "파라미터 파생(원문에서)", "stage": "적재",
     "does": "정책 원문에서 값(금액·개수·기간)을 뽑아 파라미터로 덧붙인다 — 원문을 대신하지 않고, 원문에 글자로 있는 값만",
     "values": {"적재": (None, "안 함(실험만 — 사람 문항 효과 없음)")}, "evidence": [("2026-09-04", "파라미터만 남기면 34.8% vs 원문 통째 73.9% → 원문이 기준·파라미터는 파생(v0 §1)"),
                                ("2026-10-26", "적재 검수: 설계 #9 미실행 — 현행 파라미터 0건"),
                                ("2026-10-26", "사본 DB 실험(899값): 카드 10장 사람 문항 ±0 · 답 없음 +5.8%p / 카드 3장 사람 −5.0%p·목록 −8.3%p → 채택 안 함", 18),
                                ("2026-10-26", "카드 3장 판정", 19)],
     "where": "service/policy/param_derive.py:validate_params"},
    {"key": "ref_table_chat", "name": "참고표·개요 채팅 연결", "stage": "검색",
     "does": "표·개요 요약 벡터로 찾아 표 압축본·개요 본문을 문맥에, 결정 조건이 빠지면 되묻기(정책 서술보다 강할 때만)",
     "values": {"채팅 연결": ("POLICY_REF_CHAT", "꺼짐"), "개요도": ("POLICY_REF_OVERVIEW", "꺼짐")},
     "evidence": [("2026-10-26", "쓰임 검사: 정답이 참고표인 30문항·개요인 14문항 모두 문맥에 없음"),
                  ("2026-10-26", "붙이는 규칙 측정(검색만): 표 23/30·엉뚱하게 7/455, 개요 12/14·0/471 — 유사도만으론 0.6에서도 42/455"),
                  ("2026-10-27", "Claude 채점: 표만+지시 한 줄 — 참고표 80% · 되묻기 73% · 기존 −0.5%p, 답 없음 −2문항 그대로 → 켜지 않음(개요는 따로 꺼짐)")],
     "where": "service/policy/ref_search.py:search_refs"},
    {"key": "knowledge_dedupe", "name": "지식 중복 조각 정리", "stage": "적재",
     "does": "같은 내용 조각이 한 컬렉션에 둘 이상이면 하나만 남긴다(검색 자리 낭비·답 반복 방지)",
     "values": {}, "evidence": [("2026-10-26", "적재 검수: 제휴서비스 컨플루언스 적재분 4쌍")], "where": None},
    {"key": "duplicate_review", "name": "등록 중복 의심 → 승인 대기", "stage": "적재",
     "does": "새 조각이 기존 활성 지식(또는 같은 묶음의 다른 조각)과 유사도 하한 이상이면 검색에 안 쓰고 승인 대기로 — 사람이 그대로 저장·덮어쓰기·반려",
     "values": {"유사도 하한": ("DUPLICATE_MIN_SIMILARITY", "0.88"), "글자 하한": ("DUPLICATE_MIN_TEXT_SIMILARITY", "0.8")},
     "evidence": [("2026-11-03", "홈페이지 공간 적재: 승인 대기 457건 전부 중복 의심 — 같은 페이지 58·다른 페이지 399, 0.98 이상 89(소제목 한 줄 조각이 많음 → 청크 구조화 설계)"),
                  ("2026-11-04", "중복 판정 2단: 457건 중 양식 줄을 뺀 글자 0.8 이상 178(완전 일치 66 포함) — 나머지는 같은 양식의 다른 페이지")],
     "where": "agents/knowledge_rag/knowledge/service.py:dup_threshold",
     "rules": ["새 조각마다 가장 비슷한 기존 활성 지식을 찾는다(같은 묶음 안 조각끼리도)",
               "유사도 {유사도 하한} 이상인 후보 중, 그 컬렉션 양식 줄(여러 페이지에 똑같이 나오는 줄 — 3쪽·1% 이상, 데이터에서 셈)을 뺀 "
               "글자 유사도도 {글자 하한} 이상일 때만 승인 대기 — 의미만 비슷하면 정상 등록",
               "사람이 그대로 저장하면 '중복 아님' = 번복으로 기록(AI 감사 로그 › 중복 의심)"]},
    {"key": "exact_reject", "name": "완전 일치 등록 거부", "stage": "적재",
     "does": "공백·줄바꿈만 다르고 글이 똑같은 지식이 이미 있으면 저장하지 않는다 — 등록 이력에 건너뜀 수·기존 id, 단건 등록은 오류로 알림",
     "values": {}, "evidence": [("2026-11-04", "홈페이지 승인 대기 457건 중 66건이 같은 글(화면 '100%'는 뜻 점수 반올림이라 같은 글이 아닐 수 있음)")],
     "where": "agents/knowledge_rag/knowledge/service.py:bulk_create_knowledge"},
    {"key": "heading_merge", "name": "제목만 조각 합치기", "stage": "적재",
     "does": "본문 줄 없이 제목(# 또는 번호 제목 줄)만 있는 조각을 같은 문서의 다음 조각 앞(마지막이면 앞 조각 뒤)에 붙인다 — 섹션마다 나누는 모드만",
     "values": {}, "evidence": [("2026-11-04", "다시 나누면 홈페이지 제목만 조각 20 → 7 · 딜리버스 원문 113 → 0(합치기 전 기준) — 짧아도 문장이 있으면 그대로 둠")],
     "where": "agents/knowledge_rag/ingestion/chunker.py:_merge_heading_only"},
    {"key": "ingest_contract", "name": "적재 계약(설계 완료 기준) 검사", "stage": "적재",
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
    {"key": "grounding_check", "name": "답 뒤 근거 확인", "stage": "생성",
     "does": "답을 만든 뒤 같은 모델이 주장마다 문맥 근거를 확인 — 없는 주장은 빼고 핵심이 없으면 '못 찾음'(질문마다 LLM 1회 추가)",
     "values": {}, "evidence": [("2026-10-30", "착수 — 업무지식 단정 오답 17.9%, 답 단계 오답 14건 중 지어냄 7·섞음 6. 지시문·문맥은 답 생성과 같은 통로로"
                                               "(한 덩어리로 보내면 게이트웨이 거부 2/5)")],
     "where": "libs/trustkit/trustkit/answer/grounding.py:def apply_verdict"},
]

TECHNIQUES += [
    {"key": "linked_scope", "name": "관련 정책 연결 검색", "stage": "검색",
     "does": "고른 컬렉션 + 관련 정책으로 연결된 시스템·정책까지 지식을 찾는다(채팅 기본)",
     "values": {"범위": (None, "linked")},
     "evidence": [("2026-11-02", "운영 지식 시스템 이동과 한 묶음 — #71 업무지식 73.6→77.4% · #75 정책서 84.9→86.0%, 끄면 정책에서 물은 운영 지식 0%")],
     "where": "agents/knowledge_rag/agent.py:CHAT_KNOWLEDGE_SCOPE"},
    {"key": "context_budget", "name": "문맥 예산", "stage": "검색",
     "does": "정책·지식 후보를 한 줄 순위로 세워 위에서부터 예산까지만 AI에게 넘긴다",
     "values": {"예산": (None, "1만 2천 자")},
     "evidence": [("2026-11-02", "#68 업무지식 73.6→76.4% · #70 정책서 84.9→85.8%, 단정 오답 26.4→20.3%")],
     "where": "agents/knowledge_rag/agent.py:CHAT_CONTEXT_BUDGET"},
    {"key": "url_guard", "name": "허용 호스트 검사", "stage": "적재",
     "does": "URL·컨플 가져오기는 허용 호스트만 — 리다이렉트마다 다시 보고 응답은 20MB까지",
     "values": {"허용": ("URL_IMPORT_ALLOWED_HOSTS", "confl.sinc.co.kr")},
     "evidence": [("2026-11-03", "운영 전 보안 리뷰 #3 — 사내 내부 주소를 서버가 대신 읽던 것(SSRF)·PAT 유출 차단")],
     "where": "agents/knowledge_rag/ingestion/web_crawler.py:def guarded_client"},
    {"key": "batch_publish", "name": "검수 후 게시", "stage": "적재",
     "does": "팀 적재 배치는 검수 대기로 두고 [게시] 뒤에만 채팅에 — 게시 전엔 같은 페이지 옛 버전이 그대로",
     "values": {}, "evidence": [("2026-11-03", "결정 (가) — 적재 즉시 답에 섞이던 것")],
     "where": "agents/knowledge_rag/ingestion/team_import.py:def publish_batch"},
]

# 흐름도(11/03 태훈 "흐름도상 어떤 단계에서 쓰이는지 + 필수는 필수, 끄고 켤 수 있는 건 끄고 켜게") — 기법마다 (흐름, 단계, 모드, 미적용 사유)
# 모드: required = 필수(끄면 기본 동작이 깨짐) · on = 적용 중(코드에 고정, 스위치 없음) · off = 미적용(사유) · switch = 실행 중 켜고 끔(core/technique_flags)
FLOWS = {"chat": ("질문 이해", "검색", "근거 판정", "문맥 구성", "답 모양", "생성", "답 뒤"),
         "ingest": ("수집", "넣을 곳", "분할·저장", "점검", "게시"),
         "eval": ("측정", "원인·개선")}
FLOW_OF: dict[str, tuple[str, str, str, Optional[str]]] = {
    "glossary_match": ("chat", "질문 이해", "required", None),
    "glossary_synonyms": ("chat", "질문 이해", "on", None),
    "linked_scope": ("chat", "검색", "switch", None),
    "policy_rrf": ("chat", "검색", "required", None),
    "keyword_rescue": ("chat", "검색", "on", None),
    "ref_table_chat": ("chat", "검색", "switch", None),
    "abstain_prefilter": ("chat", "근거 판정", "off", "서술 하한이 비어 있어 안 씀 — 하한 재보정(게이트) 뒤 값으로 켠다"),
    "context_budget": ("chat", "문맥 구성", "switch", None),
    "policy_top_k": ("chat", "문맥 구성", "required", None),
    "parent_expansion": ("chat", "문맥 구성", "on", None),
    "list_context_filter": ("chat", "문맥 구성", "on", None),
    "category_list": ("chat", "답 모양", "on", None),
    "list_line_format": ("chat", "답 모양", "required", None),
    "clarify": ("chat", "답 모양", "on", None),
    "ref_clarify": ("chat", "답 모양", "on", None),
    "condition_hint": ("chat", "답 모양", "on", None),
    "chat_prompt": ("chat", "생성", "required", None),
    "grounding_check": ("chat", "답 뒤", "off", "실험만 — 10/30 #11에서 단정 오답 17.2→21.9%로 나빠져 채팅에 연결하지 않음"),
    "collection_suggest": ("chat", "답 뒤", "switch", None),
    "url_guard": ("ingest", "수집", "required", None),
    "knowledge_location": ("ingest", "넣을 곳", "required", None),
    "chunk_header": ("ingest", "분할·저장", "required", None),
    "knowledge_dedupe": ("ingest", "분할·저장", "required", None),
    "duplicate_review": ("ingest", "분할·저장", "required", None),
    "exact_reject": ("ingest", "분할·저장", "required", None),
    "heading_merge": ("ingest", "분할·저장", "required", None),
    "param_derive": ("ingest", "분할·저장", "off", "실험만 — 사람 문항에 효과 없음(적재에 안 씀)"),
    "ingest_contract": ("ingest", "점검", "required", None),
    "source_check": ("ingest", "점검", "required", None),
    "batch_publish": ("ingest", "게시", "required", None),
    "improve_recommend": ("eval", "원인·개선", "on", None),
}

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
        v = {"True": "켜짐", "False": "꺼짐"}.get(v, v)   # 켜기/끄기 설정은 사람 말로
        out[label] = v if v not in (None, "") else default
    return out


def rules_of(t: dict, values: dict[str, str]) -> list[str]:
    """규칙 문장의 {기준값 이름}을 지금 값으로 — 값을 바꾸면 설명도 같이 바뀐다(문장에 숫자를 따로 적어 두면 어긋남)."""
    out = []
    for r in t.get("rules", []):
        for k, v in values.items():
            r = r.replace("{" + k + "}", v)
        out.append(r)
    return out


def as_rows() -> list[dict]:
    """화면용 — 근거는 [{date, note}], 기준값은 운영값으로, 설명(예시·규칙)은 있을 때만."""
    rows = []
    for t in TECHNIQUES:
        vals = current_values(t)
        rows.append({**{k: t[k] for k in ("key", "name", "stage", "does", "where")},
                     "values": [{"label": k, "value": v, "setting": t["values"][k][0]} for k, v in vals.items()],
                     "evidence": [{"date": e[0], "note": e[1], "gate_run_id": e[2] if len(e) > 2 else None} for e in t["evidence"]],
                     "example": t.get("example"), "rules": rules_of(t, vals), **flow_view(t["key"])})
    return rows


def flow_view(key: str) -> dict:
    """흐름도 칸 — 흐름·단계·상태(required/on/off)·스위치 여부·미적용 사유·마지막 변경. 스위치는 지금 값(core/technique_flags)."""
    from core import technique_flags
    flow, step, mode, why = FLOW_OF.get(key, ("chat", "검색", "on", None))
    if mode == "switch":
        state = "on" if technique_flags.is_on(key) else "off"
    else:
        state = {"required": "required", "off": "off"}.get(mode, "on")
    sw = mode == "switch"
    return {"flow": flow, "step": step, "state": state, "switchable": sw, "off_reason": why,
            "changed": technique_flags.changed(key) if sw else None,
            # 검증된 최적 값(켜짐/꺼짐)과 벗어나면 잃는 것 — 화면이 "최적 상태" 표시·경고에 쓴다
            "baseline_on": technique_flags.BASELINE.get(key) if sw else None,
            "baseline_warn": technique_flags.BASELINE_WARN.get(key) if sw else None}
