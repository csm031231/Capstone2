from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import date, datetime

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


# ==================== ⭐ 캘린더 API (개선) ====================

@router.get("/calendar/{year}/{month}")
async def get_festival_calendar(
    year: int,
    month: int,
    region: Optional[str] = Query(None, description="지역 필터 (선택)"),
    max_duration: int = Query(
        10, 
        ge=10, 
        le=365, 
        description="최대 축제 기간 (일). 이보다 긴 축제는 제외됩니다."
    ),
    db: AsyncSession = Depends(provide_session)
):
    """
    월별 축제 캘린더 (필터링 개선 버전)
    
    특정 연월에 진행되는 축제 목록을 날짜별로 그룹화하여 반환
    
    ⭐ 개선사항:
    - 너무 긴 기간의 축제 자동 필터링 (기본 30일 초과 제외)
    - 해당 월에 실제로 진행되는 날짜만 표시
    - 제외된 축제 수 통계 제공
    
    Parameters:
        - year: 연도 (2024~2030)
        - month: 월 (1~12)
        - region: 지역명 (서울, 부산 등)
                - max_duration: 최대 기간 제한 (기본 10일)
                    예: 30으로 설정 시 1월1일~12월31일 같은 연중 축제 제외
    
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
        result = await service.get_festivals_by_month(
            db, 
            year, 
            month, 
            region,
            max_duration_days=max_duration  # ⭐ 필터링 파라미터 추가
        )
        
        return {
            "success": result["success"],
            "year": result["year"],
            "month": result["month"],
            "festivals_by_date": result["festivals_by_date"],
            "total_count": result["total_count"],
            "excluded_count": result.get("excluded_count", 0),  # ⭐ 제외 통계
            "filter_applied": result.get("filter_applied", {}),
            "message": f"{result['total_count']}개 축제 ({result.get('excluded_count', 0)}개 제외됨)"
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"캘린더 조회 중 오류 발생: {str(e)}"
        )


@router.get("/calendar/{year}/{month}/summary")
async def get_calendar_summary(
    year: int,
    month: int,
    region: Optional[str] = Query(None, description="지역 필터 (선택)"),
    db: AsyncSession = Depends(provide_session)
):
    """
    ⭐ 신규: 월별 캘린더 요약 (초경량 버전)
    
    각 날짜별로 축제 개수와 대표 축제 1개만 반환
    → 캘린더 UI 렌더링 최적화용
    
    응답 예시:
    {
      "dates": {
        "20250301": {
          "count": 3,
          "representative": {
            "id": 123456,
            "title": "서울 벚꽃 축제",
            "image_url": "https://..."
          }
        }
      }
    }
    
    사용 시나리오:
    - 캘린더에 점이나 뱃지 표시용
    - 날짜 클릭 시 전체 목록은 별도 API 호출
    
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
        result = await service.get_calendar_summary(db, year, month, region)
        
        return result
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"캘린더 요약 조회 중 오류 발생: {str(e)}"
        )


@router.get("/calendar/date/{date_str}")
async def get_festivals_by_specific_date(
    date_str: str,
    region: Optional[str] = Query(None, description="지역 필터 (선택)"),
    db: AsyncSession = Depends(provide_session)
):
    """
    ⭐ 신규: 특정 날짜의 축제 목록
    
    캘린더에서 날짜를 클릭했을 때 사용
    
    Parameters:
        - date_str: 날짜 (YYYYMMDD, 예: 20250301)
        - region: 지역 필터 (선택)
    
    인증 불필요
    """
    # 날짜 형식 검증
    if not date_str.isdigit() or len(date_str) != 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="날짜 형식이 올바르지 않습니다 (YYYYMMDD 형식, 예: 20250301)"
        )
    try:
        target_date = datetime.strptime(date_str, "%Y%m%d").date()
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="날짜 형식이 올바르지 않습니다 (YYYYMMDD 형식)"
        )
    
    year = target_date.year
    month = target_date.month
    
    service = get_festival_service()
    
    try:
        # 해당 월 데이터 가져오기
        result = await service.get_festivals_by_month(db, year, month, region)
        
        if not result["success"]:
            return {
                "success": False,
                "date": date_str,
                "festivals": [],
                "message": "축제를 찾을 수 없습니다."
            }
        
        # 특정 날짜의 축제만 추출
        festivals_on_date = result["festivals_by_date"].get(date_str, [])
        
        return {
            "success": True,
            "date": date_str,
            "festivals": festivals_on_date,
            "count": len(festivals_on_date),
            "message": f"{target_date.year}년 {target_date.month}월 {target_date.day}일의 축제 {len(festivals_on_date)}개"
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"날짜별 축제 조회 중 오류 발생: {str(e)}"
        )


# ==================== 기타 축제 조회 API ====================

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
    from datetime import timedelta
    
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


@router.delete("/calendar/cache")
async def clear_festival_calendar_cache():
    """
    캘린더 캐시 수동 초기화

    데이터 갱신 후 즉시 반영이 필요할 때 사용 (TTL 기다리지 않고 강제 삭제)

    인증 불필요
    """
    service = get_festival_service()
    count = len(service._calendar_cache)
    service._calendar_cache.clear()
    service._calendar_cache_time.clear()
    return {"success": True, "message": f"캘린더 캐시 {count}건 삭제됨"}


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
    from datetime import timedelta
    
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
    from DataCollector.tour_api_service import get_tour_api_service, TourAPIRateLimitError

    tour_api = get_tour_api_service()

    try:
        # 공통 정보 + 소개 정보 조회
        detail = await tour_api.get_full_place_info(festival_id, 15)  # 15 = 축제공연행사

        if detail is None:
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
    except TourAPIRateLimitError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TourAPI 요청 제한 중입니다. 잠시 후 다시 시도해주세요."
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"축제 상세 조회 중 오류 발생: {str(e)}"
        )
    
    