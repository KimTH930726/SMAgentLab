"""팀 적재(컨플 수집기) — 팀·파트 컨플 공간을 통째로: 수집 → 분류 미리보기 → 분류 확정·적재 → 게이트 → (나빠지면) 롤백.
설계: docs/domains/knowledge/sources/confluence/confluence-curation-layer-design.md §12 (10/29 태훈 결정).

원칙 — 정확도가 먼저, AI는 그 길을 줄일 뿐:
- 자동 분류는 지금 기준(LLM과 이웃 5중 4 일치, locator.is_confident)을 넘은 것만. 10/29 실측: "묶음 다수 의견" 규칙은 13건 중 틀림 3 → 기각
  (eval_out/confl_diag/bundle_rule_probe.py). 나머지는 전부 검토 필요(늘어도 됨) — 사람이 AI 제안별로 묶어 분류한다.
- 업무 후보는 등록하는 사람의 팀 안으로(팀 파트가 담당하는 업무 + 담당 없는 업무). 다른 팀 업무로는 안 간다(GIFT 실측: 이웃이 영업시스템으로 샘).
  담당 없는 업무를 AI가 고르면 "담당 미지정" — 시스템에 없는 정보라 사람이 담당을 정하거나 보류.
- 표본 검토: 자동 분류도 컬렉션마다 무작위 SAMPLE_SIZE건을 사람이 확인(Amazon A2I 무작위 표본 감사). 팀 첫 적재는 게시 전 필수,
  그 팀의 바로 앞 적재 배치가 표본 정정 0이었으면 선택(B안). 정정이 나오면 그 컬렉션 목표를 2배로 올리고 필수로 바꾼다.
- 이미 사용 중인 페이지(다른 경로로 넣은 것)는 "이미 적재됨"으로 두고 다시 넣지 않는다 — 미리보기 때와 적재 직전 두 번 본다
  (사람이 정한 위치를 덮지 않고, 롤백이 남의 조각을 deprecated로 남기지 않게).
원문은 원장(rag_source_document)에만, 이 표엔 제목·id·컬렉션 id·AI 이유 한 문장(요약)만.
"""
import asyncio
import json
import re
import logging
import random
from typing import Optional

from agents.knowledge_rag.ingestion import locator
from agents.knowledge_rag.ingestion.locator import Candidate

logger = logging.getLogger(__name__)

SAMPLE_SIZE = 10
MAX_PAGES = 500                 # 한 배치 수집 상한(web_crawler.TREE_HARD_MAX_PAGES와 같음) — 넘으면 truncated
CONCURRENCY = 4                 # 페이지 동시 처리(본문 받기 + 로컬 LLM 판정) — 로컬 LLM에 한꺼번에 몰리지 않게
REASONS = {"low_confidence": "신뢰도 낮음", "unowned": "담당 미지정", "not_writable": "담당 아님", "ai_failed": "AI 판정 실패",
           "default_conflict": "기본 위치와 다름"}   # 옛 'org_like'(조직 지식으로 보임)는 조직 컬렉션 폐지로 없앰 — 남은 줄은 #106이 low_confidence로
DECIDABLE = ("auto", "review")  # 사람이 분류를 바꿀 수 있는 상태
LOADABLE = ("auto", "review")
ACTIVE_KNOWLEDGE = ("active", "pending_review")
HELD_KNOWLEDGE = ("staging", "staging_review")   # 게시 전(보류 작업) 조각 — 검색에 안 나온다


# ─── 순수 함수(테스트 대상) ────────────────────────────────────────────────────

def _err_text(e: BaseException) -> str:
    """페이지 실패 사유 — 메시지 없는 예외(시간 초과 등)도 종류는 남긴다(11/02 #85 실패 1건이 빈 사유로 저장됨)."""
    return (f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)[:300]


def team_scope(cands: list[Candidate], owners: dict[int, set[int]], team_parts: set[int]) -> tuple[list[Candidate], set[int]]:
    """팀 적재 후보 — 그 팀 파트가 담당하는 컬렉션 + 담당 없는 컬렉션(→ 담당 미지정), 다른 팀만 담당하는 곳은 뺀다.
    → (후보, 담당 없는 컬렉션 id)."""
    out, unowned = [], set()
    for c in cands:
        o = owners.get(c.id) or set()
        if not o:
            out.append(c)
            unowned.add(c.id)
        elif o & team_parts:
            out.append(c)
    return out, unowned


def classify(ai_id: Optional[int], confident: bool, llm_fail: str, unowned: set[int],
             denied: frozenset = frozenset()) -> tuple[str, Optional[str], Optional[int]]:
    """판정 한 건 → (상태, 사유, 자동 최종 위치). 확신이어도 담당 없는 업무면 사람에게(담당은 AI가 정할 수 없는 정보),
    넣는 사람이 쓸 수 없는 곳(같은 팀 다른 파트 담당)이면 사람에게 — 자동으로 두면 확정이 403으로 막혔다(재리뷰 10/29)."""
    if llm_fail:
        return "review", "ai_failed", None
    if ai_id is not None and ai_id in unowned:
        return "review", "unowned", None
    if ai_id is not None and ai_id in denied:
        return "review", "not_writable", None
    if confident and ai_id is not None:
        return "auto", None, ai_id
    return "review", "low_confidence", None


def classify_default(ai_id: Optional[int], confident: bool, llm_fail: str, unowned: set[int], denied: frozenset,
                     default_ns: Optional[int]) -> tuple[str, Optional[str], Optional[int], Optional[str]]:
    """공간 기본 위치(사람이 "이 공간 = 온라인스토어"를 한 번 정함, 10/29)가 있을 때 → (상태, 사유, 최종 위치, 누가 정함).
    페이지는 기본 위치로(사람 결정 — 표본 검토로 확인), AI는 예외만: 확신하는데 기본 위치와 다른 곳이면 검토 필요.
    확신 + 기본 위치와 같음 = AI 자동. 기본 위치가 없으면 classify."""
    if default_ns is None:
        st, reason, final = classify(ai_id, confident, llm_fail, unowned, denied)
        return st, reason, final, ("ai" if st == "auto" else None)
    if not llm_fail and confident and ai_id is not None:
        if ai_id == default_ns:
            return "auto", None, ai_id, "ai"
        if ai_id in denied:
            return "review", "not_writable", None, None
        return "review", "default_conflict", None, None
    return "auto", None, default_ns, "space_default"


def ledger_view(assigned_by: Optional[str], decided: bool, final: int, sample_ns: Optional[int],
                ai_ns: Optional[int], proposed_ns: Optional[int], was_auto: bool = False) -> tuple[str, Optional[int], bool]:
    """적재 때 원장 한 줄 → (누가 정했나, preset_id, 번복인가). 공간 기본값은 사람 결정이라 preset으로 남겨 번복·수락 통계에서 뺀다
    (ledger.location_outcome·override.is_override가 preset이면 AI 추천이 없다고 본다 — 리뷰 10/29 M1). 사람이 자동 분류를 다른 곳으로
    옮겼으면(최종 ≠ 정해질 때 곳) 주체는 사람(L1) — 그땐 AI 추천과 비교해 번복을 센다."""
    from service.decision.ledger import location_outcome
    moved = decided and sample_ns is not None and final != sample_ns
    if assigned_by is None and was_auto and not decided:
        assigned_by = "ai"   # v2.192 전에 만든 배치의 자동 분류(칸이 비어 있음) — 사람이 손대지 않았으면 AI(재리뷰 N3)
    # 묶음 규칙(10/30 연결)은 묶음 하나에 한 번 정한 사람 결정 — 공간 기본값과 같이 preset, 페이지마다 옮긴 게 아니라 사람 번복으로 세지 않는다(리뷰 M2)
    by = "rule" if assigned_by == "rule" else "human" if (assigned_by is None or moved) else assigned_by
    preset = final if by in ("space_default", "rule") else None
    changed, _ = location_outcome(None if ai_ns is None else str(ai_ns), None if proposed_ns is None else str(proposed_ns), final, None,
                                  preset_ns=None if preset is None else str(preset))
    return by, preset, changed


def bundles_of(tree: list[dict], root_id: str) -> dict[str, tuple[str, int]]:
    """페이지 → (묶음 = 수집 대상 바로 아래 페이지 제목, 깊이). 수집 대상 자신은 "(최상위)"."""
    by_id = {t["page_id"]: t for t in tree}
    out: dict[str, tuple[str, int]] = {}
    for t in tree:
        pid, depth, cur = t["page_id"], int(t.get("depth") or 0), t
        if pid == root_id or depth == 0:
            out[pid] = ("(최상위)", 0)
            continue
        guard = 0
        while cur.get("parent_id") not in (None, root_id) and cur.get("parent_id") in by_id and guard < 50:
            cur = by_id[cur["parent_id"]]
            guard += 1
        out[pid] = (cur.get("title") or cur["page_id"], depth)
    return out


