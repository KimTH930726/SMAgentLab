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


def test_explain_rules_fill_every_value_name():
    """설명 규칙의 {기준값 이름}은 전부 그 기법의 기준값이어야 한다 — 이름을 바꾸면 화면에 "{…}"가 그대로 보였을 것(10/30).
    AI 감사 로그 [기준] 모달에 나오는 판단은 예시·규칙 3줄을 갖는다."""
    import re
    for t in techniques.TECHNIQUES:
        for r in t.get("rules", []):
            for name in re.findall(r"\{([^}]+)\}", r):
                assert name in t["values"], (t["key"], name)
    rows = {r["key"]: r for r in techniques.as_rows()}
    for key in ("knowledge_location", "clarify", "ref_clarify", "source_check", "collection_suggest", "improve_recommend"):
        r = rows[key]
        assert r["example"] and r["example"]["lines"] and len(r["rules"]) == 3, key
        assert not any("{" in x for x in r["rules"]), key
    clar = next(t for t in techniques.TECHNIQUES if t["key"] == "clarify")   # 지금 기준값으로 채움(설정이 가짜여도 같은 값을 쓰는지)
    assert f"정책 {techniques.current_values(clar)['묻는 기준(정책 수 초과)']}개" in rows["clarify"]["rules"][0]


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


def test_ingest_fix_error():
    """데이터 고치기 후보(ingest_*) = 승인 뒤 적재 검사에서 그 검사 어긋남 0일 때만(11/04). 다른 후보는 해당 없음."""
    from datetime import datetime, timedelta, timezone
    t0 = datetime(2026, 11, 4, tzinfo=timezone.utc)
    run = lambda at, bad: {"created_at": at, "results": [{"key": "knowledge_dup", "name": "중복", "bad": bad}, {"key": "x", "bad": 3}]}
    assert improve.ingest_fix_error("retrieval_x", None, None) is None                      # 처리 기법 후보는 해당 없음
    assert improve.ingest_fix_error("ingest_knowledge_dup", run(t0 + timedelta(hours=1), 0), t0) is None
    assert "아직 10건" in improve.ingest_fix_error("ingest_knowledge_dup", run(t0 + timedelta(hours=1), 10), t0)
    assert "승인 뒤" in improve.ingest_fix_error("ingest_knowledge_dup", run(t0 - timedelta(hours=1), 0), t0)   # 승인 전 검사는 안 됨
    assert "승인 뒤" in improve.ingest_fix_error("ingest_knowledge_dup", None, t0)
    assert "승인 뒤" in improve.ingest_fix_error("ingest_knowledge_dup", run(t0, 0), None)
    assert "없습니다" in improve.ingest_fix_error("ingest_gone", run(t0 + timedelta(hours=1), 0), t0)
    failed = {"created_at": t0 + timedelta(hours=1), "results": [{"key": "knowledge_dup", "error": "UndefinedTable"}]}
    assert "끝내지 못했습니다" in improve.ingest_fix_error("ingest_knowledge_dup", failed, t0)   # 검사 실패 ≠ 어긋남 0(리뷰 M1)
    nobad = {"created_at": t0 + timedelta(hours=1), "results": [{"key": "knowledge_dup"}]}
    assert "끝내지 못했습니다" in improve.ingest_fix_error("ingest_knowledge_dup", nobad, t0)



def test_adopt_requires_confident_wrong_not_up_and_two_reps():
    """운영 반영 B안(10/30): 답을 잰 측정이면 단정 오답이 늘지 않아야 하고 2회 이상 — 검색만 잰 측정은 해당 없음."""
    t0 = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
    g = lambda cw: {"kind": "gate", "verdict": "pass", "run_at": t0 + timedelta(minutes=5), "answers": {"groups": {}, "confident_wrong": cw}}  # noqa: E731
    ok = {"before": 0.20, "after": 0.12, "n": 95, "reps_before": 2, "reps_after": 2}
    assert improve.adopt_error("approved", g(ok), t0) is None
    assert improve.adopt_error("approved", g({**ok, "after": 0.20}), t0) is None                 # 같으면 됨(검색 개선 등)
    assert "늘었습니다" in improve.adopt_error("approved", g({**ok, "after": 0.24}), t0)
    assert "1회만" in improve.adopt_error("approved", g({**ok, "reps_after": 1}), t0)
    assert "옛 측정" in improve.adopt_error("approved", g(None), t0)
    assert improve.adopt_error("approved", {**g(ok), "answers": None}, t0) is None              # 검색만 잰 측정


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


CW_OK = {"before": 0.1, "after": 0.1, "n": 10, "reps_before": 2, "reps_after": 2}   # 단정 오답 조건(10/30)은 통과하는 값


