"""정책 항목 위험도 — 승인 대기 큐를 "위험한 것만 사람이 본다"로 나누는 결정론적 규칙(2026-10-01, LLM 미사용).

배경: pending_review 368건 중 승인 11건(9/23 하루) 뒤 0건 — 전부 사람이 보는 구조라 큐가 줄지 않았다.
등급은 **저장하지 않고 조회할 때마다 계산**한다. parse_status·청크를 바꾸는 경로가 셋(임포트, 반려 후 수정
`edit.py`, 미해결 승격 `unresolved_report.py`)이라 저장해 두면 어긋난다. 결정 시점의 등급·근거는
`policy_review_log`에 스냅샷으로 남아, 나중에 자동 승인 기준을 조정하는 데이터가 된다.

규칙을 바꾸면 rule_key 버전을 올린다(로그에서 어느 규칙으로 통과됐는지 구분하기 위해).
"""
from __future__ import annotations

from dataclasses import dataclass, field

LOW_RISK_RULE = "low_risk_v1"
LEVEL_ORDER = {"high": 0, "medium": 1, "low": 2}

# 등급 판정에 필요한 값 — policy_item 별칭 p 기준으로 SELECT 목록에 붙여 쓴다(목록·자동 통과·요약 공용)
RISK_FEATURES_SQL = """
    (SELECT COUNT(*) FROM policy_chunk c WHERE c.policy_item_id = p.id) AS chunk_count,
    jsonb_array_length(COALESCE(p.unresolved_segments, '[]'::jsonb)) AS unresolved_count,
    EXISTS (SELECT 1 FROM policy_review_log l
            WHERE l.logical_id = p.logical_id AND l.action IN ('rejected', 'resubmitted')) AS prior_reject,
    EXISTS (SELECT 1 FROM policy_review_log l
            WHERE l.logical_id = p.logical_id AND l.action = 'auto_reverted') AS prior_revert,
    (p.source_missing_at IS NOT NULL) AS source_missing
"""
# prior_reject에 resubmitted도 센다 — 이력 테이블 이전(9/23~)에 반려됐다가 수정·재제출된 항목은 'rejected' 행이 없다.
# prior_revert — 사람이 자동 통과를 되돌린 항목은 다음 실행·임포트 때 다시 자동 통과되면 안 된다(/code-review).


@dataclass(frozen=True)
class Risk:
    level: str                      # high | medium | low
    rule_key: str | None            # 자동 통과 후보면 그 규칙(low만)
    reasons: list[str] = field(default_factory=list)
    short: str = ""                 # 목록 한 줄에 배지 옆에 붙는 핵심 이유(마우스·펼침 없이 보이게)
    next_step: str = ""             # 그래서 담당자가 할 일


def classify(parse_status: str | None, unresolved_count: int, chunk_count: int, prior_reject: bool,
             prior_revert: bool = False, source_missing: bool = False) -> Risk:
    """사람이 봐야 하는 이유를 앞에 쌓는다 — 높음 사유가 하나라도 있으면 높음."""
    high: list[tuple[str, str, str]] = []  # (상세 이유, 짧은 이유, 할 일) — 앞에 있을수록 먼저 볼 이유
    # 재임포트한 엑셀에서 사라진 정책(2026-10-06, 정책 버전 관리) — 자동 폐기하지 않고 사람이 결정
    if source_missing:
        high.append(("최근 임포트한 원본 엑셀에서 사라진 정책 — 폐기할지 확인", "원본에서 사라짐",
                     "원본에서 일부러 뺀 정책이면 반려(검색에서 제외), 실수로 빠진 거면 승인(유지)하세요."))
    if parse_status in ("unresolved", "partial"):
        label = "전혀" if parse_status == "unresolved" else "일부"
        high.append((f"원문을 {label} 구조화하지 못함(미해결 조각 {unresolved_count}개)",
                     f"미분류 조각 {unresolved_count}개",
                     "펼쳐서 미분류 조각을 서술/파라미터로 편입한 뒤 내용을 확인해 승인·반려하세요."))
    if prior_reject:
        high.append(("이전에 사람이 반려한 항목 — 수정·재등록돼도 사람이 다시 확인", "이전에 반려됨",
                     "반려됐던 내용이 제대로 고쳐졌는지 원문과 비교한 뒤 승인·반려하세요."))
    if prior_revert:
        high.append(("자동 통과를 사람이 되돌린 항목 — 다시 자동 통과하지 않음", "자동 통과 되돌림",
                     "누군가 자동 통과를 되돌린 항목입니다 — 내용을 확인해 직접 승인·반려하세요."))
    split = f"서술이 {chunk_count}개로 나뉨 — 조건이 쪼개졌을 위험" if chunk_count >= 2 else None
    if high:
        return Risk("high", None, [h[0] for h in high] + ([split] if split else []),
                    " · ".join(h[1] for h in high), high[0][2])
    if split:
        return Risk("medium", None, [split], f"서술 {chunk_count}개로 나뉨",
                    "서술들을 원문과 비교해 조건이 엉뚱하게 쪼개지지 않았는지 확인하고 승인·반려하세요.")
    return Risk("low", LOW_RISK_RULE, ["구조화 완료 + 서술 1개 — 자동 통과 후보"], "구조화 완료",
                "자동 통과 대상이라 따로 볼 필요 없어요(표본으로 남은 건만 확인).")


def classify_row(row) -> Risk:
    return classify(row["parse_status"], row["unresolved_count"] or 0, row["chunk_count"] or 0, bool(row["prior_reject"]),
                    bool(row.get("prior_revert")), bool(row.get("source_missing")))
