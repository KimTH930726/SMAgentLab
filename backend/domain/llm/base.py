"""LLM Provider 추상 기반 클래스."""
from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable, Optional

from domain.prompt.loader import get_prompt as _load_prompt


_FALLBACK_SYSTEM_PROMPT = """IT 운영 보조 에이전트. 아래 규칙을 따르세요.

[원칙]
- 반드시 제공된 [참고 문서]만 근거로 답변. 문서에 없는 내용은 절대 만들어내지 마세요.
- 관련 문서가 없으면 "관련 지식을 찾지 못했습니다"로 답변.
- 신뢰도 높음 문서를 우선 근거로 사용. 낮음은 보조 참고만.

[문맥 활용]
- [과거 유사 사례]가 있으면 답변 형식을 참고하되 현재 문서 내용 우선.
- 이전 대화가 있으면 맥락을 이어서 답변.

[형식]
- Markdown(표, 목록, 코드 블록, 볼드) 사용. 한국어 답변.
- 컨테이너명, 테이블명, SQL이 있으면 반드시 포함.
- 답변 끝에 근거 표시: 📎 문서 N, 문서 M 참고"""


async def resolve_system_prompt(system_prompt: Optional[str] = None) -> str:
    """system_prompt가 명시적으로 주어지면 그대로 사용, 아니면 DB에서 chat_system 로드."""
    if system_prompt is not None:
        return system_prompt
    return await _load_prompt("chat_system", _FALLBACK_SYSTEM_PROMPT)


def build_messages(
    context: str, question: str, history: list[dict] | None = None,
    *, system_prompt: Optional[str] = None,
) -> list[dict]:
    """GPT/Ollama chat 형식의 messages 배열 생성.
    system_prompt를 지정하면 기본 시스템 프롬프트 대신 사용한다.
    """
    sp = system_prompt if system_prompt is not None else _FALLBACK_SYSTEM_PROMPT
    sys_content = f"{sp}\n\n[참고 문서]\n{context}" if context else sp
    messages = [{"role": "system", "content": sys_content}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": question})
    return messages


class LLMProvider(ABC):
    @abstractmethod
    async def generate_once(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2000,
    ) -> str:
        """단순 단일 응답 생성 (파이프라인 스테이지용 — 스트리밍 없음).

        Args:
            prompt: 사용자 프롬프트 (템플릿 치환 완료 후)
            system: 시스템 프롬프트
            max_tokens: 최대 생성 토큰 수

        Returns:
            LLM이 생성한 텍스트
        """
        ...

    @abstractmethod
    async def generate(
        self,
        context: str,
        question: str,
        history: list[dict] | None = None,
        *,
        api_key: Optional[str] = None,
        ext_conversation_id: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> tuple[str, Optional[str]]:
        """답변 생성. 반환값: (answer, ext_conversation_id).
        system_prompt: 기본 시스템 프롬프트를 대체할 커스텀 프롬프트.
        """
        ...

    @abstractmethod
    async def generate_stream(
        self,
        context: str,
        question: str,
        history: list[dict] | None = None,
        *,
        api_key: Optional[str] = None,
        ext_conversation_id: Optional[str] = None,
        on_ext_conversation_id: Optional[Callable[[str], None]] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncIterator[str]:
        """스트리밍 답변 생성."""
        ...

    @abstractmethod
    async def health_check(self) -> bool: ...
