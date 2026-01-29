from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, Dict, List

from core.models import UserPreference
from Recommend.dto import PreferenceSurvey


async def get_user_preference(
    db: AsyncSession,
    user_id: int
) -> Optional[UserPreference]:
    """사용자 선호도 조회"""
    result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def save_user_preference(
    db: AsyncSession,
    user_id: int,
    survey: PreferenceSurvey
) -> UserPreference:
    """선호도 저장 (생성 또는 업데이트)"""
    existing = await get_user_preference(db, user_id)

    # 카테고리 점수를 가중치(0-1)로 변환
    category_weights = normalize_category_ratings(survey.category_ratings)

    if existing:
        # 업데이트
        existing.category_weights = category_weights
        existing.preferred_themes = survey.preferred_themes
        existing.travel_pace = survey.travel_pace
        existing.budget_level = survey.budget_level
        existing.preferred_start_time = survey.preferred_start_time
        existing.preferred_end_time = survey.preferred_end_time
        await db.commit()
        await db.refresh(existing)
        return existing
    else:
        # 생성
        preference = UserPreference(
            user_id=user_id,
            category_weights=category_weights,
            preferred_themes=survey.preferred_themes,
            travel_pace=survey.travel_pace,
            budget_level=survey.budget_level,
            preferred_start_time=survey.preferred_start_time,
            preferred_end_time=survey.preferred_end_time
        )
        db.add(preference)
        await db.commit()
        await db.refresh(preference)
        return preference


def normalize_category_ratings(ratings: Dict[str, int]) -> Dict[str, float]:
    """
    카테고리 점수(1-5)를 가중치(0-1)로 정규화

    1점 -> 0.2
    2점 -> 0.4
    3점 -> 0.6
    4점 -> 0.8
    5점 -> 1.0
    """
    return {
        category: score / 5.0
        for category, score in ratings.items()
    }


def calculate_preference_weight(
    preference: Optional[UserPreference],
    category: Optional[str],
    tags: Optional[List[str]]
) -> float:
    """
    선호도 기반 가중치 계산

    반환값: 0.0 ~ 1.0
    """
    if not preference:
        return 0.5  # 기본값

    score = 0.0
    factors = 0

    # 1. 카테고리 가중치 (50%)
    if preference.category_weights and category:
        cat_weight = preference.category_weights.get(category, 0.5)
        score += cat_weight * 0.5
        factors += 1

    # 2. 테마 매칭 (50%)
    if preference.preferred_themes and tags:
        # 정규화된 테마 매칭
        normalized_pref = set(normalize_themes(preference.preferred_themes))
        normalized_tags = set(normalize_themes(tags))

        if normalized_pref:
            matched = normalized_pref & normalized_tags
            theme_score = len(matched) / len(normalized_pref)
            score += theme_score * 0.5
            factors += 1

    if factors == 0:
        return 0.5

    return min(score, 1.0)


def normalize_themes(themes: List[str]) -> List[str]:
    """테마/태그 정규화 (동의어 처리)"""
    THEME_SYNONYMS = {
        "자연": ["자연", "바다", "산", "호수", "강", "숲", "공원", "해변"],
        "힐링": ["힐링", "휴양", "휴식", "조용한", "평화로운"],
        "액티비티": ["액티비티", "레저", "스포츠", "체험", "놀이"],
        "역사": ["역사", "문화재", "유적", "전통", "고궁"],
        "도시": ["도시", "야경", "시내", "번화가", "쇼핑"],
        "맛집": ["맛집", "음식", "식당", "먹거리", "미식"],
        "카페": ["카페", "디저트", "베이커리", "커피"],
        "사진명소": ["사진명소", "포토스팟", "인스타", "뷰맛집", "전망"],
    }

    normalized = set()
    for theme in themes:
        theme_lower = theme.lower().strip()
        # 동의어 그룹에서 대표 테마 찾기
        found = False
        for main_theme, synonyms in THEME_SYNONYMS.items():
            if theme_lower in [s.lower() for s in synonyms]:
                normalized.add(main_theme)
                found = True
                break
        if not found:
            normalized.add(theme)

    return list(normalized)


def get_travel_pace_config(pace: str) -> dict:
    """여행 페이스에 따른 설정"""
    configs = {
        "relaxed": {
            "max_places_per_day": 3,
            "min_stay_duration": 90,
            "buffer_time": 30
        },
        "moderate": {
            "max_places_per_day": 5,
            "min_stay_duration": 60,
            "buffer_time": 20
        },
        "packed": {
            "max_places_per_day": 7,
            "min_stay_duration": 45,
            "buffer_time": 15
        }
    }
    return configs.get(pace, configs["moderate"])


def preference_to_snapshot(preference: Optional[UserPreference]) -> Optional[dict]:
    """선호도를 스냅샷으로 변환 (Trip 저장용)"""
    if not preference:
        return None

    return {
        "category_weights": preference.category_weights,
        "preferred_themes": preference.preferred_themes,
        "travel_pace": preference.travel_pace,
        "budget_level": preference.budget_level,
        "preferred_start_time": preference.preferred_start_time.isoformat() if preference.preferred_start_time else None,
        "preferred_end_time": preference.preferred_end_time.isoformat() if preference.preferred_end_time else None
    }
