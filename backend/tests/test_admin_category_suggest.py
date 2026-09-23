"""service/admin/service.py의 suggest_category_for_content() — 2026-09-22 지식 카테고리
자동화(docs/tech/knowledge-category-automation.md)의 핵심 재사용 블록. 컨플루언스 벌크
등록(agents/knowledge_rag/knowledge/router.py)과 기존 수동 추천 API(POST /categories/suggest)
양쪽이 공유하므로, 이 함수 하나만 검증하면 두 경로 모두 보호된다.

실제 3단 폴백 전체(결정론 매칭 + 미분류 자동생성)의 실 DB 시나리오 검증은 스크립트로 별도
확인함(전부 통과: parent_title 일치 → LLM 미호출, override 우선, 매칭 실패 시 '미분류' 자동
생성 + 중복 없이 재사용) — 이 파일은 그중 LLM 응답 파싱 로직만 유닛테스트로 고정한다.

conftest.py는 shared/service를 통째로 MagicMock 처리해서(test_agent_rrf_context.py의 동일한
문제 설명 참고) service.admin.service를 그냥 import하면 안 된다 — 파일 경로 기반으로 실제
모듈을 로드해 sys.modules에 등록한다."""
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


# service.admin.service가 함수 내부에서 지역 import하는 service.llm.factory/
# service.prompt.loader는 실제 모듈을 로드하지 않고 최소 속성만 가진 가짜 모듈로 등록—
# factory.py 자체가 service.llm.base/inhouse/ollama까지 실제 LLM SDK 의존성을 끌고 와서
# 이 테스트 목적(문자열 매칭 로직 검증)엔 과하고, 테스트마다 monkeypatch로 그 속성만
# 바꿔치기하면 충분하다.
sys.modules.setdefault("service.admin", MagicMock())
sys.modules.setdefault("service.llm", MagicMock())
sys.modules.setdefault("service.prompt", MagicMock())
if "service.llm.factory" not in sys.modules:
    fake_factory = types.ModuleType("service.llm.factory")
    fake_factory.get_llm_provider = MagicMock()
    sys.modules["service.llm.factory"] = fake_factory
if "service.prompt.loader" not in sys.modules:
    fake_loader = types.ModuleType("service.prompt.loader")

    async def _default_get_prompt(key, fallback):
        return fallback
    fake_loader.get_prompt = _default_get_prompt
    sys.modules["service.prompt.loader"] = fake_loader

service = _load_real("service.admin.service", "service/admin/service.py")


class _FakeConn:
    def __init__(self, category_rows):
        self._rows = category_rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetch(self, *a, **kw):
        return [{"name": n} for n in self._rows]


def _patch_categories(monkeypatch, names):
    monkeypatch.setattr(service, "get_conn", MagicMock(return_value=_FakeConn(names)))


def _patch_llm(monkeypatch, answer: str):
    # 주의: `import service.llm.factory as x`는 상위 패키지 service/service.llm이
    # MagicMock이라 속성 접근으로 새 Mock을 만들어내며 sys.modules의 진짜 fake 모듈을
    # 완전히 비껴간다 — 반드시 sys.modules에서 직접 꺼내 몽키패치해야 실제로 먹힌다.
    provider = MagicMock()
    provider.generate = AsyncMock(return_value=(answer, {}))
    llm_factory = sys.modules["service.llm.factory"]
    monkeypatch.setattr(llm_factory, "get_llm_provider", MagicMock(return_value=provider))

    async def _fake_load_prompt(key, fallback):
        return fallback
    prompt_loader = sys.modules["service.prompt.loader"]
    monkeypatch.setattr(prompt_loader, "get_prompt", _fake_load_prompt)


class TestSuggestCategoryForContent:
    @pytest.mark.asyncio
    async def test_empty_content_returns_none_without_llm_call(self, monkeypatch):
        _patch_categories(monkeypatch, ["공통지식"])
        assert await service.suggest_category_for_content(1, "   ") is None

    @pytest.mark.asyncio
    async def test_no_categories_defined_returns_none(self, monkeypatch):
        _patch_categories(monkeypatch, [])
        assert await service.suggest_category_for_content(1, "아무 내용") is None

    @pytest.mark.asyncio
    async def test_exact_llm_answer_matches_existing_category(self, monkeypatch):
        _patch_categories(monkeypatch, ["외부서비스", "공통지식"])
        _patch_llm(monkeypatch, "외부서비스")
        result = await service.suggest_category_for_content(1, "배달의민족 연동 관련 내용")
        assert result == "외부서비스"

    @pytest.mark.asyncio
    async def test_llm_answer_never_invents_new_category(self, monkeypatch):
        """LLM이 목록에 없는 이름을 답해도(예: 따옴표·부연설명 섞임) 부분일치로만 매칭하고,
        그마저 안 되면 None — 새 카테고리를 즉석에서 만들어내지 않는다(설계 문서 §5-1 결정:
        기존 목록 내 매핑만 허용)."""
        _patch_categories(monkeypatch, ["외부서비스", "공통지식"])
        _patch_llm(monkeypatch, "완전히 존재하지 않는 카테고리명")
        result = await service.suggest_category_for_content(1, "무관한 내용")
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_partial_match_still_resolves_to_existing_name(self, monkeypatch):
        _patch_categories(monkeypatch, ["외부서비스", "공통지식"])
        _patch_llm(monkeypatch, '"외부서비스" 카테고리가 적합합니다')
        result = await service.suggest_category_for_content(1, "쿠팡이츠 연동 문의")
        assert result == "외부서비스"

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back_to_none(self, monkeypatch):
        _patch_categories(monkeypatch, ["외부서비스"])
        provider = MagicMock()
        provider.generate = AsyncMock(side_effect=RuntimeError("LLM 타임아웃"))
        llm_factory = sys.modules["service.llm.factory"]
        monkeypatch.setattr(llm_factory, "get_llm_provider", MagicMock(return_value=provider))
        prompt_loader = sys.modules["service.prompt.loader"]

        async def _fake_load_prompt(key, fallback):
            return fallback
        monkeypatch.setattr(prompt_loader, "get_prompt", _fake_load_prompt)

        result = await service.suggest_category_for_content(1, "아무 내용")
        assert result is None
