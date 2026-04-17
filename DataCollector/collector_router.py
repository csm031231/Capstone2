import logging
import asyncio
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel, Field
from typing import List, Optional

from core.database import provide_session
from core import database as core_database
from core.models import User, Place
from User.user_router import get_current_user
from DataCollector.collector_service import get_collector_service
from DataCollector.tour_api_service import get_tour_api_service, generate_tags_from_place

logger = logging.getLogger(__name__)

# 인덱스 빌드 진행 상태 (전역)
_index_build_status = {
    "running": False,
    "indexed": 0,
    "skipped": 0,
    "errors": 0,
    "total": 0,
    "message": "대기 중"
}


router = APIRouter(
    prefix="/data",
    tags=["data-collector"]
)


# ==================== DTO ====================

class CollectByAreaRequest(BaseModel):
    """지역별 데이터 수집 요청"""
    area_name: str = Field(..., description="지역명 (서울, 부산, 제주 등)")
    content_types: Optional[List[str]] = Field(
        None,
        description="수집할 타입 (관광지, 문화시설, 음식점 등). 미지정 시 관광지+문화시설+음식점"
    )
    max_items_per_type: int = Field(
        default=300, ge=10, le=1000,
        description="타입별 최대 수집 개수 (타입마다 독립 적용)"
    )
    enhance_with_wiki: bool = Field(default=True, description="Wikipedia로 설명 보강")


class CollectByKeywordRequest(BaseModel):
    """키워드 검색 데이터 수집 요청"""
    keyword: str = Field(..., min_length=1)
    area_name: Optional[str] = None
    max_items: int = Field(default=50, ge=10, le=200)
    enhance_with_wiki: bool = True


# ==================== API ====================

@router.get("/stats")
async def get_stats(
    db: AsyncSession = Depends(provide_session)
):
    """
    현재 수집된 데이터 통계

    인증 불필요
    """
    collector = get_collector_service()
    return await collector.get_collection_stats(db)


@router.get("/areas")
async def get_available_areas():
    """사용 가능한 지역 코드 목록"""
    tour_api = get_tour_api_service()
    return {
        "areas": list(tour_api.AREA_CODE.keys()),
        "content_types": list(tour_api.CONTENT_TYPE.keys())
    }


