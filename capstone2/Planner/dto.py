from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict, Any
from datetime import date, time


# ==================== 일정 생성 요청 DTOs ====================

class GenerateRequest(BaseModel):
    """AI 일정 생성 요청"""
    title: str = Field(..., min_length=1, max_length=100, description="여행 제목")
    region: str = Field(..., description="지역 (필수)")
    start_date: date = Field(..., description="시작일")
    end_date: date = Field(..., description="종료일")

    # 선택적 조건
    must_visit_places: List[int] = Field(
        default=[],
        description="필수 포함 장소 ID"
    )
    exclude_places: List[int] = Field(
        default=[],
        description="제외할 장소 ID"
    )
    themes: List[str] = Field(
        default=[],
        description="테마 오버라이드 (없으면 선호도 사용)"
    )
    max_places_per_day: int = Field(
        default=5,
        ge=2,
        le=10,
        description="하루 최대 장소 수"
    )

    # 시작/종료 위치
    start_location: Optional[Dict[str, float]] = Field(
        None,
        description="시작 위치 {'lat': 35.1, 'lng': 129.0}"
    )
    end_location: Optional[Dict[str, float]] = Field(
        None,
        description="종료 위치"
    )


# ==================== 생성 결과 DTOs ====================

class GeneratedItinerary(BaseModel):
    """생성된 일정 항목"""
    place_id: int
    place_name: str
    place_category: Optional[str] = None
    place_address: Optional[str] = None
    latitude: float
    longitude: float
    image_url: Optional[str] = None
    tags: Optional[List[str]] = None

    day_number: int
    order_index: int
    suggested_arrival_time: Optional[time] = None
    suggested_stay_duration: int = Field(description="분 단위")

    travel_time_from_prev: Optional[int] = Field(None, description="분 단위")
    transport_mode: Optional[str] = None

    selection_reason: str = Field(description="AI 추천 이유")


class DaySummary(BaseModel):
    """일차별 요약"""
    day_number: int
    theme: str
    itineraries: List[GeneratedItinerary]
    total_places: int
    total_travel_time: int
    summary: str


class GenerateResponse(BaseModel):
    """AI 일정 생성 응답"""
    trip_id: int
    title: str
    region: str
    start_date: date
    end_date: date

    days: List[DaySummary]

    # 메타 정보
    total_days: int
    total_places: int
    total_travel_time: int
    optimization_score: float = Field(description="동선 최적화 점수 (0-1)")

    # AI 요약
    trip_summary: str
    generation_method: str = "ai"


# ==================== 채팅 DTOs ====================

class ChatMessage(BaseModel):
    """대화 메시지"""
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    """대화형 수정 요청"""
    session_id: Optional[int] = Field(None, description="세션 ID (없으면 새 세션)")
    trip_id: int = Field(..., description="수정할 여행 ID")
    message: str = Field(..., description="사용자 입력")


class ChangeItem(BaseModel):
    """변경 항목"""
    action: Literal["add", "remove", "replace", "reorder", "modify"]
    details: Dict[str, Any]


class ChatResponse(BaseModel):
    """대화형 수정 응답"""
    session_id: int
    response: str = Field(description="AI 응답 메시지")

    # 변경 사항
    changes_made: Optional[List[ChangeItem]] = None
    updated_trip: Optional[Dict] = None

    # 확인 필요 여부
    needs_confirmation: bool = False
    confirmation_message: Optional[str] = None


class ChatHistoryResponse(BaseModel):
    """대화 히스토리 응답"""
    session_id: int
    trip_id: Optional[int]
    messages: List[ChatMessage]
    current_state: Optional[str]


# ==================== 최적화 요청 DTOs ====================

class OptimizeRequest(BaseModel):
    """동선 최적화 요청"""
    trip_id: int
    start_location: Optional[Dict[str, float]] = None
    end_location: Optional[Dict[str, float]] = None
