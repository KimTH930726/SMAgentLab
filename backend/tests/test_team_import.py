"""팀 적재(컨플 수집기, #98) — 순수 규칙: 팀 범위 후보 · 분류(자동/검토 필요 사유) · 묶음 · 표본 뽑기 · 확정 막는 것.
설계 confluence-curation-layer-design §12. 정확도 우선: 자동 분류는 지금 확신 기준만(묶음 다수 규칙은 10/29 실측 13건 중 틀림 3으로 기각)."""
import importlib.util as _ilu
import random
import sys
from pathlib import Path

from agents.knowledge_rag.ingestion import team_import as ti

# conftest가 service 패키지를 가짜로 둔다 — 원장 번복 규칙(ledger.location_outcome·override.is_override)은 진짜로(test_decision_log와 같은 방식)
_b = Path(__file__).resolve().parent.parent
for _name, _rel in (("service.decision.ledger", "service/decision/ledger.py"), ("service.eval.override", "service/eval/override.py")):
    if not hasattr(sys.modules.get(_name), "__file__"):
        _sp = _ilu.spec_from_file_location(_name, str(_b / _rel))
        _m = _ilu.module_from_spec(_sp)
        sys.modules[_name] = _m
        _sp.loader.exec_module(_m)
from agents.knowledge_rag.ingestion.locator import Candidate

BIZ = [Candidate(1, "딜리버스", "딜리버스", "domain"), Candidate(2, "온라인스토어", "온라인스토어", "domain"),
       Candidate(3, "쿠폰", "쿠폰", "domain"), Candidate(4, "회원", "회원", "domain")]
OWNERS = {1: {774}, 2: {775}, 4: {902}}        # 1·2 = CSP 파트, 4 = 다른 팀 파트, 3 = 담당 없음
CSP_PARTS = {774, 775}


class TestTeamScope:
    def test_other_team_areas_excluded_unowned_kept(self):
        scoped, unowned = ti.team_scope(BIZ, OWNERS, CSP_PARTS)
        assert [c.id for c in scoped] == [1, 2, 3]       # 회원(다른 팀 담당)은 후보에서 빠진다
        assert unowned == {3}

    def test_shared_area_with_other_team_kept(self):
        scoped, _ = ti.team_scope(BIZ, {**OWNERS, 4: {902, 775}}, CSP_PARTS)
        assert 4 in [c.id for c in scoped]               # 우리 팀 파트도 담당이면 후보

    def test_team_without_parts_gets_only_unowned(self):
        scoped, unowned = ti.team_scope(BIZ, OWNERS, set())
        assert [c.id for c in scoped] == [3] and unowned == {3}


class TestClassify:
    def test_confident_owned_is_auto(self):
        assert ti.classify(1, True, "", {3}) == ("auto", None, 1)

    def test_confident_but_unowned_goes_to_review(self):
        # 담당 파트는 AI가 정할 수 없는 정보 — 확신이어도 사람에게
        assert ti.classify(3, True, "", {3}) == ("review", "unowned", None)

    def test_not_confident_is_low_confidence(self):
        assert ti.classify(1, False, "", {3}) == ("review", "low_confidence", None)
        assert ti.classify(None, False, "", {3}) == ("review", "low_confidence", None)

    def test_llm_failure_is_flagged_not_auto(self):
        assert ti.classify(1, True, "refusal", set()) == ("review", "ai_failed", None)

    def test_auto_only_when_confident(self):
        """불변식: 어떤 입력이든 자동 분류면 확신·담당 있음·실패 없음이고 최종 위치 = AI 위치."""
        rnd = random.Random(7)
        for _ in range(500):
            ai = rnd.choice([None, 1, 2, 3])
            conf, fail = rnd.random() < 0.5, rnd.choice(["", "", "error"])
            st, reason, final = ti.classify(ai, conf, fail, {3})
            if st == "auto":
                assert conf and not fail and ai not in (None, 3) and final == ai and reason is None
            else:
                assert st == "review" and reason in ti.REASONS and final is None


