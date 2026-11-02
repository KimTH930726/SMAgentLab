"""메뉴 노출 설정(10/23) — 저장 모양 검사. 범위는 kind:/role:만, 메뉴 id는 글자 모양만, 중복 제거·정렬."""
import importlib.util
from pathlib import Path

import pytest

# conftest가 service.admin을 가짜 모듈로 바꿔 두므로 파일에서 바로 읽는다(normalize는 DB·앱 의존 없음)
_spec = importlib.util.spec_from_file_location(
    "_menu_visibility", Path(__file__).resolve().parents[1] / "service" / "admin" / "menu_visibility.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
normalize = _mod.normalize


def test_normalize_sorts_and_dedups():
    assert normalize({"kind:policy_test": ["q.stats", "k.docs", "q.stats"], "kind:domain": []}) == \
        {"kind:policy_test": ["k.docs", "q.stats"], "kind:domain": []}


def test_role_scope_allowed_for_later():
    assert normalize({"role:viewer": ["voc"]}) == {"role:viewer": ["voc"]}


@pytest.mark.parametrize("bad", [
    {"policy_test": ["k.docs"]},                 # 범위 접두사 없음
    {"kind:Org": ["k.docs"]},                  # 대문자
    {"kind:policy_test": "k.docs"},              # 목록 아님
    {"kind:policy_test": ["k.docs;drop"]},       # 메뉴 id 모양 아님
    {"kind:policy_test": [1]},
    {"kind:policy_test": ["k.docs"] * 51},       # 너무 많음
    ["kind:policy_test"],
])
def test_normalize_rejects_bad_shapes(bad):
    with pytest.raises(ValueError):
        normalize(bad)


@pytest.mark.parametrize("raw", ["[]", '"x"', "1", "not json", '{"hidden": {"bad": ["k.docs"]}}', '{"hidden": []}'])
def test_broken_stored_value_hides_nothing(raw):
    # 깨진 저장 값 → 숨김 없음(메뉴·설정 화면이 살아 있어야 다시 저장해 고칠 수 있다)
    assert _mod.parse_stored(raw) == {}


def test_stored_value_roundtrip():
    assert _mod.parse_stored('{"hidden": {"kind:policy_test": ["q.stats"]}}') == {"kind:policy_test": ["q.stats"]}
    assert _mod.parse_stored(None) == {}
