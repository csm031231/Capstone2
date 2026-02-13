import logging
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from typing import List, Optional

from core.database import provide_session
from core.models import User, Place
from User.user_router import get_current_user
from DataCollector.collector_service import get_collector_service
from DataCollector.tour_api_service import get_tour_api_service

logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/data",
    tags=["data-collector"]
)


# ==================== DTO ====================

class CollectByAreaRequest(BaseModel):
    """지역별 데이터 수집 요청"""
    area_name: str = Field(..., description="지역명 (서울, 부산, 제주 등)")
    content_types: Optional[List[str]] = Field(
        None,
        description="수집할 타입 (관광지, 문화시설, 맛집 등)"
    )
    max_items: int = Field(default=100, ge=10, le=500)
    enhance_with_wiki: bool = Field(default=True, description="Wikipedia로 설명 보강")


class CollectByKeywordRequest(BaseModel):
    """키워드 검색 데이터 수집 요청"""
    keyword: str = Field(..., min_length=1)
    area_name: Optional[str] = None
    max_items: int = Field(default=50, ge=10, le=200)
    enhance_with_wiki: bool = True


# ==================== API ====================

@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(provide_session)
):
    """
    현재 수집된 데이터 통계

    인증 불필요
    """
    collector = get_collector_service()
    return await collector.get_collection_stats(db)


@router.get("/areas")
async def get_available_areas():
    """사용 가능한 지역 코드 목록"""
    tour_api = get_tour_api_service()
    return {
        "areas": list(tour_api.AREA_CODE.keys()),
        "content_types": list(tour_api.CONTENT_TYPE.keys())
    }


@router.post("/collect/area")
async def collect_by_area(
    request: CollectByAreaRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    지역별 관광지 데이터 수집

    - 한국관광공사 TourAPI에서 데이터 가져오기
    - Wikipedia로 부족한 설명 보강
    - Place 테이블에 저장

    지원 지역: 서울, 부산, 제주, 강원, 경기, 인천, 대구, 광주, 대전, 울산, 세종,
              경북, 경남, 전북, 전남, 충북, 충남
    """
    collector = get_collector_service()

    result = await collector.collect_places_by_area(
        db=db,
        area_name=request.area_name,
        content_types=request.content_types,
        max_items=request.max_items,
        enhance_with_wiki=request.enhance_with_wiki
    )

    return result


@router.post("/collect/keyword")
async def collect_by_keyword(
    request: CollectByKeywordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    키워드로 관광지 검색 및 저장

    예: "해운대", "경복궁", "한라산"
    """
    collector = get_collector_service()

    result = await collector.collect_by_keyword(
        db=db,
        keyword=request.keyword,
        area_name=request.area_name,
        max_items=request.max_items,
        enhance_with_wiki=request.enhance_with_wiki
    )

    return result


@router.post("/collect/bulk")
async def collect_bulk(
    areas: List[str] = ["부산", "제주", "강원", "경주"],
    max_per_area: int = 100,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    여러 지역 일괄 수집

    기본: 부산, 제주, 강원, 경주
    """
    collector = get_collector_service()
    results = []

    for area in areas:
        result = await collector.collect_places_by_area(
            db=db,
            area_name=area,
            max_items=max_per_area,
            enhance_with_wiki=True
        )
        results.append(result)

    total_collected = sum(r.get("collected", 0) for r in results)

    return {
        "success": True,
        "total_collected": total_collected,
        "by_area": results
    }


# ==================== FAISS 인덱싱 ====================

@router.post("/index/build")
async def build_faiss_index(
    region: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    FAISS 벡터 인덱스 구축

    DB의 Place 중 image_url이 있는 장소의 이미지를 CLIP으로 벡터화하여
    FAISS 인덱스에 추가합니다. Vision 추천 기능에 필요합니다.

    - region: 특정 지역만 인덱싱 (없으면 전체)
    - 이미 인덱싱된 장소는 건너뜁니다
    """
    import httpx
    from PIL import Image
    from io import BytesIO
    from Vision.clip_service import get_clip_service
    from Vision.faiss_index import get_faiss_index, PlaceVector

    clip = get_clip_service()
    faiss_index = get_faiss_index()

    # 이미 인덱싱된 place_id 수집
    indexed_ids = {m["place_id"] for m in faiss_index.metadata}

    # DB에서 이미지 있는 장소 조회
    query = select(Place).where(Place.image_url.isnot(None))
    if region:
        query = query.where(Place.address.contains(region))

    result = await db.execute(query)
    places = result.scalars().all()

    indexed = 0
    skipped = 0
    errors = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for place in places:
            if place.id in indexed_ids:
                skipped += 1
                continue

            try:
                # 이미지 다운로드
                resp = await client.get(place.image_url)
                resp.raise_for_status()
                img = Image.open(BytesIO(resp.content)).convert("RGB")

                # CLIP 벡터 추출
                vector = clip.get_image_embedding(img)

                # FAISS에 추가
                place_vector = PlaceVector(
                    place_id=place.id,
                    name=place.name,
                    image_url=place.image_url,
                    tags=place.tags or [],
                    category=place.category or "기타",
                    address=place.address or "",
                    latitude=place.latitude,
                    longitude=place.longitude
                )
                faiss_index.add_place(place_vector, vector)
                indexed += 1

            except Exception as e:
                errors.append(f"{place.name}: {str(e)[:50]}")
                continue

    # 인덱스 저장
    if indexed > 0:
        faiss_index.save()

    return {
        "success": True,
        "indexed": indexed,
        "skipped": skipped,
        "errors": len(errors),
        "error_details": errors[:10],
        "total_in_index": faiss_index.get_total_count(),
        "message": f"{indexed}개 장소를 인덱싱했습니다."
    }


@router.get("/index/stats")
async def get_index_stats():
    """FAISS 인덱스 현황 조회"""
    from Vision.faiss_index import get_faiss_index

    faiss_index = get_faiss_index()

    return {
        "total_vectors": faiss_index.get_total_count(),
        "dimension": faiss_index.dimension,
        "index_path": faiss_index.index_path
    }


@router.get("/places")
async def list_places(
    region: Optional[str] = None,
    category: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(provide_session)
):
    """
    수집된 장소 목록 조회

    인증 불필요
    """
    from sqlalchemy import select
    from core.models import Place

    query = select(Place)

    if region:
        query = query.where(Place.address.contains(region))

    if category:
        query = query.where(Place.category == category)

    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    places = result.scalars().all()

    return {
        "places": [
            {
                "id": p.id,
                "name": p.name,
                "category": p.category,
                "address": p.address,
                "latitude": p.latitude,
                "longitude": p.longitude,
                "image_url": p.image_url,
                "tags": p.tags,
                "operating_hours": p.operating_hours,
                "closed_days": p.closed_days,
                "description": p.description[:200] + "..." if p.description and len(p.description) > 200 else p.description
            }
            for p in places
        ],
        "count": len(places)
    }
