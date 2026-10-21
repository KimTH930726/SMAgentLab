"""정책 항목이 채팅 검색에 보이는지 — 한 곳에서 정하는 조건(2026-10-21, v0 재구축 1단계).

막힌 일: 검색 조건이 여러 파일에 `status NOT IN ('deprecated', 'rejected')`로 흩어져 있어, 검토 대기(pending_review)도 검색에 나왔다.
엑셀 분해 시절(v1)엔 승인 화면이 없어 일부러 그렇게 했지만(search.py 머리말), v0(원문 통째)는 사람이 고친 새 버전이
승인 전에 옛 버전과 나란히 검색·답변에 섞인다.
규칙: v0는 승인된 것(active)만, 옛 엑셀 경로(v0 아님)는 예전처럼 검토 대기도 포함.

다른 import가 없는 잎 모듈이다 — search.py ↔ keyword_index.py 순환 import를 피하고, 테스트가 service.policy를 가짜 패키지로
둬도 이 파일만 등록하면 어디서든 실제 문자열을 쓴다(conftest).
"""
from __future__ import annotations

# v0_convert.PIPELINE_WHOLE과 같은 값
PIPELINE_WHOLE = "v0_whole"


def searchable_sql(alias: str = "i") -> str:
    """WHERE 절에 넣는 조건 — alias는 policy_item의 별칭(별칭이 없는 쿼리는 테이블 이름 "policy_item")."""
    a = alias
    return (f"({a}.status = 'active' OR ({a}.status = 'pending_review' "
            f"AND {a}.pipeline_version IS DISTINCT FROM '{PIPELINE_WHOLE}'))")


def is_searchable(status: str, pipeline_version: str | None) -> bool:
    """searchable_sql과 같은 규칙의 파이썬 판정(이미 읽은 행에 쓴다)."""
    return status == "active" or (status == "pending_review" and pipeline_version != PIPELINE_WHOLE)


# ── 정책 항목을 겨눈 대기 중 정정(10/21 리뷰 L10) ──
# 정정 원장(improvement: 정책당 대기 정정 1건 막기)과 정책 화면(catalog: "정정 #N 검토 중")이 같은 기준을 써야 해서 한 곳에 둔다.
# 두 곳에 같은 SQL·대상 목록이 복사돼 있어 한쪽만 바뀌면 화면엔 "검토 중"이 없는데 제안은 막히는 식으로 어긋날 수 있었다.
POLICY_TARGETS = ("policy_param", "policy_narrative")

# 정정 원장(ops_improvement_item)의 정책 쪽 값 — 정정 원장(improvement)·원문 확인(source_check)·JSON 가져오기(json_io)·관리자 통계가
# 같은 문자열을 각자 적고 있었다(10/21 리뷰 C6). 잎 모듈이라 어디서 import해도 순환이 없다.
KIND_SOURCE_CHECK = "source_check"   # 원문 확인 항목 — 정정이 아니라 정정 목록·배지·처리 API에서 뺀다
SOURCE_JSON_IMPORT = "json_import"   # 정책서 JSON 가져오기로 만든 수정 제안의 source


def open_correction_sql(item_expr: str, targets_param: str, *, cols: str = "id", extra: str = "") -> str:
    """이 정책 항목을 겨눈 대기 중 정정(출처 무관 — 채팅 신고 포함) 중 가장 오래된 1건의 SELECT.
    item_expr = 정책 항목 id 식("$1" 또는 상관 서브쿼리의 "i.id"), targets_param = POLICY_TARGETS를 넘긴 자리("$2"),
    extra = 덧붙일 조건(예: 자기 자신 빼기). 열 이름은 별칭 o로 한정 — 상관 서브쿼리에서 바깥 policy_item의 id·status와 헷갈리지 않게."""
    return (f"SELECT {cols} FROM ops_improvement_item o WHERE o.status = 'pending' "
            f"AND o.target_type = ANY({targets_param}::text[]) AND o.target_id = {item_expr}{extra} ORDER BY o.id LIMIT 1")
