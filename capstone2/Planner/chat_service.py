import json
import re
import logging
import asyncio
from typing import List, Optional, Dict, Any
from openai import OpenAI
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.config import get_config
from core.models import ChatSession, Itinerary, Place, Trip
from Trip import crud as trip_crud
from Planner.dto import ChatRequest, ChatResponse, ChatMessage, ChangeItem

logger = logging.getLogger(__name__)


class ChatService:
    """대화형 일정 수정 서비스"""

    SYSTEM_PROMPT = """당신은 여행 일정 수정을 도와주는 AI 어시스턴트입니다.

반드시 아래 JSON 형식으로만 응답하세요. 자연어 텍스트는 절대 출력하지 마세요.

사용자가 일정 수정을 요청하면:
1. 요청을 정확히 이해합니다
2. 필요한 변경 사항을 파악합니다
3. 변경 사항을 JSON 형식으로 반환합니다

## 지원하는 액션
- add: 새 장소 추가
- remove: 기존 장소 제거
- replace: 장소 교체
- reorder: 순서/일차 변경
- modify: 시간/메모 수정
- question: 추가 정보 필요

## 응답 형식 (JSON만 출력)
{
  "understood": true,
  "action_type": "add|remove|replace|reorder|modify|question",
  "changes": [
    {
      "action": "add",
      "place_name": "추가할 장소명",
      "day_number": 1,
      "order_index": 2
    }
  ],
  "response_message": "사용자에게 보여줄 친절한 응답",
  "needs_confirmation": false,
  "confirmation_question": null
}

## 예시 요청과 응답

사용자: "2일차에 카페 하나 넣어줘"
응답: {"action_type": "add", "changes": [{"action": "add", "category": "카페", "day_number": 2}], "response_message": "2일차에 카페를 추가할게요. 특별히 원하는 분위기나 지역이 있나요?", "needs_confirmation": true}

사용자: "감천문화마을 빼줘"
응답: {"action_type": "remove", "changes": [{"action": "remove", "place_name": "감천문화마을"}], "response_message": "감천문화마을을 일정에서 제거했어요.", "needs_confirmation": false}

사용자: "1일차 순서 바꿔줘, 해운대 먼저"
응답: {"action_type": "reorder", "changes": [{"action": "reorder", "place_name": "해운대해수욕장", "day_number": 1, "new_order": 1}], "response_message": "해운대해수욕장을 1일차 첫 번째로 이동했어요.", "needs_confirmation": false}

사용자: "해운대 체류시간 2시간으로 바꿔줘"
응답: {"action_type": "modify", "changes": [{"action": "modify", "place_name": "해운대해수욕장", "stay_duration": 120}], "response_message": "해운대해수욕장 체류시간을 2시간으로 변경했어요.", "needs_confirmation": false}"""

    def __init__(self):
        config = get_config()
        self.client = OpenAI(api_key=config.openai_api_key)

    async def process_message(
        self,
        db: AsyncSession,
        user_id: int,
        request: ChatRequest
    ) -> ChatResponse:
        """
        대화 메시지 처리

        1. 세션 로드 또는 생성
        2. 현재 일정 컨텍스트 구성
        3. GPT 호출
        4. 변경 사항 적용
        5. 세션 업데이트
        """
        # 1. 여행 및 일정 로드
        trip = await trip_crud.get_trip_by_id(db, request.trip_id, user_id)
        if not trip:
            return ChatResponse(
                session_id=0,
                response="여행을 찾을 수 없습니다.",
                needs_confirmation=False
            )

        # 2. 세션 로드 또는 생성
        session = await self._get_or_create_session(
            db, user_id, request.trip_id, request.session_id
        )

        # 3. 현재 일정 컨텍스트 구성
        itinerary_context = self._format_itineraries(trip.itineraries)

        # 4. 요청 내용 기반으로 관련 장소 필터링
        hints = self._extract_query_hints(request.message)
        available_places = await self._get_places_by_hints(db, trip, hints)
        places_context = self._format_available_places(available_places)

        # 5. 대화 히스토리 구성
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "system", "content": f"## 현재 일정\n{itinerary_context}"},
            {"role": "system", "content": f"## 추가 가능한 장소\n{places_context}"}
        ]

        # 이전 대화 추가 (최근 10개)
        if session.messages:
            for msg in session.messages[-10:]:
                messages.append(msg)

        # 새 메시지 추가
        messages.append({"role": "user", "content": request.message})

        # 6. GPT 호출 (파싱 실패 시 최대 2회 재시도)
        def _call_gpt():
            return self.client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=1000,
                temperature=0.5,
                response_format={"type": "json_object"}
            )

        result = None
        for attempt in range(3):
            gpt_response = await asyncio.to_thread(_call_gpt)
            result_text = gpt_response.choices[0].message.content
            parsed = self._parse_response(result_text)
            # _parse_response는 실패 시 fallback dict를 반환하므로 action_type으로 판별
            if parsed.get("action_type"):
                result = parsed
                break
            logger.warning(f"채팅 GPT 응답 파싱 불완전 (시도 {attempt + 1}/3)")

        if result is None:
            result = {
                "understood": False,
                "action_type": "question",
                "response_message": "요청을 이해하지 못했어요. 다시 한 번 말씀해 주시겠어요?",
                "needs_confirmation": False
            }

        # 7. 변경 사항 적용 (확인 불필요한 경우)
        changes_made = None
        updated_trip = None
        response_message = result.get("response_message", "요청을 처리했습니다.")

        if not result.get("needs_confirmation") and result.get("action_type") != "question":
            changes_made, updated_trip = await self._apply_changes(
                db, user_id, trip, result.get("changes", []), available_places
            )
            # 변경 요청은 했는데 실제로 적용된 게 없으면 사용자에게 알림
            if result.get("changes") and not changes_made:
                response_message = "요청하신 장소를 현재 목록에서 찾을 수 없어 변경하지 못했어요. 다른 장소명으로 다시 시도해 주세요."

        # 8. 세션 업데이트
        await self._update_session(
            db, session,
            request.message,
            response_message
        )

        return ChatResponse(
            session_id=session.id,
            response=response_message,
            changes_made=[
                ChangeItem(action=c["action"], details=c)
                for c in (changes_made or [])
            ] if changes_made else None,
            updated_trip=updated_trip,
            needs_confirmation=result.get("needs_confirmation", False),
            confirmation_message=result.get("confirmation_question")
        )

    async def _get_or_create_session(
        self,
        db: AsyncSession,
        user_id: int,
        trip_id: int,
        session_id: Optional[int]
    ) -> ChatSession:
        """세션 로드 또는 생성"""
        if session_id:
            result = await db.execute(
                select(ChatSession).where(
                    ChatSession.id == session_id,
                    ChatSession.user_id == user_id
                )
            )
            session = result.scalar_one_or_none()
            if session:
                return session

        # 새 세션 생성
        session = ChatSession(
            user_id=user_id,
            trip_id=trip_id,
            messages=[],
            current_state="modifying"
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session

    async def _update_session(
        self,
        db: AsyncSession,
        session: ChatSession,
        user_message: str,
        assistant_response: str
    ):
        """세션 히스토리 업데이트"""
        messages = session.messages or []
        messages.append({"role": "user", "content": user_message})
        messages.append({"role": "assistant", "content": assistant_response})

        # 최근 20개만 유지
        session.messages = messages[-20:]
        await db.commit()

    def _format_itineraries(self, itineraries: List[Itinerary]) -> str:
        """일정 포맷팅"""
        if not itineraries:
            return "일정이 비어있습니다."

        lines = []
        current_day = 0

        for it in sorted(itineraries, key=lambda x: (x.day_number, x.order_index)):
            if it.day_number != current_day:
                current_day = it.day_number
                lines.append(f"\n### {current_day}일차")

            place = it.place
            time_str = it.arrival_time.strftime("%H:%M") if it.arrival_time else "미정"
            lines.append(
                f"  {it.order_index}. {place.name} ({place.category}) "
                f"[ID: {it.id}] - {time_str}"
            )

        return '\n'.join(lines)

    def _extract_query_hints(self, message: str) -> dict:
        """사용자 메시지에서 카테고리 힌트 추출 (GPT 없이 키워드 매칭)"""
        CATEGORY_MAP = {
            "카페": ["카페", "커피", "디저트", "베이커리", "브런치"],
            "맛집": ["맛집", "식당", "음식", "밥", "점심", "저녁", "먹을", "레스토랑",
                    "고기", "해산물", "국밥", "냉면", "분식", "피자", "치킨"],
            "관광지": ["관광지", "명소", "관광", "여행지", "볼거리", "경치", "뷰"],
            "문화시설": ["박물관", "미술관", "전시", "문화", "공연", "갤러리", "역사"],
            "자연": ["공원", "산", "바다", "해변", "해수욕장", "자연", "트레킹", "등산", "숲"],
            "쇼핑": ["쇼핑", "마트", "시장", "백화점", "쇼핑몰", "면세점"],
            "체험": ["체험", "액티비티", "놀이", "테마파크", "워터파크"],
        }

        found = []
        for cat, keywords in CATEGORY_MAP.items():
            if any(kw in message for kw in keywords):
                found.append(cat)

        return {"categories": found}

    async def _get_places_by_hints(
        self,
        db: AsyncSession,
        trip: Trip,
        hints: dict
    ) -> List[Place]:
        """요청 힌트 기반으로 관련 장소 인기순 조회"""
        from sqlalchemy import nulls_last

        categories = hints.get("categories", [])
        collected: List[Place] = []
        seen_ids: set = set()

        # 힌트 카테고리가 있으면 해당 카테고리 위주로 조회 (카테고리당 30개)
        if categories:
            for cat in categories:
                query = select(Place)
                if trip.region:
                    query = query.where(Place.address.contains(trip.region))
                query = (
                    query
                    .where(Place.category == cat)
                    .order_by(nulls_last(Place.readcount.desc()))
                    .limit(30)
                )
                result = await db.execute(query)
                for p in result.scalars().all():
                    if p.id not in seen_ids:
                        collected.append(p)
                        seen_ids.add(p.id)

        # 힌트가 없거나 결과 부족 시 전체 인기순으로 보완 (최대 100개)
        if len(collected) < 50:
            query = select(Place)
            if trip.region:
                query = query.where(Place.address.contains(trip.region))
            query = (
                query
                .order_by(nulls_last(Place.readcount.desc()))
                .limit(100)
            )
            result = await db.execute(query)
            for p in result.scalars().all():
                if p.id not in seen_ids:
                    collected.append(p)
                    seen_ids.add(p.id)
                if len(collected) >= 100:
                    break

        return collected

    def _format_available_places(self, places: List[Place]) -> str:
        """추가 가능한 장소 포맷팅 (최대 50개 GPT 전달)"""
        if not places:
            return "추가 가능한 장소가 없습니다."

        lines = []
        for p in places[:50]:
            tags = ', '.join(p.tags[:2]) if p.tags else ''
            lines.append(f"- {p.name} ({p.category}) [ID: {p.id}] {tags}")

        return '\n'.join(lines)

    def _parse_response(self, text: str) -> dict:
        """GPT 응답 파싱"""
        text = text.strip()

        # 코드 블록 제거
        if text.startswith("```"):
            text = re.sub(r'^```(?:json)?\n?', '', text)
            text = re.sub(r'\n?```$', '', text)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass

            # 파싱 실패 시 기본 응답
            return {
                "understood": False,
                "action_type": "question",
                "response_message": text,
                "needs_confirmation": False
            }

    def _find_place_by_name(
        self,
        name: str,
        places: List[Place]
    ) -> Optional[Place]:
        """장소명으로 정확한 매칭 (부분매칭 개선)"""
        if not name:
            return None

        name_lower = name.lower().strip()

        # 1. 정확 매칭
        for p in places:
            if p.name.lower() == name_lower:
                return p

        # 2. 포함 매칭 (이름 길이 차이가 가장 작은 것 선택)
        best_match = None
        best_len_diff = float('inf')
        for p in places:
            pname = p.name.lower()
            if name_lower in pname or pname in name_lower:
                diff = abs(len(pname) - len(name_lower))
                if diff < best_len_diff:
                    best_len_diff = diff
                    best_match = p

        return best_match

    def _find_itinerary_by_name(
        self,
        name: str,
        itineraries: List[Itinerary]
    ) -> Optional[Itinerary]:
        """일정에서 장소명으로 Itinerary 찾기"""
        if not name:
            return None

        name_lower = name.lower().strip()

        # 1. 정확 매칭
        for it in itineraries:
            if it.place.name.lower() == name_lower:
                return it

        # 2. 포함 매칭
        best_match = None
        best_len_diff = float('inf')
        for it in itineraries:
            pname = it.place.name.lower()
            if name_lower in pname or pname in name_lower:
                diff = abs(len(pname) - len(name_lower))
                if diff < best_len_diff:
                    best_len_diff = diff
                    best_match = it

        return best_match

    async def _apply_changes(
        self,
        db: AsyncSession,
        user_id: int,
        trip: Trip,
        changes: List[dict],
        available_places: List[Place]
    ) -> tuple:
        """변경 사항 적용"""
        applied_changes = []
        place_id_dict = {p.id: p for p in available_places}

        for change in changes:
            action = change.get("action")

            try:
                if action == "add":
                    result = await self._apply_add(db, trip, change, available_places, place_id_dict)
                    if result:
                        applied_changes.append(result)

                elif action == "remove":
                    result = await self._apply_remove(db, trip, change)
                    if result:
                        applied_changes.append(result)

                elif action == "replace":
                    result = await self._apply_replace(db, trip, change, available_places, place_id_dict)
                    if result:
                        applied_changes.append(result)

                elif action == "reorder":
                    result = await self._apply_reorder(db, trip, change)
                    if result:
                        applied_changes.append(result)

                elif action == "modify":
                    result = await self._apply_modify(db, trip, change)
                    if result:
                        applied_changes.append(result)

            except Exception as e:
                logger.error(f"변경 사항 적용 실패 ({action}): {e}")

        # 업데이트된 여행 정보 조회 (user_id 포함하여 보안 유지)
        updated = await trip_crud.get_trip_by_id(db, trip.id, user_id)

        trip_dict = None
        if updated:
            trip_dict = {
                "id": updated.id,
                "title": updated.title,
                "itineraries": [
                    {
                        "id": it.id,
                        "place_name": it.place.name,
                        "day_number": it.day_number,
                        "order_index": it.order_index
                    }
                    for it in sorted(
                        updated.itineraries,
                        key=lambda x: (x.day_number, x.order_index)
                    )
                ]
            }

        return applied_changes, trip_dict

    async def _apply_add(
        self, db, trip, change, available_places, place_id_dict
    ) -> Optional[dict]:
        """장소 추가"""
        existing_ids = {it.place_id for it in trip.itineraries}
        place = None

        if change.get("place_name"):
            place = self._find_place_by_name(change["place_name"], available_places)

        if not place and change.get("place_id"):
            place = place_id_dict.get(change["place_id"])

        if not place and change.get("category"):
            cat = change["category"]
            for p in available_places:
                if p.id not in existing_ids and p.category and cat in p.category:
                    place = p
                    break

        if place:
            from Trip.dto import ItineraryCreate
            day = change.get("day_number", 1)
            order = change.get("order_index", 99)

            await trip_crud.create_itinerary(
                db, trip.id,
                ItineraryCreate(
                    place_id=place.id,
                    day_number=day,
                    order_index=order
                )
            )
            return {"action": "add", "place_name": place.name, "day_number": day}
        return None

    async def _apply_remove(self, db, trip, change) -> Optional[dict]:
        """장소 제거"""
        target = self._find_itinerary_by_name(
            change.get("place_name", ""), trip.itineraries
        )
        if target:
            await trip_crud.delete_itinerary(db, target.id)
            return {"action": "remove", "place_name": target.place.name}
        return None

    async def _apply_replace(
        self, db, trip, change, available_places, place_id_dict
    ) -> Optional[dict]:
        """장소 교체"""
        old_it = self._find_itinerary_by_name(
            change.get("old_place", ""), trip.itineraries
        )
        new_place = self._find_place_by_name(
            change.get("new_place", ""), available_places
        )
        if not new_place and change.get("place_id"):
            new_place = place_id_dict.get(change["place_id"])

        if old_it and new_place:
            from Trip.dto import ItineraryUpdate
            await trip_crud.update_itinerary(
                db, old_it.id,
                ItineraryUpdate(place_id=new_place.id)
            )
            return {
                "action": "replace",
                "old_place": old_it.place.name,
                "new_place": new_place.name
            }
        return None

    async def _apply_reorder(self, db, trip, change) -> Optional[dict]:
        """순서 변경 후 같은 날 전체 order_index 재정렬"""
        target = self._find_itinerary_by_name(
            change.get("place_name", ""), trip.itineraries
        )
        if not target:
            return None

        new_day = change.get("day_number") or target.day_number
        new_order = change.get("new_order")

        if new_order is None:
            return None

        from Trip.dto import ItineraryUpdate

        # 대상 장소를 먼저 원하는 day/order로 이동
        await trip_crud.update_itinerary(
            db, target.id,
            ItineraryUpdate(day_number=new_day, order_index=new_order)
        )

        # 같은 날의 나머지 장소들을 충돌 없이 재정렬
        same_day = sorted(
            [it for it in trip.itineraries if it.day_number == new_day],
            key=lambda x: (x.order_index, x.id)
        )

        # 대상 장소를 원하는 위치에 끼워넣고 나머지를 순서대로 밀어냄
        others = [it for it in same_day if it.id != target.id]
        # new_order 위치에 target을 삽입
        insert_at = max(0, min(new_order - 1, len(others)))
        ordered = others[:insert_at] + [target] + others[insert_at:]

        for idx, it in enumerate(ordered, start=1):
            if it.order_index != idx:
                await trip_crud.update_itinerary(
                    db, it.id,
                    ItineraryUpdate(order_index=idx)
                )

        return {
            "action": "reorder",
            "place_name": target.place.name,
            "day_number": new_day,
            "order_index": new_order
        }

    async def _apply_modify(self, db, trip, change) -> Optional[dict]:
        """시간/메모 수정"""
        target = self._find_itinerary_by_name(
            change.get("place_name", ""), trip.itineraries
        )
        if not target:
            return None

        from Trip.dto import ItineraryUpdate
        update_data = {}

        if change.get("stay_duration") is not None:
            update_data["stay_duration"] = change["stay_duration"]
        if change.get("memo") is not None:
            update_data["memo"] = change["memo"]
        if change.get("arrival_time") is not None:
            update_data["arrival_time"] = change["arrival_time"]

        if update_data:
            await trip_crud.update_itinerary(
                db, target.id,
                ItineraryUpdate(**update_data)
            )
            return {"action": "modify", "place_name": target.place.name, **update_data}
        return None

    async def get_chat_history(
        self,
        db: AsyncSession,
        user_id: int,
        session_id: int
    ) -> Optional[ChatSession]:
        """대화 히스토리 조회"""
        result = await db.execute(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id
            )
        )
        return result.scalar_one_or_none()


# 싱글톤 인스턴스
_chat_service_instance = None


def get_chat_service() -> ChatService:
    """싱글톤 채팅 서비스 반환"""
    global _chat_service_instance
    if _chat_service_instance is None:
        _chat_service_instance = ChatService()
    return _chat_service_instance
