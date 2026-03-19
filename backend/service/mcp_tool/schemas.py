"""MCP 도구 도메인 — Pydantic 스키마."""
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class McpToolParam(BaseModel):
    """도구 파라미터 스키마 항목."""
    name: str
    type: str = "string"              # string | number | boolean
    required: bool = True
    description: str = ""
    example: Optional[str] = None

    @field_validator("example", mode="before")
    @classmethod
    def coerce_example(cls, v):
        if v is None:
            return None
        return str(v)


class McpToolCreate(BaseModel):
    namespace: str
    name: str = Field(..., max_length=100)
    description: str = ""
    method: str = Field(default="GET", pattern="^(GET|POST|PUT|PATCH|DELETE)$")
    hub_base_url: str = ''
    tool_path: str
    headers: dict = Field(default_factory=dict)
    param_schema: list[McpToolParam] = Field(default_factory=list)
    response_example: Optional[dict] = None
    timeout_sec: int = Field(default=10, ge=1, le=60)
    max_response_kb: int = Field(default=50, ge=1, le=500)
    agent_type: str = "knowledge_rag"


class McpToolUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    method: Optional[str] = Field(default=None, pattern="^(GET|POST|PUT|PATCH|DELETE)$")
    hub_base_url: Optional[str] = None
    tool_path: Optional[str] = None
    headers: Optional[dict] = None
    param_schema: Optional[list[McpToolParam]] = None
    response_example: Optional[dict] = None
    timeout_sec: Optional[int] = Field(default=None, ge=1, le=60)
    max_response_kb: Optional[int] = Field(default=None, ge=1, le=500)


class McpToolOut(BaseModel):
    id: int
    namespace: str
    name: str
    description: str
    method: str
    hub_base_url: str
    tool_path: str
    url: str
    headers: dict
    param_schema: list[McpToolParam]
    response_example: Optional[dict]
    timeout_sec: int
    max_response_kb: int
    is_active: bool
    agent_type: str
    created_at: str


class McpToolToggle(BaseModel):
    is_active: bool


class AutoCompleteRequest(BaseModel):
    namespace: str
    raw_text: str = Field(..., min_length=10)


class McpToolTestRequest(BaseModel):
    params: dict = Field(default_factory=dict)
