"""용어집 활용 재설계 (2026-10-06, v2.128) — 질문에 실제로 나온 용어만, 동의어는 LLM 자동 + 품질 게이트.

예전 방식(질문 임베딩 ↔ 용어 설명 최근접 1개)은 골든 88문항 중 79건에 용어를 붙였는데 질문에 실제로 있던 건 6건뿐이고, 무관 질문
60건 중 21건에도 붙어("오늘 날씨" → "출고송신진행") 검색어를 오염시켰다. 여기서 고정하는 것:
- 글자 그대로(띄어쓰기·대소문자 무시) 나온 용어만, 긴 것 우선, 겹치는 짧은 용어 제외, 못 찾으면 아무것도 안 붙임
- 키워드 확장은 질문에 *표기 그대로* 없는 것만("상품쿠폰" 질문엔 "상품 쿠폰" 표기를 붙여야 문서 토큰과 맞는다 — 실측 1문항)
- 설명 블록은 근거 해석용이라고 명시
- LLM 동의어는 정리·품질 게이트(임베딩 유사도) 통과분만, 질문 기록 표현은 질문에 실제로 있고 용어집에 있는 것만
"""
import pytest

from agents.knowledge_rag.knowledge import glossary_terms as gt

E = gt.GlossaryEntry


def _entries():
    return [
        E("상품 쿠폰", "상품에 적용되는 쿠폰", ["상품쿠폰할인"]),
        E("기초재고", "기간 시작 시점 재고"),
        E("재고", "보유 수량"),
        E("배송비", "배송 요금", ["택배비"]),
        E("주", "한 글자 용어"),
        E("APPUSER_SELECT_ROLE", "앱 사용자 권한 조회"),
    ]


class TestFindTerms:
    def test_spacing_and_case_insensitive(self):
        m = gt.find_terms("상품쿠폰에만 따로 적용되는 규정 있어?", _entries())
        assert [x.term for x in m] == ["상품 쿠폰"] and m[0].matched == "상품 쿠폰"

    def test_synonym_hit_maps_to_term(self):
        m = gt.find_terms("반품 택배비는 누가 내?", _entries())
        assert [(x.term, x.matched) for x in m] == [("배송비", "택배비")]

    def test_longest_wins_and_contained_short_term_dropped(self):
        # "기초재고"가 잡히면 그 안의 "재고"는 따로 안 쓴다
        assert [x.term for x in gt.find_terms("기초재고는 어떻게 계산돼?", _entries())] == ["기초재고"]

    def test_separate_occurrences_both_kept(self):
        assert sorted(x.term for x in gt.find_terms("기초재고와 배송비", _entries())) == ["기초재고", "배송비"]

    def test_unrelated_question_gets_nothing(self):
        assert gt.find_terms("오늘 날씨 어때?", _entries()) == []

    def test_one_char_terms_ignored_and_code_terms_matched(self):
        assert gt.find_terms("주말에 뭐해", _entries()) == []
        assert [x.term for x in gt.find_terms("appuser select role 권한", _entries())] == ["APPUSER_SELECT_ROLE"]

    def test_limit(self):
        many = [E(f"용어{i}", "") for i in range(10)]
        assert len(gt.find_terms(" ".join(f"용어{i}" for i in range(10)), many, limit=3)) == 3


class TestExpansionAndDefinitions:
    def test_adds_canonical_spelling_when_question_spelled_differently(self):
        m = gt.find_terms("상품쿠폰 규정", _entries())
        out = gt.expansion_text("상품쿠폰 규정", m)
        assert out.startswith("상품쿠폰 규정 ") and "상품 쿠폰" in out and "상품쿠폰할인" in out

    def test_does_not_repeat_exact_text_already_in_question(self):
        m = gt.find_terms("배송비 얼마야", _entries())
        assert gt.expansion_text("배송비 얼마야", m) == "배송비 얼마야 택배비"

    def test_no_match_returns_question_unchanged(self):
        assert gt.expansion_text("오늘 날씨", []) == "오늘 날씨"

    def test_definitions_block_marks_purpose(self):
        block = gt.definitions_block(gt.find_terms("기초재고", _entries()))
        assert block.startswith("[용어 설명") and "- 기초재고: 기간 시작 시점 재고" in block
        assert gt.definitions_block([]) == ""


def test_clean_synonyms():
    raw = ["결제 성공", "결제완료", " 결제성공 ", "1", "a" * 40, 123, "결제 OK", "결제확정", "x1", "y2", "z3"]
    assert gt.clean_synonyms("결제완료", raw) == ["결제 성공", "결제 OK", "결제확정", "x1", "y2", "z3"]
    assert gt.clean_synonyms("t", "not a list") == []


def _fake_embed(table):
    """표현 → 2차원 단위벡터(유사도를 직접 지정) — 실제 모델 없이 게이트 동작만 검증."""
    import math

    async def embed(s):
        ang = table.get(s, 0.0)
        return [math.cos(ang), math.sin(ang)]
    return embed


class _LLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    async def generate_once(self, prompt, system=""):
        self.calls.append(prompt)
        return self.reply


