"""팀 적재(컨플 수집기, #98) — 순수 규칙: 팀 범위 후보 · 분류(자동/검토 필요 사유) · 묶음 · 표본 뽑기 · 확정 막는 것.
설계 confluence-curation-layer-design §12. 정확도 우선: 자동 분류는 지금 확신 기준만(묶음 다수 규칙은 10/29 실측 13건 중 틀림 3으로 기각)."""
import random

from agents.knowledge_rag.ingestion import team_import as ti
from agents.knowledge_rag.ingestion.locator import Candidate

BIZ = [Candidate(1, "딜리버스", "딜리버스", "domain"), Candidate(2, "온라인스토어", "온라인스토어", "domain"),
       Candidate(3, "쿠폰", "쿠폰", "domain"), Candidate(4, "회원", "회원", "domain")]
ORG = [Candidate(10, "org:team:1", "팀 공통 · CSP", "org_space", team_id=1, is_team_common=True, short="팀 공통")]
OWNERS = {1: {774}, 2: {775}, 4: {902}}        # 1·2 = CSP 파트, 4 = 다른 팀 파트, 3 = 담당 없음
CSP_PARTS = {774, 775}


class TestTeamScope:
    def test_other_team_areas_excluded_unowned_kept(self):
        scoped, unowned = ti.team_scope(BIZ + ORG, OWNERS, CSP_PARTS)
        assert [c.id for c in scoped] == [1, 2, 3, 10]   # 회원(다른 팀 담당)은 후보에서 빠진다
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
