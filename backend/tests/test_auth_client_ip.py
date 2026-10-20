"""로그인 실패 제한의 사용자 IP — nginx 뒤에서는 X-Real-IP(직접 연결이 사설 주소일 때만). 예전엔 모두 nginx 주소로 세서
아무나 5번 틀리면 전원이 잠겼다(2026-10-20)."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_spec = importlib.util.spec_from_file_location("rate_limit_ip_under_test", str(Path(__file__).resolve().parents[1] / "shared/rate_limit.py"))
with patch.dict(sys.modules, {"core.config": SimpleNamespace(settings=SimpleNamespace(redis_url=""))}):
    ar = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(ar)


def _req(peer, real=None):
    return SimpleNamespace(client=SimpleNamespace(host=peer) if peer else None, headers={"x-real-ip": real} if real else {})


def test_trusts_real_ip_only_from_private_proxy():
    assert ar.client_ip(_req("172.18.0.5", "10.20.30.40")) == "10.20.30.40"     # nginx(도커 망)가 넘긴 사용자 IP
    assert ar.client_ip(_req("8.8.8.8", "10.20.30.40")) == "8.8.8.8"    # 공인 주소에서 직접 온 머리글은 안 믿음
    assert ar.client_ip(_req("172.18.0.5", "not-an-ip")) == "172.18.0.5"         # 형식이 틀리면 무시
    assert ar.client_ip(_req("172.18.0.5")) == "172.18.0.5"
    assert ar.client_ip(_req(None)) is None
