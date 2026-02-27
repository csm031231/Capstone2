import logging
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from core.config import get_config
from core.database import init_db

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = get_config()
    init_db(config)
    logger.info("서버 시작. 데이터 수집은 /data/collect/bulk API를 통해 수동으로 실행하세요.")
    yield
    logger.info("서버 종료")


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
