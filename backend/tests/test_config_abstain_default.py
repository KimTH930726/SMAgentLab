"""근거 없음 즉시 거절 기본값(11/09 재보정) — 안 주면 0.50, 빈 값이면 꺼짐(compose ${POLICY_ABSTAIN_MIN_SCORE-0.50}와 짝). 리뷰: 이 회귀를 잡는 테스트가 없었다."""
import importlib.util
from pathlib import Path

import pytest

_P = Path(__file__).resolve().parents[1] / "core" / "config.py"


def _settings_cls():
    spec = importlib.util.spec_from_file_location("_real_core_config_abstain", _P)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Settings


@pytest.mark.parametrize("env,expected", [(None, 0.50), ("", None), ("off", None), ("0.45", 0.45)])
def test_abstain_min_score_default_and_off(monkeypatch, env, expected):
    monkeypatch.delenv("POLICY_ABSTAIN_MIN_SCORE", raising=False)
    if env is not None:
        monkeypatch.setenv("POLICY_ABSTAIN_MIN_SCORE", env)
    assert _settings_cls()(_env_file=None).policy_abstain_min_score == expected


def test_compose_passes_default_and_keeps_empty():
    """compose가 ':-'(빈 값도 기본값으로 바꿈)로 돌아가면 '빈 값 = 끔'이 사라진다 — 콜론 없는 '-'여야 한다."""
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml")
    if not compose.exists():
        pytest.skip("compose 파일 없음(컨테이너 안)")
    assert "POLICY_ABSTAIN_MIN_SCORE: ${POLICY_ABSTAIN_MIN_SCORE-0.50}" in compose.read_text(encoding="utf-8")
