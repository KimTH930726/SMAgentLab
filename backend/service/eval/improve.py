"""자가 개선 후보 만들기 (2026-10-23, eval-gate-redesign §10) — 진단(trustkit.eval.diagnose) → 원인마다 "볼 기법 · 바꿀 것 · 잴 방법" 후보.

DB·LLM 없는 순수 함수(scripts/eval_improve.py가 파일·DB를 읽어 넘긴다). 후보는 제안일 뿐 — 사람이 [시도 승인]/[보류]/[기각]하고,
채택은 통과한 판정 기록과 연결될 때만(service.adopt_candidate). 원문은 다루지 않는다: qid·개수·숫자·기법 키만.
잴 방법: replay = 저장된 측정으로 LLM 없이 다시 셈(스크립트가 바로 잼) · retrieval = 검색 측정 재실행(LLM 0, 수 분) · answers = 답 재측정(로컬 LLM ~1시간).
"""
from __future__ import annotations

import json
import re
from typing import Callable, Iterable, Mapping, Optional

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
          recommend: str = "review", why: str = "", replay: Optional[dict] = None, change: Optional[dict] = None) -> dict:
    """change = 기계로 적용할 변경(있으면 [실험해 보기]가 실제로 잰다 — service/eval/experiment.py). 없으면 글(action)뿐 → 개발 작업."""
    assert technique in BY_KEY, technique   # 대장에 없는 기법을 가리키지 않는다(테스트도 확인)
    assert measure in MEASURES and recommend in ("try", "reject", "review")
    return {"key": key, "cause": cause, "technique": technique, "title": title, "action": action, "measure": measure,
            "n_target": len(qids), "qids": sorted(qids), "recommend": recommend, "why": why, "replay": replay, "change": change}


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


CARRY_STATUSES = ("rejected", "held", "approved", "failed")   # 실패(10/30)도 — 같은 변경을 다시 묻지 않게([다시 실행]은 남음)

# 화면 단계(10/30 사용자 "실험 끝났는데 '실험 중'?") — 상태 + 마지막 실험 작업 + 운영 반영 판정으로 서버가 정한다(화면이 따로 계산하면 어긋남)
PHASES = ("proposed", "running", "error", "review", "adopted", "failed", "rejected", "held")


def measurement_fresh(run_at, approved_at, baseline_run_at) -> bool:
    """이 측정으로 지금 결정할 수 있나 — 시도 승인 뒤에, 지금 점수(기준선) 뒤에 잰 것. 아니면 실패가 아니라 다시 재야 하는 측정
    (예전 진단에서 넘어온 측정 · 다른 후보 반영으로 기준선이 옮겨짐, 재리뷰 10/30 M1)."""
    return approved_at is not None and run_at >= approved_at and (baseline_run_at is None or run_at >= baseline_run_at)


def phase_of(status: str, job: Optional[Mapping], adopt_check: Optional[Mapping]) -> str:
    """제안 · 실험 중 · 실험 오류 · 검토 대기(통과, 반영할지 사람이) · 반영 · 실패 · 기각 · 보류.
    approved(시도 승인): 작업이 돌면 실험 중 → **새 측정**(승인·기준선 뒤)이 반영 가능이면 검토 대기 / 불가면 실패(자동 종결 전 잠깐)
    → 새 측정 없이 작업이 실패면 실험 오류 → 그 밖(측정 전 · 예전 측정뿐 · 개발 작업)은 실험 중."""
    if status != "approved":
        return {"proposed": "proposed", "adopted": "adopted", "failed": "failed", "rejected": "rejected", "held": "held"}[status]
    if job and job.get("status") in ("queued", "running"):
        return "running"
    if adopt_check and adopt_check.get("fresh"):
        return "review" if adopt_check.get("ok") else "failed"
    if job and job.get("status") == "failed":
        return "error"
    return "running"


