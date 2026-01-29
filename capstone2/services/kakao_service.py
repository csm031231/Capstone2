# services/kakao_service.py
import httpx
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

# 2. 경로 계산 (좌표 -> 시간/거리)
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
                    return {
                        "duration": summary["duration"], # 초 단위
                        "distance": summary["distance"]  # 미터 단위
                    }
            return {"duration": 0, "distance": 0}
    except Exception as e:
        print(f"DEBUG(Route): 시스템 에러 -> {e}")
        return {"duration": 0, "distance": 0}