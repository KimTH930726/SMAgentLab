"""Tests for service/policy/ — 엑셀 파서, LLM 분해, 버전 관리 임포트 파이프라인.

conftest.py가 sys.modules["service"]를 통째로 MagicMock으로 치환해두므로(다른 service.*
서브모듈 테스트와 동일한 문제), 파일 경로 기반으로 service.policy 서브모듈들을 의존성
순서대로(excel_parser/decompose → service) 직접 로드해 sys.modules에 등록한다.
"""
import importlib.util as _ilu
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
excel_parser = _load("service.policy.excel_parser", "service/policy/excel_parser.py")
decompose = _load("service.policy.decompose", "service/policy/decompose.py")
# service.py가 `from service.policy import excel_parser, decompose`로 임포트한다 — 부모
# 패키지가 MagicMock이라 sys.modules 등록만으로는 속성 접근이 실제 모듈로 안 이어진다
# (MagicMock 속성 접근은 자동으로 새 Mock을 만들어버림). 명시적으로 이어준다.
_policy_pkg.excel_parser = excel_parser
_policy_pkg.decompose = decompose
# agents.knowledge_rag.knowledge.service는 다른 테스트 파일(test_ingestion.py 등)이 실제
# 모듈로 의존하는 전역 sys.modules 캐시라 여기서 mock으로 치환하면 그 테스트들이 스위트
# 전체 실행 시에만 오염돼 깨진다(단독 실행은 통과 — 실제로 겪은 버그). 치환하지 않고
# 진짜 모듈을 그대로 import시킨 뒤, 개별 테스트에서 service.create_glossary만 monkeypatch한다.
service = _load("service.policy.service", "service/policy/service.py")


# ── excel_parser ──────────────────────────────────────────────────────────────

class TestSheetClassification:
    def test_glossary_sheet_detected_by_headers(self):
        headers = ["No.", "용어명", "", "용어 정의", "비고"]
        assert excel_parser._classify_sheet(headers) == "glossary"

    def test_policy_sheet_detected_by_headers(self):
        headers = ["No.", "대분류", "중분류", "소분류", "정책명", "조건/상세", "비고"]
        assert excel_parser._classify_sheet(headers) == "policy"

    def test_unrecognized_headers_return_unknown(self):
        headers = ["A", "B", "C"]
        assert excel_parser._classify_sheet(headers) == "unknown"


class TestDynamicDepthCategoryPath:
    """실측(§1): 팀마다 대분류/중분류/소분류 컬럼 수가 다르다 — 헤더 라벨이 아니라
    'No.와 정책명 사이 컬럼 개수'로 동적으로 category_path 깊이를 정해야 한다."""

    def test_three_level_category_extracted_in_order(self):
        headers = ["No.", "대분류", "중분류", "소분류", "정책명", "조건/상세", "비고"]
        data_rows = [(1, "1.주문", "1-1.장바구니", "", "장바구니 담기", "본문", "비고1")]
        rows = excel_parser._parse_policy_sheet(headers, data_rows, header_row_idx=0)
        assert len(rows) == 1
        assert rows[0].category_path == ["1.주문", "1-1.장바구니", ""]
        assert rows[0].policy_name == "장바구니 담기"
        assert rows[0].raw_body == "본문"
        assert rows[0].remark == "비고1"

    def test_two_level_category_no_remark_column(self):
        headers = ["No.", "정책항목", "세부항목", "정책명", "조건/상세"]
        data_rows = [(1, "카드원장", "기본", "상태 정책", "본문")]
        rows = excel_parser._parse_policy_sheet(headers, data_rows, header_row_idx=0)
        assert len(rows) == 1
        assert rows[0].category_path == ["카드원장", "기본"]
        assert rows[0].remark is None  # 비고 컬럼 자체가 없는 팀(실측)

    def test_empty_policy_name_row_skipped(self):
        headers = ["No.", "대분류", "정책명", "조건/상세"]
        data_rows = [(1, "카테고리", "", "본문"), (2, "카테고리", "실제정책", "본문2")]
        rows = excel_parser._parse_policy_sheet(headers, data_rows, header_row_idx=0)
        assert len(rows) == 1
        assert rows[0].policy_name == "실제정책"

    def test_merged_category_cells_forward_filled(self):
        """실측 확인(2026-09-03): openpyxl은 세로 병합 영역의 첫 셀에만 값을 주고 나머지는
        None을 반환한다 — 대분류/중분류가 여러 row에 걸쳐 병합된 실제 정책서 패턴에서
        빈 category_path가 나오던 버그. 마지막 non-empty 값으로 채워야 한다."""
        headers = ["No.", "대분류", "중분류", "정책명", "조건/상세"]
        data_rows = [
            (1, "1.주문/결제", "1-1.장바구니", "담기", "본문1"),
            (2, None, None, "조회", "본문2"),   # 병합셀 — openpyxl이 None 반환
            (3, None, None, "수량조회", "본문3"),
        ]
        rows = excel_parser._parse_policy_sheet(headers, data_rows, header_row_idx=0)
        assert len(rows) == 3
        assert rows[1].category_path == ["1.주문/결제", "1-1.장바구니"]
        assert rows[2].category_path == ["1.주문/결제", "1-1.장바구니"]

    def test_merge_only_affects_category_columns_not_policy_name(self):
        """정책명 자체가 병합으로 비어있으면(포워드필 대상 아님) 빈 행으로 스킵돼야 한다 —
        카테고리 forward-fill이 정책명까지 오염시키면 안 됨."""
        headers = ["No.", "대분류", "정책명", "조건/상세"]
        data_rows = [(1, "카테고리", "정책1", "본문"), (2, None, "", "본문2")]
        rows = excel_parser._parse_policy_sheet(headers, data_rows, header_row_idx=0)
        assert len(rows) == 1  # 2번째 행은 정책명이 비어 스킵


