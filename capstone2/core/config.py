# core/config.py
import os
from pydantic_settings import BaseSettings

#참고로 나 지금 DB가 계속 설치가 안돼서 일단 임의로 아무거나 적어놓은거임 설치 되면 바로 수정할게
class Settings(BaseSettings):
    # 데이터베이스 설정 (database.py에서 사용)
    postgresql_endpoint: str = "localhost"
    postgresql_port: str = "5432"
    postgresql_table: str = "travel_capstone" 
    postgresql_user: str = "postgres"
    postgresql_password: str = "1234"

    # JWT 설정 (dependencies.py에서 사용)
    jwt_secret_key: str = "super_secret_key_change_this" 
    jwt_expire_minutes: int = 60 * 24 

    class Config:
        env_file = ".env"  

def get_config():
    return Settings()