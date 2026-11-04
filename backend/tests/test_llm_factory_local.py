"""get_local_llm(11/04) — 원문이 들어가는 짧은 판정은 로컬 LLM으로, 주소가 없으면 시스템 LLM + 경고(대체 처리를 보이게).

conftest가 service·core 설정을 가짜로 바꿔 두므로 factory.py를 파일 경로로 직접 로드한다(프로바이더 클래스는 가짜 그대로).
"""
import importlib.util as _ilu
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

for _m in ("service.llm", "service.llm.base", "service.llm.inhouse", "service.llm.ollama", "service.llm.openai_compat"):
    sys.modules.setdefault(_m, MagicMock())   # factory가 가져오는 프로바이더 모듈 — 로드용 자리(진짜 동작은 아래에서 함수 단위로)
_spec = _ilu.spec_from_file_location("llm_factory_under_test", str(Path(__file__).resolve().parent.parent / "service" / "llm" / "factory.py"))
factory = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = factory
_spec.loader.exec_module(factory)


@pytest.fixture
def fresh(monkeypatch):
    settings = MagicMock()
    monkeypatch.setattr(factory, "settings", settings)
    monkeypatch.setattr(factory, "_ingest_local", None)
    local_cls = MagicMock(side_effect=lambda *a, **k: object())
    monkeypatch.setattr(factory, "OpenAICompatProvider", local_cls)
    system = object()
    monkeypatch.setattr(factory, "get_llm_provider", lambda: system)
    return settings, local_cls, system


def test_local_when_address_set_and_reused(fresh):
    settings, local_cls, system = fresh
    settings.openai_compat_base_url = "http://10.0.0.1:8000/v1"
    a, b = factory.get_local_llm(), factory.get_local_llm()
    assert a is b and a is not system and local_cls.call_count == 1


def test_falls_back_to_system_with_warning(fresh, caplog):
    settings, local_cls, system = fresh
    settings.openai_compat_base_url = ""
    with caplog.at_level(logging.WARNING):
        assert factory.get_local_llm() is system
    assert local_cls.call_count == 0 and "시스템 LLM으로" in caplog.text
