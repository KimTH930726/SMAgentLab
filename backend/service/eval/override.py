"""운영 번복 — 기능을 쓰다 사람이 AI 추천을 번복한 기록(AI 판단 원장)을 자가 개선 진단의 입력으로(10/27, docs/domains/quality/self-improvement-loop-design.md).

업계 override monitoring 관례: 결정 종류별 번복률·이유 코드·패턴을 보고, 기대보다 높으면 기준(임계·가중)을 의심한다.
위치 판정은 원장에 남은 신호(AI 값·가까운 이웃)로 제안 규칙을 LLM 없이 다시 계산해 "맞은 제안 · 확신인데 틀림"을 비교한다 —
현행보다 맞은 제안이 많고 확신 오답이 늘지 않으면 "해 볼 만함". 원문은 안 쓴다(id·이름·숫자, 원장 규칙).
"""
from __future__ import annotations

import json
from typing import Optional

from trustkit.decision.ledger import is_confident, vote

LOCATION = "knowledge_location"
DUPLICATE = "duplicate_check"   # 지식 등록 때 중복 의심(유사도) → 사람이 승인 대기에서 그대로 저장·덮어쓰기·반려(11/03)
KIND_NAME = {LOCATION: "등록 위치", "chat_clarify": "되묻기", "source_check": "원문 확인", "collection_suggest": "다른 컬렉션 제안",
             DUPLICATE: "중복 의심"}
# 번복 사유(업계 override reason code) — 11/04 태훈 "업계 용어로"
REASON_NAME = {"wrong": "AI 오류", "both_ok": "선호 차이", "context_changed": "맥락 변화"}
NO_REASON = "사유 없음"
# 형식적 승인 의심(업계: 수락이 지나치게 높으면 사람이 실제로 안 보고 있을 수 있다) — 표본이 충분할 때만
RUBBER_MIN_N, RUBBER_RATE = 20, 0.95
K = 5

# 제안 규칙(locator.decide와 같은 모양) — (이름, 이웃 기준 개수, 순서)
RULES = [
    ("현행(이웃 5중 4 → 이웃, 아니면 AI)", 4, "nb_if_enough"),
    ("이웃 5중 3이면 이웃", 3, "nb_if_enough"),
    ("늘 AI 먼저", 4, "ai_first"),
    ("늘 이웃 먼저", 4, "nb_first"),
]


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


def propose(ai: Optional[int], neighbors: list[int], need: int, order: str) -> tuple[Optional[int], bool]:
    """(미리 고를 곳, 확신) — 순수 함수. 확신 규칙은 현행과 같게 두 신호 일치(이웃 기준만 need로)."""
    top, n, _ = vote(neighbors[:K])
    if order == "ai_first":
        p = ai if ai is not None else top
    elif order == "nb_first":
        p = top if top is not None else ai
    else:
        p = top if (top is not None and n >= need) else (ai if ai is not None else top)
    return p, is_confident(ai, neighbors, k=K, need=need)


def replay_location(rows: list[dict]) -> dict:
    """rows = 확정된 위치 판정 {ai, neighbors, final}. 규칙마다 맞은 제안·확신인데 틀림 — 현행과 비교해 고침/새로 틀림까지."""
    out = []
    base_hit: dict[int, bool] = {}
    for idx, (name, need, order) in enumerate(RULES):
        hit = auto_wrong = 0
        fixed, broken = [], []
        for i, r in enumerate(rows):
            p, conf = propose(r["ai"], r["neighbors"], need, order)
            ok = p is not None and p == r["final"]
            hit += ok
            auto_wrong += conf and not ok
            if idx == 0:
                base_hit[i] = ok
            elif ok and not base_hit[i]:
                fixed.append(r["id"])
            elif base_hit[i] and not ok:
                broken.append(r["id"])
        out.append({"rule": name, "hit": hit, "n": len(rows), "auto_wrong": auto_wrong, "fixed": fixed, "broken": broken})
    cur = out[0] if out else None
    better = [x for x in out[1:] if cur and x["hit"] > cur["hit"] and x["auto_wrong"] <= cur["auto_wrong"] and not x["broken"]]
    best = max(better, key=lambda x: x["hit"]) if better else None
    return {"rules": out, "best": best}


# 사람이 실제로 검토해 고르는 종류만 형식적 승인을 본다(되묻기·제안은 사용자 선택이라 수락률이 높아도 문제 아님)
REVIEWED_KINDS = (LOCATION, "source_check", DUPLICATE)


