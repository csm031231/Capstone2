import logging
import math
from datetime import datetime, time, timedelta, date
from typing import List, Dict, Optional, Tuple, Any

from core.models import UserPreference
from services.kakao_service import get_route_info
from Planner.constants import (
    WEEKDAY_KR, WEEKDAY_EN,
    DEFAULT_DAY_START, DEFAULT_DAY_END,
    LUNCH_START, LUNCH_END, DINNER_START, NIGHT_START,
)

logger = logging.getLogger(__name__)


class TimeConstraintService:
    """시간 제약 처리 서비스"""

    # 야경/야간 관련 키워드 (이 태그가 있으면 18:00 이후에만 배치)
    NIGHT_KEYWORDS = {"야경", "야간", "night", "루프탑", "야시장", "불꽃", "일몰", "노을"}

    # 카테고리별 기본 체류 시간 (분)
    DEFAULT_STAY_DURATION = {
        "관광지": 90,
        "카페": 45,
        "맛집": 60,
        "식당": 60,
        "자연": 120,
        "쇼핑": 60,
        "체험": 90,
        "박물관": 90,
        "전시": 60,
        "공원": 60,
    }

    # 여행 페이스별 설정
    PACE_CONFIG = {
        "relaxed": {
            "stay_multiplier": 1.3,
            "buffer_time": 30
        },
        "moderate": {
            "stay_multiplier": 1.0,
            "buffer_time": 15
        },
        "packed": {
            "stay_multiplier": 0.8,
            "buffer_time": 10
        }
    }

    # ── 구조 분리 ─────────────────────────────────────────────────────────────

    def _is_night_place(self, p: dict) -> bool:
        """야경/야간 장소 여부 판별.

        우선순위:
        1. GPT가 is_night=true로 명시 → 카테고리 무관하게 야간 취급
        2. 태그/이름에 야간 키워드 포함 → 야간 취급 (단, 명확한 비야간 카테고리 제외)
        """
        # GPT가 명시적으로 야간 장소로 표시한 경우 우선 신뢰
        if p.get('is_night_place', False):
            return True

        NON_NIGHT_CATEGORIES = {'체험', '박물관', '관광지', '맛집', '식당', '카페', '쇼핑', '전시'}
        category = (p.get('place_category') or p.get('category') or '')
        if category in NON_NIGHT_CATEGORIES:
            return False

        tags = p.get('tags') or []
        name = (p.get('place_name') or p.get('name', '')).lower()
        return (
            any(kw in t.lower() for t in tags for kw in self.NIGHT_KEYWORDS)
            or any(kw in name for kw in self.NIGHT_KEYWORDS)
        )

    @staticmethod
    def _is_meal_place(p: dict) -> bool:
        """식사 장소 여부 판별"""
        cat = p.get('place_category') or p.get('category') or ''
        return cat in ('맛집', '식당')

    def _split_day_places(
        self, day_num: int, places: List[dict]
    ) -> Tuple[Dict[str, List[dict]], List[str]]:
        """하루 장소를 세그먼트로 분리 (아침/점심/오후/저녁/야경).

        Returns:
            (segments, warnings)
            segments = {'morning': [...], 'lunch': [...], 'afternoon': [...], 'dinner': [...], 'night': [...]}
        """
        night_places = [p for p in places if self._is_night_place(p)]
        meals = [p for p in places if self._is_meal_place(p) and not self._is_night_place(p)]
        others = [p for p in places if not self._is_meal_place(p) and not self._is_night_place(p)]

        lunch = meals[0] if meals else None
        dinner = meals[1] if len(meals) >= 2 else None

        warnings = []
        if not lunch:
            warnings.append(f"{day_num}일차: 점심 식당이 없습니다")
        if not dinner:
            warnings.append(f"{day_num}일차: 저녁 식당이 없습니다")

        split = min(len(others) // 2, 2)
        afternoon = others[split:]

        # 야경 장소가 없고 오후 장소가 2개 이상이면 마지막 오후 장소를 저녁 이후로 이동
        # (저녁 식사 후 빈 시간 방지 — 야경 전용 제약 없이 자연스럽게 배치됨)
        if not night_places and len(afternoon) >= 2:
            night_places = [afternoon.pop()]

        return {
            'morning':   others[:split],
            'lunch':     [lunch] if lunch else [],
            'afternoon': afternoon,
            'dinner':    [dinner] if dinner else [],
            'night':     night_places,
        }, warnings

    def structural_split_all(
        self, places_by_day: Dict[int, List[dict]]
    ) -> Tuple[Dict[int, Dict[str, List[dict]]], List[str]]:
        """전체 일정을 세그먼트로 분리 (시간 계산 없음).

        Returns:
            (segmented_by_day, warnings)
            segmented_by_day = {day_num: {'morning': [...], 'lunch': [...], ...}}
        """
        segmented: Dict[int, Dict[str, List[dict]]] = {}
        all_warnings: List[str] = []
        for day_num, places in places_by_day.items():
            segments, warnings = self._split_day_places(day_num, places)
            segmented[day_num] = segments
            all_warnings.extend(warnings)
        return segmented, all_warnings

    # ── 시간 계산 ─────────────────────────────────────────────────────────────

    async def apply_time_calculations(
        self,
        segmented_by_day: Dict[int, Dict[str, List[dict]]],
        preference: Optional[UserPreference],
        start_date: date
    ) -> Tuple[Dict[int, List[dict]], List[str]]:
        """세그먼트화된 일정에 도착 시간 / 체류 시간 / 영업시간 제약 적용.

        segmented_by_day 는 structural_split_all 또는 route_optimizer.optimize_segments 의 출력.
        """
        if preference:
            day_start = preference.preferred_start_time or DEFAULT_DAY_START
            day_end = preference.preferred_end_time or DEFAULT_DAY_END
            pace = preference.travel_pace or "moderate"
        else:
            day_start = DEFAULT_DAY_START
            day_end = DEFAULT_DAY_END
            pace = "moderate"

        pace_config = self.PACE_CONFIG.get(pace, self.PACE_CONFIG["moderate"])
        result: Dict[int, List[dict]] = {}
        warnings: List[str] = []

        for day_num, segments in segmented_by_day.items():
            current_date = start_date + timedelta(days=int(day_num) - 1)
            current_time = datetime.combine(current_date, day_start)
            end_datetime = datetime.combine(current_date, day_end)

            # 세그먼트 결합 (순서 고정: 아침 → 점심 → 오후 → 저녁 → 야경)
            places = (
                segments.get('morning', []) +
                segments.get('lunch', []) +
                segments.get('afternoon', []) +
                segments.get('dinner', []) +
                segments.get('night', [])
            )

            # 최종 순서 기반 이동시간 재계산 (Kakao API)
            await self._recalculate_travel_times(places)

            day_itineraries = []

            for place in places:
                is_must_visit = place.get('must_visit', False)
                place_name = place.get('place_name') or place.get('name', '알 수 없음')

                travel_time = place.get('travel_time_from_prev', 0) or 0
                arrival_time = current_time + timedelta(minutes=travel_time)

                # 식사 시간대 push
                place_category = place.get('place_category') or place.get('category') or ''
                if place_category in ('맛집', '식당'):
                    t = arrival_time.time()
                    if t < LUNCH_START:
                        meal_time = datetime.combine(current_date, LUNCH_START)
                        arrival_time = meal_time
                        if current_time < meal_time:
                            current_time = meal_time
                    elif LUNCH_END <= t < DINNER_START:
                        meal_time = datetime.combine(current_date, DINNER_START)
                        arrival_time = meal_time
                        if current_time < meal_time:
                            current_time = meal_time

                # 야경 NIGHT_START 이전 불가
                if self._is_night_place(place) and arrival_time.time() < NIGHT_START:
                    night_dt = datetime.combine(current_date, NIGHT_START)
                    arrival_time = night_dt
                    if current_time < night_dt:
                        current_time = night_dt

                # 휴무일 체크
                if self._is_closed(place.get('closed_days'), current_date):
                    if is_must_visit:
                        warnings.append(
                            f"{day_num}일차: {place_name}은(는) 휴무일이지만 필수 방문 장소이므로 포함합니다"
                        )
                    else:
                        continue

                # 영업시간 체크
                opens, closes = self._parse_operating_hours(place.get('operating_hours'))
                if opens and arrival_time.time() < opens:
                    arrival_time = datetime.combine(current_date, opens)
                if closes and arrival_time.time() >= closes:
                    if is_must_visit:
                        warnings.append(
                            f"{day_num}일차: {place_name}은(는) 영업시간이 지났지만 필수 방문 장소이므로 포함합니다"
                        )
                    else:
                        continue

                # 체류 시간 결정
                category = place.get('place_category') or place.get('category')
                base_duration = self.DEFAULT_STAY_DURATION.get(category, 60)
                gpt_suggested = place.get('suggested_stay_duration')
                if gpt_suggested and isinstance(gpt_suggested, (int, float)) and 15 <= gpt_suggested <= 300:
                    stay_duration = int(gpt_suggested * pace_config["stay_multiplier"])
                else:
                    stay_duration = int(base_duration * pace_config["stay_multiplier"])

                finish_time = arrival_time + timedelta(minutes=stay_duration)
                if arrival_time >= end_datetime:
                    if is_must_visit:
                        warnings.append(
                            f"{day_num}일차: {place_name}은(는) 선호 종료 시간 이후 도착이지만 필수 방문 장소이므로 포함합니다"
                        )
                    else:
                        continue
                elif finish_time > end_datetime:
                    warnings.append(f"{day_num}일차: {place_name} 방문이 선호 종료 시간을 초과합니다")

                place['suggested_arrival_time'] = arrival_time.time()
                place['suggested_stay_duration'] = stay_duration
                day_itineraries.append(place)
                current_time = finish_time + timedelta(minutes=pace_config["buffer_time"])

            for idx, place in enumerate(day_itineraries):
                place['order_index'] = idx + 1

            result[day_num] = day_itineraries

        return result, warnings

    async def apply_constraints(
        self,
        places_by_day: Dict[int, List[dict]],
        preference: Optional[UserPreference],
        start_date: date
    ) -> Tuple[Dict[int, List[dict]], List[str]]:
        """편의 메서드: 구조 분리 + 시간 계산 (route_optimizer 없이).
        chat_service 재생성 등 단순 경로에서 사용.
        """
        segmented, structural_warnings = self.structural_split_all(places_by_day)
        constrained, time_warnings = await self.apply_time_calculations(
            segmented, preference, start_date
        )
        return constrained, structural_warnings + time_warnings

    def validate_schedule(
        self,
        places_by_day: Dict[int, List[dict]],
        preference: Optional[UserPreference],
        start_date: date
    ) -> Dict[str, Any]:
        """
        스케줄 유효성 검증

        Returns:
            {
                "valid": bool,
                "warnings": List[str],
                "errors": List[str]
            }
        """
        warnings = []
        errors = []

        if preference:
            day_end = preference.preferred_end_time or DEFAULT_DAY_END
        else:
            day_end = DEFAULT_DAY_END

        for day_num, places in places_by_day.items():
            if isinstance(day_num, str) and day_num.startswith('_'):
                continue

            current_date = start_date + timedelta(days=int(day_num) - 1)
            end_datetime = datetime.combine(current_date, day_end)

            for place in places:
                place_name = place.get('place_name') or place.get('name', '알 수 없음')

                # 휴무일 경고
                if self._is_closed(place.get('closed_days'), current_date):
                    warnings.append(
                        f"{day_num}일차: {place_name}은(는) 휴무일입니다"
                    )

                # 영업시간 체크
                opens, closes = self._parse_operating_hours(
                    place.get('operating_hours')
                )

                arrival = place.get('suggested_arrival_time')
                if arrival and closes and arrival >= closes:
                    warnings.append(
                        f"{day_num}일차: {place_name} 도착 시간이 영업시간 이후입니다"
                    )

            # 일차 종료 시간 체크
            if places:
                last_place = places[-1]
                arrival = last_place.get('suggested_arrival_time')
                duration = last_place.get('suggested_stay_duration', 60)

                if arrival:
                    finish = datetime.combine(
                        current_date,
                        arrival
                    ) + timedelta(minutes=duration)

                    if finish > end_datetime:
                        warnings.append(
                            f"{day_num}일차: 일정이 선호 종료 시간을 초과합니다"
                        )

        return {
            "valid": len(errors) == 0,
            "warnings": warnings,
            "errors": errors
        }

    def _parse_operating_hours(
        self,
        hours_str: Optional[str]
    ) -> Tuple[Optional[time], Optional[time]]:
        """영업시간 문자열 파싱"""
        if not hours_str:
            return None, None

        try:
            # "09:00 - 18:00" 또는 "09:00~18:00" 형식
            clean = hours_str.replace(" ", "")
            separator = "-" if "-" in clean else "~" if "~" in clean else None

            if separator:
                parts = clean.split(separator)
                if len(parts) == 2:
                    opens = datetime.strptime(parts[0], "%H:%M").time()
                    closes = datetime.strptime(parts[1], "%H:%M").time()
                    return opens, closes
        except Exception:
            pass

        return None, None

    def _is_closed(
        self,
        closed_days: Optional[str],
        check_date: date
    ) -> bool:
        """휴무일 체크"""
        if not closed_days:
            return False

        weekday = check_date.weekday()
        today_kr = WEEKDAY_KR[weekday]
        today_en = WEEKDAY_EN[weekday]

        closed_lower = closed_days.lower()

        # 한글 요일 체크
        if today_kr in closed_days or f"{today_kr}요일" in closed_days:
            return True

        # 영어 요일 체크
        if today_en in closed_lower:
            return True

        # "매주 X요일" 패턴
        if f"매주 {today_kr}" in closed_days:
            return True

        return False

    async def _recalculate_travel_times(self, places: List[dict]) -> None:
        """재정렬된 장소 순서에 맞게 travel_time_from_prev 재계산.
        Kakao 경로 API 우선, 실패 시 Haversine 폴백.
        """
        for i, place in enumerate(places):
            if i == 0:
                place['travel_time_from_prev'] = 0
                place['transport_mode'] = None
                continue

            prev = places[i - 1]
            prev_lng = prev.get('longitude') or 0
            prev_lat = prev.get('latitude') or 0
            curr_lng = place.get('longitude') or 0
            curr_lat = place.get('latitude') or 0

            travel_time = 15
            transport_mode = 'public_transit'

            if prev_lng and prev_lat and curr_lng and curr_lat:
                try:
                    route_info = await get_route_info(prev_lng, prev_lat, curr_lng, curr_lat)
                    duration = route_info.get('duration', 0)
                    distance = route_info.get('distance', 0)

                    if duration > 0:
                        travel_time = max(int(duration / 60), 1)
                        dist_km = distance / 1000
                        if dist_km < 0.5:
                            transport_mode = 'walk'
                        elif dist_km < 5:
                            transport_mode = 'public_transit'
                        else:
                            transport_mode = 'car'
                    else:
                        travel_time, transport_mode = self._haversine_fallback(
                            prev_lat, prev_lng, curr_lat, curr_lng
                        )
                except Exception:
                    travel_time, transport_mode = self._haversine_fallback(
                        prev_lat, prev_lng, curr_lat, curr_lng
                    )

            place['travel_time_from_prev'] = max(travel_time, 5)
            place['transport_mode'] = transport_mode

    @staticmethod
    def _haversine_fallback(lat1: float, lon1: float, lat2: float, lon2: float) -> Tuple[int, str]:
        """Haversine 직선거리 기반 이동시간/수단 추정"""
        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        dist_km = 6371 * 2 * math.asin(math.sqrt(a))

        if dist_km < 0.5:
            return max(int(dist_km * 1000 / 80), 5), 'walk'
        elif dist_km < 5:
            return int(dist_km / 20 * 60) + 5, 'public_transit'
        else:
            return int(dist_km / 30 * 60) + 10, 'car'

    def get_recommended_stay_duration(
        self,
        category: Optional[str],
        pace: str = "moderate"
    ) -> int:
        """권장 체류 시간 반환"""
        base = self.DEFAULT_STAY_DURATION.get(category, 60)
        multiplier = self.PACE_CONFIG.get(pace, {}).get("stay_multiplier", 1.0)
        return int(base * multiplier)


# 싱글톤 인스턴스
_time_service_instance = None


def get_time_constraint_service() -> TimeConstraintService:
    """싱글톤 시간 제약 서비스 반환"""
    global _time_service_instance
    if _time_service_instance is None:
        _time_service_instance = TimeConstraintService()
    return _time_service_instance
