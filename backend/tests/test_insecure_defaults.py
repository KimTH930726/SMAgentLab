"""운영 기동 중단 검사(11/03 보안 리뷰 #1) — 기본 시크릿·비밀번호면 운영(APP_ENV=production)에서 기동을 멈춘다.
반입 전 점검표가 이 검사에 기대므로, 조건이 조용히 빠지지 않게 고정한다(11/12).
단위 테스트는 앱 모듈(core 등)을 가짜로 바꿔 두고 돌아 main을 직접 import할 수 없다 → 진짜 모듈을 별도 프로세스에서 불러 검사한다."""
import json
import os
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PROBE = r"""
import json, sys
from types import SimpleNamespace
import main

GOOD = dict(jwt_secret_key="x" * 40, admin_default_password="Str0ng!pass",
            database_url="postgresql://ops:Str0ng!db@postgres:5432/opsdb")
def s(**kw): return SimpleNamespace(**{**GOOD, **kw})

out = {"good": main.insecure_settings_reasons(s()), "cases": {}}
for name, kw in {
    "jwt_default": dict(jwt_secret_key=main._DEFAULT_JWT),
    "jwt_short": dict(jwt_secret_key="short"),
    "admin_default": dict(admin_default_password="1111"),
    "admin_short": dict(admin_default_password="abc"),
    "db_default": dict(database_url="postgresql://ops:ops1234@postgres:5432/opsdb"),
}.items():
    out["cases"][name] = main.insecure_settings_reasons(s(**kw))

out["all_bad"] = main.insecure_settings_reasons(s(jwt_secret_key="short", admin_default_password="abc",
                                                  database_url="postgresql://ops:ops1234@postgres:5432/opsdb"))

main.settings = SimpleNamespace(app_env="production", **{**GOOD, "admin_default_password": "1111"})
main.insecure_settings_reasons.__defaults__ = (main.settings,)
try:
    main._warn_if_insecure_defaults(); out["prod"] = "started"
except RuntimeError as e:
    out["prod"] = "stopped" if "운영 기동 중단" in str(e) else "other"

main.settings = SimpleNamespace(app_env="development", **{**GOOD, "admin_default_password": "1111"})
main.insecure_settings_reasons.__defaults__ = (main.settings,)
main._warn_if_insecure_defaults(); out["dev"] = "started"
print("RESULT" + json.dumps(out, ensure_ascii=False))
"""


def _probe() -> dict:
    p = subprocess.run([sys.executable, "-c", _PROBE], cwd=APP_DIR, capture_output=True, text=True, timeout=120)
    line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT")), None)
    assert line, f"main 불러오기 실패(rc={p.returncode}): {p.stderr[-800:]}"
    return json.loads(line[len("RESULT"):])


def test_insecure_defaults_guard():
    r = _probe()
    assert r["good"] == []
    expect = {"jwt_default": "JWT_SECRET_KEY", "jwt_short": "JWT_SECRET_KEY", "admin_default": "ADMIN_DEFAULT_PASSWORD",
              "admin_short": "ADMIN_DEFAULT_PASSWORD", "db_default": "POSTGRES_PASSWORD"}
    for name, word in expect.items():
        why = r["cases"][name]
        assert len(why) == 1 and word in why[0], (name, why)
    assert len(r["all_bad"]) == 3
    text = " ".join(r["all_bad"])
    for secret in ("short", "abc", "ops1234@"):   # 사유에 값 자체는 안 나온다
        assert secret not in text
    assert r["prod"] == "stopped"    # 운영: 기본 비밀번호면 기동 중단
    assert r["dev"] == "started"     # 개발: 경고만
