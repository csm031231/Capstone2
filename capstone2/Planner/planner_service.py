import json
import re
import logging
import asyncio
from typing import List, Optional, Dict
from datetime import time
from openai import OpenAI

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_config
from core.models import UserPreference, Place
from Trip.dto import TripCreate
from Trip import crud as trip_crud
from Recommend.dto import RecommendCondition
from Recommend.recommend_service import get_condition_recommender
from Recommend.preference_service import (
    get_user_preference,
    preference_to_snapshot,
    get_travel_pace_config
)
from Planner.dto import GenerateRequest, GenerateResponse, GeneratedItinerary, DaySummary
from Planner.route_optimizer import get_route_optimizer
from Planner.time_constraint import get_time_constraint_service

logger = logging.getLogger(__name__)


class PlannerService:
    """GPT 기반 AI 일정 생성 서비스"""

    def __init__(self):
        config = get_config()
        self.client = OpenAI(api_key=config.openai_api_key)
        self.recommender = get_condition_recommender()
        self.route_optimizer = get_route_optimizer()
        self.time_service = get_time_constraint_service()

    async def generate_itinerary(
        self,
        db: AsyncSession,
        user_id: int,
        request: GenerateRequest,
        user_preference: Optional[UserPreference] = None
    ) -> GenerateResponse:
        """
        AI 일정 생성 파이프라인

        1. 후보 장소 수집 (조건 + 선호도)
        2. GPT로 일정 초안 생성
        3. 시간 제약 적용
        4. 동선 최적화
        5. Trip/Itinerary DB 저장
        """
        # 1단계: 여행 기간 계산
        total_days = (request.end_date - request.start_date).days + 1

        # 2단계: 후보 장소 수집
        print("[PLANNER] 2단계: 후보 장소 수집 시작")
        candidates = await self._gather_candidates(
            db, request, user_preference, total_days
        )

        if not candidates:
            raise ValueError("조건에 맞는 여행지가 없습니다")
        print(f"[PLANNER] 2단계 완료: {len(candidates)}개 후보 장소")

        # 3단계: GPT 일정 초안 생성
        print("[PLANNER] 3단계: GPT 일정 생성 시작")
        draft = await self._generate_with_gpt(
            candidates, request, user_preference, total_days
        )
        print(f"[PLANNER] 3단계 완료: {len(draft.get('days', []))}일 일정 생성")

        # 4단계: 장소 딕셔너리로 변환
        print("[PLANNER] 4단계: 장소 매핑 시작")
        place_dict = {c['place_id']: c for c in candidates}
        places_by_day = self._build_places_by_day(draft, place_dict)
        print(f"[PLANNER] 4단계 완료: {sum(len(v) for v in places_by_day.values())}개 장소 매핑")

        # 5단계: 동선 최적화
        print("[PLANNER] 5단계: 동선 최적화 시작")
        optimized = await self.route_optimizer.optimize(
            places_by_day,
            request.start_location,
            request.end_location
        )
        print("[PLANNER] 5단계 완료")

        # 6단계: 시간 제약 적용
        print("[PLANNER] 6단계: 시간 제약 적용 시작")
        constrained, warnings = self.time_service.apply_constraints(
            optimized,
            user_preference,
            request.start_date
        )
        print("[PLANNER] 6단계 완료")

        # 7단계: DB 저장
        print("[PLANNER] 7단계: DB 저장 시작")
        trip = await self._save_trip(
            db, user_id, request, constrained, user_preference
        )
        print(f"[PLANNER] 7단계 완료: trip_id={trip.id}")

        # 8단계: 응답 생성
        print("[PLANNER] 8단계: 응답 생성")
        return self._build_response(
            trip, constrained, draft, request, total_days, warnings
        )

    async def _gather_candidates(
        self,
        db: AsyncSession,
        request: GenerateRequest,
        preference: Optional[UserPreference],
        total_days: int
    ) -> List[dict]:
        """후보 장소 수집"""
        # 필요 장소 수 계산 (여유분 포함, 최대 100개)
        needed = request.max_places_per_day * total_days * 2

        # 테마 결정
        themes = request.themes
        if not themes and preference and preference.preferred_themes:
            themes = preference.preferred_themes

        # 추천 조건 생성
        condition = RecommendCondition(
            region=request.region,
            themes=themes,
            exclude_places=request.exclude_places,
            top_k=min(needed, 100)
        )

        # 추천 실행
        places = await self.recommender.recommend(db, condition, preference)

        # 딕셔너리로 변환 (readcount 포함을 위해 DB에서 Place 재조회)
        place_ids = [p.place_id for p in places]
        db_places = {}
        if place_ids:
            from sqlalchemy import select as sa_select
            result = await db.execute(
                sa_select(Place).where(Place.id.in_(place_ids))
            )
            for dp in result.scalars().all():
                db_places[dp.id] = dp

        candidates = [
            {
                "place_id": p.place_id,
                "name": p.name,
                "category": p.category,
                "address": p.address,
                "latitude": p.latitude,
                "longitude": p.longitude,
                "image_url": p.image_url,
                "tags": p.tags,
                "operating_hours": p.operating_hours,
                "closed_days": p.closed_days,
                "description": p.description,
                "readcount": db_places[p.place_id].readcount if p.place_id in db_places else None,
                "score": p.final_score
            }
            for p in places
        ]

        # 필수 포함 장소 추가 (리스트 앞쪽에)
        if request.must_visit_places:
            existing_ids = {c['place_id'] for c in candidates}
            for place_id in request.must_visit_places:
                if place_id not in existing_ids:
                    place = await trip_crud.get_place_by_id(db, place_id)
                    if place:
                        candidates.insert(0, {
                            "place_id": place.id,
                            "name": place.name,
                            "category": place.category,
                            "address": place.address,
                            "latitude": place.latitude,
                            "longitude": place.longitude,
                            "image_url": place.image_url,
                            "tags": place.tags,
                            "operating_hours": place.operating_hours,
                            "closed_days": place.closed_days,
                            "description": place.description,
                            "score": 1.0,
                            "must_visit": True
                        })

        return candidates

    async def _generate_with_gpt(
        self,
        candidates: List[dict],
        request: GenerateRequest,
        preference: Optional[UserPreference],
        total_days: int
    ) -> dict:
        """GPT로 일정 초안 생성 (파싱 실패 시 최대 2회 재시도)"""
        # 장소 정보 문자열화 (must_visit 장소는 무조건 포함되도록)
        places_info = self._format_places_for_gpt(candidates, total_days)

        # 선호도 정보
        pref_info = self._format_preference_for_gpt(preference)

        # 필수 방문 장소
        must_visit = [
            c['name'] for c in candidates if c.get('must_visit')
        ]

        prompt = f"""당신은 여행 일정 전문가입니다. 아래 조건에 맞는 {total_days}일 여행 일정을 생성해주세요.

## 여행 정보
- 지역: {request.region}
- 기간: {request.start_date} ~ {request.end_date} ({total_days}일)
- 하루 최대 장소: {request.max_places_per_day}개

## 사용자 선호도
{pref_info}

## 필수 포함 장소
{', '.join(must_visit) if must_visit else '없음'}

## 후보 장소 목록
{places_info}

## 지시사항
1. 각 날짜별로 방문할 장소를 선택하세요
2. 지리적 근접성을 고려하여 동선을 배치하세요
3. 카테고리를 다양하게 배치하세요 (관광지 → 식사 → 카페 등)
4. 필수 포함 장소는 반드시 일정에 포함하세요
5. 사용자 선호도에 맞는 장소를 우선 선택하세요

## 응답 형식 (JSON만 출력)
{{
  "days": [
    {{
      "day_number": 1,
      "theme": "첫째 날 테마 (예: 해운대 해변 투어)",
      "places": [
        {{
          "place_id": 123,
          "order": 1,
          "stay_duration": 60,
          "reason": "선택 이유 (예: 부산 대표 해변)"
        }}
      ]
    }}
  ],
  "trip_summary": "전체 여행 요약 (1-2문장)",
  "day_summaries": {{
    "1": "첫째 날 요약",
    "2": "둘째 날 요약"
  }}
}}"""

        def _call_gpt():
            return self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": "여행 일정 전문가입니다. JSON 형식으로만 응답합니다."
                    },
                    {"role": "user", "content": prompt}
                ],
                max_tokens=3000,
                temperature=0.7
            )

        last_error = None
        for attempt in range(3):
            response = await asyncio.to_thread(_call_gpt)
            result_text = response.choices[0].message.content
            try:
                return self._parse_gpt_response(result_text)
            except ValueError as e:
                last_error = e
                logger.warning(f"GPT 응답 파싱 실패 (시도 {attempt + 1}/3): {e}")

        raise ValueError(f"GPT 응답을 3회 시도 후에도 파싱할 수 없습니다: {last_error}")

    def _format_places_for_gpt(self, candidates: List[dict], total_days: int = 1) -> str:
        """GPT용 장소 정보 포맷팅 (must_visit 장소는 잘리지 않도록)"""
        # must_visit 장소를 먼저 포함
        must_visit_places = [c for c in candidates if c.get('must_visit')]
        other_places = [c for c in candidates if not c.get('must_visit')]

        # 일수에 비례해 전달 장소 수 결정 (하루 최소 8개, 최대 80개)
        target = max(total_days * 8, 30)
        max_others = max(target - len(must_visit_places), 10)
        selected = must_visit_places + other_places[:max_others]

        lines = []
        for c in selected:
            tags_str = ', '.join(c.get('tags', [])[:5]) if c.get('tags') else ''
            must = " [필수]" if c.get('must_visit') else ""
            popularity = f", 인기도: {c.get('readcount', 0)}" if c.get('readcount') else ""
            desc_short = ""
            if c.get('description'):
                desc_short = f", 설명: {c['description'][:40]}..."
            lines.append(
                f"- ID: {c['place_id']}, 이름: {c['name']}, "
                f"카테고리: {c.get('category', '기타')}, "
                f"태그: [{tags_str}], 점수: {c.get('score', 0):.2f}"
                f"{popularity}{desc_short}{must}"
            )
        return '\n'.join(lines)

    def _format_preference_for_gpt(
        self,
        preference: Optional[UserPreference]
    ) -> str:
        """GPT용 선호도 정보 포맷팅"""
        if not preference:
            return "설정된 선호도 없음 (기본 설정 사용)"

        lines = []

        if preference.preferred_themes:
            lines.append(f"- 선호 테마: {', '.join(preference.preferred_themes)}")

        if preference.category_weights:
            high_pref = [
                cat for cat, weight in preference.category_weights.items()
                if weight >= 0.8
            ]
            if high_pref:
                lines.append(f"- 선호 카테고리: {', '.join(high_pref)}")

        if preference.travel_pace:
            pace_desc = {
                "relaxed": "여유로운 여행 (장소당 충분한 시간)",
                "moderate": "보통 페이스",
                "packed": "빡빡한 일정 (많은 장소 방문)"
            }
            lines.append(f"- 여행 스타일: {pace_desc.get(preference.travel_pace, preference.travel_pace)}")

        return '\n'.join(lines) if lines else "기본 설정"

    def _parse_gpt_response(self, text: str) -> dict:
        """GPT 응답 파싱"""
        # 코드 블록 제거
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\n?', '', text)
            text = re.sub(r'\n?```$', '', text)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # JSON 부분만 추출 시도
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                return json.loads(match.group())
            raise ValueError("GPT 응답을 파싱할 수 없습니다")

    def _build_places_by_day(
        self,
        draft: dict,
        place_dict: Dict[int, dict]
    ) -> Dict[int, List[dict]]:
        """GPT 결과를 일차별 장소 딕셔너리로 변환"""
        result = {}

        for day_data in draft.get("days", []):
            day_num = day_data["day_number"]
            places = []

            for place_data in day_data.get("places", []):
                place_id = place_data["place_id"]
                if place_id in place_dict:
                    place = place_dict[place_id].copy()
                    place['order_index'] = place_data.get("order", len(places) + 1)
                    place['suggested_stay_duration'] = place_data.get("stay_duration", 60)
                    place['selection_reason'] = place_data.get("reason", "AI 추천")
                    place['day_number'] = day_num
                    # 키이름 통일 (category를 표준으로)
                    place['place_category'] = place.get('category')
                    place['place_name'] = place.get('name')
                    place['place_address'] = place.get('address')
                    places.append(place)

            result[day_num] = places

        return result

    async def _save_trip(
        self,
        db: AsyncSession,
        user_id: int,
        request: GenerateRequest,
        places_by_day: Dict[int, List[dict]],
        preference: Optional[UserPreference]
    ):
        """Trip 및 Itinerary DB 저장 (단일 트랜잭션)"""
        try:
            # Trip 생성
            trip_data = TripCreate(
                title=request.title,
                start_date=request.start_date,
                end_date=request.end_date,
                region=request.region,
                conditions={
                    "max_places_per_day": request.max_places_per_day,
                    "themes": request.themes,
                    "must_visit_places": request.must_visit_places
                }
            )

            trip = await trip_crud.create_trip(
                db, user_id, trip_data,
                generation_method="ai",
                preference_snapshot=preference_to_snapshot(preference)
            )

            # Itinerary 일괄 생성
            itinerary_items = []
            for day_num, places in places_by_day.items():
                for place in places:
                    itinerary_items.append({
                        "place_id": place["place_id"],
                        "day_number": day_num,
                        "order_index": place.get("order_index", 1),
                        "arrival_time": place.get("suggested_arrival_time"),
                        "stay_duration": place.get("suggested_stay_duration"),
                        "travel_time_from_prev": place.get("travel_time_from_prev"),
                        "transport_mode": place.get("transport_mode"),
                        "memo": place.get("selection_reason")
                    })

            await trip_crud.bulk_create_itineraries(db, trip.id, itinerary_items)

            # Trip 다시 로드 (itineraries 포함)
            return await trip_crud.get_trip_by_id(db, trip.id, user_id)

        except Exception as e:
            logger.error(f"Trip 저장 실패: {e}")
            await db.rollback()
            raise

    def _build_response(
        self,
        trip,
        places_by_day: Dict[int, List[dict]],
        draft: dict,
        request: GenerateRequest,
        total_days: int,
        warnings: List[str] = None
    ) -> GenerateResponse:
        """응답 객체 생성"""
        days = []
        total_places = 0
        total_travel = 0

        for day_num in range(1, total_days + 1):
            places = places_by_day.get(day_num, [])
            day_travel = sum(
                p.get('travel_time_from_prev', 0) or 0 for p in places
            )

            itineraries = [
                GeneratedItinerary(
                    place_id=p['place_id'],
                    place_name=p.get('place_name') or p.get('name'),
                    place_category=p.get('place_category') or p.get('category'),
                    place_address=p.get('place_address') or p.get('address'),
                    latitude=p['latitude'],
                    longitude=p['longitude'],
                    image_url=p.get('image_url'),
                    tags=p.get('tags'),
                    day_number=day_num,
                    order_index=p.get('order_index', 1),
                    suggested_arrival_time=p.get('suggested_arrival_time'),
                    suggested_stay_duration=p.get('suggested_stay_duration', 60),
                    travel_time_from_prev=p.get('travel_time_from_prev'),
                    transport_mode=p.get('transport_mode'),
                    selection_reason=p.get('selection_reason', 'AI 추천')
                )
                for p in places
            ]

            # 일차 테마 찾기
            day_theme = ""
            for d in draft.get("days", []):
                if d["day_number"] == day_num:
                    day_theme = d.get("theme", "")
                    break

            day_summary_text = draft.get("day_summaries", {}).get(str(day_num), "")

            days.append(DaySummary(
                day_number=day_num,
                theme=day_theme,
                itineraries=itineraries,
                total_places=len(places),
                total_travel_time=day_travel,
                summary=day_summary_text
            ))

            total_places += len(places)
            total_travel += day_travel

        # 최적화 점수 계산
        opt_score = self.route_optimizer.calculate_optimization_score(places_by_day)

        # 경고가 있으면 trip_summary에 포함
        trip_summary = draft.get("trip_summary", "AI가 생성한 여행 일정입니다.")
        if warnings:
            trip_summary += " [주의: " + "; ".join(warnings) + "]"

        return GenerateResponse(
            trip_id=trip.id,
            title=request.title,
            region=request.region,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            total_days=total_days,
            total_places=total_places,
            total_travel_time=total_travel,
            optimization_score=round(opt_score, 2),
            trip_summary=trip_summary,
            generation_method="ai"
        )


# 싱글톤 인스턴스
_planner_instance = None


def get_planner_service() -> PlannerService:
    """싱글톤 플래너 서비스 반환"""
    global _planner_instance
    if _planner_instance is None:
        _planner_instance = PlannerService()
    return _planner_instance
