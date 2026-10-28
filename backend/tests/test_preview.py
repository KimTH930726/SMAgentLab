"""검색 미리보기(10/27) — 채팅이 할 일 판정(되묻기 > 근거 없음 > 목록 답 > 답) · 문맥 후보 목록.
불변식: 후보 수 = 정책 파라미터 + 정책 서술 + 지식 + 공통코드 + 칼럼 + 참고표 + 개요(빠짐·중복 없이), 정책 줄은 시험지 정답용 logical_id를 갖는다."""
import importlib.util as _ilu
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

_spec = _ilu.spec_from_file_location("preview_under_test", str(Path(__file__).resolve().parent.parent / "service" / "eval" / "preview.py"))
pv = _ilu.module_from_spec(_spec)
sys.modules["preview_under_test"] = pv
_spec.loader.exec_module(pv)


def _cc(**kw):
    base = dict(clarify=None, abstain=False, list_answer=None, llm_context="문맥", refs=None, common_codes=[], db_columns=[], results=[],
                policy_result=NS(params=[], narratives=[]))
    base.update(kw)
    return NS(**base)


@pytest.mark.parametrize("kw, key", [
    (dict(clarify={"message": "어느 업무?", "options": [{"label": "주문"}]}, abstain=True, list_answer={"total": 3, "shown": 3, "names": []}), "clarify"),
    (dict(abstain=True, list_answer={"total": 3, "shown": 3, "names": []}), "abstain"),
    (dict(list_answer={"total": 30, "shown": 30, "names": ["배송"]}), "list"),
    (dict(llm_context="  "), "empty"),
    (dict(), "answer"),
])
def test_verdict_order_matches_chat(kw, key):
    assert pv.verdict(_cc(**kw))["key"] == key


def test_clarify_options_are_labels():
    v = pv.verdict(_cc(clarify={"message": "m", "options": [{"label": "주문"}, {"label": "전부 보기"}]}))
    assert v["options"] == ["주문", "전부 보기"]


def test_items_cover_every_branch_once():
    p = NS(logical_id=7, policy_name="배송비", category_path=["배송"], score=0.3, param_name="기준", value="3만원")
    n = NS(logical_id=8, policy_name="반품", category_path=[], score=0.6)
    k = [NS(id=i, heading_path=[], source_file=None, content="x" * 500, base_weight=1.0, final_score=1.0) for i in (1, 2)]
    refs = NS(tables=[NS(title="표", score=0.5)], overviews=[NS(title="개요", score=0.4)])
    cc = _cc(policy_result=NS(params=[p], narratives=[n]), results=k, common_codes=[{"code_name": "A"}], db_columns=[{"column_name": "c", "table_name": "t"}], refs=refs)
    out = pv.items(cc, {1}, lambda r: 0.5)
    assert len(out) == 1 + 1 + 2 + 1 + 1 + 2
    pol = [x for x in out if x["kind"] == "policy"]
    assert [x["logical_id"] for x in pol] == [7, 8] and pol[0]["label"] == "기준 = 3만원"
    kn = [x for x in out if x["kind"] == "knowledge"]
    assert [x["adopted"] for x in kn] == [True, False]
    assert all(len(x["label"]) == pv.SNIPPET for x in kn)   # 원문 전체가 아니라 앞부분만
