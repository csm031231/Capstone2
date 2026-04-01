# services/kakao_service.py
import httpx
from typing import Optional
from core.config import get_config

settings = get_config()

# 1. 장소 검색 (키워드 -> 좌표)
async def search_places(keyword: str, page: int = 1, size: int = 5):
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {settings.kakao_rest_api_key}"}
    params = {"query": keyword, "page": page, "size": size}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params)

            # 터미널에 로그 찍기 (디버깅용)
            print(f"DEBUG(Search): 상태코드={response.status_code}")

            if response.status_code == 200:
                return response.json().get("documents", [])
            else:
                print(f"DEBUG(Search): 에러내용={response.text}")
                return []
    except Exception as e:
        print(f"DEBUG(Search): 시스템 에러 -> {e}")
        return []

# 2. 경로 계산 (좌표 -> 시간/거리/도로경로)
async def get_route_info(origin_x: float, origin_y: float, dest_x: float, dest_y: float):
    url = "https://apis-navi.kakaomobility.com/v1/directions"
    headers = {"Authorization": f"KakaoAK {settings.kakao_rest_api_key}"}
    params = {
        "origin": f"{origin_x},{origin_y}",
        "destination": f"{dest_x},{dest_y}",
        "priority": "RECOMMEND"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params)

            print(f"DEBUG(Route): 상태코드={response.status_code}")

            if response.status_code == 200:
                data = response.json()
                routes = data.get("routes", [])
                if routes:
                    summary = routes[0]["summary"]

                    # 도로 경로 좌표 추출
                    # vertexes는 [lng, lat, lng, lat, ...] 플랫 배열 형태로 반환됨
                    road_path = []
                    for section in routes[0].get("sections", []):
                        for road in section.get("roads", []):
                            verts = road.get("vertexes", [])
                            for i in range(0, len(verts) - 1, 2):
                                road_path.append({
                                    "lng": verts[i],
                                    "lat": verts[i + 1]
                                })

                    return {
                        "duration": summary["duration"],  # 초 단위
                        "distance": summary["distance"],  # 미터 단위
                        "road_path": road_path            # 실제 도로 좌표 배열
                    }

        return {"duration": 0, "distance": 0, "road_path": []}
    except Exception as e:
        print(f"DEBUG(Route): 시스템 에러 -> {e}")
        return {"duration": 0, "distance": 0, "road_path": []}


# 3. 역지오코딩 (GPS 좌표 → 주소/지역명)
async def reverse_geocode(latitude: float, longitude: float) -> Optional[dict]:
    """
    GPS 좌표를 주소로 변환 (카카오 좌표-주소 변환 API)

    Returns:
        {"city": "제주시", "district": "애월읍", "full_address": "제주특별자치도 제주시 애월읍"} 또는 None
    """
    url = "https://dapi.kakao.com/v2/local/geo/coord2address.json"
    headers = {"Authorization": f"KakaoAK {settings.kakao_rest_api_key}"}
    params = {"x": longitude, "y": latitude}

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers, params=params)

            if response.status_code != 200:
                return None

            data = response.json()
            documents = data.get("documents", [])
            if not documents:
                return None

            address = documents[0].get("address") or documents[0].get("road_address")
            if not address:
                return None

            region_1 = address.get("region_1depth_name", "")  # 시/도
            region_2 = address.get("region_2depth_name", "")  # 시/군/구
            region_3 = address.get("region_3depth_name", "")  # 읍/면/동

            # city: 시/군/구 우선, 없으면 시/도
            city = region_2 or region_1

            # 제주도처럼 region_1이 실질적인 도시인 경우 처리
            if "특별자치도" in region_1 or "특별자치시" in region_1:
                city = region_2 or region_1

            full_address = " ".join(filter(None, [region_1, region_2, region_3]))

            return {
                "city": city,
                "district": region_3,
                "province": region_1,
                "full_address": full_address
            }

    except Exception as e:
        print(f"DEBUG(ReverseGeocode): 시스템 에러 -> {e}")
        return None