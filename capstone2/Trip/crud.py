from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import date

from core.models import Trip, Itinerary, Place, ChatSession
from Trip.dto import TripCreate, TripUpdate, ItineraryCreate, ItineraryUpdate, ItineraryReorderItem


# ==================== Trip CRUD ====================

async def create_trip(
    db: AsyncSession,
    user_id: int,
    data: TripCreate,
    generation_method: str = "manual",
    preference_snapshot: Optional[dict] = None
) -> Trip:
    """여행 생성"""
    trip = Trip(
        user_id=user_id,
        title=data.title,
        start_date=data.start_date,
        end_date=data.end_date,
        region=data.region,
        conditions=data.conditions,
        generation_method=generation_method,
        preference_snapshot=preference_snapshot
    )
    db.add(trip)
    await db.commit()
    await db.refresh(trip)
    return trip


async def get_trip_by_id(
    db: AsyncSession,
    trip_id: int,
    user_id: Optional[int] = None
) -> Optional[Trip]:
    """여행 조회 (일정 포함)"""
    query = select(Trip).options(
        selectinload(Trip.itineraries).selectinload(Itinerary.place)
    ).where(Trip.id == trip_id)

    if user_id is not None:
        query = query.where(Trip.user_id == user_id)

    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_trips_by_user(
    db: AsyncSession,
    user_id: int,
    skip: int = 0,
    limit: int = 20
) -> List[Trip]:
    """사용자의 여행 목록 조회"""
    query = select(Trip).where(
        Trip.user_id == user_id
    ).order_by(Trip.created_at.desc()).offset(skip).limit(limit)

    result = await db.execute(query)
    return result.scalars().all()


async def count_trips_by_user(db: AsyncSession, user_id: int) -> int:
    """사용자의 여행 개수"""
    from sqlalchemy import func
    query = select(func.count()).select_from(Trip).where(Trip.user_id == user_id)
    result = await db.execute(query)
    return result.scalar() or 0


async def update_trip(
    db: AsyncSession,
    trip_id: int,
    user_id: int,
    data: TripUpdate
) -> Optional[Trip]:
    """여행 수정"""
    trip = await get_trip_by_id(db, trip_id, user_id)
    if not trip:
        return None

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(trip, key, value)

    await db.commit()
    await db.refresh(trip)
    return trip


async def delete_trip(db: AsyncSession, trip_id: int, user_id: int) -> bool:
    """여행 삭제 (cascade로 일정도 삭제)"""
    trip = await get_trip_by_id(db, trip_id, user_id)
    if not trip:
        return False

    # chat_sessions의 FK 제약 조건으로 인해 trip 삭제 전 먼저 연결된 세션 삭제
    await db.execute(delete(ChatSession).where(ChatSession.trip_id == trip_id))

    await db.delete(trip)
    await db.commit()
    return True


# ==================== Itinerary CRUD ====================

async def create_itinerary(
    db: AsyncSession,
    trip_id: int,
    data: ItineraryCreate
) -> Itinerary:
    """일정 항목 생성"""
    itinerary = Itinerary(
        trip_id=trip_id,
        place_id=data.place_id,
        day_number=data.day_number,
        order_index=data.order_index,
        arrival_time=data.arrival_time,
        stay_duration=data.stay_duration,
        memo=data.memo,
        transport_mode=data.transport_mode
    )
    db.add(itinerary)
    await db.commit()
    await db.refresh(itinerary)

    # place 정보도 로드
    query = select(Itinerary).options(
        selectinload(Itinerary.place)
    ).where(Itinerary.id == itinerary.id)
    result = await db.execute(query)
    return result.scalar_one()


async def get_itinerary_by_id(
    db: AsyncSession,
    itinerary_id: int
) -> Optional[Itinerary]:
    """일정 항목 조회"""
    query = select(Itinerary).options(
        selectinload(Itinerary.place)
    ).where(Itinerary.id == itinerary_id)

    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_itineraries_by_trip(
    db: AsyncSession,
    trip_id: int
) -> List[Itinerary]:
    """여행의 모든 일정 조회"""
    query = select(Itinerary).options(
        selectinload(Itinerary.place)
    ).where(
        Itinerary.trip_id == trip_id
    ).order_by(Itinerary.day_number, Itinerary.order_index)

    result = await db.execute(query)
    return result.scalars().all()


async def update_itinerary(
    db: AsyncSession,
    itinerary_id: int,
    data: ItineraryUpdate
) -> Optional[Itinerary]:
    """일정 항목 수정"""
    itinerary = await get_itinerary_by_id(db, itinerary_id)
    if not itinerary:
        return None

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(itinerary, key, value)

    await db.commit()
    await db.refresh(itinerary)
    return itinerary


async def delete_itinerary(db: AsyncSession, itinerary_id: int) -> bool:
    """일정 항목 삭제"""
    itinerary = await get_itinerary_by_id(db, itinerary_id)
    if not itinerary:
        return False

    await db.delete(itinerary)
    await db.commit()
    return True


async def reorder_itineraries(
    db: AsyncSession,
    trip_id: int,
    items: List[ItineraryReorderItem]
) -> List[Itinerary]:
    """일정 순서 일괄 변경"""
    for item in items:
        await db.execute(
            update(Itinerary)
            .where(Itinerary.id == item.id, Itinerary.trip_id == trip_id)
            .values(day_number=item.day_number, order_index=item.order_index)
        )

    await db.commit()
    return await get_itineraries_by_trip(db, trip_id)


async def bulk_create_itineraries(
    db: AsyncSession,
    trip_id: int,
    items: List[dict]
) -> List[Itinerary]:
    """일정 일괄 생성 (AI 생성용)"""
    itineraries = []
    for item in items:
        itinerary = Itinerary(
            trip_id=trip_id,
            place_id=item["place_id"],
            day_number=item["day_number"],
            order_index=item["order_index"],
            arrival_time=item.get("arrival_time"),
            stay_duration=item.get("stay_duration"),
            memo=item.get("memo"),
            travel_time_from_prev=item.get("travel_time_from_prev"),
            transport_mode=item.get("transport_mode")
        )
        db.add(itinerary)
        itineraries.append(itinerary)

    await db.commit()

    # 모든 일정 다시 로드 (place 포함)
    return await get_itineraries_by_trip(db, trip_id)


async def clear_itineraries(db: AsyncSession, trip_id: int) -> int:
    """여행의 모든 일정 삭제"""
    result = await db.execute(
        delete(Itinerary).where(Itinerary.trip_id == trip_id)
    )
    await db.commit()
    return result.rowcount


# ==================== Place 조회 헬퍼 ====================

async def get_place_by_id(db: AsyncSession, place_id: int) -> Optional[Place]:
    """장소 조회"""
    result = await db.execute(select(Place).where(Place.id == place_id))
    return result.scalar_one_or_none()


async def validate_place_exists(db: AsyncSession, place_id: int) -> bool:
    """장소 존재 여부 확인"""
    place = await get_place_by_id(db, place_id)
    return place is not None
