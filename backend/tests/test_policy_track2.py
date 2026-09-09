"""Tests for service/policy/track2.py — Track 2 A/B 비교 실행 API."""
import importlib.util as _ilu
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_backend_dir = Path(__file__).resolve().parent.parent


def _load(name: str, rel_path: str):
    spec = _ilu.spec_from_file_location(name, str(_backend_dir / rel_path))
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_policy_pkg = MagicMock()
sys.modules["service.policy"] = _policy_pkg
search = _load("service.policy.search", "service/policy/search.py")
_policy_pkg.search = search
track2 = _load("service.policy.track2", "service/policy/track2.py")


class TestNamespaceForFile:
    def test_online_store_file_maps_correctly(self):
        assert track2._namespace_for_file("비즈니스정책서_온라인스토어_재구성.xlsx") == "온라인스토어 DB"

    def test_delivus_file_maps_correctly(self):
        assert track2._namespace_for_file("비즈니스정책서_딜리버스_외부서비스_재구성.xlsx") == "딜리버스 DB"

    def test_unknown_file_returns_none(self):
        assert track2._namespace_for_file("모르는파일.xlsx") is None


class TestRunComparison:
    @pytest.mark.asyncio
    async def test_missing_golden_set_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(track2, "_GOLDEN_SET_PATH", tmp_path / "없음.jsonl")
        with pytest.raises(ValueError, match="골든셋"):
            await track2.run_comparison()

    @pytest.mark.asyncio
    async def test_aggregates_by_type_and_cleans_up_test_namespace(self, monkeypatch, tmp_path):
        golden_path = tmp_path / "golden.jsonl"
        entries = [
            {"qid": "q1", "query": "param 질문1", "type": "param",
             "source": {"file": "비즈니스정책서_온라인스토어_재구성.xlsx", "sheet": "s", "row": 1}, "expected_answer": "20개"},
            {"qid": "q2", "query": "param 질문2", "type": "param",
             "source": {"file": "비즈니스정책서_온라인스토어_재구성.xlsx", "sheet": "s", "row": 2}, "expected_answer": "5개"},
            {"qid": "q3", "query": "narrative 질문", "type": "narrative",
             "source": {"file": "비즈니스정책서_온라인스토어_재구성.xlsx", "sheet": "s", "row": 3}, "expected_answer": "..."},
        ]
        golden_path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries), encoding="utf-8")
        monkeypatch.setattr(track2, "_GOLDEN_SET_PATH", golden_path)

        conn = MagicMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=False)
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=999)  # 신규 A 네임스페이스 id
        # setup_track_a용 policy_item 조회는 빈 목록으로(색인 대상 없음 — 이 테스트는 채점 로직만 검증)
        conn.fetch = AsyncMock(return_value=[])
        # 각 골든셋 항목의 gold item_id 조회(_resolve_item_by_source) — row 1,2,3 각각 다른 item
        conn.fetchrow = AsyncMock(side_effect=[
            {"id": 101, "namespace_id": 1}, {"id": 102, "namespace_id": 1}, {"id": 103, "namespace_id": 1},
        ])

        monkeypatch.setattr(track2, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(track2, "resolve_namespace_id", AsyncMock(return_value=1))
        monkeypatch.setattr(track2.embedding_service, "embed", AsyncMock(return_value=[0.1] * 768))

        # A그룹: 첫 두 param 질문은 못 찾고(빈 결과), narrative 질문만 찾음
        search_knowledge_mock = AsyncMock(side_effect=[[], [], [MagicMock(id=999)]])
        monkeypatch.setattr(track2, "search_knowledge", search_knowledge_mock)

        # B그룹: param 질문 둘 다 정답, narrative는 못 찾음
        def _make_result(param_ids=(), narrative_ids=()):
            r = MagicMock()
            r.params = [MagicMock(item_id=i) for i in param_ids]
            r.narratives = [MagicMock(item_id=i) for i in narrative_ids]
            return r
        search_policy_mock = AsyncMock(side_effect=[
            _make_result(param_ids=[101]), _make_result(param_ids=[102]), _make_result(),
        ])
        monkeypatch.setattr(search, "search_policy", search_policy_mock)

        result = await track2.run_comparison(top_k=5)

        assert result.total_n == 3
        by_type = {t.type: t for t in result.by_type}
        assert by_type["param"].n == 2
        assert by_type["param"].a_hit_rate == 0.0   # A는 id_map이 비어있어 못 찾음
        assert by_type["param"].b_hit_rate == 1.0   # B는 둘 다 정답
        assert by_type["param"].a_precision == 0.0  # A는 후보 자체가 없어 precision도 0
        assert by_type["param"].b_precision == 1.0  # B는 후보 1개가 전부 정답이라 precision 만점
        assert by_type["narrative"].n == 1
        assert by_type["narrative"].b_hit_rate == 0.0
        assert by_type["narrative"].a_precision == 0.0  # A 후보(999)가 id_map에 없어 무관 처리
        assert by_type["narrative"].b_precision == 0.0  # B는 후보 자체가 없음(빈 결과)
        assert result.a_precision == 0.0
        assert result.b_precision == pytest.approx(2 / 3)  # (1.0 + 1.0 + 0.0) / 3
        # param 두 건 다 params(RDB)에서만 정답을 찾음(narrative_ids는 항상 빈 목록)
        assert by_type["param"].b_hit_rdb_only == 1.0
        assert by_type["param"].b_hit_vector_only == 0.0
        assert by_type["param"].b_hit_both == 0.0
        # narrative는 B가 아예 못 찾음(miss) — 세 갈래 다 0
        assert by_type["narrative"].b_hit_rdb_only == 0.0
        assert by_type["narrative"].b_hit_vector_only == 0.0
        assert by_type["narrative"].b_hit_both == 0.0

    @pytest.mark.asyncio
    async def test_source_attribution_rdb_vs_vector(self, monkeypatch, tmp_path):
        """B그룹 hit을 RDB(param)/벡터(narrative)/둘 다로 쪼개는 로직 검증 — q1은 RDB만,
        q2는 벡터만, q3는 둘 다에서 정답을 찾는 상황을 구성해 세 갈래가 각각 1건씩 잡히는지 확인."""
        golden_path = tmp_path / "golden.jsonl"
        entries = [
            {"qid": "q1", "query": "rdb만", "type": "param",
             "source": {"file": "비즈니스정책서_온라인스토어_재구성.xlsx", "sheet": "s", "row": 1}, "expected_answer": "x"},
            {"qid": "q2", "query": "벡터만", "type": "param",
             "source": {"file": "비즈니스정책서_온라인스토어_재구성.xlsx", "sheet": "s", "row": 2}, "expected_answer": "x"},
            {"qid": "q3", "query": "둘다", "type": "param",
             "source": {"file": "비즈니스정책서_온라인스토어_재구성.xlsx", "sheet": "s", "row": 3}, "expected_answer": "x"},
        ]
        golden_path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries), encoding="utf-8")
        monkeypatch.setattr(track2, "_GOLDEN_SET_PATH", golden_path)

        conn = MagicMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=False)
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=999)
        conn.fetch = AsyncMock(return_value=[])
        conn.fetchrow = AsyncMock(side_effect=[
            {"id": 201, "namespace_id": 1}, {"id": 202, "namespace_id": 1}, {"id": 203, "namespace_id": 1},
        ])
        monkeypatch.setattr(track2, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(track2, "resolve_namespace_id", AsyncMock(return_value=1))
        monkeypatch.setattr(track2.embedding_service, "embed", AsyncMock(return_value=[0.1] * 768))
        monkeypatch.setattr(track2, "search_knowledge", AsyncMock(return_value=[]))  # A그룹은 이 테스트 관심사 아님

        def _make_result(param_ids=(), narrative_ids=()):
            r = MagicMock()
            r.params = [MagicMock(item_id=i) for i in param_ids]
            r.narratives = [MagicMock(item_id=i) for i in narrative_ids]
            return r
        search_policy_mock = AsyncMock(side_effect=[
            _make_result(param_ids=[201]),                    # q1: RDB만
            _make_result(narrative_ids=[202]),                 # q2: 벡터만
            _make_result(param_ids=[203], narrative_ids=[203]), # q3: 둘 다
        ])
        monkeypatch.setattr(search, "search_policy", search_policy_mock)

        result = await track2.run_comparison(top_k=5)

        by_type = {t.type: t for t in result.by_type}
        assert by_type["param"].n == 3
        assert by_type["param"].b_hit_rdb_only == pytest.approx(1 / 3)
        assert by_type["param"].b_hit_vector_only == pytest.approx(1 / 3)
        assert by_type["param"].b_hit_both == pytest.approx(1 / 3)
        # 세 갈래 합은 항상 b_hit_rate와 같아야 한다
        assert (by_type["param"].b_hit_rdb_only + by_type["param"].b_hit_vector_only
                + by_type["param"].b_hit_both) == pytest.approx(by_type["param"].b_hit_rate)

        # 테스트 네임스페이스 정리(삭제) 호출 확인
        delete_calls = [c for c in conn.execute.call_args_list if "DELETE" in c.args[0]]
        assert any("rag_knowledge" in c.args[0] for c in delete_calls)
        assert any("ops_namespace" in c.args[0] for c in delete_calls)

    @pytest.mark.asyncio
    async def test_unresolvable_namespace_file_skipped(self, monkeypatch, tmp_path):
        """golden set의 source.file이 알려진 팀 파일명을 안 담고 있으면 그 문항은 건너뛴다."""
        golden_path = tmp_path / "golden.jsonl"
        golden_path.write_text(json.dumps({
            "qid": "q1", "query": "질문", "type": "param",
            "source": {"file": "알수없는파일.xlsx", "sheet": "s", "row": 1}, "expected_answer": "x",
        }, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(track2, "_GOLDEN_SET_PATH", golden_path)

        conn = MagicMock()
        conn.__aenter__ = AsyncMock(return_value=conn)
        conn.__aexit__ = AsyncMock(return_value=False)
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=999)
        conn.fetch = AsyncMock(return_value=[])
        monkeypatch.setattr(track2, "get_conn", MagicMock(return_value=conn))
        monkeypatch.setattr(track2, "resolve_namespace_id", AsyncMock(return_value=1))
        monkeypatch.setattr(track2.embedding_service, "embed", AsyncMock(return_value=[0.1] * 768))

        result = await track2.run_comparison()

        assert result.total_n == 0
        assert result.by_type == []
