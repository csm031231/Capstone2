import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    postgresql_endpoint: str
    postgresql_port: int
    postgresql_table: str
    postgresql_user: str
    postgresql_password: str

    jwt_secret_key: str
    
    jwt_token_expire_minutes: int = 60 * 24  

    openai_api_key: str = ""
    kakao_map_api_key: str = ""
    tour_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        extra="ignore"  # <-- 이걸 추가해야 정의되지 않은 다른 .env 값들 때문에 에러나는 걸 막아줍니다.
    )

@lru_cache
def get_config():
    return Settings()