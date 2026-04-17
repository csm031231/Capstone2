"""
이미지 없는 여행지 업데이트 스크립트
실행: python scripts/fill_missing_images.py

Tour API의 detailImage2를 사용하여 image_url이 없는 places를 업데이트
"""
import asyncio
import sys
import os
from datetime import datetime

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import get_config
from core.database import init_db
from core import database
from sqlalchemy import select, update
from core.models import Place
from DataCollector.tour_api_service import TourAPIService, get_tour_api_service


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


async def update_missing_images():
    """image_url이 없는 places를 Tour API로 업데이트"""
    tour_api = get_tour_api_service()
    updated_count = 0
    error_count = 0

    log("=" * 50)
    log("이미지 없는 여행지 업데이트 시작")
    log("=" * 50)

    async with database.DBSessionLocal() as session:
        # image_url이 null이고 content_id가 있는 places 조회 (테스트용으로 10개만)
        stmt = select(Place).where(
            Place.image_url.is_(None),
            Place.content_id.isnot(None)
        ).limit(10)
        result = await session.execute(stmt)
        places = result.scalars().all()

        log(f"업데이트 대상: {len(places)}개")

        for place in places:
            try:
                # Tour API에서 이미지 정보 조회
                image_url = None
                images = await tour_api.get_detail_image(place.content_id)
                if images and len(images) > 0:
                    image_url = images[0].get("originimgurl") or images[0].get("smallimageurl")

                if image_url:
                    update_stmt = (
                        update(Place)
                        .where(Place.id == place.id)
                        .values(image_url=image_url)
                    )
                    await session.execute(update_stmt)
                    updated_count += 1
                    log(f"  [{place.name}] 이미지 업데이트 성공: {image_url}")
                else:
                    log(f"  [{place.name}] 이미지 없음 - Tour API/픽사베이 모두 실패")
                    error_count += 1

                # API 호출 간격 (rate limit 방지)
                await asyncio.sleep(0.5)

            except Exception as e:
                log(f"  [{place.name}] 오류: {e}")
                error_count += 1

        # 커밋
        await session.commit()

    log(f"업데이트 완료 - 성공: {updated_count}개, 실패: {error_count}개")
    return updated_count


async def main():
    # DB 초기화
    config = get_config()
    init_db(config)

    # 업데이트 실행
    await update_missing_images()


if __name__ == "__main__":
    asyncio.run(main())