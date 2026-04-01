import asyncio
import base64
import json
import logging
from openai import OpenAI
from typing import Optional
from core.config import get_config
from Vision.dto import VisionAnalysisResult, LocationInfo, SceneInfo, VisionResponse, ExifInfo, LocationCandidate
from Planner.constants import GPT_VISION_MAX_TOKENS

logger = logging.getLogger(__name__)

config = get_config()
client = OpenAI(api_key=config.openai_api_key)


VISION_PROMPT = """이 앱은 대한민국 국내 여행 전용 앱입니다. 사진을 분석해줘.

[중요] 이 앱은 한국 국내 여행만 다룹니다.
- landmark는 반드시 대한민국에 실재하는 장소만 작성할 것
- 해외 장소로 판단되면 landmark를 null로 설정하고 confidence 0.1 이하로 작성
- country는 항상 "대한민국"으로 고정 (해외 사진이라도 null 대신 "대한민국" 유지)

반드시 아래 JSON 형식으로만 응답해:
{
    "candidates": [
        {"landmark": "장소명 또는 null", "country": "대한민국", "city": "도시/지역명 또는 null", "confidence": 0.0},
        {"landmark": null, "country": "대한민국", "city": "도시/지역명 또는 null", "confidence": 0.0}
    ],
    "travel_tags": ["태그1", "태그2"],
    "scene_type": ["유형1", "유형2"],
    "atmosphere": "이 사진의 분위기를 한 문장으로 (예: 조용한 해변에서 일몰을 감상하는 힐링 여행)",
    "reason": "판단 근거 (한국어)"
}

landmark 작성 규칙 (중요):
- 건물·유적뿐 아니라 자연 명소도 landmark로 적극 기재할 것
- 인공 명소 예시: 경복궁, 남산타워, 해운대, 광안대교, 동대문디자인플라자, 인사동,
  명동, 북촌한옥마을, 전주한옥마을, 통영 케이블카, 여수 밤바다, 순천만습지 등
- 자연 명소 예시: 한라산, 백록담, 설악산, 대청봉, 지리산, 덕유산, 오대산, 계룡산,
  성산일출봉, 주상절리, 만장굴, 천지연폭포, 천제연폭포, 정방폭포, 협재해수욕장,
  광치기해변, 오름, 거제 바람의언덕, 소매물도, 외도, 울릉도, 독도, 변산반도,
  태안해안국립공원, 남해 독일마을, 대왕암, 주왕산, 월출산, 팔공산, 가야산,
  내연산, 청송 주산지, 산청 대원사, 하동 쌍계사 벚꽃길 등
- 특징적인 지형(화산지형, 주상절리, 독특한 암석, 특정 폭포, 특이한 해안선 등)이
  보이면 가장 가까운 한국 명소로 landmark에 기재
- 확실하지 않으면 null

travel_tags 작성 규칙:
- 아래 목록에서만 선택, 최대 5개
- 이 사진의 분위기·장소 특성과 가장 잘 맞는 것만 고를 것
자연, 바다, 해변, 산, 숲, 공원, 호수, 강, 노을, 일출,
계곡, 폭포, 오름, 분화구, 주상절리, 갯벌, 절벽, 섬, 습지,
힐링, 휴양, 조용한, 평화로운,
액티비티, 레저, 체험, 어드벤처,
역사, 문화재, 유적, 전통, 고궁, 박물관, 사찰,
도시, 야경, 시내, 번화가, 쇼핑,
맛집, 음식, 미식, 로컬푸드,
카페, 디저트, 커피, 브런치,
사진명소, 포토스팟, 전망, 경치, 뷰맛집,
여름, 겨울, 봄, 가을, 눈, 단풍, 벚꽃,
실내, 실외, 가족, 커플

scene_type 작성 규칙:
- 한국어로 작성
- 예시: 해변, 산, 숲, 계곡, 폭포, 오름, 섬, 도시, 야경, 궁궐, 사찰, 공원, 카페, 맛집 등
- 사진에 보이는 장면을 가장 잘 설명하는 유형 최대 3개

confidence 작성 규칙 (매우 중요):
- 명확한 랜드마크(건물·자연 명소 포함)가 보이고 위치가 확실하면 0.8 이상
- 특징적인 자연 지형(화산지형, 주상절리, 독특한 암석·폭포 등)이 보이면 0.6~0.8
- 랜드마크는 없지만 지역 특성(간판, 특징적 건물, 지형 등)이 보이면 0.5~0.7
- 지역 단서가 없는 일반적인 자연 풍경(흔한 해변, 평범한 산·숲 등)은 0.3 이하
- 한국 어느 지역인지 특정할 수 없으면 city를 null로 설정하고 confidence 0.2 이하
- 억지로 도시를 추측하지 말 것. 모르면 null이 정답

candidates 작성 규칙:
- 위치를 특정할 수 없으면 두 후보 모두 city: null, confidence: 0.2 이하로 작성
- 2번째 후보가 의미 없으면 첫 번째보다 낮은 confidence로 null 도시 작성"""


