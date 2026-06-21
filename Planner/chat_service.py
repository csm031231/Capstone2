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
from Planner.constants import REGION_PREFIX, CHAT_CONTEXT_LIMIT, CHAT_STORAGE_LIMIT, GPT_CHAT_MAX_TOKENS

logger = logging.getLogger(__name__)


class ChatService:
    """대화형 일정 수정 서비스"""

    SYSTEM_PROMPT = """당신은 여행 일정 수정을 도와주는 AI 어시스턴트입니다.

반드시 아래 JSON 형식으로만 응답하세요. 자연어 텍스트는 절대 출력하지 마세요.

사용자가 일정 수정을 요청하면:
1. 요청을 정확히 이해합니다
2. 필요한 변경 사항을 모두 파악합니다 (복합 요청이면 여러 action을 changes 배열에 모두 담으세요)
3. 변경 사항을 JSON 형식으로 반환합니다

## 지원하는 액션
- add: 새 장소 추가 (category: 카테고리명 / tags: 속성 태그 배열 / place_name: 특정 장소명)
  태그 예시: ["야경", "포토스팟", "힐링", "바다", "숲", "조용한", "실내", "체험"]
- remove: 기존 장소 제거 (no_fill: true 추가 시 빈 자리를 자동으로 채우지 않고 그대로 비워둠)
- replace: 장소 교체
- reorder: 특정 장소 하나를 특정 번호 위치로 이동 (new_order 필드 사용, 반드시 현재 일정에서 정확한 위치 번호 지정)
- swap_places: 같은 일차 내 두 장소의 위치를 서로 교환 (place_a, place_b 필드 사용)
- swap_days: 두 일차의 모든 장소를 통째로 교환 (day_a, day_b 필드 사용)
- modify: 특정 장소 하나의 체류시간/시작시간/메모 수정 (arrival_time: "HH:MM", stay_duration: 분 단위)
- bulk_modify: 하루 전체 또는 카테고리 단위 일괄 수정
  (day_number: 일차 또는 null=전체 / category: 카테고리 필터 또는 null /
   stay_duration: 고정값(분) / stay_duration_delta: 증감값(분, 음수=축소) /
   start_time_shift: 시작 시간 이동(분, 음수=앞당김))
- regenerate: 일정 전체 또는 특정 일차를 조건에 맞게 새로 생성
  (scope: "full"=전체재생성, 숫자=특정일차 / themes: 테마 배열 / requirements: 사용자 요구사항 자유형 문자열)
- change_duration: 여행 전체 기간(일수) 변경 — 늘리거나 줄이기
  (new_total_days: 새 총 일수 / delta_days: 증감일수, 양수=늘리기, 음수=줄이기)
  늘릴 때는 추가 일차 일정을 자동 생성, 줄일 때는 마지막 일차부터 삭제
- optimize_route: 현재 장소는 유지하고 이동 동선만 최적화
- question: 추가 정보 필요

## 복합 요청 처리 원칙 (매우 중요)
하나의 메시지에 여러 요청이 담겨있으면 반드시 changes 배열에 모든 액션을 담으세요.
action_type은 가장 대표적인 액션 하나를 쓰되, "compound"를 써도 됩니다.

예: "12시 이후로 시작하고 장소 하나 줄여줘"
→ changes에 [modify(첫 장소 arrival_time="12:00"), remove(덜 중요한 장소)] 두 개를 모두 담는다.

예: "오전 일정 빼고 오후 2시부터 시작하게 해줘"
→ changes에 [remove(오전 장소들), modify(첫 번째 남은 장소 arrival_time="14:00")] 담는다.

예: "카페 빼고 체류시간도 줄여줘"
→ changes에 [remove(카페), modify(다른 장소 stay_duration 축소)] 담는다.

## 언제 어떤 액션을 쓸지 판단 기준
- 특정 장소 하나를 추가/제거/교체/시간수정 → add/remove/replace/modify
- "야경 있는 곳", "포토스팟", "조용한 곳", "실내 명소" 등 분위기·속성 기반 추가 → add (tags 필드 사용)
- "A랑 B 자리 바꿔줘", "A와 B 순서 교환" 등 두 장소를 서로 맞교환 → swap_places (place_a, place_b에 각각 장소명)
- "A를 첫 번째로", "B를 3번째로" 등 특정 장소를 특정 위치로 이동 → reorder (new_order에 반드시 정확한 위치 번호)
- "N일차랑 M일차 바꿔줘" 등 일차 전체 교환 → swap_days
- "X 테마로 바꿔줘", "전체 다시 짜줘", "힐링/쇼핑/야경 위주로" 등 대규모 재구성 → regenerate
- "하루 더", "이틀 줄여", "3박4일로 바꿔" 등 기간 자체 변경 → change_duration
- "동선 최적화해줘", "이동거리 줄여줘" → optimize_route
- "힘들다", "빡세다", "너무 많아" 등 피로·부담 호소 → bulk_modify(stay_duration_delta=-20) + remove(덜 중요한 장소) 복합 사용
- "넉넉하게", "여유있게", "느긋하게" 등 여유 요청 → 덜 중요한 장소 remove(no_fill: true) 1~2개로 일정 줄이기
- "간소화", "단순하게", "줄여줘" → 덜 중요한 장소 remove(no_fill: true) 여러 개
- "그자리 비워놔", "빈 자리로 놔둬", "채우지 마" 등 → remove에 no_fill: true 추가
- "체류시간 다 늘려줘/줄여줘", "1일차 전체 30분 앞당겨줘", "맛집 체류시간 1시간으로" → bulk_modify
- "~시부터 시작", "~시 이후로" → 첫 장소에 modify(arrival_time) 적용하면 이후 모든 장소 시간이 자동 연쇄 조정됨
- 요청이 포괄적이어서 선택지가 여러 개인 경우 → needs_confirmation: true 설정

## 응답 형식 (JSON만 출력)
{
  "understood": true,
  "action_type": "add|remove|replace|reorder|swap_places|swap_days|modify|regenerate|change_duration|optimize_route|compound|question",
  "changes": [
    {
      "action": "modify",
      "place_name": "첫 번째 장소명",
      "arrival_time": "12:00"
    },
    {
      "action": "remove",
      "place_name": "덜 중요한 장소명"
    }
  ],
  "response_message": "사용자에게 보여줄 친절한 응답",
  "needs_confirmation": false,
  "confirmation_question": null
}

## replace 액션의 상세 필드
{
  "action": "replace",
  "day_number": 2,
  "source_place_id": 456,
  "old_place": "뺄 장소명",
  "target_category": "카페",
  "target_search_keyword": "스타벅스 해운대점"
}

## needs_confirmation 활용 가이드
- "아무 카페나", "뭔가 추가해줘"처럼 기준이 없는 요청 → needs_confirmation: true
- 카테고리+일차가 명확하면 바로 처리 (needs_confirmation: false)

## 예시 요청과 응답

사용자: "2일차에 카페 하나 넣어줘"
응답: {"action_type": "add", "changes": [{"action": "add", "category": "카페", "day_number": 2}], "response_message": "2일차에 카페를 추가할게요!", "needs_confirmation": false}

사용자: "감천문화마을 빼줘"
응답: {"action_type": "remove", "changes": [{"action": "remove", "place_name": "감천문화마을"}], "response_message": "감천문화마을을 일정에서 제거했어요.", "needs_confirmation": false}

사용자: "사라오름 빼고 그자리 비워놔"
응답: {"action_type": "remove", "changes": [{"action": "remove", "place_name": "사라오름", "no_fill": true}], "response_message": "사라오름을 빼고 그 자리를 비워뒀어요.", "needs_confirmation": false}

사용자: "2일차 카페를 스타벅스 해운대점으로 바꿔줘"
응답: {"action_type": "replace", "changes": [{"action": "replace", "day_number": 2, "source_place_id": null, "old_place": null, "target_category": "카페", "target_search_keyword": "스타벅스 해운대점"}], "response_message": "2일차 카페를 스타벅스 해운대점으로 교체할게요!", "needs_confirmation": false}

사용자: "1일차 순서 바꿔줘, 해운대 먼저"
응답: {"action_type": "reorder", "changes": [{"action": "reorder", "place_name": "해운대해수욕장", "day_number": 1, "new_order": 1}], "response_message": "해운대해수욕장을 1일차 첫 번째로 이동했어요.", "needs_confirmation": false}

사용자: "해운대 체류시간 2시간으로 바꿔줘"
응답: {"action_type": "modify", "changes": [{"action": "modify", "place_name": "해운대해수욕장", "stay_duration": 120}], "response_message": "해운대해수욕장 체류시간을 2시간으로 변경했어요.", "needs_confirmation": false}

사용자: "1일차를 12시 이후로 조정하고 간소화해줘"
응답: {"action_type": "compound", "changes": [{"action": "modify", "place_name": "1일차 첫 번째 장소명", "arrival_time": "12:00"}, {"action": "remove", "place_name": "1일차에서 덜 중요한 장소명"}], "response_message": "1일차 시작을 12시로 조정하고 장소 하나를 줄였어요!", "needs_confirmation": false}

사용자: "오후 2시부터 시작하게 바꿔줘"
응답: {"action_type": "modify", "changes": [{"action": "modify", "place_name": "첫 번째 장소명", "arrival_time": "14:00"}], "response_message": "첫 일정을 오후 2시부터 시작하도록 변경했어요. 이후 일정도 자동으로 조정됩니다.", "needs_confirmation": false}

사용자: "일정이 너무 빡세"
응답: {"action_type": "compound", "changes": [{"action": "remove", "place_name": "가장 덜 중요한 장소명"}, {"action": "modify", "place_name": "체류 시간이 짧은 장소명", "stay_duration": 90}], "response_message": "일정이 빡빡하군요! 장소 하나를 빼고 체류 시간도 여유있게 조정했어요.", "needs_confirmation": false}

사용자: "간소화해줘"
응답: {"action_type": "remove", "changes": [{"action": "remove", "place_name": "덜 중요한 장소명1", "no_fill": true}, {"action": "remove", "place_name": "덜 중요한 장소명2", "no_fill": true}], "response_message": "일정을 간소화했어요!", "needs_confirmation": false}

사용자: "수월봉 빼고 일정 좀 넉넉하게 해줘"
응답: {"action_type": "compound", "changes": [{"action": "remove", "place_name": "수월봉", "no_fill": true}, {"action": "remove", "place_name": "일정 중 덜 중요한 장소명", "no_fill": true}], "response_message": "수월봉을 빼고 장소 하나를 더 줄여서 여유있게 조정했어요.", "needs_confirmation": false}

사용자: "힐링 테마로 전체 다시 짜줘"
응답: {"action_type": "regenerate", "changes": [{"action": "regenerate", "scope": "full", "themes": ["힐링", "자연"], "requirements": "힐링·자연 위주, 복잡한 도심보다 조용한 명소"}], "response_message": "전체 일정을 힐링 테마로 새로 구성할게요!", "needs_confirmation": false}

사용자: "2일차를 쇼핑 위주로 바꿔줘"
응답: {"action_type": "regenerate", "changes": [{"action": "regenerate", "scope": 2, "themes": ["쇼핑"], "requirements": "쇼핑·맛집 위주로 배치"}], "response_message": "2일차를 쇼핑 중심으로 재구성할게요!", "needs_confirmation": false}

사용자: "해운대랑 감천문화마을 자리 바꿔줘"
응답: {"action_type": "swap_places", "changes": [{"action": "swap_places", "place_a": "해운대해수욕장", "place_b": "감천문화마을"}], "response_message": "해운대해수욕장과 감천문화마을의 순서를 교환할게요!", "needs_confirmation": false}

사용자: "1일차랑 4일차 바꿔줘"
응답: {"action_type": "swap_days", "changes": [{"action": "swap_days", "day_a": 1, "day_b": 4}], "response_message": "1일차와 4일차를 통째로 교환할게요!", "needs_confirmation": false}

사용자: "동선이 너무 비효율적이야, 최적화해줘"
응답: {"action_type": "optimize_route", "changes": [{"action": "optimize_route"}], "response_message": "이동 동선을 최적화할게요!", "needs_confirmation": false}

사용자: "카페빼고 식당 2개 넣어줘"
응답: {"action_type": "compound", "changes": [{"action": "remove", "place_name": "카페명"}, {"action": "add", "category": "맛집"}, {"action": "add", "category": "맛집"}], "response_message": "카페를 빼고 식당 2곳을 추가할게요!", "needs_confirmation": false}

사용자: "하루 더 추가해줘"
응답: {"action_type": "change_duration", "changes": [{"action": "change_duration", "delta_days": 1}], "response_message": "여행을 하루 늘려서 새 일차 일정을 만들게요!", "needs_confirmation": false}

사용자: "이틀 줄여줘"
응답: {"action_type": "change_duration", "changes": [{"action": "change_duration", "delta_days": -2}], "response_message": "여행을 이틀 줄이고 마지막 두 일차 일정을 삭제할게요!", "needs_confirmation": false}

사용자: "야경 볼 수 있는 곳 추가해줘"
응답: {"action_type": "add", "changes": [{"action": "add", "tags": ["야경", "뷰맛집"], "day_number": null}], "response_message": "야경 명소를 일정에 추가할게요!", "needs_confirmation": false}

사용자: "포토스팟 하나 넣어줘"
응답: {"action_type": "add", "changes": [{"action": "add", "tags": ["사진명소", "포토스팟", "전망"]}], "response_message": "사진 찍기 좋은 명소를 추가할게요!", "needs_confirmation": false}

사용자: "체류시간 다 30분씩 줄여줘"
응답: {"action_type": "bulk_modify", "changes": [{"action": "bulk_modify", "stay_duration_delta": -30}], "response_message": "전체 일정의 체류시간을 30분씩 줄였어요!", "needs_confirmation": false}

사용자: "1일차 전체 1시간 앞당겨줘"
응답: {"action_type": "bulk_modify", "changes": [{"action": "bulk_modify", "day_number": 1, "start_time_shift": -60}], "response_message": "1일차 일정을 1시간 앞당겼어요!", "needs_confirmation": false}

사용자: "맛집 체류시간 다 1시간으로 맞춰줘"
응답: {"action_type": "bulk_modify", "changes": [{"action": "bulk_modify", "category": "맛집", "stay_duration": 60}], "response_message": "식당 체류시간을 모두 1시간으로 조정했어요!", "needs_confirmation": false}

사용자: "일정이 너무 빡빡해"
응답: {"action_type": "compound", "changes": [{"action": "bulk_modify", "stay_duration_delta": -15}, {"action": "remove", "place_name": "덜 중요한 장소명"}], "response_message": "전체 체류시간을 15분씩 줄이고 장소 하나를 뺐어요. 좀 여유로워졌을 거예요!", "needs_confirmation": false}"""

    def __init__(self):
        config = get_config()
        self.client = OpenAI(api_key=config.openai_api_key)
        self.model = config.openai_model

    async def process_message(
        self,
        db: AsyncSession,
        user_id: int,
        request: ChatRequest
    ) -> ChatResponse:
        """
        대화 메시지 처리

        1. 세션 로드 또는 생성
        2. 현재 일정 컨텍스트 구성.
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

        # 축제 요청 시 여행 기간 정보 컨텍스트 추가 (GPT가 날짜 범위를 인식하도록)
        if hints.get("has_festival"):
            festival_places = [p for p in available_places if p.category == "축제/행사"]
            if festival_places:
                festival_lines = []
                for p in festival_places:
                    date_info = ""
                    if hasattr(p, "event_start_date") and p.event_start_date:
                        date_info = f" ({p.event_start_date}~{p.event_end_date})"
                    festival_lines.append(f"- {p.name} [ID: {p.id}]{date_info}")
                messages.append({
                    "role": "system",
                    "content": (
                        f"## 여행 기간({trip.start_date}~{trip.end_date}) 내 축제 목록\n"
                        + "\n".join(festival_lines)
                        + "\n위 축제들을 일정에 추가(add 액션)하세요."
                    )
                })
            else:
                messages.append({
                    "role": "system",
                    "content": f"여행 기간({trip.start_date}~{trip.end_date}) 내 해당 지역에서 진행되는 축제를 찾지 못했습니다."
                })

        # 이전 대화 추가 (최근 CHAT_CONTEXT_LIMIT개)
        if session.messages:
            for msg in session.messages[-CHAT_CONTEXT_LIMIT:]:
                messages.append(msg)

        # 새 메시지 추가
        messages.append({"role": "user", "content": request.message})

        # 6. GPT 호출 (파싱 실패 시 최대 2회 재시도)
        def _call_gpt():
            return self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=GPT_CHAT_MAX_TOKENS,
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
            requested_changes = result.get("changes", [])
            changes_made, updated_trip, warnings = await self._apply_changes(
                db, user_id, trip, requested_changes, available_places
            )

            if changes_made:
                # 실제 적용된 내용을 구체적인 문장으로 구성
                actual_msg = self._build_applied_message(changes_made)
                if actual_msg:
                    response_message = actual_msg

                # 일부 요청이 처리되지 않은 경우 안내
                if requested_changes and len(changes_made) < len(requested_changes):
                    response_message += "\n(일부 요청은 처리하지 못했습니다. 장소명을 확인해 주세요.)"

            elif requested_changes:
                # 변경을 요청했지만 아무것도 적용되지 않음
                response_message = (
                    "요청하신 변경을 처리하지 못했습니다. "
                    "장소 이름이 일정에 없거나 조건에 맞는 장소를 찾지 못했어요. "
                    "다시 한 번 말씀해 주시겠어요?"
                )

            # _apply_changes 수준 경고 (교체 차단 등) — 최우선
            if warnings:
                response_message = " ".join(warnings)

        # 8. 세션 업데이트 (실제 변경 결과도 함께 저장해서 GPT가 다음 대화에서 참조 가능)
        await self._update_session(
            db, session,
            request.message,
            response_message,
            changes_made=changes_made
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
        """세션 로드 또는 생성

        우선순위:
        1) 명시적 session_id → 해당 세션 반환
        2) session_id 없음 → 같은 trip의 최근 세션 재사용 (대화 맥락 유지)
        3) 기존 세션 없음 → 새 세션 생성
        """
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

        # session_id가 없거나 찾지 못한 경우 → 같은 trip의 최근 세션 재사용
        result = await db.execute(
            select(ChatSession)
            .where(
                ChatSession.user_id == user_id,
                ChatSession.trip_id == trip_id
            )
            .order_by(ChatSession.id.desc())
            .limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        # 기존 세션 없으면 새로 생성
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
        assistant_response: str,
        changes_made: Optional[list] = None
    ):
        """세션 히스토리 업데이트.
        changes_made가 있으면 assistant 메시지에 실제 변경 결과를 덧붙여
        GPT가 다음 대화에서 실제로 무엇이 바뀌었는지 참조할 수 있게 한다.
        """
        import json as _json
        from sqlalchemy.orm.attributes import flag_modified

        messages = session.messages or []
        messages.append({"role": "user", "content": user_message})

        if changes_made:
            result_summary = _json.dumps(changes_made, ensure_ascii=False)
            full_response = f"{assistant_response}\n[변경결과: {result_summary}]"
        else:
            full_response = assistant_response

        messages.append({"role": "assistant", "content": full_response})

        # 최근 CHAT_STORAGE_LIMIT개만 유지
        session.messages = messages[-CHAT_STORAGE_LIMIT:]
        flag_modified(session, "messages")
        await db.commit()

    def _build_applied_message(self, changes_made: list) -> str:
        """실제 적용된 변경 사항을 구체적인 문장으로 변환"""
        if not changes_made:
            return ""

        parts = []
        warn_parts = []

        for c in changes_made:
            action = c.get("action", "")

            if action == "add":
                pname = c.get("place_name", "장소")
                day   = c.get("day_number")
                t     = c.get("arrival_time", "")
                if hasattr(t, "strftime"):
                    t = t.strftime("%H:%M")
                day_str  = f"{day}일차 " if day else ""
                time_str = f" ({t})" if t else ""
                parts.append(f"{day_str}{pname}을(를) 추가했습니다{time_str}")

            elif action == "remove":
                pname  = c.get("place_name", "장소")
                filled = c.get("filled_with")
                if filled:
                    parts.append(f"{pname}을(를) 삭제하고 {filled}로 채웠습니다")
                elif c.get("no_fill"):
                    parts.append(f"{pname}을(를) 삭제하고 그 자리를 비워뒀습니다")
                else:
                    parts.append(f"{pname}을(를) 일정에서 삭제했습니다")

            elif action == "replace":
                old = c.get("old_place", "기존 장소")
                new = c.get("new_place", "새 장소")
                day = c.get("day_number")
                day_str = f" ({day}일차)" if day else ""
                parts.append(f"{old}을(를) {new}으로 교체했습니다{day_str}")

            elif action == "modify":
                pname   = c.get("place_name", "장소")
                details = []
                t = c.get("arrival_time")
                if t:
                    if hasattr(t, "strftime"):
                        t = t.strftime("%H:%M")
                    details.append(f"방문 시간 → {t}")
                if c.get("stay_duration"):
                    details.append(f"체류 시간 → {c['stay_duration']}분")
                if c.get("memo") is not None:
                    details.append("메모 수정")
                parts.append(f"{pname}: {', '.join(details)}" if details else f"{pname} 수정 완료")

            elif action == "reorder":
                pname     = c.get("place_name", "장소")
                day       = c.get("day_number", "?")
                new_order = c.get("new_order")
                if new_order:
                    parts.append(f"{day}일차 {pname}을(를) {new_order}번째 순서로 이동했습니다")
                else:
                    parts.append(f"{day}일차 동선 순서를 조정했습니다")

            elif action == "optimize_route":
                parts.append("전체 동선을 최적화했습니다")

            elif action == "swap_places":
                a = c.get("place_a", "장소A")
                b = c.get("place_b", "장소B")
                parts.append(f"{a}와(과) {b}의 순서를 바꿨습니다")

            elif action == "swap_days":
                da = c.get("day_a", "?")
                db = c.get("day_b", "?")
                parts.append(f"{da}일차와 {db}일차 일정을 통째로 교환했습니다")

            elif action == "bulk_modify":
                scope = f"{c['day_number']}일차 " if c.get("day_number") else "전체 "
                details = []
                delta = c.get("stay_duration_delta")
                if delta:
                    details.append(f"체류시간 {'+' if delta > 0 else ''}{delta}분")
                shift = c.get("start_time_shift")
                if shift:
                    details.append(f"시작시간 {'+' if shift > 0 else ''}{shift}분")
                if c.get("stay_duration"):
                    details.append(f"체류시간 → {c['stay_duration']}분")
                parts.append(f"{scope}일정 조정: {', '.join(details)}" if details else f"{scope}일정을 조정했습니다")

            elif action == "regenerate":
                parts.append(f"{c.get('scope', '일정')}을(를) 새로 생성했습니다")

            if c.get("warning"):
                warn_parts.append(c["warning"])

        result = " / ".join(parts)
        if warn_parts:
            result += "\n⚠️ " + " / ".join(warn_parts)
        return result

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
                f"[IID:{it.id} PID:{it.place_id}] - {time_str}"
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

        # 축제 요청 감지 (별도 플래그)
        festival_keywords = ["축제", "페스티벌", "festival", "행사", "이벤트"]
        has_festival = any(kw in message for kw in festival_keywords)

        return {"categories": found, "has_festival": has_festival}

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

        search_region = REGION_PREFIX.get(trip.region, trip.region) if trip.region else None

        # 축제 요청 시 여행 기간 내 축제를 TourAPI에서 조회 후 Place 테이블에 저장
        if hints.get("has_festival"):
            festival_places = await self._fetch_and_save_festivals(db, trip)
            for p in festival_places:
                if p.id not in seen_ids:
                    collected.append(p)
                    seen_ids.add(p.id)

        # 힌트 카테고리가 있으면 해당 카테고리 위주로 조회 (카테고리당 20개 → 토큰 절약)
        if categories:
            from sqlalchemy import cast, Text
            for cat in categories:
                query = select(Place)
                if search_region:
                    query = query.where(Place.address.contains(search_region))
                query = (
                    query
                    .where(Place.category == cat)
                    .order_by(nulls_last(Place.readcount.desc()))
                    .limit(20)
                )
                result = await db.execute(query)
                cat_places = result.scalars().all()
                for p in cat_places:
                    if p.id not in seen_ids:
                        collected.append(p)
                        seen_ids.add(p.id)

                # DB에 해당 카테고리가 없으면 (카페, 쇼핑 등) 태그 텍스트로 폴백
                if not cat_places:
                    tag_q = select(Place)
                    if search_region:
                        tag_q = tag_q.where(Place.address.contains(search_region))
                    tag_q = (
                        tag_q
                        .where(cast(Place.tags, Text).contains(f'"{cat}"'))
                        .order_by(nulls_last(Place.readcount.desc()))
                        .limit(20)
                    )
                    result = await db.execute(tag_q)
                    for p in result.scalars().all():
                        if p.id not in seen_ids:
                            collected.append(p)
                            seen_ids.add(p.id)

        # 힌트가 없거나 결과 부족 시 전체 인기순으로 보완 (최대 50개 → 토큰 절약)
        if len(collected) < 30:
            query = select(Place)
            if search_region:
                query = query.where(Place.address.contains(search_region))
            query = (
                query
                .order_by(nulls_last(Place.readcount.desc()))
                .limit(50)
            )
            result = await db.execute(query)
            for p in result.scalars().all():
                if p.id not in seen_ids:
                    collected.append(p)
                    seen_ids.add(p.id)
                if len(collected) >= 50:
                    break

        return collected

    async def _fetch_and_save_festivals(
        self,
        db: AsyncSession,
        trip: Trip
    ) -> List[Place]:
        """여행 기간 내 축제를 TourAPI에서 조회 후 Place 테이블에 저장, Place 객체 목록 반환"""
        try:
            from Festival.service import get_festival_service
            from Festival.dto import FestivalSearchRequest

            festival_service = get_festival_service()
            region = REGION_PREFIX.get(trip.region, trip.region) if trip.region else None

            search_req = FestivalSearchRequest(
                region=trip.region,
                start_date=trip.start_date,
                end_date=trip.end_date,
                max_items=20
            )
            result = await festival_service.search_festivals(db, search_req, fetch_detail=False)
            if not result.get("success"):
                return []

            festivals = result.get("festivals", [])
            places: List[Place] = []

            for festival in festivals:
                try:
                    place_id = await festival_service.save_festival_as_place(db, festival.id)
                    place_result = await db.execute(select(Place).where(Place.id == place_id))
                    place = place_result.scalar_one_or_none()
                    if place:
                        places.append(place)
                except Exception as e:
                    logger.warning(f"축제 Place 저장 실패 (id={festival.id}): {e}")

            return places

        except Exception as e:
            logger.error(f"축제 조회 실패: {e}")
            return []

    def _format_available_places(self, places: List[Place]) -> str:
        """추가 가능한 장소 포맷팅 (최대 30개 GPT 전달)
        태그를 5개까지 노출해 GPT가 분위기/속성 기반 선택 가능하도록 함.
        """
        if not places:
            return "추가 가능한 장소가 없습니다."

        lines = []
        for p in places[:30]:
            tags = ', '.join(p.tags[:5]) if p.tags else ''
            tag_part = f" [{tags}]" if tags else ""
            lines.append(f"- {p.name} ({p.category}) [ID: {p.id}]{tag_part}")

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

            # 파싱 실패 시 기본 응답 (원문 text는 JSON 코드일 수 있으므로 사용자에게 노출 금지)
            return {
                "understood": False,
                "action_type": "question",
                "response_message": "요청을 처리하지 못했어요. 좀 더 구체적으로 말씀해 주시겠어요?",
                "needs_confirmation": False
            }

    async def _search_place_in_db(
        self,
        db: AsyncSession,
        name: str,
        region: Optional[str] = None
    ) -> Optional[Place]:
        """DB에서 직접 장소 검색 — available_places 50개 안에 없을 때 폴백용.

        통합검색(_get_places_by_hints)과의 차이:
        - 통합검색은 인기순 상위 N개를 미리 불러와 GPT 컨텍스트로 제공
        - 이 메서드는 사용자가 특정 장소명을 직접 지목했을 때 DB 전체를 대상으로 검색
          → 인기도가 낮거나 새로 수집된 장소도 이름만 알면 찾을 수 있음
        """
        if not name:
            return None

        import re as _re
        from sqlalchemy import nulls_last

        search_region = REGION_PREFIX.get(region, region) if region else None

        # 1. 정확 매칭 (지역 필터 포함)
        q = select(Place).where(Place.name == name)
        if search_region:
            q = q.where(Place.address.contains(search_region))
        result = await db.execute(q)
        place = result.scalar_one_or_none()
        if place:
            return place

        # 2. 포함 매칭 (지역 필터 포함, 인기순)
        q = select(Place).where(Place.name.contains(name))
        if search_region:
            q = q.where(Place.address.contains(search_region))
        q = q.order_by(nulls_last(Place.readcount.desc())).limit(1)
        result = await db.execute(q)
        place = result.scalar_one_or_none()
        if place:
            return place

        # 3. 포함 매칭 (지역 필터 없이 재시도 — 지역 표기가 달라도 찾을 수 있도록)
        q = (
            select(Place)
            .where(Place.name.contains(name))
            .order_by(nulls_last(Place.readcount.desc()))
            .limit(1)
        )
        result = await db.execute(q)
        place = result.scalar_one_or_none()
        if place:
            return place

        # 4. 한글 토큰 분리 후 가장 긴 토큰으로 검색
        tokens = sorted(_re.findall(r'[가-힣]{2,}', name), key=len, reverse=True)
        for token in tokens[:2]:
            q = select(Place).where(Place.name.contains(token))
            if search_region:
                q = q.where(Place.address.contains(search_region))
            q = q.order_by(nulls_last(Place.readcount.desc())).limit(1)
            result = await db.execute(q)
            place = result.scalar_one_or_none()
            if place:
                return place

        # 5. DB에 없으면 TourAPI로 검색 후 저장, 재시도
        try:
            from DataCollector.collector_service import DataCollectorService
            collector = DataCollectorService()
            await collector.collect_by_keyword(
                db, keyword=name, area_name=region, max_items=5, enhance_with_wiki=False
            )
            # 저장 후 재검색
            q = select(Place).where(Place.name.contains(name))
            if search_region:
                q = q.where(Place.address.like(f"{search_region}%"))
            q = q.order_by(nulls_last(Place.readcount.desc())).limit(1)
            result = await db.execute(q)
            place = result.scalar_one_or_none()
            if place:
                return place
        except Exception as e:
            logger.warning(f"TourAPI 폴백 검색 실패 ({name}): {e}")

        return None

    async def _search_place_in_db_strict(
        self,
        db: AsyncSession,
        name: str,
        region: Optional[str]
    ) -> Optional[Place]:
        """지역 필터를 끝까지 유지하는 DB 검색 — 교체 시 다른 지역 장소 유입 방지.
        지역 내에서 못 찾으면 None 반환 (지역 외 결과는 반환하지 않음).
        """
        if not name:
            return None

        import re as _re
        from sqlalchemy import nulls_last

        search_region = REGION_PREFIX.get(region, region) if region else None

        def apply_region(q):
            if search_region:
                return q.where(Place.address.contains(search_region))
            return q

        # 1. 정확 매칭
        q = apply_region(select(Place).where(Place.name == name))
        result = await db.execute(q)
        place = result.scalar_one_or_none()
        if place:
            return place

        # 2. 포함 매칭 (readcount 정렬)
        q = apply_region(select(Place).where(Place.name.contains(name)))
        q = q.order_by(nulls_last(Place.readcount.desc())).limit(1)
        result = await db.execute(q)
        place = result.scalar_one_or_none()
        if place:
            return place

        # 3. 한글 토큰 AND 조건
        tokens = sorted(_re.findall(r'[가-힣]{2,}', name), key=len, reverse=True)
        if len(tokens) >= 2:
            from sqlalchemy import and_ as _and
            q = apply_region(select(Place).where(_and(*[Place.name.contains(t) for t in tokens])))
            q = q.order_by(nulls_last(Place.readcount.desc())).limit(1)
            result = await db.execute(q)
            place = result.scalar_one_or_none()
            if place:
                return place

        # 4. TourAPI 검색 후 재시도 (지역 필터 유지)
        try:
            from DataCollector.collector_service import DataCollectorService
            collector = DataCollectorService()
            await collector.collect_by_keyword(
                db, keyword=name, area_name=region,
                max_items=3, enhance_with_wiki=False
            )
            q = apply_region(select(Place).where(Place.name.contains(name)))
            q = q.order_by(nulls_last(Place.readcount.desc())).limit(1)
            result = await db.execute(q)
            place = result.scalar_one_or_none()
            if place:
                return place
        except Exception:
            pass

        return None  # 지역 내 미발견 → None (다른 지역 장소 반환 안 함)

    def _find_place_by_name(
        self,
        name: str,
        places: List[Place]
    ) -> Optional[Place]:
        """장소명으로 매칭 (정확 → 포함 → 토큰 교집합 순으로 폴백)
        available_places 리스트 안에서만 검색. DB 전체 검색은 _search_place_in_db 사용.
        """
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

        if best_match:
            return best_match

        # 3. 토큰 교집합 매칭 (예: "감천마을" → "감천문화마을" 매칭)
        # 2글자 이상 한글 단어를 토큰으로 분리하여 교집합이 가장 큰 장소 선택
        import re as _re
        query_tokens = set(_re.findall(r'[가-힣]{2,}', name_lower))
        if query_tokens:
            best_score = 0
            for p in places:
                place_tokens = set(_re.findall(r'[가-힣]{2,}', p.name.lower()))
                intersection = query_tokens & place_tokens
                score = len(intersection) / max(len(query_tokens), 1)
                if score > best_score and score >= 0.5:
                    best_score = score
                    best_match = p

        return best_match

    def _get_day_centroid(self, itineraries: list, day_number: int) -> Optional[tuple]:
        """특정 일차 장소들의 평균 좌표(centroid) 반환. 좌표 없는 장소는 제외."""
        day_its = [
            it for it in itineraries
            if it.day_number == day_number
            and it.place and it.place.latitude and it.place.longitude
        ]
        if not day_its:
            return None
        avg_lat = sum(it.place.latitude for it in day_its) / len(day_its)
        avg_lng = sum(it.place.longitude for it in day_its) / len(day_its)
        return avg_lat, avg_lng

    def _find_closest_day(self, itineraries: list, lat: float, lng: float, total_days: int) -> Optional[int]:
        """새 장소 좌표에서 centroid가 가장 가까운 일차 반환. 모든 일차가 비어있으면 None."""
        from Planner.route_optimizer import get_route_optimizer
        optimizer = get_route_optimizer()

        best_day = None
        best_dist = float('inf')

        for day in range(1, total_days + 1):
            centroid = self._get_day_centroid(itineraries, day)
            if centroid is None:
                continue
            dist = optimizer._haversine(lat, lng, centroid[0], centroid[1])
            if dist < best_dist:
                best_dist = dist
                best_day = day

        return best_day

    def _check_route_distance(
        self, itineraries: list, day_number: int,
        lat: float, lng: float, threshold_km: float = 15.0
    ) -> Optional[str]:
        """새 장소가 특정 일차 동선 중심에서 threshold_km 이상 멀면 경고 문자열 반환."""
        from Planner.route_optimizer import get_route_optimizer
        optimizer = get_route_optimizer()

        centroid = self._get_day_centroid(itineraries, day_number)
        if centroid is None:
            return None

        dist = optimizer._haversine(lat, lng, centroid[0], centroid[1])
        if dist > threshold_km:
            return (
                f"{day_number}일차 동선 중심에서 약 {dist:.0f}km 떨어진 장소입니다. "
                "이동 시간이 길어질 수 있습니다."
            )
        return None

    def _check_operating_hours_conflict(self, place, ordered_itineraries: list) -> Optional[str]:
        """추가된 장소의 도착 예정 시간과 영업시간을 대조해 충돌 경고 문자열 반환.
        time_constraint의 파서를 재사용하고, 충돌이 없으면 None 반환.
        """
        if not place.operating_hours:
            return None

        try:
            from Planner.time_constraint import TimeConstraintService

            tc = TimeConstraintService()
            open_t, close_t = tc._parse_operating_hours(place.operating_hours)
            if open_t is None:
                return None

            # 추가된 장소의 예정 도착 시간 찾기
            arrival = next(
                (it.arrival_time for it in ordered_itineraries if it.place_id == place.id),
                None
            )
            if arrival is None:
                return None

            if arrival < open_t or arrival >= close_t:
                return (
                    f"{place.name}의 영업시간({open_t.strftime('%H:%M')}~"
                    f"{close_t.strftime('%H:%M')})과 예정 도착 시간({arrival.strftime('%H:%M')})이 맞지 않습니다."
                )
        except Exception:
            pass

        return None

    def _find_itinerary_by_name(
        self,
        name: str,
        itineraries: List[Itinerary]
    ) -> Optional[Itinerary]:
        """일정에서 장소명으로 Itinerary 찾기 (정확 → 포함 → 토큰 교집합 폴백)"""
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

        if best_match:
            return best_match

        # 3. 토큰 교집합 매칭 (예: "감천마을" → "감천문화마을")
        import re as _re
        query_tokens = set(_re.findall(r'[가-힣]{2,}', name_lower))
        if query_tokens:
            best_score = 0
            for it in itineraries:
                place_tokens = set(_re.findall(r'[가-힣]{2,}', it.place.name.lower()))
                intersection = query_tokens & place_tokens
                score = len(intersection) / max(len(query_tokens), 1)
                if score > best_score and score >= 0.5:
                    best_score = score
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
        warning_messages = []
        place_id_dict = {p.id: p for p in available_places}

        for change in changes:
            action = change.get("action")

            try:
                if action == "add":
                    result = await self._apply_add(db, trip, change, available_places, place_id_dict)
                    if result:
                        applied_changes.append(result)

                elif action == "remove":
                    result = await self._apply_remove(db, trip, change, available_places)
                    if result:
                        applied_changes.append(result)

                elif action == "replace":
                    result = await self._apply_replace(db, trip, change, available_places, place_id_dict)
                    if result:
                        if result.get("_blocked"):
                            warning_messages.append(result["_blocked"])
                        else:
                            applied_changes.append(result)

                elif action == "reorder":
                    result = await self._apply_reorder(db, trip, change, available_places)
                    if result:
                        applied_changes.append(result)

                elif action == "modify":
                    result = await self._apply_modify(db, trip, change)
                    if result:
                        applied_changes.append(result)

                elif action == "regenerate":
                    result = await self._apply_regenerate(db, user_id, trip, change)
                    if result:
                        applied_changes.append(result)

                elif action == "swap_places":
                    result = await self._apply_swap_places(db, trip, change)
                    if result:
                        applied_changes.append(result)

                elif action == "swap_days":
                    result = await self._apply_swap_days(db, trip, change)
                    if result:
                        applied_changes.append(result)

                elif action == "change_duration":
                    result = await self._apply_change_duration(db, user_id, trip, change)
                    if result:
                        applied_changes.append(result)

                elif action == "optimize_route":
                    result = await self._apply_optimize_route(db, user_id, trip)
                    if result:
                        applied_changes.append(result)

                elif action == "bulk_modify":
                    result = await self._apply_bulk_modify(db, trip, change)
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
                "region": updated.region,
                "start_date": str(updated.start_date),
                "end_date": str(updated.end_date),
                "itineraries": [
                    {
                        "id": it.id,
                        "place_id": it.place_id,
                        "place_name": it.place.name,
                        "place_category": it.place.category,
                        "place_address": it.place.address,
                        "latitude": it.place.latitude,
                        "longitude": it.place.longitude,
                        "image_url": it.place.image_url,
                        "day_number": it.day_number,
                        "order_index": it.order_index,
                        "arrival_time": it.arrival_time.strftime("%H:%M") if it.arrival_time else None,
                        "stay_duration": it.stay_duration,
                        "travel_time_from_prev": it.travel_time_from_prev,
                        "transport_mode": it.transport_mode,
                        "memo": it.memo,
                    }
                    for it in sorted(
                        updated.itineraries,
                        key=lambda x: (x.day_number, x.order_index)
                    )
                ]
            }

        return applied_changes, trip_dict, warning_messages

    # 카테고리별 기본 배치 시간 (분 단위)
    CATEGORY_DEFAULT_MINUTES = {
        "관광지":    9 * 60,       # 09:00 오전 관광
        "문화시설":  10 * 60,      # 10:00 오전 문화
        "카페":      10 * 60,      # 10:00 or 오후 브런치
        "맛집":      12 * 60,      # 12:00 점심 기본 (DB 카테고리명)
        "쇼핑":      14 * 60,      # 14:00 오후 쇼핑
        "레저/스포츠": 10 * 60,
        "숙박":      21 * 60,      # 21:00 저녁 체크인
    }
    NIGHT_KEYWORDS = ["야경", "야간", "밤", "night", "루프탑"]

    def _estimate_place_minutes(self, place) -> int:
        """장소의 카테고리/태그 기반으로 적절한 방문 시간(분) 추정"""
        tags = (place.tags or []) if place else []
        category = (place.category or "") if place else ""

        if any(kw in str(tags).lower() for kw in self.NIGHT_KEYWORDS):
            return 19 * 60  # 야경/야간 → 19:00

        return self.CATEGORY_DEFAULT_MINUTES.get(category, 10 * 60)

    async def _apply_add(
        self, db, trip, change, available_places, place_id_dict
    ) -> Optional[dict]:
        """장소 추가 — 카테고리/시간 기반으로 적절한 위치에 삽입 후 시간 재계산"""
        from Trip.dto import ItineraryCreate, ItineraryUpdate
        from sqlalchemy import select as sa_select
        from collections import Counter
        from core.models import Itinerary as ItineraryModel

        # DB에서 현재 실제 상태로 재조회 (이전 remove/fill이 이미 반영됐을 수 있어 스냅샷 사용 금지)
        _cur = await db.execute(
            sa_select(ItineraryModel.place_id).where(ItineraryModel.trip_id == trip.id)
        )
        existing_ids = {row[0] for row in _cur.fetchall()}
        place = None

        if change.get("place_name"):
            place = self._find_place_by_name(change["place_name"], available_places)
            if not place:
                place = await self._search_place_in_db(db, change["place_name"], trip.region)

        if not place and change.get("place_id"):
            place = place_id_dict.get(change["place_id"])

        if not place and change.get("category"):
            cat = change["category"]
            for p in available_places:
                if p.id not in existing_ids and p.category and cat in p.category:
                    place = p
                    break
            # available_places에 없으면 DB에서 직접 조회 (카테고리 전용 폴백)
            if not place:
                from sqlalchemy import nulls_last, cast, Text
                from core.models import Place as PlaceModel
                search_region = REGION_PREFIX.get(trip.region, trip.region) if trip.region else None
                q = select(PlaceModel).where(PlaceModel.category == cat)
                if search_region:
                    q = q.where(PlaceModel.address.contains(search_region))
                q = q.where(~PlaceModel.id.in_(existing_ids))
                q = q.order_by(nulls_last(PlaceModel.readcount.desc())).limit(10)
                result = await db.execute(q)
                for p in result.scalars().all():
                    if p.id not in existing_ids:
                        place = p
                        break
            # 카테고리 이름이 DB에 없는 경우 (예: "카페", "쇼핑") → tags 텍스트 검색
            if not place:
                from sqlalchemy import nulls_last, cast, Text
                from core.models import Place as PlaceModel
                search_region = REGION_PREFIX.get(trip.region, trip.region) if trip.region else None
                tag_q = select(PlaceModel)
                if search_region:
                    tag_q = tag_q.where(PlaceModel.address.contains(search_region))
                tag_q = (
                    tag_q
                    .where(cast(PlaceModel.tags, Text).contains(f'"{cat}"'))
                    .where(~PlaceModel.id.in_(existing_ids))
                    .order_by(nulls_last(PlaceModel.readcount.desc()))
                    .limit(10)
                )
                result = await db.execute(tag_q)
                for p in result.scalars().all():
                    if p.id not in existing_ids:
                        place = p
                        break

        # 태그 기반 검색 (카테고리로도 못 찾은 경우)
        # GPT가 "야경", "포토스팟", "힐링" 등 속성 기반으로 요청할 때 사용
        if not place and change.get("tags"):
            from Vision.tag_matcher import calculate_tag_score
            query_tags = change["tags"]
            best_score = 0.0
            for p in available_places:
                if p.id in existing_ids or not p.tags:
                    continue
                score = calculate_tag_score(p.tags, query_tags)
                if score > best_score:
                    best_score = score
                    place = p

        if not place:
            return None

        if place.id in existing_ids:
            return None

        # day_number 미지정 시 → 동선 중심에 가장 가까운 날 우선, 없으면 장소 수 최소인 날
        day = change.get("day_number")
        total_days = (trip.end_date - trip.start_date).days + 1
        if not day:
            if place.latitude and place.longitude:
                day = self._find_closest_day(
                    trip.itineraries, place.latitude, place.longitude, total_days
                )
            if not day:
                day_counts = Counter(it.day_number for it in trip.itineraries)
                day = min(range(1, total_days + 1), key=lambda d: day_counts.get(d, 0))

        # 해당 일차 기존 일정 조회 (arrival_time + place 포함)
        from sqlalchemy.orm import selectinload as _selectinload
        result = await db.execute(
            sa_select(ItineraryModel)
            .options(_selectinload(ItineraryModel.place))
            .where(ItineraryModel.trip_id == trip.id, ItineraryModel.day_number == day)
            .order_by(ItineraryModel.order_index, ItineraryModel.id)
        )
        day_its = list(result.scalars().all())

        # 삽입 위치 결정
        if change.get("order_index"):
            # GPT가 위치를 명시한 경우
            insert_at = max(0, min(change["order_index"] - 1, len(day_its)))
        else:
            # 카테고리/태그 기반으로 시간순 적절한 위치 찾기
            new_minutes = self._estimate_place_minutes(place)
            insert_at = len(day_its)  # 기본 마지막
            for i, it in enumerate(day_its):
                existing_minutes = (
                    it.arrival_time.hour * 60 + it.arrival_time.minute
                    if it.arrival_time else self.CATEGORY_DEFAULT_MINUTES.get(
                        (it.place.category if it.place else ""), 10 * 60
                    )
                )
                if new_minutes < existing_minutes:
                    insert_at = i
                    break

        # 임시로 마지막 order_index로 생성 후 전체 재정렬
        temp_order = len(day_its) + 1
        await trip_crud.create_itinerary(
            db, trip.id,
            ItineraryCreate(place_id=place.id, day_number=day, order_index=temp_order)
        )

        # 새로 생성된 itinerary 포함해서 재조회
        result2 = await db.execute(
            sa_select(ItineraryModel)
            .where(ItineraryModel.trip_id == trip.id, ItineraryModel.day_number == day)
            .order_by(ItineraryModel.order_index, ItineraryModel.id)
        )
        all_its = list(result2.scalars().all())

        # 새 장소를 insert_at 위치로 이동
        new_it = next((it for it in all_its if it.place_id == place.id), None)
        others = [it for it in all_its if it.place_id != place.id]
        ordered = others[:insert_at] + ([new_it] if new_it else []) + others[insert_at:]

        # 커밋 전에 시작 시간 미리 추출 (update loop 후 ORM 객체 expire 방지)
        first_orig_time = ordered[0].arrival_time if ordered else None
        start_h = first_orig_time.hour if first_orig_time else 9
        start_m = first_orig_time.minute if first_orig_time else 0

        # order_index 재정렬
        for idx, it in enumerate(ordered, start=1):
            await trip_crud.update_itinerary(db, it.id, ItineraryUpdate(order_index=idx))

        # 업데이트 후 ORM 객체 expire됨 → DB에서 재조회 후 시간 재계산
        from sqlalchemy.orm import selectinload as _selectinload
        result3 = await db.execute(
            sa_select(ItineraryModel)
            .options(_selectinload(ItineraryModel.place))
            .where(ItineraryModel.trip_id == trip.id, ItineraryModel.day_number == day)
            .order_by(ItineraryModel.order_index, ItineraryModel.id)
        )
        fresh_ordered = list(result3.scalars().all())
        await self._recalculate_day_times(db, fresh_ordered, start_hour=start_h, start_minute=start_m)

        # 동선 거리 경고 + 영업시간 충돌 경고
        warnings = []
        if place.latitude and place.longitude:
            dist_warn = self._check_route_distance(
                trip.itineraries, day, place.latitude, place.longitude
            )
            if dist_warn:
                warnings.append(dist_warn)
        hours_warn = self._check_operating_hours_conflict(place, fresh_ordered)
        if hours_warn:
            warnings.append(hours_warn)

        # 추가된 장소의 최종 arrival_time 조회
        added_arrival = None
        try:
            added_it = next(
                (it for it in fresh_ordered if it.place_id == place.id), None
            )
            if added_it and added_it.arrival_time:
                added_arrival = added_it.arrival_time.strftime("%H:%M")
        except Exception:
            pass

        result_info = {
            "action": "add",
            "place_name": place.name,
            "day_number": day,
            "arrival_time": added_arrival,
        }
        if warnings:
            result_info["warning"] = " / ".join(warnings)
        return result_info

    async def _apply_remove(self, db, trip, change, available_places: list = None) -> Optional[dict]:
        """장소 제거 후 같은 일차 order_index 재정렬 + 빈 자리 자동 보충"""
        target = self._find_itinerary_by_name(
            change.get("place_name", ""), trip.itineraries
        )
        if not target:
            return None

        removed_day = target.day_number
        removed_name = target.place.name
        removed_category = target.place.category if target.place else None
        removed_order = target.order_index          # 삭제 전 순서 (1-based)
        removed_arrival = target.arrival_time       # 삭제 전 도착 시간

        # 커밋 전에 모두 미리 추출 (delete 커밋 후 trip 객체 expire 방지)
        all_used_ids = {it.place_id for it in trip.itineraries if it.id != target.id}
        # 방금 삭제된 장소는 fill 대상에서도 제외 (삭제 직후 같은 장소가 다시 채워지는 버그 방지)
        all_used_ids.add(target.place_id)
        trip_id = trip.id
        trip_region = trip.region

        from Trip.dto import ItineraryUpdate, ItineraryCreate
        from sqlalchemy import select as sa_select
        from core.models import Itinerary as ItineraryModel

        logger.info(f"[remove] '{removed_name}' 삭제 (day={removed_day}, order={removed_order})")

        await trip_crud.delete_itinerary(db, target.id)

        # 삭제 후 같은 날 남은 장소 재조회
        result = await db.execute(
            sa_select(ItineraryModel)
            .where(ItineraryModel.trip_id == trip_id, ItineraryModel.day_number == removed_day)
            .order_by(ItineraryModel.order_index, ItineraryModel.id)
        )
        remaining = list(result.scalars().all())

        # ── 빈 자리 자동 보충 (no_fill: true 이면 채우지 않음) ──
        no_fill = change.get("no_fill", False)
        fill_place = None
        if not no_fill:
            fill_place = await self._find_fill_place(
                db, trip_region, removed_category, all_used_ids, available_places or []
            )
            logger.info(f"[remove] fill 탐색 결과: {fill_place.name if fill_place else 'None'}")
        else:
            logger.info("[remove] no_fill=true, 자동 보충 생략")

        if fill_place:
            # 제거된 장소의 도착 시간 기준으로 삽입 위치 결정 (아침 슬롯 보호)
            target_min = (
                (removed_arrival.hour * 60 + removed_arrival.minute)
                if removed_arrival else self._estimate_place_minutes(fill_place)
            )

            # remaining 중 target_min보다 늦은 첫 위치에 삽입
            insert_at = len(remaining)
            for i, it in enumerate(remaining):
                it_min = (
                    it.arrival_time.hour * 60 + it.arrival_time.minute
                    if it.arrival_time else 10 * 60
                )
                if it_min > target_min:
                    insert_at = i
                    break

            temp_order = len(remaining) + 1
            await trip_crud.create_itinerary(
                db, trip_id,
                ItineraryCreate(
                    place_id=fill_place.id,
                    day_number=removed_day,
                    order_index=temp_order
                )
            )
            # fill 포함하여 재조회
            result = await db.execute(
                sa_select(ItineraryModel)
                .where(ItineraryModel.trip_id == trip_id, ItineraryModel.day_number == removed_day)
                .order_by(ItineraryModel.order_index, ItineraryModel.id)
            )
            all_its = list(result.scalars().all())

            # fill을 올바른 위치로 삽입
            fill_it = next((it for it in all_its if it.place_id == fill_place.id), None)
            others = [it for it in all_its if it.place_id != fill_place.id]
            remaining = others[:insert_at] + ([fill_it] if fill_it else []) + others[insert_at:]

        if not remaining:
            return {"action": "remove", "place_name": removed_name}

        # 시작 시간 결정:
        # 삭제된 장소가 첫 번째였으면 그 장소의 도착 시간(=당일 시작)을 그대로 사용
        if removed_order == 1:
            start_h = removed_arrival.hour if removed_arrival else 9
            start_m = removed_arrival.minute if removed_arrival else 0
        else:
            first_time = remaining[0].arrival_time
            start_h = first_time.hour if first_time else 9
            start_m = first_time.minute if first_time else 0

        # order_index 재정렬
        for idx, it in enumerate(remaining, start=1):
            if it.order_index != idx:
                await trip_crud.update_itinerary(db, it.id, ItineraryUpdate(order_index=idx))

        # 재조회 후 시간 재계산 (place 포함 eager load 필수 — lazy load는 async에서 실패)
        from sqlalchemy.orm import selectinload as _selectinload
        result2 = await db.execute(
            sa_select(ItineraryModel)
            .options(_selectinload(ItineraryModel.place))
            .where(ItineraryModel.trip_id == trip_id, ItineraryModel.day_number == removed_day)
            .order_by(ItineraryModel.order_index, ItineraryModel.id)
        )
        fresh_remaining = list(result2.scalars().all())
        await self._recalculate_day_times(db, fresh_remaining, start_hour=start_h, start_minute=start_m)

        result_info = {"action": "remove", "place_name": removed_name}
        if fill_place:
            result_info["filled_with"] = fill_place.name
        if no_fill:
            result_info["no_fill"] = True
        return result_info

    async def _find_fill_place(
        self, db, region: Optional[str], removed_category: Optional[str],
        all_used_ids: set, available_places: list
    ) -> Optional[Place]:
        """
        제거된 장소를 보충할 미사용 장소 탐색.
        - 같은 카테고리 우선, 없으면 카테고리 무관
        - 맛집을 뺀 경우: 다른 맛집으로 보충 (식사 슬롯 유지)
        - 맛집 외를 뺀 경우: 같은 카테고리 → 관광지/문화시설 순으로 보충
        """
        from sqlalchemy import select as sa_select, nulls_last
        from core.models import Place as PlaceModel

        search_region = REGION_PREFIX.get(region, region) if region else None
        is_restaurant = removed_category and "맛집" in removed_category

        # ── 맛집을 뺀 경우: 다른 맛집으로 보충 ──
        if is_restaurant:
            for p in available_places:
                if p.id not in all_used_ids and p.category and "맛집" in p.category:
                    return p
            # available_places에 없으면 DB에서 조회
            q = sa_select(PlaceModel).where(PlaceModel.category == "맛집")
            if search_region:
                q = q.where(PlaceModel.address.contains(search_region))
            q = q.where(~PlaceModel.id.in_(all_used_ids))
            q = q.order_by(nulls_last(PlaceModel.readcount.desc())).limit(1)
            result = await db.execute(q)
            return result.scalar_one_or_none()

        # ── 맛집 외를 뺀 경우 ──
        # 1. available_places에서 같은 카테고리 미사용 장소
        if removed_category:
            for p in available_places:
                if p.id not in all_used_ids and p.category and removed_category in p.category:
                    return p

        # 2. available_places에서 관광지/문화시설 우선, 그다음 맛집 외 전체
        for cat in [removed_category, "관광지", "문화시설", None]:
            for p in available_places:
                if p.id not in all_used_ids and "맛집" not in (p.category or ""):
                    if cat is None or (p.category and cat in p.category):
                        return p

        # 3. DB에서 같은 지역 인기순
        q = sa_select(PlaceModel)
        if search_region:
            q = q.where(PlaceModel.address.contains(search_region))
        if removed_category:
            q = q.where(PlaceModel.category == removed_category)
        q = q.where(~PlaceModel.id.in_(all_used_ids))
        q = q.where(PlaceModel.category != "맛집")
        q = q.order_by(nulls_last(PlaceModel.readcount.desc())).limit(1)
        result = await db.execute(q)
        return result.scalar_one_or_none()

    async def _apply_replace(
        self, db, trip, change, available_places, place_id_dict
    ) -> Optional[dict]:
        """장소 교체 (source_place_id / target_search_keyword 지원)"""
        # 커밋 전에 미리 추출 (이후 trip.itineraries expire 방지)
        existing_ids = {it.place_id for it in trip.itineraries}
        itineraries_snapshot = list(trip.itineraries)

        # ── 뺄 장소(old) 찾기 ──
        old_it = None

        # source_place_id로 직접 매핑 (가장 정확)
        if change.get("source_place_id"):
            for it in itineraries_snapshot:
                if it.place_id == change["source_place_id"]:
                    old_it = it
                    break

        # old_place 이름으로 폴백
        if not old_it:
            old_it = self._find_itinerary_by_name(
                change.get("old_place", ""), itineraries_snapshot
            )

        # day_number + target_category 기반 폴백 (카테고리로 해당 날 장소 찾기)
        if not old_it and change.get("day_number") and change.get("target_category"):
            day = change["day_number"]
            cat = change["target_category"]
            for it in itineraries_snapshot:
                if it.day_number == day and it.place.category and cat in it.place.category:
                    old_it = it
                    break

        # ── 넣을 장소(new) 찾기 — 반드시 같은 지역 내에서만 ──
        new_place = None
        region = trip.region  # 지역 미리 추출

        # target_search_keyword: available_places 우선, DB 폴백은 지역 필터 강제
        if change.get("target_search_keyword"):
            new_place = self._find_place_by_name(
                change["target_search_keyword"], available_places
            )
            if not new_place:
                new_place = await self._search_place_in_db_strict(
                    db, change["target_search_keyword"], region
                )

        # new_place 이름으로 폴백
        if not new_place and change.get("new_place"):
            new_place = self._find_place_by_name(
                change["new_place"], available_places
            )
            if not new_place:
                new_place = await self._search_place_in_db_strict(
                    db, change["new_place"], region
                )

        # place_id로 직접 매핑
        if not new_place and change.get("place_id"):
            new_place = place_id_dict.get(change["place_id"])

        # target_category로 폴백 (카테고리 내 첫 번째 미사용 장소, available_places = 지역 필터됨)
        # 이름 검색이 실패한 경우에도 같은 카테고리로 자동 대체
        if not new_place:
            cat = change.get("target_category") or (old_it.place.category if old_it else None)
            if cat:
                for p in available_places:
                    if p.id not in existing_ids and p.category and cat in p.category:
                        new_place = p
                        break

        if old_it and new_place:
            # 새 장소가 이미 다른 날 일정에 있으면 교체 불가 (교체될 old 장소는 제외하고 체크)
            already_used = {it.place_id for it in itineraries_snapshot if it.id != old_it.id}
            if new_place.id in already_used:
                return {"_blocked": f"'{new_place.name}'은(는) 이미 다른 날 일정에 포함되어 있어 교체할 수 없습니다. 원하시는 식당 이름을 직접 말씀해 주세요"}
            from Trip.dto import ItineraryUpdate
            await trip_crud.update_itinerary(
                db, old_it.id,
                ItineraryUpdate(place_id=new_place.id)
            )

            # 교체된 장소가 그날 동선에서 너무 멀면 경고
            dist_warn = None
            if new_place.latitude and new_place.longitude:
                dist_warn = self._check_route_distance(
                    itineraries_snapshot, old_it.day_number,
                    new_place.latitude, new_place.longitude
                )

            result = {"action": "replace", "old_place": old_it.place.name, "new_place": new_place.name}
            if dist_warn:
                result["warning"] = dist_warn
            return result
        return None

    async def _apply_reorder(self, db, trip, change, available_places: list = None) -> Optional[dict]:
        """순서 변경 / 다른 일차로 이동 후 관련 일차 전체 order_index + arrival_time 재정렬
        다른 날로 이동 시 출발 일차에 빈 자리 자동 보충"""
        from Trip.dto import ItineraryUpdate, ItineraryCreate
        from sqlalchemy import select as sa_select
        from core.models import Itinerary as ItineraryModel

        target = self._find_itinerary_by_name(
            change.get("place_name", ""), trip.itineraries
        )
        if not target:
            return None

        src_day = target.day_number
        new_day = change.get("day_number") or src_day
        new_order = change.get("new_order")

        # 커밋 전에 모두 미리 추출 (이후 trip 객체 expire 방지)
        target_name = target.place.name
        target_category = target.place.category if target.place else None
        target_id = target.id
        trip_id = trip.id
        trip_region = trip.region
        all_used_ids = {it.place_id for it in trip.itineraries}
        moving_to_other_day = (src_day != new_day)

        # ── 1. target을 목적 일차로 이동 ──
        await trip_crud.update_itinerary(
            db, target_id, ItineraryUpdate(day_number=new_day)
        )

        # ── 2. 출발 일차에 빈 자리 보충 (다른 날로 이동한 경우만) ──
        if moving_to_other_day:
            fill_place = await self._find_fill_place(
                db, trip_region, target_category, all_used_ids, available_places or []
            )
            if fill_place:
                # src_day의 현재 장소 수 파악
                cnt_result = await db.execute(
                    sa_select(ItineraryModel)
                    .where(ItineraryModel.trip_id == trip_id, ItineraryModel.day_number == src_day)
                )
                src_count = len(cnt_result.scalars().all())
                await trip_crud.create_itinerary(
                    db, trip_id,
                    ItineraryCreate(
                        place_id=fill_place.id,
                        day_number=src_day,
                        order_index=src_count + 1
                    )
                )
                all_used_ids.add(fill_place.id)

        # ── 3. 영향받는 일차들을 DB에서 재조회 후 정렬 + 시간 재계산 ──
        affected_days = {src_day, new_day} if moving_to_other_day else {src_day}
        from sqlalchemy.orm import selectinload as _selectinload

        for day in affected_days:
            result = await db.execute(
                sa_select(ItineraryModel)
                .options(_selectinload(ItineraryModel.place))
                .where(ItineraryModel.trip_id == trip_id, ItineraryModel.day_number == day)
                .order_by(ItineraryModel.order_index, ItineraryModel.id)
            )
            day_its = list(result.scalars().all())

            if not day_its:
                continue

            # 목적 일차: target을 적절한 위치에 삽입
            if day == new_day:
                others = [it for it in day_its if it.id != target_id]
                target_it = next((it for it in day_its if it.id == target_id), None)
                if target_it is None:
                    continue
                if new_order is not None:
                    insert_at = max(0, min(new_order - 1, len(others)))
                else:
                    insert_at = self._find_insert_position(target_it, others)
                ordered = others[:insert_at] + [target_it] + others[insert_at:]
            else:
                # 출발 일차: 남은 장소 순서 그대로 (보충된 장소 포함)
                ordered = day_its

            if not ordered:
                continue

            # 커밋 전 시작 시간 추출
            # 목적 일차로 이동한 경우: 기존 장소(others)의 시작 시간 기준
            # (이동해온 장소는 원래 일차 시간을 갖고 있으므로 기준으로 쓰면 안 됨)
            if day == new_day and moving_to_other_day:
                ref_time = others[0].arrival_time if others else None
                start_h = ref_time.hour if ref_time else 9
                start_m = ref_time.minute if ref_time else 0
            else:
                first_time = ordered[0].arrival_time
                start_h = first_time.hour if first_time else 9
                start_m = first_time.minute if first_time else 0

            # order_index 정리
            for idx, it in enumerate(ordered, start=1):
                if it.order_index != idx:
                    await trip_crud.update_itinerary(db, it.id, ItineraryUpdate(order_index=idx))

            # 재조회 후 시간 재계산 (place eager load 필수)
            from sqlalchemy.orm import selectinload as _selectinload
            result_fresh = await db.execute(
                sa_select(ItineraryModel)
                .options(_selectinload(ItineraryModel.place))
                .where(ItineraryModel.trip_id == trip_id, ItineraryModel.day_number == day)
                .order_by(ItineraryModel.order_index, ItineraryModel.id)
            )
            fresh_day = list(result_fresh.scalars().all())
            await self._recalculate_day_times(db, fresh_day, start_hour=start_h, start_minute=start_m)

        return {
            "action": "reorder",
            "place_name": target_name,
            "day_number": new_day,
            "order_index": new_order or 999
        }

    def _find_insert_position(self, target_it, others: list) -> int:
        """
        arrival_time 기준으로 target을 삽입할 위치 결정.
        arrival_time 없으면 카테고리 기반 추정 시간 사용.
        """
        CATEGORY_DEFAULT_HOUR = {
            "맛집": 12,     # 점심대 (DB 카테고리명)
            "카페": 10,
            "문화시설": 10,
            "관광지": 9,
            "쇼핑": 14,
            "숙박": 21,
        }
        NIGHT_KEYWORDS = ["야경", "야간", "밤", "night"]

        def to_minutes(it) -> int:
            if it.arrival_time:
                return it.arrival_time.hour * 60 + it.arrival_time.minute
            # 카테고리로 추정
            category = (it.place.category if it.place else None) or ""
            tags = (it.place.tags if it.place else None) or []
            # 야경 태그 있으면 저녁
            if any(kw in str(tags).lower() for kw in NIGHT_KEYWORDS):
                return 19 * 60
            return CATEGORY_DEFAULT_HOUR.get(category, 10) * 60

        target_minutes = to_minutes(target_it)

        for i, other in enumerate(others):
            if to_minutes(other) > target_minutes:
                return i
        return len(others)  # 모든 장소보다 늦으면 마지막

    async def _recalculate_day_times(
        self,
        db,
        ordered_itineraries: list,
        start_hour: int = 9,
        start_minute: int = 0,
        pace_buffer: int = 15,
    ) -> list:
        """순서 변경 후 arrival_time을 체인 방식으로 재계산.

        초기 생성과 동일한 시간 제약을 적용:
        - 카카오 API 실제 이동 시간 (single-pass, 제거된 장소 건너뜀)
        - 페이스 기반 버퍼 (기본 moderate=15분)
        - 식사 시간대 snapping (11:30 전→11:30, 14:00~17:30 사이→17:30)
        - 야경 장소 20:00 이전이면 push
        - 영업 마감 30분 이내 / 초과 도착 → 일정에서 제외
        - 23:00 이후 배치(야경 제외) → 일정에서 제외

        Returns: 사용자에게 보여줄 메시지 리스트 (제외된 장소 포함)
        """
        from datetime import time as time_type
        from sqlalchemy import delete as sa_delete, select as sa_select
        from core.models import Itinerary as ItineraryModel
        from Trip.dto import ItineraryUpdate
        from services.kakao_service import get_route_info
        from Planner.constants import LUNCH_START, LUNCH_END, EARLY_DINNER_START, NIGHT_START
        from Planner.time_constraint import get_time_constraint_service

        if not ordered_itineraries:
            return []

        tc = get_time_constraint_service()
        messages = []
        REMOVE_AFTER = 23 * 60   # 23:00 이후 비야경 장소 제외

        MEAL_CATS = {'맛집', '식당'}
        NIGHT_KEYWORDS = {"야경", "야간", "night", "루프탑", "야시장", "불꽃", "일몰", "노을", "선셋"}
        NON_NIGHT_CATS = {'체험', '박물관', '관광지', '맛집', '식당', '카페', '쇼핑', '전시'}

        # commit 후 SQLAlchemy expire 방지: 필요한 값 미리 추출
        it_ids   = [it.id for it in ordered_itineraries]
        stays    = [it.stay_duration or 60 for it in ordered_itineraries]
        places   = [getattr(it, 'place', None) for it in ordered_itineraries]

        categories, is_night_flags = [], []
        for place in places:
            cat  = (place.category if place else None) or ""
            tags = (place.tags if place else None) or []
            name = (place.name if place else "") or ""
            categories.append(cat)
            is_night_flags.append(
                cat not in NON_NIGHT_CATS and (
                    any(kw in t.lower() for t in tags for kw in NIGHT_KEYWORDS) or
                    any(kw in name for kw in NIGHT_KEYWORDS)
                )
            )

        LUNCH_S = LUNCH_START.hour * 60 + LUNCH_START.minute
        LUNCH_E = LUNCH_END.hour * 60 + LUNCH_END.minute
        EARLY_D = EARLY_DINNER_START.hour * 60 + EARLY_DINNER_START.minute
        NIGHT_M = NIGHT_START.hour * 60 + NIGHT_START.minute

        # ── single-pass: 이전 유효 장소 기준으로 이동 시간 계산 ──────────────
        prev_valid_place   = None
        prev_valid_minutes = start_hour * 60 + start_minute  # 마지막 유효 장소 출발 후 시각
        to_delete_ids      = []
        is_first_kept      = True  # DB에 저장한 첫 번째 장소 여부

        for i, it_id in enumerate(it_ids):
            place = places[i]
            pname = (place.name if place else None) or "장소"

            # 이전 유효 장소 → 현재 장소 이동 시간 계산
            if prev_valid_place is None:
                travel = 0
            else:
                travel = 15
                if (prev_valid_place.latitude and prev_valid_place.longitude
                        and place and place.latitude and place.longitude):
                    try:
                        route_info = await get_route_info(
                            prev_valid_place.longitude, prev_valid_place.latitude,
                            place.longitude, place.latitude
                        )
                        duration = route_info.get('duration', 0)
                        if duration > 0:
                            travel = max(int(duration / 60), 5)
                    except Exception:
                        pass

            arrival_minutes = prev_valid_minutes + travel

            # 식사 시간대 보정
            if categories[i] in MEAL_CATS:
                if arrival_minutes < LUNCH_S:
                    arrival_minutes = LUNCH_S
                elif LUNCH_E <= arrival_minutes < EARLY_D:
                    arrival_minutes = EARLY_D

            # 야경 장소: 20:00 이전이면 push
            if is_night_flags[i] and arrival_minutes < NIGHT_M:
                arrival_minutes = NIGHT_M

            # ── 제외 판단 ──────────────────────────────────────────────────
            should_remove = False
            remove_reason = ""

            # 1) 영업시간 마감 30분 이내 또는 초과
            if place and getattr(place, 'operating_hours', None):
                try:
                    _, closes = tc._parse_operating_hours(place.operating_hours)
                    if closes:
                        close_min = closes.hour * 60 + closes.minute
                        if arrival_minutes >= close_min - 30:
                            should_remove = True
                            remove_reason = f"영업 마감({closes.strftime('%H:%M')}) 시간에 방문 불가"
                except Exception:
                    pass

            # 2) 23:00 이후 비야경 장소
            if not should_remove and arrival_minutes >= REMOVE_AFTER and not is_night_flags[i]:
                h, m = divmod(arrival_minutes, 60)
                should_remove = True
                remove_reason = f"너무 늦은 시간({h:02d}:{m:02d})에 배치됨"

            if should_remove:
                to_delete_ids.append(it_id)
                messages.append(f"{pname}: {remove_reason}으로 일정에서 제외했습니다")
                # 시간 체인에서 이 장소의 체류는 제외 (prev_valid_* 유지)
                continue

            # ── DB 저장 ────────────────────────────────────────────────────
            h, m = divmod(arrival_minutes, 60)
            if h >= 24:
                h, m = 23, 59

            update_fields = {"arrival_time": time_type(h, m)}
            if not is_first_kept:
                update_fields["travel_time_from_prev"] = travel

            await trip_crud.update_itinerary(db, it_id, ItineraryUpdate(**update_fields))

            prev_valid_place   = place
            prev_valid_minutes = arrival_minutes + stays[i] + pace_buffer
            is_first_kept      = False

        # 제외 장소 삭제
        if to_delete_ids:
            await db.execute(
                sa_delete(ItineraryModel).where(ItineraryModel.id.in_(to_delete_ids))
            )
            await db.commit()

        # ── order_index를 arrival_time 순서에 맞게 재정렬 ──────────────────
        # _recalculate_day_times는 arrival_time만 업데이트하므로
        # 식사/야경 스냅으로 순서가 바뀐 경우 order_index가 틀어질 수 있음
        kept_pairs = []  # (arrival_minutes, it_id)
        for i, it_id in enumerate(it_ids):
            if it_id not in to_delete_ids:
                # 업데이트된 arrival_time을 미리 수집한 arrival_minutes로 근사
                kept_pairs.append((it_id,))
        # arrival_time 기준 재정렬이 필요한 경우를 위해 DB 재조회
        if kept_pairs and ordered_itineraries:
            # trip_id, day_number를 첫 번째 itinerary에서 추출
            first_it = ordered_itineraries[0]
            trip_id_val  = getattr(first_it, 'trip_id', None)
            day_num_val  = getattr(first_it, 'day_number', None)
            if trip_id_val and day_num_val:
                result_ord = await db.execute(
                    sa_select(ItineraryModel.id, ItineraryModel.arrival_time)
                    .where(
                        ItineraryModel.trip_id == trip_id_val,
                        ItineraryModel.day_number == day_num_val
                    )
                    .order_by(ItineraryModel.arrival_time.nullsfirst(), ItineraryModel.id)
                )
                sorted_ids = [row[0] for row in result_ord.fetchall()]
                for new_idx, sid in enumerate(sorted_ids, start=1):
                    await trip_crud.update_itinerary(
                        db, sid, ItineraryUpdate(order_index=new_idx)
                    )
                if sorted_ids:
                    await db.commit()

        return messages

    async def _apply_modify(self, db, trip, change) -> Optional[dict]:
        """시간/메모 수정"""
        target = self._find_itinerary_by_name(
            change.get("place_name", ""), trip.itineraries
        )
        if not target:
            return None

        from Trip.dto import ItineraryUpdate
        from sqlalchemy import select as sa_select
        from core.models import Itinerary as ItineraryModel

        update_data = {}
        target_day = target.day_number
        trip_id = trip.id

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

            # arrival_time 또는 stay_duration 변경 시 당일 시간 연쇄 재계산
            recalc_warnings = []
            if "arrival_time" in update_data or "stay_duration" in update_data:
                from sqlalchemy.orm import selectinload as _selectinload
                result = await db.execute(
                    sa_select(ItineraryModel)
                    .options(_selectinload(ItineraryModel.place))
                    .where(ItineraryModel.trip_id == trip_id, ItineraryModel.day_number == target_day)
                    .order_by(ItineraryModel.order_index, ItineraryModel.id)
                )
                day_its = list(result.scalars().all())
                if day_its:
                    target_idx = next(
                        (i for i, it in enumerate(day_its) if it.id == target.id), 0
                    )
                    # arrival_time을 중간 장소에 변경한 경우: 해당 위치부터만 재계산
                    # (첫 번째 장소부터 재계산하면 지정한 시간이 덮어씌워짐)
                    if "arrival_time" in update_data and target_idx > 0:
                        sub_list = day_its[target_idx:]
                        anchor_time = day_its[target_idx].arrival_time
                        start_h = anchor_time.hour if anchor_time else 9
                        start_m = anchor_time.minute if anchor_time else 0
                        recalc_warnings = await self._recalculate_day_times(db, sub_list, start_hour=start_h, start_minute=start_m)
                    else:
                        # 첫 번째 장소 시간 변경이거나 stay_duration만 변경: 전체 재계산
                        first_time = day_its[0].arrival_time
                        start_h = first_time.hour if first_time else 9
                        start_m = first_time.minute if first_time else 0
                        recalc_warnings = await self._recalculate_day_times(db, day_its, start_hour=start_h, start_minute=start_m)

            result_dict = {"action": "modify", "place_name": target.place.name, **update_data}
            if recalc_warnings:
                result_dict["warning"] = " / ".join(recalc_warnings)
            return result_dict
        return None

    async def _apply_regenerate(
        self,
        db: AsyncSession,
        user_id: int,
        trip,
        change: dict
    ) -> Optional[dict]:
        """일정 전체 또는 특정 일차 재생성"""
        from datetime import timedelta
        from sqlalchemy import delete as sa_delete
        from core.models import Itinerary as ItineraryModel
        from Planner.dto import GenerateRequest
        from Planner.planner_service import get_planner_service
        from Recommend.preference_service import get_user_preference

        scope = change.get("scope", "full")
        themes = change.get("themes", [])
        requirements = change.get("requirements", "")

        conditions = trip.conditions or {}
        merged_themes = themes or conditions.get("themes", [])

        preference = await get_user_preference(db, user_id)
        planner = get_planner_service()
        total_days = (trip.end_date - trip.start_date).days + 1

        # 특정 일차 vs 전체 판단
        day_scope = None
        if scope != "full" and scope is not None:
            try:
                day_scope = int(scope)
            except (ValueError, TypeError):
                pass

        if day_scope is not None:
            # ── 특정 일차 재생성 ──
            other_place_ids = [
                it.place_id for it in trip.itineraries
                if it.day_number != day_scope
            ]
            target_date = trip.start_date + timedelta(days=day_scope - 1)

            request = GenerateRequest(
                title=trip.title,
                region=trip.region,
                start_date=target_date,
                end_date=target_date,
                themes=merged_themes,
                max_places_per_day=conditions.get("max_places_per_day", 10),
                exclude_places=other_place_ids,
            )

            candidates = await planner._gather_candidates(db, request, preference, 1)
            if not candidates:
                return None

            draft = await planner._generate_with_gpt(
                candidates, request, preference, 1, user_requirements=requirements
            )
            place_dict = {c['place_id']: c for c in candidates}
            places_by_day = planner._build_places_by_day(draft, place_dict)
            segmented, _ = planner.time_service.structural_split_all(places_by_day)
            segmented = await planner.route_optimizer.optimize_segments(segmented)
            constrained, _ = await planner.time_service.apply_time_calculations(
                segmented, preference, target_date
            )

            # 해당 일차 기존 itineraries 삭제
            await db.execute(
                sa_delete(ItineraryModel).where(
                    ItineraryModel.trip_id == trip.id,
                    ItineraryModel.day_number == day_scope
                )
            )
            await db.flush()

            # 새 itineraries 삽입 (GPT day=1 → 실제 day_scope로 매핑)
            itinerary_items = []
            for _, places in constrained.items():
                for place in places:
                    itinerary_items.append({
                        "place_id": place["place_id"],
                        "day_number": day_scope,
                        "order_index": place.get("order_index", 1),
                        "arrival_time": place.get("suggested_arrival_time"),
                        "stay_duration": place.get("suggested_stay_duration"),
                        "travel_time_from_prev": place.get("travel_time_from_prev"),
                        "transport_mode": place.get("transport_mode"),
                        "memo": place.get("selection_reason"),
                    })

            await trip_crud.bulk_create_itineraries(db, trip.id, itinerary_items)
            return {"action": "regenerate", "scope": f"{day_scope}일차 재생성"}

        else:
            # ── 전체 재생성 ──
            request = GenerateRequest(
                title=trip.title,
                region=trip.region,
                start_date=trip.start_date,
                end_date=trip.end_date,
                themes=merged_themes,
                max_places_per_day=conditions.get("max_places_per_day", 10),
                must_visit_places=conditions.get("must_visit_places", []),
            )

            candidates = await planner._gather_candidates(db, request, preference, total_days)
            if not candidates:
                return None

            draft = await planner._generate_with_gpt(
                candidates, request, preference, total_days, user_requirements=requirements
            )
            place_dict = {c['place_id']: c for c in candidates}
            places_by_day = planner._build_places_by_day(draft, place_dict)
            segmented, _ = planner.time_service.structural_split_all(places_by_day)
            segmented = await planner.route_optimizer.optimize_segments(segmented)
            constrained, _ = await planner.time_service.apply_time_calculations(
                segmented, preference, trip.start_date
            )

            # 모든 기존 itineraries 삭제
            await db.execute(
                sa_delete(ItineraryModel).where(ItineraryModel.trip_id == trip.id)
            )
            await db.flush()

            # 새 itineraries 삽입
            itinerary_items = []
            for day_num, places in constrained.items():
                for place in places:
                    itinerary_items.append({
                        "place_id": place["place_id"],
                        "day_number": day_num,
                        "order_index": place.get("order_index", 1),
                        "arrival_time": place.get("suggested_arrival_time"),
                        "stay_duration": place.get("suggested_stay_duration"),
                        "travel_time_from_prev": place.get("travel_time_from_prev"),
                        "transport_mode": place.get("transport_mode"),
                        "memo": place.get("selection_reason"),
                    })

            await trip_crud.bulk_create_itineraries(db, trip.id, itinerary_items)

            # themes가 변경된 경우 trip.conditions 업데이트
            if themes:
                from sqlalchemy import update as sa_update
                from core.models import Trip as TripModel
                new_conditions = {**conditions, "themes": themes}
                await db.execute(
                    sa_update(TripModel)
                    .where(TripModel.id == trip.id)
                    .values(conditions=new_conditions)
                )
                await db.commit()

            return {"action": "regenerate", "scope": "전체 재생성"}

    async def _apply_swap_places(self, db, trip, change) -> Optional[dict]:
        """같은 일차 내 두 장소의 order_index를 서로 교환"""
        it_a = self._find_itinerary_by_name(
            change.get("place_a", ""), trip.itineraries
        )
        it_b = self._find_itinerary_by_name(
            change.get("place_b", ""), trip.itineraries
        )

        if not it_a or not it_b or it_a.id == it_b.id:
            return None

        from Trip.dto import ItineraryUpdate

        order_a = it_a.order_index
        order_b = it_b.order_index
        day_a = it_a.day_number
        day_b = it_b.day_number

        # DB 업데이트 전에 각 일차의 전체 장소 목록 및 시작 시간 미리 수집
        days_snapshot: dict[int, list] = {}
        for day in {day_a, day_b}:
            its = sorted(
                [it for it in trip.itineraries if it.day_number == day],
                key=lambda x: x.order_index
            )
            days_snapshot[day] = its

        # 각 일차의 시작 시간 미리 저장
        start_times: dict[int, tuple] = {}
        for day, its in days_snapshot.items():
            ft = next((it.arrival_time for it in its if it.arrival_time is not None), None)
            start_times[day] = (ft.hour if ft else 9, ft.minute if ft else 0)

        # commit 후 ORM 객체가 expire되므로 order_index를 미리 추출 (sort key에서 사용)
        days_order: dict[int, dict[int, int]] = {
            day: {it.id: it.order_index for it in its}
            for day, its in days_snapshot.items()
        }

        # 순서만 맞교환 (일차도 다를 수 있으므로 day_number도 교환)
        await trip_crud.update_itinerary(
            db, it_a.id,
            ItineraryUpdate(day_number=day_b, order_index=order_b)
        )
        await trip_crud.update_itinerary(
            db, it_b.id,
            ItineraryUpdate(day_number=day_a, order_index=order_a)
        )

        # 영향받은 각 일차의 arrival_time 재계산
        # swap 후 순서: snapshot 기반으로 it_a ↔ it_b 교환하여 직접 구성
        for day in {day_a, day_b}:
            its = days_snapshot[day]
            # it_a가 이 날에 있었으면 it_b로 교체, it_b가 있었으면 it_a로 교체
            new_its = []
            for it in its:
                if it.id == it_a.id:
                    new_its.append(it_b)
                elif it.id == it_b.id:
                    new_its.append(it_a)
                else:
                    new_its.append(it)
            # order_index 기준으로 재정렬 (swap 전 snapshot 순서 유지, id 기준 대체만)
            new_its_sorted = sorted(new_its, key=lambda x: (
                order_b if x.id == it_b.id and day == day_a else
                order_a if x.id == it_a.id and day == day_b else
                days_order[day].get(x.id, x.order_index)  # 미리 추출된 값 사용 (expire 방지)
            ))
            if new_its_sorted:
                sh, sm = start_times[day]
                await self._recalculate_day_times(db, new_its_sorted, start_hour=sh, start_minute=sm)

        return {
            "action": "swap_places",
            "place_a": it_a.place.name,
            "place_b": it_b.place.name,
        }

    async def _apply_swap_days(self, db, trip, change) -> Optional[dict]:
        """두 일차의 모든 장소를 통째로 교환

        충돌 방지를 위해 3단계로 처리:
        1) day_a → temp(9999)
        2) day_b → day_a
        3) temp  → day_b
        """
        day_a = change.get("day_a")
        day_b = change.get("day_b")

        if not day_a or not day_b or day_a == day_b:
            return None

        from Trip.dto import ItineraryUpdate

        TEMP_DAY = 9999

        # 루프 중 in-memory day_number 변경이 꼬이지 않도록 ID만 미리 수집
        day_a_ids = [it.id for it in trip.itineraries if it.day_number == day_a]
        day_b_ids = [it.id for it in trip.itineraries if it.day_number == day_b]

        if not day_a_ids or not day_b_ids:
            return None

        for iid in day_a_ids:
            await trip_crud.update_itinerary(db, iid, ItineraryUpdate(day_number=TEMP_DAY))

        for iid in day_b_ids:
            await trip_crud.update_itinerary(db, iid, ItineraryUpdate(day_number=day_a))

        for iid in day_a_ids:
            await trip_crud.update_itinerary(db, iid, ItineraryUpdate(day_number=day_b))

        # 두 일차 모두 arrival_time 재계산 (교환 후 시간 순서 정렬)
        all_its = await trip_crud.get_itineraries_by_trip(db, trip.id)
        for day in [day_a, day_b]:
            day_its = sorted(
                [it for it in all_its if it.day_number == day],
                key=lambda x: x.order_index
            )
            if day_its:
                first_time = day_its[0].arrival_time
                sh = first_time.hour if first_time else 9
                sm = first_time.minute if first_time else 0
                await self._recalculate_day_times(db, day_its, start_hour=sh, start_minute=sm)

        return {"action": "swap_days", "day_a": day_a, "day_b": day_b}

    async def _apply_change_duration(
        self,
        db: AsyncSession,
        user_id: int,
        trip,
        change: dict
    ) -> Optional[dict]:
        """여행 기간 변경 (늘리기/줄이기)"""
        from datetime import timedelta
        from sqlalchemy import delete as sa_delete, update as sa_update
        from core.models import Itinerary as ItineraryModel, Trip as TripModel
        from Planner.dto import GenerateRequest
        from Planner.planner_service import get_planner_service
        from Recommend.preference_service import get_user_preference

        current_total_days = (trip.end_date - trip.start_date).days + 1

        # new_total_days 또는 delta_days 로 새 일수 계산
        new_total_days = change.get("new_total_days")
        delta_days = change.get("delta_days")

        if new_total_days is not None:
            new_total_days = int(new_total_days)
        elif delta_days is not None:
            new_total_days = current_total_days + int(delta_days)
        else:
            return None

        # 1일 미만 또는 14일 초과는 거부
        if new_total_days < 1 or new_total_days > 14:
            return None

        if new_total_days == current_total_days:
            return None

        new_end_date = trip.start_date + timedelta(days=new_total_days - 1)

        if new_total_days < current_total_days:
            # ── 기간 축소: 마지막 일차부터 삭제 ──
            days_to_remove = list(range(new_total_days + 1, current_total_days + 1))
            await db.execute(
                sa_delete(ItineraryModel).where(
                    ItineraryModel.trip_id == trip.id,
                    ItineraryModel.day_number.in_(days_to_remove)
                )
            )
            await db.flush()

            # trip 날짜 업데이트
            await db.execute(
                sa_update(TripModel)
                .where(TripModel.id == trip.id)
                .values(end_date=new_end_date)
            )
            await db.commit()

            return {
                "action": "change_duration",
                "old_days": current_total_days,
                "new_days": new_total_days,
                "removed_days": days_to_remove,
            }

        else:
            # ── 기간 연장: 새 일차 일정 생성 ──
            existing_place_ids = [it.place_id for it in trip.itineraries]
            conditions = trip.conditions or {}
            preference = await get_user_preference(db, user_id)
            planner = get_planner_service()
            added_days = []

            for day_num in range(current_total_days + 1, new_total_days + 1):
                target_date = trip.start_date + timedelta(days=day_num - 1)
                request = GenerateRequest(
                    title=trip.title,
                    region=trip.region,
                    start_date=target_date,
                    end_date=target_date,
                    themes=conditions.get("themes", []),
                    max_places_per_day=conditions.get("max_places_per_day", 10),
                    exclude_places=existing_place_ids,
                )

                candidates = await planner._gather_candidates(db, request, preference, 1)
                if not candidates:
                    continue

                draft = await planner._generate_with_gpt(candidates, request, preference, 1)
                place_dict = {c['place_id']: c for c in candidates}
                places_by_day = planner._build_places_by_day(draft, place_dict)
                segmented, _ = planner.time_service.structural_split_all(places_by_day)
                segmented = await planner.route_optimizer.optimize_segments(segmented)
                constrained, _ = await planner.time_service.apply_time_calculations(
                    segmented, preference, target_date
                )

                itinerary_items = []
                for _, places in constrained.items():
                    for place in places:
                        itinerary_items.append({
                            "place_id": place["place_id"],
                            "day_number": day_num,
                            "order_index": place.get("order_index", 1),
                            "arrival_time": place.get("suggested_arrival_time"),
                            "stay_duration": place.get("suggested_stay_duration"),
                            "travel_time_from_prev": place.get("travel_time_from_prev"),
                            "transport_mode": place.get("transport_mode"),
                            "memo": place.get("selection_reason"),
                        })
                        existing_place_ids.append(place["place_id"])

                await trip_crud.bulk_create_itineraries(db, trip.id, itinerary_items)
                added_days.append(day_num)

            # trip 날짜 업데이트
            await db.execute(
                sa_update(TripModel)
                .where(TripModel.id == trip.id)
                .values(end_date=new_end_date)
            )
            await db.commit()

            return {
                "action": "change_duration",
                "old_days": current_total_days,
                "new_days": new_total_days,
                "added_days": added_days,
            }

    async def _apply_bulk_modify(self, db, trip, change) -> Optional[dict]:
        """일괄 수정 — 하루 전체 또는 카테고리 단위로 stay_duration/arrival_time 변경

        change 필드:
          day_number: int|null — 대상 일차 (null이면 전체 일정)
          category: str|null — 대상 카테고리 필터 (null이면 전체)
          stay_duration: int|null — 고정 체류시간(분)으로 덮어쓰기
          stay_duration_delta: int|null — 현재 체류시간에 더할 분수 (음수=축소)
          start_time_shift: int|null — 첫 장소 도착 시간을 N분 앞당기거나 뒤로 밀기
        """
        from Trip.dto import ItineraryUpdate
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import selectinload as _selectinload
        from core.models import Itinerary as ItineraryModel

        target_day = change.get("day_number")
        target_cat = change.get("category")
        stay_fixed = change.get("stay_duration")
        stay_delta = change.get("stay_duration_delta")
        time_shift = change.get("start_time_shift")  # 분 단위

        # 대상 일정 수집
        query = (
            sa_select(ItineraryModel)
            .options(_selectinload(ItineraryModel.place))
            .where(ItineraryModel.trip_id == trip.id)
            .order_by(ItineraryModel.day_number, ItineraryModel.order_index, ItineraryModel.id)
        )
        if target_day:
            query = query.where(ItineraryModel.day_number == target_day)
        result = await db.execute(query)
        all_its = list(result.scalars().all())

        if not all_its:
            return None

        # 카테고리 필터
        targets = all_its
        if target_cat:
            targets = [it for it in all_its if it.place and target_cat in (it.place.category or "")]

        modified_count = 0

        # stay_duration 변경
        for it in targets:
            update_fields = {}
            if stay_fixed is not None:
                update_fields["stay_duration"] = max(10, min(stay_fixed, 480))
            elif stay_delta is not None:
                current = it.stay_duration or 60
                update_fields["stay_duration"] = max(10, min(current + stay_delta, 480))
            if update_fields:
                await trip_crud.update_itinerary(db, it.id, ItineraryUpdate(**update_fields))
                modified_count += 1

        # start_time_shift: 영향받는 일차들의 첫 장소 시간을 이동 후 연쇄 재계산
        if time_shift is not None:
            affected_days = {it.day_number for it in (targets if targets else all_its)}
            for day in affected_days:
                day_its_result = await db.execute(
                    sa_select(ItineraryModel)
                    .options(_selectinload(ItineraryModel.place))
                    .where(ItineraryModel.trip_id == trip.id, ItineraryModel.day_number == day)
                    .order_by(ItineraryModel.order_index, ItineraryModel.id)
                )
                day_its = list(day_its_result.scalars().all())
                if not day_its:
                    continue
                first_time = day_its[0].arrival_time
                base_min = (first_time.hour * 60 + first_time.minute) if first_time else 9 * 60
                new_min = max(0, min(base_min + time_shift, 23 * 60))
                new_h, new_m = divmod(new_min, 60)
                await self._recalculate_day_times(db, day_its, start_hour=new_h, start_minute=new_m)
        elif stay_fixed is not None or stay_delta is not None:
            # stay_duration만 변경된 경우에도 연쇄 시간 재계산
            affected_days = {it.day_number for it in targets}
            for day in affected_days:
                day_its_result = await db.execute(
                    sa_select(ItineraryModel)
                    .options(_selectinload(ItineraryModel.place))
                    .where(ItineraryModel.trip_id == trip.id, ItineraryModel.day_number == day)
                    .order_by(ItineraryModel.order_index, ItineraryModel.id)
                )
                day_its = list(day_its_result.scalars().all())
                if day_its:
                    first_time = day_its[0].arrival_time
                    start_h = first_time.hour if first_time else 9
                    start_m = first_time.minute if first_time else 0
                    await self._recalculate_day_times(db, day_its, start_hour=start_h, start_minute=start_m)

        return {
            "action": "bulk_modify",
            "modified_count": modified_count,
            "day_number": target_day,
            "category": target_cat,
        }

    async def _apply_optimize_route(
        self,
        db: AsyncSession,
        user_id: int,
        trip
    ) -> Optional[dict]:
        """현재 장소 유지 + 동선만 최적화"""
        from Planner.route_optimizer import get_route_optimizer
        from Trip.dto import ItineraryReorderItem

        if not trip.itineraries:
            return None

        places_by_day = {}
        for it in trip.itineraries:
            day = it.day_number
            if day not in places_by_day:
                places_by_day[day] = []
            places_by_day[day].append({
                "itinerary_id": it.id,
                "place_id": it.place_id,
                "place_name": it.place.name,
                "latitude": it.place.latitude,
                "longitude": it.place.longitude,
                "order_index": it.order_index,
            })

        optimizer = get_route_optimizer()
        optimized = await optimizer.optimize(places_by_day, None, None)

        reorder_items = []
        for day, places in optimized.items():
            for place in places:
                reorder_items.append(
                    ItineraryReorderItem(
                        id=place["itinerary_id"],
                        day_number=day,
                        order_index=place["order_index"]
                    )
                )

        await trip_crud.reorder_itineraries(db, trip.id, reorder_items)

        # 순서 변경 후 각 일차 arrival_time 재계산
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import selectinload as _selectinload
        from core.models import Itinerary as ItineraryModel

        from Recommend.preference_service import get_user_preference
        preference = await get_user_preference(db, user_id)
        pace_buffer = 15
        if preference and preference.travel_pace:
            pace_map = {"relaxed": 20, "moderate": 15, "packed": 10}
            pace_buffer = pace_map.get(preference.travel_pace, 15)

        all_days = sorted({item.day_number for item in reorder_items})
        for day in all_days:
            result = await db.execute(
                sa_select(ItineraryModel)
                .options(_selectinload(ItineraryModel.place))
                .where(ItineraryModel.trip_id == trip.id, ItineraryModel.day_number == day)
                .order_by(ItineraryModel.order_index, ItineraryModel.id)
            )
            day_its = list(result.scalars().all())
            if day_its:
                first_time = day_its[0].arrival_time
                start_h = first_time.hour if first_time else 9
                start_m = first_time.minute if first_time else 0
                await self._recalculate_day_times(
                    db, day_its, start_hour=start_h, start_minute=start_m,
                    pace_buffer=pace_buffer
                )

        return {"action": "optimize_route"}

    async def get_chat_history(
        self,
        db: AsyncSession,
        user_id: int,
        session_id: int
    ) -> Optional[ChatSession]:
        """대화 히스토리 조회 (session_id 기반)"""
        result = await db.execute(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def get_latest_session_by_trip(
        self,
        db: AsyncSession,
        user_id: int,
        trip_id: int
    ) -> Optional[ChatSession]:
        """특정 여행의 가장 최근 채팅 세션 조회 (trip_id 기반)

        프론트엔드에서 session_id를 저장하지 않았을 때 대화를 이어가기 위해 사용.
        """
        result = await db.execute(
            select(ChatSession)
            .where(
                ChatSession.user_id == user_id,
                ChatSession.trip_id == trip_id
            )
            .order_by(ChatSession.id.desc())
            .limit(1)
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
