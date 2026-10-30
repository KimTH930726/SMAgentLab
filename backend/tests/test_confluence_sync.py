"""컨플 동기화(10/30) 순수 규칙 — 바뀐 페이지의 묶음 자리가 미리보기(bundles_of·paths_of)와 같은가(성질, 무작위 트리) · 전체 대조 안전장치 · 증분 질의."""
import random
import sys
import types
from datetime import datetime

sys.modules.setdefault("core.database", types.SimpleNamespace(get_conn=None))
from agents.knowledge_rag.ingestion import confluence_sync as cs  # noqa: E402
from agents.knowledge_rag.ingestion import team_import as ti  # noqa: E402


def _random_space(rnd):
    """공간 = 맨 위 페이지 여럿 아래 무작위 트리. 페이지마다 ancestors(맨 위부터) — 컨플 REST expand=ancestors 모양."""
    pages, anc = [], {}
    for i in range(rnd.randint(1, 4)):
        pid = f"T{i}"
        pages.append({"id": pid, "title": f"맨위{i}", "ancestors": []})
        anc[pid] = []
    for k in range(rnd.randint(5, 40)):
        parent = rnd.choice(pages)
        pid = f"P{k}"
        a = anc[parent["id"]] + [{"id": parent["id"], "title": parent["title"]}]
        pages.append({"id": pid, "title": f"페이지{k}", "ancestors": a})
        anc[pid] = a
    return pages


def test_place_matches_preview_rules_space_and_page_root():
    """불변식: 동기화가 새 페이지에 매기는 묶음·경로·깊이 = 미리보기(bundles_of·paths_of)가 매긴 값 — 다르면 새 페이지가 엉뚱한 묶음 규칙으로 간다."""
    rnd = random.Random(11)
    for _ in range(40):
        pages = _random_space(rnd)
        # 공간 전체
        tree = ti.space_tree(pages)
        b, pth = ti.bundles_of(tree, ti.SPACE_ROOT), ti.paths_of(tree, ti.SPACE_ROOT)
        for p in pages:
            got = cs.place(None, p["ancestors"], {"page_id": p["id"], "title": p["title"]})
            assert (got["bundle"], got["depth"], got["path"]) == (b[p["id"]][0], b[p["id"]][1], pth[p["id"]]), p
        # 페이지 아래(수집 대상 = 무작위 페이지 R) — R 아래 페이지만, 깊이는 R 기준
        root = rnd.choice(pages)
        rid = root["id"]
        sub = [p for p in pages if rid in [a["id"] for a in p["ancestors"]]]
        rtree = [{"page_id": rid, "title": root["title"], "depth": 0, "parent_id": None}] + [
            {"page_id": p["id"], "title": p["title"], "depth": len(p["ancestors"]) - [a["id"] for a in p["ancestors"]].index(rid),
             "parent_id": p["ancestors"][-1]["id"]} for p in sub]
        b, pth = ti.bundles_of(rtree, rid), ti.paths_of(rtree, rid)
        for p in sub:
            got = cs.place(rid, p["ancestors"], {"page_id": p["id"], "title": p["title"]})
            assert (got["bundle"], got["depth"], got["path"]) == (b[p["id"]][0], b[p["id"]][1], pth[p["id"]]), p
        outside = [p for p in pages if p["id"] != rid and p not in sub]
        for p in outside:
            assert cs.place(rid, p["ancestors"], {"page_id": p["id"], "title": p["title"]}) is None   # 범위 밖
        assert cs.place(rid, root["ancestors"], {"page_id": rid, "title": root["title"]}) is None     # 수집 대상 자신


def test_missing_pages_only_when_listing_complete():
    """전체 대조는 목록을 끝까지 받았을 때만 — 끊긴 목록으로 판단하면 멀쩡한 지식을 대량으로 내린다(업계 공통 교훈)."""
    assert cs.missing_pages(["1", "2", "3"], ["1"], complete=False) is None
    assert cs.missing_pages(["1", "2", "3"], ["1", "3", "9"], complete=True) == ["2"]
    assert cs.missing_pages([], ["1"], complete=True) == []


