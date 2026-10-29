"""scripts/knowledge_eval_set.py — 지식 골든셋 문항을 정답 지식의 **지금** 영역으로 묻는다(10/29). 영역 재구성 뒤 옛 이름으로 물어 0%가 나왔던 사고.
불변식: 문항 수·순서·질문·정답 id는 그대로, 바뀌는 건 ns뿐 — 정답 지식이 활성인 문항만."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

_P = Path(__file__).resolve().parent.parent / "scripts" / "knowledge_eval_set.py"
_spec = importlib.util.spec_from_file_location("knowledge_eval_set", _P)
kes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kes)


class _Conn:
    def __init__(self, active):   # 활성 지식 id → 지금 영역 이름
        self.active, self.calls = active, []

    async def fetch(self, sql, ids):
        self.calls.append(ids)
        return [{"id": i, "name": n} for i, n in self.active.items() if i in ids]


def test_items_follow_gold_knowledge_to_current_area(tmp_path, capsys):
    items = [{"qid": "k1-0", "ns": "옛영역", "gold_ids": [1], "question": "q1"},
             {"qid": "k2-0", "ns": "B", "gold_ids": [2], "question": "q2"},          # 이미 지금 영역
             {"qid": "k3-0", "ns": "옛영역", "gold_ids": [3], "question": "q3"},      # 정답 지식이 지워짐 → 그대로
             {"qid": "k4-0", "ns": "옛영역", "gold_ids": [9, 4], "question": "q4"}]   # 첫 정답이 비활성이면 다음 정답의 영역
    f = tmp_path / "set.jsonl"
    f.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in items), encoding="utf-8")
    conn = _Conn({1: "A", 2: "B", 4: "C"})
    out = asyncio.run(kes.load_items(conn, str(f)))
    assert [x["ns"] for x in out] == ["A", "B", "옛영역", "C"]
    strip = lambda xs: [{k: v for k, v in x.items() if k != "ns"} for x in xs]   # noqa: E731
    assert strip(out) == strip(items)                                # ns 말고는 하나도 안 바뀜
    assert conn.calls == [[1, 2, 3, 4, 9]]                           # DB는 한 번, 정답 id 전부
    assert "2/4문항" in capsys.readouterr().out                      # 바꾼 건 보인다