PATH_SEP = " › "


def group_of(page: dict) -> Optional[str]:
    """묶음 안 하위 갈래 이름(2단계 상위 페이지 제목) — 묶음(1단계) 자신은 None. 경로(path) = 수집 대상 아래 조상 제목들(자기 제외).
    2단계 페이지는 자기 제목이 갈래, 3단계 이상은 경로의 둘째 칸."""
    depth = int(page.get("depth") or 0)
    if depth <= 1:
        return None
    parts = (page.get("path") or "").split(PATH_SEP) if page.get("path") else []
    return parts[1] if len(parts) >= 2 else (page.get("title") or page.get("page_id"))


def connect_target(page: dict, rules: dict[str, dict]) -> tuple[bool, Optional[int]]:
    """연결 규칙 적용(10/30 묶음 단위 연결) — rules = 묶음 이름 → {"ns": id|None, "groups": {갈래: id|None}}.
    돌려줌 (규칙이 있나, 넣을 곳 — None이면 제외). 갈래 예외가 묶음 값보다 우선. 묶음 규칙이 없으면 (False, None) = 건드리지 않음."""
    r = rules.get(page.get("bundle") or "")
    if r is None:
        return False, None
    g = group_of(page)
    groups = r.get("groups") or {}
    if g is not None and g in groups:
        return True, groups[g]
    return True, r.get("ns")


def paths_of(tree: list[dict], root_id: str) -> dict[str, str]:
    """페이지 → 경로(수집 대상 아래 ~ 바로 위 페이지 제목, " › "로). 자기 자신·수집 대상은 넣지 않는다 — 수집 대상 바로 아래는 "".
    화면에서 사람이 판단하고 같은 상위끼리 묶어 보는 데만 쓴다. AI 입력엔 안 넣음(10/29 실측 path_probe: GIFT 자동인데 틀림 1→10)."""
    by_id = {t["page_id"]: t for t in tree}
    out: dict[str, str] = {}
    for t in tree:
        chain, cur, guard = [], t, 0
        while cur.get("parent_id") not in (None, root_id) and cur.get("parent_id") in by_id and guard < 50:
            cur = by_id[cur["parent_id"]]
            chain.append(cur.get("title") or cur["page_id"])
            guard += 1
        out[t["page_id"]] = PATH_SEP.join(reversed(chain))
    return out


SPACE_ROOT = "__space__"   # 공간 주소(최상위 페이지가 여럿)일 때 트리의 가상 뿌리 — 최상위 페이지 하나하나가 묶음


def space_tree(pages: list[dict]) -> list[dict]:
    """공간 전체 페이지(조상 목록 포함) → bundles_of가 쓰는 트리 모양. 조상이 공간 밖(목록에 없음)이면 최상위로 본다."""
    ids = {p["id"] for p in pages}
    out = []
    for p in pages:
        anc = [a["id"] for a in (p.get("ancestors") or []) if a["id"] in ids]
        out.append({"page_id": p["id"], "title": p.get("title"), "depth": len(anc) + 1,
                    "parent_id": anc[-1] if anc else SPACE_ROOT})
    return out


def sample_targets(auto_counts: dict[str, int], wrong_ns: set[str]) -> dict[str, int]:
    """표본 묶음(sample_key)마다 목표 — SAMPLE_SIZE, 정정이 나온 묶음은 2배. 자동 분류 수보다 많을 수 없다."""
    return {ns: min(n, SAMPLE_SIZE * (2 if ns in wrong_ns else 1)) for ns, n in auto_counts.items()}


def pick_samples(auto_by_ns: dict, already: dict, k, seed: int) -> list[int]:
    """묶음마다 아직 안 뽑힌 자동 분류 페이지에서 무작위 k건(k = 숫자 또는 묶음별 dict, 묶음 키 = sample_key 또는 컬렉션 id).
    배치 id 기반 시드라 다시 불러도 같은 표본."""
    rnd = random.Random(seed)
    out: list[int] = []
    for ns in sorted(auto_by_ns):
        pool = sorted(set(auto_by_ns[ns]) - already.get(ns, set()))
        rnd.shuffle(pool)
        out.extend(pool[:max(0, k.get(ns, 0) if isinstance(k, dict) else k)])
    return out


def sample_key(p: dict) -> Optional[str]:
    """표본 묶음 — (누가 정했나, 정해진 곳). AI 자동과 공간 기본값은 따로 검사한다(같은 컬렉션이어도 검사하는 판단이 다르다).
    정해진 곳은 정해질 때 값(sample_ns) — 사람이 표본을 "틀림 → 다른 곳"으로 바꿔도 그 표본은 원래 판단 묶음에 남는다."""
    ns = p.get("sample_ns") or p.get("ai_namespace_id")
    return None if ns is None else f"{p.get('assigned_by') or 'ai'}:{ns}"


def _sample_view(pages: list[dict]) -> tuple[dict[str, int], set[str], dict[str, dict[str, int]]]:
    """(표본 묶음별 자동 분류 수, 정정이 나온 묶음, 묶음별 표본 상태 수). 묶음 = sample_key."""
    counts: dict[str, int] = {}
    states: dict[str, dict[str, int]] = {}
    wrong: set[str] = set()
    for p in pages:
        ns = sample_key(p)
        if p["sample_state"] == "wrong" and ns is not None:
            wrong.add(ns)
        if p["status"] != "auto" or ns is None:
            continue
        counts[ns] = counts.get(ns, 0) + 1
        if p["sample_state"]:
            states.setdefault(ns, {}).setdefault(p["sample_state"], 0)
            states[ns][p["sample_state"]] += 1
    return counts, wrong, states


def blockers(pages: list[dict], sampling_required: bool) -> list[str]:
    """분류 확정을 막는 것 — 검토 필요 미분류, (필수일 때) 컬렉션마다 표본 목표만큼 검토 안 됨. 빈 목록이면 확정 가능.
    표본은 자동 분류로 남은 페이지에서만 센다 — 표본을 보류·제외로 빼서 검토를 건너뛸 수 없다(리뷰 10/29)."""
    out = []
    undecided = sum(1 for p in pages if p["status"] == "review" and p["final_namespace_id"] is None)
    if undecided:
        out.append(f"검토 필요 {undecided}건 — 분류하거나 보류")
    if sampling_required:
        counts, wrong, states = _sample_view(pages)
        targets = sample_targets(counts, wrong)
        left = sum(max(targets[ns] - states.get(ns, {}).get("ok", 0) - states.get(ns, {}).get("wrong", 0), 0) for ns in targets)
        if left:
            out.append(f"표본 검토 {left}건 남음 (필수)")
    if not any(p["status"] in LOADABLE and p["final_namespace_id"] is not None for p in pages):
        out.append("적재할 페이지가 없음")
    return out


# ─── DB ────────────────────────────────────────────────────────────────────────

async def sampling_required_for(conn, team_id: int, exclude_batch: Optional[int] = None) -> bool:
    """B안 — 그 팀의 바로 앞 적재 배치(가장 최근 loaded)가 표본 검토를 거쳐 정정 0이면 선택, 아니면(첫 적재·정정 있었음) 필수."""
    last = await conn.fetchval("SELECT id FROM rag_import_batch WHERE team_id = $1 AND status = 'loaded' AND id IS DISTINCT FROM $2 "
                               "ORDER BY loaded_at DESC NULLS LAST, id DESC LIMIT 1", team_id, exclude_batch)
    if last is None:
        return True
    ok, wrong = await conn.fetchrow("SELECT count(*) FILTER (WHERE sample_state = 'ok'), count(*) FILTER (WHERE sample_state = 'wrong') "
                                    "FROM rag_import_page WHERE batch_id = $1", last)
    return not (ok > 0 and wrong == 0)


async def team_context(conn, team_id: int, *, profiles: bool = False) -> dict:
    """후보·담당·초안을 둘 곳(숨긴 보관 자리, #105). profiles = 대표 용어(LLM 판정에만 — 화면 폴링에선 안 읽음)."""
    from core.database import OWNER_ROWS_SQL, STAGING_NAME
    cands = await locator.load_candidates(conn)
    owners: dict[int, set[int]] = {}
    for r in await conn.fetch(f"SELECT namespace_id, part_id FROM {OWNER_ROWS_SQL} o"):
        owners.setdefault(r["namespace_id"], set()).add(r["part_id"])
    team_parts = {r["id"] for r in await conn.fetch("SELECT id FROM ops_part WHERE team_id = $1", team_id)}
    scoped, unowned = team_scope(cands, owners, team_parts)
    # 원문 초안·읽기 제한 기록은 숨긴 보관 자리에(#105)
    draft_ns = await conn.fetchval("SELECT id FROM ops_namespace WHERE name = $1", STAGING_NAME)
    return {"cands": scoped, "unowned": unowned, "draft_ns": draft_ns, "owners": owners, "team_parts": team_parts,
            "profiles": await locator.load_profiles(conn, [c.id for c in scoped]) if profiles else {}}