class TestBundles:
    TREE = [{"page_id": "r", "title": "CSP", "depth": 0, "parent_id": None},
            {"page_id": "a", "title": "딜리버스 운영", "depth": 1, "parent_id": "r"},
            {"page_id": "a1", "title": "배치", "depth": 2, "parent_id": "a"},
            {"page_id": "a11", "title": "재실행", "depth": 3, "parent_id": "a1"},
            {"page_id": "b", "title": "팀 운영", "depth": 1, "parent_id": "r"}]

    def test_bundle_is_first_level_under_root(self):
        out = ti.bundles_of(self.TREE, "r")
        assert out["r"] == ("(최상위)", 0)
        assert out["a"] == ("딜리버스 운영", 1) and out["a1"][0] == "딜리버스 운영" and out["a11"] == ("딜리버스 운영", 3)
        assert out["b"] == ("팀 운영", 1)

    def test_every_page_gets_a_bundle(self):
        assert set(ti.bundles_of(self.TREE, "r")) == {t["page_id"] for t in self.TREE}

    def test_paths_invariants(self):
        """경로 = 수집 대상 아래 ~ 부모 제목. 모든 페이지에 하나 · 자식 경로 = 부모 경로 + 부모 제목 · 수집 대상과 그 바로 아래는 "" ·
        자기 제목·수집 대상 제목은 안 들어감(무작위 트리로)."""
        import random
        rnd = random.Random(7)
        for _ in range(50):
            tree = [{"page_id": "r", "title": "ROOT", "depth": 0, "parent_id": None}]
            for i in range(rnd.randint(1, 40)):
                par = rnd.choice(tree)
                tree.append({"page_id": f"p{i}", "title": f"T{i}", "depth": par["depth"] + 1, "parent_id": par["page_id"]})
            paths = ti.paths_of(tree, "r")
            by = {t["page_id"]: t for t in tree}
            assert set(paths) == set(by)
            for t in tree:
                p = paths[t["page_id"]]
                parts = p.split(ti.PATH_SEP) if p else []
                assert len(parts) == max(t["depth"] - 1, 0)
                assert "ROOT" not in parts and t["title"] not in parts
                if t["depth"] >= 2:
                    par = by[t["parent_id"]]
                    assert p == ti.PATH_SEP.join([x for x in [paths[par["page_id"]], par["title"]] if x])

    def test_paths_space_mode(self):
        """공간 주소(가상 뿌리) — 최상위 페이지는 "", 그 아래는 최상위 제목부터."""
        tree = ti.space_tree([{"id": "a", "title": "A", "ancestors": []},
                              {"id": "b", "title": "B", "ancestors": [{"id": "a"}]},
                              {"id": "c", "title": "C", "ancestors": [{"id": "a"}, {"id": "b"}]}])
        assert ti.paths_of(tree, ti.SPACE_ROOT) == {"a": "", "b": "A", "c": "A › B"}


class TestSamples:
    def test_k_per_collection_no_repeat_reproducible(self):
        auto = {1: list(range(100, 140)), 2: list(range(200, 205))}
        a = ti.pick_samples(auto, {}, 10, seed=5)
        assert a == ti.pick_samples(auto, {}, 10, seed=5)                  # 같은 배치 = 같은 표본
        assert len([x for x in a if x < 200]) == 10 and len([x for x in a if x >= 200]) == 5   # 적으면 전부
        assert len(a) == len(set(a))

    def test_second_draw_skips_already_sampled(self):
        auto = {1: list(range(30))}
        first = set(ti.pick_samples(auto, {}, 10, seed=1))
        second = ti.pick_samples(auto, {1: first}, 10, seed=2)
        assert len(second) == 10 and not (set(second) & first)

    def test_invariant_subset_of_pool(self):
        rnd = random.Random(3)
        for _ in range(200):
            # 페이지 줄 id는 배치 안에서 하나 — 컬렉션끼리 겹치지 않게
            auto = {ns: rnd.sample(range(ns * 1000, ns * 1000 + 1000), rnd.randint(0, 30)) for ns in range(rnd.randint(1, 5))}
            already = {ns: set(v[: rnd.randint(0, len(v))]) for ns, v in auto.items()}
            got = ti.pick_samples(auto, already, 10, seed=rnd.randint(0, 99))
            for ns, pool in auto.items():
                mine = set(got) & set(pool)
                assert mine <= set(pool) - already[ns] and len(mine) == min(10, len(set(pool) - already[ns]))


