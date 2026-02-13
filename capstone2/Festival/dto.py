from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date


# ==================== 축제 검색 요청 DTOs ====================

class FestivalSearchRequest(BaseModel):
    """축제 검색 요청"""
    region: Optional[str] = Field(None, description="지역명 (서울, 부산, 제주 등)")
    start_date: Optional[date] = Field(None, description="행사 시작일 (이후)")
    end_date: Optional[date] = Field(None, description="행사 종료일 (이전)")
    keyword: Optional[str] = Field(None, description="축제명 검색 키워드")
    max_items: int = Field(default=50, ge=10, le=200, description="최대 결과 수")


# ==================== 축제 응답 DTOs ====================

class FestivalInfo(BaseModel):
    """축제 정보"""
    id: int = Field(description="콘텐츠 ID")
    title: str = Field(description="축제명")
    address: Optional[str] = Field(None, description="주소")
    region: Optional[str] = Field(None, description="지역")
    
    # 날짜 정보
    event_start_date: Optional[str] = Field(None, description="행사 시작일 (YYYYMMDD)")
    event_end_date: Optional[str] = Field(None, description="행사 종료일 (YYYYMMDD)")
    
    # 위치
    latitude: Optional[float] = Field(None, description="위도")
    longitude: Optional[float] = Field(None, description="경도")
    
    # 상세 정보
    description: Optional[str] = Field(None, description="설명")
    image_url: Optional[str] = Field(None, description="대표 이미지")
    tel: Optional[str] = Field(None, description="문의 전화")
    homepage: Optional[str] = Field(None, description="홈페이지")
    
    # 부가 정보
    event_place: Optional[str] = Field(None, description="행사 장소")
    playtime: Optional[str] = Field(None, description="공연 시간")
    program: Optional[str] = Field(None, description="행사 프로그램")
    usetimefestival: Optional[str] = Field(None, description="이용 요금")
    
    # 상태
    is_ongoing: bool = Field(description="현재 진행 중 여부")
    is_upcoming: bool = Field(description="예정된 행사 여부")
    days_until_start: Optional[int] = Field(None, description="시작까지 남은 일수")
    days_until_end: Optional[int] = Field(None, description="종료까지 남은 일수")


class FestivalSearchResponse(BaseModel):
    """축제 검색 응답"""
    success: bool
    festivals: List[FestivalInfo]
    total_count: int
    filters_applied: dict = Field(description="적용된 필터 요약")
    message: str


class FestivalCalendarResponse(BaseModel):
    """축제 캘린더 응답 (월별)"""
    success: bool
    year: int
    month: int
    festivals_by_date: dict = Field(description="날짜별 축제 목록 {YYYYMMDD: [FestivalInfo, ...]}")
    total_count: int