def denied_ids(ctx: dict, user: dict) -> frozenset:
    """이 사람이 쓸 수 없는 곳 — 분류 미리보기·기본 위치 다시 분류 공통."""
    from core.dependencies import edit_denied_reason
    return frozenset(c.id for c in ctx["cands"] if edit_denied_reason(ctx["owners"].get(c.id, set()), user) is not None)


def allowed_ids(ctx: dict) -> set[int]:
    """이 팀 배치가 분류해 넣을 수 있는 곳 — 팀 범위 후보(team_scope)."""
    return {c.id for c in ctx["cands"]}


async def _set_page(conn, row_id: int, **cols) -> None:
    # 칸 이름은 이 모듈의 상수뿐(사용자 입력 아님), 값은 파라미터
    sets = ", ".join(f"{k} = ${i + 2}" + ("::jsonb" if k == "signals" else "") for i, k in enumerate(cols))
    vals = [json.dumps(v, ensure_ascii=False) if k == "signals" else v for k, v in cols.items()]
    await conn.execute(f"UPDATE rag_import_page SET {sets} WHERE id = $1", row_id, *vals)


async def _existing_ns(conn, page_id: str) -> Optional[int]:
    """그 페이지 조각이 이미 사용 중·승인 대기·검수 대기(다른 배치의 게시 전)로 있는 컬렉션(가장 많은 곳) — 없으면 None.
    검수 대기도 센다: 안 세면 같은 페이지가 두 배치에 들어가 나중 게시가 영영 실패하거나 두 컬렉션에 중복(재리뷰 M)."""
    return await conn.fetchval(
        "SELECT k.namespace_id FROM rag_knowledge k WHERE k.confluence_page_id = $1 AND (k.status = ANY($2::text[]) OR "
        "(k.status = ANY($3::text[]) AND EXISTS (SELECT 1 FROM rag_ingestion_job j WHERE j.id = k.ingestion_job_id AND j.status = 'held'))) "
        "GROUP BY k.namespace_id ORDER BY count(*) DESC LIMIT 1", str(page_id), list(ACTIVE_KNOWLEDGE), list(HELD_KNOWLEDGE))


async def run_preview(batch_id: int, user: dict, token: str, base_url: str) -> None:
    """분류 미리보기(백그라운드) — 트리 → 페이지마다 본문·읽기 제한·분할·원장 초안·이미 적재됨·위치 판정 → 표본 뽑기.
    아무것도 적재하지 않는다(원장 초안은 'draft' — 검색에 안 쓰임)."""
    from core.database import get_conn
    try:
        await _run_preview(batch_id, user, token, base_url)
    except Exception as e:   # noqa: BLE001 — 배치를 실패로 남겨 화면이 멈춘 채 돌지 않게
        logger.exception("팀 적재 미리보기 실패 batch=%s", batch_id)
        async with get_conn() as conn:
            # 메시지 없는 예외(시간 초과 등)도 종류는 남긴다 — 11/03 앱 공간 미리보기가 빈 사유로 실패해 화면에 이유가 안 보였다
            await conn.execute("UPDATE rag_import_batch SET status = 'failed', error = $2 WHERE id = $1", batch_id, _err_text(e))


async def fetch_space_pages(base_url: str, space_key: str, token: str, max_pages: int = MAX_PAGES) -> tuple[str, list[dict], bool]:
    """공간 주소(/display/KEY) — 공간의 모든 페이지(조상 포함). (공간 이름, 트리, 잘림 여부). GIFT처럼 최상위가 여럿인 공간용.
    다음 쪽은 응답의 _links.next로 넘긴다(서버가 한 쪽 크기를 더 작게 잘라도 조용히 끊기지 않게, 리뷰 10/29)."""
    import httpx
    from agents.knowledge_rag.ingestion.web_crawler import guarded_client
    h = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    pages: list[dict] = []
    start = 0
    # 공간 목록은 쪽마다 조상까지 펼쳐 느리다(11/03 앱 공간: 30초 안에 첫 쪽이 안 옴) — 쪽 크기를 줄이고 읽기 대기를 늘린다
    async with guarded_client(verify=False, timeout=httpx.Timeout(30.0, read=180.0)) as cl:
        sp = await cl.get(f"{base_url}/rest/api/space/{space_key}", headers=h)
        sp.raise_for_status()
        while len(pages) <= max_pages:
            r = await cl.get(f"{base_url}/rest/api/content", headers=h,
                             params={"spaceKey": space_key, "type": "page", "limit": 100, "start": start, "expand": "ancestors"})
            r.raise_for_status()
            j = r.json()
            res = j.get("results", [])
            pages += res
            if not res or not (j.get("_links") or {}).get("next"):
                break
            start += len(res)
    return sp.json().get("name") or space_key, space_tree(pages[:max_pages]), len(pages) > max_pages


