"""업무구분(지식 category) 칸·표·프롬프트 삭제(#84, 10/23) 불변식 — 앱 코드의 SQL이 지운 것을 읽거나 쓰지 않는다.

사례 대신 전체를 훑는다: C6 리뷰에서 여러 줄 SQL 문자열 속 `category`(Track 2 INSERT)를 줄 단위 grep이 놓쳤다.
단위 테스트는 conn을 가짜로 바꿔 SQL을 실행하지 않아 이런 건 기동 뒤 그 경로를 처음 부를 때야 터진다.
"""
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_APP_DIRS = ("agents", "service", "core", "shared", "libs")

_GONE_NAMES = re.compile(r"\brag_knowledge_category\b|\bcategory_suggest\b")
_INSERT_COLS = re.compile(r"INSERT\s+INTO\s+(?:public\.)?rag_knowledge\s*\(([^)]*)\)", re.IGNORECASE)
# UPDATE rag_knowledge [별칭] SET ... category = (리뷰 10/23: 가장 흔한 쓰기 모양인데 빠져 있었음)
_UPDATE_SET = re.compile(r"UPDATE\s+(?:public\.)?rag_knowledge\b(?:\s+\w+)?\s+SET\b[^;]{0,400}?\bcategory\s*=", re.IGNORECASE)
_ALIAS_COL = re.compile(r"\b(?:k|rk|kn|knowledge)\.category\b")


def _violations(text: str) -> list[str]:
    found = [m.group(0) for m in _GONE_NAMES.finditer(text)]
    found += [f"INSERT rag_knowledge({m.group(1).strip()})" for m in _INSERT_COLS.finditer(text)
              if re.search(r"\bcategory\b", m.group(1))]
    found += [m.group(0) for m in _UPDATE_SET.finditer(text)]
    found += [m.group(0) for m in _ALIAS_COL.finditer(text)]
    return found


def test_checker_catches_multiline_insert():
    # 리뷰가 찾은 실제 모양(여러 줄 INSERT) — 이걸 못 잡으면 아래 전체 검사도 믿을 수 없다
    old = '''"""INSERT INTO rag_knowledge (namespace_id, content, embedding, base_weight, category, status)
               VALUES ($1, $2, $3::vector, 1.0, $4, 'active') RETURNING id"""'''
    assert _violations(old)
    assert not _violations('"""INSERT INTO rag_knowledge (namespace_id, content, status) VALUES ($1, $2, $3)"""')
    assert _violations("SELECT k.category FROM rag_knowledge k")
    assert _violations("UPDATE rag_knowledge SET status = 'x', category = $1 WHERE id = $2")
    assert _violations("INSERT INTO public.rag_knowledge (content, category) VALUES ($1, $2)")
    assert not _violations("UPDATE rag_knowledge SET status = 'deleted' WHERE id = $1")
    assert not _violations("SELECT category_path FROM policy_item")


def test_app_code_has_no_dropped_category_sql():
    bad = {}
    for d in _APP_DIRS:
        for p in (_ROOT / d).rglob("*.py"):
            if "tests" in p.parts:
                continue
            v = _violations(p.read_text(encoding="utf-8"))
            if v:
                bad[str(p.relative_to(_ROOT))] = v
    assert not bad, f"지운 업무구분 칸·표·프롬프트를 쓰는 SQL: {bad}"
