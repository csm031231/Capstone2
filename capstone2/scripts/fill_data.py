"""
데이터 보완 스크립트
실행: python scripts/fill_data.py

순서:
  1. 음식점 데이터 수집 (전체 지역)
  2. 경북/경남/전남 관광지 보완 수집
  3. 기존 데이터 description 업데이트 (배치 반복)
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
from DataCollector.collector_service import get_collector_service


def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


async def step1_collect_food():
    """음식점 데이터 수집 - 전체 지역"""
    areas = ["서울", "부산", "제주", "강원", "전북", "인천", "경기", "경북", "경남", "전남"]
    collector = get_collector_service()
    total = 0

    log("=" * 50)
    log("STEP 1: 음식점 데이터 수집 시작")
    log("=" * 50)

    for area in areas:
        try:
            async with database.DBSessionLocal() as session:
                result = await collector.collect_places_by_area(
                    db=session,
                    area_name=area,
                    content_types=["음식점"],
                    max_items_per_type=300,
                    enhance_with_wiki=False  # 속도 우선
                )
                collected = result.get("collected", 0)
                skipped  = result.get("skipped", 0)
                total += collected
                log(f"  [{area}] 음식점 신규: {collected}개, 스킵: {skipped}개")
        except Exception as e:
            log(f"  [{area}] 오류: {e}")
        # 지역 간 요청 간격
        await asyncio.sleep(2)

    log(f"STEP 1 완료 - 총 신규 {total}개 추가\n")
    return total


async def step2_collect_weak_regions():
    """경북/경남/전남 관광지+문화시설 보완"""
    areas = ["경북", "경남", "전남"]
    collector = get_collector_service()
    total = 0

    log("=" * 50)
    log("STEP 2: 부족 지역 관광지 보완 수집")
    log("=" * 50)

    for area in areas:
        try:
            async with database.DBSessionLocal() as session:
                result = await collector.collect_places_by_area(
                    db=session,
                    area_name=area,
                    content_types=["관광지", "문화시설"],
                    max_items_per_type=300,
                    enhance_with_wiki=False
                )
                collected = result.get("collected", 0)
                skipped  = result.get("skipped", 0)
                total += collected
                log(f"  [{area}] 신규: {collected}개, 스킵: {skipped}개")
        except Exception as e:
            log(f"  [{area}] 오류: {e}")
        await asyncio.sleep(2)

    log(f"STEP 2 완료 - 총 신규 {total}개 추가\n")
    return total


async def step3_update_descriptions():
    """기존 데이터 description 업데이트 (배치 반복)"""
    collector = get_collector_service()
    batch_size = 50
    total_updated = 0
    call_count = 0
    no_progress_count = 0  # 진행 없는 배치 연속 횟수

    log("=" * 50)
    log("STEP 3: 기존 데이터 description 업데이트 시작")
    log(f"  배치 크기: {batch_size}개 / Wikipedia 보강 OFF (속도 우선)")
    log("=" * 50)

    while True:
        try:
            async with database.DBSessionLocal() as session:
                result = await collector.update_missing_data(
                    db=session,
                    batch_size=batch_size,
                    enhance_with_wiki=False
                )

            call_count += 1
            updated   = result.get("updated", 0)
            skipped   = result.get("skipped_no_desc", 0)
            errors    = result.get("errors", 0)
            remaining = result.get("remaining", 0)
            processed = result.get("processed", 0)
            total_updated += updated

            log(f"  배치 #{call_count:3d} | 업데이트: {updated:3d} | 스킵: {skipped:3d} | "
                f"오류: {errors:2d} | 남은 대상: {remaining:,}개 | 누적: {total_updated:,}개")

            # 더 이상 처리할 게 없으면 종료
            if remaining == 0 or processed == 0:
                log("  남은 대상 없음 - 완료!")
                break

            # 진행 없는 배치 체크 (업데이트+스킵=0이면 API 오류만 발생)
            if updated == 0 and skipped == 0:
                no_progress_count += 1
                if no_progress_count >= 3:
                    log("  연속 3회 진행 없음 - 60초 대기 후 재시도 (API rate limit 회복)")
                    await asyncio.sleep(60)
                    no_progress_count = 0
                else:
                    await asyncio.sleep(10)
            else:
                no_progress_count = 0

            # 오류 많을 시 잠깐 대기
            if errors >= batch_size * 0.5:
                log("  오류 많음 - 20초 대기 후 재시도")
                await asyncio.sleep(20)

        except Exception as e:
            log(f"  배치 #{call_count} 예외: {e}")
            log("  30초 대기 후 재시도...")
            await asyncio.sleep(30)

    log(f"STEP 3 완료 - 총 {total_updated:,}개 description 업데이트\n")
    return total_updated


async def main():
    log("데이터 보완 스크립트 시작")
    log(f"시작 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # DB 초기화
    config = get_config()
    init_db(config)
    log("DB 연결 완료\n")

    # STEP 1: 음식점 수집
    await step1_collect_food()

    # STEP 2: 부족 지역 보완
    await step2_collect_weak_regions()

    # STEP 3: description 업데이트
    await step3_update_descriptions()

    log("=" * 50)
    log(f"전체 완료! 종료 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
