from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import date

from core.database import provide_session
from core.models import User
from User.user_router import get_current_user

from Festival.dto import (
    FestivalSearchRequest, 
    FestivalSearchResponse,
    FestivalCalendarResponse
)
from Festival.service import get_festival_service


router = APIRouter(
    prefix="/festivals",
    tags=["festivals"]
)


# ==================== 축제 검색 API ====================

@router.post("/search", response_model=FestivalSearchResponse)
async def search_festivals(
    request: FestivalSearchRequest,
    db: AsyncSession = Depends(provide_session)
):
    """
    축제 검색
    
    - 지역별 필터링 (서울, 부산, 제주 등)
    - 기간별 필터링 (시작일 ~ 종료일)
    - 키워드 검색 (축제명)
    
    인증 불필요
    """
    service = get_festival_service()
    
    try:
        result = await service.search_festivals(db, request)
        
        return FestivalSearchResponse(
            success=result["success"],
            festivals=result["festivals"],
            total_count=result["total_count"],
            filters_applied=result["filters_applied"],
            message=result["message"]
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"축제 검색 중 오류 발생: {str(e)}"
        )


@router.get("/calendar/{year}/{month}", response_model=FestivalCalendarResponse)
async def get_festival_calendar(
    year: int,
    month: int,
    region: Optional[str] = Query(None, description="지역 필터 (선택)"),
    db: AsyncSession = Depends(provide_session)
):
    """
    월별 축제 캘린더
    
    특정 연월에 진행되는 축제 목록을 날짜별로 그룹화하여 반환
    
    인증 불필요
    """
    # 유효성 검사
    if year < 2024 or year > 2030:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="연도는 2024~2030 사이여야 합니다"
        )
    
    if month < 1 or month > 12:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="월은 1~12 사이여야 합니다"
        )
    
    service = get_festival_service()
    
    try:
        result = await service.get_festivals_by_month(db, year, month, region)
        
        return FestivalCalendarResponse(
            success=result["success"],
            year=result["year"],
            month=result["month"],
            festivals_by_date=result["festivals_by_date"],
            total_count=result["total_count"]
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"캘린더 조회 중 오류 발생: {str(e)}"
        )


@router.get("/ongoing")
async def get_ongoing_festivals(
    region: Optional[str] = Query(None, description="지역 필터 (선택)"),
    max_items: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(provide_session)
):
    """
    현재 진행 중인 축제
    
    오늘 날짜 기준으로 진행 중인 축제만 필터링
    
    인증 불필요
    """
    service = get_festival_service()
    
    try:
        result = await service.get_ongoing_festivals(db, region, max_items)
        
        return {
            "success": result["success"],
            "festivals": result["festivals"],
            "total_count": result["total_count"],
            "filters_applied": result["filters_applied"],
            "message": result["message"]
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"진행 중인 축제 조회 중 오류 발생: {str(e)}"
        )


@router.get("/upcoming")
async def get_upcoming_festivals(
    region: Optional[str] = Query(None, description="지역 필터 (선택)"),
    days: int = Query(30, ge=1, le=365, description="앞으로 N일 이내"),
    max_items: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(provide_session)
):
    """
    예정된 축제
    
    앞으로 N일 이내에 시작하는 축제 조회
    
    인증 불필요
    """
    from datetime import datetime, timedelta
    
    today = datetime.now().date()
    end_date = today + timedelta(days=days)
    
    service = get_festival_service()
    
    try:
        request = FestivalSearchRequest(
            region=region,
            start_date=today,
            end_date=end_date,
            max_items=max_items
        )
        
        result = await service.search_festivals(db, request)
        
        # 예정된 것만 필터링
        upcoming = [f for f in result["festivals"] if f.is_upcoming]
        
        # 시작일 임박순 정렬
        upcoming.sort(key=lambda x: x.days_until_start if x.days_until_start else 999)
        
        return {
            "success": True,
            "festivals": upcoming,
            "total_count": len(upcoming),
            "filters_applied": {
                "region": region,
                "days": days,
                "status": "upcoming"
            },
            "message": f"앞으로 {days}일 이내 시작 예정 축제 {len(upcoming)}개"
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"예정 축제 조회 중 오류 발생: {str(e)}"
        )


