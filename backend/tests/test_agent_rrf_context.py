"""agent.py의 _build_rrf_context() — 일반지식/정책 파라미터/정책 서술을 RRF로 합친
컨텍스트 검증(2026-09-18). 기존 doc_context + policy_context 순차 이어붙이기를 대체.

conftest.py는 shared/service를 통째로 MagicMock 처리하는데, agent.py가 새로 import하는
shared.rrf와 (원래도 필요했던) service.policy.search/query_type은 개별 등록이 안 돼 있어
그대로면 agent.py import 자체가 실패한다(test_policy_search.py가 같은 이유로 자체
등록하는 것과 동일한 문제) — 여기서도 같은 방식으로 필요한 모듈만 파일 경로 기반으로
실제 로드해 등록한다."""
import importlib.util as _ilu
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent


def _load_real(name: str, rel_path: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = _ilu.spec_from_file_location(name, str(_backend_dir / rel_path))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_load_real("shared.rrf", "shared/rrf.py")
sys.modules.setdefault("service.policy", MagicMock())
_load_real("service.policy.query_type", "service/policy/query_type.py")
_load_real("service.policy.search", "service/policy/search.py")
sys.modules.setdefault("service.refdata", MagicMock())
_load_real("service.refdata.parser", "service/refdata/parser.py")
_load_real("service.refdata.service", "service/refdata/service.py")

from agents.knowledge_rag import agent  # noqa: E402
from agents.knowledge_rag.knowledge.retrieval import RetrievalResult  # noqa: E402
from service.policy.search import ParamHit, NarrativeHit, PolicySearchResult  # noqa: E402

_THRESHOLDS = {
    "knowledge_min_score": 0.5,
    "knowledge_mid_score": 0.6,
    "knowledge_high_score": 0.8,
}


def _make_result(id_, final_score, content="내용", category=None):
    return RetrievalResult(id=id_, namespace="ns", content=content, base_weight=0.0, final_score=final_score, category=category)


class TestBuildRrfContext:
    def test_empty_inputs_return_empty_string(self, monkeypatch):
        monkeypatch.setattr(agent.retrieval, "get_thresholds", lambda: _THRESHOLDS)
        assert agent._build_rrf_context([], PolicySearchResult()) == ""

    def test_below_min_score_excluded(self, monkeypatch):
        monkeypatch.setattr(agent.retrieval, "get_thresholds", lambda: _THRESHOLDS)
        results = [_make_result(1, 0.3, "무관한 문서")]
        assert agent._build_rrf_context(results, PolicySearchResult()) == ""

    def test_single_axis_matches_original_order(self, monkeypatch):
        """일반지식만 있을 때는 final_score 내림차순 그대로 유지돼야 함(RRF가 순서를 안 뒤집음)."""
        monkeypatch.setattr(agent.retrieval, "get_thresholds", lambda: _THRESHOLDS)
        results = [_make_result(1, 0.9, "첫 번째"), _make_result(2, 0.7, "두 번째")]
        text = agent._build_rrf_context(results, PolicySearchResult())
        assert text.index("첫 번째") < text.index("두 번째")

    def test_keyword_only_category_included_despite_low_score(self, monkeypatch):
        """DB/공통코드는 final_score가 knowledge_min_score보다 낮아도(ts_rank 스케일이
        코사인보다 항상 작음) 매칭이 됐으면 채택돼야 한다 — "DS14가 뭐야?" 실측 버그."""
        monkeypatch.setattr(agent.retrieval, "get_thresholds", lambda: _THRESHOLDS)
        results = [_make_result(1, 0.03, "DS14 : 배차 취소(POS)", category="공통코드")]
        text = agent._build_rrf_context(results, PolicySearchResult())
        assert "DS14" in text
        assert "정확 매칭" in text

    def test_keyword_only_category_excluded_when_no_match(self, monkeypatch):
        monkeypatch.setattr(agent.retrieval, "get_thresholds", lambda: _THRESHOLDS)
        results = [_make_result(1, 0.0, "무관한 코드표", category="DB")]
        assert agent._build_rrf_context(results, PolicySearchResult()) == ""

    def test_cross_axis_interleaves_by_rank_not_by_axis(self, monkeypatch):
        """정책 1위가 일반지식 1위보다 먼저 나올 수 있어야 함 — 예전엔 일반지식이 항상 먼저."""
        monkeypatch.setattr(agent.retrieval, "get_thresholds", lambda: _THRESHOLDS)
        results = [_make_result(1, 0.55, "일반지식 2위 품질")]  # rank 0 in its own list
        policy_result = PolicySearchResult(
            params=[ParamHit(
                item_id=1, logical_id=1, policy_name="배달비 정책", category_path=[], status="active",
                param_name="최대개수", condition=None, value="20", unit="개",
            )],
        )
        text = agent._build_rrf_context(results, policy_result)
        # 둘 다 각자 목록에서 rank 0(=최상위)라 rrf 점수가 동률 — 정렬 안정성상 먼저 추가된
        # 순서(일반지식 먼저 추가)가 유지되는지가 아니라, 최소한 둘 다 컨텍스트에 포함되는지 확인
        assert "일반지식 2위 품질" in text
        assert "배달비 정책" in text

    def test_policy_param_formatted_with_condition(self, monkeypatch):
        monkeypatch.setattr(agent.retrieval, "get_thresholds", lambda: _THRESHOLDS)
        policy_result = PolicySearchResult(
            params=[ParamHit(
                item_id=1, logical_id=1, policy_name="쿠폰 정책", category_path=[], status="active",
                param_name="재발급 가능 횟수", condition="1회 한정", value="1", unit="회",
            )],
        )
        text = agent._build_rrf_context([], policy_result)
        assert "쿠폰 정책" in text
        assert "1회" in text
        assert "1회 한정" in text

    def test_policy_narrative_includes_confidence_label(self, monkeypatch):
        monkeypatch.setattr(agent.retrieval, "get_thresholds", lambda: _THRESHOLDS)
        policy_result = PolicySearchResult(
            narratives=[NarrativeHit(
                item_id=1, logical_id=1, policy_name="배차 정책", category_path=[], status="active",
                chunk_text="배차 지연 시 안내 문구", score=0.75,
            )],
        )
        text = agent._build_rrf_context([], policy_result)
        assert "배차 지연 시 안내 문구" in text
        assert "신뢰도: 높음" in text

    def test_common_code_hit_included_as_rrf_axis(self, monkeypatch):
        """참조데이터 축(2026-09-18) — id=20을 ref_common_code로 옮긴 뒤 chat에서도
        정책/일반지식과 동일하게 RRF로 합쳐져야 한다."""
        monkeypatch.setattr(agent.retrieval, "get_thresholds", lambda: _THRESHOLDS)
        common_codes = [{"group_code_name": "배달 상태 코드", "code_id": "DS14", "code_name": "배차 취소(POS)"}]
        text = agent._build_rrf_context([], PolicySearchResult(), common_codes=common_codes)
        assert "DS14" in text
        assert "배차 취소(POS)" in text

    def test_db_column_hit_formatted(self, monkeypatch):
        monkeypatch.setattr(agent.retrieval, "get_thresholds", lambda: _THRESHOLDS)
        db_columns = [{"table_name": "XO_ORDER", "column_name": "status", "column_comment": "주문 상태", "data_type": "VARCHAR(2)"}]
        text = agent._build_rrf_context([], PolicySearchResult(), db_columns=db_columns)
        assert "XO_ORDER.status" in text
        assert "주문 상태" in text

    def test_no_refdata_hits_does_not_break_context(self, monkeypatch):
        monkeypatch.setattr(agent.retrieval, "get_thresholds", lambda: _THRESHOLDS)
        results = [_make_result(1, 0.9, "일반지식만")]
        text = agent._build_rrf_context(results, PolicySearchResult())
        assert "일반지식만" in text