# is_override와 같은 규칙의 SQL(별칭 d = ops_ai_decision) — 감사 로그의 "번복만" 거르기·표시가 같은 기준을 쓴다(테스트가 둘을 맞춘다)
OVERRIDE_SQL = """(d.final_value IS NOT NULL AND CASE d.kind
    WHEN 'knowledge_location' THEN d.signals->>'preset_id' IS NULL
         AND COALESCE(d.signals->>'proposed_id', d.ai_value->>'namespace_id') IS NOT NULL
         AND d.final_value->>'namespace_id' IS NOT NULL
         AND d.final_value->>'namespace_id' <> COALESCE(d.signals->>'proposed_id', d.ai_value->>'namespace_id')
    WHEN 'chat_clarify' THEN COALESCE((d.final_value->>'typed')::boolean, false) OR d.final_value->>'picked' = '*'
    WHEN 'source_check' THEN (d.final_value->>'issue') = 'false'
    WHEN 'duplicate_check' THEN d.final_value->>'action' = 'approve'
    ELSE false END)"""


# 사람 검토 결과(HITL, 11/04 태훈 "AI 판단에 대한 사람의 수용/번복이 보여야") — 판단 한 줄 = 다섯 중 하나:
#   pending 검토 대기(확정 전) · rule 규칙 적용(사람이 정한 규칙·공간 기본값·미리 고른 곳 — AI 판단 미사용) ·
#   auto 자동 적용(AI 분류를 사람 손 없이 적용) · override 번복 · accept 수용. 번복률 = 번복 / (수용 + 번복) — 사람이 본 것만으로.
REVIEWS = ("accept", "override", "auto", "rule", "pending")
RULE_SQL = "(d.kind = 'knowledge_location' AND d.signals->>'preset_id' IS NOT NULL)"
AUTO_SQL = "(d.final_value->>'choice' = 'team_import' AND d.signals->>'assigned_by' = 'ai')"
REVIEW_SQL = (f"(CASE WHEN d.final_value IS NULL THEN 'pending' WHEN {RULE_SQL} THEN 'rule' WHEN {AUTO_SQL} THEN 'auto' "
              f"WHEN {OVERRIDE_SQL} THEN 'override' ELSE 'accept' END)")


def review_status(r: dict) -> str:
    """REVIEW_SQL과 같은 규칙(테스트가 둘을 맞춘다)."""
    final, sig = r["final"], r["signals"] or {}
    if final is None:
        return "pending"
    if r["kind"] == LOCATION and sig.get("preset_id") is not None:
        return "rule"
    if final.get("choice") == "team_import" and sig.get("assigned_by") == "ai":
        return "auto"
    return "override" if is_override(r) else "accept"


def recommendation(r: dict):
    """등록 위치의 AI 추천 = 화면이 미리 골라 둔 곳(signals.proposed_id, 옛 줄은 AI 값). preset(컬렉션 화면에서 넘어온 곳)이면 None —
    사람이 정한 곳이라 번복 대상이 아니다(ledger.location_outcome과 같은 규칙, 리뷰 10/27 H1·M1)."""
    sig = r["signals"] or {}
    if sig.get("preset_id") is not None:
        return None
    return sig.get("proposed_id") or (r["ai"] or {}).get("namespace_id")


def is_override(r: dict) -> bool:
    """번복인가 — 저장된 human_changed 칸이 아니라 값에서 직접(10/23 초기 코드가 같은 곳에도 '바꿈'을 남긴 줄이 있어서).
    등록 위치 = AI 추천(확신 없으면 후보)과 다른 곳 · 되묻기 = 고르지 않고 직접 다시 물음 또는 전부 보기(괜히 물음) ·
    원문 확인 = 문제 없음(검사기가 괜히 짚음) · 다른 컬렉션 제안 = 번복 개념 없음(누름만 기록)."""
    final = r["final"] or {}
    if r["kind"] == LOCATION:
        rec = recommendation(r)
        return rec is not None and final.get("namespace_id") is not None and str(final["namespace_id"]) != str(rec)
    if r["kind"] == "chat_clarify":
        return bool(final.get("typed")) or final.get("picked") == "*"
    if r["kind"] == "source_check":
        return final.get("issue") is False
    if r["kind"] == DUPLICATE:   # AI는 "중복"이라 했는데 사람이 그대로 저장 = 중복 아님
        return final.get("action") == "approve"
    return False


