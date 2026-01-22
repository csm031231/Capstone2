from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime

# 공통 속성
class UserBase(BaseModel):
    email: EmailStr

# 회원가입 요청
class UserCreate(UserBase):
    password: str = Field(..., min_length=4, description="비밀번호는 최소 4자 이상")
    nickname: Optional[str] = None

# 로그인 요청
class UserLogin(BaseModel):
    email: EmailStr
    password: str

# 사용자 정보 응답 (비밀번호 제외)
class UserResponse(UserBase):
    id: int
    nickname: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True

# 회원정보 수정
class UserUpdate(BaseModel):
    nickname: Optional[str] = None

# 비밀번호 변경
class PasswordChange(BaseModel):
    current_password: str
    new_password: str

# 토큰 응답
class Token(BaseModel):
    access_token: str
    token_type: str