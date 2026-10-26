"""자가 개선 후보 만들기 (2026-10-23, eval-gate-redesign §10) — 진단(trustkit.eval.diagnose) → 원인마다 "볼 기법 · 바꿀 것 · 잴 방법" 후보.

DB·LLM 없는 순수 함수(scripts/eval_improve.py가 파일·DB를 읽어 넘긴다). 후보는 제안일 뿐 — 사람이 [시도 승인]/[보류]/[기각]하고,
채택은 통과한 판정 기록과 연결될 때만(service.adopt_candidate). 원문은 다루지 않는다: qid·개수·숫자·기법 키만.
잴 방법: replay = 저장된 측정으로 LLM 없이 다시 셈(스크립트가 바로 잼) · retrieval = 검색 측정 재실행(LLM 0, 수 분) · answers = 답 재측정(로컬 LLM ~1시간).
"""
from __future__ import annotations

import json
import re
from typing import Callable, Mapping, Optional

from service.eval.techniques import BY_KEY

# 잠근 시험지(hold-out, 10/26 사용자 승인) — 사람이 쓴 문항·실사용 문항. 진단·AI 묶음·후보를 만들 때 보지 않고 판정 때만 본다
# (같은 문항을 보고 고치고 그 문항으로 채점하면 시험지에만 강해진다 — 10/23 2회차: 전체 +0.9%p인데 사람 문항 −1.2%p).
LOCKED_TYPES = ("human", "real")