def summarize(rows: list[dict], names: dict[int, str]) -> dict:
    """원장 줄 → 종류별 {검토 결과 다섯 갈래 수, 사람 검토(수용+번복), 번복·번복률, 사유, 패턴 상위 5, 표본 감사, 형식적 승인 의심}.
    rows = 원장 줄(확정 전 포함, superseded 제외). decided = 사람 검토 수(번복률 분모) — 규칙·자동 적용·대기는 분모에서 뺀다(11/04:
    팀 적재 규칙 413건이 "수용"으로 섞여 번복률 1.1%로 보였다, 사람이 본 24건만 세면 21%)."""
    by: dict[str, dict] = {}
    for r in rows:
        k = by.setdefault(r["kind"], {"kind": r["kind"], "kind_name": KIND_NAME.get(r["kind"], r["kind"]), "decided": 0, "overrides": 0,
                                       "review": {x: 0 for x in REVIEWS}, "spot": {"n": 0, "overridden": 0, "auto_n": 0, "auto_overridden": 0},
                                       "admin_reviewed": r["kind"] in REVIEWED_KINDS,
                                       "reasons": {}, "patterns": {}, "pairs": {}, "override_ids": []})
        st = review_status(r)
        k["review"][st] += 1
        if r.get("spot"):
            k["spot"]["n"] += 1
            k["spot"]["overridden"] += r["spot"] == "overridden"
            if st == "auto":   # 자동 적용분만 따로 — 수용분 재감사와 섞지 않게(리뷰 L4)
                k["spot"]["auto_n"] = k["spot"].get("auto_n", 0) + 1
                k["spot"]["auto_overridden"] = k["spot"].get("auto_overridden", 0) + (r["spot"] == "overridden")
        if st not in ("accept", "override"):
            continue
        k["decided"] += 1
        if st != "override":
            continue
        k["overrides"] += 1
        k["override_ids"].append(r["id"])
        final = r["final"] or {}
        if r["kind"] == "chat_clarify":
            label = "직접 재질문" if final.get("typed") else "전부 보기"   # 채팅 버튼 이름 그대로
        elif r["kind"] == "source_check":
            from service.policy.source_check import NO_ISSUE_REASONS   # 한 곳 정의(11/05) — 원문 정상·이미 수정됨만 "오탐"의 하위 사유
            reason = final.get("reason")
            label = f"오탐 · {NO_ISSUE_REASONS[reason]}" if reason in ("source_ok", "already_fixed") else "오탐"
        elif r["kind"] == DUPLICATE:
            label = "중복 아님(그대로 저장)"   # AI는 중복이라 했는데 사람이 저장
        else:
            # 옛 줄(10/27 이전)은 감사 로그에서 받은 답(ai_verdict)이 이유 — 틀림·상황이 바뀜 둘 다(리뷰 L2)
            why = final.get("override_reason") or (r["verdict"] if r["verdict"] in ("wrong", "context_changed") else None)
            label = REASON_NAME.get(why, NO_REASON)
        k["reasons"][label] = k["reasons"].get(label, 0) + 1
        if r["kind"] == LOCATION:
            src, dst = recommendation(r), (r["final"] or {}).get("namespace_id")
            # 추천이 없었거나(사람에게 물음) 지운 컬렉션이면 이름 대신 그 사실을 — 둘 다 모르면 패턴으로 안 셈("? → ?"가 보이던 것, 11/05)
            if src is not None or dst is not None:
                key = (f"{names.get(src, '삭제된 컬렉션') if src is not None else '추천 없음'} → "
                       f"{names.get(dst, '삭제된 컬렉션') if dst is not None else '미지정'}")
                k["patterns"][key] = k["patterns"].get(key, 0) + 1
                # 옮김 표(11/05 — 줄글 패턴 대신 AI 추천 × 사람 결정 표). 이름은 화면이, 지운 컬렉션은 deleted로 따로 센다
                pk = (src, dst)
                k["pairs"][pk] = k["pairs"].get(pk, 0) + 1
    for k in by.values():
        k["rate"] = round(k["overrides"] / k["decided"], 3) if k["decided"] else None
        k["patterns"] = sorted(({"pattern": p, "n": n} for p, n in k["patterns"].items()), key=lambda x: -x["n"])[:5]
        k["pairs"] = sorted(({"from": names.get(a) if a is not None else None, "to": names.get(b) if b is not None else None,
                              "deleted": (a is not None and a not in names) or (b is not None and b not in names), "n": n}
                             for (a, b), n in k["pairs"].items()), key=lambda x: -x["n"])
        k["rubber_stamp"] = k["kind"] in REVIEWED_KINDS and k["decided"] >= RUBBER_MIN_N and (1 - (k["rate"] or 0)) > RUBBER_RATE
    return {"kinds": sorted(by.values(), key=lambda x: -x["overrides"])}


