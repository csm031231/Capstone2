import httpx
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime

from core.config import get_config


class TourAPIService:
    """
    한국관광공사 TourAPI 서비스 (v2)

    API 문서: https://api.visitkorea.or.kr

    엔드포인트:
    - /areaBasedList2: 지역기반 관광정보조회
    - /searchKeyword2: 키워드 검색 조회
    - /detailCommon2: 공통정보조회
    - /detailIntro2: 소개정보조회 (운영시간, 휴무일 등)
    - /detailInfo2: 반복정보조회
    - /detailImage2: 이미지정보조회
    - /areaCode2: 지역코드조회
    - /categoryCode2: 서비스분류코드조회
    """

    BASE_URL = "https://apis.data.go.kr/B551011/KorService2"

    # 콘텐츠 타입 코드
    CONTENT_TYPE = {
        "관광지": 12,
        "문화시설": 14,
        "축제공연행사": 15,
        "여행코스": 25,
        "레포츠": 28,
        "숙박": 32,
        "쇼핑": 38,
        "음식점": 39,
    }

    # 지역 코드
    AREA_CODE = {
        "서울": 1,
        "인천": 2,
        "대전": 3,
        "대구": 4,
        "광주": 5,
        "부산": 6,
        "울산": 7,
        "세종": 8,
        "경기": 31,
        "강원": 32,
        "충북": 33,
        "충남": 34,
        "경북": 35,
        "경남": 36,
        "전북": 37,
        "전남": 38,
        "제주": 39,
    }

    def __init__(self):
        config = get_config()
        self.api_key = config.tour_api_key

    async def search_places(
        self,
        area_code: int,
        content_type_id: Optional[int] = None,
        keyword: Optional[str] = None,
        page: int = 1,
        num_of_rows: int = 50
    ) -> List[Dict[str, Any]]:
        """
        지역별 관광지 검색

        Args:
            area_code: 지역 코드 (AREA_CODE 참조)
            content_type_id: 콘텐츠 타입 (CONTENT_TYPE 참조)
            keyword: 검색 키워드
            page: 페이지 번호
            num_of_rows: 한 페이지 결과 수

        Returns:
            관광지 목록
        """
        if keyword:
            endpoint = f"{self.BASE_URL}/searchKeyword2"
            params = {
                "serviceKey": self.api_key,
                "numOfRows": num_of_rows,
                "pageNo": page,
                "MobileOS": "ETC",
                "MobileApp": "TravelApp",
                "_type": "json",
                "keyword": keyword,
                "areaCode": area_code,
            }
        else:
            endpoint = f"{self.BASE_URL}/areaBasedList2"
            params = {
                "serviceKey": self.api_key,
                "numOfRows": num_of_rows,
                "pageNo": page,
                "MobileOS": "ETC",
                "MobileApp": "TravelApp",
                "_type": "json",
                "areaCode": area_code,
                "arrange": "P",  # 인기순
            }

        if content_type_id:
            params["contentTypeId"] = content_type_id

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, params=params)
            response.raise_for_status()
            data = response.json()

        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])

        # 단일 항목인 경우 리스트로 변환
        if isinstance(items, dict):
            items = [items]

        return items or []

    async def get_detail_common(self, content_id: int) -> Optional[Dict[str, Any]]:
        """
        관광지 공통 정보 조회

        Returns:
            - overview: 개요/설명
            - homepage: 홈페이지
            - tel: 전화번호
            - addr1, addr2: 주소
            - zipcode: 우편번호
        """
        endpoint = f"{self.BASE_URL}/detailCommon2"
        params = {
            "serviceKey": self.api_key,
            "MobileOS": "ETC",
            "MobileApp": "TravelApp",
            "_type": "json",
            "contentId": content_id,
            "defaultYN": "Y",
            "overviewYN": "Y",
            "addrinfoYN": "Y",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, params=params)
            response.raise_for_status()
            data = response.json()

        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])

        if isinstance(items, list) and items:
            return items[0]
        elif isinstance(items, dict):
            return items

        return None

    async def get_detail_intro(
        self,
        content_id: int,
        content_type_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        관광지 소개 정보 조회 (운영시간, 휴무일, 입장료 등)

        Returns (content_type에 따라 다름):
            관광지(12):
                - usetime: 이용시간
                - restdate: 휴무일
                - infocenter: 문의처

            음식점(39):
                - opentimefood: 영업시간
                - restdatefood: 휴무일
                - firstmenu: 대표메뉴

            문화시설(14):
                - usetime: 이용시간
                - restdate: 휴무일
                - usefee: 이용요금
        """
        endpoint = f"{self.BASE_URL}/detailIntro2"
        params = {
            "serviceKey": self.api_key,
            "MobileOS": "ETC",
            "MobileApp": "TravelApp",
            "_type": "json",
            "contentId": content_id,
            "contentTypeId": content_type_id,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(endpoint, params=params)
            response.raise_for_status()
            data = response.json()

        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])

        if isinstance(items, list) and items:
            return items[0]
        elif isinstance(items, dict):
            return items

        return None

    async def get_full_place_info(
        self,
        content_id: int,
        content_type_id: int
    ) -> Dict[str, Any]:
        """
        관광지 전체 정보 조회 (공통 + 소개)
        """
        common, intro = await asyncio.gather(
            self.get_detail_common(content_id),
            self.get_detail_intro(content_id, content_type_id),
            return_exceptions=True
        )

        result = {}

        if isinstance(common, dict):
            result.update(common)

        if isinstance(intro, dict):
            result.update(intro)

        return result

    def parse_place_data(
        self,
        item: Dict[str, Any],
        detail: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        API 응답을 Place 모델 형식으로 변환
        """
        content_type_id = int(item.get("contenttypeid", 12))

        # 카테고리 매핑
        category_map = {
            12: "관광지",
            14: "문화시설",
            15: "축제/행사",
            25: "여행코스",
            28: "레포츠",
            32: "숙박",
            38: "쇼핑",
            39: "맛집",
        }

        # 기본 정보
        result = {
            "name": item.get("title", ""),
            "category": category_map.get(content_type_id, "기타"),
            "address": f"{item.get('addr1', '')} {item.get('addr2', '')}".strip(),
            "latitude": float(item.get("mapy", 0)) if item.get("mapy") else 0,
            "longitude": float(item.get("mapx", 0)) if item.get("mapx") else 0,
            "image_url": item.get("firstimage") or item.get("firstimage2"),
            "content_id": int(item.get("contentid", 0)),
            "content_type_id": content_type_id,
        }

        # 상세 정보 병합
        if detail:
            # 설명
            result["description"] = self._clean_html(detail.get("overview", ""))

            # 운영시간 (콘텐츠 타입별로 다른 필드)
            operating_hours = (
                detail.get("usetime") or
                detail.get("opentimefood") or
                detail.get("usetimeculture") or
                detail.get("opentime") or
                ""
            )
            result["operating_hours"] = self._clean_html(operating_hours)

            # 휴무일
            closed_days = (
                detail.get("restdate") or
                detail.get("restdatefood") or
                detail.get("restdateculture") or
                ""
            )
            result["closed_days"] = self._clean_html(closed_days)

            # 입장료/이용요금
            fee_info = (
                detail.get("usefee") or
                detail.get("usetimeculture") or
                detail.get("parking") or
                ""
            )
            # 입장료 정보가 없으면 무료로 추정하지 않음
            result["fee_info"] = self._clean_html(fee_info) if fee_info else None

        # 태그 생성 (카테고리 + 지역)
        tags = [result["category"]]
        addr = result.get("address", "")
        for region in ["서울", "부산", "제주", "강원", "경주", "전주", "여수"]:
            if region in addr:
                tags.append(region)
                break

        result["tags"] = tags

        return result

    def _clean_html(self, text: str) -> str:
        """HTML 태그 및 불필요한 문자 제거"""
        if not text:
            return ""

        import re
        # HTML 태그 제거
        text = re.sub(r'<[^>]+>', '', text)
        # HTML 엔티티 변환
        text = text.replace("&nbsp;", " ")
        text = text.replace("&amp;", "&")
        text = text.replace("&lt;", "<")
        text = text.replace("&gt;", ">")
        text = text.replace("&quot;", '"')
        # 연속 공백 제거
        text = re.sub(r'\s+', ' ', text)
        return text.strip()


# 싱글톤 인스턴스
_tour_api_instance = None


def get_tour_api_service() -> TourAPIService:
    global _tour_api_instance
    if _tour_api_instance is None:
        _tour_api_instance = TourAPIService()
    return _tour_api_instance