async def _run_preview(batch_id: int, user: dict, token: str, base_url: str) -> None:
    from core.database import get_conn
    from core.dependencies import edit_denied_reason
    from core.security import get_user_llm_credentials
    from agents.knowledge_rag.ingestion.web_crawler import _parse_confluence_url, fetch_confluence_by_id, fetch_confluence_tree
    from agents.knowledge_rag.ingestion.chunker import chunk_document
    from agents.knowledge_rag.ingestion.source_ledger import hold_restricted_chunks, record_restricted, save_draft_for_parsed
    from agents.knowledge_rag.knowledge.locate import _locator_llm

    async with get_conn() as conn:
        b = await conn.fetchrow("SELECT team_id, root_url, default_namespace_id, include_restricted_at IS NOT NULL AS include_restricted "
                                "FROM rag_import_batch WHERE id = $1", batch_id)
        ctx = await team_context(conn, b["team_id"], profiles=True)
    if ctx["draft_ns"] is None:   # 초안·제한 기록을 둘 곳이 없으면 확정 때 전부 실패한다 — 처음에 실패로 보이게(리뷰 10/29)
        raise ValueError("원문 초안 보관 자리(stage:import)가 없어요 — 백엔드를 재시작하면 기동 마이그레이션이 만듭니다.")
    _, page_id, space_key, title_hint = _parse_confluence_url(b["root_url"])
    if page_id or title_hint:   # 페이지 주소 — 그 페이지 아래 트리
        t = await fetch_confluence_tree(b["root_url"], token, max_depth=10, max_pages=MAX_PAGES)
        root_title, pages_tree = t["root"].get("title"), t["tree"]
        truncated = bool(t.get("truncated") or t.get("max_depth_reached"))
        bundle = bundles_of(pages_tree, t["root"]["page_id"])
        path = paths_of(pages_tree, t["root"]["page_id"])
    else:                       # 공간 주소 — 공간 전체(최상위 페이지가 여럿일 수 있다)
        root_title, pages_tree, truncated = await fetch_space_pages(base_url, space_key, token)
        bundle = bundles_of(pages_tree, SPACE_ROOT)
        path = paths_of(pages_tree, SPACE_ROOT)
    async with get_conn() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE rag_import_batch SET root_title = $2, total_pages = $3, truncated = $4 WHERE id = $1",
                               batch_id, root_title, len(pages_tree), truncated)
            await conn.executemany(
                "INSERT INTO rag_import_page (batch_id, page_id, title, bundle, depth, path) VALUES ($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT (batch_id, page_id) DO NOTHING",
                [(batch_id, t["page_id"], t.get("title"), bundle[t["page_id"]][0], bundle[t["page_id"]][1], path[t["page_id"]])
                 for t in pages_tree])
        rows = await conn.fetch("SELECT id, page_id FROM rag_import_page WHERE batch_id = $1 AND status = 'queued'", batch_id)

    llm, model_tag, uses_creds = _locator_llm()
    creds = get_user_llm_credentials(user) if uses_creds else None
    sem = asyncio.Semaphore(CONCURRENCY)
    team_id = b["team_id"]
    denied = denied_ids(ctx, user)

    async def one(row) -> None:
        async with sem:
            try:
                await _preview_page(row["id"], row["page_id"])
            except Exception as e:   # noqa: BLE001 — 한 페이지 실패가 배치를 멈추지 않게(화면에 실패로)
                logger.warning("팀 적재: 페이지 처리 실패 batch=%s page=%s: %s", batch_id, row["page_id"], e)
                async with get_conn() as conn:
                    await _set_page(conn, row["id"], status="failed", reason=None, signals={"error": _err_text(e)})
            async with get_conn() as conn:
                await conn.execute("UPDATE rag_import_batch SET done_pages = done_pages + 1 WHERE id = $1", batch_id)

    async def _hold_existing_restricted(conn, page_id: str) -> int:
        """제한 페이지가 이미 다른 곳에 적재돼 있으면 — 그 컬렉션 담당이면 조각을 승인 대기로 내린다(지식 문서 일괄 미리보기와 같은 규칙).
        미리보기에서 바뀌는 유일한 것(보안 — 제한된 원문이 검색에 계속 쓰이지 않게). 배치를 버려도 되돌리지 않는다(화면·설계에 적음).
        → (내린 수, 캐시를 지울 컬렉션 — 커밋 뒤에 지운다, 재리뷰 10/29)"""
        held, names = 0, []
        for r in await conn.fetch("SELECT DISTINCT k.namespace_id, n.name FROM rag_knowledge k JOIN ops_namespace n ON n.id = k.namespace_id "
                                  "WHERE k.confluence_page_id = $1 AND k.status = 'active'", page_id):
            if edit_denied_reason(ctx["owners"].get(r["namespace_id"], set()), user) is None:
                h, _ = await hold_restricted_chunks(conn, r["namespace_id"], page_id)
                if h:
                    held += h
                    names.append(r["name"])
        return held, names

    async def _preview_page(row_id: int, page_id: str) -> None:
        doc = await fetch_confluence_by_id(base_url, page_id, token, diagram_text=True, image_marks=False,
                                           include_restricted=b["include_restricted"])
        level = doc.metadata.get("restricted")
        if level and b["include_restricted"]:   # 관리자가 제한 포함을 켬(11/05) — 보통 페이지처럼 분류, 신호에 원래 제한 표시
            included = level
            level = None
            async with get_conn() as conn:   # 옛 "제한이라 안 넣음" 기록은 끝 — 공백 감지가 적재 안 된 제한 페이지로 세지 않게(리뷰 L3)
                await conn.execute("UPDATE rag_source_document SET status = 'superseded' WHERE status = 'restricted' "
                                   "AND source_type = 'confluence' AND source_ref = $1", str(page_id))
        else:
            included = None
        if level:   # 읽기 제한 — 적재하지 않고 어떤 페이지였는지 원장에(지식 공백 감지)
            async with get_conn() as conn:
                async with conn.transaction():
                    await record_restricted(conn, ns_id=ctx["draft_ns"], page_id=str(page_id), title=doc.source_name,
                                            parent_title=doc.metadata.get("parent_title"), level=level,
                                            meta=(doc.metadata.get("media") or {}).get("page"), user_id=user.get("id"))
                    held, names = await _hold_existing_restricted(conn, str(page_id)) if level in ("page", "ancestor") else (0, [])
                    await _set_page(conn, row_id, status="restricted", reason=None, signals={"restricted": level, "held": held})
            from shared.cache import invalidate_namespace_family
            for name in names:   # 커밋 뒤 — 그사이 요청이 옛 상태를 다시 캐시하지 않게
                await invalidate_namespace_family(name)
            return
        chunks = chunk_document(doc, strategy="auto")
        if not chunks:
            async with get_conn() as conn:
                await _set_page(conn, row_id, status="empty")
            return
        async with get_conn() as conn:
            existing = await _existing_ns(conn, str(page_id))
            if existing is not None:
                await _set_page(conn, row_id, status="existing", chunk_count=len(chunks), final_namespace_id=existing)
                return
            doc_id = await save_draft_for_parsed(conn, ctx["draft_ns"], doc, chunk_strategy="auto", user_id=user.get("id"),
                                                 source_ref=str(page_id), owner_only=True)
        text = (doc.source_name or "") + "\n" + (doc.raw_text or "")
        llm_out = await locator.judge_llm(ctx["cands"], text, llm, user_credentials=creds, profiles=ctx["profiles"])
        emb = await locator.embed_head(text)
        async with get_conn() as conn:
            r = await locator.finish(conn, ctx["cands"], llm_out, emb, text=text)
            status, reason, final, by = classify_default(r.ai_id, r.confident, r.llm_fail, ctx["unowned"], denied,
                                                         b["default_namespace_id"])
            await _set_page(conn, row_id, status=status, reason=reason, source_document_id=doc_id, chunk_count=len(chunks),
                            ai_namespace_id=r.ai_id, proposed_namespace_id=r.proposed_id, confident=r.confident, final_namespace_id=final,
                            assigned_by=by, sample_ns=final if status == "auto" else None,
                            signals={"neighbors": r.neighbors, "vote": [r.vote_top, r.vote_n], "reason": r.reason, "doc_type": r.doc_type,
                                     "llm_fail": r.llm_fail, "fallback": r.fallback, "model": model_tag,
                                     **({"restricted_included": included} if included else {})})

    await asyncio.gather(*(one(r) for r in rows))
    async with get_conn() as conn:
        required = await sampling_required_for(conn, team_id, exclude_batch=batch_id)
        await top_up_samples(conn, batch_id)
        await conn.execute("UPDATE rag_import_batch SET status = 'preview', sampling_required = $2 WHERE id = $1", batch_id, required)


async def top_up_samples(conn, batch_id: int) -> int:
    """컬렉션마다 표본을 목표(sample_targets)까지 채운다 — 처음 뽑기, 정정 뒤(목표 2배), 표본이 보류·제외로 빠진 뒤 공통. 뽑은 수."""
    pages = [dict(r) for r in await conn.fetch(
        "SELECT id, status, ai_namespace_id, sample_state, sample_ns, assigned_by FROM rag_import_page WHERE batch_id = $1", batch_id)]
    counts, wrong, _ = _sample_view(pages)
    targets = sample_targets(counts, wrong)
    auto: dict[str, list[int]] = {}
    already: dict[str, set[int]] = {}
    for p in pages:
        key = sample_key(p)
        if p["status"] != "auto" or key is None:
            continue
        auto.setdefault(key, []).append(p["id"])
        if p["sample_state"]:
            already.setdefault(key, set()).add(p["id"])
    need = {ns: targets[ns] - len(already.get(ns, set())) for ns in targets}
    seed = batch_id * 1000 + sum(len(v) for v in already.values())   # 다시 뽑기는 다른 표본(같은 상태면 같은 표본)
    ids = pick_samples(auto, already, need, seed)
    if ids:
        await conn.execute("UPDATE rag_import_page SET sample_state = 'pending' WHERE id = ANY($1::bigint[])", ids)
    return len(ids)


async def apply_default(conn, batch_id: int, user: dict, default_ns: Optional[int]) -> dict:
    """미리보기 중 공간 기본 위치를 정하거나 바꾼다 — 사람이 아직 손대지 않은 자동 분류·검토 필요 페이지를 다시 분류(저장된 AI 판정으로,
    LLM 다시 안 부름). 사람이 분류·표본 판정한 페이지는 그대로. 아직 안 본 표본은 내리고 묶음에 맞게 다시 뽑는다. → 상태별 수."""
    b = await conn.fetchrow("SELECT team_id FROM rag_import_batch WHERE id = $1", batch_id)
    ctx = await team_context(conn, b["team_id"])
    denied = denied_ids(ctx, user)
    rows = await conn.fetch("SELECT id, ai_namespace_id, confident, signals->>'llm_fail' AS llm_fail FROM rag_import_page "
                            "WHERE batch_id = $1 AND status = ANY($2::text[]) AND decided_by_user_id IS NULL", batch_id, list(DECIDABLE))
    out: dict[str, int] = {}
    updates = []
    for r in rows:
        st, reason, final, by = classify_default(r["ai_namespace_id"], r["confident"], r["llm_fail"] or "", ctx["unowned"], denied,
                                                 default_ns)
        updates.append((r["id"], st, reason, final, by, final if st == "auto" else None))
        key = by if st == "auto" else reason
        out[key] = out.get(key, 0) + 1
    await conn.executemany("UPDATE rag_import_page SET status = $2, reason = $3, final_namespace_id = $4, assigned_by = $5, sample_ns = $6, "
                           "sample_state = NULL WHERE id = $1", updates)
    await conn.execute("UPDATE rag_import_batch SET default_namespace_id = $2 WHERE id = $1", batch_id, default_ns)
    await top_up_samples(conn, batch_id)
    return out


# ─── 분류 확정 · 적재 · 롤백 ───────────────────────────────────────────────────

