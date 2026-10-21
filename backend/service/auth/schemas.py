"""인증/계정 도메인 — Pydantic 스키마."""
from typing import Literal, Optional
from pydantic import BaseModel, Field

from service.validation import Name, Username


class LLMCredentialsPayload(BaseModel):
    client_id: str = Field(min_length=1, max_length=200)
    client_secret: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=100)


class RegisterRequest(BaseModel):
    # 앞뒤 공백 제거 — " abc"가 "abc" 옆에 닮은 계정으로 가입되던 것(막 쓰기 테스트 10/20)
    username: Username
    password: str = Field(min_length=4, max_length=100)
    part: str
    llm_credentials: Optional[LLMCredentialsPayload] = None  # Inhouse LLM용, 선택


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserOut"


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=4, max_length=100)


class LLMCredentialsUpdateRequest(BaseModel):
    credentials: LLMCredentialsPayload


class ConfluencePATRequest(BaseModel):
    pat: str


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    part: Optional[str] = None   # 소속 없음 가능(관리자 등) — 예전엔 비면 로그인 응답 오류(500)
    is_active: bool
    has_llm_credentials: bool
    has_confluence_pat: bool = False
    created_at: str


class UserAdminUpdate(BaseModel):
    role: Optional[Literal["admin", "user", "viewer"]] = None   # 아무 글자나 저장되던 것(막 쓰기 테스트 10/20)
    part: Optional[str] = None
    is_active: Optional[bool] = None


class PartCreate(BaseModel):
    name: Name   # 파트 이름 — URL 경로에 들어가 '/' 금지


class PartOut(BaseModel):
    id: int
    name: str
    created_at: str
    team: Optional[str] = None
    user_count: int = 0
    domains: list[str] = []   # 담당 업무 영역(관리자 목록에서만 채움)


class PartDomainsUpdate(BaseModel):
    # 저장된 이름을 그대로 받는다(이름 규칙이 생기기 전 영역도 있을 수 있어 공통 이름 검사를 걸지 않음 — 없는 이름은 서비스가 400)
    domains: list[str] = Field(default_factory=list, max_length=500)
