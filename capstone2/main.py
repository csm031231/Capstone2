import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from core.config import get_config
from core.database import init_db

# 라우터 임포트
from User.user_router import router as user_router
from Vision.vision_router import router as vision_router
from place.router import router as place_router 

@asynccontextmanager
async def lifespan(app: FastAPI):
    config = get_config()
    init_db(config)  
    yield

# 라우터 리스트
routers = []
routers.append(user_router)
routers.append(vision_router)
routers.append(place_router) 

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

