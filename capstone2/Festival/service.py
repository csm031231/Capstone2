import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime, date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from DataCollector.tour_api_service import TourAPIService, get_tour_api_service
from Festival.dto import FestivalInfo, FestivalSearchRequest
from core.models import Place  #


class FestivalService:
    """
    축제/행사 검색 및 관리 서비스
    TourAPI를 통해 정보를 조회하고, 필요 시 앱 내 DB(Place)에 저장합니다.
    """

    def __init__(self):
        self.tour_api = get_tour_api_service()

    # ==================== 1. 축제 조회 및 검색 로직 ====================

    async def search_festivals(
        self,
        db: AsyncSession,
        request: FestivalSearchRequest
    ) -> Dict[str, Any]:
        """축제 검색 메인 로직"""
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

                # 4. 키워드 필터링
                if request.keyword:
                    items = [
                        item for item in items
                        if request.keyword.lower() in item.get("title", "").lower()
                    ]

                festivals.extend(items)

                if len(items) < 50:
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
                detail = await self.tour_api.get_full_place_info(content_id, 15)  # 15 = 축제타입
                
                festival_info = self._parse_festival_data(item, detail, today)
                festival_infos.append(festival_info)
            except Exception as e:
                print(f"축제 상세 조회 오류 (ID: {item.get('contentid')}): {e}")
                continue

        return {
            "success": True,
            "festivals": festival_infos,
            "total_count": len(festival_infos),
            "filters_applied": {
                "region": request.region,
                "start_date": request.start_date.isoformat() if request.start_date else None,
                "end_date": request.end_date.isoformat() if request.end_date else None,
                "keyword": request.keyword,
            },
            "message": f"{len(festival_infos)}개의 축제를 찾았습니다."
        }

    async def get_festivals_by_month(
        self,
        db: AsyncSession,
        year: int,
        month: int,
        region: Optional[str] = None
    ) -> Dict[str, Any]:
        """월별 축제 캘린더 데이터 생성"""
        from calendar import monthrange
        last_day = monthrange(year, month)[1]
        
        start_date = date(year, month, 1)
        end_date = date(year, month, last_day)

        request = FestivalSearchRequest(
            region=region,
            start_date=start_date,
            end_date=end_date,
            max_items=200
        )

        result = await self.search_festivals(db, request)
        if not result["success"]:
            return result

        festivals_by_date = {}
        for festival in result["festivals"]:
            if not festival.event_start_date:
                continue
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
        """현재 진행 중인 축제 필터링 조회"""
        today = datetime.now().date()
        start_date = date(today.year, today.month, 1)
        
        if today.month == 12:
            end_date = date(today.year + 1, 1, 31)
        else:
            from calendar import monthrange
            end_month = today.month + 1
            end_date = date(today.year, end_month, monthrange(today.year, end_month)[1])

        request = FestivalSearchRequest(region=region, start_date=start_date, end_date=end_date, max_items=100)
        result = await self.search_festivals(db, request)

        ongoing = [f for f in result["festivals"] if f.is_ongoing]
        return {
            "success": True,
            "festivals": ongoing[:max_items],
            "total_count": len(ongoing),
            "filters_applied": {"region": region, "status": "ongoing"},
            "message": f"현재 진행 중인 축제 {len(ongoing)}개"
        }

    def _parse_festival_data(
        self,
        item: Dict[str, Any],
        detail: Optional[Dict[str, Any]],
        today: date
    ) -> FestivalInfo:
        """API 데이터를 FestivalInfo DTO로 변환"""
        content_id = int(item.get("contentid", 0))
        title = item.get("title", "")
        address = f"{item.get('addr1', '')} {item.get('addr2', '')}".strip()
        
        region = next((r for r in self.tour_api.AREA_CODE.keys() if r in address), None)

        latitude = float(item.get("mapy", 0)) if item.get("mapy") else None
        longitude = float(item.get("mapx", 0)) if item.get("mapx") else None
        event_start_date = item.get("eventstartdate")
        event_end_date = item.get("eventenddate")

        is_ongoing, is_upcoming, d_start, d_end = False, False, None, None
        if event_start_date and event_end_date:
            try:
                s_dt = datetime.strptime(event_start_date, "%Y%m%d").date()
                e_dt = datetime.strptime(event_end_date, "%Y%m%d").date()
                if s_dt <= today <= e_dt:
                    is_ongoing, d_end = True, (e_dt - today).days
                elif today < s_dt:
                    is_upcoming, d_start, d_end = True, (s_dt - today).days, (e_dt - today).days
            except ValueError: pass

        desc, tel, home, e_place, p_time, prog, fee = [None] * 7
        if detail:
            desc = self.tour_api._clean_html(detail.get("overview", ""))
            tel = detail.get("tel", "")
            home = detail.get("homepage", "")
            e_place = detail.get("eventplace", "")
            p_time = self.tour_api._clean_html(detail.get("playtime", ""))
            prog = self.tour_api._clean_html(detail.get("program", ""))
            fee = self.tour_api._clean_html(detail.get("usetimefestival", ""))

        return FestivalInfo(
            id=content_id, title=title, address=address, region=region,
            event_start_date=event_start_date, event_end_date=event_end_date,
            latitude=latitude, longitude=longitude, description=desc,
            image_url=item.get("firstimage") or item.get("firstimage2"),
            tel=tel, homepage=home, event_place=e_place, playtime=p_time,
            program=prog, usetimefestival=fee, is_ongoing=is_ongoing,
            is_upcoming=is_upcoming, days_until_start=d_start, days_until_end=d_end
        )

    # ==================== 2. 축제 → Place 변환 및 저장 로직 (핵심 추가) ====================

    async def save_festival_as_place(
        self,
        db: AsyncSession,
        festival_id: int
    ) -> int:
        """
        축제 정보를 Place 테이블에 저장하여 일정(Itinerary)에 넣을 수 있게 합니다.
        """
        # 1. 축제 상세 정보 가져오기
        detail = await self.tour_api.get_full_place_info(festival_id, 15)
        if not detail:
            raise ValueError("축제 정보를 찾을 수 없습니다")
        
        # 2. 이미 저장되어 있는지 확인 (중복 방지)
        title = detail.get("title", "")
        existing = await db.execute(
            select(Place).where(
                Place.name == title, 
                Place.is_festival == True
            )
        )
        existing_place = existing.scalar_one_or_none()
        if existing_place:
            return existing_place.id
        
        # 3. 새로운 Place 객체 생성 및 매핑
        # 기획안의 7.1, 7.2 운영 정보 제공 기능을 반영합니다.
        place = Place(
            name=title,
            category="축제/행사",
            address=f"{detail.get('addr1', '')} {detail.get('addr2', '')}".strip(),
            latitude=float(detail.get("mapy", 0)) if detail.get("mapy") else 0.0,
            longitude=float(detail.get("mapx", 0)) if detail.get("mapx") else 0.0,
            description=self.tour_api._clean_html(detail.get("overview", "")),
            image_url=detail.get("firstimage"),
            tags=["축제"],
            
            # 축제 전용 필드 (일정 생성 시 기간 확인용)
            is_festival=True,
            event_start_date=detail.get("eventstartdate"),
            event_end_date=detail.get("eventenddate"),
            
            # 운영 정보 안내 (기능 7.2 반영)
            operating_hours=self.tour_api._clean_html(detail.get("playtime", "")),
            fee_info=self.tour_api._clean_html(detail.get("usetimefestival", ""))
        )
        
        db.add(place)
        await db.commit()
        await db.refresh(place)
        
        return place.id


# 싱글톤 관리
_festival_service_instance = None

def get_festival_service() -> FestivalService:
    global _festival_service_instance
    if _festival_service_instance is None:
        _festival_service_instance = FestivalService()
    return _festival_service_instance