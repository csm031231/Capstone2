import base64
import json
from openai import OpenAI
from typing import Optional
from core.config import get_config
from Vision.dto import VisionAnalysisResult, LocationInfo, SceneInfo, VisionResponse, ExifInfo, LocationCandidate


config = get_config()
client = OpenAI(api_key=config.openai_api_key)


VISION_PROMPT = """이 사진을 분석해서 다음 정보를 JSON 형식으로 반환해줘:

1. candidates: 가능한 위치 후보 상위 2개 (신뢰도 높은 순)
   - 각 후보: landmark, country, city, confidence
2. scene_type: 장면 유형 배열 (예: ["city", "night", "nature", "beach", "mountain", "urban", "rural"])
3. reason: 판단 근거 (한국어로)

중요:
- 반드시 2개의 후보를 제시해야 함 (2번째가 애매하면 confidence를 낮게)
- 랜드마크가 없으면 landmark는 null
- confidence는 0~1 사이 값

반드시 아래 JSON 형식으로만 응답해:
{
    "candidates": [
        {"landmark": "장소명 또는 null", "country": "국가명", "city": "도시명", "confidence": 0.0},
        {"landmark": null, "country": "국가명", "city": "도시명", "confidence": 0.0}
    ],
    "scene_type": ["유형1", "유형2"],
    "reason": "판단 근거"
}"""


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
        response = client.chat.completions.create(
            model="gpt-4o",
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
            max_tokens=500
        )

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

        # 메인 결과는 top1 기준
        return VisionAnalysisResult(
            landmark=top1.landmark if top1 else None,
            country=top1.country if top1 else None,
            city=top1.city if top1 else None,
            scene_type=result_json.get("scene_type", []),
            confidence=top1.confidence if top1 else 0.0,
            reason=result_json.get("reason"),
            top1=top1,
            top2=top2,
            confidence_gap=confidence_gap
        )

    except Exception as e:
        print(f"GPT Vision 분석 오류: {e}")
        return VisionAnalysisResult(
            confidence=0.0,
            reason=f"분석 실패: {str(e)}"
        )


def determine_type(analysis: VisionAnalysisResult, exif: Optional[ExifInfo] = None) -> str:
    """
    신뢰도 + 격차 기반 Type 결정 (A/B/C)

    개선된 로직:
    - Top-1과 Top-2의 confidence 격차를 고려
    - 격차가 클수록 확실한 판단
    """
    confidence = analysis.confidence
    gap = analysis.confidence_gap

    # EXIF GPS가 있으면 신뢰도 보정
    if exif and exif.latitude and exif.longitude:
        confidence = min(confidence + 0.1, 1.0)

    # Type A 조건 (명확한 장소)
    # 1. 랜드마크 존재 + 격차가 0.3 이상 (압도적 1위)
    # 2. 또는 confidence가 매우 높음 (0.85 이상)
    if analysis.landmark:
        if gap >= 0.3:
            return "A"  # 1위가 압도적
        if confidence >= 0.85:
            return "A"  # 매우 높은 확신

    # 랜드마크 없어도 confidence + gap 조합으로 Type A 가능
    if confidence >= 0.75 and gap >= 0.25:
        return "A"

    # Type B: 유사도 기반 예측
    # confidence가 중간이거나, 격차가 작아서 애매한 경우
    if confidence >= 0.4:
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