async def load_batch(batch_id: int, user: dict) -> None:
    """분류 확정 뒤(백그라운드) — 페이지마다 원장 초안을 최종 컬렉션으로 옮기고 지금 코드로 다시 분할해 등록(페이지 = 작업 1개,
    롤백 단위). 위치 판단은 이때 원장에 남긴다(판단 + 사람 확정을 한 번에 — 미리보기가 하루 넘게 열려 있어도 지워지지 않게).
    용어 자동 추출은 컬렉션마다 한 번, 만든 용어 id 범위를 배치에 남겨 롤백 때 같이 지운다."""
    from core.database import get_conn
    try:
        await _load_batch(batch_id, user)
    except Exception as e:   # noqa: BLE001 — 중간에 멈춰도 이미 들어간 조각은 롤백할 수 있게 닫는다(리뷰 10/29)
        logger.exception("팀 적재 실패 batch=%s", batch_id)
        async with get_conn() as conn:
            await close_load(conn, batch_id, f"적재 중 오류 — {str(e)[:300]}")


async def close_load(conn, batch_id: int, error: str) -> None:
    """적재가 중간에 멈춤(오류·서버 재시작) — 이미 등록 작업이 있으면 '적재됨 + 오류'(롤백 가능), 없으면 미리보기로 되돌린다(다시 확정)."""
    jobs = await conn.fetchval("SELECT cardinality(job_ids) FROM rag_import_batch WHERE id = $1", batch_id)
    await conn.execute("UPDATE rag_import_batch SET status = $2, error = $3, loaded_at = COALESCE(loaded_at, NOW()) WHERE id = $1 "
                       "AND status = 'loading'", batch_id, "loaded" if jobs else "preview", error[:500])


async def close_interrupted(conn) -> int:
    """기동 때 — 서버 재시작으로 멈춘 배치를 닫는다(안 그러면 그 팀이 새 적재를 영영 못 시작, 리뷰 10/29). 닫은 수."""
    n = int((await conn.execute("UPDATE rag_import_batch SET status = 'failed', error = '서버 재시작으로 분류가 중단됨 — 다시 시작해 주세요' "
                                "WHERE status = 'previewing'")).split()[-1])
    # 게시 중 재시작 — 남은 보류 작업은 다시 게시. 사유를 남긴다(화면이 "사유를 모름"으로 끝나지 않게, 리뷰 L2)
    # 게시 시각을 쓴 직후(표시 풀기 전) 재시작이면 다 게시된 것 — 그땐 사유를 덮지 않는다(재리뷰 L3)
    await conn.execute("UPDATE rag_import_batch SET publishing_at = NULL, error = CASE WHEN published_at IS NULL "
                       "THEN '서버 재시작으로 게시가 중단됨 — 다시 [게시]하면 남은 것부터 이어서' ELSE error END WHERE publishing_at IS NOT NULL")
    # 검색 확인 중 재시작 — 도는 중 표시를 내리고 끊겼다고 남긴다(화면이 "질문 3/10"에서 영영 멈춰 보이지 않게)
    await conn.execute("UPDATE rag_import_batch SET publish_check = (publish_check - 'running') || '{\"interrupted\": true}'::jsonb "
                       "WHERE publish_check->>'running' = 'true'")
    for r in await conn.fetch("SELECT id FROM rag_import_batch WHERE status = 'loading'"):
        await close_load(conn, r["id"], "서버 재시작으로 적재가 중단됨 — 일부만 들어갔으면 롤백 뒤 다시")
        n += 1
    return n


async def _load_batch(batch_id: int, user: dict) -> None:
    from core.database import get_conn
    from agents.knowledge_rag.ingestion.source_ledger import rechunk_items
    from agents.knowledge_rag.knowledge import service
    from service.decision import ledger
    from core.database import POLICY_COLLECTION_KNOWLEDGE_MSG

    async with get_conn() as conn:
        creator = await conn.fetchval("SELECT created_by_user_id FROM rag_import_batch WHERE id = $1", batch_id)
        pages = await conn.fetch(
            """SELECT p.id, p.page_id, p.title, p.final_namespace_id ns, n.name ns_name, n.kind ns_kind, n.area ns_area, p.source_document_id, p.ai_namespace_id,
                      p.proposed_namespace_id, p.confident, p.signals, p.reason, p.sample_state, p.assigned_by, p.sample_ns,
                      p.decided_by_user_id, p.status
               FROM rag_import_page p JOIN ops_namespace n ON n.id = p.final_namespace_id
               WHERE p.batch_id = $1 AND p.status = ANY($2::text[]) ORDER BY p.final_namespace_id, p.id""", batch_id, list(LOADABLE))
        await conn.execute("UPDATE rag_import_batch SET load_total = $2, load_done = 0 WHERE id = $1", batch_id, len(pages))
    for p in pages:
        sig = _sig(p)
        async with get_conn() as conn:
            # 미리보기 뒤에 다른 경로로 같은 페이지가 들어왔으면 넣지 않는다(교체·중복 방지, 리뷰 10/29)
            existing = await _existing_ns(conn, str(p["page_id"]))
            if existing is not None:
                await _set_page(conn, p["id"], status="existing", final_namespace_id=existing)
                await conn.execute("UPDATE rag_import_batch SET load_done = load_done + 1 WHERE id = $1", batch_id)
                continue
            # 운영 지식은 시스템에만(11/02 D) — 11/02 전 배치의 넣을 곳이 정책이면 초안을 옮기기 전에 멈춘다(옮긴 뒤 등록이 막히면 초안만 정책에 남는다)
            if p["ns_kind"] == "domain" and p["ns_area"] == "policy":
                await _set_page(conn, p["id"], status="failed", signals={**sig, "error": POLICY_COLLECTION_KNOWLEDGE_MSG})
                await conn.execute("UPDATE rag_import_batch SET load_done = load_done + 1 WHERE id = $1", batch_id)
                continue
            # 이 배치를 만든 사람의 초안만 옮긴다(같은 내용이면 남의 초안이 재사용됐을 수 있다)
            doc = await conn.fetchrow("UPDATE rag_source_document SET namespace_id = $2 WHERE id = $1 AND status = 'draft' "
                                      "AND created_by_user_id IS NOT DISTINCT FROM $3 RETURNING *", p["source_document_id"], p["ns"], creator)
            if doc is None:
                await _set_page(conn, p["id"], status="failed", signals={**sig, "error": "원문 초안이 없음(다른 등록이 썼거나 지워짐)"})
                await conn.execute("UPDATE rag_import_batch SET load_done = load_done + 1 WHERE id = $1", batch_id)
                continue
        items = [{**it, "source_document_id": doc["id"], "confluence_page_id": str(p["page_id"]),
                  "confluence_version": doc["source_version"]} for it in rechunk_items(doc)]
        try:
            res = await service.bulk_create_knowledge(namespace=p["ns_name"], items=items, source_file=p["title"], source_type="confluence",
                                                      created_by_user_id=user["id"], background=False, hold=True)   # 검수 후 게시(11/03)
        except Exception as e:   # noqa: BLE001 — 한 페이지 실패는 그 페이지만(화면에 실패로)
            logger.warning("팀 적재: 등록 실패 batch=%s page=%s: %s", batch_id, p["page_id"], e)
            async with get_conn() as conn:
                await _set_page(conn, p["id"], status="failed", signals={**sig, "error": _err_text(e)})
                await conn.execute("UPDATE rag_import_batch SET load_done = load_done + 1 WHERE id = $1", batch_id)
            continue
        ok = res.get("status") in ("completed", "held")
        async with get_conn() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE rag_import_batch SET job_ids = array_append(job_ids, $2), load_done = load_done + 1 WHERE id = $1",
                                   batch_id, res["job_id"])
                await _set_page(conn, p["id"], status="loaded" if ok else "failed", job_id=res["job_id"],
                                **({} if ok else {"signals": {**sig, "error": f"등록 작업 {res.get('status')}"}}))
        if not ok:
            continue
        by, preset, changed = ledger_view(p["assigned_by"], p["decided_by_user_id"] is not None, p["ns"], p["sample_ns"],
                                          p["ai_namespace_id"], p["proposed_namespace_id"], was_auto=p["status"] == "auto")
        try:   # 위치 판단 원장 — 실패해도 적재는 그대로(감사 기록만 빠짐, 로그)
            async with get_conn() as conn:
                did = await ledger.record(conn, locator.DECISION_KIND, ai_value={"namespace_id": p["ai_namespace_id"], "doc_type": sig.get("doc_type")},
                                          confident=p["confident"], user_id=user.get("id"), model=sig.get("model", ""),
                                          prompt_version=locator.PROMPT_VERSION,
                                          signals={"team_import_batch": batch_id, "neighbors": sig.get("neighbors"), "vote": sig.get("vote"),
                                                   "proposed_id": p["proposed_namespace_id"], "reason": sig.get("reason"),
                                                   "review_reason": p["reason"], "sample": p["sample_state"],
                                                   # 공간 기본값 = 사람 결정 — preset으로 남겨 번복·수락 통계에서 뺀다(ledger_view)
                                                   "assigned_by": by, "preset_id": preset})
                await ledger.finalize(conn, did, kind=locator.DECISION_KIND, user_id=user.get("id"),
                                      final_value={"namespace_id": p["ns"], "choice": "team_import"},
                                      changed=changed, target_type="ingestion_job", target_ids=[res["job_id"]])
        except Exception:   # noqa: BLE001
            logger.warning("팀 적재: 위치 판단 원장 기록 실패 batch=%s page=%s", batch_id, p["page_id"], exc_info=True)
    # 자동 용어 추출은 게시 때(publish_batch) — 게시 전 내용으로 용어를 만들면 검수 전에 용어집(채팅 문맥)에 들어간다
    async with get_conn() as conn:
        await conn.execute("UPDATE rag_import_batch SET status = 'loaded', loaded_at = NOW(), loaded_by_user_id = $2 "
                           "WHERE id = $1 AND status = 'loading'", batch_id, user["id"])


