from jose import JWTError, jwt
from datetime import datetime, timedelta
from passlib.context import CryptContext
from core.config import get_config

config = get_config()

# 비밀 키 및 알고리즘 설정
SECRET_KEY = config.jwt_secret_key
ALGORITHM = "HS256"
EXPIRE_MINUTES = config.jwt_token_expire_minutes

def create_jwt(data: dict):
    # 만료 시간 설정
    expire = datetime.utcnow() + timedelta(minutes=EXPIRE_MINUTES)
    data.update({"exp": expire})
    # JWT 생성
    token = jwt.encode(data, SECRET_KEY, algorithm=ALGORITHM)
    return token

def verify_jwt(token: str):
    try:
        # JWT 디코드 및 검증
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
    
# CryptContext 설정
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 패스워드 해싱
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

# 패스워드 검증
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)