import logging
from datetime import datetime, time, timedelta, date
from typing import List, Dict, Optional, Tuple, Any

from core.models import UserPreference

logger = logging.getLogger(__name__)


class TimeConstraintService:
    """시간 제약 처리 서비스"""

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

    def apply_constraints(
        self,
        places_by_day: Dict[int, List[dict]],
        preference: Optional[UserPreference],
        start_date: date
    ) -> Dict[int, List[dict]]:
        """
        시간 제약 적용

        1. 영업시간 체크
        2. 휴무일 체크 (must-visit은 경고만)
        3. 체류 시간 배정
        4. 도착 시간 계산

        Returns:
            places_by_day with warnings list at key '_warnings'
        """
        # 시간 설정
        if preference:
            day_start = preference.preferred_start_time or time(9, 0)
            day_end = preference.preferred_end_time or time(21, 0)
            pace = preference.travel_pace or "moderate"
        else:
            day_start = time(9, 0)
            day_end = time(21, 0)
            pace = "moderate"

        pace_config = self.PACE_CONFIG.get(pace, self.PACE_CONFIG["moderate"])

        result = {}
        warnings = []

        for day_num, places in places_by_day.items():
            current_date = start_date + timedelta(days=int(day_num) - 1)
            current_time = datetime.combine(current_date, day_start)
            end_datetime = datetime.combine(current_date, day_end)

            day_itineraries = []

            for place in places:
                is_must_visit = place.get('must_visit', False)

                # 휴무일 체크
                if self._is_closed(place.get('closed_days'), current_date):
                    if is_must_visit:
                        # 필수 방문지는 경고만 하고 포함
                        place_name = place.get('place_name') or place.get('name', '알 수 없음')
                        warnings.append(
                            f"{day_num}일차: {place_name}은(는) 휴무일이지만 필수 방문 장소이므로 포함합니다"
                        )
                    else:
                        continue

                # 영업시간 체크
                opens, closes = self._parse_operating_hours(
                    place.get('operating_hours')
                )

                # 영업 시작 시간까지 대기
                if opens and current_time.time() < opens:
                    current_time = datetime.combine(current_date, opens)

                # 영업 종료 확인
                if closes and current_time.time() >= closes:
                    if is_must_visit:
                        place_name = place.get('place_name') or place.get('name', '알 수 없음')
                        warnings.append(
                            f"{day_num}일차: {place_name}은(는) 영업시간이 지났지만 필수 방문 장소이므로 포함합니다"
                        )
                    else:
                        continue

                # 체류 시간 결정
                category = place.get('place_category') or place.get('category')
                base_duration = self.DEFAULT_STAY_DURATION.get(category, 60)
                stay_duration = int(base_duration * pace_config["stay_multiplier"])

                # 종료 시간 확인
                finish_time = current_time + timedelta(minutes=stay_duration)
                if finish_time > end_datetime:
                    if is_must_visit:
                        place_name = place.get('place_name') or place.get('name', '알 수 없음')
                        warnings.append(
                            f"{day_num}일차: {place_name} 방문 시 선호 종료 시간을 초과합니다"
                        )
                    else:
                        break

                # 일정 추가
                place['suggested_arrival_time'] = current_time.time()
                place['suggested_stay_duration'] = stay_duration
                day_itineraries.append(place)

                # 다음 장소 시간 (체류 + 버퍼)
                travel_time = place.get('travel_time_from_prev', 0) or 0
                current_time = finish_time + timedelta(
                    minutes=pace_config["buffer_time"] + travel_time
                )

            result[day_num] = day_itineraries

        # 경고를 별도 키에 저장
        if warnings:
            result['_warnings'] = warnings

        return result

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
            day_end = preference.preferred_end_time or time(21, 0)
        else:
            day_end = time(21, 0)

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
        weekday_names_kr = ["월", "화", "수", "목", "금", "토", "일"]
        weekday_names_en = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

        today_kr = weekday_names_kr[weekday]
        today_en = weekday_names_en[weekday]

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
