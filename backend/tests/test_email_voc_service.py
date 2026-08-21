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

    def test_masks_international_signature_phone(self):
        # 실제 거부 사례: 이메일 서명란의 "M 82.10.2547.7280" 형태
        text = service._mask_pii("M 82.10.2547.7280")
        assert "82.10.2547.7280" not in text
        assert "[REDACTED_PHONE]" in text

    def test_masks_dashed_phone_numbers(self):
        text = service._mask_pii("연락처: 010-1234-5678, 02-1234-5678")
        assert "010-1234-5678" not in text
        assert "02-1234-5678" not in text
        assert text.count("[REDACTED_PHONE]") == 2

    def test_masks_space_separated_phone(self):
        # 실제 거부 사례: "+82 10 5000 3985" (공백 구분 국제 표기)
        text = service._mask_pii("+82 10 5000 3985")
        assert "5000 3985" not in text
        assert "[REDACTED_PHONE]" in text

    def test_does_not_mangle_iso_dates(self):
        # 만료일 같은 날짜는 심각도 판단에 중요한 정보라 마스킹되면 안 된다
        # (YYYY-MM-DD는 첫 그룹이 4자리라 전화번호 정규식과 구분됨).
        text = service._mask_pii("만료일 2024-08-06, 2026-04-07")
        assert text == "만료일 2024-08-06, 2026-04-07"

    def test_masks_ticket_id(self):
        # 실제 거부 사례: VOC 티켓 ID "C202608150816" — 구분자 없는 12자리 숫자
        text = service._mask_pii("문의번호 C202608150816 관련입니다")
        assert "202608150816" not in text
        assert "[REDACTED_ID]" in text

    def test_masks_sku_code(self):
        # 실제 거부 사례: SKU 코드 "9900000000339" — 구분자 없는 13자리 숫자
        text = service._mask_pii("콜드 폼(레시피용): 9900000000339")
        assert "9900000000339" not in text
        assert "[REDACTED_ID]" in text

    def test_does_not_mask_short_amounts(self):
        text = service._mask_pii("결제금액 15000원, 재고 500개")
        assert text == "결제금액 15000원, 재고 500개"


class TestStripForwardedChain:
    def test_strips_at_korean_forward_header(self):
        text = (
            "안녕하세요 담당님\n스타벅스 이영훈입니다.\n\n"
            "콜드폼 정렬 순서 운영환경에서도 변경 완료하였고\n익일 마스터로 내려갈 예정입니다.\n\n"
            "감사합니다.\n피칸드림.\n\n"
            "보낸 사람: 신형섭(온라인서비스 딜리버스/외부서비스) - 스타벅스CSP팀 <shinhs@shinsegae.com>\n"
            "날짜: 목요일, 2026년 8월 20일 오후 3:56"
        )
        result = service._strip_forwarded_chain(text)
        assert "보낸 사람" not in result
        assert "shinhs@shinsegae.com" not in result
        assert "피칸드림." in result  # 새로 쓴 부분은 그대로 남아야 함

    def test_strips_at_english_from_header(self):
        text = (
            "안녕하세요.\nSSG 페이먼츠 결제플랫폼팀 나성민입니다.\n협조 요청 사항입니다.\n\n감사합니다.\n\n\n"
            "From: 나성민(파트장) - 인프라\nSent: Tuesday, August 11, 2026 4:15 PM\n"
            "To: 신세계I&C 이마트팀 <IC0M23A570@shinsegae.com>; ..."
        )
        result = service._strip_forwarded_chain(text)
        assert "From:" not in result
        assert "IC0M23A570" not in result
        assert "협조 요청 사항입니다." in result

    def test_no_marker_returns_original(self):
        text = "고객이 결제 시도 시 500 에러가 계속 발생한다고 합니다."
        assert service._strip_forwarded_chain(text) == text

    def test_strips_at_original_message_separator(self):
        text = "새로 쓴 내용입니다.\n\n-----Original Message-----\n예전 내용입니다."
        result = service._strip_forwarded_chain(text)
        assert result == "새로 쓴 내용입니다."


class TestSnippet:
    def test_short_content_unchanged(self):
        assert service._snippet("짧은 내용") == "짧은 내용"

    def test_long_content_truncated_with_ellipsis(self):
        content = "가" * 100
        result = service._snippet(content, length=60)
        assert result == "가" * 60 + "..."

    def test_collapses_whitespace(self):
        assert service._snippet("여러   줄\n바꿈\n\n텍스트") == "여러 줄 바꿈 텍스트"
