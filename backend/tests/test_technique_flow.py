"""기법 대장 흐름도(11/03) — 모든 기법이 흐름·단계에 놓였는지, 스위치 모드인 기법만 스위치가 있는지, 스위치 기본값이 지금 동작과 같은지,
켜고 끄기가 메모리·DB(같은 줄)에 같이 쓰이는지. conftest가 core.*를 가짜로 바꾸므로 실제 파일을 읽는다."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tech = _load("_flow_real_techniques", "service/eval/techniques.py")
flags = _load("_flow_real_flags", "core/technique_flags.py")


def test_every_technique_has_flow_and_valid_step():
    keys = {t["key"] for t in tech.TECHNIQUES}
    assert keys == set(tech.FLOW_OF), keys ^ set(tech.FLOW_OF)
    for k, (flow, step, mode, why) in tech.FLOW_OF.items():
        assert step in tech.FLOWS[flow], (k, step)
        assert mode in ("required", "on", "off", "switch"), k
        assert (mode == "off") == bool(why), k   # 미적용엔 사유가 꼭 있다(화면에 보인다)


def test_switch_mode_matches_flag_registry():
    switch = {k for k, v in tech.FLOW_OF.items() if v[2] == "switch"}
    assert switch == set(flags.SWITCHES)


def test_defaults_keep_current_behaviour():
    """스위치를 달아도 기본 동작은 그대로 — 채팅 기본 linked·예산 켬, 다른 컬렉션 제안은 꺼짐(예전 기본)."""
    flags._state.clear()
    assert flags.is_on("linked_scope") and flags.is_on("context_budget")
    assert not flags.is_on("collection_suggest")
    assert flags.is_on("glossary_match")   # 스위치가 아닌 기법은 늘 켜짐


@pytest.mark.asyncio
async def test_set_switch_writes_memory_and_db():
    flags._state.clear()
    conn = MagicMock()
    conn.execute = AsyncMock()
    await flags.set_switch(conn, "linked_scope", False, "admin")
    assert flags.is_on("linked_scope") is False and flags.changed("linked_scope")["by"] == "admin"
    key, value = conn.execute.await_args_list[0].args[1:3]
    assert key == "technique.linked_scope" and json.loads(value)["on"] is False
    await flags.set_switch(conn, "collection_suggest", True, "admin")   # 그 기능이 읽는 옛 줄도 같이
    assert ("chat.collection_suggest", "true") in [c.args[1:3] for c in conn.execute.await_args_list]
    with pytest.raises(KeyError):
        await flags.set_switch(conn, "chat_prompt", False, "admin")    # 필수는 스위치 없음
    flags._state.clear()


@pytest.mark.asyncio
async def test_load_reads_saved_rows():
    flags._state.clear()
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"key": "technique.context_budget", "value": json.dumps({"on": False, "by": "a", "at": "t"})},
                                         {"key": "chat.collection_suggest", "value": "true"}])
    await flags.load(conn)
    assert flags.is_on("context_budget") is False and flags.is_on("collection_suggest") is True
    flags._state.clear()


def test_baseline_matches_defaults_and_detects_drift():
    """검증된 최적 = 지금 기본값(스위치 줄 없음 → 최적) · 하나라도 바꾸면 벗어난 키로 잡힌다 · 경고 문장이 스위치마다 있다."""
    flags._state.clear()
    assert set(flags.BASELINE) == set(flags.SWITCHES) == set(flags.BASELINE_WARN)
    orig = flags.SWITCHES["ref_table_chat"]
    flags.SWITCHES["ref_table_chat"] = (lambda: True, orig[1])   # 운영 설정(policy_ref_chat 켜짐)과 같게 — conftest 가짜 settings 대신
    try:
        assert flags.off_baseline() == []
        flags._state["linked_scope"] = {"on": False, "by": "t", "at": "t"}
        assert flags.off_baseline() == ["linked_scope"]
    finally:
        flags.SWITCHES["ref_table_chat"] = orig
        flags._state.clear()


def test_required_techniques_never_switchable():
    """필수는 스위치 목록에 없고(서버 PUT은 403), 스위치 목록의 기법은 필수가 아니다 — 둘이 겹치면 필수가 꺼질 수 있다."""
    required = {k for k, v in tech.FLOW_OF.items() if v[2] == "required"}
    assert required and not (required & set(flags.SWITCHES))
    src = (ROOT / "service/eval/router.py").read_text(encoding="utf-8")
    fn = src[src.index("async def switch_technique"):]
    assert fn.index('== "required"') < fn.index("set_switch(")   # 바꾸기 전에 필수 거부