def _p(status, final=None, sample=None, ai=None):
    return {"status": status, "final_namespace_id": final, "sample_state": sample, "ai_namespace_id": ai if ai is not None else final}


class TestBlockers:
    def test_undecided_review_blocks(self):
        assert any("검토 필요 1건" in b for b in ti.blockers([_p("auto", 1), _p("review")], False))

    def test_held_review_does_not_block(self):
        assert ti.blockers([_p("auto", 1), _p("held")], False) == []

    def test_pending_sample_blocks_only_when_required(self):
        pages = [_p("auto", 1, "pending"), _p("auto", 1, "ok")]
        assert any("표본 검토 1건" in b for b in ti.blockers(pages, True))
        assert ti.blockers(pages, False) == []

    def test_holding_a_sample_cannot_skip_review(self):
        """리뷰 10/29 #4: 표본을 보류·제외로 빼면 남은 표본 0 → 확정되던 결함. 목표 = 자동 분류로 남은 페이지 수 기준."""
        pages = [_p("auto", 1) for _ in range(12)] + [_p("held", 1, "pending") for _ in range(10)]
        assert any("표본 검토 10건" in b for b in ti.blockers(pages, True))

    def test_wrong_doubles_target(self):
        pages = [_p("auto", 1, "ok") for _ in range(9)] + [_p("auto", 2, "wrong", ai=1)] + [_p("auto", 1) for _ in range(20)]
        assert any("표본 검토 10건" in b for b in ti.blockers(pages, True))   # 목표 20, 검토 10

    def test_blockers_empty_implies_invariants(self):
        """불변식: 확정 가능(막는 것 없음)이면 ① 검토 필요 미분류 0 ② 필수면 컬렉션마다 목표만큼 표본 판정 ③ 적재할 것 1건 이상.
        보류·제외·분류·표본 판정을 무작위로 섞어 본다(예시가 아니라 성질 — #4를 못 잡은 이유)."""
        rnd = random.Random(11)
        for _ in range(1500):
            pages = []
            for _ in range(rnd.randint(0, 40)):
                st = rnd.choice(["auto", "auto", "review", "held", "excluded", "existing"])
                ns = rnd.choice([1, 2, 3])
                final = ns if st == "auto" else (rnd.choice([None, ns]) if st == "review" else None)
                sample = rnd.choice([None, None, "pending", "ok", "wrong"]) if st in ("auto", "held", "excluded") else None
                pages.append(_p(st, final, sample, ai=ns))
            req = rnd.random() < 0.7
            if ti.blockers(pages, req):
                continue
            assert not any(p["status"] == "review" and p["final_namespace_id"] is None for p in pages)
            assert any(p["status"] in ti.LOADABLE and p["final_namespace_id"] is not None for p in pages)
            if req:
                counts, wrong, states = ti._sample_view(pages)
                for ns, target in ti.sample_targets(counts, wrong).items():
                    assert states.get(ns, {}).get("ok", 0) + states.get(ns, {}).get("wrong", 0) >= target

    def test_nothing_to_load_blocks(self):
        assert ti.blockers([_p("existing", 1), _p("excluded")], False) == ["적재할 페이지가 없음"]


class TestSpaceTree:
    def test_multi_root_space_each_root_is_bundle(self):
        pages = [{"id": "r1", "title": "운영", "ancestors": []}, {"id": "r2", "title": "개발", "ancestors": []},
                 {"id": "c1", "title": "배치", "ancestors": [{"id": "r1"}]},
                 {"id": "c2", "title": "재실행", "ancestors": [{"id": "r1"}, {"id": "c1"}]},
                 {"id": "x", "title": "밖 조상", "ancestors": [{"id": "other-space"}]}]
        tree = ti.space_tree(pages)
        b = ti.bundles_of(tree, ti.SPACE_ROOT)
        assert b["r1"][0] == "운영" and b["c1"][0] == "운영" and b["c2"] == ("운영", 3)
        assert b["r2"][0] == "개발" and b["x"][0] == "밖 조상"     # 조상이 공간 밖이면 최상위
        assert set(b) == {p["id"] for p in pages}


