from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date, time


# ==================== Trip DTOs ====================

class TripCreate(BaseModel):
    """여행 생성 요청"""
    title: str = Field(..., min_length=1, max_length=100, description="여행 제목")
    start_date: date = Field(..., description="여행 시작일")
    end_date: date = Field(..., description="여행 종료일")
    region: Optional[str] = Field(None, description="지역 (부산, 제주 등)")
    conditions: Optional[dict] = Field(None, description="추가 조건")


class TripUpdate(BaseModel):
    """여행 수정 요청"""
    title: Optional[str] = Field(None, min_length=1, max_length=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    region: Optional[str] = None
    conditions: Optional[dict] = None


# ==================== Itinerary DTOs ====================

class ItineraryCreate(BaseModel):
    """일정 항목 생성"""
    place_id: int = Field(..., description="장소 ID")
    day_number: int = Field(..., ge=1, description="여행 일차 (1부터 시작)")
    order_index: int = Field(..., ge=1, description="방문 순서 (1부터 시작)")
    arrival_time: Optional[time] = Field(None, description="예상 도착 시간")
    stay_duration: Optional[int] = Field(None, ge=10, le=480, description="체류 시간 (분)")
    memo: Optional[str] = Field(None, description="사용자 메모")
    transport_mode: Optional[str] = Field(None, description="이동 수단 (walk, car, public)")


class ItineraryUpdate(BaseModel):
    """일정 항목 수정"""
    place_id: Optional[int] = None
    day_number: Optional[int] = Field(None, ge=1)
    order_index: Optional[int] = Field(None, ge=1)
    arrival_time: Optional[time] = None
    stay_duration: Optional[int] = Field(None, ge=10, le=480)
    memo: Optional[str] = None
    transport_mode: Optional[str] = None


class ItineraryReorderItem(BaseModel):
    """일정 순서 변경 항목"""
    id: int = Field(..., description="일정 항목 ID")
    day_number: int = Field(..., ge=1, description="새 일차")
    order_index: int = Field(..., ge=1, description="새 순서")


class ItineraryReorder(BaseModel):
    """일정 순서 일괄 변경"""
    items: List[ItineraryReorderItem]


# ==================== Response DTOs ====================

class PlaceInfo(BaseModel):
    """장소 정보 (응답용)"""
    id: int
    name: str
    category: Optional[str] = None
    address: Optional[str] = None
    latitude: float
    longitude: float
    image_url: Optional[str] = None
    tags: Optional[List[str]] = None
    operating_hours: Optional[str] = None
    closed_days: Optional[str] = None

    class Config:
        from_attributes = True


class ItineraryResponse(BaseModel):
    """일정 항목 응답"""
    id: int
    place_id: int
    place: PlaceInfo
    day_number: int
    order_index: int
    arrival_time: Optional[time] = None
    stay_duration: Optional[int] = None
    memo: Optional[str] = None
    travel_time_from_prev: Optional[int] = None
    transport_mode: Optional[str] = None

    class Config:
        from_attributes = True


class TripResponse(BaseModel):
    """여행 응답"""
    id: int
    title: str
    start_date: date
    end_date: date
    region: Optional[str] = None
    conditions: Optional[dict] = None
    generation_method: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class TripDetailResponse(BaseModel):
    """여행 상세 응답 (일정 포함)"""
    id: int
    title: str
    start_date: date
    end_date: date
    region: Optional[str] = None
    conditions: Optional[dict] = None
    generation_method: str
    total_days: int
    itineraries: List[ItineraryResponse] = []
    itineraries_by_day: Optional[dict] = None  # {1: [...], 2: [...]}

    class Config:
        from_attributes = True


class TripListResponse(BaseModel):
    """여행 목록 응답"""
    trips: List[TripResponse]
    total: int