def location_rows(rows: list[dict]) -> list[dict]:
    """확정된 위치 판정 → 리플레이 입력. 넣을 곳이 정해진(preset) 판정과 이웃 신호가 없는 줄은 뺀다(규칙이 고를 게 없음)."""
    out = []
    for r in rows:
        sig, final = r["signals"] or {}, r["final"] or {}
        if r["kind"] != LOCATION or sig.get("preset_id") is not None or not final.get("namespace_id"):
            continue
        nb = [int(n[0]) for n in sig.get("neighbors") or [] if n and n[0] is not None]
        if not nb:
            continue
        out.append({"id": r["id"], "ai": (r["ai"] or {}).get("namespace_id"), "neighbors": nb, "final": final["namespace_id"]})
    return out


async def load(conn, days: int) -> tuple[list[dict], dict[int, str]]:
    rows = [{"id": r["id"], "kind": r["kind"], "ai": _j(r["ai_value"]), "final": _j(r["final_value"]), "signals": _j(r["signals"]),
             "changed": bool(r["human_changed"]), "verdict": r["ai_verdict"], "spot": r["spot_result"]}
            for r in await conn.fetch(
                """SELECT id, kind, ai_value, final_value, signals, human_changed, ai_verdict, spot_result FROM ops_ai_decision
                   WHERE NOT (COALESCE(final_value, '{}'::jsonb) ? 'superseded') AND created_at > NOW() - make_interval(days => $1)
                   ORDER BY id""", days)]   # 검토 대기(확정 전)도 센다 — 다섯 갈래 중 하나
    names = {r["id"]: r["name"] for r in await conn.fetch("SELECT id, name FROM ops_namespace")}
    return rows, names


async def report(conn, days: int = 90) -> dict:
    """감사 로그 요약·진단이 같이 쓰는 숫자 — 종류별 번복 + 위치 판정 리플레이."""
    rows, names = await load(conn, days)
    s = summarize(rows, names)
    s["days"] = days
    s["location_replay"] = replay_location(location_rows(rows))
    return s


def candidates(rep: dict) -> list[dict]:
    """운영 번복 → 개선 후보(eval_improve --save). 대상 = 번복한 판정(D-<id>, 골든 문항이 아님). 위치 판정만(규칙을 다시 계산할 수 있는 것)."""
    from service.eval.improve import _cand
    loc = next((k for k in rep["kinds"] if k["kind"] == LOCATION), None)
    if not loc or not loc["overrides"]:
        return []
    qids = [f"D-{i}" for i in loc["override_ids"]]
    rp = rep["location_replay"]
    cur = rp["rules"][0] if rp["rules"] else None
    base = (f"등록 위치 번복 {loc['overrides']}/{loc['decided']}({(loc['rate'] or 0) * 100:.0f}%)"
            + (" · 이유: " + ", ".join(f"{k} {v}" for k, v in loc["reasons"].items()) if loc["reasons"] else "")
            + (f" · 잦은 번복 {loc['patterns'][0]['pattern']} {loc['patterns'][0]['n']}건" if loc["patterns"] else ""))
    if rp["best"] and cur:
        b = rp["best"]
        return [_cand("override_location_rule", "override_location", "knowledge_location", f"위치 제안 규칙 — {b['rule']}",
                      f"locator.decide 제안 규칙을 '{b['rule']}'로(확신 규칙은 그대로)", "replay", qids, recommend="try",
                      why=f"{base} · 다시 계산: 맞은 제안 {cur['hit']} → {b['hit']}/{b['n']}, 새로 틀림 0, 확신인데 틀림 {cur['auto_wrong']} → {b['auto_wrong']}",
                      replay=rp)]
    return [_cand("override_location_examples", "override_location", "knowledge_location", "위치 판정 — 자주 번복되는 경계 보강",
                  "번복이 몰린 컬렉션 쌍의 경계 예시를 위치 판정 프롬프트에(프롬프트 관리 · knowledge_locate_system)", "replay", qids,
                  recommend="review", why=f"{base} · 제안 규칙만 바꿔선 나아지지 않음(다시 계산) — 프롬프트·경계 쪽", replay=rp)]
