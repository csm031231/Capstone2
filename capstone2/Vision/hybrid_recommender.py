"""
Hybrid 추천 시스템
- CLIP 유사도 (주) + 태그 매칭 (보조/Fallback)
- 최적의 추천 결과 도출
"""
from PIL import Image
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
import numpy as np

from Vision.clip_service import get_clip_service
from Vision.faiss_index import get_faiss_index
from Vision.tag_matcher import match_tags_with_places, normalize_tags


@dataclass
class RecommendationResult:
    """추천 결과"""
    place_id: int
    name: str
    address: str
    latitude: float
    longitude: float
    image_url: str
    tags: List[str]
    category: str

    # 점수 정보
    clip_score: float  # CLIP 유사도 (0~1)
    tag_score: float   # 태그 매칭 점수 (0~1)
    final_score: float # 최종 점수 (가중 합산)

    # 추천 방식
    method: str  # "clip", "tag", "hybrid"
    reason: str  # 추천 이유


class HybridRecommender:
    """
    Hybrid 추천 엔진

    전략:
    1. CLIP 유사도 검색 시도
    2. 동적 임계값으로 신뢰도 판단 (Top-K 평균 대비)
    3. 유사도 높으면 → CLIP 결과 반환
    4. 유사도 낮으면 → 태그 매칭으로 Fallback
    5. 둘 다 있으면 → 가중 합산으로 Hybrid
    """

    # 동적 임계값 배율 (Top-K 평균 대비)
    HIGH_RATIO = 1.4    # 평균의 1.4배 이상 → 높음 (CLIP만으로 충분)
    LOW_RATIO = 0.9     # 평균의 0.9배 이하 → 낮음 (태그 Fallback)

    # Fallback 고정값 (데이터 적을 때 사용)
    FALLBACK_HIGH = 0.55
    FALLBACK_LOW = 0.30

    # 가중치 (CLIP 중심)
    CLIP_WEIGHT = 0.7
    TAG_WEIGHT = 0.3

    def __init__(self):
        self.clip_service = None
        self.faiss_index = None
        self._initialized = False

    def _ensure_initialized(self):
        """지연 초기화 (첫 호출 시 모델 로드)"""
        if not self._initialized:
            self.clip_service = get_clip_service()
            self.faiss_index = get_faiss_index()
            self._initialized = True

    def _calculate_dynamic_thresholds(
        self,
        scores: List[float]
    ) -> Tuple[float, float]:
        """
        동적 임계값 계산 (Top-K 평균 대비)

        Args:
            scores: CLIP 유사도 점수 리스트

        Returns:
            (high_threshold, low_threshold)
        """
        if len(scores) < 3:
            # 데이터 적으면 고정값 사용
            return self.FALLBACK_HIGH, self.FALLBACK_LOW

        # Top-K 평균 계산 (상위 절반, 최소 2개 사용)
        top_count = max(len(scores) // 2, 2)
        top_half = scores[:min(top_count, len(scores))]
        avg_score = np.mean(top_half)

        # 동적 임계값
        high_threshold = avg_score * self.HIGH_RATIO
        low_threshold = avg_score * self.LOW_RATIO

        # 범위 제한 (너무 극단적이지 않게)
        high_threshold = max(min(high_threshold, 0.8), 0.4)
        low_threshold = max(min(low_threshold, 0.5), 0.2)

        return high_threshold, low_threshold

    def _analyze_score_distribution(
        self,
        scores: List[float]
    ) -> str:
        """
        점수 분포 분석 (디버깅/설명용)

        Returns:
            "dominant" | "competitive" | "weak"
        """
        if len(scores) < 2:
            return "weak"

        top1 = scores[0]
        top2 = scores[1] if len(scores) > 1 else 0
        gap = top1 - top2

        if gap > 0.15:
            return "dominant"    # 1위가 압도적
        elif gap > 0.05:
            return "competitive" # 경쟁 상태
        else:
            return "weak"        # 전반적으로 약함

    def recommend(
        self,
        image: Image.Image,
        tags: Optional[List[str]] = None,
        top_k: int = 5
    ) -> List[RecommendationResult]:
        """
        이미지 기반 여행지 추천

        Args:
            image: 입력 이미지
            tags: GPT Vision에서 추출한 태그 (선택)
            top_k: 반환할 추천 개수

        Returns:
            추천 결과 리스트 (점수 내림차순)
        """
        self._ensure_initialized()

        # 1. CLIP 임베딩 추출
        query_vector = self.clip_service.get_image_embedding(image)

        # 2. FAISS 검색
        clip_results = self.faiss_index.search(
            query_vector,
            top_k=top_k * 2,  # 여유있게 검색
            min_similarity=0.0  # 일단 다 가져옴
        )

        # 3. 검색 결과가 없으면 태그 Fallback
        if not clip_results:
            return self._fallback_to_tags_only(tags, top_k)

        # 4. 점수 리스트 추출
        scores = [score for _, score in clip_results]
        top_score = scores[0]

        # 5. 동적 임계값 계산
        high_threshold, low_threshold = self._calculate_dynamic_thresholds(scores)

        # 6. 점수 분포 분석
        distribution = self._analyze_score_distribution(scores)

        # 7. 전략 결정
        if top_score >= high_threshold or distribution == "dominant":
            # CLIP만으로 충분히 높거나 1위가 압도적
            return self._build_results(clip_results[:top_k], method="clip")

        elif top_score < low_threshold and distribution == "weak":
            # CLIP 낮고 전반적으로 약함 → 태그 Fallback
            if tags:
                return self._hybrid_with_tag_priority(clip_results, tags, top_k)
            else:
                return self._build_results(clip_results[:top_k], method="clip")

        else:
            # 중간 또는 경쟁 상태 → Hybrid 합산
            if tags:
                return self._hybrid_blend(clip_results, tags, top_k)
            else:
                return self._build_results(clip_results[:top_k], method="clip")

    def _hybrid_blend(
        self,
        clip_results: List[Tuple[Dict, float]],
        tags: List[str],
        top_k: int
    ) -> List[RecommendationResult]:
        """CLIP + 태그 가중 합산"""
        # 태그 점수 계산
        places = [r[0] for r in clip_results]
        tag_results = match_tags_with_places(tags, places)
        tag_scores = {p["place_id"]: score for p, score in tag_results}

        # 합산
        results = []
        for place, clip_score in clip_results:
            tag_score = tag_scores.get(place["place_id"], 0)
            final_score = (
                clip_score * self.CLIP_WEIGHT +
                tag_score * self.TAG_WEIGHT
            )

            results.append(RecommendationResult(
                place_id=place["place_id"],
                name=place["name"],
                address=place["address"],
                latitude=place["latitude"],
                longitude=place["longitude"],
                image_url=place["image_url"],
                tags=place["tags"],
                category=place["category"],
                clip_score=clip_score,
                tag_score=tag_score,
                final_score=final_score,
                method="hybrid",
                reason=self._generate_reason(clip_score, tag_score, place["tags"], tags)
            ))

        # 최종 점수로 정렬
        results.sort(key=lambda x: x.final_score, reverse=True)
        return results[:top_k]

    def _hybrid_with_tag_priority(
        self,
        clip_results: List[Tuple[Dict, float]],
        tags: List[str],
        top_k: int
    ) -> List[RecommendationResult]:
        """태그 우선 + CLIP 보조 (CLIP 낮을 때)"""
        # 태그 가중치 높임
        clip_weight = 0.3
        tag_weight = 0.7

        places = [r[0] for r in clip_results]
        tag_results = match_tags_with_places(tags, places)
        tag_scores = {p["place_id"]: score for p, score in tag_results}

        results = []
        for place, clip_score in clip_results:
            tag_score = tag_scores.get(place["place_id"], 0)
            final_score = clip_score * clip_weight + tag_score * tag_weight

            results.append(RecommendationResult(
                place_id=place["place_id"],
                name=place["name"],
                address=place["address"],
                latitude=place["latitude"],
                longitude=place["longitude"],
                image_url=place["image_url"],
                tags=place["tags"],
                category=place["category"],
                clip_score=clip_score,
                tag_score=tag_score,
                final_score=final_score,
                method="tag",
                reason=self._generate_reason(clip_score, tag_score, place["tags"], tags)
            ))

        results.sort(key=lambda x: x.final_score, reverse=True)
        return results[:top_k]

    def _fallback_to_tags_only(
        self,
        tags: Optional[List[str]],
        top_k: int
    ) -> List[RecommendationResult]:
        """FAISS에 데이터 없을 때 태그만으로"""
        if not tags:
            return []

        # FAISS 메타데이터에서 태그 매칭
        all_places = self.faiss_index.metadata
        if not all_places:
            return []

        tag_results = match_tags_with_places(tags, all_places)

        return [
            RecommendationResult(
                place_id=place["place_id"],
                name=place["name"],
                address=place["address"],
                latitude=place["latitude"],
                longitude=place["longitude"],
                image_url=place["image_url"],
                tags=place["tags"],
                category=place["category"],
                clip_score=0.0,
                tag_score=score,
                final_score=score,
                method="tag",
                reason=f"태그 매칭: {', '.join(normalize_tags(tags)[:3])}"
            )
            for place, score in tag_results[:top_k]
        ]

    def _build_results(
        self,
        clip_results: List[Tuple[Dict, float]],
        method: str
    ) -> List[RecommendationResult]:
        """CLIP 결과만으로 RecommendationResult 생성"""
        results = []
        for place, score in clip_results:
            try:
                results.append(RecommendationResult(
                    place_id=place["place_id"],
                    name=place.get("name", ""),
                    address=place.get("address", ""),
                    latitude=place.get("latitude", 0.0),
                    longitude=place.get("longitude", 0.0),
                    image_url=place.get("image_url", ""),
                    tags=place.get("tags", []),
                    category=place.get("category", ""),
                    clip_score=score,
                    tag_score=0.0,
                    final_score=score,
                    method=method,
                    reason=f"이미지 유사도 {score:.0%}"
                ))
            except KeyError:
                continue
        return results

    def _generate_reason(
        self,
        clip_score: float,
        tag_score: float,
        place_tags: List[str],
        query_tags: List[str]
    ) -> str:
        """추천 이유 생성"""
        reasons = []

        if clip_score >= 0.5:
            reasons.append(f"사진 분위기 유사도 {clip_score:.0%}")

        if tag_score > 0 and query_tags:
            matched = set(normalize_tags(place_tags)) & set(normalize_tags(query_tags))
            if matched:
                reasons.append(f"태그 일치: {', '.join(list(matched)[:3])}")

        return " / ".join(reasons) if reasons else "추천"


# 전역 인스턴스
_recommender: Optional[HybridRecommender] = None


def get_recommender() -> HybridRecommender:
    """Hybrid 추천 엔진 인스턴스"""
    global _recommender
    if _recommender is None:
        _recommender = HybridRecommender()
    return _recommender