def carry_decision(prev: Optional[Mapping], recommend: str) -> Optional[Mapping]:
    """이어받을 이전 결정 — prev = 같은 key·cause의 **가장 최근** 후보(상태 무관). 그 후보가 기각·보류·시도 승인이고 추천이 그대로일 때만.
    최근 행이 되돌리기(제안)·채택이면 이어받지 않는다(옛 기각을 다시 살리지 않게, 리뷰 M1). 추천이 바뀌면 새 근거라 다시 묻는다(리뷰 M2).
    시도 승인도 이어받는다 — 측정 중에 진단을 다시 돌리면 승인이 사라져 채택을 못 하던 것(재리뷰)."""
    if not prev or prev.get("status") not in CARRY_STATUSES or prev.get("recommend") != recommend:
        return None
    return prev


def adopt_error(status: str, gate: Optional[Mapping], approved_at, *, measured_ids: Optional[Iterable[int]] = None,
                baseline_run_at=None) -> Optional[str]:
    """채택할 수 없는 이유(되면 None) — 시도 승인된 후보 + **승인 뒤에** 잰 통과 게이트만(§10-5: 승인 → 적용 → 측정 → 판정).
    기준 시각은 승인 시각(이어받은 승인이면 원래 승인 시각) — 진단을 다시 돌려도 측정한 판정을 그대로 연결할 수 있게(재리뷰).
    measured_ids(주면) = 이 후보를 잰 판정(eval_gate.py --candidate)만 · baseline_run_at(주면) = 지금 점수보다 예전 측정은 안 됨 —
    운영 반영이 그 측정을 지금 점수로 옮기므로, 남의 측정·옛 측정으로 지금 점수가 되돌아가지 않게(v2.172 리뷰 M3)."""
    if status != "approved":
        return "시도 승인된 후보만 채택할 수 있습니다."
    if not gate or gate.get("kind") != "gate" or gate.get("verdict") != "pass":
        return "통과한 게이트 판정만 연결할 수 있습니다."
    if approved_at is None or gate["run_at"] < approved_at:
        return "시도 승인보다 먼저 잰 판정입니다 — 이 후보를 적용한 뒤 잰 판정을 연결해 주세요."
    if measured_ids is not None and gate.get("id") not in set(measured_ids):
        return "이 후보를 잰 측정이 아닙니다 — eval_gate.py --save … --candidate <후보 번호>로 잰 측정을 연결해 주세요."
    if baseline_run_at is not None and gate["run_at"] < baseline_run_at:
        return "지금 점수보다 예전 측정입니다 — 반영하면 지금 점수가 예전으로 돌아갑니다. 지금 상태에서 다시 재 주세요."
    groups = (gate.get("answers") or {}).get("groups") or {}
    worse = [t for t in LOCKED_TYPES if (groups.get(t) or {}).get("diff", 0) < 0]
    if worse:   # 잠근 시험지에서 내려가면 운영 반영 안 함 — 오차 안이라도(고칠 때 안 본 문항에서 나빠졌다는 신호)
        return f"잠근 문항({', '.join(worse)})에서 정답률이 내려간 측정입니다 — 운영 반영 대신 다시 실험하세요."
    return confident_wrong_error(gate.get("answers"))


def confident_wrong_error(answers: Optional[Mapping]) -> Optional[str]:
    """운영 반영 조건 B안(10/30 사람 결정 "틀린 단정보다 모른다가 낫다") — 답을 잰 측정이면 단정 오답(채점 "오답")이 늘지 않아야 하고
    2회 이상 재야 한다(1회는 같은 지시문으로도 17.9%↔19.8%로 흔들렸다). 게이트 통과(나빠지지 않았나)와 따로 — 반영은 실제로 나아졌을 때만.
    답을 안 잰 측정(검색만)은 해당 없음. 이 칸이 없는 옛 측정은 다시 재게 한다."""
    if not answers:
        return None
    cw = answers.get("confident_wrong")
    if not cw or cw.get("before") is None:
        return "단정 오답을 재지 않은 옛 측정입니다 — 지금 eval_gate.py로 다시 판정해 주세요."
    if min(cw.get("reps_before") or 0, cw.get("reps_after") or 0) < 2:
        return "답을 1회만 잰 측정입니다 — 흔들림이 커서 2회 이상(--repeat 2) 잰 측정만 운영 반영할 수 있어요."
    if cw["after"] > cw["before"]:
        return f"단정 오답이 늘었습니다({cw['before'] * 100:.1f}% → {cw['after'] * 100:.1f}%) — 틀린 답 단정이 늘면 운영 반영하지 않습니다."
    return None


