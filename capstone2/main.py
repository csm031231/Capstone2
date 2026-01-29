import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 1. 기존 라우터 임포트
from User.user_router import router as user_router
from Vision.vision_router import router as vision_router
# [추가] 새로 만든 Place 라우터 가져오기
from Place.router import router as place_router 

routers = []
routers.append(user_router)
routers.append(vision_router)
# [추가] 리스트에 Place 라우터 넣기
routers.append(place_router) 

app = FastAPI(
    title="Travel Itinerary Service",
    description="사진 분석 기반 여행지 추천 및 일정 생성 서비스",
    version="1.0.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

# 여기서 반복문 돌면서 다 등록됨
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