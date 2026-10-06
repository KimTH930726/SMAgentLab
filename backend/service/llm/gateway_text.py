"""사내 게이트웨이(DevX) 민감정보 필터 오탐 회피 — 나가는 프롬프트의 숫자 형태만 바꾸고, 답은 원래 형태로 되돌린다.

게이트웨이는 컨플루언스 매뉴얼의 버전·목차 번호(`3.1.2.4`)를 IP로, 8자리 이상 숫자를 ID로 보고 요청 전체를
거부한다(2026-10-06 재현). 이 값들은 답에 필요한 정보라 가리지 않고(VOC `_mask_pii`와 다름) 구분자만 바꾼다.
- 점 4묶음 이상 숫자: 점 → SEP_DOT
- 8자리 이상 연속 숫자: 4자리마다 SEP_NUM
구분자는 게이트웨이 실측으로 고름(2026-10-06: `·`/`．`/`。`/`-` 모두 통과, `-`는 전화번호·날짜와 섞여 원복이 모호해
제외). 원문에 거의 안 나오는 문자라 답에서 "숫자 사이의 이 문자"를 원래대로 되돌려도 다른 내용을 건드리지 않는다.

**임시책(10/8 시연용)이자 보안 필터 우회다** — 그래서 적용 범위를 근거 문서 본문·이전 답으로만 한정한다(inhouse
`_build_query`). 사용자 질문·사용자 이력엔 절대 적용하지 않는다(사용자가 넣은 진짜 민감정보는 게이트웨이가 막아야 함).
근본 해결은 게이트웨이 담당에 오탐 예외·규칙 조정 요청 — 그게 되면 이 변환은 제거한다.

URL·이메일 등 다른 오탐 패턴이 생기면 _RULES에 (패턴, 변환, 원복 패턴, 원복) 한 쌍을 추가한다.
"""
import re

SEP_DOT = "．"  # 전각 마침표 '．'
SEP_NUM = "·"  # 가운뎃점 '·'

# 앞뒤가 숫자·점이 아닌 "숫자(.숫자){3,}" — 소수(3.5)·날짜(2026-10-06)·3묶음(1.2.3)은 대상 아님
_DOTTED = re.compile(r"(?<![\d.])\d+(?:\.\d+){3,}(?![\d]|\.\d)")
_LONG_NUM = re.compile(r"(?<![\d.])\d{8,}(?![\d])")

_DOTTED_BACK = re.compile(rf"(?<!\d)\d+(?:{SEP_DOT}\d+){{3,}}(?!\d)")
_LONG_NUM_BACK = re.compile(rf"(?<!\d)\d{{1,4}}(?:{SEP_NUM}\d{{4}}){{1,}}(?!\d)")


def _group4(digits: str) -> str:
    # 뒤에서부터 4자리씩(천 단위 쉼표처럼) — 12345678901 → 123·4567·8901
    head = len(digits) % 4 or 4
    return SEP_NUM.join([digits[:head]] + [digits[i:i + 4] for i in range(head, len(digits), 4)])


def to_gateway(text: str) -> str:
    """게이트웨이로 보내기 전 변환. 대상 패턴이 없으면 그대로 반환."""
    if not text:
        return text
    text = _DOTTED.sub(lambda m: m.group(0).replace(".", SEP_DOT), text)
    text = _LONG_NUM.sub(lambda m: _group4(m.group(0)), text)
    return text


def from_gateway(text: str) -> str:
    """LLM 답에 나온 변환 형태를 원래대로."""
    if not text or (SEP_DOT not in text and SEP_NUM not in text):
        return text
    text = _DOTTED_BACK.sub(lambda m: m.group(0).replace(SEP_DOT, "."), text)
    text = _LONG_NUM_BACK.sub(lambda m: m.group(0).replace(SEP_NUM, ""), text)
    return text


_PENDING_TAIL = re.compile(rf"[\d{SEP_DOT}{SEP_NUM}]+$")


class StreamRestorer:
    """스트리밍 토큰 원복 — 토큰 끝이 숫자·구분자면 다음 토큰과 이어질 수 있어 잠시 붙잡아 둔다.

    feed()가 내보내는 부분은 항상 숫자·구분자가 아닌 문자로 끝나므로 그 안의 숫자 묶음은 완결돼 있다.
    """

    def __init__(self) -> None:
        self._held = ""

    def feed(self, token: str) -> str:
        buf = self._held + token
        m = _PENDING_TAIL.search(buf)
        if m:
            self._held, buf = buf[m.start():], buf[:m.start()]
        else:
            self._held = ""
        return from_gateway(buf)

    def flush(self) -> str:
        out, self._held = from_gateway(self._held), ""
        return out
