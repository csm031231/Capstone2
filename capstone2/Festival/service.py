import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from DataCollector.tour_api_service import TourAPIService, get_tour_api_service
from Festival.dto import FestivalInfo, FestivalSearchRequest
from core.models import Place  # DB 모델 임포트


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
        request: FestivalSearchRequest,
        fetch_detail: bool = True
    ) -> Dict[str, Any]:
        """축제 검색 메인 로직"""
        print("DEBUG Festival: search request =", request.dict())
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

                # 4. 키워드 필터링 (플레이스홀더 처리 포함)
                kw = request.keyword
                if kw:
                    kw = kw.strip()
                    # 프론트에서 기본값으로 'string' 같은 플레이스홀더가 넘어오는 경우 무시
                    if not kw or kw.lower() in ("string", "undefined", "null"):
                        kw = None

                if kw:
                    items = [
                        item for item in items
                        if kw.lower() in (item.get("title", "") or "").lower()
                    ]

                festivals.extend(items)

                if len(items) < 50:  # 마지막 페이지 확인
                    break

                page += 1
                await asyncio.sleep(0.3)  # Rate limit 방지

            except Exception as e:
                print(f"축제 검색 오류: {e}")
                break

        # 5. 상세 정보 조회 및 변환
        today = datetime.now().date()

        if fetch_detail:
            # 상세 API 병렬 호출 (검색/상세 조회용)
            concurrency = 10
            sem = asyncio.Semaphore(concurrency)

            async def _fetch_and_parse(item):
                async with sem:
                    content_id = int(item.get("contentid", 0))
                    detail = None
                    try:
                        detail = await self.tour_api.get_full_place_info(content_id, 15)
                    except Exception as e:
                        print(f"축제 상세 조회 실패 (ID: {content_id}), 목록 데이터만 사용: {e}")

                    try:
                        return self._parse_festival_data(item, detail, today)
                    except Exception as e:
                        print(f"축제 데이터 변환 오류 (ID: {content_id}): {e}")
                        return None

            tasks = [_fetch_and_parse(item) for item in festivals[:request.max_items]]
            results = []
            if tasks:
                results = await asyncio.gather(*tasks)
        else:
            # 경량 모드: 상세 API 호출 없이 목록 데이터만으로 파싱 (캘린더용)
            results = []
            for item in festivals[:request.max_items]:
                try:
                    results.append(self._parse_festival_data(item, None, today))
                except Exception as e:
                    print(f"축제 데이터 변환 오류 (ID: {item.get('contentid')}): {e}")
                    results.append(None)

        festival_infos = [r for r in results if r]

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
        region: Optional[str] = None,
        max_duration_days: int = 10
    ) -> Dict[str, Any]:
        """
        월별 축제 캘린더 데이터 생성 (필터링 개선 버전)

        Args:
            max_duration_days: 이 일수를 초과하는 축제는 제외 (기본 10일)
                               비현실적으로 긴 축제(예: 연중 행사)도 제외
        """
        from calendar import monthrange
        last_day = monthrange(year, month)[1]

        month_start = date(year, month, 1)
        month_end = date(year, month, last_day)

        # 검색 범위: 전월 ~ 다음달 (해당 월에 걸쳐있는 축제 포함)
        search_start = month_start - timedelta(days=60)
        search_end = month_end + timedelta(days=60)

        request = FestivalSearchRequest(
            region=region,
            start_date=search_start,
            end_date=search_end,
            max_items=200
        )

        # 캘린더용: 상세 API 호출 없이 경량 목록 데이터만 사용
        result = await self.search_festivals(db, request, fetch_detail=False)
        if not result["success"]:
            return result

        festivals_by_date = {}
        filtered_festivals = []
        excluded_count = 0

        for festival in result["festivals"]:
            if not festival.event_start_date or not festival.event_end_date:
                excluded_count += 1
                continue

            try:
                f_start = datetime.strptime(festival.event_start_date, "%Y%m%d").date()
                f_end = datetime.strptime(festival.event_end_date, "%Y%m%d").date()

                # 필터 1: 기간이 너무 긴 축제 제외
                duration = (f_end - f_start).days
                if (duration > max_duration_days) or (duration >= 300) or (
                    f_start.month == 1 and f_start.day == 1 and f_end.month == 12 and f_end.day == 31
                ):
                    excluded_count += 1
                    continue

                # 필터 2: 해당 월과 겹치는지 확인
                if f_end < month_start or f_start > month_end:
                    continue

                actual_start = max(f_start, month_start)
                actual_end = min(f_end, month_end)

                current_date = actual_start
                while current_date <= actual_end:
                    date_key = current_date.strftime("%Y%m%d")

                    if date_key not in festivals_by_date:
                        festivals_by_date[date_key] = []

                    calendar_item = {
                        "id": festival.id,
                        "title": festival.title,
                        "is_ongoing": festival.is_ongoing,
                        "is_upcoming": festival.is_upcoming,
                        "event_start_date": festival.event_start_date,
                        "event_end_date": festival.event_end_date,
                        "image_url": festival.image_url,
                        "region": festival.region,
                    }

                    festivals_by_date[date_key].append(calendar_item)
                    current_date += timedelta(days=1)

                filtered_festivals.append(festival)

            except ValueError:
                excluded_count += 1
                continue

        return {
            "success": True,
            "year": year,
            "month": month,
            "festivals_by_date": festivals_by_date,
            "total_count": len(filtered_festivals),
            "excluded_count": excluded_count,
            "filter_applied": {
                "max_duration_days": max_duration_days,
                "region": region
            }
        }

    async def get_calendar_summary(
        self,
        db: AsyncSession,
        year: int,
        month: int,
        region: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        캘린더용 초경량 요약 데이터

        각 날짜별로 축제 개수와 대표 축제 1개만 반환
        → 프론트 렌더링 속도 최적화
        """
        full_data = await self.get_festivals_by_month(db, year, month, region)

        if not full_data["success"]:
            return full_data

        summary = {}
        for date_key, festivals in full_data["festivals_by_date"].items():
            if not festivals:
                continue

            # 대표 축제 선택 (진행중 > 예정 > 첫번째)
            representative = None
            for fest in festivals:
                if fest.get("is_ongoing"):
                    representative = fest
                    break

            if not representative:
                for fest in festivals:
                    if fest.get("is_upcoming"):
                        representative = fest
                        break

            if not representative:
                representative = festivals[0]

            summary[date_key] = {
                "count": len(festivals),
                "representative": {
                    "id": representative["id"],
                    "title": representative["title"],
                    "image_url": representative.get("image_url")
                }
            }

        return {
            "success": True,
            "year": year,
            "month": month,
            "dates": summary,
            "total_festival_count": full_data["total_count"],
            "excluded_count": full_data["excluded_count"]
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
        """API 원본 데이터를 FestivalInfo DTO로 변환"""
        content_id = int(item.get("contentid", 0))
        title = item.get("title", "")
        address = f"{item.get('addr1', '')} {item.get('addr2', '')}".strip()

        # 지역 추출
        region = next((r for r in self.tour_api.AREA_CODE.keys() if r in address), None)

        # 좌표 및 날짜
        latitude = float(item.get("mapy", 0)) if item.get("mapy") else None
        longitude = float(item.get("mapx", 0)) if item.get("mapx") else None
        event_start_date = item.get("eventstartdate")
        event_end_date = item.get("eventenddate")

        # 상태 계산
        is_ongoing, is_upcoming, d_start, d_end = False, False, None, None
        if event_start_date and event_end_date:
            try:
                s_dt = datetime.strptime(event_start_date, "%Y%m%d").date()
                e_dt = datetime.strptime(event_end_date, "%Y%m%d").date()
                if s_dt <= today <= e_dt:
                    is_ongoing, d_end = True, (e_dt - today).days
                elif today < s_dt:
                    is_upcoming, d_start, d_end = True, (s_dt - today).days, (e_dt - today).days
            except ValueError:
                pass

        # tel은 목록 데이터에서 기본값으로 가져옴 (detail 없어도 유지)
        tel = item.get("tel", "")
        desc = home = e_place = p_time = prog = fee = None

        # 상세 정보 통합 (detail이 없으면 목록 데이터만으로 파싱 - 404 대응)
        if detail:
            try:
                if isinstance(detail, dict):
                    desc = self.tour_api._clean_html(detail.get("overview", ""))
                    tel = detail.get("tel", "") or tel  # detail에 없으면 목록 tel 유지
                    home = detail.get("homepage", "")
                    e_place = detail.get("eventplace", "")
                    p_time = self.tour_api._clean_html(detail.get("playtime", ""))
                    prog = self.tour_api._clean_html(detail.get("program", ""))
                    fee = self.tour_api._clean_html(detail.get("usetimefestival", ""))
                else:
                    print(f"WARNING Festival: unexpected detail type for content {content_id}: {type(detail)}")
            except Exception as ex:
                print(f"ERROR Festival: parsing detail failed for content {content_id}: {ex}")
                import traceback
                traceback.print_exc()

        return FestivalInfo(
            id=content_id, title=title, address=address, region=region,
            event_start_date=event_start_date, event_end_date=event_end_date,
            latitude=latitude, longitude=longitude, description=desc,
            image_url=item.get("firstimage") or item.get("firstimage2"),
            tel=tel, homepage=home, event_place=e_place, playtime=p_time,
            program=prog, usetimefestival=fee, is_ongoing=is_ongoing,
            is_upcoming=is_upcoming, days_until_start=d_start, days_until_end=d_end
        )

    # ==================== 2. DB 저장 및 관리 로직 ====================

    async def save_festival_as_place(
        self,
        db: AsyncSession,
        festival_id: int
    ) -> int:
        """축제를 Place 테이블에 저장하여 여행 일정에 포함시킵니다."""
        # 1. 상세 정보 확인
        detail = await self.tour_api.get_full_place_info(festival_id, 15)
        if not detail:
            raise ValueError("축제 정보를 찾을 수 없습니다")

        # 2. 중복 체크
        title = detail.get("title", "")
        existing = await db.execute(select(Place).where(Place.name == title, Place.is_festival == True))
        existing_place = existing.scalar_one_or_none()
        if existing_place:
            return existing_place.id

        # 3. 신규 Place 생성
        place = Place(
            name=title,
            category="축제/행사",
            address=f"{detail.get('addr1', '')} {detail.get('addr2', '')}".strip(),
            latitude=float(detail.get("mapy", 0)) if detail.get("mapy") else 0.0,
            longitude=float(detail.get("mapx", 0)) if detail.get("mapx") else 0.0,
            description=self.tour_api._clean_html(detail.get("overview", "")),
            image_url=detail.get("firstimage"),
            tags=["축제"],
            is_festival=True,
            event_start_date=detail.get("eventstartdate"),
            event_end_date=detail.get("eventenddate"),
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