class TestSampleTargets:
    def test_target_capped_and_doubled(self):
        assert ti.sample_targets({1: 50, 2: 4, 3: 50}, {3}) == {1: 10, 2: 4, 3: 20}

    def test_pick_with_per_collection_need(self):
        auto = {1: list(range(50)), 2: list(range(100, 150))}
        got = ti.pick_samples(auto, {}, {1: 3, 2: 0}, seed=1)
        assert len(got) == 3 and all(x < 50 for x in got)


class TestTeamScopeProperty:
    def test_other_team_only_area_never_candidate(self):
        """불변식: 다른 팀 파트만 담당하는 업무는 어떤 담당 배치에서도 후보가 아니다. 담당 없는 업무는 늘 후보 + unowned."""
        rnd = random.Random(5)
        for _ in range(500):
            cands = [Candidate(i, f"u{i}", f"u{i}", "domain") for i in range(1, 15)]
            team = {rnd.randint(1, 6) for _ in range(rnd.randint(0, 3))}
            owners = {c.id: {rnd.randint(1, 12) for _ in range(rnd.randint(0, 2))} for c in cands}
            scoped, unowned = ti.team_scope(cands, owners, team)
            ids = {c.id for c in scoped}
            for c in cands:
                o = owners[c.id]
                if not o:
                    assert c.id in ids and c.id in unowned
                elif not (o & team):
                    assert c.id not in ids
                else:
                    assert c.id in ids and c.id not in unowned


class TestClassifyNotWritable:
    def test_confident_but_not_writable_goes_to_review(self):
        """재리뷰 10/29 #1: 같은 팀 다른 파트 담당 업무로 확신 분류되면 넣는 사람이 확정할 수 없다(403) — 자동이 아니라 검토 필요."""
        assert ti.classify(1, True, "", set(), frozenset({1})) == ("review", "not_writable", None)
        assert ti.classify(2, True, "", set(), frozenset({1})) == ("auto", None, 2)


class TestClassifyDefault:
    """공간 기본 위치(10/29): 페이지는 기본 위치로(사람 결정), AI는 예외만 — 확신하는데 다른 곳."""
    D = 2

    def c(self, ai, conf=False, fail="", denied=frozenset()):
        return ti.classify_default(ai, conf, fail, {3}, denied, self.D)

    def test_business_pages_go_to_default_as_human_decision(self):
        assert self.c(None) == ("auto", None, 2, "space_default")       # 판단 못 함
        assert self.c(3) == ("auto", None, 2, "space_default")          # 담당 없는 업무를 골랐어도
        assert self.c(1) == ("auto", None, 2, "space_default")          # 다른 업무(확신 아님)
        assert self.c(1, fail="error") == ("auto", None, 2, "space_default")

    def test_exceptions_go_to_review(self):
        assert self.c(1, conf=True) == ("review", "default_conflict", None, None)
        assert self.c(1, conf=True, denied=frozenset({1})) == ("review", "not_writable", None, None)

    def test_confident_same_is_ai_auto(self):
        assert self.c(2, conf=True) == ("auto", None, 2, "ai")

    def test_no_org_like_reason(self):
        """조직 컬렉션 폐지(11/02) — '조직 지식으로 보임' 사유는 없다(옛 줄은 #106이 low_confidence로)."""
        assert "org_like" not in ti.REASONS

    def test_no_default_is_plain_classify(self):
        assert ti.classify_default(1, True, "", set(), frozenset(), None) == ("auto", None, 1, "ai")
        assert ti.classify_default(1, False, "", set(), frozenset(), None) == ("review", "low_confidence", None, None)

    def test_property_auto_target_is_default_or_ai_confident(self):
        rnd = random.Random(9)
        for _ in range(1000):
            ai = rnd.choice([None, 1, 2, 3, 4])
            conf, fail = rnd.random() < 0.4, rnd.choice(["", "", "error"])
            denied = frozenset(x for x in (1, 4) if rnd.random() < 0.3)
            st, reason, final, by = ti.classify_default(ai, conf, fail, {3}, denied, self.D)
            if st == "auto":
                assert (by == "space_default" and final == self.D) or (by == "ai" and conf and not fail and final == ai and ai not in denied)
                assert final not in denied
            else:
                assert reason in ti.REASONS and final is None


