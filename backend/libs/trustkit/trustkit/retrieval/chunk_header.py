"""맥락 머리(contextual chunk header) — 조각을 임베딩할 때 "무엇에 대한 조각인지"를 앞에 붙인다.

Anthropic "Introducing Contextual Retrieval"(2024)과 같은 발상. 원형은 LLM이 조각마다 맥락 문장을 쓰지만, 여기선 제목·경로로
결정적으로 만든다(비용 0, 같은 입력 → 같은 결과). 저장 텍스트·답 문맥은 조각 그대로 두고 **임베딩 입력에만** 쓰는 것이 원칙.
OpsLens 정책서 실측: 조각 중앙값 28자("최대 20개"), Recall@10 88.6→92.0%(나빠진 문항 0).
"""
from __future__ import annotations

from typing import Optional, Sequence


def contextual_header(title: str, path: Optional[Sequence[str]], chunk: str, sep: str = " > ") -> str:
    """"제목 (경로1 > 경로2): 조각". 이미 같은 머리로 시작하는 조각("제목 (…): …")은 그대로."""
    if chunk.startswith(f"{title} ("):
        return chunk
    return f"{title} ({sep.join(path or [])}): {chunk}"
