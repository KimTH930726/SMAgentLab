"""참고표·개요 채팅 연결(service/policy/ref_search.py, v0 #12) — 표 압축·거르기·되묻기 규칙을 성질로 검사(DB 없음)."""
import importlib.util
import random
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_db = types.ModuleType("core.database")
_db.get_conn = None
_prev = sys.modules.get("core.database")
sys.modules["core.database"] = _db
try:
    _spec = importlib.util.spec_from_file_location("ref_search_t", ROOT / "service" / "policy" / "ref_search.py")
    rs = importlib.util.module_from_spec(_spec)
    sys.modules["ref_search_t"] = rs   # dataclass가 자기 모듈을 sys.modules에서 찾는다
    _spec.loader.exec_module(rs)
finally:
    if _prev is not None:
        sys.modules["core.database"] = _prev
    else:
        del sys.modules["core.database"]

COLS = [{"name": "No.", "role": "id", "decisive": False},
        {"name": "챌린지 유형", "role": "condition", "decisive": True},
        {"name": "미션 단위", "role": "condition", "decisive": True},
        {"name": "비고", "role": "condition", "decisive": False},
        {"name": "응모기회", "role": "result", "decisive": False}]


def _rows(n=40, seed=1):
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        t, u = rnd.choice(["일반형", "확률형"]), rnd.choice(["주문 건수", "결제 금액", "방문 일수"])
        out.append({"No.": str(i), "챌린지 유형": t, "미션 단위": u, "비고": f"메모{i}", "응모기회": f"{t}-{u}-1회"})
    return out


def test_compressed_view_loses_no_decision_to_result_pair():
    """압축본 = 결정 열 + 결과 열의 고유 줄 — 원표의 (결정 값 → 결과)가 하나도 빠지지 않고, 없던 짝이 생기지 않는다."""
    rows = _rows()
    cols, body, dec = rs.table_view(COLS, rows)
    assert dec == ["챌린지 유형", "미션 단위"] and cols == dec + ["응모기회"]
    want = {(r["챌린지 유형"], r["미션 단위"], r["응모기회"]) for r in rows}
    assert {tuple(b) for b in body} == want and len(body) == len(want)   # 유실 0 · 중복 0 · 새 내용 0


def test_small_table_without_decision_columns_kept_whole():
    cols = [{"name": "번호", "role": "id"}, {"name": "유형", "role": None}, {"name": "템플릿", "role": None}]
    rows = [{"번호": "1", "유형": "A", "템플릿": "x"}, {"번호": "2", "유형": "A", "템플릿": "x"}]
    c, body, dec = rs.table_view(cols, rows)
    assert dec == [] and c == ["유형", "템플릿"] and len(body) == 2   # 통째 — 같은 줄도 그대로(줄이지 않음)


def test_clarify_only_for_missing_decision_and_picked_option_never_reasks():
    rows = _rows()
    cols, body, dec = rs.table_view(COLS, rows)
    q = "챌린지 응모기회 몇 번 줘?"
    c = rs.clarify_for_table("응모기회표", dec, cols, body, q)
    assert c and [o["label"] for o in c["options"]] == ["일반형", "확률형"]   # 첫 빠진 결정 열
    assert all(o["scope"].startswith(rs.REF_SCOPE) for o in c["options"])
    for o in c["options"]:   # 성질: 고른 선택지의 질문으로 다시 물으면 같은 열을 다시 묻지 않는다(대화가 맴돌지 않음)
        c2 = rs.clarify_for_table("응모기회표", dec, cols, body, o["query"])
        assert c2 is None or "챌린지 유형" not in c2["message"]
    full = "확률형 챌린지 결제 금액 미션이면 응모기회 몇 번?"
    assert rs.clarify_for_table("응모기회표", dec, cols, body, full) is None   # 조건을 다 말했으면 묻지 않음


def test_clarify_skips_when_too_many_values():
    cols = [{"name": "등급", "role": "condition", "decisive": True}, {"name": "혜택", "role": "result"}]
    rows = [{"등급": f"등급{i}", "혜택": str(i)} for i in range(rs.CLARIFY_MAX_OPTIONS + 3)]
    c, body, dec = rs.table_view(cols, rows)
    assert rs.clarify_for_table("t", dec, c, body, "혜택 알려줘") is None   # 선택지가 너무 많으면 표로 답하게


def test_big_table_filtered_by_mentioned_values_then_truncated_with_note():
    rows = [{"No.": str(i), "챌린지 유형": "일반형" if i % 2 else "확률형", "미션 단위": f"단위{i:04d}", "비고": "",
             "응모기회": "x" * 60} for i in range(400)]
    block, dec, truncated, body, cols = rs.table_block("큰 표", COLS, rows, "일반형 응모기회")
    assert len(block) <= rs.MAX_TABLE_CHARS + 200
    assert all(r[0] == "일반형" for r in body)            # 질문에 나온 결정 값으로 거름
    assert truncated and "앞" in block.splitlines()[0]     # 잘랐으면 숨기지 않고 표시
    small, _, t2, _, _ = rs.table_block("작은 표", COLS, _rows(10), "아무 질문")
    assert not t2 and "앞" not in small.splitlines()[0]


def test_mentioned_ignores_spaces_and_single_chars():
    assert rs.mentioned("주문 건수", "주문건수 미션은?") and not rs.mentioned("Y", "Y 인가요")


def test_clarify_options_complete_even_when_table_truncated():
    """리뷰 M2 — 표가 잘려도 되묻기 선택지 = (조건값으로 거른) 전체 행의 값 집합. 잘린 행으로 세면 고를 값이 버튼에 없었다."""
    types_ = ["일반형", "확률형", "스탬프형"]
    rows = [{"No.": str(i), "챌린지 유형": types_[i % 3], "미션 단위": f"단위{i:04d}", "비고": "", "응모기회": "x" * 60}
            for i in range(600)]
    block, dec, truncated, body, cols = rs.table_block("큰 표", COLS, rows, "응모기회 몇 번?")
    assert truncated
    assert {r[0] for r in body} == set(types_)                     # 거른(이번엔 안 거름) 전체 행 — 잘리기 전
    c = rs.clarify_for_table("큰 표", dec, cols, body, "응모기회 몇 번?")
    assert c and {o["label"] for o in c["options"]} == set(types_)