def _sig(p) -> dict:
    s = p["signals"]
    return json.loads(s) if isinstance(s, str) else dict(s or {})


_WARN_NUM = re.compile(r"\s*(\d+)\s*(곳|개|건|줄)?")


def warning_kind(text: str) -> tuple[str, int]:
    """품질 경고 문장 → (종류, 수) — 문장 속 숫자를 떼어 종류로 묶는다("겹친 곳 1곳"·"겹친 곳 2곳"이 다른 종류로 세지던 것, 11/05 태훈).
    숫자가 없으면 1. 문장 형식은 quality.py가 정하므로 형식이 아니라 "숫자를 뺀 나머지"로 묶는다(새 경고 문장도 그대로 묶임)."""
    s = str(text).split("(")[0].split(":")[0].strip()
    m = _WARN_NUM.search(s)
    n = int(m.group(1)) if m else 1
    return (_WARN_NUM.sub("", s).strip() or s)[:40], n


async def held_summary(conn, batch_id: int) -> dict:
    """게시 전 검수 카드 — 보류 작업 수 · 조각 수 · 중복 의심(staging_review) · 품질 경고(작업별 quality.warnings 합)."""
    jobs = await conn.fetchval("SELECT job_ids FROM rag_import_batch WHERE id = $1", batch_id) or []
    row = await conn.fetchrow(
        "SELECT count(DISTINCT j.id) AS jobs, count(k.id) FILTER (WHERE k.status = 'staging') AS chunks, "
        "count(k.id) FILTER (WHERE k.status = 'staging_review') AS dup FROM rag_ingestion_job j "
        "LEFT JOIN rag_knowledge k ON k.ingestion_job_id = j.id AND k.status = ANY($2::text[]) "
        "WHERE j.id = ANY($1::int[]) AND j.status = 'held'", list(jobs), list(HELD_KNOWLEDGE))
    warns: dict[str, int] = {}       # 종류 → 페이지(작업) 수
    places: dict[str, int] = {}      # 종류 → 모두 몇 곳
    items: list[dict] = []           # 리포트 창 — 페이지별(종류 · 몇 곳 · 제목 · 페이지 번호)
    # 이웃 겹침 — 결함 의심(원문보다 많이 나온 줄) / 원문 반복(원문이 원래 같은 줄을 여러 번) / 원문 대조 못 함(11/05 태훈 "분석적으로")
    ov = {"defect": 0, "source_repeat": 0, "unchecked_jobs": 0, "types": {}}
    ov_items: list[dict] = []
    for r in await conn.fetch(
            # 쌍 목록(overlap_pairs)은 펼칠 때만(overlap_detail) — 요약은 빼고 읽는다(리뷰 7)
            "SELECT j.id, j.quality - 'overlap_pairs' AS quality, p.title, p.page_id FROM rag_ingestion_job j "
            "LEFT JOIN rag_import_page p ON p.batch_id = $2 AND p.job_id = j.id "
            "WHERE j.id = ANY($1::int[]) AND j.status = 'held' AND j.quality IS NOT NULL", list(jobs), batch_id):
        q = r["quality"]
        q = json.loads(q) if isinstance(q, str) else (q or {})
        for w in q.get("warnings") or []:
            kind, n = warning_kind(w)
            warns[kind] = warns.get(kind, 0) + 1
            places[kind] = places.get(kind, 0) + n
            items.append({"kind": kind, "n": n, "title": r["title"], "page_id": r["page_id"], "job_id": r["id"]})
        d, rep = int(q.get("adjacent_overlap") or 0), int(q.get("adjacent_source_repeat") or 0)
        checked = bool(q.get("source_checked"))
        ov["defect"] += d
        ov["source_repeat"] += rep
        ov["unchecked_jobs"] += (not checked) and d > 0
        for t, n in (q.get("overlap_line_types") or {}).items():
            ov["types"][t] = ov["types"].get(t, 0) + n
        if d or rep:
            ov_items.append({"job_id": r["id"], "title": r["title"], "page_id": r["page_id"], "defect": d, "source_repeat": rep, "checked": checked})
    items.sort(key=lambda x: (x["kind"], -x["n"]))
    ov_items.sort(key=lambda x: (-x["defect"], -x["source_repeat"]))
    return {"jobs": row["jobs"], "chunks": row["chunks"], "dup": row["dup"], "warnings": warns, "warning_places": places,
            "warning_items": items[:500], "overlap": ov, "overlap_items": ov_items[:500]}


async def overlap_detail(conn, batch_id: int, job_id: int) -> Optional[dict]:
    """한 페이지의 이웃 겹침 상세 — 조각 번호 쌍 · 줄 · 유형 · 원문/조각 등장 수. 품질 기록엔 줄 해시만 있으니(본문 비기록) 조각에서 다시 찾는다.
    이 배치의 작업이 아니면 None."""
    from trustkit.ingest.quality import line_hash, text_lines
    from agents.knowledge_rag.ingestion.quality import JOB_CHUNKS_SQL
    jobs = await conn.fetchval("SELECT job_ids FROM rag_import_batch WHERE id = $1", batch_id) or []
    if job_id not in jobs:
        return None
    q = await conn.fetchval("SELECT quality FROM rag_ingestion_job WHERE id = $1", job_id)
    q = json.loads(q) if isinstance(q, str) else (q or {})
    rows = await conn.fetch(JOB_CHUNKS_SQL, job_id)
    pos = {r["id"]: n for n, r in enumerate(rows)}
    out, stale = [], 0
    for p in q.get("overlap_pairs") or []:
        ids = p.get("ids") or []
        # 조각 id로 찾는다 — 기록 뒤 중간 조각이 빠져도(반려·삭제) 번호가 밀려 엉뚱한 쌍에 붙지 않게(리뷰 3). 없는 쌍은 "바뀜"으로 센다
        if len(ids) != 2 or ids[0] not in pos or ids[1] not in pos:
            stale += 1
            continue
        a = pos[ids[0]]
        text = {line_hash(ln): ln for ln in text_lines(rows[a]["content"])}
        out.append({"a": a + 1, "b": pos[ids[1]] + 1, "a_id": ids[0], "b_id": ids[1], "defect": p["defect"],
                    "lines": [{**x, "text": text.get(x["h"])} for x in p["lines"]]})
    return {"checked": bool(q.get("source_checked")), "chunks": len(rows), "pairs": out, "stale": stale,
            "truncated": bool(q.get("overlap_pairs_truncated"))}


CHECK_SAMPLES = 10
_Q_SYSTEM = ("운영 문서 조각을 읽고, 이 조각이 답이 될 만한 짧은 질문 하나를 한국어로 만드세요. "
             "조각에 없는 내용은 묻지 마세요. 질문 한 줄만 출력하세요.")


def check_running(batch: dict) -> bool:
    """검색 확인이 도는 중인가(배치 publish_check.running) — 두 번 누름 막기. 재시작으로 끊긴 건 기동 때 close_interrupted가 내린다."""
    pc = batch.get("publish_check")
    pc = json.loads(pc) if isinstance(pc, str) else (pc or {})
    return bool(pc.get("running"))


