# Place/router.py
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import Optional
from pydantic import BaseModel

from core.database import provide_session
from core.models import Place, User
from services.kakao_service import search_places, get_route_info
from User.user_router import get_current_user

router = APIRouter(
    prefix="/places",
    tags=["places"]
)


class CustomPlaceCreate(BaseModel):
    name: str
    address: Optional[str] = None
    category: Optional[str] = None
    memo: Optional[str] = None


@router.get("/search")
async def search_kakao_places(keyword: str):
    """카카오 API 장소 검색 (프론트 지도 표시용)"""
    return await search_places(keyword)


@router.get("/search/db")
async def search_db_places(
    keyword: str,
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(provide_session)
):
    """
    DB 장소 검색 (이름/주소 키워드 매칭, 인기도순 정렬)

    수집된 Place 데이터에서 검색하며 태그, 설명, 인기도 등 풍부한 정보 포함
    """
    query = select(Place).where(
        or_(
            Place.name.contains(keyword),
            Place.address.contains(keyword),
        )
    ).order_by(Place.readcount.desc().nullslast()).limit(limit)

    result = await db.execute(query)
    db_places = result.scalars().all()

    return {
        "success": True,
        "keyword": keyword,
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
                "description": p.description,
                "operating_hours": p.operating_hours,
                "closed_days": p.closed_days,
                "fee_info": p.fee_info,
                "readcount": p.readcount,
                "tel": p.tel,
            }
            for p in db_places
        ],
        "total": len(db_places),
    }


@router.get("/route")
async def check_route(ox: float, oy: float, dx: float, dy: float):
    """카카오 경로 계산 (좌표 → 이동시간/거리)"""
    return await get_route_info(ox, oy, dx, dy)


@router.get("/search/tour")
async def search_tour_and_save(
    keyword: str,
    region: Optional[str] = Query(None, description="지역 필터 (예: 대구, 서울)"),
    db: AsyncSession = Depends(provide_session),
    current_user: User = Depends(get_current_user),
):
    """
    TourAPI 키워드 검색 → DB 자동 저장 → place_id 포함 결과 반환

    일정에 추가할 장소를 이름으로 검색할 때 사용.
    DB에 없는 장소는 TourAPI에서 가져와 자동 저장됩니다.
    """
    from DataCollector.collector_service import DataCollectorService
    collector = DataCollectorService()
    await collector.collect_by_keyword(
        db, keyword=keyword, area_name=region, max_items=10, enhance_with_wiki=False
    )

    # DB에서 검색 결과 조회 (방금 저장된 것 포함)
    query = select(Place).where(
        or_(Place.name.contains(keyword), Place.address.contains(keyword))
    )
    if region:
        query = query.where(Place.address.like(f"{region}%"))
    query = query.order_by(Place.readcount.desc().nullslast()).limit(10)

    result = await db.execute(query)
    places = result.scalars().all()

    return {
        "success": True,
        "keyword": keyword,
        "places": [
            {
                "place_id": p.id,
                "name": p.name,
                "category": p.category,
                "address": p.address,
                "latitude": p.latitude,
                "longitude": p.longitude,
                "image_url": p.image_url,
                "tags": p.tags,
                "operating_hours": p.operating_hours,
                "closed_days": p.closed_days,
                "tel": p.tel,
            }
            for p in places
        ],
        "total": len(places),
    }


@router.post("/custom")
async def add_custom_place(
    data: CustomPlaceCreate,
    db: AsyncSession = Depends(provide_session),
    current_user: User = Depends(get_current_user),
):
    """
    사용자 직접 장소 추가

    상호명(+주소)을 입력하면 카카오 검색으로 좌표를 자동 변환하여 DB에 저장합니다.
    이미 같은 이름+주소로 저장된 장소가 있으면 기존 place_id를 반환합니다.
    """
    # 카카오 키워드 검색으로 좌표 확보
    search_query = f"{data.address} {data.name}" if data.address else data.name
    kakao_results = await search_places(search_query, size=1)

    if not kakao_results:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="장소를 찾을 수 없습니다. 상호명이나 주소를 더 정확히 입력해주세요."
        )

    kakao = kakao_results[0]
    lat = float(kakao["y"])
    lng = float(kakao["x"])
    address = (
        data.address
        or kakao.get("road_address_name")
        or kakao.get("address_name", "")
    )
    category = data.category or kakao.get("category_name", "기타")

    # 중복 확인 (같은 이름 + 주소)
    existing_result = await db.execute(
        select(Place).where(Place.name == data.name, Place.address == address)
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        return {
            "place_id": existing.id,
            "name": existing.name,
            "address": existing.address,
            "latitude": existing.latitude,
            "longitude": existing.longitude,
            "already_existed": True,
        }

    # 새 장소 저장
    place = Place(
        name=data.name,
        category=category,
        address=address,
        latitude=lat,
        longitude=lng,
    )
    db.add(place)
    await db.commit()
    await db.refresh(place)

    return {
        "place_id": place.id,
        "name": place.name,
        "address": place.address,
        "latitude": place.latitude,
        "longitude": place.longitude,
        "already_existed": False,
    }

@router.get("/{place_id}")
async def get_place_detail(
    place_id: int,
    db: AsyncSession = Depends(provide_session)
):
    """
    장소 상세 조회
    
    place_id로 장소 상세 정보 반환
    인증 불필요
    """
    from sqlalchemy import select
    
    result = await db.execute(select(Place).where(Place.id == place_id))
    place = result.scalar_one_or_none()
    
    if not place:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="장소를 찾을 수 없습니다"
        )
    
    return {
        "success": True,
        "place": {
            "id": place.id,
            "name": place.name,
            "category": place.category,
            "address": place.address,
            "latitude": place.latitude,
            "longitude": place.longitude,
            "description": place.description,
            "image_url": place.image_url,
            "tags": place.tags,
            "operating_hours": place.operating_hours,
            "closed_days": place.closed_days,
            "fee_info": place.fee_info,
            "tel": place.tel,
            "homepage": place.homepage,
            "is_festival": place.is_festival,
            "event_start_date": place.event_start_date,
            "event_end_date": place.event_end_date,
            "readcount": place.readcount,
        }
    }