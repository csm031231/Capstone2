# Place/router.py
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from core.database import provide_session
from core.models import Place
from services.kakao_service import search_places, get_route_info

router = APIRouter(
    prefix="/places",
    tags=["places"]
)


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