class TestSampleKey:
    def test_default_and_ai_sampled_separately(self):
        pages = [{"status": "auto", "sample_state": None, "ai_namespace_id": 7, "assigned_by": "space_default", "sample_ns": 2}
                 for _ in range(30)] + [{"status": "auto", "sample_state": None, "ai_namespace_id": 2, "assigned_by": "ai", "sample_ns": 2}]
        counts, _, _ = ti._sample_view(pages)
        assert counts == {"space_default:2": 30, "ai:2": 1}
        assert ti.sample_targets(counts, set()) == {"space_default:2": 10, "ai:2": 1}


class TestLedgerView:
    """리뷰 10/29 M1·L1: 공간 기본값은 사람 결정 — 원장 preset으로 남아 번복·수락 통계에서 빠진다. 사람이 옮기면 주체는 사람."""

    def test_space_default_is_preset_not_override(self):
        # 기본 위치 2, AI 제안 7 → 예전엔 "AI 틀림(번복)"으로 셌다
        assert ti.ledger_view("space_default", False, 2, 2, 7, 7) == ("space_default", 2, False)
        assert ti.ledger_view("space_default", True, 2, 2, 7, 7) == ("space_default", 2, False)   # 표본 "맞음"도 그대로

    def test_override_rule_treats_space_default_as_preset(self):
        from service.eval.override import is_override
        by, preset, changed = ti.ledger_view("space_default", False, 2, 2, 7, 7)
        row = {"kind": "knowledge_location", "signals": {"preset_id": preset, "proposed_id": 7},
               "ai": {"namespace_id": 7}, "final": {"namespace_id": 2}}
        assert not is_override(row)
        assert is_override({**row, "signals": {"proposed_id": 7}})   # preset이 없으면(예전) 번복으로 셌다

    def test_human_moved_auto_page_counts_against_ai(self):
        assert ti.ledger_view("ai", True, 3, 2, 2, 2) == ("human", None, True)
        assert ti.ledger_view("space_default", True, 3, 2, 7, 7) == ("human", None, True)

    def test_ai_auto_kept_is_accept(self):
        assert ti.ledger_view("ai", False, 2, 2, 2, 2) == ("ai", None, False)

    def test_review_page_classified_by_human(self):
        assert ti.ledger_view(None, True, 4, None, 7, 7) == ("human", None, True)
        assert ti.ledger_view(None, True, 7, None, 7, 7) == ("human", None, False)


class TestBlockersMixedGroups:
    def test_property_mixed_default_and_ai_groups(self):
        """불변식(리뷰 10/29 빠진 테스트 2): 공간 기본값·AI 묶음이 섞이고 표본을 '틀림 → 다른 곳'으로 옮겨도, 막는 것이 없으면
        묶음마다 목표만큼 판정됐다. 옮긴 표본은 원래 묶음에 남는다(sample_ns)."""
        rnd = random.Random(21)
        for _ in range(1500):
            pages = []
            for _ in range(rnd.randint(1, 40)):
                by = rnd.choice(["ai", "space_default"])
                sns = rnd.choice([1, 2])
                st = rnd.choice(["auto", "auto", "auto", "held", "review"])
                sample = rnd.choice([None, None, "pending", "ok", "wrong"]) if st != "review" else None
                final = (rnd.choice([sns, 3]) if sample == "wrong" else sns) if st == "auto" else (rnd.choice([None, 1]) if st == "review" else None)
                pages.append({"status": st, "final_namespace_id": final, "sample_state": sample, "ai_namespace_id": rnd.choice([1, 2, 9]),
                              "assigned_by": by if st == "auto" else None, "sample_ns": sns if st == "auto" else None})
            if ti.blockers(pages, True):
                continue
            counts, wrong, states = ti._sample_view(pages)
            for key, target in ti.sample_targets(counts, wrong).items():
                assert states.get(key, {}).get("ok", 0) + states.get(key, {}).get("wrong", 0) >= target
            for p in pages:
                if p["status"] == "auto":
                    assert ti.sample_key(p) == f"{p['assigned_by']}:{p['sample_ns']}"


class TestLedgerViewOldRows:
    def test_old_auto_row_without_assigned_by_is_ai(self):
        """재리뷰 N3: v2.192 전 배치의 자동 분류(assigned_by 비어 있음)는 사람이 손대지 않았으면 AI."""
        assert ti.ledger_view(None, False, 2, None, 2, 2, was_auto=True) == ("ai", None, False)
        assert ti.ledger_view(None, True, 2, None, 2, 2, was_auto=True) == ("human", None, False)


