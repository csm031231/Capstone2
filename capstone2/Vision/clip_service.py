"""
CLIP 모델 서비스
- 이미지를 벡터로 변환
- Hugging Face transformers 사용
"""
import threading
import logging
import torch
from PIL import Image
from transformers import CLIPProcessor, CLIPModel
from typing import List, Optional
import numpy as np

logger = logging.getLogger(__name__)


class CLIPService:
    _instance = None
    _model = None
    _processor = None
    _device = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if CLIPService._model is None:
            with CLIPService._lock:
                if CLIPService._model is None:
                    self._load_model()

    def _load_model(self):
        """CLIP 모델 로드 (싱글톤)"""
        logger.info("CLIP 모델 로딩 중...")

        # GPU 사용 가능하면 GPU, 아니면 CPU
        CLIPService._device = "cuda" if torch.cuda.is_available() else "cpu"

        # ViT-B/32 모델 사용 (속도와 성능의 균형)
        model_name = "openai/clip-vit-base-patch32"

        CLIPService._model = CLIPModel.from_pretrained(model_name).to(CLIPService._device)
        CLIPService._processor = CLIPProcessor.from_pretrained(model_name)

        # 추론 모드로 설정 (메모리 절약)
        CLIPService._model.eval()

        logger.info(f"CLIP 모델 로드 완료 (Device: {CLIPService._device})")

    def get_image_embedding(self, image: Image.Image) -> np.ndarray:
        """
        이미지를 벡터로 변환

        Args:
            image: PIL Image 객체

        Returns:
            512차원 벡터 (numpy array)
        """
        with torch.no_grad():
            # 이미지 전처리
            inputs = CLIPService._processor(
                images=image,
                return_tensors="pt"
            ).to(CLIPService._device)

            # 이미지 임베딩 추출
            image_features = CLIPService._model.get_image_features(**inputs)

            # 정규화 (코사인 유사도 계산용)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)

            return image_features.cpu().numpy().flatten()

    def get_image_embedding_from_path(self, image_path: str) -> np.ndarray:
        """파일 경로에서 이미지 임베딩 추출"""
        image = Image.open(image_path).convert("RGB")
        return self.get_image_embedding(image)

    def get_text_embedding(self, text: str) -> np.ndarray:
        """
        텍스트를 벡터로 변환 (태그 매칭에 활용 가능)

        Args:
            text: 텍스트 (예: "바다 노을 해변")

        Returns:
            512차원 벡터
        """
        with torch.no_grad():
            inputs = CLIPService._processor(
                text=[text],
                return_tensors="pt",
                padding=True
            ).to(CLIPService._device)

            text_features = CLIPService._model.get_text_features(**inputs)
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)

            return text_features.cpu().numpy().flatten()

    def compute_similarity(self, embedding1: np.ndarray, embedding2: np.ndarray) -> float:
        """두 벡터 간 코사인 유사도 계산"""
        return float(np.dot(embedding1, embedding2))

    def compute_image_text_similarity(self, image: Image.Image, text: str) -> float:
        """이미지와 텍스트 간 유사도 (분위기 태그 매칭용)"""
        img_emb = self.get_image_embedding(image)
        txt_emb = self.get_text_embedding(text)
        return self.compute_similarity(img_emb, txt_emb)


# 전역 인스턴스 (Lazy Loading)
_clip_service: Optional[CLIPService] = None
_clip_lock = threading.Lock()


def get_clip_service() -> CLIPService:
    """CLIP 서비스 인스턴스 반환"""
    global _clip_service
    if _clip_service is None:
        with _clip_lock:
            if _clip_service is None:
                _clip_service = CLIPService()
    return _clip_service
