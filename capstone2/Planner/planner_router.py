from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import provide_session
from core.models import User
from User.user_router import get_current_user

from Recommend.preference_service import get_user_preference
from Recommend.dto import PreferenceSurvey, PreferenceResponse
from Recommend.preference_service import save_user_preference

from Planner.dto import (
    GenerateRequest, GenerateResponse,
    ChatRequest, ChatResponse, ChatHistoryResponse, ChatMessage,
    OptimizeRequest
)
from Planner.planner_service import get_planner_service
from Planner.chat_service import get_chat_service
from Planner.route_optimizer import get_route_optimizer
from Trip import crud as trip_crud


router = APIRouter(
    prefix="/planner",
    tags=["planner"]
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

    카테고리 점수(1-5)를 가중치(0-1)로 변환하여 저장합니다.
    이후 AI 일정 생성 시 자동으로 반영됩니다.
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


# ==================== AI 일정 생성 API ====================

@router.post("/generate", response_model=GenerateResponse)
async def generate_itinerary(
    request: GenerateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    AI 일정 생성

    선호도 + 조건을 기반으로 GPT가 최적의 여행 일정을 생성합니다.

    - 후보 장소 수집 (조건 + 선호도 반영)
    - GPT로 일정 초안 생성
    - 동선 최적화 (TSP 알고리즘)
    - 시간 제약 적용 (영업시간, 체류시간)
    - DB 저장
    """
    # 날짜 유효성 검사
    if request.end_date < request.start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="종료일은 시작일보다 이후여야 합니다"
        )

    # 사용자 선호도 로드
    preference = await get_user_preference(db, current_user.id)

    # 일정 생성
    planner = get_planner_service()

    try:
        result = await planner.generate_itinerary(
            db, current_user.id, request, preference
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"일정 생성 중 오류가 발생했습니다: {str(e)}"
        )


# ==================== 대화형 수정 API ====================

@router.post("/chat", response_model=ChatResponse)
async def chat_modify(
    request: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    대화형 일정 수정

    자연어로 일정 수정을 요청하면 AI가 이해하고 적용합니다.

    예시:
    - "2일차에 카페 하나 넣어줘"
    - "감천문화마을 빼줘"
    - "해운대를 첫 번째로 옮겨줘"
    - "1일차랑 2일차 바꿔줘"
    """
    # 여행 소유권 확인
    trip = await trip_crud.get_trip_by_id(db, request.trip_id, current_user.id)
    if not trip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="여행을 찾을 수 없습니다"
        )

    # 채팅 처리
    chat_service = get_chat_service()

    try:
        result = await chat_service.process_message(
            db, current_user.id, request
        )
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"처리 중 오류가 발생했습니다: {str(e)}"
        )


@router.get("/chat/history/{session_id}", response_model=ChatHistoryResponse)
async def get_chat_history(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """대화 히스토리 조회"""
    chat_service = get_chat_service()
    session = await chat_service.get_chat_history(db, current_user.id, session_id)

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="채팅 세션을 찾을 수 없습니다"
        )

    return ChatHistoryResponse(
        session_id=session.id,
        trip_id=session.trip_id,
        messages=[
            ChatMessage(role=m["role"], content=m["content"])
            for m in (session.messages or [])
        ],
        current_state=session.current_state
    )


# ==================== 동선 최적화 API ====================

@router.post("/optimize")
async def optimize_route(
    request: OptimizeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    동선 최적화만 실행

    기존 일정의 순서를 최적화합니다 (TSP 알고리즘).
    장소 추가/삭제 없이 순서만 변경됩니다.
    """
    # 여행 로드
    trip = await trip_crud.get_trip_by_id(db, request.trip_id, current_user.id)
    if not trip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="여행을 찾을 수 없습니다"
        )

    if not trip.itineraries:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="일정이 비어있습니다"
        )

    # 일차별 그룹화
    places_by_day = {}
    for it in trip.itineraries:
        day = it.day_number
        if day not in places_by_day:
            places_by_day[day] = []

        places_by_day[day].append({
            "itinerary_id": it.id,
            "place_id": it.place_id,
            "place_name": it.place.name,
            "latitude": it.place.latitude,
            "longitude": it.place.longitude,
            "order_index": it.order_index
        })

    # 최적화 실행
    optimizer = get_route_optimizer()
    optimized = optimizer.optimize(
        places_by_day,
        request.start_location,
        request.end_location
    )

    # DB 업데이트
    from Trip.dto import ItineraryReorderItem
    reorder_items = []
    for day, places in optimized.items():
        for place in places:
            reorder_items.append(
                ItineraryReorderItem(
                    id=place["itinerary_id"],
                    day_number=day,
                    order_index=place["order_index"]
                )
            )

    await trip_crud.reorder_itineraries(db, trip.id, reorder_items)

    # 최적화 점수 계산
    score = optimizer.calculate_optimization_score(optimized)
    total_travel = optimizer.estimate_total_travel_time(optimized)

    return {
        "success": True,
        "trip_id": trip.id,
        "optimization_score": round(score, 2),
        "total_travel_time": total_travel,
        "message": "동선이 최적화되었습니다"
    }
