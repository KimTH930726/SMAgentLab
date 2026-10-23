"""측정 한 판의 헤드라인 숫자 (2026-10-23, 평가 게이트 개편 1단계) — 판정 기록에 "후보(after) 쪽이 몇 점이었나"를 같이 남긴다.

게이트(gate.py)는 전/후 차이만 본다. 기록·화면은 그 판의 점수 자체(Recall·Top-1·정답률·구간·라벨 분포·층별)와
"검색은 맞았는데 답이 틀린 문항"(답 생성 쪽 문제 후보)이 필요하다. 입력은 채점 결과 행뿐(파일 형식·DB 무관), 원문은 받지 않는다.
골든셋 버전 = 측정 문항 qid 집합의 지문 — 세트가 바뀌면 다른 값이라 서로 다른 세트의 점수를 나란히 놓는 실수를 막는다.
"""
from __future__ import annotations

import collections
import hashlib
from typing import Iterable, Mapping

from trustkit.eval.gate import per_question
from trustkit.eval.stats import estimate


def golden_version(qids: Iterable[str]) -> str:
    """"문항 수:sha1 앞 12자" — 순서·중복과 무관(집합)."""
    s = sorted({q for q in qids if q})
    digest = hashlib.sha1("\n".join(s).encode("utf-8")).hexdigest()[:12]
    return f"{len(s)}:{digest}"


def retrieval_summary(rows: Iterable[Mapping]) -> dict:
    """행 = {qid, hit, top1?, type?}. 같은 qid가 여러 번이면 마지막 행(검색은 결정적 — 한 판에 한 번)."""
    by_q = {r["qid"]: r for r in rows if r.get("qid")}
    by_type: dict = {}
    for r in by_q.values():
        x = by_type.setdefault(r.get("type") or "-", {"n": 0, "hit": 0, "top1": 0})
        x["n"] += 1
        x["hit"] += bool(r.get("hit"))
        x["top1"] += bool(r.get("top1"))
    n = len(by_q)
    hit = sum(bool(r.get("hit")) for r in by_q.values())
    top1 = sum(bool(r.get("top1")) for r in by_q.values())
    return {"n": n, "hit": hit, "top1": top1, "recall": hit / n if n else None, "top1_rate": top1 / n if n else None,
            "by_type": by_type}


def answer_summary(rows: Iterable[Mapping], correct: str = "정답") -> dict:
    """행 = {qid, label, type?, cluster?, rep?}(gate.answer_gate와 같은 모양). 정답률 = 문항별 회차 평균의 평균 + 95% 구간(군집).
    labels = 회차까지 센 라벨 분포(채점기가 무엇을 얼마나 냈나 — 판정불가는 호출 쪽이 이미 뺀다)."""
    rows = list(rows)
    acc, meta = per_question(rows, correct)
    qs = sorted(acc)
    cl = [meta[q][1] or q for q in qs]
    e = estimate([acc[q] for q in qs], cl) if qs else {"mean": None, "lo": None, "hi": None}
    by_type: dict = {}
    for q in qs:
        by_type.setdefault(meta[q][0] or "-", []).append(acc[q])
    reps = collections.Counter(r["qid"] for r in rows)
    return {"n": len(qs), "accuracy": e["mean"], "lo": e["lo"], "hi": e["hi"],
            "labels": dict(collections.Counter(r["label"] for r in rows)),
            "reps": max(reps.values()) if reps else 0,
            "by_type": {t: {"n": len(v), "accuracy": sum(v) / len(v)} for t, v in sorted(by_type.items())}}


def retrieval_ok_answer_wrong(hits: Mapping[str, bool], answer_rows: Iterable[Mapping], correct: str = "정답") -> list[str]:
    """검색은 정답 정책을 문맥에 넣었는데(hit) 답은 회차 과반이 틀린 문항 — 답 생성·문맥 다듬기 쪽을 볼 후보. 양쪽에 다 있는 문항만."""
    acc, _ = per_question(answer_rows, correct)
    return sorted(q for q, a in acc.items() if hits.get(q) and a < 0.5)
