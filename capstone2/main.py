import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

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
from Festival.router import router as festival_router
from Board.router import router as board_router

logger = logging.getLogger(__name__)

# 수집 대상 지역 및 설정
DEFAULT_COLLECT_AREAS = [
    "서울", "부산", "제주", "강원", "경북",
    "전남", "경남", "전북", "인천", "경기"
]
DEFAULT_MAX_PER_TYPE = 300  # 지역별·타입별 최대 수집 개수


async def _collect_all_areas():
    """전체 지역 데이터 수집 (신규 장소만 추가, 중복 스킵)"""
    from DataCollector.collector_service import get_collector_service

    logger.info(f"데이터 수집 시작: {DEFAULT_COLLECT_AREAS}")
    collector = get_collector_service()
    total = 0

    for area in DEFAULT_COLLECT_AREAS:
        try:
            async with database.DBSessionLocal() as session:
                result = await collector.collect_places_by_area(
                    db=session,
                    area_name=area,
                    max_items_per_type=DEFAULT_MAX_PER_TYPE,
                    enhance_with_wiki=True
                )
                collected = result.get("collected", 0)
                total += collected
                logger.info(f"  [{area}] 완료 - 신규: {collected}개, 스킵: {result.get('skipped', 0)}개")
        except Exception as e:
            logger.error(f"  [{area}] 수집 실패: {e}")

    logger.info(f"데이터 수집 완료. 총 신규 {total}개 추가됨.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = get_config()
    init_db(config)

    # 서버 시작 시 데이터 수집 (비어있으면 전체, 있으면 신규만)
    try:
        await _collect_all_areas()
    except Exception as e:
        logger.error(f"초기 데이터 수집 중 오류 (서버는 정상 시작됩니다): {e}")

    # 매일 새벽 3시 자동 갱신 스케줄러
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _collect_all_areas,
        trigger=CronTrigger(hour=3, minute=0),
        id="daily_data_refresh",
        name="일간 장소 데이터 갱신",
        misfire_grace_time=3600
    )
    scheduler.start()
    logger.info("스케줄러 시작: 매일 03:00 자동 데이터 갱신")

    yield

    scheduler.shutdown()
    logger.info("스케줄러 종료")

# 라우터 리스트
routers = []
routers.append(user_router)
routers.append(vision_router)
routers.append(place_router)
routers.append(trip_router)
routers.append(planner_router)
routers.append(recommend_router)
routers.append(data_router)
routers.append(festival_router)
routers.append(board_router)

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