class TestGlossaryParsing:
    def test_parses_term_and_description(self):
        headers = ["No.", "용어명", "용어 정의", "비고"]
        data_rows = [(1, "딜리버스", "자사 앱 배달 서비스", None), (2, "", "", None)]
        rows = excel_parser._parse_glossary_sheet(headers, data_rows, header_row_idx=0)
        assert len(rows) == 1
        assert rows[0].term == "딜리버스"
        assert rows[0].description == "자사 앱 배달 서비스"


class TestWorkbookParsing:
    def test_glossary_and_policy_sheets_classified_independently(self):
        import io
        import openpyxl
        wb = openpyxl.Workbook()
        ws1 = wb.active
        ws1.title = "용어집"
        ws1.append(["No.", "용어명", "용어 정의"])
        ws1.append([1, "딜리버스", "배달 서비스"])
        ws2 = wb.create_sheet("정책")
        ws2.append(["No.", "대분류", "정책명", "조건/상세"])
        ws2.append([1, "카테고리", "정책1", "본문"])
        buf = io.BytesIO()
        wb.save(buf)

        sheets = excel_parser.parse_workbook(buf.getvalue())
        kinds = {s.sheet_name: s.kind for s in sheets}
        assert kinds == {"용어집": "glossary", "정책": "policy"}


# ── decompose ─────────────────────────────────────────────────────────────────

class TestDecomposePolicyBody:
    @pytest.mark.asyncio
    async def test_parses_llm_json_into_segments(self, monkeypatch):
        provider = MagicMock()
        provider.generate_once = AsyncMock(return_value=(
            '{"segments": ['
            '{"type": "param", "text": "일반 배달: 20개", "extracted": {"name": "최대개수", "condition": "일반 배달", "value": "20", "unit": "개"}},'
            '{"type": "narrative", "text": "재고 없으면 SOLD OUT"}'
            ']}'
        ))
        monkeypatch.setattr(decompose, "get_llm_provider", MagicMock(return_value=provider))

        segments = await decompose.decompose_policy_body("테스트정책", "원문")
        assert len(segments) == 2
        assert segments[0].type == "param"
        assert segments[0].extracted["value"] == "20"
        assert segments[1].type == "narrative"

    @pytest.mark.asyncio
    async def test_strips_markdown_code_fence(self, monkeypatch):
        provider = MagicMock()
        provider.generate_once = AsyncMock(return_value='```json\n{"segments": []}\n```')
        monkeypatch.setattr(decompose, "get_llm_provider", MagicMock(return_value=provider))
        segments = await decompose.decompose_policy_body("정책", "원문")
        assert segments == []

    @pytest.mark.asyncio
    async def test_malformed_llm_response_falls_back_to_unresolved(self, monkeypatch):
        """분해 실패 시 파이프라인이 죽지 않고 원문 전체를 unresolved로 보존해야 한다
        (§2-3 — 실패해도 사유와 함께 사람이 검토할 수 있게)."""
        provider = MagicMock()
        provider.generate_once = AsyncMock(return_value="이건 JSON이 아님")
        monkeypatch.setattr(decompose, "get_llm_provider", MagicMock(return_value=provider))

        segments = await decompose.decompose_policy_body("정책", "원문 내용")
        assert len(segments) == 1
        assert segments[0].type == "unresolved"
        assert segments[0].text == "원문 내용"
        assert "실패" in segments[0].reason

    @pytest.mark.asyncio
    async def test_empty_raw_body_skips_llm_call(self, monkeypatch):
        provider = MagicMock()
        provider.generate_once = AsyncMock()
        monkeypatch.setattr(decompose, "get_llm_provider", MagicMock(return_value=provider))
        segments = await decompose.decompose_policy_body("정책", "   ")
        assert segments == []
        provider.generate_once.assert_not_awaited()


