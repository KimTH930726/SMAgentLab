"""틀린 문항 진단 + 리플레이 (2026-10-23, 자가 개선 루프 — OpsLens eval-gate-redesign §10). 표준 라이브러리만.

진단 = 오류 분석의 축 코딩(Husain·Shankar): 틀린 문항을 흐름에서 **첫 실패** 한 칸에 넣고 칸별 개수를 센다. 칸은 RAG 흐름 단계
(LLM 호출 → 근거 판정 → 검색 → 거절 → 답 모양 → 생성) — 앞 단계가 실패하면 뒤 단계는 보지 않는다(앞 실패가 뒤를 끌고 감).
리플레이 = 저장된 측정으로 "이 규칙을 바꾸면 문항마다 어떻게 되나"를 LLM 없이 다시 센다 — 고침 m · 망가짐 k(게이트의 나빠짐 0과 같은 기준).
입력은 채점·측정 행뿐(파일 형식·DB 무관), 원문은 받지 않는다.
"""
from __future__ import annotations

from typing import Callable, Iterable, Mapping

from trustkit.eval.gate import per_question

# (키, 이름, 볼 곳) — 순서 = 흐름 순서 = 판정 우선순위
CAUSES: tuple[tuple[str, str, str], ...] = (
    ("llm_error", "LLM 오류", "LLM 연결·게이트웨이 — 답 품질 문제가 아님"),
    ("answered_no_answer", "답 없는 질문에 답함", "근거 없음 판정(하한)·거절 지시"),
    ("retrieval_miss", "검색 실패", "정답 정책이 문맥에 없음 — 용어·동의어·검색 범위·순위"),
    ("refused_with_context", "근거 있는데 거절", "답 생성 지시(근거가 있으면 답하기)"),
    ("list_incomplete", "목록 덜 나옴", "목록 답 문맥·상한·되묻기"),
    ("wrong_with_context", "문맥 있는데 틀림", "답 생성 — 채점 이유 묶음으로 가설"),
)
CAUSE_KEYS = tuple(k for k, _, _ in CAUSES)


def _majority(labels: list[str]) -> str:
    """가장 많은 라벨(동률이면 먼저 나온 것)."""
    best = max(set(labels), key=lambda x: (labels.count(x), -labels.index(x)))
    return best


def diagnose(rows: Iterable[Mapping], *, hits: Mapping[str, bool], negative: Iterable[str], list_types: Iterable[str] = (),
             correct: str = "정답", refused: str = "거절") -> dict:
    """행 = {qid, label, type?, error?(bool)}. 틀린 문항 = 회차 평균 정답 < 0.5(retrieval_ok_answer_wrong과 같은 기준).
    hits = 문항별 정답 정책이 문맥에 들었나, negative = 답 없는 질문(거절이 정답) qid. 반환: {n, n_failed, causes: {키: [qid]}}.
    성질: 칸끼리 겹치지 않고 합 = n_failed(한 문항 = 한 칸)."""
    rows = list(rows)
    acc, meta = per_question(rows, correct)
    neg, lists = set(negative), set(list_types)
    by_q: dict[str, list[Mapping]] = {}
    for r in rows:
        by_q.setdefault(r["qid"], []).append(r)
    causes: dict[str, list[str]] = {k: [] for k in CAUSE_KEYS}
    for q in sorted(acc):
        if acc[q] >= 0.5:
            continue
        rs = by_q[q]
        label = _majority([r["label"] for r in rs])
        # 회차 과반이 오류일 때만 LLM 오류 — 하나만 오류면 나머지 회차의 답 품질 문제를 가린다(리뷰 L2)
        if sum(bool(r.get("error")) for r in rs) * 2 > len(rs):   # 반반이면 정상 회차의 답 품질로 본다(재리뷰)
            key = "llm_error"
        elif q in neg:
            key = "answered_no_answer"
        elif not hits.get(q):
            key = "retrieval_miss"
        elif label == refused:
            key = "refused_with_context"
        elif meta[q][0] in lists:
            key = "list_incomplete"
        else:
            key = "wrong_with_context"
        causes[key].append(q)
    return {"n": len(acc), "n_failed": sum(len(v) for v in causes.values()), "causes": causes}


def replay(current: Mapping[str, float], would_refuse: Callable[[str], bool], negative: Iterable[str]) -> dict:
    """거절 규칙 리플레이 — 규칙이 거절하는 문항은 답 없는 질문이면 1(맞음), 아니면 0(틀림), 나머지는 지금 점수 그대로.
    current = 문항별 회차 평균 정답(per_question). 다른 답은 바뀌지 않는다는 가정(거절은 LLM을 부르기 전이라 다른 문항엔 영향 없음).
    반환: 전/후 평균, 고침(점수 오름)·망가짐(점수 내림) qid."""
    neg = set(negative)
    after = {q: ((1.0 if q in neg else 0.0) if would_refuse(q) else v) for q, v in current.items()}
    qs = sorted(current)
    n = len(qs) or 1
    return {"n": len(qs), "before": sum(current.values()) / n, "after": sum(after.values()) / n,
            "fixed": [q for q in qs if after[q] > current[q]], "broken": [q for q in qs if after[q] < current[q]],
            "refused": sum(bool(would_refuse(q)) for q in qs)}