def encode_image_to_base64(image_path: str) -> str:
    """이미지를 base64로 인코딩"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


async def analyze_image_with_gpt(image_path: str) -> VisionAnalysisResult:
    """GPT Vision으로 이미지 분석"""

    base64_image = encode_image_to_base64(image_path)

    # 이미지 확장자 확인
    ext = image_path.lower().split(".")[-1]
    media_type = "image/jpeg" if ext in ["jpg", "jpeg"] else f"image/{ext}"

    try:
        def _call_gpt():
            return client.chat.completions.create(
                model=config.openai_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": VISION_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{media_type};base64,{base64_image}",
                                    "detail": "high"
                                }
                            }
                        ]
                    }
                ],
                max_tokens=GPT_VISION_MAX_TOKENS
            )

        response = await asyncio.to_thread(_call_gpt)

        result_text = response.choices[0].message.content

        # JSON 파싱
        # 코드 블록 제거
        if "```json" in result_text:
            result_text = result_text.split("```json")[1].split("```")[0]
        elif "```" in result_text:
            result_text = result_text.split("```")[1].split("```")[0]

        result_json = json.loads(result_text.strip())

        # Top-2 후보 파싱
        candidates = result_json.get("candidates", [])

        top1 = None
        top2 = None
        confidence_gap = 0.0

        if len(candidates) >= 1:
            c1 = candidates[0]
            top1 = LocationCandidate(
                landmark=c1.get("landmark"),
                country=c1.get("country"),
                city=c1.get("city"),
                confidence=float(c1.get("confidence", 0))
            )

        if len(candidates) >= 2:
            c2 = candidates[1]
            top2 = LocationCandidate(
                landmark=c2.get("landmark"),
                country=c2.get("country"),
                city=c2.get("city"),
                confidence=float(c2.get("confidence", 0))
            )
            confidence_gap = top1.confidence - top2.confidence if top1 else 0.0
        elif top1:
            # 후보 1개만 있으면 경쟁자 없음 → gap을 높게 설정
            confidence_gap = top1.confidence

        # 메인 결과는 top1 기준
        return VisionAnalysisResult(
            landmark=top1.landmark if top1 else None,
            country=top1.country if top1 else None,
            city=top1.city if top1 else None,
            scene_type=result_json.get("scene_type", []),
            travel_tags=result_json.get("travel_tags", []),
            atmosphere=result_json.get("atmosphere"),
            confidence=top1.confidence if top1 else 0.0,
            reason=result_json.get("reason"),
            top1=top1,
            top2=top2,
            confidence_gap=confidence_gap
        )

    except json.JSONDecodeError as e:
        logger.error(f"GPT Vision 응답 JSON 파싱 실패: {e}")
        return VisionAnalysisResult(
            confidence=0.0,
            reason=f"분석 응답 파싱 실패: {str(e)}"
        )
    except Exception as e:
        logger.error(f"GPT Vision 분석 오류: {e}")
        return VisionAnalysisResult(
            confidence=0.0,
            reason=f"분석 실패: {str(e)}"
        )


def determine_type(analysis: VisionAnalysisResult, exif: Optional[ExifInfo] = None) -> str:
    """
    신뢰도 + 격차 기반 Type 결정 (A/B/C)

    - Top-1과 Top-2의 confidence 격차를 고려
    - 자연 명소도 landmark로 인식하므로 기준 완화
    """
    confidence = analysis.confidence
    gap = analysis.confidence_gap

    # EXIF GPS가 있으면 신뢰도 보정
    if exif and exif.latitude and exif.longitude:
        confidence = min(confidence + 0.1, 1.0)

    # Type A 조건 (명확한 장소 — 건물·자연 명소 모두 포함)
    if analysis.landmark:
        if gap >= 0.25:
            return "A"  # 1위가 압도적
        if confidence >= 0.75:
            return "A"  # 높은 확신
        if confidence >= 0.5 and gap >= 0.1:
            return "A"  # 중간 확신 + 경쟁자와 격차 있음

    # 랜드마크 없어도 confidence + gap 조합으로 Type A 가능
    if confidence >= 0.65 and gap >= 0.2:
        return "A"

    # Type B: 지역은 추정되나 확신 부족
    if confidence >= 0.3:
        return "B"

    # Type C: 추정 불가
    return "C"


def build_response(
    analysis: VisionAnalysisResult,
    result_type: str,
    exif: Optional[ExifInfo] = None,
    image_path: Optional[str] = None
) -> VisionResponse:
    """최종 응답 생성"""

    location = None
    scene = None

    if result_type == "A":
        # Type A: 명확한 위치 정보
        location = LocationInfo(
            landmark=analysis.landmark,
            country=analysis.country,
            city=analysis.city
        )
        scene = SceneInfo(scene_type=analysis.scene_type)
        explanation = f"{analysis.landmark} ({analysis.city}, {analysis.country})"

    elif result_type == "B":
        # Type B: 유사도 기반 예측
        area = f"{analysis.city or ''} {analysis.country or ''}".strip() or "알 수 없는 지역"
        location = LocationInfo(
            country=analysis.country,
            city=analysis.city,
            predicted_area=area,
            similarity=analysis.confidence
        )
        scene = SceneInfo(scene_type=analysis.scene_type)
        explanation = f"{area}과 유사한 분위기입니다"

    else:
        # Type C: 분위기만 제공
        scene = SceneInfo(
            scene_type=analysis.scene_type,
            atmosphere=", ".join(analysis.scene_type) if analysis.scene_type else "알 수 없음"
        )
        explanation = "위치를 특정할 수 없습니다. 분위기 정보만 제공됩니다."

    return VisionResponse(
        type=result_type,
        location=location,
        scene=scene,
        confidence=analysis.confidence,
        explanation=explanation,
        exif=exif,
        image_path=image_path
    )
