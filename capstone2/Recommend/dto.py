from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date, time


# ==================== 선호도 DTOs ====================

class PreferenceSurvey(BaseModel):
    """선호도 설문 요청"""
    # 카테고리 선호도 (1-5점)
    category_ratings: dict = Field(
        ...,
        description="카테고리별 선호도 점수 (1-5)",
        example={"관광지": 5, "카페": 3, "맛집": 4, "자연": 5, "역사": 2}
    )

    # 선호 테마 (복수 선택)
    preferred_themes: List[str] = Field(
        ...,
        description="선호하는 테마/분위기",
        example=["힐링", "사진명소", "액티비티"]
    )

    # 여행 스타일
    travel_pace: str = Field(
        default="moderate",
        description="여행 페이스 (relaxed, moderate, packed)"
    )
    budget_level: str = Field(
        default="medium",
        description="예산 수준 (low, medium, high)"
    )

    # 시간 선호
    preferred_start_time: time = Field(
        default=time(9, 0),
        description="하루 시작 시간"
    )
    preferred_end_time: time = Field(
        default=time(21, 0),
        description="하루 종료 시간"
    )


class PreferenceResponse(BaseModel):
    """선호도 응답"""
    id: int
    user_id: int
    category_weights: Optional[dict] = None
    preferred_themes: Optional[List[str]] = None
    travel_pace: Optional[str] = None
    budget_level: Optional[str] = None
    preferred_start_time: Optional[time] = None
    preferred_end_time: Optional[time] = None

    class Config:
        from_attributes = True


# ==================== 추천 조건 DTOs ====================

class RecommendCondition(BaseModel):
    """조건 기반 추천 요청"""
    region: Optional[str] = Field(None, description="지역 (부산, 제주 등)")
    themes: List[str] = Field(default=[], description="원하는 테마")
    categories: List[str] = Field(default=[], description="원하는 카테고리")
    budget_level: Optional[str] = Field(None, description="예산 수준")
    travel_date: Optional[date] = Field(None, description="여행 날짜 (휴무일 필터)")
    exclude_places: List[int] = Field(default=[], description="제외할 장소 ID")
    top_k: int = Field(default=10, ge=1, le=50, description="추천 개수")


class HybridRecommendRequest(BaseModel):
    """이미지 + 조건 통합 추천 요청"""
    image_path: str = Field(..., description="분석할 이미지 경로")
    condition: Optional[RecommendCondition] = Field(None, description="추가 조건")
    top_k: int = Field(default=10, ge=1, le=50)


# ==================== 추천 결과 DTOs ====================

class RecommendedPlaceDetail(BaseModel):
    """추천 여행지 상세"""
    place_id: int
    name: str
    category: Optional[str] = None
    address: Optional[str] = None
    latitude: float
    longitude: float
    image_url: Optional[str] = None
    tags: Optional[List[str]] = None
    description: Optional[str] = None
    operating_hours: Optional[str] = None
    closed_days: Optional[str] = None
    fee_info: Optional[str] = None

    # 점수
    relevance_score: float = Field(description="조건 부합도")
    preference_score: float = Field(description="선호도 반영 점수")
    final_score: float = Field(description="최종 점수")

    # 추천 이유
    match_reasons: List[str] = Field(default=[], description="매칭 이유")


class ConditionRecommendResponse(BaseModel):
    """조건 기반 추천 응답"""
    success: bool
    places: List[RecommendedPlaceDetail]
    total_count: int
    applied_filters: dict = Field(description="적용된 필터 요약")
    message: str


class HybridRecommendResponse(BaseModel):
    """이미지 + 조건 통합 추천 응답"""
    success: bool
    image_analysis: Optional[dict] = Field(None, description="이미지 분석 결과")
    places: List[RecommendedPlaceDetail]
    total_count: int
    message: str
