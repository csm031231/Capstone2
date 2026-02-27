import json
import traceback
from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, Form, File, HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import provide_session
from core.models import User
from User.user_router import get_current_user

from Recommend.preference_service import get_user_preference

from Planner.dto import (
    GenerateRequest, GenerateResponse,
    GenerateWithPhotoRequest, GenerateWithPhotoResponse,
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


# 선호도 API는 /recommend/preference로 통일되었습니다.


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
        print(f"\n{'='*60}")
        print(f"[PLANNER ERROR] 일정 생성 중 오류:")
        traceback.print_exc()
        print(f"{'='*60}\n")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"일정 생성 중 오류가 발생했습니다: {str(e)}"
        )


# ==================== 사진 기반 일정 생성 API ====================

# scene_type → 한국어 테마 매핑
SCENE_TO_THEME = {
    "city": "도시여행", "urban": "도시여행",
    "historical": "역사", "heritage": "역사", "temple": "역사",
    "nature": "자연", "mountain": "자연", "forest": "자연",
    "beach": "해변", "ocean": "해변", "sea": "해변",
    "night": "야경", "nightlife": "야경",
    "culture": "문화", "art": "문화", "museum": "문화",
    "food": "맛집", "restaurant": "맛집",
    "shopping": "쇼핑",
    "rural": "전통", "village": "전통",
    "theme_park": "테마파크", "amusement": "테마파크",
}

# 지역명 정규화 (영문/한문 → 대표 한국어)
REGION_ALIASES = {
    "서울": ["seoul", "서울", "서울특별시"],
    "부산": ["busan", "부산", "부산광역시"],
    "제주": ["jeju", "제주도", "제주특별자치도"],
    "강원": ["gangwon", "강원", "강원도"],
    "경기": ["gyeonggi", "경기", "경기도"],
    "인천": ["incheon", "인천", "인천광역시"],
    "대구": ["daegu", "대구", "대구광역시"],
    "광주": ["gwangju", "광주", "광주광역시"],
    "대전": ["daejeon", "대전", "대전광역시"],
    "경북": ["gyeongbuk", "경북", "경상북도"],
    "경남": ["gyeongnam", "경남", "경상남도"],
    "전북": ["jeonbuk", "전북", "전라북도"],
    "전남": ["jeonnam", "전남", "전라남도"],
}


def _normalize_region(name: str) -> Optional[str]:
    """도시/지역명을 대표 한국어 지역명으로 정규화"""
    if not name:
        return None
    name_lower = name.lower().strip()
    for key, aliases in REGION_ALIASES.items():
        if any(alias.lower() in name_lower or name_lower in alias.lower() for alias in aliases):
            return key
    return None


def _regions_match(photo_city: str, trip_region: str) -> bool:
    """사진 도시와 여행 지역이 같은 지역인지 확인"""
    photo_norm = _normalize_region(photo_city)
    trip_norm = _normalize_region(trip_region)
    if photo_norm and trip_norm:
        return photo_norm == trip_norm
    # 정규화 실패 시 단순 포함 여부
    return photo_city.lower() in trip_region.lower() or trip_region.lower() in photo_city.lower()


def _extract_themes_from_scene(scene_types: List[str]) -> List[str]:
    """scene_type 배열에서 한국어 테마 추출"""
    themes = []
    for s in scene_types:
        theme = SCENE_TO_THEME.get(s.lower())
        if theme and theme not in themes:
            themes.append(theme)
    return themes