def test_connect_rules_assign_every_page_once_by_bundle_and_group():
    """묶음 단위 연결(10/30) 불변식: 규칙이 있는 묶음의 모든 페이지는 정확히 한 곳(또는 제외)으로 — 하위 갈래 예외가 묶음 값보다 우선,
    갈래 예외는 그 갈래 페이지에만(다른 갈래·묶음 자신은 묶음 값). 무작위 트리로."""
    rnd = random.Random(7)
    for _ in range(30):
        bundles = [f"B{i}" for i in range(rnd.randint(1, 5))]
        pages = []
        for b in bundles:
            pages.append({"bundle": b, "depth": 1, "path": "", "title": b, "page_id": b})
            for g in range(rnd.randint(0, 4)):
                gname = f"{b}-G{g}"
                pages.append({"bundle": b, "depth": 2, "path": b, "title": gname, "page_id": gname})
                for k in range(rnd.randint(0, 5)):
                    d = rnd.randint(3, 5)
                    mid = [f"{gname}-m{j}" for j in range(d - 3)]
                    pages.append({"bundle": b, "depth": d, "path": ti.PATH_SEP.join([b, gname, *mid]), "title": f"{gname}-p{k}",
                                  "page_id": f"{gname}-p{k}-{d}"})
        rules = {}
        for b in bundles:
            groups = {g: rnd.choice([None, 11, 12]) for g in {ti.group_of(p) for p in pages if p["bundle"] == b} - {None} if rnd.random() < 0.4}
            rules[b] = {"ns": rnd.choice([None, 10]), "groups": groups}
        for p in pages:
            has, ns = ti.connect_target(p, rules)
            assert has
            g = ti.group_of(p)
            r = rules[p["bundle"]]
            if p["depth"] == 1:
                assert g is None and ns == r["ns"]                     # 묶음 자신은 묶음 값
            else:
                assert g == (p["title"] if p["depth"] == 2 else p["path"].split(ti.PATH_SEP)[1])
                assert ns == (r["groups"][g] if g in r["groups"] else r["ns"])
    assert ti.connect_target({"bundle": "없음", "depth": 1}, {}) == (False, None)   # 규칙 없는 묶음은 건드리지 않음



def test_ledger_view_rule_is_preset_not_override():
    """묶음 규칙으로 정한 페이지는 preset — AI 추천과 달라도 번복으로 안 센다(공간 기본값과 같은 규칙, 리뷰 10/30 M2)."""
    assert ti.ledger_view("rule", True, 2, 7, 7, 7) == ("rule", 2, False)     # AI는 7, 규칙은 2 — 번복 아님
    assert ti.ledger_view("rule", True, 7, 2, 7, 7) == ("rule", 7, False)     # sample_ns와 달라도 사람 번복으로 바뀌지 않음


def test_err_text_keeps_type_when_message_empty():
    # 11/02 #85: 메시지 없는 예외가 빈 사유("")로 저장돼 실패 원인을 알 수 없었다
    import asyncio
    assert ti._err_text(asyncio.TimeoutError()) == "TimeoutError"
    assert ti._err_text(ValueError("bad page")) == "ValueError: bad page"
    assert len(ti._err_text(RuntimeError("x" * 1000))) == 300


def test_warning_kind_groups_by_text_without_numbers():
    """품질 경고 문장 → (종류, 수) — 숫자만 다른 문장은 같은 종류(11/05: "겹친 곳 1곳"·"2곳"이 따로 세지던 것)."""
    from agents.knowledge_rag.ingestion import team_import as ti
    assert ti.warning_kind("이웃 청크와 같은 내용이 겹친 곳 1곳") == ("이웃 청크와 같은 내용이 겹친 곳", 1)
    assert ti.warning_kind("이웃 청크와 같은 내용이 겹친 곳 13곳") == ("이웃 청크와 같은 내용이 겹친 곳", 13)
    assert ti.warning_kind("다음 제목이 끝에 매달린 청크 1개") == ("다음 제목이 끝에 매달린 청크", 1)
    assert ti.warning_kind("빈 조각(내용 없음)") == ("빈 조각", 1)
