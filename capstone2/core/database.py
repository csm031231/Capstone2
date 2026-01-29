from typing import Optional
# import ssl  <-- 로컬 테스트 시 보통 불필요하여 주석 처리
from sqlalchemy.engine import Engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

# 전역 변수 초기화
DBSessionLocal: Optional[sessionmaker] = None
db_engine: Optional[Engine] = None

def init_db(config) -> None:
    global DBSessionLocal, db_engine
    
    # config 객체에서 속성 가져오기
    # (config.py의 Settings 클래스 속성명과 정확히 일치해야 합니다)
    postgres_endpoint = config.postgresql_endpoint
    postgres_port = config.postgresql_port
    postgres_table = config.postgresql_table
    postgres_user = config.postgresql_user
    postgres_password = config.postgresql_password
    
    # DB 접속 URL 생성
    db_url = (
        "postgresql+asyncpg://"
        + f"{postgres_user}:{postgres_password}"
        + f"@{postgres_endpoint}:{postgres_port}/{postgres_table}"
    )
    
    # --- [수정] SSL 설정 (로컬 환경에서는 보통 에러 원인이 되므로 주석 처리함) ---
    # ssl_context = ssl.create_default_context()
    # ssl_context.check_hostname = False
    # ssl_context.verify_mode = ssl.CERT_NONE
    # connect_args = {"ssl": ssl_context}
    
    # 로컬 개발용 connect_args (비워둠)
    connect_args = {} 
    
    print(f"Connecting to DB at {postgres_endpoint}...") # 연결 시도 로그

    # --- [수정] try-except 제거: 연결 실패 시 서버가 멈추고 에러를 뱉어야 함 ---
    db_engine = create_async_engine(
        db_url,
        connect_args=connect_args,
        echo=True  # 터미널에 SQL 로그 출력 (디버깅용)
    )
    
    DBSessionLocal = sessionmaker(
        bind=db_engine,
        autoflush=False,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    print("Database connection initialized successfully.")


async def provide_session():
    """
    Dependency Injection을 위한 함수
    """
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