from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from collections import defaultdict

from core.database import provide_session
from core.models import User
from User.user_router import get_current_user

from Trip.dto import (
    TripCreate, TripUpdate, TripResponse, TripDetailResponse, TripListResponse,
    ItineraryCreate, ItineraryUpdate, ItineraryReorder, ItineraryResponse, PlaceInfo
)
from Trip import crud

router = APIRouter(
    prefix="/trips",
    tags=["trips"]
)


# ==================== Helper Functions ====================

def build_itinerary_response(itinerary) -> ItineraryResponse:
    """Itinerary 모델을 ItineraryResponse로 변환"""
    place_info = PlaceInfo(
        id=itinerary.place.id,
        name=itinerary.place.name,
        category=itinerary.place.category,
        address=itinerary.place.address,
        latitude=itinerary.place.latitude,
        longitude=itinerary.place.longitude,
        image_url=itinerary.place.image_url,
        tags=itinerary.place.tags,
        operating_hours=itinerary.place.operating_hours,
        closed_days=itinerary.place.closed_days
    )

    return ItineraryResponse(
        id=itinerary.id,
        place_id=itinerary.place_id,
        place=place_info,
        day_number=itinerary.day_number,
        order_index=itinerary.order_index,
        arrival_time=itinerary.arrival_time,
        stay_duration=itinerary.stay_duration,
        memo=itinerary.memo,
        travel_time_from_prev=itinerary.travel_time_from_prev,
        transport_mode=itinerary.transport_mode
    )


def build_trip_detail_response(trip) -> TripDetailResponse:
    """Trip 모델을 TripDetailResponse로 변환"""
    total_days = (trip.end_date - trip.start_date).days + 1

    # 일정 변환
    itineraries = [build_itinerary_response(it) for it in trip.itineraries]

    # 일차별 그룹화
    itineraries_by_day = defaultdict(list)
    for it in itineraries:
        itineraries_by_day[it.day_number].append(it)

    # 각 일차 내에서 순서 정렬
    for day in itineraries_by_day:
        itineraries_by_day[day].sort(key=lambda x: x.order_index)

    return TripDetailResponse(
        id=trip.id,
        title=trip.title,
        start_date=trip.start_date,
        end_date=trip.end_date,
        region=trip.region,
        conditions=trip.conditions,
        generation_method=trip.generation_method or "manual",
        total_days=total_days,
        itineraries=itineraries,
        itineraries_by_day=dict(itineraries_by_day)
    )


# ==================== Trip Endpoints ====================

