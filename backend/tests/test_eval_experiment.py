"""실험 실행기(10/30) — 자동 실험 되는 후보 판정 · 단계 모양(전/후 같은 방식, 변경은 '후'에만) · 후보 지시문 · 진행 줄."""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.modules.setdefault("core.database", types.SimpleNamespace(get_conn=None))
_spec = importlib.util.spec_from_file_location("experiment_mod", ROOT / "service" / "eval" / "experiment.py")
ex = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ex)


def _c(**kw):
    return {"id": 7, "key": "k", "title": "t", "measure": "retrieval", "change": {"env": {"POLICY_KEYWORD_RESCUE_SLOTS": "5"}}, **kw}


def test_runnable_reasons():
    assert ex.runnable(_c(), "policy") is None
    assert "개발 작업" in ex.runnable(_c(change=None), "policy")
    assert "다시 계산" in ex.runnable(_c(measure="replay"), "policy")
    assert "업무지식 축만" in ex.runnable(_c(measure="answers", change={"no_expand": True}), "policy")
    assert ex.runnable(_c(measure="answers", change={"policy_top_k": 0, "no_expand": True}), "knowledge") is None


def _env_of(steps):
    return [s["env"] for s in steps]


def test_plan_change_only_on_after_side_and_gate_links_candidate():
    """불변식: 변경(env·지시문·문맥 옵션)은 '바꾼 설정' 단계에만 — '지금 설정'은 운영 그대로. 마지막은 게이트(후보 연결 · 기준선 안 옮김)."""
    for axis, measure in (("policy", "retrieval"), ("policy", "answers"), ("knowledge", "retrieval"), ("knowledge", "answers")):
        ch = {"env": {"X": "1"}, **({"policy_top_k": 0, "no_expand": True} if axis == "knowledge" and measure == "answers" else {})}
        steps = ex.plan(3, _c(measure=measure, change=ch), axis, label="L", by="u", prompt_file="p.txt" if measure == "answers" else None)
        gate = steps[-1]["argv"]
        assert "eval_gate.py" in gate[1] and gate[gate.index("--candidate") + 1] == "7" and "--keep-baseline" in gate
        assert gate[gate.index("--axis") + 1] == axis
        before = [s for s in steps if s["stage"].startswith("지금 설정")]
        after = [s for s in steps if s["stage"].startswith("바꾼 설정")]
        assert before and after
        for s in before:
            assert s["env"] == {} and "--prompt-file" not in s["argv"] and "--no-expand" not in s["argv"] and "--policy-top-k" not in s["argv"]
        gen_after = [s for s in after if "judge" not in s["argv"]]
        assert all(s["env"] == {"X": "1"} for s in gen_after)
        if measure == "answers":
            assert all("--prompt-file" in s["argv"] for s in gen_after)
            assert all(s["argv"][s["argv"].index("--repeat") + 1] == str(ex.REPEAT) for s in steps if "--repeat" in s["argv"])
        if axis == "knowledge" and measure == "answers":
            assert "--no-expand" in gen_after[0]["argv"] and gen_after[0]["argv"][gen_after[0]["argv"].index("--policy-top-k") + 1] == "0"
        # 게이트가 읽는 파일 = 앞 단계가 쓴 파일
        written = {s["argv"][s["argv"].index(k) + 1] for s in steps[:-1] for k in ("--out", "--details") if k in s["argv"]}
        read = {gate[gate.index(k) + 1] for k in ("--retrieval-before", "--retrieval-after", "--answers-before", "--answers-after") if k in gate}
        assert read and read <= written, (axis, measure, read, written)


def test_candidate_prompt_appends_one_line_under_principles():
    cur = "머리\n\n[원칙]\n- 하나\n- 둘\n\n[형식]\n- 셋"
    out = ex.candidate_prompt("- 넷", cur)
    assert out.count("- 넷") == 1 and out.index("- 넷") < out.index("[형식]") and out.replace("\n- 넷", "").replace("\n\n\n", "\n\n") .count("- 하나") == 1
    assert ex.candidate_prompt("넷", "규칙 없음").endswith("- 넷")
    assert ex._PROGRESS.match("  80/106") and not ex._PROGRESS.match("정답 80/106")
    assert ex._SAVED.search("기록 #52 저장(업무지식)").group(1) == "52"


def test_candidate_prompt_on_real_chat_prompt_changes_nothing_else():
    """불변식(리뷰 10/30 H1): 실제 채팅 지시문에 한 줄 넣고 그 줄을 빼면 원문 그대로 — 규칙 안 "[참고 문서]"에서 자르지 않는다. [원칙] 안, 다음 절 앞."""
    src = (ROOT / "service" / "llm" / "base.py").read_text(encoding="utf-8")
    real = src.split('CHAT_SYSTEM_PROMPT = """', 1)[1].split('"""', 1)[0]
    line = "- 새 규칙 한 줄"
    out = ex.candidate_prompt(line, real)
    assert out.count(line) == 1 and out.replace("\n" + line, "", 1) == real
    assert out.index("[원칙]") < out.index(line) < out.index("[문맥 활용]")
    assert "- 반드시 제공된 [참고 문서]만 근거로 답변." in out
