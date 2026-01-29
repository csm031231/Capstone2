import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from User.user_router import router as user_router
from Vision.vision_router import router as vision_router
from Trip.trip_router import router as trip_router
from Recommend.recommend_router import router as recommend_router
from Planner.planner_router import router as planner_router
from DataCollector.collector_router import router as collector_router

routers = [
    user_router,
    vision_router,
    trip_router,
    recommend_router,
    planner_router,
    collector_router,
]


async def init_data_if_empty():
    """서버 시작 시 데이터가 없으면 자동 수집"""
    from sqlalchemy import select, func
    from core.database import DBSessionLocal
    from core.models import Place
    from DataCollector.collector_service import get_collector_service

    if DBSessionLocal is None:
        print("⚠️ DB 세션이 초기화되지 않았습니다.")
        return

    db = None
    try:
        db = DBSessionLocal()
        # Place 테이블 데이터 확인
        result = await db.execute(select(func.count()).select_from(Place))
        count = result.scalar() or 0

        if count == 0:
            print("📍 Place 데이터가 없습니다. 자동 수집을 시작합니다...")
            collector = get_collector_service()

            # 기본 지역 데이터 수집 (부산, 제주)
            default_areas = ["부산", "제주"]
            for area in default_areas:
                print(f"   - {area} 데이터 수집 중...")
                collect_result = await collector.collect_places_by_area(
                    db=db,
                    area_name=area,
                    content_types=["관광지", "문화시설", "음식점"],
                    max_items=50,
                    enhance_with_wiki=True
                )
                print(f"   - {area}: {collect_result.get('collected', 0)}개 수집 완료")

            print("✅ 초기 데이터 수집 완료!")
        else:
            print(f"📍 Place 데이터 {count}개 존재. 수집 생략.")

    except Exception as e:
        print(f"⚠️ 데이터 초기화 중 오류 (무시하고 진행): {e}")
    finally:
        if db:
            await db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """서버 시작/종료 이벤트"""
    # 시작 시 - DB 초기화
    from core.database import init_db
    from core.config import get_config

    print("🚀 서버 시작...")
    init_db(get_config())

    # 데이터 자동 수집
    await init_data_if_empty()

    yield
    # 종료 시
    print("👋 서버 종료...")


app = FastAPI(
    title="Travel Itinerary Service",
    description="사진 분석 기반 여행지 추천 및 일정 생성 서비스",
    version="1.0.0",
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
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