"""Tests for service/policy/korean_text.py — 규칙 기반 한국어 조사/어미 제거.

SQL 쪽 구현(init/06-policy-strip-ko.sql의 policy_strip_ko())과의 동작 일치는 2026-09-10
수동 검증(docker exec ops-postgres psql로 동일 단어 세트 비교)으로 확인됨 — 이 프로젝트의
DB 접근 테스트는 실 DB에 붙지 않고 목으로 처리하는 기존 컨벤션(test_policy_search.py 등)을
따라, 여기서는 순수 함수 단위 테스트만 둔다.

파일 경로 기반 spec 로딩을 쓰는 이유: 다른 policy 테스트 모듈들이 `sys.modules["service.policy"]`
를 MagicMock으로 치환해둬서(예: test_policy_browse.py) 같은 pytest 세션에서 일반
`from service.policy.korean_text import ...`가 "service.policy is not a package"로 깨진다
(korean_text.py 자체는 외부 의존성이 없는 순수 함수라 이렇게 격리해서 로드해도 무방)."""
import importlib.util as _ilu
from pathlib import Path

_spec = _ilu.spec_from_file_location(
    "service.policy.korean_text", str(Path(__file__).resolve().parent.parent / "service" / "policy" / "korean_text.py")
)
korean_text = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(korean_text)
strip_suffix = korean_text.strip_suffix
strip_text = korean_text.strip_text


class TestStripSuffix:
    def test_strips_object_particle(self):
        assert strip_suffix("장바구니를") == "장바구니"

    def test_strips_topic_particle(self):
        assert strip_suffix("개수는") == "개수"

    def test_strips_longest_matching_suffix_first(self):
        # "습니다"(3글자)가 "다"(1글자)보다 먼저 매칭돼야 함
        assert strip_suffix("확인했습니다") == "확인했"

    def test_does_not_strip_below_min_stem_length(self):
        # "담을" - "을" = "담"(1글자) → 최소 잔여 2글자 미달이라 절단 안 함
        assert strip_suffix("담을") == "담을"

    def test_word_with_no_matching_suffix_unchanged(self):
        assert strip_suffix("테스트") == "테스트"

    def test_empty_string_unchanged(self):
        assert strip_suffix("") == ""


class TestStripText:
    def test_strips_each_word_independently(self):
        assert strip_text("장바구니를 개수는") == "장바구니 개수"

    def test_preserves_word_order_and_count(self):
        result = strip_text("배달비가 20개까지는 무료입니다")
        assert result.split() == ["배달비", "20개", "무료"]

    def test_single_word(self):
        assert strip_text("상품이며") == "상품"
