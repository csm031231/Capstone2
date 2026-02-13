import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from core.config import get_config
from core.database import init_db
from core import database

# 라우터 임포트
from User.user_router import router as user_router
from Vision.vision_router import router as vision_router
from Place.router import router as place_router
from Trip.trip_router import router as trip_router
from Planner.planner_router import router as planner_router
from Recommend.recommend_router import router as recommend_router
from DataCollector.collector_router import router as data_router

logger = logging.getLogger(__name__)

# 초기 수집 대상 지역
DEFAULT_COLLECT_AREAS = ["부산", "제주", "강원", "서울", "경주"]
DEFAULT_MAX_PER_AREA = 100


async def _seed_places_if_empty():
    """Place 테이블이 비어있으면 기본 지역 데이터를 자동 수집"""
    from sqlalchemy import select, func
    from core.models import Place
    from DataCollector.collector_service import get_collector_service

    async with database.DBSessionLocal() as session:
        result = await session.execute(select(func.count()).select_from(Place))
        count = result.scalar() or 0

    if count > 0:
        logger.info(f"Place 테이블에 이미 {count}개의 데이터가 있습니다. 초기 수집을 건너뜁니다.")
        return

    logger.info(f"Place 테이블이 비어있습니다. 기본 지역 데이터를 수집합니다: {DEFAULT_COLLECT_AREAS}")
    collector = get_collector_service()

    for area in DEFAULT_COLLECT_AREAS:
        try:
            async with database.DBSessionLocal() as session:
                result = await collector.collect_places_by_area(
                    db=session,
                    area_name=area,
                    max_items=DEFAULT_MAX_PER_AREA,
                    enhance_with_wiki=True
                )
                logger.info(f"  [{area}] 수집 완료: {result.get('collected', 0)}개")
        except Exception as e:
            logger.error(f"  [{area}] 수집 실패: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = get_config()
    init_db(config)

    # Place 테이블이 비어있으면 자동 수집
    try:
        await _seed_places_if_empty()
    except Exception as e:
        logger.error(f"초기 데이터 수집 중 오류 (서버는 정상 시작됩니다): {e}")

    yield

# 라우터 리스트
routers = []
routers.append(user_router)
routers.append(vision_router)
routers.append(place_router)
routers.append(trip_router)
routers.append(planner_router)
routers.append(recommend_router)
routers.append(data_router)

# FastAPI 앱 생성
app = FastAPI(
    lifespan=lifespan,
    title="Travel Itinerary Service",
    description="사진 분석 기반 여행지 추천 및 일정 생성 서비스",
    version="1.0.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

# 라우터 등록
for router in routers:
    app.include_router(router=router)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