def agreement(rows) -> dict:
    """추천과 사람 결정이 맞았나 — 사람이 직접 정한 후보만(이어받은 줄은 같은 결정의 복사라 빼야 한 번만 센다, 리뷰 M2).
    맞음 = 시도 추천 → 승인·채택, 기각 추천 → 기각. 반대 = 그 반대. 검토 추천·제안·보류는 세지 않는다."""
    agree = disagree = 0
    for r in rows:
        if r.get("carried_from_id") is not None:
            continue
        rec, st, n = r["recommend"], r["status"], r.get("n", 1)
        # 실패(자동 종결)는 사람이 '해 보자'고 정한 뒤의 결과 — 사람 결정은 시도 승인으로 센다
        if (rec == "try" and st in ("approved", "adopted", "failed")) or (rec == "reject" and st == "rejected"):
            agree += n
        elif (rec == "try" and st == "rejected") or (rec == "reject" and st in ("approved", "adopted", "failed")):
            disagree += n
    return {"agree": agree, "disagree": disagree}


# 적재 검사 → 개선 후보(10/26 사용자 "적재가 잘 됐는지 스스로 검수하는 개선 루프"). 처리 기법 후보와 같은 흐름으로 사람이 승인.
INGEST_REMEDY: dict[str, tuple[str, str, str, str]] = {   # 검사 키 → (기법 키, 제목, 바꿀 것, 잴 방법)
    "policy_param_derived": ("param_derive", "파라미터 파생 단계 실행(v0 설계 #9)",
                             "derive_policy_params.py extract → 사본 DB에 load → 같은 문항으로 측정(값은 원문에 있는 것만)", "answers"),
    # 10/29부터 켬(policy_ref_chat) — 남은 어긋남 = 켰는데도 표가 문맥에 안 들어간 문항
    "usage_ref_table": ("ref_table_chat", "참고표가 문맥에 안 들어간 문항(v0 설계 #12)", "참고표 표 단위 검색 순위 · 결정 조건이 빠지면 되묻기", "answers"),
    "usage_overview": ("ref_table_chat", "개요를 채팅 문맥에 연결(v0 설계 #12)", "개요 문서 검색 채널 추가", "answers"),
    "knowledge_dup": ("knowledge_dedupe", "같은 지식 조각 정리", "중복 쌍 중 하나 은퇴(원문 페이지 확인은 로컬 LLM)", "retrieval"),
}


# 적재 검사를 "꺼 둠(결정)"으로 — 결정은 그 검사 후보에 사람이 내린 기각·보류(사유 포함, 다음 진단이 이어받음)에서만 온다.
# 10/29 전엔 결정을 check_ingest.py 코드에 적어 둬서, 화면 후보는 계속 "결정 전"이고 진단을 다시 안 돌리면 옛 후보가 남았다(사용자 지적).
LATEST_INGEST_DECISIONS_SQL = ("SELECT DISTINCT ON (key) key, status, review_reason FROM eval_candidate "
                               "WHERE key LIKE 'ingest\\_%' ORDER BY key, id DESC")
DECIDED_OFF = {"rejected": "안 함"}   # 보류(나중에)는 끄지 않는다 — 후보로 남아 이어받음(꺼 두면 화면에서 사라져 되돌릴 길이 없었다, 재리뷰 10/29)
# 번복(위치 판정) 후보를 만드는 진단 = 업무지식 축의 가장 최근 진단
LATEST_KNOWLEDGE_RUN_SQL = "(SELECT i.id FROM eval_improvement_run i JOIN eval_gate_run g ON g.id = i.gate_run_id WHERE g.axis = 'knowledge' ORDER BY i.created_at DESC, i.id DESC LIMIT 1)"
# 후보 → 진단한 평가 축(eval_gate --candidate · 채택 · 이어받기 공통)
CANDIDATE_AXIS_SQL = ("SELECT g.axis FROM eval_candidate c JOIN eval_improvement_run i ON i.id = c.run_id "
                      "JOIN eval_gate_run g ON g.id = i.gate_run_id WHERE c.id = $1")
