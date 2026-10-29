"""정확도 게이트 — 전/후 측정 파일을 받아 trustkit.eval.gate로 통과/실패를 판정한다(검색 나빠짐 0 · 답 의미 있는 하락 없음).

    python scripts/eval_gate.py --answers-before eval_out/judged_A.jsonl --answers-after eval_out/judged_B.jsonl \
        [--retrieval-before eval_out/retr_A.jsonl --retrieval-after eval_out/retr_B.jsonl] [--save "무엇을 바꿨나"]
    python scripts/eval_gate.py --baseline --answers-after ... [--retrieval-after ...] --save "기준선 이름"   # 단독 측정 → 기준선

- 답 파일: eval_answers judge 결과(같은 날 채점한 것끼리 — 채점기가 날마다 흔들림). 회차(rep)는 문항별 평균.
- 검색 파일: eval_golden_v0 --details 결과(정답 세트 줄만, qid로 짝).
- --axis: 정책서(기본)·지식 — 기준선은 축마다 한 줄. 지식 = scripts/eval_knowledge_retrieval·eval_knowledge_answers 결과.
- 답을 못 낸 문항(오류·채점실패 — 게이트웨이 거부 등)은 정답률에서 빼고 따로 센다. 새로 못 낸 문항이 생기면 게이트 실패(비교에서 빠져 하락을 못 봄).
- 결과 파일이 지식 측정(type/set = knowledge)인데 --axis가 다르면 입력 오류 — 축을 빠뜨려 다른 축 기준선을 덮지 않게.
- --save: 판정 기록(eval_gate_run, 2026-10-23 평가 게이트 개편 1단계)에 한 줄 — 품질 › 평가 게이트 화면이 이것을 읽기만 한다.
  DB를 쓰므로 컨테이너 안에서. 통과한 판정은 기준선이 된다(--keep-baseline이면 그대로), --baseline은 판정 없이 기준선만.
출력은 숫자만(질문·답 원문 없음, 기록도 qid·라벨·숫자만). 종료 코드: 통과 0, 실패 1, 입력 오류 2 — 자동 실행에 그대로 쓸 수 있게.
절차 전체: docs/domains/quality/trustkit-design.md §5 · docs/domains/quality/eval-gate-redesign.md §4.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs" / "trustkit"))   # 호스트에서도(표준 라이브러리만)
from trustkit.eval.gate import answer_gate, per_question, retrieval_gate  # noqa: E402
from trustkit.eval.summary import (answer_summary, failure_qids, golden_version,  # noqa: E402
                                   retrieval_ok_answer_wrong, retrieval_summary, scored_rows)


def _rows(path: str) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def _failures(rows: list[dict]) -> list[str]:
    """답을 못 낸 문항(회차 하나라도 오류·채점실패 — 게이트웨이 거부 등) — 정답률엔 안 넣고 따로 보인다(trustkit failure_qids)."""
    return sorted(failure_qids(rows))


def _answer_rows(rows: list[dict]) -> list[dict]:
    # 판정불가(정답 데이터가 모두 꺼짐)·장애 문항(회차 하나라도 오류·채점실패면 그 문항 전체)은 분모에서 뺀다 — 리포트·층별 문항과 같은 규칙
    return [{"qid": r["qid"], "label": r["judge"]["label"], "type": r.get("type"), "cluster": r.get("cluster"), "rep": r.get("rep", 0)}
            for r in scored_rows(rows)]


def _axis_of(rows: list[dict]) -> str | None:
    """결과 파일이 어느 축의 측정인가 — 지식 측정(eval_knowledge_*)은 줄마다 type/set = knowledge. 섞였거나 비면 None(판단 안 함)."""
    kinds = {"knowledge" if (r.get("type") == "knowledge" or r.get("set") == "knowledge") else "policy" for r in rows if r.get("qid")}
    return kinds.pop() if len(kinds) == 1 else None


def _judge_meta(rows: list[dict]) -> dict:
    """채점 조건 — 화면이 "같은 날 같은 기준으로 채점한 비교인가"를 보여 준다. 답 생성 모델은 파일에 있을 때만(eval_answers collect가 기록)."""
    pick = lambda k: sorted({str(r[k]) for r in rows if r.get(k)})   # noqa: E731
    return {"judge_model": pick("judge_model"), "judged_on": pick("judged_on"), "rubrics": pick("judge_rubric"),
            "answer_model": pick("answer_model"), "reps": len({r.get("rep", 0) for r in rows})}


def _judge_pairs(rows: list[dict]) -> set:
    return {((r.get("judge_rubric") or ""), (r.get("judged_on") or "")) for r in rows}


def _retrieval_rows(rows: list[dict], mode: str) -> list[dict]:
    # 답 없는 질문(negative)은 "정답 들어감" 분모에서 뺀다. mode 없는 파일(지식 측정)은 그대로
    return [r for r in rows if r.get("set") != "negative" and r.get("qid") and r.get("mode", mode) == mode]


def _pct(x):
    return "-" if x is None else f"{x * 100:+.1f}" if x < 0 or x > 0 else "+0.0"


def _moved(before: list[dict], after: list[dict]) -> tuple[list[str], list[str]]:
    """답이 좋아진·나빠진 qid(문항별 회차 평균) — 화면의 "나빠진 문항" 목록. answer_gate는 개수만 준다."""
    b, _ = per_question(before)
    a, _ = per_question(after)
    qs = sorted(set(a) & set(b))
    return [q for q in qs if a[q] > b[q]], [q for q in qs if a[q] < b[q]]


def evaluate(a: argparse.Namespace) -> dict:
    """판정 + 기록할 모양. 출력·저장은 호출 쪽 — 계산은 여기 한 곳(화면은 이 결과를 읽기만, 재계산 없음)."""
    rec: dict = {"kind": "baseline" if a.baseline else "gate", "retrieval": None, "answers": None, "lines": [], "ok": True}
    say = rec["lines"].append
    after_qids: set = set()
    before_qids: set = set()
    hits = None

    if a.retrieval_after:
        raw_ra = _rows(a.retrieval_after)
        after_qids |= {r["qid"] for r in raw_ra if r.get("qid")}   # 버전은 거르기 전 문항 집합(답 없는 질문 포함) — 리뷰 M2
        ra = _retrieval_rows(raw_ra, a.mode)
        hits = {r["qid"]: bool(r["hit"]) for r in ra}
        rs = retrieval_summary(ra)
        rec["summary_retrieval"] = rs
        if a.retrieval_before:
            raw_rb = _rows(a.retrieval_before)
            before_qids |= {r["qid"] for r in raw_rb if r.get("qid")}
            rb = _retrieval_rows(raw_rb, a.mode)
            v = retrieval_gate({r["qid"]: r["hit"] for r in rb}, hits, {r["qid"]: r.get("type") or "-" for r in ra})
            rec["retrieval"] = v
            rec["ok"] &= v["pass"]
            if v.get("error"):
                say(f"[검색] 실패 — {v['error']}(qid·mode 확인)")
            say(f"[검색] {'통과' if v['pass'] else '실패'} — Recall {v['before']}/{v['n']} → {v['after']}/{v['n']} · "
                f"좋아짐 {len(v['up'])} 나빠짐 {len(v['down'])} · McNemar p={v['p']:.3f}{' (의미 있음)' if v['significant'] else ''}")
            if v["down"]:
                say(f"       나빠진 문항: {v['down']}")
            for t, x in sorted(v["by_type"].items()):
                say(f"       {t:16s} {x['before']}/{x['n']} → {x['after']}/{x['n']}")
        elif not rs["n"]:   # 셀 문항 없음(전부 negative·다른 mode) — 답 쪽과 같이 기준선으로 남기지 않는다(재리뷰 L1)
            rec["ok"] = False
            say("[검색] 측정된 문항 없음 — 파일·--mode 확인")
        else:
            say(f"[검색] Recall {rs['hit']}/{rs['n']} · Top-1 {rs['top1']}/{rs['n']}")

    if a.answers_after:
        raw_a = _rows(a.answers_after)
        ans_a = _answer_rows(raw_a)
        after_qids |= {r["qid"] for r in raw_a if r.get("qid")}   # 판정불가도 세트의 문항 — 정책이 꺼졌다고 세트가 바뀐 게 아니다(리뷰 M2)
        meta_a = _judge_meta(raw_a)
        rec["judge_meta"] = meta_a
        s = answer_summary(ans_a)
        if hits is None:   # 검색 파일이 없으면 답 측정이 기록한 "정답 정책이 문맥에 있었나"로
            hits = {r["qid"]: bool(r.get("gold_in_context")) for r in raw_a}
        s["retrieval_ok_answer_wrong"] = retrieval_ok_answer_wrong(hits, ans_a)
        s["failures"] = _failures(raw_a)
        rec["summary_answers"] = s
        if a.answers_before:
            raw_b = _rows(a.answers_before)
            ans_b = _answer_rows(raw_b)
            before_qids |= {r["qid"] for r in raw_b if r.get("qid")}
            # 같은 날·같은 기준인가 — 옛 스크립트와 같은 규칙(채점 기준·날짜 쌍의 집합) · 같은 문구(리뷰 L1)
            pb, pa = _judge_pairs(raw_b), _judge_pairs(raw_a)
            same = pb == pa
            rec["judge_meta"] = {**meta_a, "before": _judge_meta(raw_b), "same_day_rubric": same}
            if not same:
                say(f"⚠ 채점 기준·날짜가 다름 {sorted(pb)} vs {sorted(pa)} — 기준 답을 같은 날 다시 채점해서 비교할 것")
            v = answer_gate(ans_b, ans_a)
            up, down = _moved(ans_b, ans_a)
            fb, fa = _failures(raw_b), s["failures"]
            new_fail = sorted((set(fa) - set(fb)) & before_qids)   # 전 측정에 있던 문항만 — 골든셋에 새로 넣은 문항은 "전엔 답하던" 게 아니다(재리뷰 L4, 세트 차이는 따로 경고)
            # 새로 답을 못 낸 문항은 정답률 비교에서 빠진다(짝이 없음) — 그대로 두면 "전엔 맞던 문항이 거부되는 변경"이 통과하고 기준선까지 옮겨진다
            # (리뷰 10/29 M1). 정확도 하락 금지 원칙대로 실패로 — 연결 오류 탓이면 다시 재면 된다(잘못 통과보다 잘못 실패가 안전)
            if new_fail:
                v = {**v, "pass": False, "failed": [*v["failed"], "새로 답 못 냄"]}
            rec["answers"] = {**v, "up_qids": up, "down_qids": down, "failures_before": len(fb), "failures_after": len(fa),
                              "failures_new": new_fail}
            if fa or fb:
                say(f"{'⚠ ' if new_fail else ''}[장애] 답 못 냄 {len(fb)} → {len(fa)}문항(정답률에서 뺌)"
                    + (f" — 새로 못 냄 {new_fail}: 게이트웨이 거부·연결 오류인지 확인하고 다시 잴 것" if new_fail else ""))
            rec["ok"] &= v["pass"]
            say(f"[답]   {'통과' if v['pass'] else '실패 — ' + ', '.join(v['failed'])}")
            for g, x in v["groups"].items():
                ci = f"[{_pct(x['lo'])}, {_pct(x['hi'])}]" if x["lo"] is not None else "[계산불가]"
                tag = " 의미 있게 좋아짐" if x["significant"] and x["diff"] > 0 else (
                    " 판정 불가(오차를 못 잼 + 나빠진 문항 있음)" if x["undecidable"] else " 의미 있게 나빠짐" if x["worse"] else "")
                tag += " ※묶음 1개 — 문항 독립 가정 구간" if x["iid_fallback"] else ""
                say(f"       {g:16s} {x['n']:3d}문항 {x['before'] * 100:5.1f}% → {x['after'] * 100:5.1f}%  "
                    f"차이 {_pct(x['diff'])}%p {ci}  좋아짐 {x['up']} 나빠짐 {x['down']}{tag}")
        elif s["accuracy"] is None:   # 셀 수 있는 답이 없음(빈 파일·전부 판정불가) — 기준선으로 남기지 않는다(리뷰 L2)
            rec["ok"] = False
            say("[답]   채점된 문항 없음 — 파일 확인")
        else:
            ci = f"[{s['lo'] * 100:.1f}, {s['hi'] * 100:.1f}]" if s["lo"] is not None else "[계산불가]"
            say(f"[답]   정답률 {s['accuracy'] * 100:.1f}% {ci} · {s['n']}문항 × {s['reps']}회"
                + (f" · 답 못 냄 {len(s['failures'])}문항(정답률에서 뺌)" if s["failures"] else ""))

    rec["golden_version"] = golden_version(after_qids)
    if before_qids and golden_version(before_qids) != rec["golden_version"]:
        rec["golden_before"] = golden_version(before_qids)
        say(f"⚠ 전/후 문항 세트가 다름 {rec['golden_before']} → {rec['golden_version']} — 짝지은 문항만 비교됨")
    if rec["kind"] == "gate":
        say("게이트: " + ("통과" if rec["ok"] else "실패"))
    return rec


AXIS_NAMES = {"policy": "정책서", "knowledge": "업무지식"}   # main.py #96 CHECK와 같은 값
BASELINE_LOCK = 86_001   # service/eval/service.py와 같은 값 — 기준선을 옮기는 두 길(스크립트·API)이 같은 잠금을 잡는다


def _git_commit(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5,
                             cwd=Path(__file__).resolve().parent)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None   # 컨테이너엔 .git이 없다 — --commit으로 주거나 비워 둔다(저장 때 알림)


async def save(rec: dict, a: argparse.Namespace) -> int:
    """기록 한 줄. 기준선은 한 행만(부분 고유 인덱스) — 옮길 땐 같은 트랜잭션에서 옛 줄을 먼저 내린다."""
    sys.path.insert(0, "/app")
    from core.database import close_pool, get_conn, init_pool   # 앱 DB — --save 때만(호스트에서 판정만 볼 땐 필요 없음)

    summary = {"retrieval": rec.get("summary_retrieval"), "answers": rec.get("summary_answers"),
               "inputs": {k: Path(v).name for k, v in (("answers_before", a.answers_before), ("answers_after", a.answers_after),
                                                        ("retrieval_before", a.retrieval_before), ("retrieval_after", a.retrieval_after)) if v},
               "golden_before": rec.get("golden_before"),
               "mode": a.mode}   # 검색 비교에 쓴 용어 방식 — 화면의 층별 문항(/layers)이 같은 줄로 센다(리뷰)
    verdict = None if rec["kind"] == "baseline" else ("pass" if rec["ok"] else "fail")
    make_baseline = rec["kind"] == "baseline" or (verdict == "pass" and not a.keep_baseline)
    run_at = datetime.fromisoformat(a.run_at) if a.run_at else None
    j = lambda v: None if v is None else json.dumps(v, ensure_ascii=False)   # noqa: E731
    await init_pool()
    try:
        async with get_conn() as conn:
            async with conn.transaction():
                # 기준선 옮기기는 한 번에 하나(API 기준선 지정과 같은 잠금) — 동시에 옮기면 둘째가 고유 인덱스에 부딪혀 기록을 잃었다(리뷰 M1)
                await conn.execute("SELECT pg_advisory_xact_lock($1)", BASELINE_LOCK)
                if make_baseline:   # 기준선은 축마다 한 줄(#96) — 다른 축의 기준선은 건드리지 않는다
                    await conn.execute("UPDATE eval_gate_run SET is_baseline = FALSE WHERE is_baseline AND axis = $1", a.axis)
                if a.candidate and not await conn.fetchval("SELECT 1 FROM eval_candidate WHERE id = $1", a.candidate):
                    raise SystemExit(f"후보 #{a.candidate} 없음 — 저장 안 함")
                rid = await conn.fetchval(
                    """INSERT INTO eval_gate_run (run_at, created_by, label, kind, golden_version, verdict, retrieval, answers, summary,
                                                  judge_meta, is_baseline, git_commit, notes, axis)
                       VALUES (COALESCE($1, NOW()), $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10::jsonb, $11, $12, $13, $14)
                       RETURNING id""",
                    run_at, a.by, a.save, rec["kind"], rec["golden_version"], verdict, j(rec["retrieval"]), j(rec["answers"]),
                    j(summary), j(rec.get("judge_meta") or {}), make_baseline, _git_commit(a.commit), a.notes, a.axis)
                if a.candidate:   # 이 후보를 잰 판정 — 화면의 진행 단계·문항 상세가 읽는다(#88)
                    await conn.execute("UPDATE eval_candidate SET measured_gate_run_ids = array_append(measured_gate_run_ids, $2) "
                                       "WHERE id = $1", a.candidate, rid)
    finally:
        await close_pool()
    print(f"기록 #{rid} 저장({AXIS_NAMES[a.axis]}){' · 기준선' if make_baseline else ''}")
    return rid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers-before")
    ap.add_argument("--answers-after")
    ap.add_argument("--retrieval-before")
    ap.add_argument("--retrieval-after")
    ap.add_argument("--mode", default="lexical", help="검색 상세에 여러 용어 방식(mode)이 섞여 있으면 이것만 비교(기본: 운영값 lexical)")
    ap.add_argument("--baseline", action="store_true", help="판정 없이 단독 측정(-after 파일만)을 기준선으로 기록")
    ap.add_argument("--save", metavar="LABEL", help="판정 기록에 저장 — 무엇을 바꿨나 한 줄(컨테이너 안에서)")
    ap.add_argument("--keep-baseline", action="store_true", help="통과해도 기준선을 옮기지 않음(채택 안 한 실험)")
    ap.add_argument("--commit", help="코드 버전(기본: git rev-parse — 컨테이너엔 .git이 없어 비게 됨)")
    ap.add_argument("--notes")
    ap.add_argument("--by", default="script", help="기록한 사람")
    ap.add_argument("--run-at", help="측정 시각 ISO(옛 결과 파일을 다시 기록할 때만)")
    ap.add_argument("--axis", choices=sorted(AXIS_NAMES), default="policy",
                    help="어느 축의 측정인가 — 정책서(policy, 기본)·지식(knowledge, 컨플 합성 문항 등). 기준선은 축마다 한 줄(#96)")
    ap.add_argument("--candidate", type=int, help="이 판정이 잰 개선 후보 id(평가 게이트 [개선]) — 진행 단계에 '측정'으로 붙는다")
    a = ap.parse_args()
    pairs = [(a.answers_before, a.answers_after), (a.retrieval_before, a.retrieval_after)]
    if a.baseline:
        if a.answers_before or a.retrieval_before or not (a.answers_after or a.retrieval_after):
            print("입력 오류: --baseline은 -after 파일만 주세요(전/후 판정이 아님)")
            return 2
    elif any(bool(x) != bool(y) for x, y in pairs) or not any(x and y for x, y in pairs):
        print("입력 오류: --answers-before/after 또는 --retrieval-before/after를 짝으로 주세요")
        return 2
    if a.candidate and a.axis != "policy":
        print("입력 오류: 개선 후보(--candidate)는 정책서 축만 — 자가 개선 루프는 아직 정책서 문항으로만 돈다")
        return 2
    if a.candidate and not a.save:
        print("입력 오류: --candidate는 --save와 같이(기록에 연결)")
        return 2
    if a.baseline and a.keep_baseline:
        print("입력 오류: --baseline(기준선으로 기록)과 --keep-baseline(기준선 그대로)은 함께 쓸 수 없습니다")
        return 2
    if a.run_at:
        try:
            ok_tz = datetime.fromisoformat(a.run_at).tzinfo is not None
        except ValueError:
            ok_tz = False
        if not ok_tz:   # 시간대 없는 값은 DB가 어떻게 해석할지 모른다(리뷰 L5)
            print("입력 오류: --run-at은 시간대까지 쓴 ISO 시각(예: 2026-10-21T22:44+09:00)")
            return 2
    for path in (a.answers_after, a.answers_before, a.retrieval_after, a.retrieval_before):
        found = _axis_of(_rows(path)) if path else None
        if found and found != a.axis:   # --axis를 빠뜨리면 지식 측정이 정책서 기준선을 덮는다(리뷰 10/29 M2)
            print(f"입력 오류: {Path(path).name}은 {AXIS_NAMES[found]} 측정인데 --axis {a.axis} — --axis {found}로")
            return 2
    rec = evaluate(a)
    print("\n".join(rec["lines"]))
    if a.save and rec["kind"] == "baseline" and not rec["ok"]:
        print("저장 안 함 — 셀 수 있는 결과가 없는 측정은 기준선이 될 수 없습니다")
    elif a.save:
        asyncio.run(save(rec, a))
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
