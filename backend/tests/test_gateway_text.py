"""사내 게이트웨이 민감정보 오탐 회피 (2026-10-06, v2.126).

게이트웨이가 매뉴얼의 버전·목차 번호(`3.1.2.4`)를 IP로, 8자리 이상 숫자를 ID로 보고 질문 전체를 거부했다.
나가는 프롬프트에서 숫자 형태만 바꾸고(가리지 않음), 답에서는 원래 형태로 되돌려야 한다 — 스트리밍 토큰이
숫자 중간에서 잘려도. 날짜·금액·소수 같은 비대상은 그대로여야 한다.
"""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "_real_llm_gateway_text",
    str(Path(__file__).resolve().parent.parent / "service" / "llm" / "gateway_text.py"),
)
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)

D, N = gw.SEP_DOT, gw.SEP_NUM


class TestToGateway:
    @pytest.mark.parametrize("src,expected", [
        ("메뉴 3.1.2.4 참고", f"메뉴 3{D}1{D}2{D}4 참고"),
        ("2.10.1.3.5단계", f"2{D}10{D}1{D}3{D}5단계"),
        ("(1.2.3.4)", f"(1{D}2{D}3{D}4)"),
        ("끝 3.1.2.4.", f"끝 3{D}1{D}2{D}4."),  # 문장 끝 마침표는 그대로
        ("번호 123456789012 확인", f"번호 1234{N}5678{N}9012 확인"),
        ("20261006", f"2026{N}1006"),
        ("123456789", f"1{N}2345{N}6789"),
    ])
    def test_targets(self, src, expected):
        assert gw.to_gateway(src) == expected

    @pytest.mark.parametrize("src", [
        "2026-10-06", "150000원", "3.5", "1.2.3", "v2.124", "1234567", "010-1234-5678",
        "3.5배", "금액 1,500,000원", "", "숫자 없음",
    ])
    def test_non_targets_unchanged(self, src):
        assert gw.to_gateway(src) == src

    def test_none_safe(self):
        assert gw.to_gateway(None) is None


class TestRestore:
    @pytest.mark.parametrize("src", [
        "메뉴 3.1.2.4 참고", "2.10.1.3.5단계", "번호 123456789012 확인", "20261006", "123456789",
        "섞임 1.2.3.4 와 87654321, 날짜 2026-10-06, 금액 150000원, 3.5",
    ])
    def test_roundtrip(self, src):
        assert gw.from_gateway(gw.to_gateway(src)) == src

    def test_plain_text_untouched(self):
        # 원래 있던 가운뎃점(숫자 사이가 아님)은 그대로
        assert gw.from_gateway("각인·선물포장 옵션") == "각인·선물포장 옵션"


class TestStreamRestorer:
    def _run(self, tokens):
        r = gw.StreamRestorer()
        return "".join(r.feed(t) for t in tokens) + r.flush()

    def test_split_inside_dotted(self):
        tokens = ["메뉴 3", D, "1", f"{D}2", D, "4 항목", "입니다"]
        assert self._run(tokens) == "메뉴 3.1.2.4 항목입니다"

    def test_split_inside_long_number(self):
        tokens = ["번호 12", f"34{N}56", f"78{N}", "9012", " 확인"]
        assert self._run(tokens) == "번호 123456789012 확인"

    def test_number_at_end_flushed(self):
        assert self._run(["버전은 3", f"{D}1{D}2{D}4"]) == "버전은 3.1.2.4"

    def test_every_char_split(self):
        src = f"a 1{D}2{D}3{D}4 b 1234{N}5678 c 150000원"
        assert self._run(list(src)) == "a 1.2.3.4 b 12345678 c 150000원"

    def test_text_without_numbers_passes_immediately(self):
        r = gw.StreamRestorer()
        assert r.feed("안녕하세요") == "안녕하세요"
        assert r.feed(" 반갑") == " 반갑"
        assert r.flush() == ""