@router.post("", response_model=TripDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_trip(
    data: TripCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """여행 생성"""
    # 날짜 유효성 검사
    if data.end_date < data.start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="종료일은 시작일보다 이후여야 합니다"
        )

    trip = await crud.create_trip(db, current_user.id, data)
    return build_trip_detail_response(trip)


@router.get("", response_model=TripListResponse)
async def get_my_trips(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """내 여행 목록 조회"""
    trips = await crud.get_trips_by_user(db, current_user.id, skip, limit)
    total = await crud.count_trips_by_user(db, current_user.id)

    trip_responses = [
        TripResponse(
            id=trip.id,
            title=trip.title,
            start_date=trip.start_date,
            end_date=trip.end_date,
            region=trip.region,
            conditions=trip.conditions,
            generation_method=trip.generation_method or "manual",
            created_at=trip.created_at.isoformat() if trip.created_at else None,
            updated_at=trip.updated_at.isoformat() if trip.updated_at else None
        )
        for trip in trips
    ]

    return TripListResponse(trips=trip_responses, total=total)


@router.get("/{trip_id}", response_model=TripDetailResponse)
async def get_trip(
    trip_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """여행 상세 조회 (일정 포함)"""
    trip = await crud.get_trip_by_id(db, trip_id, current_user.id)
    if not trip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="여행을 찾을 수 없습니다"
        )

    return build_trip_detail_response(trip)


@router.put("/{trip_id}", response_model=TripDetailResponse)
async def update_trip(
    trip_id: int,
    data: TripUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """여행 정보 수정"""
    # 날짜 유효성 검사
    if data.start_date and data.end_date and data.end_date < data.start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="종료일은 시작일보다 이후여야 합니다"
        )

    trip = await crud.update_trip(db, trip_id, current_user.id, data)
    if not trip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="여행을 찾을 수 없습니다"
        )

    return build_trip_detail_response(trip)


@router.delete("/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trip(
    trip_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """여행 삭제 (일정도 함께 삭제)"""
    success = await crud.delete_trip(db, trip_id, current_user.id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="여행을 찾을 수 없습니다"
        )
    return None


# ==================== Itinerary Endpoints ====================

@router.post("/{trip_id}/itineraries", response_model=ItineraryResponse, status_code=status.HTTP_201_CREATED)
async def add_itinerary(
    trip_id: int,
    data: ItineraryCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """일정 항목 추가"""
    # 여행 소유권 확인
    trip = await crud.get_trip_by_id(db, trip_id, current_user.id)
    if not trip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="여행을 찾을 수 없습니다"
        )

    # 장소 존재 확인
    if not await crud.validate_place_exists(db, data.place_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="장소를 찾을 수 없습니다"
        )

    # 일차 유효성 검사
    total_days = (trip.end_date - trip.start_date).days + 1
    if data.day_number < 1 or data.day_number > total_days:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"일차는 1~{total_days} 범위여야 합니다"
        )

    itinerary = await crud.create_itinerary(db, trip_id, data)
    return build_itinerary_response(itinerary)


@router.put("/{trip_id}/itineraries/{itinerary_id}", response_model=ItineraryResponse)
async def update_itinerary(
    trip_id: int,
    itinerary_id: int,
    data: ItineraryUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """일정 항목 수정"""
    # 여행 소유권 확인
    trip = await crud.get_trip_by_id(db, trip_id, current_user.id)
    if not trip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="여행을 찾을 수 없습니다"
        )

    # 일정 확인
    itinerary = await crud.get_itinerary_by_id(db, itinerary_id)
    if not itinerary or itinerary.trip_id != trip_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="일정을 찾을 수 없습니다"
        )

    # 장소 변경 시 존재 확인
    if data.place_id and not await crud.validate_place_exists(db, data.place_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="장소를 찾을 수 없습니다"
        )

    # 일차 변경 시 유효성 검사
    if data.day_number is not None:
        total_days = (trip.end_date - trip.start_date).days + 1
        if data.day_number < 1 or data.day_number > total_days:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"일차는 1~{total_days} 범위여야 합니다"
            )

    updated = await crud.update_itinerary(db, itinerary_id, data)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="일정을 찾을 수 없습니다"
        )
    return build_itinerary_response(updated)


@router.delete("/{trip_id}/itineraries/{itinerary_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_itinerary(
    trip_id: int,
    itinerary_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """일정 항목 삭제"""
    # 여행 소유권 확인
    trip = await crud.get_trip_by_id(db, trip_id, current_user.id)
    if not trip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="여행을 찾을 수 없습니다"
        )

    # 일정 확인
    itinerary = await crud.get_itinerary_by_id(db, itinerary_id)
    if not itinerary or itinerary.trip_id != trip_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="일정을 찾을 수 없습니다"
        )

    await crud.delete_itinerary(db, itinerary_id)
    return None


@router.put("/{trip_id}/itineraries/reorder", response_model=TripDetailResponse)
async def reorder_itineraries(
    trip_id: int,
    data: ItineraryReorder,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """일정 순서 일괄 변경"""
    # 여행 소유권 확인
    trip = await crud.get_trip_by_id(db, trip_id, current_user.id)
    if not trip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="여행을 찾을 수 없습니다"
        )

    # 일차 유효성 검사
    total_days = (trip.end_date - trip.start_date).days + 1
    for item in data.items:
        if item.day_number < 1 or item.day_number > total_days:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"일차는 1~{total_days} 범위여야 합니다"
            )

    await crud.reorder_itineraries(db, trip_id, data.items)

    # 업데이트된 여행 정보 반환
    updated_trip = await crud.get_trip_by_id(db, trip_id, current_user.id)
    return build_trip_detail_response(updated_trip)
