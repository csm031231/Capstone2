import asyncio
from DataCollector.tour_api_service import get_tour_api_service

async def main():
    svc = get_tour_api_service()
    cid = 1506389
    print('calling detailCommon...')
    common = await svc.get_detail_common(cid)
    print('common:', common)
    print('calling detailIntro...')
    intro = await svc.get_detail_intro(cid, 15)
    print('intro:', intro)

if __name__ == '__main__':
    asyncio.run(main())
