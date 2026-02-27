import httpx
import asyncio
from typing import List, Optional, Dict, Any

from core.config import get_config


class TourAPIRateLimitError(Exception):
    """TourAPI 요청 제한 에러"""
    pass


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
        # 간단한 in-memory TTL 캐시 for detail lookups
        self._detail_cache: dict = {}
        self._cache_lock = asyncio.Lock()
        # 캐시 TTL 초 (기본 6시간)
        self._detail_cache_ttl = 60 * 60 * 6
        # 재사용 가능한 Async HTTP 클라이언트 생성 (커넥션 풀 재활용)
        # limits는 필요 시 환경에 맞게 조정
        self._client = httpx.AsyncClient(timeout=30.0)

        # 검색 결과 캐시 (간단한 요청 레벨 캐시, 기본 5분)
        self._search_cache: dict = {}
        self._search_cache_ttl = 60 * 5

    async def _get_with_fallback(self, endpoint: str, params: dict):
        """기본 요청 수행. 404 발생 시 KorService2 -> KorService로 재시도."""
        try:
            response = await self._client.get(endpoint, params=params)
        except Exception:
            raise

        # 404인 경우 'KorService2' 대신 'KorService'로 재시도
        if response.status_code == 404 and 'KorService2' in endpoint:
            alt = endpoint.replace('KorService2', 'KorService')
            print(f"WARN TourAPI: endpoint returned 404, retrying with {alt}")
            try:
                response = await self._client.get(alt, params=params)
            except Exception:
                raise

        return response

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

        t0 = asyncio.get_event_loop().time()
        try:
            response = await self._get_with_fallback(endpoint, params)
            response.raise_for_status()
            data = response.json()
        finally:
            t1 = asyncio.get_event_loop().time()
            print(f"DEBUG TourAPI.search_places: elapsed={(t1-t0):.3f}s endpoint={endpoint}")

        items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])

        # 단일 항목인 경우 리스트로 변환
        if isinstance(items, dict):
            items = [items]

        return items or []
    
    async def search_festivals(
        self,
        area_code: Optional[int] = None,
        event_start_date: Optional[str] = None,
        event_end_date: Optional[str] = None,
        page: int = 1,
        num_of_rows: int = 50
    ) -> List[Dict[str, Any]]:
        """
        축제/행사 검색

        Args:
            area_code: 지역 코드 (선택, 없으면 전국)
            event_start_date: 행사 시작일 (YYYYMMDD 형식)
            event_end_date: 행사 종료일 (YYYYMMDD 형식)
            page: 페이지 번호
            num_of_rows: 한 페이지 결과 수

        Returns:
            축제/행사 목록
        """
        endpoint = f"{self.BASE_URL}/searchFestival2"
        params = {
            "serviceKey": self.api_key,
            "numOfRows": num_of_rows,
            "pageNo": page,
            "MobileOS": "ETC",
            "MobileApp": "TravelApp",
            "_type": "json",
            "arrange": "A",  # 제목순 정렬
        }

        if area_code:
            params["areaCode"] = area_code

        if event_start_date:
            params["eventStartDate"] = event_start_date

        if event_end_date:
            params["eventEndDate"] = event_end_date

        print(f"DEBUG TourAPI: 요청 URL = {endpoint}")
        print(f"DEBUG TourAPI: 파라미터 = {params}")

        # 캐시 키 생성
        import json
        cache_key = f"searchFestival:{endpoint}:{json.dumps(params, sort_keys=True, ensure_ascii=False)}"

        # 캐시 확인
        async with self._cache_lock:
            cached = self._search_cache.get(cache_key)
            if cached:
                ts, value = cached
                import time
                if time.time() - ts < self._search_cache_ttl:
                    print("DEBUG TourAPI.search_festivals: cache hit", cache_key)
                    return value
                else:
                    try:
                        del self._search_cache[cache_key]
                    except KeyError:
                        pass

        t0 = asyncio.get_event_loop().time()
        try:
            response = await self._get_with_fallback(endpoint, params)
            print(f"DEBUG TourAPI: 응답 상태 = {response.status_code}")
            response.raise_for_status()
            data = response.json()
            print("DEBUG TourAPI: 전체 응답 =", data)

            header = data.get("response", {}).get("header", {})
            result_code = header.get("resultCode") or data.get("resultCode")
            result_msg = header.get("resultMsg") or data.get("resultMsg")
            print(f"DEBUG TourAPI: 결과 코드 = {result_code}, 메시지 = {result_msg}")

            if result_code and str(result_code) not in ("00", "0000", "0"):
                print(f"ERROR TourAPI: API error {result_code} - {result_msg}")
                return []

            items = data.get("response", {}).get("body", {}).get("items", {}).get("item", [])
            if isinstance(items, dict):
                items = [items]

            print(f"DEBUG TourAPI: 파싱된 아이템 수 = {len(items) if items else 0}")

            # 결과를 캐시에 저장
            async with self._cache_lock:
                import time
                try:
                    self._search_cache[cache_key] = (time.time(), items or [])
                except Exception:
                    pass

            return items or []
        except Exception as e:
            print(f"ERROR TourAPI: {e}")
            import traceback
            traceback.print_exc()
            return []
        finally:
            t1 = asyncio.get_event_loop().time()
            print(f"DEBUG TourAPI.search_festivals: elapsed={(t1-t0):.3f}s")

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

        t0 = asyncio.get_event_loop().time()
        try:
            response = await self._get_with_fallback(endpoint, params)
            response.raise_for_status()
            data = response.json()
        finally:
            t1 = asyncio.get_event_loop().time()
            print(f"DEBUG TourAPI.get_detail_common: elapsed={(t1-t0):.3f}s contentId={content_id}")

        # TourAPI는 결과 없을 때 items를 빈 문자열("")로 반환하는 경우가 있어
        # items 컨테이너를 먼저 안전하게 추출하고 타입을 검사한다.
        items_container = data.get("response", {}).get("body", {}).get("items") or {}
        if not isinstance(items_container, dict):
            return None

        items = items_container.get("item", [])
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

        t0 = asyncio.get_event_loop().time()
        try:
            response = await self._get_with_fallback(endpoint, params)
            response.raise_for_status()
            data = response.json()
        finally:
            t1 = asyncio.get_event_loop().time()
            print(f"DEBUG TourAPI.get_detail_intro: elapsed={(t1-t0):.3f}s contentId={content_id}")

        # 안전한 items 처리: 빈 문자열 또는 비정상 구조에 대비
        items_container = data.get("response", {}).get("body", {}).get("items") or {}
        if not isinstance(items_container, dict):
            return None

        items = items_container.get("item", [])
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
        # 캐시 키
        cache_key = f"{content_id}_{content_type_id}"

        # 1) 캐시 확인
        async with self._cache_lock:
            cached = self._detail_cache.get(cache_key)
            if cached:
                ts, value = cached
                # 만료 검사
                import time
                if time.time() - ts < self._detail_cache_ttl:
                    return value
                else:
                    # 만료된 항목 삭제
                    try:
                        del self._detail_cache[cache_key]
                    except KeyError:
                        pass

        # 2) 실제 요청 (병렬로 공통/소개 요청 수행)
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

        # 3) 캐시에 저장
        async with self._cache_lock:
            import time
            try:
                self._detail_cache[cache_key] = (time.time(), result)
            except Exception:
                # 캐시 저장 실패는 무시 (메모리 제한 등)
                pass

        return result

    # cat3 소분류 코드 → 한글 태그 매핑
    CAT3_TAG_MAP = {
        # 관광지(12) 하위
        "A01010100": "자연경관", "A01010200": "해변", "A01010300": "산",
        "A01010400": "호수", "A01010500": "강", "A01010600": "폭포",
        "A01010700": "해안", "A01010800": "섬", "A01010900": "계곡",
        "A01011000": "온천", "A01011100": "동굴", "A01011200": "수목원",
        "A01011300": "공원",
        "A02010100": "역사유적", "A02010200": "사찰", "A02010300": "고궁",
        "A02010400": "성", "A02010500": "탑", "A02010600": "전통건축",
        "A02010700": "마을", "A02010800": "박물관", "A02010900": "기념관",
        "A02020100": "테마공원", "A02020200": "전시관", "A02020300": "미술관",
        "A02020400": "공연장", "A02020500": "체험관", "A02020600": "캠핑",
        "A02020700": "수상레포츠", "A02020800": "레저스포츠",
        "A02030100": "유원지", "A02030200": "관광지",
        "A02030300": "거리", "A02030400": "야시장",
        "A02050100": "전망대", "A02050200": "야경",
        "A02060100": "카페거리", "A02060200": "벽화마을",
        # 음식점(39) 하위
        "A05020100": "한식", "A05020200": "서양식", "A05020300": "일식",
        "A05020400": "중식", "A05020500": "분식", "A05020600": "카페",
        "A05020700": "해산물", "A05020800": "고기",
        "A05020900": "간식",
    }

    # 설명(overview)에서 키워드 추출용 매핑
    KEYWORD_TAG_MAP = {
        "바다": ["바다", "해변", "해수욕", "해안", "파도", "갯벌"],
        "산": ["산", "등산", "트레킹", "하이킹", "능선", "정상"],
        "역사": ["역사", "유적", "문화재", "조선", "고려", "백제", "신라", "사적"],
        "카페": ["카페", "커피", "디저트", "베이커리", "브런치"],
        "야경": ["야경", "야간", "조명", "밤", "불빛", "라이트업"],
        "힐링": ["힐링", "휴식", "조용", "평화", "명상", "치유", "여유"],
        "액티비티": ["체험", "액티비티", "놀이", "레저", "스포츠", "어드벤처"],
        "전통": ["전통", "한옥", "민속", "공예", "전래"],
        "자연": ["자연", "생태", "숲", "녹지", "습지", "철새"],
        "맛집": ["맛집", "미식", "로컬푸드", "향토", "먹거리", "특산"],
        "포토스팟": ["포토", "인스타", "사진", "경치", "뷰", "전망"],
        "가족": ["가족", "어린이", "키즈", "놀이터", "체험학습"],
        "커플": ["데이트", "커플", "로맨틱", "분위기"],
        "축제": ["축제", "행사", "이벤트", "페스티벌", "공연"],
        "쇼핑": ["쇼핑", "시장", "상점", "기념품", "특산품"],
    }

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

        # Tour API 분류 코드 수집
        result["cat1"] = item.get("cat1")
        result["cat2"] = item.get("cat2")
        result["cat3"] = item.get("cat3")
        result["readcount"] = int(item.get("readcount", 0)) if item.get("readcount") else None

        # 상세 정보 병합 (빈 dict {}도 처리되도록 is not None 체크)
        if detail is not None:
            # 설명
            result["description"] = self._clean_html(detail.get("overview", ""))

            # 전화번호, 홈페이지
            result["tel"] = self._clean_html(detail.get("tel", "")) or None
            result["homepage"] = self._clean_html(detail.get("homepage", "")) or None

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

        # 풍부한 태그 생성
        result["tags"] = self._generate_rich_tags(result)

        return result

    def _generate_rich_tags(self, place_data: Dict[str, Any]) -> List[str]:
        """
        풍부한 태그 생성 (5~10개 목표)

        태그 소스:
        1. 카테고리
        2. 지역
        3. cat3 소분류 코드 매핑
        4. 설명(overview) 키워드 추출
        """
        tags = set()

        # 1. 카테고리
        if place_data.get("category"):
            tags.add(place_data["category"])

        # 2. 지역
        addr = place_data.get("address", "")
        for region in ["서울", "부산", "제주", "강원", "경주", "전주", "여수",
                        "인천", "대구", "광주", "대전", "울산", "세종",
                        "속초", "강릉", "춘천", "수원", "통영", "목포"]:
            if region in addr:
                tags.add(region)
                break

        # 3. cat3 소분류 코드 → 한글 태그
        cat3 = place_data.get("cat3")
        if cat3 and cat3 in self.CAT3_TAG_MAP:
            tags.add(self.CAT3_TAG_MAP[cat3])

        # 4. 설명에서 키워드 추출
        description = place_data.get("description", "")
        if description:
            desc_lower = description.lower()
            for tag_name, keywords in self.KEYWORD_TAG_MAP.items():
                for kw in keywords:
                    if kw in desc_lower:
                        tags.add(tag_name)
                        break

        return list(tags)

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
