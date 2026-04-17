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


VISION_PROMPT = """이 앱은 대한민국 국내 여행 전용 앱입니다. 사진을 단계적으로 분석해줘.

## 1단계: 시각적 특징 추출 (반드시 먼저 수행)
사진에서 보이는 것들을 아래 항목별로 구체적으로 서술해:
- 지형: (예: 완만한 분화구, 수직 화강암 절벽, 검은 현무암 해안, 넓은 모래사장, 기암괴석)
- 식생: (예: 한라산 구상나무, 소나무 군락, 억새밭, 동백나무, 해송)
- 수계: (예: 에메랄드빛 계곡, 다단 폭포, 황토색 강물, 맑은 청록 바다)
- 암석/토양: (예: 검은 현무암, 붉은 화강암, 흰 석회암, 황토)
- 기후/계절 단서: (예: 눈 덮인 정상, 단풍, 벚꽃, 여름 피서객)
- 인공물: (예: 특정 표지판, 데크, 등대, 케이블카, 특이한 다리)

## 2단계: 한국 자연 명소 추론
1단계에서 파악한 특징을 근거로 한국 어느 명소인지 추론해.
아래 지역별 특징을 참고해:

[제주도]
- 검은 현무암 + 완만한 오름 곡선 → 제주 오름 일대
- 분화구 + 백록담 설경 → 한라산 백록담
- 육각형 주상절리 절벽 + 바다 → 중문 주상절리
- 성산 일출봉: 바다 위 솟은 분화구 형태 + 성읍 방향 조망
- 협재·곽지: 옥빛 바다 + 흰 모래 + 비양도
- 천지연·천제연·정방: 폭포 높이·형태·주변 암석으로 구분

[강원도]
- 웅장한 화강암 암봉 + 울창한 소나무 → 설악산 (울산바위, 공룡능선 등)
- V자 협곡 + 기암절벽 → 내설악 또는 주왕산
- 넓은 초원 + 풍력발전기 → 대관령 삼양목장, 가리왕산
- 동해 해안 절벽 + 촛대 모양 바위 → 동해 촛대바위, 정동진
- 하얀 모래 + 청정 동해 → 경포대, 낙산해수욕장

[경상도]
- 억새 + 넓은 평원 능선 → 황매산, 영남알프스
- 낙동강 S자 굽이 조망 → 회룡포, 부용대
- 해금강·외도: 기암절벽 섬, 아열대 식물
- 거제 바람의언덕: 억새 + 풍차

[전라도]
- 갯벌 + 일몰 → 순천만, 변산반도
- 고창 선운산: 동백 군락
- 남해 다랑논 + 바다 조망 → 다랭이마을

[경기·충청]
- 석회암 동굴 → 단양 고수동굴
- 서해 갯벌 + 낙조 → 태안해안, 대부도

## 3단계: 결론 도출
- 1단계 특징이 특정 명소와 3가지 이상 일치 → 해당 명소로 landmark 확정, confidence 0.75 이상
- 2가지 일치 → 후보로 기재, confidence 0.5~0.74
- 1가지 이하 또는 여러 곳에 해당 → landmark null, confidence 0.3 이하
- 특징이 보이지만 어느 곳인지 특정 불가 → city만 추정, landmark null

[중요] 이 앱은 한국 국내 여행만 다룹니다.
- landmark는 반드시 대한민국에 실재하는 장소만 작성할 것
- 해외 장소로 판단되면 landmark를 null로 설정하고 confidence 0.1 이하로 작성
- country는 항상 "대한민국"으로 고정

반드시 아래 JSON 형식으로만 응답해:
{
    "visual_features": {
        "terrain": "지형 묘사",
        "vegetation": "식생 묘사",
        "water": "수계 묘사 또는 null",
        "rock_soil": "암석/토양 묘사 또는 null",
        "season_clues": "계절 단서 또는 null",
        "artificial": "인공물 묘사 또는 null"
    },
    "candidates": [
        {"landmark": "장소명 또는 null", "country": "대한민국", "city": "도시/지역명 또는 null", "confidence": 0.0},
        {"landmark": null, "country": "대한민국", "city": "도시/지역명 또는 null", "confidence": 0.0}
    ],
    "travel_tags": ["태그1", "태그2"],
    "scene_type": ["유형1", "유형2"],
    "atmosphere": "이 사진의 분위기를 한 문장으로",
    "reason": "1단계 특징 → 2단계 추론 과정을 2~3문장으로 요약 (한국어)"
}

travel_tags 작성 규칙:
- 아래 목록에서만 선택, 최대 5개
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
- 한국어로 작성, 최대 3개
- 예시: 해변, 산, 숲, 계곡, 폭포, 오름, 섬, 도시, 야경, 궁궐, 사찰, 공원, 카페, 맛집 등

confidence 작성 규칙:
- 3가지 이상 시각 특징이 특정 명소와 일치 → 0.75 이상
- 2가지 일치 → 0.5~0.74
- 1가지 이하 또는 특정 불가 → 0.3 이하
- 억지로 도시를 추측하지 말 것. 모르면 null이 정답

candidates 작성 규칙:
- 위치를 특정할 수 없으면 두 후보 모두 city: null, confidence: 0.2 이하로 작성
- 2번째 후보가 의미 없으면 첫 번째보다 낮은 confidence로 null 도시 작성

[자연 경관 특별 지침]
- 랜드마크를 특정할 수 없어도 scene_type과 travel_tags는 반드시 3개 이상 구체적으로 작성
- 계곡, 폭포, 오름, 갯벌, 절벽, 섬, 주상절리, 습지, 억새, 단풍 등 지형/식생 특징이 보이면 travel_tags에 반드시 포함
- 랜드마크를 억지로 작성하지 말고 특정 불가한 경우 landmark를 null로 두되 travel_tags에 집중
- 자연 사진에서 바다/산/숲/계곡/폭포 등 명확한 지형이 보이면 해당 태그를 travel_tags에 포함
- scene_type도 "해변", "계곡", "산", "숲" 등 지형을 구체적으로 기재할 것"""


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
    - travel_tags가 풍부하면 자연 경관도 B로 처리 (추천 받을 수 있도록)
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

    # travel_tags가 3개 이상이면 자연 경관으로 판단 → B로 처리 (추천 활성화)
    # 자연 사진은 랜드마크 특정이 어려워 confidence가 낮게 나오는 경향이 있음
    has_rich_tags = len(analysis.travel_tags or []) >= 3
    if has_rich_tags:
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