def test_cql_changed_scope_and_margin():
    t = datetime(2026, 10, 30, 6, 0)
    q = cs.cql_changed("GIFT", None, t)
    assert 'space = "GIFT"' in q and 'lastmodified >= "2026-10-30 05:00"' in q   # 1시간 앞에서부터(시간대·분 단위 여유)
    assert "ancestor = 123" in cs.cql_changed(None, "123", t)
    # 시간대 있는 값은 UTC로 바꿔 넘긴다(KST 15:00 = UTC 06:00 → 05:00부터)
    from datetime import timedelta, timezone
    kst = datetime(2026, 10, 30, 15, 0, tzinfo=timezone(timedelta(hours=9)))
    assert 'lastmodified >= "2026-10-30 05:00"' in cs.cql_changed("GIFT", None, kst)


def _ch(pid, title=None):
    return {"page_id": pid, "title": title or pid, "ancestors": [{"id": "R", "title": "루트"}]}


def test_retry_failed_capped_conflict_kept():
    """실패·충돌 페이지는 다음 증분에 다시 — 안 그러면 마지막 시각이 넘어가 그 변경이 영영 빠진다(리뷰 M2).
    실패는 MAX_RETRY회까지, 충돌은 사람이 풀 때까지. 성공하면 목록에서 빠진다."""
    retry = []
    for _ in range(cs.MAX_RETRY):
        retry = cs.next_retry(retry, [(_ch("F"), "failed"), (_ch("C"), "conflict"), (_ch("OK"), None)])
    assert {r["page_id"]: r["tries"] for r in retry} == {"F": cs.MAX_RETRY, "C": cs.MAX_RETRY}
    retry = cs.next_retry(retry, [(_ch("F"), "failed"), (_ch("C"), "conflict")])
    assert [r["page_id"] for r in retry] == ["C"]                       # 실패는 포기, 충돌은 남음
    assert cs.next_retry(retry, [(_ch("C"), None)]) == []                # 풀리면 빠짐
    # 이번 증분에 또 바뀐 페이지는 새 목록(조상·제목 최신)이 이기고, 나머지 재시도만 붙는다 — 유실·중복 0
    changes = [_ch("C", "새 제목"), _ch("N")]
    merged = cs.merge_retry(changes, [{**_ch("C"), "why": "conflict"}, {**_ch("F"), "why": "failed"}])
    assert [m["page_id"] for m in merged] == ["C", "N", "F"] and merged[0]["title"] == "새 제목"


def test_title_url_resolves_root(monkeypatch):
    """제목 주소(/display/KEY/제목)면 페이지 id로 확정 — 안 하면 증분이 공간 전체를 받고 묶음 자리도 공간 기준으로 틀린다(리뷰 M1)."""
    import asyncio
    import httpx

    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"id": 777}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            seen.update(params)
            return _Resp()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    src = cs.RestPatSource("https://wiki.example.com/display/GIFT/%EC%9A%B4%EC%98%81", "tok")
    assert src.root_id is None and src.title_hint
    asyncio.run(src.resolve())
    assert src.root_id == "777" and seen["spaceKey"] == "GIFT"
    assert "ancestor = 777" in cs.cql_changed(src.space_key, src.root_id, datetime(2026, 10, 30))


def test_auth_error_sees_wrapped_cause():
    """토큰 만료(401/403)는 '중단' — 크롤러가 ValueError로 감싸도 원인까지 본다. 500은 '오류'(재시도)."""
    import httpx
    req = httpx.Request("GET", "https://w/rest/api/content/1403")

    def err(code):
        return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req))

    try:
        raise ValueError("페이지 트리 실패") from err(401)
    except ValueError as e:
        assert cs._auth_error(e)
    assert cs._auth_error(err(403)) and not cs._auth_error(err(500)) and not cs._auth_error(RuntimeError("401"))
