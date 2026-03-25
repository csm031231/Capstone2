import os
import uuid
import logging
from fastapi import APIRouter, UploadFile, File, HTTPException, status, Query, Depends
from PIL import Image
import io
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_config
from core.database import provide_session
from core.models import User, AnalysisLog
from User.user_router import get_current_user

from Vision.dto import (
    UploadResponse, RecommendedPlace, FullAnalysisResponse
)
from Vision.exif_utils import extract_exif_info
from Vision.gpt_vision import analyze_image_with_gpt, determine_type, build_response
from Vision.hybrid_recommender import get_recommender


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/vision",
    tags=["vision"]
)

# 이미지 저장 경로 (로컬)
UPLOAD_DIR = "uploads"
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def ensure_upload_dir():
    """업로드 디렉토리 생성"""
    if not os.path.exists(UPLOAD_DIR):
        os.makedirs(UPLOAD_DIR)


def _get_safe_extension(filename: Optional[str]) -> Optional[str]:
    """파일명에서 확장자를 안전하게 추출"""
    if not filename or "." not in filename:
        return None
    return filename.rsplit(".", 1)[-1].lower()


async def _validate_and_read_image(image: UploadFile):
    """
    이미지 파일 검증 및 읽기 (공통 유틸)

    Returns:
        (contents, img, ext) 튜플
    Raises:
        HTTPException on validation failure
    """
    # 확장자 검증
    ext = _get_safe_extension(image.filename)
    if not ext or ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"허용되지 않는 파일 형식입니다. 허용: {ALLOWED_EXTENSIONS}"
        )

    # 파일 읽기
    contents = await image.read()

    # 크기 검증
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"파일 크기가 너무 큽니다. 최대: {MAX_FILE_SIZE // (1024*1024)}MB"
        )

    # 이미지 열기
    try:
        img = Image.open(io.BytesIO(contents))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"이미지를 열 수 없습니다: {str(e)}"
        )

    return contents, img, ext


def _save_image(contents: bytes, ext: str) -> tuple[str, str]:
    """이미지를 디스크에 저장하고 (로컬경로, URL) 튜플 반환"""
    filename = f"{uuid.uuid4()}.{ext}"
    ensure_upload_dir()
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "wb") as f:
        f.write(contents)

    base_url = get_config().base_url.rstrip("/")
    return file_path, f"{base_url}/uploads/{filename}"


async def _save_analysis_log(
    db: AsyncSession,
    user_id: int,
    image_path: str,
    result_type: str,
    analysis_result,
    exif_info
):
    """분석 결과를 AnalysisLog에 저장"""
    try:
        log = AnalysisLog(
            user_id=user_id,
            image_path=image_path,
            predicted_location_name=analysis_result.landmark or analysis_result.city,
            confidence_score=analysis_result.confidence,
            atmosphere_tags=analysis_result.scene_type if analysis_result.scene_type else None,
            result_type=result_type
        )
        db.add(log)
        await db.commit()
    except Exception as e:
        logger.error(f"AnalysisLog 저장 실패: {e}")
        await db.rollback()


@router.post("/upload", response_model=UploadResponse)
async def upload_image(image: UploadFile = File(...)):
    """
    이미지 업로드 엔드포인트
    - 이미지 저장 (로컬)
    - 파일 검증 (형식/크기)
    - EXIF 정보 추출
    """
    contents, img, ext = await _validate_and_read_image(image)
    exif_info = extract_exif_info(img)
    file_path, image_url = _save_image(contents, ext)

    return UploadResponse(
        success=True,
        image_path=image_url,
        exif=exif_info,
        message="이미지가 성공적으로 업로드되었습니다."
    )


@router.post("/full-analyze", response_model=FullAnalysisResponse)
async def full_analyze(
    image: UploadFile = File(...),
    top_k: int = Query(default=5, ge=1, le=20, description="추천 개수"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session)
):
    """
    전체 분석 엔드포인트 (GPT Vision + 유사 여행지 추천 통합)

    1. GPT Vision으로 이미지 분석 (랜드마크, 분위기, 신뢰도)
    2. Type 분기 (A/B/C)
    3. Type B, C인 경우 → CLIP + 태그 Hybrid로 유사 여행지 추천
    4. AnalysisLog 저장
    5. 통합 응답 반환
    """
    contents, img, ext = await _validate_and_read_image(image)
    img_rgb = img.convert("RGB")
    exif_info = extract_exif_info(img)
    file_path, image_url = _save_image(contents, ext)

    # 1. GPT Vision 분석
    analysis_result = await analyze_image_with_gpt(file_path)

    # 2. Type 결정
    result_type = determine_type(analysis_result, exif_info)

    # 3. AnalysisLog 저장
    await _save_analysis_log(
        db, current_user.id, image_url, result_type, analysis_result, exif_info
    )

    # 4. 기본 응답 생성
    base_response = build_response(
        analysis=analysis_result,
        result_type=result_type,
        exif=exif_info,
        image_path=image_url
    )

    # 5. Type B, C인 경우 유사 여행지 추천
    recommendations = []
    recommendation_strategy = None

    if result_type in ["B", "C"]:
        try:
            recommender = get_recommender()

            # travel_tags 우선, 없으면 scene_type fallback
            tags = analysis_result.travel_tags if analysis_result.travel_tags else analysis_result.scene_type or None

            results = recommender.recommend(
                image=img_rgb,
                tags=tags,
                top_k=top_k
            )

            recommendations = [
                RecommendedPlace(
                    place_id=r.place_id,
                    name=r.name,
                    address=r.address,
                    latitude=r.latitude,
                    longitude=r.longitude,
                    image_url=r.image_url,
                    tags=r.tags,
                    category=r.category,
                    clip_score=r.clip_score,
                    tag_score=r.tag_score,
                    final_score=r.final_score,
                    method=r.method,
                    reason=r.reason
                )
                for r in results
            ]

            if recommendations:
                methods = set(r.method for r in recommendations)
                if "hybrid" in methods:
                    recommendation_strategy = "CLIP + 태그 Hybrid"
                elif "tag" in methods:
                    recommendation_strategy = "태그 매칭 (Fallback)"
                else:
                    recommendation_strategy = "CLIP 이미지 유사도"
            else:
                recommendation_strategy = "유사 장소를 찾지 못했습니다"

        except Exception as e:
            logger.error(f"추천 처리 중 오류: {e}")
            recommendation_strategy = f"추천 처리 실패: {str(e)}"

    return FullAnalysisResponse(
        type=base_response.type,
        location=base_response.location,
        scene=base_response.scene,
        confidence=base_response.confidence,
        explanation=base_response.explanation,
        exif=base_response.exif,
        image_path=base_response.image_path,
        recommendations=recommendations,
        recommendation_strategy=recommendation_strategy
    )