@pytest.mark.asyncio
async def test_gate_thresholds_differ_by_source():
    import math
    sim = lambda c: math.acos(c)   # 코사인 c가 되도록 각도 지정(용어는 각도 0)
    embed = _fake_embed({"가깝다": sim(0.80), "같다": sim(0.95), "멀다": sim(0.50)})
    pairs = [("가깝다", "용어"), ("같다", "용어"), ("멀다", "용어")]
    assert await gt.gate_synonyms(pairs, "llm_term", embed) == [True, True, False]    # 0.65 기준
    assert await gt.gate_synonyms(pairs, "llm_query", embed) == [False, True, False]  # 0.85 기준(환불→반품확정 0.80 차단)


@pytest.mark.asyncio
async def test_generate_term_synonyms_parses_cleans_and_gates():
    import math
    llm = _LLM('```json\n[{"term": "결제완료", "synonyms": ["결제 성공", "정산진행", "결제완료"]},'
               ' {"term": "모르는용어", "synonyms": ["x"]}]\n```')
    embed = _fake_embed({"결제 성공": math.acos(0.89), "정산진행": math.acos(0.51)})
    out = await gt.generate_term_synonyms([("결제완료", "결제가 끝남")], llm, embed=embed)
    assert out == {"결제완료": ["결제 성공"]}          # 자기 자신·배치 밖 용어·게이트 탈락 제거


@pytest.mark.asyncio
async def test_generate_term_synonyms_survives_llm_failure():
    class Boom:
        async def generate_once(self, *a, **k):
            raise RuntimeError("gateway down")
    assert await gt.generate_term_synonyms([("t", "d")], Boom(), embed=_fake_embed({})) == {}


@pytest.mark.asyncio
async def test_mine_query_expressions_validates_against_question_and_glossary():
    import math
    entries = [E("상품 쿠폰", "쿠폰", ["상품쿠폰할인"]), E("반품확정", "반품 완료")]
    questions = ["상품쿠폰 규정 알려줘", "환불은 언제 돼?"]
    llm = _LLM('[{"q": 1, "expression": "상품쿠폰", "term": "상품 쿠폰"},'        # 띄어쓰기 변형 = 용어 이름과 같음(정규화) → 제외
               ' {"q": 1, "expression": "쿠폰 규정", "term": "상품 쿠폰"},'       # 질문에 있음 → 후보
               ' {"q": 2, "expression": "환불", "term": "반품확정"},'             # 질문에 있으나 게이트에서 차단
               ' {"q": 2, "expression": "반품 처리", "term": "반품확정"},'        # 질문에 없음(LLM이 지어냄) → 제외
               ' {"q": 9, "expression": "x", "term": "상품 쿠폰"},'               # 없는 질문 번호
               ' {"q": 1, "expression": "규정", "term": "없는용어"}]')
    embed = _fake_embed({"쿠폰 규정": math.acos(0.90), "환불": math.acos(0.80), "상품 쿠폰": 0.0, "반품확정": 0.0})
    found = await gt.mine_query_expressions(questions, entries, llm, embed=embed)
    assert found == [("쿠폰 규정", "상품 쿠폰", 0)]



class TestFindTermsBoundaries:
    """코드 리뷰 2026-10-07 재현 사례 — 짧은 영문·띄어쓰기 건너뛰기·여러 위치."""
    ENTRIES = [E("PG", "결제대행"), E("PO", "발주"), E("POS", "판매시점"), E("고객", "구매자"),
               E("기초재고", "시작 재고"), E("재고", "보유 수량"), E("상품 쿠폰", "쿠폰")]

    @pytest.mark.parametrize("q", ["앱 upgrade 후 오류", "report 출력이 안돼요", "재고 객체 생성", "position 값"])
    def test_no_match_inside_other_words_or_across_space(self, q):
        assert "PG" not in [m.term for m in gt.find_terms(q, self.ENTRIES)]
        assert "PO" not in [m.term for m in gt.find_terms(q, self.ENTRIES)]
        assert "POS" not in [m.term for m in gt.find_terms(q, self.ENTRIES)]
        assert "고객" not in [m.term for m in gt.find_terms(q, self.ENTRIES)]

    @pytest.mark.parametrize("q, term", [("PG 승인 실패", "PG"), ("POS시스템 오류", "POS"), ("pos 화면", "POS"),
                                         ("고객센터 번호", "고객"), ("상품쿠폰 규정", "상품 쿠폰")])
    def test_real_mentions_still_match(self, q, term):
        assert term in [m.term for m in gt.find_terms(q, self.ENTRIES)]

    def test_standalone_short_term_after_longer_one_kept(self):
        assert [m.term for m in gt.find_terms("기초재고 말고 재고 수량", self.ENTRIES)] == ["기초재고", "재고"]


@pytest.mark.asyncio
async def test_resolve_returns_single_representative_term():
    """질의 기록·용어별 통계는 용어 하나로 집계 — 쉼표로 이으면 통계 조인이 깨진다(리뷰 2026-10-07)."""
    entries = [E("기초재고", "시작 재고"), E("배송비", "요금")]
    matches, mapped, enriched = await gt.resolve_query_terms("ns", "기초재고와 배송비", None, mode="lexical", entries=entries)
    assert mapped == "기초재고" and len(matches) == 2 and enriched == "기초재고와 배송비"
    assert await gt.resolve_query_terms("ns", "q", None, mode="off") == ([], None, "q")


def test_question_key_ignores_spacing_and_punctuation():
    assert gt.question_key("택배비 얼마?") == gt.question_key("택배비얼마") != gt.question_key("배송비 얼마")
