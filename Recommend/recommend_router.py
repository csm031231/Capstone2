from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import provide_session
from core.models import User
from User.user_router import get_current_user

from Recommend.dto import (
    PreferenceSurvey, PreferenceResponse,
    RecommendCondition, ConditionRecommendResponse
)
from Recommend.preference_service import (
    get_user_preference, save_user_preference
)
from Recommend.recommend_service import get_condition_recommender


router = APIRouter(
    prefix="/recommend",
    tags=["recommend"]
)


# ==================== 선호도 API ====================

@router.post("/preference", response_model=PreferenceResponse)
async def save_preference(
    survey: PreferenceSurvey,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    선호도 설문 저장

    - 카테고리별 선호도 (1-5점)
    - 선호 테마 (복수 선택)
    - 여행 스타일 (relaxed/moderate/packed)
    - 예산 수준 (low/medium/high)
    - 하루 시작/종료 시간
    """
    preference = await save_user_preference(db, current_user.id, survey)

    return PreferenceResponse(
        id=preference.id,
        user_id=preference.user_id,
        category_weights=preference.category_weights,
        preferred_themes=preference.preferred_themes,
        travel_pace=preference.travel_pace,
        budget_level=preference.budget_level,
        preferred_start_time=preference.preferred_start_time,
        preferred_end_time=preference.preferred_end_time
    )


@router.get("/preference", response_model=PreferenceResponse)
async def get_preference(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """내 선호도 조회"""
    preference = await get_user_preference(db, current_user.id)

    if not preference:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="선호도 설정이 없습니다. 먼저 선호도를 설정해주세요."
        )

    return PreferenceResponse(
        id=preference.id,
        user_id=preference.user_id,
        category_weights=preference.category_weights,
        preferred_themes=preference.preferred_themes,
        travel_pace=preference.travel_pace,
        budget_level=preference.budget_level,
        preferred_start_time=preference.preferred_start_time,
        preferred_end_time=preference.preferred_end_time
    )


# ==================== 추천 API ====================

@router.post("/condition", response_model=ConditionRecommendResponse)
async def recommend_by_condition(
    condition: RecommendCondition,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    조건 기반 여행지 추천

    - 지역 (부산, 제주 등)
    - 테마 (힐링, 액티비티, 역사 등)
    - 카테고리 (관광지, 카페, 맛집 등)
    - 예산 수준
    - 여행 날짜 (휴무일 필터)

    선호도가 설정되어 있으면 자동으로 반영됩니다.
    """
    # 사용자 선호도 로드
    preference = await get_user_preference(db, current_user.id)

    # 추천 실행
    recommender = get_condition_recommender()
    places = await recommender.recommend(db, condition, preference)

    # 적용된 필터 요약
    applied_filters = {
        "region": condition.region,
        "themes": condition.themes if condition.themes else None,
        "categories": condition.categories if condition.categories else None,
        "budget_level": condition.budget_level,
        "travel_date": condition.travel_date.isoformat() if condition.travel_date else None,
        "preference_applied": preference is not None
    }

    if not places:
        return ConditionRecommendResponse(
            success=True,
            places=[],
            total_count=0,
            applied_filters=applied_filters,
            message="조건에 맞는 여행지가 없습니다. 조건을 조정해보세요."
        )

    return ConditionRecommendResponse(
        success=True,
        places=places,
        total_count=len(places),
        applied_filters=applied_filters,
        message=f"{len(places)}개의 여행지를 추천합니다."
    )


@router.get("/popular")
async def get_popular_places(
    region: str = None,
    limit: int = 10,
    db: AsyncSession = Depends(provide_session)
):
    """
    인기 여행지 조회 (로그인 불필요)

    추후 방문 수, 좋아요 등 기반으로 개선 가능
    """
    from sqlalchemy import select
    from core.models import Place

    query = select(Place)

    if region:
        query = query.where(Place.address.contains(region))

    query = query.order_by(Place.readcount.desc().nullslast()).limit(limit)

    result = await db.execute(query)
    places = result.scalars().all()

    return {
        "success": True,
        "places": [
            {
                "id": p.id,
                "name": p.name,
                "category": p.category,
                "address": p.address,
                "latitude": p.latitude,
                "longitude": p.longitude,
                "image_url": p.image_url,
                "tags": p.tags
            }
            for p in places
        ],
        "total": len(places)
    }