@router.post("/collect/area")
async def collect_by_area(
    request: CollectByAreaRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    지역별 관광지 데이터 수집

    - 한국관광공사 TourAPI에서 데이터 가져오기
    - Wikipedia로 부족한 설명 보강
    - Place 테이블에 저장

    지원 지역: 서울, 부산, 제주, 강원, 경기, 인천, 대구, 광주, 대전, 울산, 세종,
              경북, 경남, 전북, 전남, 충북, 충남
    """
    collector = get_collector_service()

    result = await collector.collect_places_by_area(
        db=db,
        area_name=request.area_name,
        content_types=request.content_types,
        max_items_per_type=request.max_items_per_type,
        enhance_with_wiki=request.enhance_with_wiki
    )

    return result


@router.post("/collect/keyword")
async def collect_by_keyword(
    request: CollectByKeywordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    키워드로 관광지 검색 및 저장

    예: "해운대", "경복궁", "한라산"
    """
    collector = get_collector_service()

    result = await collector.collect_by_keyword(
        db=db,
        keyword=request.keyword,
        area_name=request.area_name,
        max_items=request.max_items,
        enhance_with_wiki=request.enhance_with_wiki
    )

    return result


class CollectBulkRequest(BaseModel):
    """여러 지역 일괄 수집 요청"""
    areas: List[str] = Field(
        default=["서울", "부산", "제주", "강원", "경북", "전남", "경남", "전북", "인천", "경기"],
        description="수집할 지역 목록"
    )
    max_items_per_type: int = Field(
        default=300, ge=10, le=1000,
        description="지역별·타입별 최대 수집 개수"
    )
    enhance_with_wiki: bool = Field(default=True)


@router.post("/collect/bulk")
async def collect_bulk(
    request: CollectBulkRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    여러 지역 일괄 수집

    기본 10개 지역: 서울, 부산, 제주, 강원, 경북, 전남, 경남, 전북, 인천, 경기
    각 지역마다 관광지/문화시설/음식점을 타입별 독립 카운터로 수집
    """
    collector = get_collector_service()
    results = []

    for area in request.areas:
        result = await collector.collect_places_by_area(
            db=db,
            area_name=area,
            max_items_per_type=request.max_items_per_type,
            enhance_with_wiki=request.enhance_with_wiki
        )
        results.append(result)

    total_collected = sum(r.get("collected", 0) for r in results)

    return {
        "success": True,
        "total_collected": total_collected,
        "by_area": results
    }


# ==================== 기존 데이터 보완 ====================

@router.post("/update/missing")
async def update_missing_descriptions(
    batch_size: int = Query(default=100, ge=10, le=500, description="한 번에 처리할 개수"),
    enhance_with_wiki: bool = Query(default=True, description="Wikipedia 보강 여부"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    기존 데이터 보완 - description 없는 places에 상세 정보 채우기

    - description이 NULL인 장소를 batch_size개씩 처리
    - TourAPI 재호출 → description, 운영시간, 휴무일, 요금 업데이트
    - description 기반 tags 재생성
    - 전체 20,957개 처리 시 여러 번 반복 호출 필요 (remaining 확인)

    예시: batch_size=100이면 한 번 호출 시 100개 처리, remaining이 0이 될 때까지 반복
    """
    collector = get_collector_service()
    return await collector.update_missing_data(
        db=db,
        batch_size=batch_size,
        enhance_with_wiki=enhance_with_wiki
    )


# ==================== FAISS 인덱싱 ====================

async def _run_build_index(region: Optional[str], force_rebuild: bool):
    """백그라운드에서 실행되는 FAISS 인덱스 빌드 작업"""
    global _index_build_status

    import httpx
    import faiss as faiss_lib
    from PIL import Image
    from io import BytesIO
    from Vision.clip_service import get_clip_service
    from Vision.faiss_index import get_faiss_index, PlaceVector

    _index_build_status["running"] = True
    _index_build_status["indexed"] = 0
    _index_build_status["skipped"] = 0
    _index_build_status["errors"] = 0
    _index_build_status["message"] = "초기화 중..."

    try:
        clip = get_clip_service()
        faiss_index = get_faiss_index()

        if force_rebuild:
            faiss_index.index = faiss_lib.IndexFlatIP(faiss_index.dimension)
            faiss_index.metadata = []
            logger.info("FAISS 인덱스 초기화 완료 - 전체 재빌드 시작")

        indexed_ids = {m["place_id"] for m in faiss_index.metadata}

        # 별도 DB 세션 사용 (백그라운드 태스크는 요청 세션 사용 불가)
        # 세션 닫히기 전에 dict로 변환 → DetachedInstanceError 방지
        async with core_database.DBSessionLocal() as db:
            query = select(Place).where(Place.image_url.isnot(None))
            if region:
                query = query.where(Place.address.contains(region))
            result = await db.execute(query)
            places = [
                {
                    "id": p.id,
                    "name": p.name,
                    "image_url": p.image_url,
                    "tags": p.tags,
                    "category": p.category,
                    "address": p.address or "",
                    "latitude": p.latitude,
                    "longitude": p.longitude,
                    "cat1": p.cat1,
                    "cat2": p.cat2,
                    "cat3": p.cat3,
                    "description": p.description,
                }
                for p in result.scalars().all()
            ]

        _index_build_status["total"] = len(places)
        _index_build_status["message"] = f"총 {len(places)}개 장소 인덱싱 시작"
        logger.info(f"FAISS 빌드 시작: {len(places)}개 장소")

        indexed = 0
        skipped = 0
        errors = 0

        async with httpx.AsyncClient(timeout=15.0) as client:
            for place in places:
                if place["id"] in indexed_ids:
                    skipped += 1
                    _index_build_status["skipped"] = skipped
                    continue

                try:
                    resp = await client.get(place["image_url"])
                    resp.raise_for_status()
                    img = Image.open(BytesIO(resp.content)).convert("RGB")

                    vector = clip.get_image_embedding(img)

                    rich_tags = generate_tags_from_place(
                        cat1=place["cat1"],
                        cat2=place["cat2"],
                        cat3=place["cat3"],
                        category=place["category"],
                        address=place["address"],
                        description=place["description"],
                        existing_tags=place["tags"],
                    )

                    place_vector = PlaceVector(
                        place_id=place["id"],
                        name=place["name"],
                        image_url=place["image_url"],
                        tags=rich_tags,
                        category=place["category"] or "기타",
                        address=place["address"] or "",
                        latitude=place["latitude"],
                        longitude=place["longitude"]
                    )
                    faiss_index.add_place(place_vector, vector)
                    indexed += 1

                    # 상태 업데이트
                    _index_build_status["indexed"] = indexed
                    _index_build_status["message"] = (
                        f"진행 중: {indexed + skipped}/{len(places)} "
                        f"(인덱싱 {indexed} / 스킵 {skipped} / 오류 {errors})"
                    )

                    # 100개마다 중간 저장
                    if indexed % 100 == 0:
                        faiss_index.save()
                        logger.info(f"FAISS 중간 저장: {indexed}개 완료")

                except Exception as e:
                    errors += 1
                    _index_build_status["errors"] = errors
                    logger.warning(f"인덱싱 실패 {place['name']}: {e}")
                    continue

        if indexed > 0:
            faiss_index.save()

        _index_build_status["message"] = (
            f"완료: 인덱싱 {indexed}개 / 스킵 {skipped}개 / 오류 {errors}개 "
            f"(총 인덱스: {faiss_index.get_total_count()}개)"
        )
        logger.info(f"FAISS 빌드 완료: {_index_build_status['message']}")

    except Exception as e:
        _index_build_status["message"] = f"빌드 실패: {str(e)}"
        logger.error(f"FAISS 빌드 중 오류: {e}")
    finally:
        _index_build_status["running"] = False


@router.post("/index/build")
async def build_faiss_index(
    background_tasks: BackgroundTasks,
    region: Optional[str] = None,
    force_rebuild: bool = False,
    current_user: User = Depends(get_current_user),
):
    """
    FAISS 벡터 인덱스 구축 (백그라운드 실행)

    - region: 특정 지역만 인덱싱 (없으면 전체)
    - force_rebuild: True면 기존 인덱스를 초기화하고 전체 재빌드
    - 백그라운드에서 실행되며 GET /data/index/status 로 진행상황 확인 가능
    """
    global _index_build_status

    if _index_build_status["running"]:
        return {
            "success": False,
            "message": "이미 빌드가 진행 중입니다. GET /data/index/status 로 확인하세요.",
            "status": _index_build_status
        }

    background_tasks.add_task(_run_build_index, region, force_rebuild)

    return {
        "success": True,
        "message": "인덱스 빌드를 백그라운드에서 시작했습니다. GET /data/index/status 로 진행상황을 확인하세요.",
        "region": region or "전체",
        "force_rebuild": force_rebuild
    }


@router.get("/index/status")
async def get_index_build_status():
    """FAISS 인덱스 빌드 진행상황 조회"""
    return _index_build_status


@router.get("/index/stats")
async def get_index_stats():
    """FAISS 인덱스 현황 조회"""
    from Vision.faiss_index import get_faiss_index

    faiss_index = get_faiss_index()

    return {
        "total_vectors": faiss_index.get_total_count(),
        "dimension": faiss_index.dimension,
        "index_path": faiss_index.index_path
    }


@router.get("/places")
async def list_places(
    region: Optional[str] = None,
    category: Optional[str] = None,
    skip: int = 0,
    limit: int = 20,
    db: AsyncSession = Depends(provide_session)
):
    """
    수집된 장소 목록 조회

    인증 불필요
    """
    from sqlalchemy import select
    from core.models import Place

    query = select(Place)

    if region:
        query = query.where(Place.address.contains(region))

    if category:
        query = query.where(Place.category == category)

    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    places = result.scalars().all()

    return {
        "places": [
            {
                "id": p.id,
                "name": p.name,
                "category": p.category,
                "address": p.address,
                "latitude": p.latitude,
                "longitude": p.longitude,
                "image_url": p.image_url,
                "tags": p.tags,
                "operating_hours": p.operating_hours,
                "closed_days": p.closed_days,
                "description": p.description[:200] + "..." if p.description and len(p.description) > 200 else p.description
            }
            for p in places
        ],
        "count": len(places)
    }
