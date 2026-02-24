import math
import asyncio
from typing import List, Dict, Optional, Tuple

from services.kakao_service import get_route_info


class RouteOptimizer:
    """
    동선 최적화 서비스 (TSP 기반)

    알고리즘:
    1. Greedy Nearest Neighbor (초기 경로)
    2. 2-opt Local Search (개선)
    3. 카카오 경로 API로 실제 이동시간 계산
    """

    async def optimize(
        self,
        places_by_day: Dict[int, List[dict]],
        start_location: Optional[Dict[str, float]] = None,
        end_location: Optional[Dict[str, float]] = None
    ) -> Dict[int, List[dict]]:
        """
        각 날짜별 동선 최적화

        Args:
            places_by_day: {day_number: [place_dict, ...]}
            start_location: {'lat': float, 'lng': float}
            end_location: {'lat': float, 'lng': float} (숙소 복귀 등)

        Returns:
            최적화된 places_by_day
        """
        optimized = {}

        for day, places in places_by_day.items():
            if len(places) <= 2:
                # 2개 이하면 최적화 불필요하지만 이동 시간은 추가
                optimized[day] = await self._add_travel_times(places)
                for idx, place in enumerate(optimized[day]):
                    place['order_index'] = idx + 1
                continue

            # 시작점 설정
            if start_location and (start_location.get('lat') or start_location.get('latitude')):
                lat = start_location.get('lat') or start_location.get('latitude')
                lng = start_location.get('lng') or start_location.get('longitude')
                start = (lat, lng)
            else:
                # 첫 번째 장소를 시작점으로
                start = (places[0]['latitude'], places[0]['longitude'])

            # 종료점 설정 (숙소 복귀)
            end = None
            if end_location and (end_location.get('lat') or end_location.get('latitude')):
                lat = end_location.get('lat') or end_location.get('latitude')
                lng = end_location.get('lng') or end_location.get('longitude')
                end = (lat, lng)

            # 거리 행렬 계산 (TSP는 Haversine으로 빠르게)
            distance_matrix = self._build_distance_matrix(places)

            # 최적화 실행
            route = self._nearest_neighbor(distance_matrix, start, places)
            route = self._two_opt(route, distance_matrix)

            # end_location이 있으면 마지막 장소가 end에 가장 가까운지 확인
            if end:
                route = self._optimize_for_end_location(route, places, end)

            # 결과 재정렬
            reordered = [places[i] for i in route]

            # 카카오 경로 API로 실제 이동 시간 계산
            reordered = await self._add_travel_times(reordered)

            # order_index 재설정
            for idx, place in enumerate(reordered):
                place['order_index'] = idx + 1

            optimized[day] = reordered

        return optimized

    def _optimize_for_end_location(
        self,
        route: List[int],
        places: List[dict],
        end: Tuple[float, float]
    ) -> List[int]:
        """종료 위치(숙소 등)에 가장 가까운 장소가 마지막이 되도록 조정"""
        if len(route) < 2:
            return route

        # 마지막 장소의 종료점까지 거리
        last_idx = route[-1]
        last_dist = self._haversine(
            places[last_idx]['latitude'], places[last_idx]['longitude'],
            end[0], end[1]
        )

        # 더 가까운 장소가 있는지 확인 (마지막 3개만)
        best_route = route[:]
        best_dist = last_dist

        for i in range(max(0, len(route) - 3), len(route) - 1):
            candidate_idx = route[i]
            dist = self._haversine(
                places[candidate_idx]['latitude'], places[candidate_idx]['longitude'],
                end[0], end[1]
            )
            if dist < best_dist:
                # i를 마지막으로 이동
                new_route = route[:i] + route[i+1:] + [route[i]]
                # 총 거리 비교 (end 포함)
                new_total = self._route_distance_with_endpoints(new_route, places, None, end)
                old_total = self._route_distance_with_endpoints(route, places, None, end)
                if new_total < old_total:
                    best_route = new_route
                    best_dist = dist

        return best_route

    def _route_distance_with_endpoints(
        self,
        route: List[int],
        places: List[dict],
        start: Optional[Tuple[float, float]],
        end: Optional[Tuple[float, float]]
    ) -> float:
        """시작/종료점 포함 경로 총 거리"""
        total = 0.0

        if start and route:
            total += self._haversine(
                start[0], start[1],
                places[route[0]]['latitude'], places[route[0]]['longitude']
            )

        for i in range(len(route) - 1):
            total += self._haversine(
                places[route[i]]['latitude'], places[route[i]]['longitude'],
                places[route[i+1]]['latitude'], places[route[i+1]]['longitude']
            )

        if end and route:
            total += self._haversine(
                places[route[-1]]['latitude'], places[route[-1]]['longitude'],
                end[0], end[1]
            )

        return total

    def calculate_optimization_score(
        self,
        places_by_day: Dict[int, List[dict]]
    ) -> float:
        """
        동선 최적화 점수 계산 (0-1)

        낮은 총 이동 거리 = 높은 점수
        """
        total_distance = 0
        total_places = 0

        for day, places in places_by_day.items():
            if len(places) < 2:
                continue

            for i in range(len(places) - 1):
                dist = self._haversine(
                    places[i]['latitude'], places[i]['longitude'],
                    places[i+1]['latitude'], places[i+1]['longitude']
                )
                total_distance += dist

            total_places += len(places)

        if total_places < 2:
            return 1.0

        # 평균 거리 기반 점수 (5km 이하 = 1.0, 20km 이상 = 0.0)
        avg_distance = total_distance / (total_places - 1)

        if avg_distance <= 5:
            return 1.0
        elif avg_distance >= 20:
            return 0.0
        else:
            return 1.0 - (avg_distance - 5) / 15

    def _haversine(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float
    ) -> float:
        """두 좌표 간 거리 (km)"""
        R = 6371  # 지구 반경 (km)

        lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
        c = 2 * math.asin(math.sqrt(a))

        return R * c

    def _build_distance_matrix(self, places: List[dict]) -> List[List[float]]:
        """거리 행렬 생성"""
        n = len(places)
        matrix = [[0.0] * n for _ in range(n)]

        for i in range(n):
            for j in range(i + 1, n):
                dist = self._haversine(
                    places[i]['latitude'], places[i]['longitude'],
                    places[j]['latitude'], places[j]['longitude']
                )
                matrix[i][j] = dist
                matrix[j][i] = dist

        return matrix

    def _nearest_neighbor(
        self,
        matrix: List[List[float]],
        start: Tuple[float, float],
        places: List[dict]
    ) -> List[int]:
        """최근접 이웃 알고리즘"""
        n = len(matrix)
        visited = [False] * n

        # 시작점에서 가장 가까운 장소 찾기
        min_dist = float('inf')
        start_idx = 0
        for i, place in enumerate(places):
            dist = self._haversine(
                start[0], start[1],
                place['latitude'], place['longitude']
            )
            if dist < min_dist:
                min_dist = dist
                start_idx = i

        route = [start_idx]
        visited[start_idx] = True

        for _ in range(n - 1):
            current = route[-1]
            nearest = -1
            min_dist = float('inf')

            for j in range(n):
                if not visited[j] and matrix[current][j] < min_dist:
                    min_dist = matrix[current][j]
                    nearest = j

            if nearest >= 0:
                route.append(nearest)
                visited[nearest] = True

        return route

    def _two_opt(
        self,
        route: List[int],
        matrix: List[List[float]]
    ) -> List[int]:
        """2-opt 로컬 서치로 경로 개선"""
        improved = True
        best_route = route[:]
        best_distance = self._route_distance(best_route, matrix)

        while improved:
            improved = False
            for i in range(1, len(best_route) - 1):
                for j in range(i + 1, len(best_route)):
                    # i~j 구간 뒤집기
                    new_route = (
                        best_route[:i] +
                        best_route[i:j+1][::-1] +
                        best_route[j+1:]
                    )

                    new_distance = self._route_distance(new_route, matrix)
                    if new_distance < best_distance:
                        best_route = new_route
                        best_distance = new_distance
                        improved = True

        return best_route

    def _route_distance(
        self,
        route: List[int],
        matrix: List[List[float]]
    ) -> float:
        """경로 총 거리"""
        total = 0
        for i in range(len(route) - 1):
            total += matrix[route[i]][route[i + 1]]
        return total

    async def _add_travel_times(self, places: List[dict]) -> List[dict]:
        """카카오 경로 API로 실제 이동 시간 계산 (실패 시 Haversine 폴백)"""
        for i, place in enumerate(places):
            if i == 0:
                place['travel_time_from_prev'] = None
                place['transport_mode'] = None
            else:
                prev = places[i - 1]
                prev_lng = prev['longitude']
                prev_lat = prev['latitude']
                curr_lng = place['longitude']
                curr_lat = place['latitude']

                # 카카오 경로 API 호출 (경도, 위도 순서)
                route_info = await get_route_info(
                    prev_lng, prev_lat, curr_lng, curr_lat
                )

                duration = route_info.get("duration", 0)  # 초 단위
                distance = route_info.get("distance", 0)  # 미터 단위

                if duration > 0:
                    # 카카오 API 성공: 실제 도로 기반 시간
                    travel_time = max(int(duration / 60), 1)  # 초 → 분
                    dist_km = distance / 1000

                    # 500m 미만: 도보, 500m~5km: 대중교통, 5km 초과: 자동차
                    if dist_km < 0.5:
                        transport_mode = "walk"
                    elif dist_km < 5:
                        transport_mode = "public_transit"
                    else:
                        transport_mode = "car"
                else:
                    # 카카오 API 실패: Haversine 폴백
                    dist_km = self._haversine(prev_lat, prev_lng, curr_lat, curr_lng)

                    if dist_km < 0.5:
                        # 500m 미만: 도보 (분속 80m)
                        travel_time = max(int(dist_km * 1000 / 80), 5)
                        transport_mode = "walk"
                    elif dist_km < 5:
                        # 500m~5km: 대중교통 (평균 20km/h)
                        travel_time = int(dist_km / 20 * 60) + 5
                        transport_mode = "public_transit"
                    else:
                        # 5km 초과: 자동차 (평균 30km/h + 대기 10분)
                        travel_time = int(dist_km / 30 * 60) + 10
                        transport_mode = "car"

                place['travel_time_from_prev'] = max(travel_time, 5)  # 최소 5분
                place['transport_mode'] = transport_mode

        return places

    def estimate_total_travel_time(
        self,
        places_by_day: Dict[int, List[dict]]
    ) -> int:
        """총 이동 시간 추정 (분)"""
        total = 0
        for day, places in places_by_day.items():
            for place in places:
                if place.get('travel_time_from_prev'):
                    total += place['travel_time_from_prev']
        return total


# 싱글톤 인스턴스
_optimizer_instance = None


def get_route_optimizer() -> RouteOptimizer:
    """싱글톤 최적화 서비스 반환"""
    global _optimizer_instance
    if _optimizer_instance is None:
        _optimizer_instance = RouteOptimizer()
    return _optimizer_instance
