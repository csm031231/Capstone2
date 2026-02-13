"""
FAISS 벡터 인덱스 관리
- 여행지 이미지 벡터 저장/검색
- 고속 유사도 검색
"""
import os
import json
import faiss
import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict


@dataclass
class PlaceVector:
    """여행지 벡터 데이터"""
    place_id: int
    name: str
    image_url: str
    tags: List[str]
    category: str
    address: str
    latitude: float
    longitude: float
    vector: Optional[np.ndarray] = None  # 저장 시 제외


class FAISSIndex:
    """FAISS 기반 벡터 인덱스"""

    def __init__(self, index_path: str = "data/faiss_index"):
        self.index_path = index_path
        self.dimension = 512  # CLIP ViT-B/32 출력 차원
        self.index: Optional[faiss.IndexFlatIP] = None  # Inner Product (코사인 유사도)
        self.metadata: List[Dict] = []  # place_id, name, tags 등 메타데이터

        self._ensure_data_dir()
        self._load_or_create_index()

    def _ensure_data_dir(self):
        """데이터 디렉토리 생성"""
        os.makedirs(self.index_path, exist_ok=True)

    def _load_or_create_index(self):
        """인덱스 로드 또는 새로 생성"""
        index_file = os.path.join(self.index_path, "places.index")
        meta_file = os.path.join(self.index_path, "places_meta.json")

        if os.path.exists(index_file) and os.path.exists(meta_file):
            # 기존 인덱스 로드
            self.index = faiss.read_index(index_file)
            with open(meta_file, "r", encoding="utf-8") as f:
                self.metadata = json.load(f)
            print(f"FAISS 인덱스 로드 완료: {self.index.ntotal}개 벡터")
        else:
            # 새 인덱스 생성 (Inner Product = 코사인 유사도, 정규화된 벡터 가정)
            self.index = faiss.IndexFlatIP(self.dimension)
            self.metadata = []
            print("새 FAISS 인덱스 생성됨")

    def save(self):
        """인덱스 저장"""
        index_file = os.path.join(self.index_path, "places.index")
        meta_file = os.path.join(self.index_path, "places_meta.json")

        faiss.write_index(self.index, index_file)
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, ensure_ascii=False, indent=2)

        print(f"FAISS 인덱스 저장 완료: {self.index.ntotal}개 벡터")

    def add_place(self, place: PlaceVector, vector: np.ndarray):
        """
        여행지 벡터 추가

        Args:
            place: 여행지 정보
            vector: 512차원 정규화된 벡터
        """
        # 벡터 정규화 확인
        vector = vector.astype(np.float32).reshape(1, -1)

        # NaN/Inf 검사
        if np.any(np.isnan(vector)) or np.any(np.isinf(vector)):
            raise ValueError("벡터에 NaN 또는 Inf 값이 포함되어 있습니다")

        norm = np.linalg.norm(vector)
        if norm == 0:
            raise ValueError("영벡터(zero vector)는 인덱스에 추가할 수 없습니다")
        vector = vector / norm

        # FAISS에 추가
        self.index.add(vector)

        # 메타데이터 저장 (vector 제외)
        meta = {
            "place_id": place.place_id,
            "name": place.name,
            "image_url": place.image_url,
            "tags": place.tags,
            "category": place.category,
            "address": place.address,
            "latitude": place.latitude,
            "longitude": place.longitude
        }
        self.metadata.append(meta)

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int = 5,
        min_similarity: float = 0.3
    ) -> List[Tuple[Dict, float]]:
        """
        유사 여행지 검색

        Args:
            query_vector: 검색할 이미지 벡터
            top_k: 반환할 최대 개수
            min_similarity: 최소 유사도 (이하는 제외)

        Returns:
            [(메타데이터, 유사도), ...] 리스트
        """
        if self.index.ntotal == 0:
            return []

        # 벡터 정규화
        query_vector = query_vector.astype(np.float32).reshape(1, -1)

        if np.any(np.isnan(query_vector)) or np.any(np.isinf(query_vector)):
            return []

        norm = np.linalg.norm(query_vector)
        if norm == 0:
            return []
        query_vector = query_vector / norm

        # 검색 (Inner Product = 코사인 유사도)
        scores, indices = self.index.search(query_vector, min(top_k * 2, self.index.ntotal))

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self.metadata):
                continue
            if score < min_similarity:
                continue

            results.append((self.metadata[idx], float(score)))

            if len(results) >= top_k:
                break

        return results

    def get_total_count(self) -> int:
        """저장된 벡터 개수"""
        return self.index.ntotal if self.index else 0


# 전역 인스턴스
_faiss_index: Optional[FAISSIndex] = None


def get_faiss_index() -> FAISSIndex:
    """FAISS 인덱스 인스턴스 반환"""
    global _faiss_index
    if _faiss_index is None:
        _faiss_index = FAISSIndex()
    return _faiss_index
