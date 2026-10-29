"""자가 개선 후보(service/eval/improve.py)·기법 대장(techniques.py)·가설 묶음 파싱(scripts/eval_improve.py) — 성질로.
- 대장: 키 유일 · 단계 유효 · 코드 위치의 이름이 실제 그 파일에 있음(다시 코드에만 숨지 않게)
- 후보: 가리키는 기법이 대장에 있음 · 후보 qid ⊆ 그 원인 qid · 비지 않은 원인(LLM 오류 제외)마다 후보 1개 이상
- 리플레이: 망가짐 0으로 고치는 하한이 있으면 시도 추천, 없으면 기각 추천 + 생성 쪽 후보
- 묶음 파싱: 유실 0 · 중복 0 · 없는 qid 0(LLM이 무엇을 내든)
"""
import importlib.util
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "libs" / "trustkit"))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


techniques = _load("service.eval.techniques", "service/eval/techniques.py")
improve = _load("service.eval.improve", "service/eval/improve.py")
from trustkit.eval.diagnose import CAUSE_KEYS, replay  # noqa: E402


def test_registry_points_at_real_code():
    keys = [t["key"] for t in techniques.TECHNIQUES]
    assert len(keys) == len(set(keys))
    for t in techniques.TECHNIQUES:
        assert t["stage"] in techniques.STAGES, t["key"]
        if t["where"]:
            path, name = t["where"].split(":")
            assert name in (ROOT / path).read_text(encoding="utf-8"), t["where"]
        for _label, (env, default) in t["values"].items():
            assert default != "" and (env is None or env.isupper())
    rows = techniques.as_rows()
    assert [r["key"] for r in rows] == keys and all(isinstance(r["evidence"], list) for r in rows)


def _diag(seed):
    rng = random.Random(seed)
    causes = {k: [f"{k}-{i}" for i in range(rng.randint(0, 6))] for k in CAUSE_KEYS}
    return {"n": 400, "n_failed": sum(map(len, causes.values())), "causes": causes}


def test_candidates_cover_causes_and_point_at_registry():
    for seed in range(20):
        d = _diag(seed)
        rng = random.Random(seed)
        types = {q: rng.choice(["fact", "cross", "condition", "navigation"]) for v in d["causes"].values() for q in v}
        mapped = {q: (rng.choice([None, "용어"])) for q in types}
        cands = improve.build_candidates(d, types=types, mapped=mapped, abstain=None)
        for c in cands:
            assert c["technique"] in techniques.BY_KEY
            assert set(c["qids"]) <= set(d["causes"][c["cause"]]) and c["n_target"] == len(c["qids"]) > 0
            assert c["measure"] in improve.MEASURES
        for k, qs in d["causes"].items():
            if qs and k != "llm_error":
                assert any(c["cause"] == k for c in cands), k
        keys = [c["key"] for c in cands]
        assert len(keys) == len(set(keys))   # (run_id, key) 고유 — 저장 때 부딪히지 않게


def test_abstain_replay_recommendation():
    neg = {"N1", "N2"}
    cur = {"N1": 0.0, "N2": 0.0, "A": 1.0, "B": 1.0}
    rp = lambda fn: replay(cur, fn, neg)   # noqa: E731
    safe = {"N1": {"s": 0.3}, "N2": {"s": 0.9}, "A": {"s": 0.8}, "B": {"s": 0.7}}
    abst = improve.abstain_replay(rp, safe, lambda sig, cut, prm: sig["s"] < cut - 0.1)
    d = {"n": 4, "n_failed": 2, "causes": {**{k: [] for k in CAUSE_KEYS}, "answered_no_answer": ["N1", "N2"]}}
    c = improve.build_candidates(d, types={}, mapped={}, abstain=abst)
    assert c[0]["recommend"] == "try" and abst["best"]["broken"] == 0 and abst["best"]["fixed"] >= 1
    hopeless = improve.abstain_replay(rp, {"N1": {"s": 0.9}, "N2": {"s": 0.9}, "A": {"s": 0.1}, "B": {"s": 0.9}},
                                      lambda sig, cut, prm: sig["s"] < cut)
    c2 = improve.build_candidates(d, types={}, mapped={}, abstain=hopeless)
    assert hopeless["best"] is None and c2[0]["recommend"] == "reject"
    assert any(x["key"] == "abstain_prompt" and x["measure"] == "answers" for x in c2)