@router.get("/regions")
async def get_available_regions():
    """
    축제 검색 가능한 지역 목록
    
    인증 불필요
    """
    from DataCollector.tour_api_service import get_tour_api_service
    
    tour_api = get_tour_api_service()
    
    return {
        "regions": list(tour_api.AREA_CODE.keys()),
        "message": "축제 검색 가능한 지역 목록"
    }


@router.get("/popular")
async def get_popular_festivals(
    limit: int = Query(10, ge=1, le=50, description="최대 결과 수"),
    region: Optional[str] = Query(None, description="지역 필터 (선택)"),
    db: AsyncSession = Depends(provide_session)
):
    """
    인기 축제 목록 (홈 화면용)
    
    우선순위:
    1. 현재 진행 중인 축제 (진행중 우선)
    2. 곧 시작하는 축제 (D-day 순)
    3. 종료까지 남은 일수 순
    
    인증 불필요
    """
    from datetime import datetime, timedelta
    
    service = get_festival_service()
    today = datetime.now().date()
    
    # 현재부터 3개월 후까지 검색
    end_date = today + timedelta(days=90)
    
    try:
        request = FestivalSearchRequest(
            region=region,
            start_date=today - timedelta(days=30),  # 30일 전부터 (현재 진행중 포함)
            end_date=end_date,
            max_items=100  # 넓게 가져와서 필터링
        )
        
        result = await service.search_festivals(db, request)
        
        if not result["success"]:
            return {
                "success": False,
                "festivals": [],
                "total_count": 0,
                "message": "축제를 찾을 수 없습니다."
            }
        
        festivals = result["festivals"]
        
        # 우선순위 정렬
        def priority_score(festival):
            """
            우선순위 점수 계산
            - 진행 중: 1000 + (종료까지 남은 일수)
            - 예정: 500 - (시작까지 남은 일수)
            - 종료: 0
            """
            if festival.is_ongoing:
                return 1000 + (festival.days_until_end or 0)
            elif festival.is_upcoming:
                return 500 - (festival.days_until_start or 0)
            else:
                return 0
        
        # 정렬: 우선순위 높은 순
        festivals.sort(key=priority_score, reverse=True)
        
        # 상위 N개만
        popular = festivals[:limit]
        
        return {
            "success": True,
            "festivals": popular,
            "total_count": len(popular),
            "filters_applied": {"region": region},
            "message": f"인기 축제 {len(popular)}개"
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"인기 축제 조회 중 오류 발생: {str(e)}"
        )


@router.get("/{festival_id}/detail")
async def get_festival_detail(
    festival_id: int,
    db: AsyncSession = Depends(provide_session)
):
    """
    축제 상세 정보
    
    content_id로 축제의 상세 정보 조회
    
    인증 불필요
    """
    from DataCollector.tour_api_service import get_tour_api_service
    
    tour_api = get_tour_api_service()
    
    try:
        # 공통 정보 + 소개 정보 조회
        detail = await tour_api.get_full_place_info(festival_id, 15)  # 15 = 축제공연행사
        
        if not detail:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="축제 정보를 찾을 수 없습니다"
            )
        
        # 필요한 정보만 추출
        return {
            "success": True,
            "festival": {
                "id": festival_id,
                "title": detail.get("title", ""),
                "description": tour_api._clean_html(detail.get("overview", "")),
                "address": f"{detail.get('addr1', '')} {detail.get('addr2', '')}".strip(),
                "tel": detail.get("tel", ""),
                "homepage": detail.get("homepage", ""),
                "event_start_date": detail.get("eventstartdate"),
                "event_end_date": detail.get("eventenddate"),
                "event_place": detail.get("eventplace", ""),
                "playtime": tour_api._clean_html(detail.get("playtime", "")),
                "program": tour_api._clean_html(detail.get("program", "")),
                "usetimefestival": tour_api._clean_html(detail.get("usetimefestival", "")),
                "sponsor1": detail.get("sponsor1", ""),
                "sponsor1tel": detail.get("sponsor1tel", ""),
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"축제 상세 조회 중 오류 발생: {str(e)}"
        )