def split_locked(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """(진단에 쓸 행, 잠근 문항 qid) — 잠근 층 문항은 한 행도 남기지 않는다."""
    locked = sorted({r["qid"] for r in rows if r.get("type") in LOCKED_TYPES})
    return [r for r in rows if r.get("type") not in LOCKED_TYPES], locked

MEASURES = {"replay": "LLM 0 리플레이(바로 잼)", "retrieval": "검색 재측정(LLM 0, 수 분)", "answers": "답 재측정(로컬 LLM ~1시간)"}

# 근거 없음 하한 리플레이 격자 — 10/06 실측 범위(0.45~0.65) + 파라미터 하한 0/0.05
ABSTAIN_GRID = [(c, p) for c in (0.45, 0.50, 0.55, 0.60, 0.65) for p in (0.0, 0.05)]


def _cand(key: str, cause: str, technique: str, title: str, action: str, measure: str, qids: list[str], *,
          recommend: str = "review", why: str = "", replay: Optional[dict] = None) -> dict:
    assert technique in BY_KEY, technique   # 대장에 없는 기법을 가리키지 않는다(테스트도 확인)
    assert measure in MEASURES and recommend in ("try", "reject", "review")
    return {"key": key, "cause": cause, "technique": technique, "title": title, "action": action, "measure": measure,
            "n_target": len(qids), "qids": sorted(qids), "recommend": recommend, "why": why, "replay": replay}


def abstain_replay(replay_fn: Callable[[Callable[[str], bool]], dict], signals: Mapping[str, dict],
                   should_abstain: Callable[[dict, float, float], bool]) -> dict:
    """하한 격자마다 고침·망가짐 — 망가짐 0 중 고침 최대를 고른다(게이트 규칙과 같이 "나빠짐 0"). 신호 없는 문항은 거절하지 않음."""
    rows = []
    for cut, prm in ABSTAIN_GRID:
        r = replay_fn(lambda q, c=cut, p=prm: bool(signals.get(q)) and should_abstain(signals[q], c, p))
        rows.append({"min_score": cut, "min_param_rank": prm, "refused": r["refused"], "fixed": len(r["fixed"]),
                     "broken": len(r["broken"]), "before": r["before"], "after": r["after"],
                     "fixed_qids": r["fixed"], "broken_qids": r["broken"]})
    safe = [x for x in rows if x["broken"] == 0 and x["fixed"] > 0]
    best = max(safe, key=lambda x: (x["fixed"], -x["min_score"])) if safe else None
    return {"grid": rows, "best": best}


CARRY_STATUSES = ("rejected", "held", "approved")


def carry_decision(prev: Optional[Mapping], recommend: str) -> Optional[Mapping]:
    """이어받을 이전 결정 — prev = 같은 key·cause의 **가장 최근** 후보(상태 무관). 그 후보가 기각·보류·시도 승인이고 추천이 그대로일 때만.
    최근 행이 되돌리기(제안)·채택이면 이어받지 않는다(옛 기각을 다시 살리지 않게, 리뷰 M1). 추천이 바뀌면 새 근거라 다시 묻는다(리뷰 M2).
    시도 승인도 이어받는다 — 측정 중에 진단을 다시 돌리면 승인이 사라져 채택을 못 하던 것(재리뷰)."""
    if not prev or prev.get("status") not in CARRY_STATUSES or prev.get("recommend") != recommend:
        return None
    return prev


def adopt_error(status: str, gate: Optional[Mapping], approved_at) -> Optional[str]:
    """채택할 수 없는 이유(되면 None) — 시도 승인된 후보 + **승인 뒤에** 잰 통과 게이트만(§10-5: 승인 → 적용 → 측정 → 판정).
    기준 시각은 승인 시각(이어받은 승인이면 원래 승인 시각) — 진단을 다시 돌려도 측정한 판정을 그대로 연결할 수 있게(재리뷰)."""
    if status != "approved":
        return "시도 승인된 후보만 채택할 수 있습니다."
    if not gate or gate.get("kind") != "gate" or gate.get("verdict") != "pass":
        return "통과한 게이트 판정만 연결할 수 있습니다."
    if approved_at is None or gate["run_at"] < approved_at:
        return "시도 승인보다 먼저 잰 판정입니다 — 이 후보를 적용한 뒤 잰 판정을 연결해 주세요."
    groups = (gate.get("answers") or {}).get("groups") or {}
    worse = [t for t in LOCKED_TYPES if (groups.get(t) or {}).get("diff", 0) < 0]
    if worse:   # 잠근 시험지에서 내려가면 운영 반영 안 함 — 오차 안이라도(고칠 때 안 본 문항에서 나빠졌다는 신호)
        return f"잠근 문항({', '.join(worse)})에서 정답률이 내려간 측정입니다 — 운영 반영 대신 다시 실험하세요."
    return None


def agreement(rows) -> dict:
    """추천과 사람 결정이 맞았나 — 사람이 직접 정한 후보만(이어받은 줄은 같은 결정의 복사라 빼야 한 번만 센다, 리뷰 M2).
    맞음 = 시도 추천 → 승인·채택, 기각 추천 → 기각. 반대 = 그 반대. 검토 추천·제안·보류는 세지 않는다."""
    agree = disagree = 0
    for r in rows:
        if r.get("carried_from_id") is not None:
            continue
        rec, st, n = r["recommend"], r["status"], r.get("n", 1)
        if (rec == "try" and st in ("approved", "adopted")) or (rec == "reject" and st == "rejected"):
            agree += n
        elif (rec == "try" and st == "rejected") or (rec == "reject" and st in ("approved", "adopted")):
            disagree += n
    return {"agree": agree, "disagree": disagree}


def build_candidates(diag: dict, *, types: Mapping[str, str], mapped: Mapping[str, Optional[str]],
                     abstain: Optional[dict] = None, hypotheses: Optional[list[dict]] = None) -> list[dict]:
    """진단 → 후보 목록(원인 흐름 순서). types = qid → 층, mapped = qid → 용어 매핑(없으면 None), abstain = abstain_replay 결과,
    hypotheses = 로컬 LLM 묶음 [{name, hint, qids}](문맥 있는데 틀림·목록 덜 나옴 등, 사람 확인 전)."""
    c = diag["causes"]
    out: list[dict] = []

    q = c.get("answered_no_answer", [])
    if q:
        best = (abstain or {}).get("best")
        if best:
            out.append(_cand("abstain_threshold", "answered_no_answer", "abstain_prefilter",
                             f"근거 없음 하한 켜기 — 서술 {best['min_score']:.2f} · 파라미터 {best['min_param_rank']:.2f}",
                             "POLICY_ABSTAIN_MIN_SCORE / POLICY_ABSTAIN_MIN_PARAM_RANK 설정", "replay", q, recommend="try",
                             why=f"리플레이: 고침 {best['fixed']} · 망가짐 0 · 정답률 {best['before'] * 100:.1f}→{best['after'] * 100:.1f}%",
                             replay=abstain))
        else:
            fixed_any = max((x["fixed"] for x in (abstain or {}).get("grid", [])), default=0)
            out.append(_cand("abstain_threshold", "answered_no_answer", "abstain_prefilter", "근거 없음 하한 켜기(정책 점수 하한)",
                             "POLICY_ABSTAIN_MIN_SCORE 설정", "replay", q, recommend="reject",
                             why=("리플레이: 어느 하한에서도 망가짐 없이 고치는 문항 없음" if abstain else "검색 신호가 없어 못 잼")
                             + (f"(고침 최대 {fixed_any}건은 망가짐 동반)" if fixed_any else " — 이 문항들은 점수가 높아 하한으로 못 거름"),
                             replay=abstain))
            out.append(_cand("abstain_prompt", "answered_no_answer", "chat_prompt", "답 없는 질문 거절 지시 보강",
                             "채팅 프롬프트: 근거에 질문의 그 내용이 없으면 비슷한 정책으로 단정하지 말고 없다고 답하기", "answers", q,
                             why="하한으로 못 거르는 문항 — 생성 단계에서 거절해야 함"))

    q = c.get("retrieval_miss", [])
    if q:
        cross = [x for x in q if types.get(x) == "cross"]
        rest = [x for x in q if x not in cross]
        if cross:
            out.append(_cand("cross_collection", "retrieval_miss", "collection_suggest", "다른 컬렉션에서 찾아보기 제안",
                             "채팅: 질문이 다른 컬렉션 것 같으면 버튼 제안(기본 검색은 그대로 — 예정 기능)", "retrieval", cross,
                             why="영역 넘김 문항 — 물은 컬렉션 밖에 정답"))
        unmapped = [x for x in rest if not mapped.get(x)]
        if unmapped:
            out.append(_cand("synonyms", "retrieval_miss", "glossary_synonyms", "용어·동의어 보강",
                             "질문에 용어집 매핑이 없는 문항의 표현을 동의어로(임베딩 유사도 게이트)", "retrieval", unmapped,
                             why="검색 실패 중 용어 매핑 없음"))
        mapped_miss = [x for x in rest if mapped.get(x)]
        if mapped_miss:
            out.append(_cand("keyword_rescue", "retrieval_miss", "keyword_rescue", "키워드 구제 슬롯·문맥 정책 수",
                             "POLICY_KEYWORD_RESCUE_SLOTS 3→4~5 또는 문맥 정책 수 10→12", "retrieval", mapped_miss,
                             why="용어는 붙었는데 정답 정책이 상위 밖"))

    q = c.get("refused_with_context", [])
    if q:
        out.append(_cand("answer_when_grounded", "refused_with_context", "chat_prompt", "근거 있으면 답하기 지시",
                         "채팅 프롬프트: 문맥에 해당 정책이 있으면 거절하지 말기", "answers", q, why="정답 정책이 문맥에 있는데 거절"))

    q = c.get("list_incomplete", [])
    if q:
        out.append(_cand("list_answer", "list_incomplete", "list_line_format", "목록 답 빠짐 줄이기",
                         "목록 줄 형식·되묻기 기준·목록 최대 줄(40) 점검 — 묶음 가설 참고", "answers", q,
                         why="목록 질문에서 부분·오답"))

    q = c.get("wrong_with_context", [])
    if q:
        cond = [x for x in q if types.get(x) == "condition"]
        if cond:
            out.append(_cand("condition_hint", "wrong_with_context", "condition_hint", "조건 질문 지시 다듬기",
                             "조건 질문 지시 문구", "answers", cond, why="조건 질문에서 문맥 있는데 틀림"))
        out.append(_cand("grounded_answer", "wrong_with_context", "chat_prompt", "문맥 있는데 틀림 — 묶음별 답 지시",
                         "묶음 가설(사람 확인 후)마다 프롬프트 한 줄", "answers", [x for x in q if x not in cond] or q,
                         why="정답 정책이 문맥에 있는데 답이 틀림 — 생성 단계"))

    for h in hypotheses or []:
        h_q = sorted(set(h.get("qids") or []))
        if not h_q:
            continue
        # 가설은 실제로 시도할 후보에 — 기각 추천(리플레이로 이미 안 되는 것)보다 검토·시도 후보 먼저
        for cand in sorted(out, key=lambda x: x["recommend"] == "reject"):
            if cand["cause"] == h.get("cause") and set(h_q) & set(cand["qids"]):
                cand.setdefault("hypotheses", []).append({"name": h["name"], "hint": h.get("hint", ""), "qids": h_q})
                break
    return out


def parse_groups(text: str, allowed: set[str]) -> list[dict]:
    """LLM 답에서 JSON 묶음만 — 없는 qid는 버리고, 두 묶음에 든 qid는 앞 묶음에만, 빠진 qid는 "기타"로(유실·중복 0)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        groups = json.loads(m.group(0)).get("groups", []) if m else []
    except (ValueError, AttributeError):
        groups = []
    seen: set[str] = set()
    out = []
    for g in groups if isinstance(groups, list) else []:
        if not isinstance(g, dict):
            continue
        qs = [q for q in (g.get("qids") or []) if isinstance(q, str) and q in allowed and q not in seen]
        seen |= set(qs)
        if qs:
            out.append({"name": str(g.get("name") or "이름 없음")[:60], "hint": str(g.get("hint") or "")[:120], "qids": sorted(qs)})
    rest = sorted(allowed - seen)
    if rest:
        out.append({"name": "기타(묶이지 않음)", "hint": "", "qids": rest})
    return out
