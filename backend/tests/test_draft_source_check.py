"""정책 초안 + 원문 확인 카드(11/05) — 카드 블록은 설명·종류·상대 정책 이름·참고표 제목만(값 없음), 설명 칸은 떼어 따로,
고칠 곳 인용은 원문에 있을 때만 보인다."""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import MagicMock

_backend = Path(__file__).resolve().parent.parent
sys.modules.setdefault("service.improvement", MagicMock())


def _load(name, rel):   # test_improvement.py와 같은 방식(conftest가 service를 가짜로 둠)
    spec = _ilu.spec_from_file_location(name, str(_backend / rel))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


d = _load("service.improvement.draft", "service/improvement/draft.py")
sys.modules["service.improvement"].draft = d
_explain = _load("service.improvement.service", "service/improvement/service.py")._explain

CTX = {"source_check": {"type": "conflict", "type_label": "정책끼리 어긋남", "description": "교환 기한이 정책마다 다름",
                        "others": ["반품 기한"], "ref_tables": ["배송비 표"]}}


def test_card_block_names_only_and_rules():
    p = d.build_prompt("policy_narrative", {"raw_body": "교환은 7일 이내"}, "", CTX)
    assert p.startswith("[원문 확인 카드]") and "반품 기한" in p and "배송비 표" in p
    assert "원문 확인 카드" in d._COMMON_RULES and "{CARD_RULE}" not in d._COMMON_RULES
    assert d.card_block(None) == "" and d.card_block({"question": "q"}) == ""


def test_explain_split_and_quote_must_exist():
    out = d.parse_draft("policy_narrative", '{"chunk_text": "교환은 14일", "raw_body": "교환은 14일", "wrong_part": "7일 이내", "fix_summary": "기한 14일로"}')
    drafted, ex = d.pop_explain(out)
    assert set(drafted) == {"chunk_text", "raw_body"} and ex == {"wrong_part": "7일 이내", "fix_summary": "기한 14일로"}
    orig = {"raw_body": "교환은  7일 이내에 신청", "chunk_text": None}
    assert _explain(ex, orig) == ex                                              # 원문에 있는 인용(공백 차이 무시)
    assert _explain({"wrong_part": "30일", "fix_summary": "x"}, orig) == {"fix_summary": "x"}   # 원문에 없는 인용은 숨김
    assert _explain({}, orig) is None
    assert d.parse_draft("knowledge", '{"content": "a", "wrong_part": "b"}') == {"content": "a"}   # 지식 초안엔 설명 칸 없음


def test_new_values_catches_invented_tokens_only():
    """원문·요청에 없던 숫자·영문/코드 값만 잡는다 — 한글 문장 고침은 안 잡음(값이 아님), 표시 안 글자는 빼고 본다."""
    orig = "B2C몰 가용재고는 판매채널별로 배분, 교환은 7일 이내"
    assert d.new_values("B2B몰 가용재고는 판매채널별로 배분", orig) == ["B2B"]
    assert d.new_values("교환은 14일 이내", orig, "14일로 바꿔 주세요") == []            # 요청에 있는 값
    assert d.new_values("교환은 7일 이내에 신청한다", orig) == []                        # 한글만 고침
    assert d.new_values("교환은 7일 【확인 필요: 30일 아닌지】", orig) == []             # 표시 안은 안 셈


def test_card_draft_routes_by_code(monkeypatch):
    """카드에서 온 정정: 요청 없음·카드 설명 그대로 → 검토 초안 · 새 값이 생긴 초안 → 검토 초안으로 대체(표시 남김) · 아니면 정정 초안."""
    import asyncio
    svc = sys.modules["service.improvement.service"]
    calls = []

    async def review(t, o, c):
        calls.append("review"); return {"chunk_text": o["raw_body"], "raw_body": o["raw_body"] + "【확인 필요: x】"}

    nxt = {}

    async def correct(t, o, u, c, b=None):
        calls.append("correct"); return dict(nxt)
    dm = svc.draft_mod   # 서비스가 실제로 쓰는 초안 모듈 — 다른 시험이 먼저 불러 둔 경우에도(전체 실행 순서 문제)
    monkeypatch.setattr(dm, "draft_review_template", review)
    monkeypatch.setattr(dm, "draft_correction", correct)
    orig = {"raw_body": "B2C몰 재고, 교환 7일", "chunk_text": "B2C몰 재고, 교환 7일"}
    run = lambda req: asyncio.run(svc._card_draft("policy_narrative", orig, req, CTX))   # noqa: E731
    assert run("")[1] == {} and calls[-1] == "review"
    assert run("교환 기한이 정책마다 다름")[1] == {} and calls[-1] == "review"          # 카드 설명 그대로
    nxt.update({"chunk_text": "B2B몰 재고, 교환 7일", "raw_body": "B2B몰 재고, 교환 7일"})
    p, extra = run("카드 문제를 고쳐 주세요")
    assert "B2B" in extra["draft_fallback"] and "【확인 필요" in p["raw_body"]               # 지어낸 값 → 대체
    nxt.clear(); nxt.update({"chunk_text": "B2C몰 재고, 교환 14일", "raw_body": "B2C몰 재고, 교환 14일", "fix_summary": "기한 14일"})
    p, extra = run("교환 14일로")
    assert p["raw_body"] == "B2C몰 재고, 교환 14일" and extra == {"draft_explain": {"fix_summary": "기한 14일"}}

