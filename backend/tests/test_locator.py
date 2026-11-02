"""등록 위치 판정(E) — 결정 규칙(decide)·LLM 응답 해석(parse_llm). 성질로: 확신이면 AI = 이웃 다수, 지어낸 이름은 받지 않음,
preset이면 위치를 바꾸지 않고 두 신호가 다른 곳으로 확신할 때만 제안."""
import random

import pytest

from agents.knowledge_rag.ingestion.locator import Candidate, decide, parse_llm

C = [Candidate(1, "딜리버스", "딜리버스", "domain"), Candidate(2, "POS", "POS", "domain"),
     Candidate(10, "org:team:1", "팀 공통 · A팀", "org_space", team_id=1, is_team_common=True, short="팀 공통"),
     Candidate(11, "org:part:5", "온라인 파트(A팀)", "org_space", team_id=1, short="온라인 파트"),
     Candidate(20, "org:team:2", "팀 공통 · B팀", "org_space", team_id=2, is_team_common=True, short="팀 공통"),
     Candidate(21, "org:part:9", "온라인 파트(B팀)", "org_space", team_id=2, short="온라인 파트")]


def nb(*ids):
    return [(i, 0.9 - n * 0.01) for n, i in enumerate(ids)]


def test_confident_only_when_both_agree():
    d = decide(C, 1, nb(1, 1, 1, 1, 2), preset_id=None, user_team_id=1, llm_fail="")
    assert d["confident"] and not d["ask"] and d["proposed_id"] == 1
    d = decide(C, 2, nb(1, 1, 1, 1, 2), preset_id=None, user_team_id=1, llm_fail="")
    # 이웃 4/5가 모이면 이웃을 미리 고른다(문서 단위 실측: 제안이 맞은 수 9→11/16) — 확신은 아님(AI와 다름)
    assert not d["confident"] and d["ask"] and d["proposed_id"] == 1
    assert d["options"] == [1, 2, 10]                      # 제안 · AI · 내 팀 공통
    d = decide(C, 2, nb(1, 1, 1, 2, 2), preset_id=None, user_team_id=1, llm_fail="")
    assert d["proposed_id"] == 2 and d["options"] == [2, 1, 10]   # 이웃 3/5뿐 → AI 먼저
    d = decide(C, 2, nb(1, 1, 1, 1, 2), preset_id=None, user_team_id=1, llm_fail="", ai_named=True)
    assert d["proposed_id"] == 2 and not d["confident"]           # 본문에 AI가 고른 업무 이름 → 이웃 4/5여도 AI 먼저(확신은 아님)


def test_names_ai_collection_only_business_names_in_text():
    from agents.knowledge_rag.ingestion.locator import names_ai_collection
    assert names_ai_collection(C, 1, "딜리버스 배차 화면 상태 코드")
    assert not names_ai_collection(C, 1, "배차 화면 상태 코드")
    assert not names_ai_collection(C, 10, "팀 공통 · A팀 규칙")     # 조직은 이름 대조 안 함
    assert not names_ai_collection(C, None, "딜리버스")
    short = C + [Candidate(3, "공통", "공통", "domain")]
    assert not names_ai_collection(short, 3, "공통 절차")             # 3글자 미만 이름은 신호 아님(거의 모든 글에 나옴)


def test_llm_fail_and_no_neighbors_are_visible():
    d = decide(C, None, nb(2, 2, 1), preset_id=None, user_team_id=None, llm_fail="error")
    assert d["proposed_id"] == 2 and d["ask"] and "AI 판단 실패" in d["fallback"]
    d = decide(C, None, nb(2, 2, 1), preset_id=None, user_team_id=None, llm_fail="refusal")
    assert "보안 필터" in d["fallback"]
    d = decide(C, 1, nb(1), preset_id=None, user_team_id=None, preset_missing=True)
    assert "넘어온 컬렉션을 찾지 못해" in d["fallback"]
    d = decide(C, 1, [], preset_id=None, user_team_id=None, llm_fail="")
    assert not d["confident"] and "비교할 지식이 없어요" in d["fallback"] and d["proposed_id"] == 1