async def publish_check(batch_id: int) -> dict:
    """게시 전 검색 확인 — 보류 조각 표본 10개로 로컬 LLM이 질문을 만들고, 그 질문으로 (이 컬렉션의 게시된 지식 + 이 배치 조각) 중
    벡터 상위 3에 그 페이지 조각이 드는지 센다. 컨플 원문이라 로컬 LLM일 때만(사내 규칙) — 아니면 건너뛰고 그렇게 기록(대체 처리를 보이게).
    결과는 배치 publish_check에 — 화면이 "9/10"과 못 찾은 질문을 보여 준다. 키워드·리랭크 없이 벡터만이라 실제 채팅보다 보수적."""
    from datetime import datetime, timezone
    from core.database import get_conn
    from service.llm.factory import get_ingest_llm
    from shared.embedding import embedding_service
    llm, system_llm = get_ingest_llm()
    now = datetime.now(timezone.utc).isoformat()
    async with get_conn() as conn:
        jobs = list(await conn.fetchval("SELECT job_ids FROM rag_import_batch WHERE id = $1", batch_id) or [])
        if system_llm:
            out = {"checked_at": now, "skipped": "로컬 LLM이 없어 검색 확인을 건너뜀(컨플 원문은 사내 LLM으로 보내지 않음)"}
        else:
            # 제목 = 이 배치의 페이지 제목(source_file은 묶음 이름이라 페이지 구분이 안 됐다, 11/05)
            samples = await conn.fetch(
                "SELECT k.id, k.content, k.namespace_id, k.confluence_page_id, COALESCE(p.title, k.source_file) AS title FROM rag_knowledge k "
                "LEFT JOIN rag_import_page p ON p.batch_id = $3 AND p.page_id = k.confluence_page_id "
                "WHERE k.ingestion_job_id = ANY($1::int[]) AND k.status = 'staging' AND length(k.content) >= 40 "
                "ORDER BY md5(k.id::text) LIMIT $2", jobs, CHECK_SAMPLES, batch_id)
            # items = 질문마다 결과(리포트 창, 11/05 태훈 "누르면 상세 리포트") — 질문·페이지·찾음·몇 위
            out = {"checked_at": now, "asked": 0, "found": 0, "failed": 0, "misses": [], "items": []}
    async def save(doc: dict) -> None:
        async with get_conn() as conn:
            await conn.execute("UPDATE rag_import_batch SET publish_check = $2::jsonb WHERE id = $1", batch_id, json.dumps(doc, ensure_ascii=False))

    if system_llm:
        await save(out)
        return out
    # 진행 기록(11/05 "멈춘 것처럼") — 화면이 running·total·asked로 "질문 3/10"을 그린다. 끝 저장(finally)이 running을 지운다 —
    # 중간에 임베딩·DB 오류로 죽어도 "도는 중"이 남아 버튼이 잠기지 않게(리뷰 M1), 끊긴 사유는 error로
    total = len(samples)
    await save({**out, "running": True, "total": total})
    try:
        for s in samples:
            try:
                q = (await llm.generate_once(prompt=s["content"][:3000], system=_Q_SYSTEM, max_tokens=120)).strip().splitlines()[0][:200]
            except Exception:   # noqa: BLE001 — 한 표본 실패는 그것만 센다(로그)
                logger.warning("게시 전 검색 확인: 질문 만들기 실패 batch=%s chunk=%s", batch_id, s["id"], exc_info=True)
                out["failed"] += 1
                continue
            vec = await embedding_service.embed(q)
            async with get_conn() as conn:
                # 제목 = 원장 원문 제목(페이지) — 못 찾았을 때 "대신 나온 것"을 보여 준다(11/05 리포트)
                top = await conn.fetch(
                    "SELECT k.id, k.confluence_page_id, COALESCE(d.title, k.source_file) AS title, 1 - (k.embedding <=> $2::vector) AS sim "
                    "FROM rag_knowledge k LEFT JOIN rag_source_document d ON d.id = k.source_document_id "
                    "WHERE k.namespace_id = $1 AND k.embedding IS NOT NULL AND (k.status = 'active' "   # 임베딩 없는 행은 sim이 NULL(리뷰 1)
                    "OR (k.status = 'staging' AND k.ingestion_job_id = ANY($3::int[]))) ORDER BY k.embedding <=> $2::vector LIMIT 3",
                    s["namespace_id"], str(vec), jobs)
            same = [t["id"] == s["id"] or bool(s["confluence_page_id"] and t["confluence_page_id"] == s["confluence_page_id"]) for t in top]
            rank = next((i for i, ok in enumerate(same, 1) if ok), None)
            hit = rank is not None
            out["asked"] += 1
            out["found"] += hit
            out["items"].append({"question": q, "title": s["title"], "page_id": s["confluence_page_id"], "found": hit, "rank": rank,
                                 "got": [{"title": t["title"], "page_id": t["confluence_page_id"], "sim": round(float(t["sim"]), 3), "same": ok}
                                         for t, ok in zip(top, same)]})
            if not hit:
                out["misses"].append({"question": q, "title": s["title"]})
            await save({**out, "running": True, "total": total})
    except Exception as e:   # noqa: BLE001 — 뒤에서 도는 일이라 화면은 이유를 모른다 — 센 데까지 + 사유를 남긴다(로그)
        logger.warning("게시 전 검색 확인 중단 batch=%s", batch_id, exc_info=True)
        out["error"] = f"검색 확인이 중간에 멈췄어요({type(e).__name__}) — 센 데까지만 · 다시 눌러 주세요"
    finally:
        await save(out)
    return out


async def claim_publish(conn, batch_id: int) -> list[int]:
    """게시 잡기 — 게시 중 표시를 세우고 이 배치 작업 id를 돌려준다. 두 번 누름·게시 중 롤백이 엇갈리지 않게(재리뷰 M·L).
    끝나면 publish_batch가 풀고, 기동 때 남은 표시는 close_interrupted가 푼다. 게시할 수 없으면 ValueError(화면 409)."""
    b = await conn.fetchrow("UPDATE rag_import_batch SET publishing_at = NOW(), error = NULL WHERE id = $1 AND status = 'loaded' "
                            "AND published_at IS NULL AND publishing_at IS NULL RETURNING job_ids", batch_id)
    if b is None:
        raise ValueError("게시할 수 있는 배치가 아닙니다(적재 끝·게시 전만, 이미 게시 중이면 끝난 뒤).")
    return list(b["job_ids"] or [])


async def publish_progress(conn, batch: dict) -> Optional[dict]:
    """게시 중 진행 — 작업 단위(끝남/전체) · 단계(작업 게시 → 용어 추출). 게시 중이 아니면 None. 화면이 몇 초마다 읽는다(11/05 태훈
    "멈춘 것처럼 보인다" — 413페이지 게시가 몇 분 걸리는데 아무 표시가 없었다)."""
    if not batch.get("publishing_at"):
        return None
    r = await conn.fetchrow("SELECT count(*) FILTER (WHERE status = 'completed') AS done, "
                            "count(*) FILTER (WHERE status IN ('held', 'completed')) AS total FROM rag_ingestion_job WHERE id = ANY($1::int[])",
                            list(batch.get("job_ids") or []))
    phase = "glossary" if r["total"] and r["done"] >= r["total"] else "jobs"
    return {"done": r["done"], "total": r["total"], "phase": phase, "started_at": batch["publishing_at"].isoformat()}


