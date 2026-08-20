"""Tests for service/email_voc/service.py — _mask_pii() 검증.

실사용 중 발견: 인하우스 LLM 게이트웨이가 프롬프트에 IP나 이메일 주소가 섞이면
"민감 정보 포함" 사유로 분석 전체를 거부한다(예: '10.103.49.63' 하나만 있어도
전체 거부). VOC 메일은 호스트 IP·CC 목록이 거의 항상 섞여 있어 이 마스킹이
없으면 실제 이메일 대부분이 분석되지 못한다 — 회귀를 막기 위한 테스트.

conftest.py가 격리 목적으로 sys.modules["service"] 전체를 MagicMock으로 치환해두므로
(teams_notify/graph_client 테스트와 동일한 문제), 파일 경로 기반으로 직접 로드한다.
"""
import importlib.util as _ilu
import sys
from pathlib import Path

_backend_dir = Path(__file__).resolve().parent.parent
_spec = _ilu.spec_from_file_location(
    "email_voc_service_under_test", str(_backend_dir / "service" / "email_voc" / "service.py"),
)
service = _ilu.module_from_spec(_spec)
sys.modules[_spec.name] = service
_spec.loader.exec_module(service)


class TestMaskPii:
    def test_masks_ip_address(self):
        assert "10.103.49.63" not in service._mask_pii("호스트 10.103.49.63 점검 필요")
        assert "[REDACTED_IP]" in service._mask_pii("호스트 10.103.49.63 점검 필요")

    def test_masks_email_address(self):
        text = service._mask_pii("문의: rlaxogns9024@shinsegae.com 로 연락주세요")
        assert "rlaxogns9024@shinsegae.com" not in text
        assert "[REDACTED_EMAIL]" in text

    def test_masks_multiple_occurrences(self):
        text = service._mask_pii("a@example.com, b@example.com, 10.0.0.1, 10.0.0.2")
        assert "@example.com" not in text
        assert text.count("[REDACTED_EMAIL]") == 2
        assert text.count("[REDACTED_IP]") == 2

    def test_leaves_normal_text_untouched(self):
        text = "패스워드 만료 예정 안내 - 시스템 계정 점검이 필요합니다"
        assert service._mask_pii(text) == text

    def test_does_not_mangle_version_like_numbers(self):
        # 버전 문자열(x.x.x.x)도 IP 정규식과 형태가 같아 같이 마스킹되는 것은
        # 허용된 트레이드오프다(과소 마스킹보다 과다 마스킹이 안전) — 다만
        # 일반 문장에서 흔한 소수점 하나짜리 숫자는 건드리지 않아야 한다.
        text = service._mask_pii("응답시간 1.5초 소요")
        assert text == "응답시간 1.5초 소요"


class TestSnippet:
    def test_short_content_unchanged(self):
        assert service._snippet("짧은 내용") == "짧은 내용"

    def test_long_content_truncated_with_ellipsis(self):
        content = "가" * 100
        result = service._snippet(content, length=60)
        assert result == "가" * 60 + "..."

    def test_collapses_whitespace(self):
        assert service._snippet("여러   줄\n바꿈\n\n텍스트") == "여러 줄 바꿈 텍스트"
