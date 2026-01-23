from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


# EXIF 정보
class ExifInfo(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    taken_at: Optional[datetime] = None
    device: Optional[str] = None


# 위치 후보 (Top-2용)
class LocationCandidate(BaseModel):
    landmark: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    confidence: float = 0.0


# GPT Vision 분석 결과
class VisionAnalysisResult(BaseModel):
    landmark: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    scene_type: List[str] = []
    confidence: float = 0.0
    reason: Optional[str] = None

    # Top-2 후보 (격차 기반 판단용)
    top1: Optional[LocationCandidate] = None
    top2: Optional[LocationCandidate] = None
    confidence_gap: float = 0.0  # top1 - top2 격차


# 위치 정보
class LocationInfo(BaseModel):
    landmark: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    predicted_area: Optional[str] = None
    similarity: Optional[float] = None


# 장면 정보
class SceneInfo(BaseModel):
    scene_type: List[str] = []
    atmosphere: Optional[str] = None


# 최종 응답 (프론트용)
class VisionResponse(BaseModel):
    type: str  # "A", "B", "C"
    location: Optional[LocationInfo] = None
    scene: Optional[SceneInfo] = None
    confidence: float
    explanation: Optional[str] = None
    exif: Optional[ExifInfo] = None
    image_path: Optional[str] = None


# 업로드 응답
class UploadResponse(BaseModel):
    success: bool
    image_path: str
    exif: Optional[ExifInfo] = None
    message: str


# 추천 여행지 정보
class RecommendedPlace(BaseModel):
    place_id: int
    name: str
    address: str
    latitude: float
    longitude: float
    image_url: Optional[str] = None
    tags: List[str] = []
    category: Optional[str] = None

    # 점수
    clip_score: float      # CLIP 이미지 유사도
    tag_score: float       # 태그 매칭 점수
    final_score: float     # 최종 점수

    # 추천 정보
    method: str            # "clip", "tag", "hybrid"
    reason: str            # 추천 이유


# 추천 응답
class RecommendationResponse(BaseModel):
    success: bool
    recommendations: List[RecommendedPlace]
    total_count: int
    strategy_used: str     # 사용된 전략 설명
    message: str


# 전체 분석 + 추천 통합 응답
class FullAnalysisResponse(BaseModel):
    # GPT Vision 분석 결과
    type: str              # "A", "B", "C"
    location: Optional[LocationInfo] = None
    scene: Optional[SceneInfo] = None
    confidence: float
    explanation: Optional[str] = None
    exif: Optional[ExifInfo] = None
    image_path: Optional[str] = None

    # 유사 여행지 추천 (Type B, C일 때)
    recommendations: List[RecommendedPlace] = []
    recommendation_strategy: Optional[str] = None