# ── service (버전 관리) ──────────────────────────────────────────────────────

def _make_fake_conn():
    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=1)
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock()
    return conn


@pytest.fixture
def patch_db(monkeypatch):
    conn = _make_fake_conn()
    monkeypatch.setattr(service, "get_conn", MagicMock(return_value=conn))
    monkeypatch.setattr(service, "resolve_namespace_id", AsyncMock(return_value=1))
    return conn


class TestContentHash:
    def test_same_input_produces_same_hash(self):
        h1 = service._content_hash(["a", "b"], "정책명", "본문", "비고")
        h2 = service._content_hash(["a", "b"], "정책명", "본문", "비고")
        assert h1 == h2

    def test_different_body_produces_different_hash(self):
        h1 = service._content_hash(["a"], "정책명", "본문1", None)
        h2 = service._content_hash(["a"], "정책명", "본문2", None)
        assert h1 != h2


class TestIngestPolicyRowVersioning:
    """§2-1 핵심 — 재업로드 시 UPDATE가 아니라 새 row INSERT + 이전 row deprecated 전환."""

    @pytest.mark.asyncio
    async def test_new_row_inserted_as_version_1(self, patch_db, monkeypatch):
        conn = patch_db
        conn.fetchrow = AsyncMock(side_effect=[None, {"id": 10, "logical_id": 10}])
        monkeypatch.setattr(decompose, "decompose_policy_body", AsyncMock(return_value=[]))

        row = excel_parser.ParsedPolicyRow(category_path=["a"], policy_name="정책1", raw_body="본문", remark=None, source_row=2)
        sheet = excel_parser.ParsedSheet(sheet_name="시트1", kind="policy")
        summary = service.SheetSummary(sheet_name="시트1", kind="policy")

        await service._ingest_policy_row(conn, 1, "sys", "file.xlsx", sheet, row, summary)

        insert_call = conn.fetchrow.call_args_list[-1]
        assert "INSERT INTO policy_item" in insert_call.args[0]
        # 위치 인자: ...(9)source_row,(10)content_hash,(11)logical_id,(12)version,(13)supersedes_id,...
        assert insert_call.args[11] is None       # logical_id — 신규라 지정 안 함(트리거가 자기 id로 채움)
        assert insert_call.args[12] == 1           # version — 최초 버전
        assert insert_call.args[13] is None        # supersedes_id — 이전 버전 없음
        assert summary.created_items == 1
        assert summary.new_versions == 0

    @pytest.mark.asyncio
    async def test_unchanged_content_skips_llm_and_insert(self, patch_db, monkeypatch):
        conn = patch_db
        same_hash = service._content_hash(["a"], "정책1", "본문", None)
        conn.fetchrow = AsyncMock(return_value={"id": 5, "logical_id": 5, "version": 1, "content_hash": same_hash})
        decompose_mock = AsyncMock(return_value=[])
        monkeypatch.setattr(decompose, "decompose_policy_body", decompose_mock)

        row = excel_parser.ParsedPolicyRow(category_path=["a"], policy_name="정책1", raw_body="본문", remark=None, source_row=2)
        sheet = excel_parser.ParsedSheet(sheet_name="시트1", kind="policy")
        summary = service.SheetSummary(sheet_name="시트1", kind="policy")

        await service._ingest_policy_row(conn, 1, "sys", "file.xlsx", sheet, row, summary)

        assert summary.unchanged_skipped == 1
        assert summary.created_items == 0
        decompose_mock.assert_not_awaited()  # 내용 안 바뀌었으면 LLM 재호출도 없어야 함
        conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_changed_content_creates_new_version_and_deprecates_old(self, patch_db, monkeypatch):
        conn = patch_db
        old_hash = service._content_hash(["a"], "정책1", "옛 본문", None)
        conn.fetchrow = AsyncMock(side_effect=[
            {"id": 5, "logical_id": 5, "version": 1, "content_hash": old_hash},
            {"id": 11, "logical_id": 5},
        ])
        monkeypatch.setattr(decompose, "decompose_policy_body", AsyncMock(return_value=[]))

        row = excel_parser.ParsedPolicyRow(category_path=["a"], policy_name="정책1", raw_body="새 본문", remark=None, source_row=2)
        sheet = excel_parser.ParsedSheet(sheet_name="시트1", kind="policy")
        summary = service.SheetSummary(sheet_name="시트1", kind="policy")

        await service._ingest_policy_row(conn, 1, "sys", "file.xlsx", sheet, row, summary)

        assert summary.new_versions == 1
        assert summary.created_items == 0
        # 이전 row(id=5)가 deprecated로 전환됐는지 확인 — UPDATE이지 절대 content 덮어쓰기가 아님
        deprecate_call = conn.execute.call_args_list[-1]
        assert "deprecated" in deprecate_call.args[0]
        assert deprecate_call.args[1] == 5

    @pytest.mark.asyncio
    async def test_unresolved_segment_sets_parse_status_and_captures_reason(self, patch_db, monkeypatch):
        conn = patch_db
        conn.fetchrow = AsyncMock(side_effect=[None, {"id": 20, "logical_id": 20}])
        monkeypatch.setattr(decompose, "decompose_policy_body", AsyncMock(return_value=[
            decompose.Segment(type="unresolved", text="상태 전이 규칙", reason="구조화 방법 미정"),
        ]))

        row = excel_parser.ParsedPolicyRow(category_path=["a"], policy_name="정책1", raw_body="본문", remark=None, source_row=2)
        sheet = excel_parser.ParsedSheet(sheet_name="시트1", kind="policy")
        summary = service.SheetSummary(sheet_name="시트1", kind="policy")

        await service._ingest_policy_row(conn, 1, "sys", "file.xlsx", sheet, row, summary)

        assert summary.unresolved_segments == 1
        insert_call = conn.fetchrow.call_args_list[-1]
        assert "'unresolved'" in insert_call.args[0] or "parse_status" in insert_call.args[0]


