import asyncio
from DataCollector.tour_api_service import get_tour_api_service

async def main():
    svc = get_tour_api_service()
    # 검색 시 eventStartDate / eventEndDate가 필수로 요구될 수 있어 범위를 지정합니다
    items = await svc.search_festivals(page=1, num_of_rows=5, event_start_date="20260101", event_end_date="20261231")
    if not items:
        print("검색 결과 없음")
        return
    print("sample item:", items[0])
    cid = int(items[0].get("contentid", 0))
    print("using contentid:", cid)
    detail = await svc.get_full_place_info(cid, 15)
    print("detail keys:", list(detail.keys()))
    print(detail)

if __name__ == "__main__":
    asyncio.run(main())