def test_adopt_blocked_when_locked_layer_drops():
    t0 = datetime(2026, 10, 26, 9, tzinfo=timezone.utc)
    ok = {"kind": "gate", "verdict": "pass", "run_at": t0 + timedelta(hours=1),
          "answers": {"groups": {"전체": {"diff": 0.01}, "human": {"diff": 0.0}, "real": {"diff": 0.02}}, "confident_wrong": CW_OK}}
    assert improve.adopt_error("approved", ok, t0) is None
    bad = {**ok, "answers": {"groups": {"전체": {"diff": 0.01}, "human": {"diff": -0.0125}}, "confident_wrong": CW_OK}}   # 10/23 #11과 같은 모양
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



def test_decided_off_comes_from_human_decision_not_code():
    """꺼 둠은 사람 결정(기각·보류)에서만 — 결정 없음·제안·시도 승인·채택은 꺼 둠이 아니고, 기능을 켜면 옛 결정이 있어도 다시 센다(10/29)."""
    dec = {"ingest_policy_param_derived": {"status": "rejected", "review_reason": "실험 #18·#19 효과 없음"},
           "ingest_usage_overview": {"status": "held", "review_reason": None},
           "ingest_usage_ref_table": {"status": "rejected", "review_reason": "부작용"},
           "ingest_knowledge_dup": {"status": "proposed", "review_reason": None}}
    assert improve.decided_off("policy_param_derived", dec) == "안 함 — 실험 #18·#19 효과 없음"
    assert improve.decided_off("usage_overview", dec) is None   # 보류(나중에)는 꺼 두지 않음 — 후보로 남아 이어받는다
    assert improve.decided_off("usage_ref_table", dec, feature_on=True) is None
    assert improve.decided_off("knowledge_dup", dec) is None
    assert improve.decided_off("policy_unique", dec) is None
    for st in ("approved", "adopted", "proposed", "held"):
        assert improve.decided_off("x", {"ingest_x": {"status": st, "review_reason": "r"}}) is None


def test_ingest_candidates_skip_decided_off_checks():
    """결정으로 안 하기로 했거나 꺼 둔 검사(10/29 — 파라미터 파생 · 참고표 채팅)는 어긋난 수가 있어도 후보로 다시 띄우지 않는다."""
    results = [{"key": "policy_param_derived", "store": "정책", "name": "파라미터", "ref": "r", "bad": 981, "total": 981,
                "decided": "안 함 — 10/26 실험"},
               {"key": "knowledge_dup", "store": "지식", "name": "중복", "ref": "r", "bad": 10, "total": 102}]
    keys = [c["key"] for c in improve.ingest_candidates(results)]
    assert keys == ["ingest_knowledge_dup"]


def test_ingest_candidates_split_by_axis():
    """적재 검사 후보는 축마다 자기 데이터만 — 지식 조각 검사 = 업무지식, 정책·참고표·개요 = 정책서. 축을 안 주면 전부(옛 호출). 합 = 전부(유실·중복 0)."""
    results = [{"key": k, "store": "s", "name": k, "ref": "r", "bad": 1, "total": 2}
               for k in ("knowledge_dup", "usage_ref_table", "policy_unique", "knowledge_heading")]
    pol = {c["key"] for c in improve.ingest_candidates(results, axis="policy")}
    kno = {c["key"] for c in improve.ingest_candidates(results, axis="knowledge")}
    every = {c["key"] for c in improve.ingest_candidates(results)}
    assert kno == {"ingest_knowledge_dup", "ingest_knowledge_heading"}
    assert pol == {"ingest_usage_ref_table", "ingest_policy_unique"}
    assert pol | kno == every and not pol & kno


def test_knowledge_axis_retrieval_miss_one_candidate_without_mapping_split():
    """업무지식 검색 측정엔 용어 매핑 칸이 없다 — 정책서식 갈래(용어·동의어 / 키워드 구제)를 만들지 않고 한 후보로. 문항은 그대로 다 덮는다."""
    d = {"causes": {"retrieval_miss": ["K1", "K2", "K3"]}}
    c = improve.build_candidates(d, types={}, mapped={}, axis="knowledge")
    assert [x["key"] for x in c] == ["knowledge_retrieval"]
    assert c[0]["qids"] == ["K1", "K2", "K3"]
    pol = improve.build_candidates(d, types={}, mapped={})
    assert "knowledge_retrieval" not in {x["key"] for x in pol}


def test_carry_not_allowed_when_feature_turned_on():
    """기능을 켠 적재 검사 후보는 옛 결정을 이어받지 않는다 — 켠 뒤 새로 어긋난 것을 사람에게 다시 묻는다(리뷰 10/29 M1). 다른 후보는 그대로 이어받음."""
    class S:
        policy_ref_chat = True
        policy_ref_overview = False
    assert improve.carry_allowed("ingest_usage_ref_table", S) is False
    assert improve.carry_allowed("ingest_usage_overview", S) is True
    assert improve.carry_allowed("ingest_policy_param_derived", S) is True
    assert improve.carry_allowed("synonyms", S) is True
    assert improve.feature_on("usage_ref_table", S) and not improve.feature_on("usage_overview", S)


