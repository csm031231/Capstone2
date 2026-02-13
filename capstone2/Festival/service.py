import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime, date
from sqlalchemy.ext.asyncio import AsyncSession

from DataCollector.tour_api_service import TourAPIService, get_tour_api_service
from Festival.dto import FestivalInfo, FestivalSearchRequest


class FestivalService:
    """
    축제/행사 검색 서비스
    
    TourAPI의 searchFestival2 활용
    """

    def __init__(self):
        self.tour_api = get_tour_api_service()

    async def search_festivals(
        self,
        db: AsyncSession,
        request: FestivalSearchRequest
    ) -> Dict[str, Any]:
        """
        축제 검색 메인 로직
        
        Args:
            db: DB 세션
            request: 검색 요청
            
        Returns:
            검색 결과 및 메타데이터
        """
        # 1. 지역 코드 변환
        area_code = None
        if request.region:
            area_code = self.tour_api.AREA_CODE.get(request.region)
            if not area_code:
                return {
                    "success": False,
                    "message": f"알 수 없는 지역: {request.region}",
                    "festivals": [],
                    "total_count": 0,
                    "filters_applied": {}
                }

        # 2. 날짜 포맷 변환 (date -> YYYYMMDD)
        event_start_date = request.start_date.strftime("%Y%m%d") if request.start_date else None
        event_end_date = request.end_date.strftime("%Y%m%d") if request.end_date else None

        # 3. TourAPI 호출
        festivals = []
        page = 1
        
        while len(festivals) < request.max_items:
            try:
                items = await self.tour_api.search_festivals(
                    area_code=area_code,
                    event_start_date=event_start_date,
                    event_end_date=event_end_date,
                    page=page,
                    num_of_rows=min(50, request.max_items - len(festivals))
                )

                if not items:
                    break

                # 4. 키워드 필터링 (API에서 직접 지원 안 함)
                if request.keyword:
                    items = [
                        item for item in items
                        if request.keyword.lower() in item.get("title", "").lower()
                    ]

                festivals.extend(items)

                if len(items) < 50:  # 마지막 페이지
                    break

                page += 1
                await asyncio.sleep(0.3)  # Rate limit 방지

            except Exception as e:
                print(f"축제 검색 오류: {e}")
                break

        # 5. 상세 정보 조회 및 변환
        festival_infos = []
        today = datetime.now().date()

        for item in festivals[:request.max_items]:
            try:
                content_id = int(item.get("contentid", 0))
                
                # 상세 정보 조회
                detail = await self.tour_api.get_full_place_info(content_id, 15)  # 15 = 축제공연행사
                
                # FestivalInfo로 변환
                festival_info = self._parse_festival_data(item, detail, today)
                festival_infos.append(festival_info)

            except Exception as e:
                print(f"축제 상세 조회 오류 (ID: {item.get('contentid')}): {e}")
                continue

        # 6. 필터 요약
        filters_applied = {
            "region": request.region,
            "start_date": request.start_date.isoformat() if request.start_date else None,
            "end_date": request.end_date.isoformat() if request.end_date else None,
            "keyword": request.keyword,
        }

        return {
            "success": True,
            "festivals": festival_infos,
            "total_count": len(festival_infos),
            "filters_applied": filters_applied,
            "message": f"{len(festival_infos)}개의 축제를 찾았습니다."
        }

    def _parse_festival_data(
        self,
        item: Dict[str, Any],
        detail: Optional[Dict[str, Any]],
        today: date
    ) -> FestivalInfo:
        """
        TourAPI 응답을 FestivalInfo로 변환
        """
        # 기본 정보
        content_id = int(item.get("contentid", 0))
        title = item.get("title", "")
        address = f"{item.get('addr1', '')} {item.get('addr2', '')}".strip()
        
        # 지역 추출
        region = None
        for region_name in self.tour_api.AREA_CODE.keys():
            if region_name in address:
                region = region_name
                break

        # 좌표
        latitude = float(item.get("mapy", 0)) if item.get("mapy") else None
        longitude = float(item.get("mapx", 0)) if item.get("mapx") else None

        # 날짜 정보
        event_start_date = item.get("eventstartdate")
        event_end_date = item.get("eventenddate")

        # 상태 계산
        is_ongoing = False
        is_upcoming = False
        days_until_start = None
        days_until_end = None

        if event_start_date and event_end_date:
            try:
                start_dt = datetime.strptime(event_start_date, "%Y%m%d").date()
                end_dt = datetime.strptime(event_end_date, "%Y%m%d").date()

                if start_dt <= today <= end_dt:
                    is_ongoing = True
                    days_until_end = (end_dt - today).days
                elif today < start_dt:
                    is_upcoming = True
                    days_until_start = (start_dt - today).days
                    days_until_end = (end_dt - today).days

            except ValueError:
                pass

        # 상세 정보
        description = None
        image_url = item.get("firstimage") or item.get("firstimage2")
        tel = None
        homepage = None
        event_place = None
        playtime = None
        program = None
        usetimefestival = None

        if detail:
            description = self.tour_api._clean_html(detail.get("overview", ""))
            tel = detail.get("tel", "")
            homepage = detail.get("homepage", "")
            
            # 축제 전용 필드
            event_place = detail.get("eventplace", "")
            playtime = self.tour_api._clean_html(detail.get("playtime", ""))
            program = self.tour_api._clean_html(detail.get("program", ""))
            usetimefestival = self.tour_api._clean_html(detail.get("usetimefestival", ""))

        return FestivalInfo(
            id=content_id,
            title=title,
            address=address,
            region=region,
            event_start_date=event_start_date,
            event_end_date=event_end_date,
            latitude=latitude,
            longitude=longitude,
            description=description,
            image_url=image_url,
            tel=tel,
            homepage=homepage,
            event_place=event_place,
            playtime=playtime,
            program=program,
            usetimefestival=usetimefestival,
            is_ongoing=is_ongoing,
            is_upcoming=is_upcoming,
            days_until_start=days_until_start,
            days_until_end=days_until_end
        )

    async def get_festivals_by_month(
        self,
        db: AsyncSession,
        year: int,
        month: int,
        region: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        월별 축제 캘린더
        
        Args:
            db: DB 세션
            year: 연도
            month: 월
            region: 지역 (선택)
            
        Returns:
            날짜별 축제 목록
        """
        # 해당 월의 시작일과 종료일
        from calendar import monthrange
        last_day = monthrange(year, month)[1]
        
        start_date = date(year, month, 1)
        end_date = date(year, month, last_day)

        # 축제 검색
        request = FestivalSearchRequest(
            region=region,
            start_date=start_date,
            end_date=end_date,
            max_items=200
        )

        result = await self.search_festivals(db, request)

        if not result["success"]:
            return result

        # 날짜별로 그룹화
        festivals_by_date = {}
        
        for festival in result["festivals"]:
            if not festival.event_start_date:
                continue

            # 해당 월에 속하는 날짜만 포함
            try:
                start_dt = datetime.strptime(festival.event_start_date, "%Y%m%d").date()
                
                if start_dt.year == year and start_dt.month == month:
                    date_key = festival.event_start_date
                    if date_key not in festivals_by_date:
                        festivals_by_date[date_key] = []
                    festivals_by_date[date_key].append(festival)

            except ValueError:
                continue

        return {
            "success": True,
            "year": year,
            "month": month,
            "festivals_by_date": festivals_by_date,
            "total_count": len(result["festivals"])
        }

    async def get_ongoing_festivals(
        self,
        db: AsyncSession,
        region: Optional[str] = None,
        max_items: int = 20
    ) -> Dict[str, Any]:
        """
        현재 진행 중인 축제 조회
        
        Args:
            db: DB 세션
            region: 지역 (선택)
            max_items: 최대 결과 수
            
        Returns:
            진행 중인 축제 목록
        """
        today = datetime.now().date()
        
        # 오늘 기준 전후 1개월
        start_date = date(today.year, today.month, 1)
        
        if today.month == 12:
            end_year = today.year + 1
            end_month = 1
        else:
            end_year = today.year
            end_month = today.month + 1
        
        from calendar import monthrange
        last_day = monthrange(end_year, end_month)[1]
        end_date = date(end_year, end_month, last_day)

        # 축제 검색
        request = FestivalSearchRequest(
            region=region,
            start_date=start_date,
            end_date=end_date,
            max_items=max_items
        )

        result = await self.search_festivals(db, request)

        if not result["success"]:
            return result

        # 진행 중인 것만 필터링
        ongoing = [f for f in result["festivals"] if f.is_ongoing]

        return {
            "success": True,
            "festivals": ongoing,
            "total_count": len(ongoing),
            "filters_applied": {"region": region, "status": "ongoing"},
            "message": f"현재 진행 중인 축제 {len(ongoing)}개"
        }


# 싱글톤 인스턴스
_festival_service_instance = None


def get_festival_service() -> FestivalService:
    global _festival_service_instance
    if _festival_service_instance is None:
        _festival_service_instance = FestivalService()
    return _festival_service_instance