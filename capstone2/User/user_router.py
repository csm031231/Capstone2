from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.security import OAuth2PasswordBearer

# User가 정의한 core 모듈들 import
from core.database import provide_session
from core.dependencies import create_jwt, verify_jwt, verify_password, hash_password
from core.models import User

from User.dto import (
    UserCreate, UserResponse, UserLogin, Token, UserUpdate, PasswordChange
)
from User.crud import (
    create_user, get_user_by_email, get_user_by_id, 
    update_user, delete_user
)

router = APIRouter(
    prefix="/users",
    tags=["users"]
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="users/login")

# --- 인증 의존성 함수 ---
async def get_current_user(
    db: AsyncSession = Depends(provide_session), 
    token: str = Depends(oauth2_scheme)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    # dependencies.py의 verify_jwt 사용
    payload = verify_jwt(token)
    if payload is None:
        raise credentials_exception
    
    # payload에서 sub(user_id) 추출 (create_jwt에서 sub를 str로 저장했으므로 주의)
    user_id = payload.get("sub")
    if user_id is None:
        raise credentials_exception
    
    user = await get_user_by_id(db, int(user_id))
    if user is None:
        raise credentials_exception
    
    return user

# --- API 엔드포인트 ---

# 1. 회원가입
@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate, db: AsyncSession = Depends(provide_session)):
    # 이메일 중복 확인
    if await get_user_by_email(db, user_data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # 사용자 생성
    return await create_user(db, user_data)

# 2. 로그인
@router.post("/login", response_model=Token)
async def login(user_data: UserLogin, db: AsyncSession = Depends(provide_session)):
    # 사용자 조회
    user = await get_user_by_email(db, user_data.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    
    # 비밀번호 검증 (core/dependencies.py 함수 사용)
    if not verify_password(user_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    
    # 토큰 발급 (core/dependencies.py 함수 사용)
    # sub는 JWT 표준 subject claim (여기서는 user id)
    access_token = create_jwt(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}

# 3. 내 정보 조회
@router.get("/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user

# 4. 내 정보 수정
@router.put("/me", response_model=UserResponse)
async def update_my_info(
    user_data: UserUpdate, 
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    update_data = {}
    if user_data.nickname:
        update_data["nickname"] = user_data.nickname
    
    return await update_user(db, current_user, update_data)

# 5. 비밀번호 변경
@router.put("/me/password")
async def change_my_password(
    pw_data: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    # 현재 비밀번호 확인
    if not verify_password(pw_data.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )
    
    # 새 비밀번호 해싱 및 업데이트
    new_hashed_pw = hash_password(pw_data.new_password)
    current_user.hashed_password = new_hashed_pw
    
    await db.commit()
    return {"message": "Password updated successfully"}

# 6. 회원 탈퇴
@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_account(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    await delete_user(db, current_user)
    return None