async def publish_batch(batch_id: int, user: dict, job_ids: Optional[list[int]] = None) -> dict:
    """검수 후 게시(11/03 결정 가) — 이 배치의 보류 작업을 하나씩 게시(옛 버전 내림·원문 확정·활성화가 이때), 다 되면 게시 시각 +
    자동 용어 추출(컬렉션마다 한 번, 만든 용어 id 범위를 배치에 — 롤백 때 같이 지운다). 실패한 작업은 보류 그대로 남고 사유를 배치 오류 칸에.
    화면 경로는 claim_publish로 먼저 잡고(job_ids) 뒤에서 이걸 돌린다 — 진행은 publish_progress."""
    from core.database import get_conn
    from agents.knowledge_rag.knowledge import service
    from agents.knowledge_rag.knowledge.router import AUTO_EXTRACT_SOURCE, _run_auto_glossary
    if job_ids is None:
        async with get_conn() as conn:
            job_ids = await claim_publish(conn, batch_id)
    done, failed = 0, []
    try:   # 잡은 직후부터 — 여기서 무엇이 실패해도 finally가 게시 중 표시를 푼다(재리뷰 L1)
        async with get_conn() as conn:
            jobs = [r["id"] for r in await conn.fetch(
                "SELECT id FROM rag_ingestion_job WHERE id = ANY($1::int[]) AND status = 'held' ORDER BY id", list(job_ids))]
        for job_id in jobs:
            try:
                await service.publish_held_job(job_id)
                done += 1
            except Exception as e:   # noqa: BLE001 — 한 작업 실패(원문 교체·교착 등)는 그 작업만 보류로 남기고 계속(로그·사유)
                logger.warning("팀 적재 게시: 작업 실패 batch=%s job=%s", batch_id, job_id, exc_info=True)
                failed.append({"job_id": job_id, "error": str(e)[:300]})
        if failed:   # 일부만 — 게시 시각은 비워 두고(남은 작업을 다시 게시), 사유는 배치 오류 칸에
            async with get_conn() as conn:
                await conn.execute("UPDATE rag_import_batch SET error = $2 WHERE id = $1 AND status = 'loaded'", batch_id,
                                   f"게시 실패 {len(failed)}건 — " + " / ".join(f["error"] for f in failed[:3]))
            return {"published": done, "failed": failed}
        # 다 게시됨 — 자동 용어 추출은 배치 전체 조각으로 한 번(앞서 일부만 게시했던 작업 몫도 빠지지 않게), 만든 범위·게시자를 배치에(롤백 기준)
        async with get_conn() as conn:
            texts: dict[str, list[str]] = {}
            for r in await conn.fetch("SELECT n.name, k.content FROM rag_knowledge k JOIN ops_namespace n ON n.id = k.namespace_id "
                                      "WHERE k.ingestion_job_id = ANY($1::int[]) AND k.status = 'active' ORDER BY k.id", list(job_ids)):
                texts.setdefault(r["name"], []).append(r["content"])
            before = await conn.fetchval("SELECT COALESCE(max(id), 0) FROM rag_glossary")
        for ns_name, parts in texts.items():
            try:
                await _run_auto_glossary(ns_name, "\n".join(parts), user, max_chars=20000)
            except Exception:   # noqa: BLE001 — 뒤에서 도는 게시라 여기서 죽으면 화면은 이유를 모른다 — 조각은 이미 게시됨, 용어만 빠짐을 표시
                logger.warning("팀 적재 게시: 자동 용어 추출 실패 batch=%s ns=%s", batch_id, ns_name, exc_info=True)
                async with get_conn() as conn:
                    await conn.execute("UPDATE rag_import_batch SET error = $2 WHERE id = $1", batch_id,
                                       f"자동 용어 추출 실패({ns_name}) — 조각은 게시됨, 용어는 다시 추출하세요")
        async with get_conn() as conn:
            after = await conn.fetchval("SELECT COALESCE(max(id), 0) FROM rag_glossary")
            await conn.execute("UPDATE rag_import_batch SET published_at = NOW(), published_by_user_id = $5, glossary_from_id = $2, "
                               "glossary_to_id = $3, glossary_source = $4 WHERE id = $1 AND status = 'loaded'",
                               batch_id, before, after, AUTO_EXTRACT_SOURCE, user.get("id"))
        return {"published": done, "failed": []}
    finally:
        async with get_conn() as conn:
            await conn.execute("UPDATE rag_import_batch SET publishing_at = NULL WHERE id = $1", batch_id)


async def rollback_batch(conn, batch_id: int) -> Optional[dict]:
    """이번 배치만 되돌린다 — 배치 작업이 만든 조각 soft delete(메운 지식 공백 다시 열기), 원문은 교체됨, 배치가 만든 자동 용어 삭제.
    이미 적재됨(다른 경로) 페이지는 애초에 넣지 않았으니 건드릴 것이 없다. 배치 뒤에 사람이 고친 조각도 같이 지워진다.
    용어는 배치가 만든 새 용어만(기존 용어에 붙은 동의어는 남는다 — 한계). 이미 되돌렸거나 적재 상태가 아니면 None."""
    async with conn.transaction():
        b = await conn.fetchrow("UPDATE rag_import_batch SET status = 'rolled_back', rolled_back_at = NOW() WHERE id = $1 AND status = 'loaded' "
                                "AND publishing_at IS NULL "   # 게시 중엔 되돌리지 않는다(끝난 뒤)
                                "RETURNING job_ids, glossary_from_id, glossary_to_id, glossary_source, "
                                "COALESCE(published_by_user_id, loaded_by_user_id) AS glossary_by", batch_id)   # 자동 용어는 게시한 사람이 만든다
        if b is None:
            return None
        jobs = list(b["job_ids"] or [])
        ids = [r["id"] for r in await conn.fetch(
            "UPDATE rag_knowledge SET status = 'deleted', updated_at = NOW() WHERE ingestion_job_id = ANY($1::int[]) "
            "AND status = ANY($2::text[]) RETURNING id", jobs, list(ACTIVE_KNOWLEDGE + HELD_KNOWLEDGE) + ["archived"])]   # 게시 전(보류)·보관 조각도
        await conn.execute("UPDATE rag_ingestion_job SET status = 'cancelled' WHERE id = ANY($1::int[]) AND status = 'held'", jobs)
        released = await _release_orphan_duplicates(conn, ids)
        # 지운 지식으로 메운 지식 공백은 다시 열린다(service.delete_knowledge와 같은 처리)
        await conn.execute("UPDATE ops_query_log SET resolved_knowledge_id = NULL, resolved_at = NULL "
                           "WHERE resolved_knowledge_id = ANY($1::int[])", ids)
        docs = await conn.execute("UPDATE rag_source_document SET status = 'superseded' WHERE id IN (SELECT source_document_id FROM "
                                  "rag_import_page WHERE batch_id = $1 AND status = 'loaded') AND status = 'active'", batch_id)
        terms = 0
        if b["glossary_to_id"] and b["glossary_to_id"] > (b["glossary_from_id"] or 0):
            terms = int((await conn.execute(
                "DELETE FROM rag_glossary WHERE id > $1 AND id <= $2 AND source = $3 AND created_by_user_id IS NOT DISTINCT FROM $4 "
                "AND namespace_id IN (SELECT DISTINCT final_namespace_id FROM rag_import_page WHERE batch_id = $5 AND status = 'loaded')",
                b["glossary_from_id"] or 0, b["glossary_to_id"], b["glossary_source"], b["glossary_by"], batch_id)).split()[-1])
        await conn.execute("UPDATE rag_import_page SET status = 'held', prev_status = NULL WHERE batch_id = $1 AND status = 'loaded'", batch_id)
    return {"chunks": len(ids), "documents": int(docs.split()[-1]), "terms": terms, "released": released}


async def _release_orphan_duplicates(conn, deleted_ids: list[int]) -> int:
    """롤백으로 지운 조각만 중복 상대로 가리키던 게시 전 승인 대기(staging_review)는 중복 근거가 없어진다 — staging으로 풀고 비교를 지운다
    (11/06 D4). 게시 때 담당 승인 필요(force_pending)는 다시 적용된다(service 게시 UPDATE).
    승인 대기(pending_review)는 안 푼다 — 담당이 아닌 사람이 넣어 승인 대기인 것과 행으로 구분이 안 된다(리뷰 중간: 담당 승인 우회).
    살아 있는 상대가 하나라도 남았거나 읽기 제한 페이지 조각이면 그대로. 푼 개수를 돌려준다."""
    if not deleted_ids:
        return 0
    rows = await conn.fetch(
        "SELECT DISTINCT k.id, k.status, k.namespace_id, k.confluence_page_id FROM rag_knowledge_duplicate_match m "
        "JOIN rag_knowledge k ON k.id = m.new_knowledge_id "
        "WHERE m.matched_knowledge_id = ANY($1::int[]) AND k.status = 'staging_review' "
        "AND NOT EXISTS (SELECT 1 FROM rag_knowledge_duplicate_match o JOIN rag_knowledge t ON t.id = o.matched_knowledge_id "
        "WHERE o.new_knowledge_id = k.id AND t.status = ANY($2::text[]))",
        deleted_ids, list(ACTIVE_KNOWLEDGE + HELD_KNOWLEDGE))
    if not rows:
        return 0
    from agents.knowledge_rag.ingestion.source_ledger import restricted_pages
    blocked: set[int] = set()
    by_ns: dict[int, list] = {}
    for r in rows:
        if r["confluence_page_id"]:
            by_ns.setdefault(r["namespace_id"], []).append(r)
    for ns_id, rs in by_ns.items():
        hit = await restricted_pages(conn, ns_id, [r["confluence_page_id"] for r in rs])
        blocked |= {r["id"] for r in rs if str(r["confluence_page_id"]) in hit}
    ids = [r["id"] for r in rows if r["id"] not in blocked]
    if not ids:
        return 0
    ids = [r["id"] for r in await conn.fetch(   # 상태 조건 — 그사이 다른 사람이 처리한 행은 건드리지 않는다(리뷰 낮음~중간)
        "UPDATE rag_knowledge SET status = 'staging', updated_at = NOW() WHERE id = ANY($1::int[]) AND status = 'staging_review' "
        "RETURNING id", ids)]
    await conn.execute("DELETE FROM rag_knowledge_duplicate_match WHERE new_knowledge_id = ANY($1::int[])", ids)
    logger.info("롤백: 중복 상대가 지워진 승인 대기 %d건 해제(%s)", len(ids), ids[:20])
    return len(ids)