def test_parse_groups_no_loss_no_dup():
    allowed = {f"Q{i}" for i in range(10)}
    for text in ['{"groups":[{"name":"a","qids":["Q1","Q2","ZZ"]},{"name":"b","qids":["Q2","Q3"]}]}', "엉망", "",
                 '앞말 {"groups": [{"name": "x", "qids": ["Q9"]}]} 뒷말', '{"groups": "no"}', '{"groups":[1,2]}']:
        gs = improve.parse_groups(text, allowed)
        got = [q for g in gs for q in g["qids"]]
        assert sorted(got) == sorted(allowed) and len(got) == len(set(got))   # 유실 0 · 중복 0 · 없는 qid 0


# ── 리뷰 M1~M3(10/23): 이어받기 · 채택 조건 · 추천 일치 — 사람 결정 흐름을 시나리오가 아니라 규칙 전체로 ──────────────

from datetime import datetime, timedelta, timezone  # noqa: E402


def test_carry_only_latest_rejected_or_held_with_same_recommend():
    # 기각 → (다음 진단에서) 되돌리기 → 그다음 진단: 최근 행은 proposed라 옛 기각을 살리지 않는다(M1)
    assert improve.carry_decision({"status": "proposed", "recommend": "reject"}, "reject") is None
    assert improve.carry_decision({"status": "adopted", "recommend": "try"}, "try") is None   # 채택은 끝난 일
    assert improve.carry_decision(None, "try") is None
    for st in ("rejected", "held", "approved"):   # 승인도 이어받는다 — 측정 중 재진단해도 채택할 수 있게
        prev = {"status": st, "recommend": "reject", "id": 3}
        assert improve.carry_decision(prev, "reject") is prev
        assert improve.carry_decision(prev, "try") is None          # 추천이 바뀌면 새 근거 — 다시 묻는다(M2)


def test_adopt_requires_approved_and_passing_gate_after_approval():
    t0 = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
    ok_gate = {"kind": "gate", "verdict": "pass", "run_at": t0 + timedelta(minutes=5)}
    assert improve.adopt_error("approved", ok_gate, t0) is None
    for status in ("proposed", "held", "rejected", "adopted"):
        assert improve.adopt_error(status, ok_gate, t0)                # 시도 승인 전·후는 안 됨
    for bad in (None, {**ok_gate, "verdict": "fail"}, {**ok_gate, "verdict": None, "kind": "baseline"},
                {**ok_gate, "kind": "judge_check"}, {**ok_gate, "run_at": t0 - timedelta(seconds=1)}):
        assert improve.adopt_error("approved", bad, t0), bad           # 통과한 게이트·승인 뒤만
    assert improve.adopt_error("approved", ok_gate, None)               # 승인 시각이 없으면(데이터 이상) 막는다


def test_agreement_counts_each_human_decision_once():
    rows = [{"recommend": "try", "status": "approved", "carried_from_id": None, "n": 2},
            {"recommend": "reject", "status": "rejected", "carried_from_id": None, "n": 1},
            {"recommend": "reject", "status": "rejected", "carried_from_id": 7, "n": 5},   # 이어받은 복사 — 세지 않음
            {"recommend": "try", "status": "rejected", "carried_from_id": None, "n": 1},
            {"recommend": "reject", "status": "adopted", "carried_from_id": None, "n": 1},
            {"recommend": "review", "status": "rejected", "carried_from_id": None, "n": 4},  # 검토 추천은 맞음/반대 없음
            {"recommend": "try", "status": "proposed", "carried_from_id": None, "n": 3}]
    assert improve.agreement(rows) == {"agree": 3, "disagree": 2}


# ── 10/26 잠근 시험지 · 실사용 문항 ─────────────────────────────────────────────────────

def test_split_locked_removes_every_locked_row():
    rows = [{"qid": "A", "type": "fact"}, {"qid": "H", "type": "human", "rep": 0}, {"qid": "H", "type": "human", "rep": 1},
            {"qid": "R", "type": "real"}, {"qid": "B", "type": None}]
    open_rows, locked = improve.split_locked(rows)
    assert locked == ["H", "R"] and [r["qid"] for r in open_rows] == ["A", "B"]
    assert not {r["qid"] for r in open_rows} & set(locked)   # 잠근 문항은 진단에 한 행도 안 남는다


def test_adopt_blocked_when_locked_layer_drops():
    t0 = datetime(2026, 10, 26, 9, tzinfo=timezone.utc)
    ok = {"kind": "gate", "verdict": "pass", "run_at": t0 + timedelta(hours=1),
          "answers": {"groups": {"전체": {"diff": 0.01}, "human": {"diff": 0.0}, "real": {"diff": 0.02}}}}
    assert improve.adopt_error("approved", ok, t0) is None
    bad = {**ok, "answers": {"groups": {"전체": {"diff": 0.01}, "human": {"diff": -0.0125}}}}   # 10/23 #11과 같은 모양
    assert "잠근 문항" in improve.adopt_error("approved", bad, t0)
    assert improve.adopt_error("approved", {**ok, "answers": None}, t0) is None   # 잠근 층이 없던 옛 측정은 막지 않음


