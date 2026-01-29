import asyncio
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.models import Place
from DataCollector.tour_api_service import TourAPIService, get_tour_api_service
from DataCollector.wikipedia_service import WikipediaService, get_wikipedia_service


class DataCollectorService:
    """
    데이터 수집 통합 서비스

    TourAPI + Wikipedia를 결합하여 Place 데이터 수집 및 저장
    """

    def __init__(self):
        self.tour_api = get_tour_api_service()
        self.wiki_service = get_wikipedia_service()

    async def collect_places_by_area(
        self,
        db: AsyncSession,
        area_name: str,
        content_types: Optional[List[str]] = None,
        max_items: int = 100,
        enhance_with_wiki: bool = True
    ) -> Dict[str, Any]:
        """
        지역별 관광지 데이터 수집 및 저장

        Args:
            db: DB 세션
            area_name: 지역명 (서울, 부산, 제주 등)
            content_types: 수집할 콘텐츠 타입 (없으면 전체)
            max_items: 최대 수집 개수
            enhance_with_wiki: Wikipedia로 설명 보강 여부

        Returns:
            수집 결과 요약
        """
        # 지역 코드 확인
        area_code = self.tour_api.AREA_CODE.get(area_name)
        if not area_code:
            return {
                "success": False,
                "message": f"알 수 없는 지역: {area_name}",
                "collected": 0
            }

        # 콘텐츠 타입 설정
        if content_types:
            type_ids = [
                self.tour_api.CONTENT_TYPE.get(ct)
                for ct in content_types
                if ct in self.tour_api.CONTENT_TYPE
            ]
        else:
            # 기본: 관광지, 문화시설, 음식점
            type_ids = [12, 14, 39]

        collected = 0
        skipped = 0
        errors = []

        for content_type_id in type_ids:
            # 페이지별로 데이터 수집
            page = 1
            while collected < max_items:
                try:
                    items = await self.tour_api.search_places(
                        area_code=area_code,
                        content_type_id=content_type_id,
                        page=page,
                        num_of_rows=50
                    )

                    if not items:
                        break

                    for item in items:
                        if collected >= max_items:
                            break

                        result = await self._process_and_save_place(
                            db, item, enhance_with_wiki
                        )

                        if result == "created":
                            collected += 1
                        elif result == "exists":
                            skipped += 1
                        else:
                            errors.append(item.get("title", "Unknown"))

                    page += 1

                    # API 호출 간격 (rate limit 방지)
                    await asyncio.sleep(0.3)

                except Exception as e:
                    errors.append(f"Page {page}: {str(e)}")
                    break

        return {
            "success": True,
            "area": area_name,
            "collected": collected,
            "skipped": skipped,
            "errors": len(errors),
            "error_details": errors[:10] if errors else []
        }

    async def _process_and_save_place(
        self,
        db: AsyncSession,
        item: Dict[str, Any],
        enhance_with_wiki: bool
    ) -> str:
        """
        개별 장소 처리 및 저장

        Returns:
            "created", "exists", "error"
        """
        try:
            content_id = int(item.get("contentid", 0))
            content_type_id = int(item.get("contenttypeid", 12))
            name = item.get("title", "")

            if not content_id or not name:
                return "error"

            # 중복 체크 (이름 + 좌표)
            lat = float(item.get("mapy", 0)) if item.get("mapy") else 0
            lng = float(item.get("mapx", 0)) if item.get("mapx") else 0

            existing = await db.execute(
                select(Place).where(
                    Place.name == name,
                    Place.latitude == lat,
                    Place.longitude == lng
                )
            )
            if existing.scalar_one_or_none():
                return "exists"

            # 상세 정보 조회
            detail = await self.tour_api.get_full_place_info(
                content_id, content_type_id
            )

            # 데이터 파싱
            place_data = self.tour_api.parse_place_data(item, detail)

            # Wikipedia로 설명 보강
            if enhance_with_wiki and (
                not place_data.get("description") or
                len(place_data.get("description", "")) < 50
            ):
                wiki_desc = await self.wiki_service.enhance_description(
                    name,
                    place_data.get("description")
                )
                if wiki_desc:
                    place_data["description"] = wiki_desc

            # 유효한 좌표 확인
            if place_data["latitude"] == 0 or place_data["longitude"] == 0:
                return "error"

            # DB 저장
            place = Place(
                name=place_data["name"],
                category=place_data.get("category"),
                address=place_data.get("address"),
                latitude=place_data["latitude"],
                longitude=place_data["longitude"],
                description=place_data.get("description"),
                tags=place_data.get("tags"),
                image_url=place_data.get("image_url"),
                operating_hours=place_data.get("operating_hours"),
                closed_days=place_data.get("closed_days"),
                fee_info=place_data.get("fee_info"),
            )

            db.add(place)
            await db.commit()

            return "created"

        except Exception as e:
            await db.rollback()
            return "error"

    async def collect_by_keyword(
        self,
        db: AsyncSession,
        keyword: str,
        area_name: Optional[str] = None,
        max_items: int = 50,
        enhance_with_wiki: bool = True
    ) -> Dict[str, Any]:
        """
        키워드로 관광지 검색 및 저장

        Args:
            db: DB 세션
            keyword: 검색 키워드
            area_name: 지역명 (선택)
            max_items: 최대 수집 개수
            enhance_with_wiki: Wikipedia 보강 여부
        """
        area_code = None
        if area_name:
            area_code = self.tour_api.AREA_CODE.get(area_name)

        collected = 0
        skipped = 0
        errors = []

        page = 1
        while collected < max_items:
            try:
                items = await self.tour_api.search_places(
                    area_code=area_code or 0,
                    keyword=keyword,
                    page=page,
                    num_of_rows=50
                )

                if not items:
                    break

                for item in items:
                    if collected >= max_items:
                        break

                    result = await self._process_and_save_place(
                        db, item, enhance_with_wiki
                    )

                    if result == "created":
                        collected += 1
                    elif result == "exists":
                        skipped += 1

                page += 1
                await asyncio.sleep(0.3)

            except Exception as e:
                errors.append(str(e))
                break

        return {
            "success": True,
            "keyword": keyword,
            "area": area_name,
            "collected": collected,
            "skipped": skipped,
            "errors": len(errors)
        }

    async def get_collection_stats(self, db: AsyncSession) -> Dict[str, Any]:
        """현재 수집된 데이터 통계"""
        from sqlalchemy import func

        # 전체 개수
        total = await db.execute(select(func.count()).select_from(Place))
        total_count = total.scalar() or 0

        # 카테고리별 개수
        category_stats = await db.execute(
            select(Place.category, func.count())
            .group_by(Place.category)
        )
        categories = {row[0] or "기타": row[1] for row in category_stats.fetchall()}

        # 지역별 개수 (주소에서 추출)
        regions = {}
        for region in ["서울", "부산", "제주", "강원", "경주", "전주", "여수", "인천", "대구", "광주"]:
            count = await db.execute(
                select(func.count())
                .select_from(Place)
                .where(Place.address.contains(region))
            )
            cnt = count.scalar() or 0
            if cnt > 0:
                regions[region] = cnt

        return {
            "total": total_count,
            "by_category": categories,
            "by_region": regions
        }


# 싱글톤 인스턴스
_collector_instance = None


def get_collector_service() -> DataCollectorService:
    global _collector_instance
    if _collector_instance is None:
        _collector_instance = DataCollectorService()
    return _collector_instance
