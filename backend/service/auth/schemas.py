"""인증/계정 도메인 — Pydantic 스키마."""
from typing import Optional
from pydantic import BaseModel, Field


class LLMCredentialsPayload(BaseModel):
    client_id: str = Field(min_length=1, max_length=200)
    client_secret: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=100)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=50)
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
    part: str
    is_active: bool
    has_llm_credentials: bool
    has_confluence_pat: bool = False
    created_at: str


class UserAdminUpdate(BaseModel):
    role: Optional[str] = None
    part: Optional[str] = None
    is_active: Optional[bool] = None


class PartCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class PartOut(BaseModel):
    id: int
    name: str
    created_at: str
    user_count: int = 0
