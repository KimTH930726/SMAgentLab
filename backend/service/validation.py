"""입력 이름 칸 공통 검증 — 업무 영역·업무구분·파트·사용자 이름이 화면마다 따로 검사돼 어긋났다(2026-10-20 막 쓰기 테스트:
공백만·빈 이름·앞뒤 공백 닮은 이름·5천 자 이름이 저장되거나 500). 새 이름 칸은 여기 타입을 쓴다."""
from typing import Annotated

from pydantic import AfterValidator, StringConstraints


def _no_slash(v: str) -> str:
    # 이름이 URL 경로에 들어간다 — '/'가 있으면 그 항목을 고치거나 지울 주소를 만들 수 없다
    if "/" in v:
        raise ValueError("이름에 '/'는 쓸 수 없습니다.")
    return v


def name_type(max_length: int = 100, min_length: int = 1, allow_slash: bool = False):
    """앞뒤 공백 제거 후 길이 검사(+ '/' 금지). 파트·업무 영역·업무구분처럼 URL 경로에 들어가는 이름은 '/' 금지."""
    parts = [StringConstraints(strip_whitespace=True, min_length=min_length, max_length=max_length)]
    if not allow_slash:
        parts.append(AfterValidator(_no_slash))
    return Annotated[str, *parts]


Name = name_type()                          # 업무 영역·업무구분·파트
Username = name_type(max_length=50, min_length=2)
