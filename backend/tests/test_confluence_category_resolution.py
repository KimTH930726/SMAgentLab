"""_resolve_confluence_page_category() / _ensure_category_exists() — 2026-09-24 정정:
직계 상위 페이지 제목이 기존 목록에 없으면 이제 자동으로 새 카테고리를 만든다(이전엔
LLM에게 넘기거나 결국 "미분류"로만 떨어졌음). 이 신호는 LLM 추측이 아니라 사람이 이미
컨플루언스에 만들어둔 실제 정보 구조라 신뢰하고 자동 생성해도 안전하다는 판단(사용자 지적
으로 재검토) — `docs/tech/knowledge-category-automation.md` 참고.

conftest.py가 core.database/service.*를 통째로 MagicMock 처리하므로, service.admin.service
(suggest_category_for_content가 실제로 정의된 곳)는 파일 경로 기반으로 실제 로드해 등록한다
(test_agent_rrf_context.py와 동일한 문제/해법)."""
import importlib.util as _ilu
import sys
import types
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


sys.modules.setdefault("service.admin", MagicMock())
if "service.llm.factory" not in sys.modules:
    _fake_factory = types.ModuleType("service.llm.factory")
    _fake_factory.get_llm_provider = MagicMock()
    sys.modules["service.llm.factory"] = _fake_factory
if "service.prompt.loader" not in sys.modules:
    _fake_loader = types.ModuleType("service.prompt.loader")

    async def _default_get_prompt(key, fallback):
        return fallback
    _fake_loader.get_prompt = _default_get_prompt
    sys.modules["service.prompt.loader"] = _fake_loader

_load_real("service.admin.service", "service/admin/service.py")

from agents.knowledge_rag.knowledge.router import (  # noqa: E402
    _resolve_confluence_page_category, _ensure_category_exists, _UNSORTED_CATEGORY,
)


class _FakeDoc:
    def __init__(self, parent_title, source_name="테스트 페이지", raw_text="본문"):
        self.metadata = {"parent_title": parent_title}
        self.source_name = source_name
        self.raw_text = raw_text


@pytest.fixture
def fake_conn(monkeypatch):
    """core.database.get_conn()을 이 테스트 전용 fake conn으로 교체 — conftest 공용
    _fake_conn은 여러 테스트가 공유해 execute 호출 기록이 섞이므로 테스트별로 새로 만든다.

    주의: `import core.database as db`는 상위 패키지 `core`가 MagicMock이라 속성 접근으로
    새 Mock을 만들어내며 sys.modules의 진짜 등록 모듈을 비껴간다 — sys.modules에서 직접
    꺼내야 함(2026-09-22 카테고리 테스트에서 같은 함정을 이미 겪음)."""
    db = sys.modules["core.database"]

    conn = MagicMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=False)
    conn.execute = AsyncMock()
    monkeypatch.setattr(db, "get_conn", MagicMock(return_value=conn))
    return conn


class TestEnsureCategoryExists:
    @pytest.mark.asyncio
    async def test_creates_when_missing(self, fake_conn):
        existing = set()
        await _ensure_category_exists(1, "외부서비스", existing)
        assert "외부서비스" in existing
        fake_conn.execute.assert_awaited_once()
        args = fake_conn.execute.call_args.args
        assert "INSERT INTO rag_knowledge_category" in args[0]
        assert args[1:] == (1, "외부서비스")

    @pytest.mark.asyncio
    async def test_no_op_when_already_present(self, fake_conn):
        existing = {"외부서비스"}
        await _ensure_category_exists(1, "외부서비스", existing)
        fake_conn.execute.assert_not_awaited()


class TestResolveConfluencePageCategory:
    @pytest.mark.asyncio
    async def test_override_wins_even_with_parent_title(self, fake_conn):
        result = await _resolve_confluence_page_category(
            1, _FakeDoc("외부서비스"), "사람이직접고름", set()
        )
        assert result == "사람이직접고름"
        fake_conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_parent_title_already_existing_category_no_insert(self, fake_conn):
        existing = {"외부서비스"}
        result = await _resolve_confluence_page_category(1, _FakeDoc("외부서비스"), None, existing)
        assert result == "외부서비스"
        fake_conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_new_parent_title_auto_creates_category(self, fake_conn):
        """핵심 정정 사항 — 예전엔 이 경우 LLM/미분류로 갔는데, 이제 그 자리에서 바로
        새 카테고리로 만든다(사람이 미리 카테고리를 안 만들어놔도 됨)."""
        existing = set()
        result = await _resolve_confluence_page_category(1, _FakeDoc("배달의민족"), None, existing)
        assert result == "배달의민족"
        assert "배달의민족" in existing
        fake_conn.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_parent_title_falls_back_to_unsorted_when_llm_unavailable(self, fake_conn):
        """트리 루트라 상위 페이지가 없는 경우만 LLM 경로로 내려가고, 여기선 새 카테고리를
        못 만들게 막아둔 그대로 — LLM이 못 고르면 미분류."""
        existing = set()
        result = await _resolve_confluence_page_category(1, _FakeDoc(None), None, existing)
        assert result == _UNSORTED_CATEGORY
        assert _UNSORTED_CATEGORY in existing
