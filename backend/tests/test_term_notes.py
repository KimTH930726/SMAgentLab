"""팀 간 용어 참고(10/29) — 질문에 나온 용어가 다른 영역 용어집에 다른 뜻으로 있을 때만 답 아래 한 줄. 답·근거엔 안 넣는다.
불변식: 같은 용어(정규화) + 설명이 다를 때만 · 이 답에 쓴 뜻의 파트는 빼고 · 팀마다 한 번 · 최대 2개 + 나머지 개수 · 입력 용어 하나당 한 줄."""
from agents.knowledge_rag.knowledge import glossary_terms as gt
from agents.knowledge_rag.knowledge.glossary_terms import TermMatch


def _m(term, desc, part=""):
    return TermMatch(term=term, description=desc, matched=term, part=part)


def _r(term, desc, area):
    return {"term": term, "description": desc, "area": area}


def test_only_same_term_with_different_meaning():
    rows = [_r("정산", "배달 기사 수수료 지급", "딜리버스"),
            _r("정 산", "매월 말일 18시 마감", "B팀"),              # 같은 용어(공백 차이)·같은 뜻 → 안 붙음
            _r("정산서", "정산 내역 문서", "C팀"),                  # 다른 용어 → 안 붙음
            _r("정산", "", "D팀")]                                  # 설명 없음 → 안 붙음
    out = gt.other_meanings_from([_m("정산", "매월 말일 18시 마감")], rows)
    assert out == [{"term": "정산", "others": [{"area": "딜리버스", "description": "배달 기사 수수료 지급"}], "more": 0}]


def test_no_note_when_nothing_differs():
    assert gt.other_meanings_from([_m("정산", "마감 기준")], [_r("정산", "마감  기준", "A")]) == []
    assert gt.other_meanings_from([], [_r("정산", "x", "A")]) == []


def test_limit_two_and_count_rest_one_line_per_term():
    rows = [_r("클레임", f"뜻{i}", f"팀{i}") for i in range(4)] + [_r("클레임", "뜻9", "팀0")]   # 같은 팀 두 번 → 한 번만
    out = gt.other_meanings_from([_m("클레임", "고객 접수"), _m("클레임", "고객 접수")], rows)
    assert len(out) == 1 and [o["area"] for o in out[0]["others"]] == ["팀0", "팀1"] and out[0]["more"] == 2


def test_meaning_from_policy_part_excludes_that_part():
    """이 답에 쓴 뜻이 다른 파트(정책서 범위) 용어면 그 파트는 '다른 팀'이 아니다."""
    rows = [_r("포인트", "적립 포인트", "멤버십"), _r("포인트", "매장 좌표", "지도팀")]
    out = gt.other_meanings_from([_m("포인트", "적립 포인트 기준", part="멤버십")], rows)
    assert [o["area"] for o in out[0]["others"]] == ["지도팀"]


def test_long_meaning_is_marked_cut():
    out = gt.other_meanings_from([_m("정산", "짧은 뜻")], [_r("정산", "가" * 200, "A")])
    d = out[0]["others"][0]["description"]
    assert d.endswith("…") and len(d) == gt.TERM_NOTE_DESC_MAX + 1


def test_term_notes_never_break_the_answer(monkeypatch):
    """실패·지연이면 참고 없이(답엔 영향 없음, 로그만) — stream_chat은 이 값만 meta·SSE에 붙인다."""
    import asyncio
    from agents.knowledge_rag import agent as ag

    class _Boom:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(ag, "get_conn", lambda: _Boom())
    assert asyncio.run(ag._term_notes("영역", [_m("정산", "x")])) == []

    class _Slow:
        async def __aenter__(self):
            await asyncio.sleep(5)

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(ag, "get_conn", lambda: _Slow())
    monkeypatch.setattr(ag, "TERM_NOTES_TIMEOUT", 0.05)
    assert asyncio.run(ag._term_notes("영역", [_m("정산", "x")])) == []
    assert asyncio.run(ag._term_notes("영역", [])) == []   # 용어가 없으면 DB도 안 부름


def test_only_business_areas_are_read():
    """내부 자리(팀 적재 보관 등 — 이름이 내부 키)·시험 영역은 '다른 팀'으로 안 보인다(리뷰 M1): SQL이 kind domain만."""
    import asyncio
    seen = {}

    class _Conn:
        async def fetch(self, sql, *args):
            seen["sql"], seen["args"] = sql, args
            return []

    asyncio.run(gt.other_meanings(_Conn(), [_m("정산", "x")], [1, 2]))
    assert "n.kind = 'domain'" in seen["sql"] and seen["args"] == ([1, 2],)

