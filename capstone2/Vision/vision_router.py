import os
import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException, status, Query
from PIL import Image
import io

from Vision.dto import (
    UploadResponse, VisionResponse, RecommendationResponse,
    RecommendedPlace, FullAnalysisResponse
)
from Vision.exif_utils import extract_exif_info
from Vision.gpt_vision import analyze_image_with_gpt, determine_type, build_response
from Vision.hybrid_recommender import get_recommender


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


def validate_image(file: UploadFile) -> bool:
    """이미지 파일 검증"""
    if not file.filename:
        return False

    ext = file.filename.lower().split(".")[-1]
    return ext in ALLOWED_EXTENSIONS


@router.post("/upload", response_model=UploadResponse)
async def upload_image(image: UploadFile = File(...)):
    """
    이미지 업로드 엔드포인트
    - 이미지 저장 (로컬)
    - 파일 검증 (형식/크기)
    - EXIF 정보 추출
    """
    # 파일 검증
    if not validate_image(image):
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

    # 이미지 열기 및 EXIF 추출
    try:
        img = Image.open(io.BytesIO(contents))
        exif_info = extract_exif_info(img)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"이미지를 열 수 없습니다: {str(e)}"
        )

    # UUID 기반 파일명 생성
    ext = image.filename.split(".")[-1].lower()
    filename = f"{uuid.uuid4()}.{ext}"

    # 파일 저장
    ensure_upload_dir()
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "wb") as f:
        f.write(contents)

    return UploadResponse(
        success=True,
        image_path=file_path,
        exif=exif_info,
        message="이미지가 성공적으로 업로드되었습니다."
    )


@router.post("/analyze", response_model=VisionResponse)
async def analyze_image(image: UploadFile = File(...)):
    """
    이미지 분석 엔드포인트 (업로드 + GPT Vision 분석 통합)
    - 이미지 업로드
    - EXIF 추출
    - GPT Vision 분석
    - Type A/B/C 분기
    - 최종 응답 반환
    """
    # 파일 검증
    if not validate_image(image):
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

    # 이미지 열기 및 EXIF 추출
    try:
        img = Image.open(io.BytesIO(contents))
        exif_info = extract_exif_info(img)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"이미지를 열 수 없습니다: {str(e)}"
        )

    # UUID 기반 파일명 생성 및 저장
    ext = image.filename.split(".")[-1].lower()
    filename = f"{uuid.uuid4()}.{ext}"

    ensure_upload_dir()
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "wb") as f:
        f.write(contents)

    # GPT Vision 분석
    analysis_result = await analyze_image_with_gpt(file_path)

    # Type 결정 (A/B/C)
    result_type = determine_type(analysis_result, exif_info)

    # 최종 응답 생성
    response = build_response(
        analysis=analysis_result,
        result_type=result_type,
        exif=exif_info,
        image_path=file_path
    )

    return response


@router.post("/recommend", response_model=RecommendationResponse)
async def recommend_places(
    image: UploadFile = File(...),
    top_k: int = Query(default=5, ge=1, le=20, description="추천 개수")
):
    """
    이미지 기반 유사 여행지 추천 (CLIP + 태그 Hybrid)

    - CLIP 모델로 이미지 유사도 검색
    - 유사도 낮으면 태그 매칭으로 Fallback
    - 최적의 추천 결과 반환
    """
    # 파일 검증
    if not validate_image(image):
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
        img = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"이미지를 열 수 없습니다: {str(e)}"
        )

    # Hybrid 추천 실행
    try:
        recommender = get_recommender()
        results = recommender.recommend(image=img, top_k=top_k)

        # DTO 변환
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

        # 전략 설명
        if recommendations:
            methods = set(r.method for r in recommendations)
            if "hybrid" in methods:
                strategy = "CLIP 이미지 유사도 + 태그 매칭 복합 사용"
            elif "tag" in methods:
                strategy = "태그 매칭 중심 (이미지 유사도 낮음)"
            else:
                strategy = "CLIP 이미지 유사도 중심"
        else:
            strategy = "추천 결과 없음"

        return RecommendationResponse(
            success=True,
            recommendations=recommendations,
            total_count=len(recommendations),
            strategy_used=strategy,
            message=f"{len(recommendations)}개의 유사 여행지를 찾았습니다."
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"추천 처리 중 오류: {str(e)}"
        )


@router.post("/full-analyze", response_model=FullAnalysisResponse)
async def full_analyze(
    image: UploadFile = File(...),
    top_k: int = Query(default=5, ge=1, le=20, description="추천 개수")
):
    """
    전체 분석 엔드포인트 (GPT Vision + 유사 여행지 추천 통합)

    1. GPT Vision으로 이미지 분석 (랜드마크, 분위기, 신뢰도)
    2. Type 분기 (A/B/C)
    3. Type B, C인 경우 → CLIP + 태그 Hybrid로 유사 여행지 추천
    4. 통합 응답 반환
    """
    # 파일 검증
    if not validate_image(image):
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

    # 이미지 열기 및 EXIF 추출
    try:
        img = Image.open(io.BytesIO(contents))
        img_rgb = img.convert("RGB")
        exif_info = extract_exif_info(img)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"이미지를 열 수 없습니다: {str(e)}"
        )

    # 파일 저장
    ext = image.filename.split(".")[-1].lower()
    filename = f"{uuid.uuid4()}.{ext}"
    ensure_upload_dir()
    file_path = os.path.join(UPLOAD_DIR, filename)

    with open(file_path, "wb") as f:
        f.write(contents)

    # 1. GPT Vision 분석
    analysis_result = await analyze_image_with_gpt(file_path)

    # 2. Type 결정
    result_type = determine_type(analysis_result, exif_info)

    # 3. 기본 응답 생성
    base_response = build_response(
        analysis=analysis_result,
        result_type=result_type,
        exif=exif_info,
        image_path=file_path
    )

    # 4. Type B, C인 경우 유사 여행지 추천
    recommendations = []
    recommendation_strategy = None

    if result_type in ["B", "C"]:
        try:
            recommender = get_recommender()

            # GPT가 추출한 scene_type을 태그로 활용
            tags = analysis_result.scene_type if analysis_result.scene_type else None

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

        except Exception as e:
            print(f"추천 처리 중 오류 (무시): {e}")

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
