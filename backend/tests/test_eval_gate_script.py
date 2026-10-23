"""scripts/eval_gate.py evaluate() — 기록할 모양이 터미널 판정과 같은 근거인가(같은 up/down qid · 실패 = 종료 코드 1 · 입력 오류 2).
DB 저장(--save)은 실 스택 시나리오로 확인(기준선 한 행) — 여기는 파일 → 판정 모양만."""
import argparse
import importlib.util
import json
from pathlib import Path

_P = Path(__file__).resolve().parent.parent / "scripts" / "eval_gate.py"
_spec = importlib.util.spec_from_file_location("eval_gate_script", _P)
eg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eg)


def _write(p: Path, rows):
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return str(p)


def _ns(**kw):
    base = dict(answers_before=None, answers_after=None, retrieval_before=None, retrieval_after=None, mode="lexical",
                baseline=False, save=None, keep_baseline=False, commit=None, notes=None, by="t", run_at=None)
    return argparse.Namespace(**{**base, **kw})


def _judged(qid, label, rep=0, gic=True, typ="fact"):
    return {"qid": qid, "rep": rep, "type": typ, "cluster": qid, "gold_in_context": gic, "judge": {"label": label},
            "judge_model": "m", "judged_on": "2026-10-21", "judge_rubric": "default_v3"}


def test_gate_records_moved_qids_and_fails_on_retrieval_drop(tmp_path):
    rb = _write(tmp_path / "rb.jsonl", [{"set": "golden_v0", "qid": f"Q{i}", "hit": True, "top1": True, "type": "fact"} for i in range(5)]
                + [{"set": "negative", "qid": "N1", "hit": False}])
    ra = _write(tmp_path / "ra.jsonl", [{"set": "golden_v0", "qid": f"Q{i}", "hit": i != 2, "top1": i == 0, "type": "fact"} for i in range(5)]
                + [{"set": "negative", "qid": "N1", "hit": False}])
    ab = _write(tmp_path / "ab.jsonl", [_judged(f"Q{i}", "정답") for i in range(5)] + [_judged("N1", "정답")])
    aa = _write(tmp_path / "aa.jsonl", [_judged(f"Q{i}", "오답" if i == 1 else "정답") for i in range(5)]
                + [_judged("N1", "판정불가")])
    rec = eg.evaluate(_ns(answers_before=ab, answers_after=aa, retrieval_before=rb, retrieval_after=ra))
    assert rec["ok"] is False and rec["retrieval"]["down"] == ["Q2"]
    assert rec["answers"]["down_qids"] == ["Q1"] and rec["answers"]["up_qids"] == []
    assert rec["summary_retrieval"]["n"] == 5                       # 답 없는 질문(negative)은 검색 분모에서 빠진다
    assert rec["summary_answers"]["n"] == 5                         # 판정불가는 답 분모에서 빠진다
    assert rec["summary_answers"]["retrieval_ok_answer_wrong"] == ["Q1"]   # Q1: 검색 맞음 · 답 틀림
    assert rec["judge_meta"]["same_day_rubric"] is True
    assert rec["golden_version"].startswith("6:") and "golden_before" not in rec   # N1이 뒤에서 판정불가여도 같은 세트(리뷰 M2)
    assert rec["lines"][-1] == "게이트: 실패"


def test_baseline_alone_has_no_verdict_and_uses_answer_hits(tmp_path):
    aa = _write(tmp_path / "aa.jsonl", [_judged("A", "오답", gic=True), _judged("B", "오답", gic=False), _judged("C", "정답")])
    rec = eg.evaluate(_ns(baseline=True, answers_after=aa))
    assert rec["kind"] == "baseline" and rec["ok"] is True and rec["answers"] is None
    assert rec["summary_answers"]["retrieval_ok_answer_wrong"] == ["A"]   # 검색 파일이 없으면 답 측정의 gold_in_context
    assert rec["golden_version"].startswith("3:")
    assert not any(l.startswith("게이트") for l in rec["lines"])


