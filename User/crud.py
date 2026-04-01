from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Optional, Dict, Any

from core.models import User
from core.dependencies import hash_password # core/dependencies.py에서 가져옴
from User.dto import UserCreate

async def get_user_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
    """ID로 사용자 조회"""
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalars().first()

async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    """이메일로 사용자 조회"""
    result = await db.execute(select(User).where(User.email == email))
    return result.scalars().first()

async def create_user(db: AsyncSession, user_data: UserCreate) -> User:
    """사용자 생성 (회원가입)"""
    # dependencies.py의 hash_password 사용
    hashed_pw = hash_password(user_data.password)
    
    # 닉네임이 없으면 이메일 ID 부분 사용
    nickname = user_data.nickname if user_data.nickname else user_data.email.split("@")[0]
    
    new_user = User(
        email=user_data.email,
        hashed_password=hashed_pw,
        nickname=nickname
    )
    
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    return new_user

async def update_user(db: AsyncSession, user: User, update_data: Dict[str, Any]) -> User:
    """사용자 정보 수정"""
    for key, value in update_data.items():
        setattr(user, key, value)
    await db.commit()
    await db.refresh(user)
    return user

async def delete_user(db: AsyncSession, user: User) -> None:
    """사용자 삭제 (회원탈퇴)"""
    await db.delete(user)
    await db.commit()