class TestIngestGlossaryRow:
    @pytest.mark.asyncio
    async def test_new_term_added(self, patch_db, monkeypatch):
        create_mock = AsyncMock(return_value={"id": 1})
        monkeypatch.setattr(service, "create_glossary", create_mock)
        row = excel_parser.ParsedGlossaryRow(term="딜리버스", description="배달 서비스", remark=None, source_row=2)
        summary = service.SheetSummary(sheet_name="용어집", kind="glossary")

        await service._ingest_glossary_row("ns", row, 1, patch_db, summary)

        assert summary.glossary_added == 1
        create_mock.assert_awaited_once_with("ns", "딜리버스", "배달 서비스")

    @pytest.mark.asyncio
    async def test_duplicate_term_counted_not_raised(self, patch_db, monkeypatch):
        monkeypatch.setattr(service, "create_glossary", AsyncMock(side_effect=ValueError("이미 등록된 용어입니다")))
        row = excel_parser.ParsedGlossaryRow(term="딜리버스", description="배달 서비스", remark=None, source_row=2)
        summary = service.SheetSummary(sheet_name="용어집", kind="glossary")

        await service._ingest_glossary_row("ns", row, 1, patch_db, summary)

        assert summary.glossary_duplicate_skipped == 1
        assert summary.glossary_added == 0
