from typing import Optional
import ssl  # 주석 해제
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

DBSessionLocal: Optional[sessionmaker] = None
db_engine: Optional[Engine] = None

def init_db(config) -> None:
    global DBSessionLocal, db_engine
    
    postgres_endpoint = config.postgresql_endpoint
    postgres_port = config.postgresql_port
    postgres_table = config.postgresql_table
    postgres_user = config.postgresql_user
    postgres_password = config.postgresql_password
    
    db_url = (
        "postgresql+asyncpg://"
        + f"{postgres_user}:{postgres_password}"
        + f"@{postgres_endpoint}:{postgres_port}/{postgres_table}"
    )
    
    # AWS RDS SSL 설정 (프로덕션 환경)
    # 로컬 개발 시에는 ssl=False 사용
    import os
    is_production = os.getenv("ENVIRONMENT", "development") == "production"
    
    if is_production:
        # SSL 인증서 검증 활성화
        ssl_context = ssl.create_default_context()
        # RDS 인증서 다운로드 필요 시:
        # ssl_context.load_verify_locations('/path/to/rds-ca-2019-root.pem')
        connect_args = {"ssl": ssl_context}
    else:
        # 로컬 개발: SSL 비활성화
        connect_args = {"ssl": False}
    
    print(f"Connecting to DB at {postgres_endpoint}...")

    db_engine = create_async_engine(
        db_url,
        connect_args=connect_args,
        echo=True,  # 프로덕션에서는 False 권장
        pool_pre_ping=True,  # 연결 체크
        pool_size=5,  # 연결 풀 크기
        max_overflow=10  # 최대 추가 연결
    )
    
    DBSessionLocal = sessionmaker(
        bind=db_engine,
        autoflush=False,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    print("Database connection initialized successfully.")


async def provide_session():
    if DBSessionLocal is None:
        raise ImportError("DB 연결 실패: init_db가 호출되지 않았거나 연결 에러가 발생했습니다.")
    
    async_session = DBSessionLocal()
    
    try:
        yield async_session
    except Exception as e:
        await async_session.rollback()
        raise e
    else:
        await async_session.commit()
    finally:
        await async_session.close()