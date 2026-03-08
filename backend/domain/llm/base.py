"""LLM Provider 추상 기반 클래스."""
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional


_SYSTEM_PROMPT = """당신은 IT 운영팀의 전문 보조 에이전트입니다.
아래 [참고 문서]를 바탕으로 운영자의 질문에 명확하고 간결하게 답변하세요.
- 각 문서에는 점수와 신뢰도(높음/보통/낮음)가 표시되어 있습니다. 신뢰도가 낮은 문서는 참고만 하고 핵심 근거로 사용하지 마세요.
- 참고 문서가 질문과 관련이 없다면, 문서 내용을 억지로 끼워맞추지 말고 "관련 지식을 찾지 못했습니다"라고 솔직하게 답하세요.
- 컨테이너명, 테이블명, SQL 쿼리가 있다면 반드시 언급하세요.
- 한국어로 답변하세요."""


def build_messages(context: str, question: str, history: list[dict] | None = None) -> list[dict]:
    """GPT/Ollama chat 형식의 messages 배열 생성."""
    messages = [
        {"role": "system", "content": f"{_SYSTEM_PROMPT}\n\n[참고 문서]\n{context}"},
    ]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})
    return messages


class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, context: str, question: str, history: list[dict] | None = None, *, api_key: Optional[str] = None) -> str: ...

    @abstractmethod
    async def generate_stream(self, context: str, question: str, history: list[dict] | None = None, *, api_key: Optional[str] = None) -> AsyncIterator[str]: ...

    @abstractmethod
    async def health_check(self) -> bool: ...