def test_preset_keeps_location_and_suggests_only_when_confident_elsewhere():
    d = decide(C, 2, nb(2, 2, 2, 2, 1), preset_id=1, user_team_id=1, llm_fail="")
    assert d["proposed_id"] == 1 and d["suggest_move"] and not d["ask"] and d["options"] == []
    d = decide(C, 2, nb(2, 2, 1, 1, 1), preset_id=1, user_team_id=1, llm_fail="")   # 확신 아님 → 조용히 그대로
    assert d["proposed_id"] == 1 and not d["suggest_move"]
    d = decide(C, 1, nb(1, 1, 1, 1, 1), preset_id=1, user_team_id=1, llm_fail="")   # 같은 곳 → 제안 없음
    assert not d["suggest_move"]


def test_decide_properties_random():
    rnd = random.Random(3)
    ids = [c.id for c in C]
    for _ in range(3000):
        ai = rnd.choice(ids + [None])
        neigh = nb(*[rnd.choice(ids) for _ in range(rnd.randint(0, 7))])
        preset = rnd.choice(ids + [None, None])
        d = decide(C, ai, neigh, preset_id=preset, user_team_id=rnd.choice([1, 2, None]), llm_fail="" if ai is not None else "error")
        if d["confident"]:
            near = [i for i, _ in neigh][:5]
            assert len(near) == 5 and near.count(ai) >= 4
        assert d["ask"] == (preset is None and not d["confident"])
        if preset is not None:
            assert d["proposed_id"] == preset
        assert len(d["options"]) == len(set(d["options"])) <= 3
        assert all(o in ids for o in d["options"])
        if preset is None and d["proposed_id"] is not None:
            assert d["options"][0] == d["proposed_id"]     # 미리 고른 곳이 선택지 맨 앞


@pytest.mark.parametrize("raw,team,want", [
    ('{"kind":"업무","collection":"딜리버스","org":null,"doc_type":"지식 문서","reason":"r"}', 1, (1, "지식 문서")),
    ('<think>...</think>{"kind":"컬렉션","collection":"POS","doc_type":"정책서"}', 1, (2, "정책서")),   # v3 답 모양(11/02)
    ('{"kind":"애매","doc_type":"이상한 값"}', 1, (None, "지식 문서")),
    ('{"kind":"애매","doc_type":"대상 아님"}', 1, (None, "대상 아님")),   # IT 운영 지식 아님(11/02) — 소스 연결이 제외 제안
])
def test_parse_llm(raw, team, want):
    ai, dt, _ = parse_llm(raw, C, team)
    assert (ai, dt) == want


@pytest.mark.parametrize("raw,team", [
    ('{"kind":"업무","collection":"없는 업무","doc_type":"지식 문서"}', 1),   # 지어낸 이름 — 애매가 아니라 모양 오류
    ('{"kind":"업무","collection":null}', 1),
    ('{"kind":"조직","org":"팀 공통","doc_type":"지식 문서"}', 1),          # 조직 컬렉션 폐지(11/02) — 조직 답은 모양 오류
    ('{"kind":"컬렉션","collection":"org:team:1"}', 1),                    # 조직 키를 이름으로 대도 후보가 아님
])
def test_parse_llm_invented_name_is_shape_error(raw, team):
    from agents.knowledge_rag.ingestion.locator import ShapeError
    with pytest.raises(ShapeError):
        parse_llm(raw, C, team)


def test_neighbor_scope_is_business_plus_my_team_org():
    from agents.knowledge_rag.ingestion.locator import neighbor_scope
    assert neighbor_scope(C, 1) == [1, 2, 10, 11]
    assert neighbor_scope(C, None) == [1, 2]


def test_parse_llm_without_kind_is_failure():
    # 답 모양이 프롬프트와 어긋나면(예: 옛 v1 모양 location) 조용히 "애매"가 아니라 실패 → 화면 노란 줄(10/23 실측: v1 프롬프트가 남아 20/20 애매)
    with pytest.raises(ValueError):
        parse_llm('{"location":"딜리버스"}', C, 1)



def test_parse_llm_bad_json_raises():
    # 형식이 깨지면 예외 → judge_llm이 "AI 판단 실패"로 표시(조용히 기본값으로 넘기지 않음)
    with pytest.raises(Exception):
        parse_llm("not json", C)

