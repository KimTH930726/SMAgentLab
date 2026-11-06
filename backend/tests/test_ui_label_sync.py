"""서버 ↔ 화면 문구 한 곳 정의 지키기(11/05 하드코딩 감사 — 원문 확인 사유가 다섯 곳에서 따로 정의돼 화면마다 달랐다).
두 파일을 글자로 읽어 비교한다(앱 모듈을 안 불러와 호스트에서도 돈다). 컨테이너엔 프론트 소스가 없어 건너뛴다 —
호스트에서: py -m pytest tests/test_ui_label_sync.py"""
import ast
import re
from pathlib import Path

import pytest

_root = Path(__file__).resolve().parents[2]


def test_no_issue_reason_labels_match_frontend():
    ui = _root / "frontend-react" / "src" / "api" / "policy.ts"
    if not ui.exists():
        pytest.skip("frontend-react 소스 없음(컨테이너)")
    src = (_root / "backend" / "service" / "policy" / "source_check.py").read_text(encoding="utf-8")
    server = ast.literal_eval(re.search(r"^NO_ISSUE_REASONS = (\{.*\})$", src, re.M).group(1))
    screen = dict(re.findall(r"\{ key: '(\w+)', label: '([^']+)' \}", ui.read_text(encoding="utf-8")))
    assert {k: screen.get(k) for k in server} == server
