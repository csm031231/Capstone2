import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from User.user_router import router as user_router
from Vision.vision_router import router as vision_router

routers = []
routers.append(user_router)
routers.append(vision_router)

app = FastAPI(
    title="Travel Itinerary Service",
    description="사진 분석 기반 여행지 추천 및 일정 생성 서비스",
    version="1.0.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)


for router in routers:
    app.include_router(router=router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)