# 적재 검사가 보는 기능 설정 — 켜져 있으면 옛 "안 함" 결정이 있어도 다시 센다(결정이 바뀐 것)
FEATURE_SETTINGS = {"usage_ref_table": "policy_ref_chat", "usage_overview": "policy_ref_overview"}


def feature_on(check_key: str, settings) -> bool:
    attr = FEATURE_SETTINGS.get(check_key)
    if attr == "policy_ref_chat":   # 기법 대장 스위치가 기준(11/03 — 꺼 둔 기능을 대상으로 후보가 생기지 않게)
        from core import technique_flags
        return bool(technique_flags.is_on("ref_table_chat"))
    return bool(attr and getattr(settings, attr, False))


def carry_allowed(cand_key: str, settings) -> bool:
    """이전 결정을 이어받아도 되나 — 기능을 켠 적재 검사 후보는 안 된다(옛 기각이 켠 뒤의 새 후보를 묻지도 않고 기각하던 것, 리뷰 10/29)."""
    return not (cand_key.startswith("ingest_") and feature_on(cand_key[len("ingest_"):], settings))


def decided_off(check_key: str, decisions: Mapping[str, Mapping], *, feature_on: bool = False) -> Optional[str]:
    """적재 검사 키 → "안 함 — 사유" / None. decisions = 후보 key → 가장 최근 행(status·review_reason).
    기능이 지금 켜져 있으면(설정을 켬 = 결정이 바뀜) None — 다시 어긋남으로 센다."""
    d = decisions.get(f"ingest_{check_key}")
    if feature_on or not d or d.get("status") not in DECIDED_OFF:
        return None
    return f"{DECIDED_OFF[d['status']]} — {d.get('review_reason') or '사유 없음'}"


def ingest_axis(check_key: str) -> str:
    """적재 검사가 어느 평가 축 것인가 — 지식 조각 검사는 업무지식, 나머지(정책·참고표·개요·용어)는 정책서."""
    return "knowledge" if check_key.startswith("knowledge_") else "policy"


def ingest_candidates(results: list[dict], locked: Iterable[str] = (), *, axis: Optional[str] = None) -> list[dict]:
    """어긋난 적재 검사(bad > 0) → 후보. 대응표에 없는 검사는 "적재 계약 다시 확인" 한 줄. cause = "ingest"(진단 칸과 별개 — 흐름 단계가 아니라 데이터).
    locked = 잠근 시험지 qid — 후보가 겨냥할 문항에서 뺀다(옛 적재 검수 기록엔 잠근 문항이 섞여 있을 수 있어서, 리뷰 M1)."""
    locked = set(locked)
    out = []
    for r in results:
        if not r.get("bad") or r.get("decided"):   # 결정으로 꺼 둔 항목(파라미터 파생·참고표 채팅 등, 10/29)은 후보로 다시 띄우지 않는다
            continue
        if axis is not None and ingest_axis(r["key"]) != axis:   # 축마다 자기 데이터 검사만(10/29 업무지식도 같은 루프)
            continue
        tech, title, action, measure = INGEST_REMEDY.get(
            r["key"], ("ingest_contract", f"적재 다시 확인 — {r['name']}", "적재 스크립트·설계 완료 기준 대조", "retrieval"))
        acc = r.get("accuracy") or {}
        why = f"{r['store']} · {r['name']}: {r['bad']} / {r['total']} 어긋남 — 근거 {r['ref']}"
        if acc.get("acc") is not None:
            why += f" · 영향 문항 {acc['n']}개 정답률 {acc['acc'] * 100:.1f}%"
            why += (f"(나머지 {acc['rest_n']}개 {acc['rest_acc'] * 100:.1f}%)" if acc.get("rest_n")
                    else "(나머지 없음 — 비교 불가, 정답률 차이로 효과를 말할 수 없음)")
        if not r.get("qids") and r["key"].startswith("knowledge_"):
            why += " · 골든 문항과 연결 안 되는 데이터(지식 골든 별도)"
        out.append(_cand(f"ingest_{r['key']}", "ingest", tech, title, action, measure,
                         [q for q in r.get("qids") or [] if q not in locked], why=why))
    return out


