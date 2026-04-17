"""
태그 기반 매칭 서비스 (Fallback)
- GPT Vision에서 추출한 태그로 여행지 검색
- CLIP 유사도가 낮을 때 사용
"""
from typing import List, Dict, Tuple, Optional


# 태그 카테고리 정의 (동의어 매핑)
TAG_SYNONYMS = {
    # 자연 — 해양/수계
    "바다": ["해변", "ocean", "beach", "sea", "coastal", "해안", "바닷가"],
    "해변": ["beach", "모래사장", "sand beach", "모래해변", "바닷가"],
    "노을": ["sunset", "일몰", "석양", "낙조"],
    "일출": ["sunrise", "새벽", "dawn"],
    "호수": ["lake", "저수지", "water", "담수"],
    "강": ["river", "하천", "천", "stream"],
    "계곡": ["valley", "gorge", "협곡", "물골", "계류"],
    "폭포": ["waterfall", "falls", "cascade", "폭"],
    "갯벌": ["tidal flat", "mudflat", "조간대", "갯바위"],
    "습지": ["wetland", "marsh", "늪", "swamp"],

    # 자연 — 지형
    "산": ["mountain", "산악", "등산", "봉우리", "peak", "high land"],
    "숲": ["forest", "나무", "tree", "woodland", "수림"],
    "공원": ["park", "녹지", "정원", "garden"],
    "섬": ["island", "islet", "도서", "무인도"],
    "절벽": ["cliff", "해식애", "암벽", "암봉", "단애"],
    "오름": ["volcanic hill", "제주오름", "분석구"],
    "분화구": ["crater", "칼데라", "화산", "volcano"],
    "주상절리": ["columnar joint", "basalt column", "현무암", "주상"],
    "기암": ["strange rock", "기암괴석", "암석", "rock formation"],

    # 자연 — 계절/식생
    "단풍": ["autumn leaves", "가을", "fall foliage", "紅葉"],
    "벚꽃": ["cherry blossom", "봄꽃", "spring flower", "벚"],
    "눈": ["snow", "설경", "겨울", "winter", "설산"],
    "억새": ["silver grass", "pampas grass", "갈대"],
    "초원": ["meadow", "grassland", "목초지", "들판"],

    # 자연 분위기
    "자연": ["nature", "자연경관", "경치", "풍경", "landscape"],
    "전망": ["view", "조망", "뷰", "panorama", "경관"],
    "포토스팟": ["photo spot", "사진명소", "인스타", "instagram"],
    "경치": ["scenery", "풍광", "landscape", "view"],

    # 도시
    "도시": ["city", "urban", "도심", "시내", "downtown"],
    "야경": ["night", "nightview", "밤", "night view", "야간"],
    "골목": ["alley", "거리", "street", "골목길"],
    "번화가": ["shopping street", "상가", "쇼핑", "shopping"],

    # 분위기
    "힐링": ["relaxing", "peaceful", "조용한", "quiet", "calm", "healing", "휴식"],
    "평화로운": ["peaceful", "serene", "tranquil", "조용한", "잔잔한"],
    "액티비티": ["activity", "active", "adventure", "레저", "체험", "어드벤처"],
    "역사": ["historic", "history", "전통", "traditional", "고궁", "유적", "문화재"],
    "사찰": ["temple", "절", "buddhist", "불교", "암자"],
    "현대": ["modern", "contemporary", "신식"],

    # 시설
    "카페": ["cafe", "coffee", "커피", "브런치", "디저트"],
    "맛집": ["restaurant", "food", "음식점", "식당", "로컬푸드", "미식"],
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