def test_golden_version_ignores_filters(tmp_path):
    """버전 = 거르기 전 문항 집합 — 답 없는 질문(검색 negative)·판정불가가 있어도 같은 세트면 같은 값(리뷰 M2)."""
    qs = ["A", "B", "N"]
    rb = _write(tmp_path / "rb.jsonl", [{"set": "golden_v0", "qid": q, "hit": True} for q in "AB"] + [{"set": "negative", "qid": "N", "hit": False}])
    ab = _write(tmp_path / "ab.jsonl", [_judged(q, "정답") for q in qs])
    aa = _write(tmp_path / "aa.jsonl", [_judged("A", "정답"), _judged("B", "판정불가"), _judged("N", "정답")])   # 정책 하나가 꺼짐
    gate = eg.evaluate(_ns(answers_before=ab, answers_after=aa, retrieval_before=rb, retrieval_after=rb))
    only_ans = eg.evaluate(_ns(baseline=True, answers_after=ab))
    only_retr = eg.evaluate(_ns(baseline=True, retrieval_after=rb))
    assert "golden_before" not in gate and not any("세트가 다름" in l for l in gate["lines"])
    assert gate["golden_version"] == only_ans["golden_version"] == only_retr["golden_version"]


def test_judge_warning_uses_pairs_like_before(tmp_path):
    """채점 기준·날짜는 쌍으로 비교(옛 규칙) — 날짜 목록·기준 목록이 같아도 쌍이 엇갈리면 경고(리뷰 L1)."""
    def rows(pairs):
        return [{**_judged(f"Q{i}", "정답"), "judge_rubric": r, "judged_on": d} for i, (r, d) in enumerate(pairs)]
    ab = _write(tmp_path / "ab.jsonl", rows([("r1", "d1"), ("r2", "d2")]))
    aa = _write(tmp_path / "aa.jsonl", rows([("r1", "d2"), ("r2", "d1")]))
    rec = eg.evaluate(_ns(answers_before=ab, answers_after=aa))
    assert rec["judge_meta"]["same_day_rubric"] is False and rec["lines"][0].startswith("⚠ 채점 기준·날짜가 다름")


def test_baseline_without_countable_answers_is_not_saved(tmp_path, monkeypatch):
    aa = _write(tmp_path / "aa.jsonl", [_judged("A", "판정불가")])
    rec = eg.evaluate(_ns(baseline=True, answers_after=aa))
    assert rec["ok"] is False                                        # 예전엔 None * 100으로 죽었다(리뷰 L2)
    monkeypatch.setattr("sys.argv", ["eval_gate.py", "--baseline", "--answers-after", aa, "--save", "x"])
    monkeypatch.setattr(eg, "save", lambda *_: (_ for _ in ()).throw(AssertionError("저장하면 안 됨")))
    assert eg.main() == 1
    rf = _write(tmp_path / "rf.jsonl", [{"set": "negative", "qid": "N", "hit": False}])   # 검색만 · 셀 문항 없음(재리뷰 L1)
    assert eg.evaluate(_ns(baseline=True, retrieval_after=rf))["ok"] is False


def test_input_errors_exit_2(tmp_path, monkeypatch):
    f = _write(tmp_path / "a.jsonl", [_judged("A", "정답")])
    for argv in (["--answers-after", f], ["--baseline", "--answers-before", f, "--answers-after", f], ["--baseline"],
                 ["--baseline", "--answers-after", f, "--run-at", "어제"],
                 ["--baseline", "--answers-after", f, "--run-at", "2026-10-21T22:44"],        # 시간대 없음(리뷰 L5)
                 ["--baseline", "--keep-baseline", "--answers-after", f]):                    # 서로 반대(리뷰 L3)
        monkeypatch.setattr("sys.argv", ["eval_gate.py", *argv])
        assert eg.main() == 2