def build_candidates(diag: dict, *, types: Mapping[str, str], mapped: Mapping[str, Optional[str]],
                     abstain: Optional[dict] = None, hypotheses: Optional[list[dict]] = None, axis: str = "policy") -> list[dict]:
    """진단 → 후보 목록(원인 흐름 순서). types = qid → 층, mapped = qid → 용어 매핑(없으면 None), abstain = abstain_replay 결과,
    hypotheses = 로컬 LLM 묶음 [{name, hint, qids}](문맥 있는데 틀림·목록 덜 나옴 등, 사람 확인 전)."""
    c = diag["causes"]
    out: list[dict] = []
    src = "업무지식" if axis == "knowledge" else "정책"   # 후보 문구의 정답 종류

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
    if q and axis == "knowledge":
        # 업무지식 검색 측정엔 용어 매핑 칸이 없다 — 정책서식 갈래(매핑 있음/없음·키워드 구제 슬롯)를 나누면 근거 없는 후보가 된다(10/29)
        out.append(_cand("knowledge_retrieval", "retrieval_miss", "chunk_header", "업무지식 검색 실패 — 조각 머리글·질문 표현",
                         "놓친 문항의 정답 조각: 머리글(제목 경로)·용어집 동의어 점검 → 검색 재측정", "retrieval", q,
                         why="정답 업무지식 조각이 문맥에 없음"))
        q = []
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
                             "POLICY_KEYWORD_RESCUE_SLOTS 3→5 (실험은 5로)", "retrieval", mapped_miss,
                             why="용어는 붙었는데 정답 정책이 상위 밖", change={"env": {"POLICY_KEYWORD_RESCUE_SLOTS": "5"}}))

    q = c.get("refused_with_context", [])
    if q:
        out.append(_cand("answer_when_grounded", "refused_with_context", "chat_prompt", "근거 있으면 답하기 지시",
                         f"채팅 프롬프트: 문맥에 해당 {src}이 있으면 거절하지 말기", "answers", q, why=f"정답 {src}이 문맥에 있는데 거절",
                         change={"prompt_append": "- [참고 문서]에 질문이 묻는 그 내용이 있으면 \"관련 지식을 찾지 못했습니다\"라고 하지 말고 그 내용으로 답한다."}))

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
                         why=f"정답 {src}이 문맥에 있는데 답이 틀림 — 생성 단계"))
        if axis == "knowledge":
            # 10/30 실측: 업무지식 문맥 = 지식 3조각 + 상위 섹션 보충 평균 30 + 정책 서술 10(1.8만 자), 틀린 14건 = 지어냄 7·섞음 6 —
            # 보충·정책 서술을 빼고 지식 조각 위주로(측정 프로세스에서만, 운영은 그대로)
            out.append(_cand("knowledge_context_narrow", "wrong_with_context", "parent_expansion", "문맥 좁히기 — 보충·정책 빼기",
                             "업무지식 질문 문맥에서 상위 섹션 보충을 끄고 정책(파라미터·서술)을 0개로", "answers", q,
                             why="문맥이 길고 다른 내용이 섞여 지어냄·섞음", change={"policy_top_k": 0, "no_expand": True}))
            # 답 뒤 근거 확인(10/30) — 초안의 주장이 문맥에 없으면 빼거나 "못 찾음"(trustkit.answer.grounding). 질문마다 LLM 1회 추가
            out.append(_cand("answer_verify", "wrong_with_context", "grounding_check", "답 뒤 근거 확인",
                             "답을 만든 뒤 같은 모델이 주장마다 문맥 근거를 확인 — 없는 주장은 빼고 핵심이 없으면 '못 찾음'", "answers", q,
                             why="검색은 맞는데 답에서 지어냄·섞음 — 틀린 단정", change={"verify": True}))

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