# ── 10/26 적재 검수 → 후보 ─────────────────────────────────────────────────────────────

def test_ingest_candidates_only_failed_and_point_at_registry():
    results = [
        {"key": "policy_param_derived", "store": "정책", "name": "파라미터 파생", "ref": "v0 #9", "bad": 981, "total": 981,
         "qids": ["Q1", "Q2"], "accuracy": {"n": 2, "acc": 0.5}},
        {"key": "policy_body", "store": "정책", "name": "원문", "ref": "§5-3", "bad": 0, "total": 981},       # 통과 — 후보 없음
        {"key": "new_unknown_check", "store": "지식", "name": "새 검사", "ref": "x", "bad": 3, "total": 10},   # 대응표 없음 — 일반 후보
    ]
    c = improve.ingest_candidates(results)
    assert [x["key"] for x in c] == ["ingest_policy_param_derived", "ingest_new_unknown_check"]
    assert all(x["cause"] == "ingest" and x["technique"] in techniques.BY_KEY for x in c)
    assert c[0]["qids"] == ["Q1", "Q2"] and "50.0%" in c[0]["why"] and c[1]["technique"] == "ingest_contract"
    assert improve.ingest_candidates([]) == []


def test_ingest_remedy_points_at_registry():
    for key, (tech, title, action, measure) in improve.INGEST_REMEDY.items():
        assert tech in techniques.BY_KEY and measure in improve.MEASURES and title and action, key


def test_ingest_check_list_shape():
    spec = importlib.util.spec_from_file_location("check_ingest_t", ROOT / "scripts" / "check_ingest.py")
    src = (ROOT / "scripts" / "check_ingest.py").read_text(encoding="utf-8")
    # 스크립트는 컨테이너 경로를 sys.path에 넣고 앱을 import하므로 불러오지 않고 목록 모양만 소스에서 확인
    assert spec is not None and "CHECKS: list[" in src
    keys = __import__("re").findall(r'^\s+\("([a-z_]+)", "(?:정책|참고표|지식|용어)"', src, __import__("re").M)
    assert len(keys) == len(set(keys)) >= 10   # 키 중복 없음 — 후보 키(ingest_<키>)가 겹치지 않게


def test_adopt_only_this_candidates_measurement_not_older_than_baseline():
    """v2.172 리뷰 M3 — 운영 반영 = 그 측정이 지금 점수가 되므로: 이 후보를 잰 측정만, 지금 점수보다 예전 측정은 안 됨."""
    t0 = datetime(2026, 10, 26, 9, tzinfo=timezone.utc)
    g = {"id": 13, "kind": "gate", "verdict": "pass", "run_at": t0 + timedelta(hours=2)}
    assert improve.adopt_error("approved", g, t0, measured_ids=[13], baseline_run_at=t0 + timedelta(hours=1)) is None
    assert "잰 측정이 아닙니다" in improve.adopt_error("approved", g, t0, measured_ids=[11, 12])
    assert "잰 측정이 아닙니다" in improve.adopt_error("approved", g, t0, measured_ids=[])
    assert "예전 측정" in improve.adopt_error("approved", g, t0, measured_ids=[13], baseline_run_at=t0 + timedelta(hours=3))


def test_ingest_candidates_drop_locked_questions():
    """v2.172 리뷰 M1 — 적재 검수 후보가 겨냥할 문항에 잠근 시험지(사람·실사용)가 들어가지 않는다."""
    results = [{"key": "policy_param_derived", "store": "정책", "name": "파라미터 파생", "ref": "v0 #9", "bad": 3, "total": 3,
                "qids": ["Q1", "H1", "REAL-2"]}]
    c = improve.ingest_candidates(results, locked=["H1", "REAL-2"])
    assert c[0]["qids"] == ["Q1"]
    assert improve.ingest_candidates(results)[0]["qids"] == ["H1", "Q1", "REAL-2"]   # locked를 안 주면 그대로(옛 호출)



def test_ingest_candidates_skip_decided_off_checks():
    """결정으로 안 하기로 했거나 꺼 둔 검사(10/29 — 파라미터 파생 · 참고표 채팅)는 어긋난 수가 있어도 후보로 다시 띄우지 않는다."""
    results = [{"key": "policy_param_derived", "store": "정책", "name": "파라미터", "ref": "r", "bad": 981, "total": 981,
                "decided": "안 함 — 10/26 실험"},
               {"key": "knowledge_dup", "store": "지식", "name": "중복", "ref": "r", "bad": 10, "total": 102}]
    keys = [c["key"] for c in improve.ingest_candidates(results)]
    assert keys == ["ingest_knowledge_dup"]
