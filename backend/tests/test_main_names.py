"""main.py 상수 이름 검사(11/04 기동 실패 재발 방지) — 마이그레이션 함수 안에서 import 안 한 상수(CHAT_SYSTEM_PROMPT_KNOWLEDGE)를 써서
기동이 멈췄다. `python -c "import main"`은 함수 안 이름을 실행하지 않아 못 잡고, 단위 테스트는 앱 모듈을 가짜로 바꿔 돌아 못 잡는다.
pyflakes가 없는 환경(폐쇄망)이라 표준 라이브러리 ast로: 파일에서 읽는 대문자 이름(상수)은 모두 이 파일 어딘가에서 정의·import돼야 한다."""
import ast
import builtins
from pathlib import Path

import pytest

_B = Path(__file__).resolve().parent.parent


def _undefined_constants(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound: set[str] = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return {u for u in used - bound if u.isupper() or (u[:1].isupper() and "_" in u)}


@pytest.mark.parametrize("rel", ["main.py"])
def test_constants_defined_or_imported(rel):
    assert _undefined_constants(_B / rel) == set()


def test_catches_missing_import(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("from x import A\n\nasync def mig():\n    return A, B_CONST\n", encoding="utf-8")
    assert _undefined_constants(f) == {"B_CONST"}