def test_phase_of_covers_every_state():
    """화면 단계(10/30) — 실험이 끝났는데 '실험 중'으로 남지 않는다: 작업 진행 → 실험 중, 작업 실패 → 실험 오류,
    측정이 반영 가능 → 검토 대기 / 불가 → 실패. 종결 상태는 그대로. 모든 결과가 PHASES 안."""
    run, fail, done = {"status": "running"}, {"status": "failed"}, {"status": "done"}
    ok, bad = {"ok": True, "fresh": True}, {"ok": False, "fresh": True}
    p = improve.phase_of
    assert p("approved", run, ok) == "running"            # 다시 실행 중엔 예전 측정이 있어도 실험 중
    assert p("approved", {"status": "queued"}, None) == "running"
    assert p("approved", fail, None) == "error"
    assert p("approved", fail, ok) == "review"            # 예전 측정이 통과했으면 그걸로 결정 가능
    assert p("approved", done, ok) == "review" and p("approved", done, bad) == "failed"
    assert p("approved", None, None) == "running"         # 자동 실험이 안 되는 후보(개발 작업)
    for st in ("proposed", "adopted", "failed", "rejected", "held"):
        assert p(st, done, ok) == st
    for st in ("proposed", "approved", "adopted", "failed", "rejected", "held"):
        for j in (None, run, fail, done):
            for a in (None, ok, bad):
                assert p(st, j, a) in improve.PHASES


def test_failed_is_carried_and_counts_as_human_try():
    """실패(자동 종결)도 다음 진단이 이어받고([다시 실행]은 남음), 추천 일치 통계에선 사람의 '해 보자'로 센다."""
    prev = {"id": 9, "status": "failed", "recommend": "try"}
    assert improve.carry_decision(prev, "try") == prev
    assert improve.carry_decision(prev, "review") is None
    assert improve.agreement([{"recommend": "try", "status": "failed"}, {"recommend": "reject", "status": "failed"}]) == {"agree": 1, "disagree": 1}


def test_stale_measurement_is_not_failure():
    """예전 측정(승인 전 · 지금 점수보다 예전)은 반영 불가라도 '실패'가 아니다 — 다시 재면 되는 것(재리뷰 M1).
    새 측정만 검토 대기·실패를 가른다. 다시 실행이 장애로 멈추면 예전 측정이 있어도 실험 오류."""
    import datetime as dt
    t = lambda d: dt.datetime(2026, 10, d, tzinfo=dt.timezone.utc)   # noqa: E731
    f = improve.measurement_fresh
    assert f(t(20), t(19), t(18)) and f(t(20), t(20), None)
    assert not f(t(18), t(19), None)            # 승인보다 먼저 잰 측정(예전 진단에서 넘어옴)
    assert not f(t(20), t(19), t(21))           # 그 뒤 다른 후보 반영으로 기준선이 옮겨짐
    assert not f(t(20), None, None)
    stale_bad = {"ok": False, "fresh": False}
    assert improve.phase_of("approved", None, stale_bad) == "running"
    assert improve.phase_of("approved", {"status": "failed"}, stale_bad) == "error"
    assert improve.phase_of("approved", {"status": "done"}, {"ok": False, "fresh": True}) == "failed"
    assert improve.phase_of("approved", {"status": "done"}, {"ok": True, "fresh": False}) == "running"


def test_adopt_check_reads_json_run_at_with_offset():
    """서버 경로 — 측정 run_at은 jsonb에서 '+09:00' 오프셋이 붙은 글자로 온다. 시간대 있는 승인·기준선 시각과 비교가 되고(TypeError 없음)
    fresh·ok가 맞게 나온다(재리뷰 L2)."""
    import datetime as dt
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("eval_service_under_test", ROOT / "service" / "eval" / "service.py")
    svc = ilu.module_from_spec(spec)
    spec.loader.exec_module(svc)
    kst = dt.timezone(dt.timedelta(hours=9))
    c = {"status": "approved", "reviewed_at": dt.datetime(2026, 10, 30, 9, 0, tzinfo=kst), "measured_gate_run_ids": [5],
         "measurements": [{"id": 5, "run_at": "2026-10-30T10:00:00.123456+09:00", "kind": "gate", "verdict": "fail", "has_answers": False}]}
    chk = svc._adopt_check(c, dt.datetime(2026, 10, 29, tzinfo=dt.timezone.utc))
    assert chk["fresh"] is True and chk["ok"] is False and chk["gate_run_id"] == 5
    chk = svc._adopt_check({**c, "reviewed_at": dt.datetime(2026, 10, 30, 11, 0, tzinfo=kst)}, None)
    assert chk["fresh"] is False                     # 승인보다 먼저 잰 측정 — 실패 아님
