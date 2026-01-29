from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional, Set
from datetime import date

from core.models import Place, UserPreference
from Recommend.dto import RecommendCondition, RecommendedPlaceDetail
from Recommend.preference_service import (
    calculate_preference_weight,
    normalize_themes
)


class ConditionRecommender:
    """조건 기반 여행지 추천 서비스"""

    # 태그 동의어 매핑
    THEME_SYNONYMS = {
        "자연": ["자연", "바다", "산", "호수", "강", "숲", "공원", "해변", "노을", "일출"],
        "힐링": ["힐링", "휴양", "휴식", "조용한", "평화로운", "여유"],
        "액티비티": ["액티비티", "레저", "스포츠", "체험", "놀이", "어드벤처"],
        "역사": ["역사", "문화재", "유적", "전통", "고궁", "박물관", "사찰"],
        "도시": ["도시", "야경", "시내", "번화가", "쇼핑", "현대"],
        "맛집": ["맛집", "음식", "식당", "먹거리", "미식", "로컬푸드"],
        "카페": ["카페", "디저트", "베이커리", "커피", "브런치"],
        "사진명소": ["사진명소", "포토스팟", "인스타", "뷰맛집", "전망", "경치"],
    }

    # 예산 레벨 매핑 (fee_info 파싱용)
    BUDGET_KEYWORDS = {
        "low": ["무료", "free", "0원"],
        "medium": ["5000", "10000", "1만"],
        "high": ["20000", "30000", "2만", "3만", "프리미엄"]
    }

    async def recommend(
        self,
        db: AsyncSession,
        condition: RecommendCondition,
        user_preference: Optional[UserPreference] = None
    ) -> List[RecommendedPlaceDetail]:
        """
        조건 기반 추천

        추천 로직:
        1. DB 필터링 (지역, 카테고리, 제외 장소)
        2. 테마 매칭 점수 계산
        3. 사용자 선호도 가중치 적용
        4. 최종 점수로 정렬
        """
        # 1단계: 기본 필터링
        places = await self._filter_places(db, condition)

        if not places:
            return []

        # 2단계: 점수 계산
        scored_places = []
        for place in places:
            relevance = self._calculate_relevance(place, condition)
            preference = calculate_preference_weight(
                user_preference,
                place.category,
                place.tags
            )

            # 최종 점수: 조건 부합도 60% + 선호도 40%
            final_score = relevance * 0.6 + preference * 0.4

            reasons = self._generate_match_reasons(
                place, condition, user_preference
            )

            scored_places.append({
                "place": place,
                "relevance_score": round(relevance, 3),
                "preference_score": round(preference, 3),
                "final_score": round(final_score, 3),
                "match_reasons": reasons
            })

        # 3단계: 정렬 및 상위 K개 선택
        scored_places.sort(key=lambda x: x["final_score"], reverse=True)
        top_places = scored_places[:condition.top_k]

        # 4단계: 응답 변환
        return [
            RecommendedPlaceDetail(
                place_id=item["place"].id,
                name=item["place"].name,
                category=item["place"].category,
                address=item["place"].address,
                latitude=item["place"].latitude,
                longitude=item["place"].longitude,
                image_url=item["place"].image_url,
                tags=item["place"].tags,
                description=item["place"].description,
                operating_hours=item["place"].operating_hours,
                closed_days=item["place"].closed_days,
                fee_info=item["place"].fee_info,
                relevance_score=item["relevance_score"],
                preference_score=item["preference_score"],
                final_score=item["final_score"],
                match_reasons=item["match_reasons"]
            )
            for item in top_places
        ]

    async def _filter_places(
        self,
        db: AsyncSession,
        condition: RecommendCondition
    ) -> List[Place]:
        """DB 필터링"""
        query = select(Place)

        # 지역 필터
        if condition.region:
            query = query.where(Place.address.contains(condition.region))

        # 카테고리 필터
        if condition.categories:
            query = query.where(Place.category.in_(condition.categories))

        # 제외 장소 필터
        if condition.exclude_places:
            query = query.where(~Place.id.in_(condition.exclude_places))

        result = await db.execute(query)
        places = result.scalars().all()

        # 휴무일 필터 (Python에서 처리)
        if condition.travel_date:
            places = [
                p for p in places
                if not self._is_closed(p.closed_days, condition.travel_date)
            ]

        return places

    def _calculate_relevance(
        self,
        place: Place,
        condition: RecommendCondition
    ) -> float:
        """조건 부합도 계산"""
        score = 0.0
        weights = {
            "theme": 0.4,
            "category": 0.3,
            "budget": 0.2,
            "availability": 0.1
        }

        # 1. 테마 매칭
        if condition.themes and place.tags:
            theme_score = self._calculate_theme_match(
                condition.themes, place.tags
            )
            score += theme_score * weights["theme"]
        elif not condition.themes:
            score += weights["theme"] * 0.5  # 테마 조건 없으면 기본 점수

        # 2. 카테고리 매칭
        if condition.categories and place.category:
            if place.category in condition.categories:
                score += weights["category"]
        elif not condition.categories:
            score += weights["category"] * 0.5

        # 3. 예산 매칭
        if condition.budget_level and place.fee_info:
            budget_score = self._match_budget(
                place.fee_info, condition.budget_level
            )
            score += budget_score * weights["budget"]
        elif not condition.budget_level:
            score += weights["budget"] * 0.5

        # 4. 가용성 (휴무일 체크는 이미 필터링됨)
        score += weights["availability"]

        return min(score, 1.0)

    def _calculate_theme_match(
        self,
        query_themes: List[str],
        place_tags: List[str]
    ) -> float:
        """테마 매칭 점수 (자카드 유사도 + 커버리지)"""
        # 정규화
        normalized_query = self._expand_themes(query_themes)
        normalized_tags = set(t.lower().strip() for t in place_tags)

        if not normalized_query:
            return 0.5

        # 교집합
        matched = normalized_query & normalized_tags

        # 자카드 유사도 (40%)
        union = normalized_query | normalized_tags
        jaccard = len(matched) / len(union) if union else 0

        # 쿼리 커버율 (60%)
        coverage = len(matched) / len(normalized_query) if normalized_query else 0

        return jaccard * 0.4 + coverage * 0.6

    def _expand_themes(self, themes: List[str]) -> Set[str]:
        """테마를 동의어로 확장"""
        expanded = set()
        for theme in themes:
            theme_lower = theme.lower().strip()
            expanded.add(theme_lower)

            # 동의어 추가
            for main_theme, synonyms in self.THEME_SYNONYMS.items():
                if theme_lower in [s.lower() for s in synonyms]:
                    expanded.update(s.lower() for s in synonyms)
                    break

        return expanded

    def _match_budget(self, fee_info: str, budget_level: str) -> float:
        """예산 매칭"""
        fee_lower = fee_info.lower()

        if budget_level == "low":
            # 무료이거나 저렴한 곳 선호
            if any(kw in fee_lower for kw in self.BUDGET_KEYWORDS["low"]):
                return 1.0
            elif any(kw in fee_lower for kw in self.BUDGET_KEYWORDS["high"]):
                return 0.2
            return 0.5

        elif budget_level == "medium":
            # 중간 가격대 선호
            if any(kw in fee_lower for kw in self.BUDGET_KEYWORDS["low"]):
                return 0.7
            elif any(kw in fee_lower for kw in self.BUDGET_KEYWORDS["high"]):
                return 0.5
            return 0.8

        elif budget_level == "high":
            # 프리미엄 경험 선호
            if any(kw in fee_lower for kw in self.BUDGET_KEYWORDS["high"]):
                return 1.0
            return 0.6

        return 0.5

    def _is_closed(self, closed_days: Optional[str], check_date: date) -> bool:
        """휴무일 체크"""
        if not closed_days:
            return False

        weekday = check_date.weekday()
        weekday_names = ["월", "화", "수", "목", "금", "토", "일"]
        today_name = weekday_names[weekday]

        closed_lower = closed_days.lower()

        # "매주 월요일", "월요일 휴무" 등 체크
        if today_name in closed_days or f"{today_name}요일" in closed_days:
            return True

        # 영어 요일도 체크
        eng_weekdays = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        if eng_weekdays[weekday] in closed_lower:
            return True

        return False

    def _generate_match_reasons(
        self,
        place: Place,
        condition: RecommendCondition,
        preference: Optional[UserPreference]
    ) -> List[str]:
        """매칭 이유 생성"""
        reasons = []

        # 테마 매칭
        if condition.themes and place.tags:
            expanded_themes = self._expand_themes(condition.themes)
            place_tags_lower = set(t.lower() for t in place.tags)
            matched = expanded_themes & place_tags_lower
            if matched:
                reasons.append(f"테마 일치: {', '.join(list(matched)[:3])}")

        # 카테고리 매칭
        if condition.categories and place.category in condition.categories:
            reasons.append(f"카테고리: {place.category}")

        # 지역 매칭
        if condition.region and condition.region in (place.address or ""):
            reasons.append(f"지역: {condition.region}")

        # 선호도 매칭
        if preference:
            if preference.category_weights and place.category:
                weight = preference.category_weights.get(place.category, 0)
                if weight >= 0.8:
                    reasons.append("선호 카테고리")

            if preference.preferred_themes and place.tags:
                pref_themes = set(normalize_themes(preference.preferred_themes))
                place_themes = set(normalize_themes(place.tags))
                if pref_themes & place_themes:
                    reasons.append("선호 테마")

        if not reasons:
            reasons.append("조건 부합")

        return reasons


# 싱글톤 인스턴스
_recommender_instance = None


def get_condition_recommender() -> ConditionRecommender:
    """싱글톤 추천 서비스 반환"""
    global _recommender_instance
    if _recommender_instance is None:
        _recommender_instance = ConditionRecommender()
    return _recommender_instance
