"""에이전트 추상 기반 클래스 + 레지스트리."""
from abc import ABC, abstractmethod
from typing import AsyncIterator


class AgentBase(ABC):
    """모든 에이전트가 구현해야 하는 인터페이스."""

    @property
    @abstractmethod
    def agent_id(self) -> str:
        """고유 식별자: "knowledge_rag", "text2sql" 등."""
        ...

    @property
    @abstractmethod
    def metadata(self) -> dict:
        """프론트엔드 동적 렌더링용 메타데이터.

        Returns:
            {
                "display_name": str,
                "description": str,
                "icon": str,          # lucide icon name
                "color": str,         # Badge 색상
                "output_type": str,   # "text" | "table" | "timeline"
                "welcome_message": str,
                "supports_debug": bool,
            }
        """
        ...

    @abstractmethod
    async def stream_chat(
        self,
        query: str,
        user: dict,
        conversation_id: int,
        context: dict,
    ) -> AsyncIterator[dict]:
        """SSE 이벤트 dict를 yield하는 비동기 제너레이터."""
        ...

    async def health_check(self) -> bool:
        """에이전트 헬스 체크. 기본은 항상 True."""
        return True

    def get_admin_router(self):
        """에이전트 전용 관리 API 라우터. 없으면 None."""
        return None


class AgentRegistry:
    """등록된 에이전트를 관리하는 싱글톤 레지스트리."""

    _agents: dict[str, AgentBase] = {}

    @classmethod
    def register(cls, agent: AgentBase) -> None:
        cls._agents[agent.agent_id] = agent

    @classmethod
    def get(cls, agent_id: str) -> AgentBase:
        if agent_id not in cls._agents:
            raise ValueError(f"Unknown agent: {agent_id}")
        return cls._agents[agent_id]

    @classmethod
    def list_all(cls) -> list[dict]:
        return [{"agent_id": a.agent_id, **a.metadata} for a in cls._agents.values()]
