"""
태그 기반 매칭 서비스 (Fallback)
- GPT Vision에서 추출한 태그로 여행지 검색
- CLIP 유사도가 낮을 때 사용
"""
from typing import List, Dict, Tuple, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from core.models import Place


# 태그 카테고리 정의 (동의어 매핑)
TAG_SYNONYMS = {
    # 자연
    "바다": ["해변", "ocean", "beach", "sea", "coastal", "해안"],
    "산": ["mountain", "산악", "등산", "숲", "forest"],
    "노을": ["sunset", "일몰", "석양"],
    "호수": ["lake", "저수지", "water"],

    # 도시
    "도시": ["city", "urban", "도심", "시내", "downtown"],
    "야경": ["night", "nightview", "밤"],
    "골목": ["alley", "거리", "street"],

    # 분위기
    "힐링": ["relaxing", "peaceful", "조용한", "quiet", "calm"],
    "액티비티": ["activity", "active", "adventure", "레저"],
    "역사": ["historic", "history", "전통", "traditional", "고궁"],
    "현대": ["modern", "contemporary", "신식"],

    # 시설
    "카페": ["cafe", "coffee", "커피"],
    "맛집": ["restaurant", "food", "음식점", "식당"],
    "관광지": ["tourist", "landmark", "명소", "sightseeing"],
}


def normalize_tags(tags: List[str]) -> List[str]:
    """
    태그 정규화 (동의어 → 대표 태그로 변환)

    Args:
        tags: 원본 태그 리스트

    Returns:
        정규화된 태그 리스트
    """
    normalized = set()

    for tag in tags:
        tag_lower = tag.lower().strip()
        found = False

        # 동의어 매핑에서 찾기
        for main_tag, synonyms in TAG_SYNONYMS.items():
            if tag_lower == main_tag.lower() or tag_lower in [s.lower() for s in synonyms]:
                normalized.add(main_tag)
                found = True
                break

        # 매핑에 없으면 원본 사용
        if not found:
            normalized.add(tag_lower)

    return list(normalized)


def calculate_tag_score(place_tags: List[str], query_tags: List[str]) -> float:
    """
    태그 매칭 점수 계산

    Args:
        place_tags: 여행지 태그
        query_tags: 검색 태그

    Returns:
        0~1 사이 점수 (자카드 유사도 + 가중치)
    """
    if not place_tags or not query_tags:
        return 0.0

    # 태그 정규화
    place_set = set(normalize_tags(place_tags))
    query_set = set(normalize_tags(query_tags))

    # 교집합
    intersection = place_set & query_set

    if not intersection:
        return 0.0

    # 자카드 유사도
    union = place_set | query_set
    jaccard = len(intersection) / len(union)

    # 쿼리 커버율 (검색 태그 중 몇 개가 매칭되었는지)
    coverage = len(intersection) / len(query_set)

    # 가중 평균 (커버율에 더 높은 가중치)
    score = jaccard * 0.4 + coverage * 0.6

    return min(score, 1.0)


async def search_by_tags(
    db: AsyncSession,
    tags: List[str],
    top_k: int = 10,
    min_score: float = 0.2
) -> List[Tuple[Place, float]]:
    """
    태그 기반 여행지 검색 (DB)

    Args:
        db: DB 세션
        tags: 검색할 태그 리스트
        top_k: 최대 반환 개수
        min_score: 최소 점수

    Returns:
        [(Place 객체, 점수), ...] 리스트
    """
    if not tags:
        return []

    # 정규화된 태그
    normalized_tags = normalize_tags(tags)

    # Place 테이블에서 태그가 있는 것들 조회
    result = await db.execute(
        select(Place).where(Place.tags.isnot(None))
    )
    places = result.scalars().all()

    # 점수 계산 및 정렬
    scored_places = []
    for place in places:
        place_tags = place.tags if isinstance(place.tags, list) else []
        score = calculate_tag_score(place_tags, normalized_tags)

        if score >= min_score:
            scored_places.append((place, score))

    # 점수 내림차순 정렬
    scored_places.sort(key=lambda x: x[1], reverse=True)

    return scored_places[:top_k]


def match_tags_with_places(
    query_tags: List[str],
    places_with_tags: List[Dict]
) -> List[Tuple[Dict, float]]:
    """
    메모리 내 태그 매칭 (FAISS 메타데이터용)

    Args:
        query_tags: 검색 태그
        places_with_tags: [{"place_id": 1, "tags": [...], ...}, ...]

    Returns:
        [(place_dict, score), ...] 정렬된 리스트
    """
    if not query_tags:
        return []

    scored = []
    for place in places_with_tags:
        place_tags = place.get("tags", [])
        score = calculate_tag_score(place_tags, query_tags)
        if score > 0:
            scored.append((place, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
