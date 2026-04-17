"""
Google Cloud Vision API - Landmark Detection
GPS 없는 사진에서 랜드마크를 감지하여 GPT Vision 결과를 보완
"""
import base64
import logging
import httpx
from typing import Optional
from dataclasses import dataclass

from core.config import get_config

logger = logging.getLogger(__name__)

GOOGLE_VISION_URL = "https://vision.googleapis.com/v1/images:annotate"


@dataclass
class LandmarkResult:
    name: str           # 랜드마크명 (영문)
    score: float        # 신뢰도 (0~1)
    latitude: float     # 위도
    longitude: float    # 경도


async def detect_landmark(image_path: str) -> Optional[LandmarkResult]:
    """
    Google Vision API로 랜드마크 감지

    Args:
        image_path: 로컬 이미지 파일 경로

    Returns:
        LandmarkResult (감지된 경우) 또는 None
    """
    config = get_config()
    if not config.google_vision_api_key:
        return None

    # 이미지 base64 인코딩
    try:
        with open(image_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        logger.error(f"Google Vision 이미지 읽기 실패: {e}")
        return None

    payload = {
        "requests": [{
            "image": {"content": image_b64},
            "features": [{"type": "LANDMARK_DETECTION", "maxResults": 3}]
        }]
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                GOOGLE_VISION_URL,
                params={"key": config.google_vision_api_key},
                json=payload
            )

        if response.status_code != 200:
            logger.warning(f"Google Vision API 오류: {response.status_code}")
            return None

        data = response.json()
        annotations = (
            data.get("responses", [{}])[0]
            .get("landmarkAnnotations", [])
        )

        if not annotations:
            return None

        # 신뢰도 가장 높은 결과 사용
        top = annotations[0]
        score = top.get("score", 0.0)

        # 자연 경관은 Google Vision이 낮은 score를 주는 경향이 있어 임계값 완화
        if score < 0.3:
            return None

        locations = top.get("locations", [])
        if not locations:
            return None

        lat_lng = locations[0].get("latLng", {})
        lat = lat_lng.get("latitude")
        lng = lat_lng.get("longitude")

        if lat is None or lng is None:
            return None

        return LandmarkResult(
            name=top.get("description", ""),
            score=score,
            latitude=lat,
            longitude=lng
        )

    except Exception as e:
        logger.error(f"Google Vision API 호출 실패: {e}")
        return None
