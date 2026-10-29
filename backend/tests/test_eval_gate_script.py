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
                 ["--baseline", "--keep-baseline", "--answers-after", f],                     # 서로 반대(리뷰 L3)
                 ["--answers-before", f, "--answers-after", f, "--save", "x", "--candidate", "1", "--axis", "knowledge"],  # 개선 후보는 정책서 축만(#96)
                 ["--baseline", "--answers-after", f, "--axis", "voc"]):                      # 없는 축
        monkeypatch.setattr("sys.argv", ["eval_gate.py", *argv])
        try:
            assert eg.main() == 2
        except SystemExit as e:   # argparse choices 위반은 SystemExit(2)
            assert e.code == 2


def test_failures_are_counted_apart_from_accuracy(tmp_path):
    """10/29 — 답을 못 낸 문항(오류·채점실패 = 게이트웨이 거부·연결 실패)은 정답률 분모에서 빼고 따로 센다.
    불변식: 채점된 문항 + 못 낸 문항 = 세트 전부(회차가 여러 개여도 — 한 회차라도 못 냈으면 그 문항 전체를 뺀다, 리뷰 L1)."""
    aa = _write(tmp_path / "aa.jsonl", [_judged("A", "정답"), _judged("B", "오류"), _judged("C", "오답"), _judged("D", "채점실패")])
    base = eg.evaluate(_ns(baseline=True, answers_after=aa))
    s = base["summary_answers"]
    assert s["failures"] == ["B", "D"] and s["n"] == 2 and s["accuracy"] == 0.5      # 거부를 오답으로 세면 25%
    assert s["n"] + len(s["failures"]) == 4 and base["golden_version"].startswith("4:")   # 세트는 그대로(못 낸 문항도 세트의 문항)
    assert "답 못 냄 2문항" in base["lines"][-1]
    multi = _write(tmp_path / "multi.jsonl", [_judged("A", "정답", rep=0), _judged("A", "오류", rep=1), _judged("A", "정답", rep=2),
                                              _judged("B", "오답", rep=0), _judged("B", "정답", rep=1), _judged("B", "정답", rep=2)])
    m = eg.evaluate(_ns(baseline=True, answers_after=multi))["summary_answers"]
    assert m["failures"] == ["A"] and m["n"] == 1                    # A는 정답률에 안 들어감(양쪽에 다 잡히지 않음)
    assert m["n"] + len(m["failures"]) == 2


def test_new_failures_fail_the_gate(tmp_path):
    """전엔 맞던 문항이 거부되면 짝이 없어져 비교에서 빠진다 — 그대로면 통과하고 기준선까지 옮겨졌다(리뷰 10/29 M1). 새로 못 낸 문항 = 실패.
    같은 문항이 계속 못 내는 건(전/후 같음) 실패 아님, 개수가 같아도 다른 문항이 새로 못 내면 실패(리뷰 L2)."""
    ab = _write(tmp_path / "ab.jsonl", [_judged("A", "정답"), _judged("B", "정답"), _judged("C", "오류")])
    aa = _write(tmp_path / "aa.jsonl", [_judged("A", "정답"), _judged("B", "오류"), _judged("C", "오류")])
    rec = eg.evaluate(_ns(answers_before=ab, answers_after=aa))
    assert rec["ok"] is False and "새로 답 못 냄" in rec["answers"]["failed"] and rec["answers"]["failures_new"] == ["B"]
    assert any(l.startswith("⚠ [장애] 답 못 냄 1 → 2") for l in rec["lines"])
    same = eg.evaluate(_ns(answers_before=aa, answers_after=aa))
    assert same["ok"] is True and same["answers"]["failures_new"] == []
    assert any(l.startswith("[장애] 답 못 냄 2 → 2") for l in same["lines"])
    swap_b = _write(tmp_path / "sb.jsonl", [_judged("A", "정답"), _judged("B", "오류"), _judged("C", "정답")])
    swap_a = _write(tmp_path / "sa.jsonl", [_judged("A", "정답"), _judged("B", "정답"), _judged("C", "오류")])
    sw = eg.evaluate(_ns(answers_before=swap_b, answers_after=swap_a))
    assert sw["ok"] is False and sw["answers"]["failures_new"] == ["C"]   # 1 → 1이어도 C가 새로 못 냄


def test_axis_must_match_result_files(tmp_path, monkeypatch):
    """지식 측정 파일(type/set = knowledge)을 --axis 없이 저장하면 정책서 기준선을 덮었다(리뷰 10/29 M2) — 입력 오류로."""
    k = _write(tmp_path / "k.jsonl", [{**_judged("k1-0", "정답"), "type": "knowledge"}])
    kr = _write(tmp_path / "kr.jsonl", [{"set": "knowledge", "qid": "k1-0", "hit": True, "type": "knowledge"}])
    monkeypatch.setattr(eg, "save", lambda *_: (_ for _ in ()).throw(AssertionError("저장하면 안 됨")))
    for argv in (["--baseline", "--answers-after", k, "--save", "x"], ["--baseline", "--retrieval-after", kr, "--save", "x"],
                 ["--baseline", "--answers-after", _write(tmp_path / "p.jsonl", [_judged("A", "정답")]), "--axis", "knowledge", "--save", "x"]):
        monkeypatch.setattr("sys.argv", ["eval_gate.py", *argv])
        assert eg.main() == 2
    monkeypatch.setattr("sys.argv", ["eval_gate.py", "--baseline", "--answers-after", k, "--axis", "knowledge"])   # 맞게 주면 통과(저장 없이)
    assert eg.main() == 0