@router.post("/generate-with-photo", response_model=GenerateWithPhotoResponse)
async def generate_with_photo(
    request: GenerateWithPhotoRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    사진 분석 결과를 반영한 AI 일정 생성

    1. 사진 지역 vs 희망 여행 지역 비교
    2. 불일치 시 → 확인 메시지 반환 (needs_clarification=true)
    3. 확인 후 use_photo_themes=true로 재요청 → 사진 분위기를 테마에 반영하여 일정 생성

    프론트 사용 흐름:
    - 1차: use_photo_themes=false → needs_clarification 확인
    - 2차: use_photo_themes=true → 실제 일정 생성
    """
    if request.end_date < request.start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="종료일은 시작일보다 이후여야 합니다"
        )

    # 사진 정보가 있고, 지역 불일치이고, 아직 확인 전인 경우
    if (
        request.photo_city
        and not request.use_photo_themes
        and not _regions_match(request.photo_city, request.region)
    ):
        photo_label = request.photo_landmark or request.photo_city
        suggested_themes = _extract_themes_from_scene(request.photo_scene_types)

        return GenerateWithPhotoResponse(
            needs_clarification=True,
            clarification_message=(
                f"사진은 {photo_label}으로 추정되는데, "
                f"{request.region} 여행을 원하시는군요! "
                f"사진의 분위기({', '.join(request.photo_scene_types) if request.photo_scene_types else '분석됨'})를 "
                f"{request.region} 일정 테마에 반영할까요?"
            ),
            photo_info={
                "city": request.photo_city,
                "landmark": request.photo_landmark,
                "scene_types": request.photo_scene_types,
            },
            suggested_themes=suggested_themes,
        )

    # 테마 합성: 사진 분위기 + 사용자 지정 테마
    merged_themes = list(request.themes)
    if request.use_photo_themes and request.photo_scene_types:
        photo_themes = _extract_themes_from_scene(request.photo_scene_types)
        for t in photo_themes:
            if t not in merged_themes:
                merged_themes.append(t)

    # 기존 GenerateRequest로 변환하여 일정 생성
    generate_request = GenerateRequest(
        title=request.title,
        region=request.region,
        start_date=request.start_date,
        end_date=request.end_date,
        must_visit_places=request.must_visit_places,
        exclude_places=request.exclude_places,
        themes=merged_themes,
        max_places_per_day=request.max_places_per_day,
        start_location=request.start_location,
        end_location=request.end_location,
    )

    preference = await get_user_preference(db, current_user.id)
    planner = get_planner_service()

    try:
        trip_data = await planner.generate_itinerary(
            db, current_user.id, generate_request, preference
        )
        return GenerateWithPhotoResponse(
            needs_clarification=False,
            photo_info={
                "city": request.photo_city,
                "landmark": request.photo_landmark,
                "scene_types": request.photo_scene_types,
            } if (request.photo_city or request.photo_landmark) else None,
            trip_data=trip_data,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"일정 생성 중 오류: {str(e)}"
        )


# ==================== 사진 파일 업로드 + 일정 생성 (Swagger 통합 테스트용) ====================

@router.post("/generate-with-photo-upload", response_model=GenerateWithPhotoResponse)
async def generate_with_photo_upload(
    image: UploadFile = File(..., description="여행 사진 파일"),
    title: str = Form(..., description="여행 제목"),
    region: str = Form(..., description="희망 여행 지역 (예: 부산)"),
    start_date: date = Form(..., description="시작일 (YYYY-MM-DD)"),
    end_date: date = Form(..., description="종료일 (YYYY-MM-DD)"),
    must_visit_places: str = Form(default="[]", description="필수 장소 ID 배열 (JSON, 예: [1,2])"),
    exclude_places: str = Form(default="[]", description="제외 장소 ID 배열 (JSON, 예: [3])"),
    themes: str = Form(default="[]", description="테마 배열 (JSON, 예: [\"해변\",\"맛집\"])"),
    max_places_per_day: int = Form(default=10, ge=2, le=20, description="하루 최대 장소 수"),
    use_photo_themes: bool = Form(default=False, description="사진 분위기를 테마에 반영할지 여부"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    사진 업로드 + AI 일정 생성 통합 엔드포인트 (Swagger docs에서 직접 테스트 가능)

    **흐름:**
    1. 사진을 업로드하면 GPT Vision이 자동으로 지역/분위기 분석
    2. 사진 지역 ≠ 희망 여행 지역이면 → `needs_clarification=true` + 확인 메시지 반환
    3. `use_photo_themes=true`로 재요청하면 → 사진 분위기를 테마로 반영하여 일정 생성

    **use_photo_themes 사용법:**
    - 1차 요청: `use_photo_themes=false` → 지역 불일치 여부 확인
    - 2차 요청: `use_photo_themes=true` → 사진 분위기 반영하여 일정 생성
    """
    from Vision.gpt_vision import analyze_image_with_gpt, determine_type
    from Vision.exif_utils import extract_exif_info
    from Vision.vision_router import _validate_and_read_image, _save_image

    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="종료일은 시작일보다 이후여야 합니다"
        )

    # 리스트 파라미터 파싱
    try:
        must_visit_list: List[int] = json.loads(must_visit_places)
        exclude_list: List[int] = json.loads(exclude_places)
        themes_list: List[str] = json.loads(themes)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="must_visit_places, exclude_places, themes는 유효한 JSON 배열이어야 합니다"
        )

    # 이미지 검증 및 저장
    contents, img, ext = await _validate_and_read_image(image)
    exif_info = extract_exif_info(img)
    file_path = _save_image(contents, ext)

    # GPT Vision 분석
    try:
        analysis = await analyze_image_with_gpt(file_path)
        result_type = determine_type(analysis, exif_info)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"사진 분석 중 오류: {str(e)}"
        )

    photo_city = analysis.city
    photo_landmark = analysis.landmark
    photo_scene_types = analysis.scene_type or []

    # 지역 불일치 확인 (use_photo_themes=false이고 아직 확인 안 된 경우)
    if photo_city and not use_photo_themes and not _regions_match(photo_city, region):
        photo_label = photo_landmark or photo_city
        suggested_themes = _extract_themes_from_scene(photo_scene_types)

        return GenerateWithPhotoResponse(
            needs_clarification=True,
            clarification_message=(
                f"사진은 {photo_label}으로 추정되는데, "
                f"{region} 여행을 원하시는군요! "
                f"사진의 분위기({', '.join(photo_scene_types) if photo_scene_types else '분석됨'})를 "
                f"{region} 일정 테마에 반영할까요?"
            ),
            photo_info={
                "city": photo_city,
                "landmark": photo_landmark,
                "scene_types": photo_scene_types,
                "result_type": result_type,
                "confidence": analysis.confidence,
            },
            suggested_themes=suggested_themes,
        )

    # 테마 합성: 사진 분위기 + 사용자 지정 테마
    merged_themes = list(themes_list)
    if use_photo_themes and photo_scene_types:
        for t in _extract_themes_from_scene(photo_scene_types):
            if t not in merged_themes:
                merged_themes.append(t)

    # 일정 생성
    generate_request = GenerateRequest(
        title=title,
        region=region,
        start_date=start_date,
        end_date=end_date,
        must_visit_places=must_visit_list,
        exclude_places=exclude_list,
        themes=merged_themes,
        max_places_per_day=max_places_per_day,
    )

    preference = await get_user_preference(db, current_user.id)
    planner = get_planner_service()

    try:
        trip_data = await planner.generate_itinerary(
            db, current_user.id, generate_request, preference
        )
        return GenerateWithPhotoResponse(
            needs_clarification=False,
            photo_info={
                "city": photo_city,
                "landmark": photo_landmark,
                "scene_types": photo_scene_types,
                "result_type": result_type,
                "confidence": analysis.confidence,
            },
            trip_data=trip_data,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"일정 생성 중 오류: {str(e)}"
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
    optimized = await optimizer.optimize(
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
