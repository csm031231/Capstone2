import httpx
from typing import Optional


class WikipediaService:
    """
    Wikipedia API 서비스

    부족한 설명 정보를 보충하는 용도
    """

    BASE_URL = "https://ko.wikipedia.org/api/rest_v1"
    SEARCH_URL = "https://ko.wikipedia.org/w/api.php"

    async def get_summary(self, title: str) -> Optional[str]:
        """
        Wikipedia 요약 정보 가져오기

        Args:
            title: 검색할 제목 (관광지명)

        Returns:
            요약 텍스트 또는 None
        """
        # URL 인코딩된 제목으로 요청
        endpoint = f"{self.BASE_URL}/page/summary/{title}"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    endpoint,
                    headers={"Accept": "application/json"},
                    follow_redirects=True
                )

                if response.status_code == 200:
                    data = response.json()
                    return data.get("extract", "")

        except Exception:
            pass

        return None

    async def search_and_get_summary(self, query: str) -> Optional[str]:
        """
        검색 후 가장 관련성 높은 문서의 요약 가져오기

        Args:
            query: 검색어

        Returns:
            요약 텍스트 또는 None
        """
        # 1단계: 검색
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": 1,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(self.SEARCH_URL, params=params)

                if response.status_code != 200:
                    return None

                data = response.json()
                results = data.get("query", {}).get("search", [])

                if not results:
                    return None

                # 2단계: 첫 번째 결과의 요약 가져오기
                title = results[0].get("title", "")
                return await self.get_summary(title)

        except Exception:
            pass

        return None

    async def enhance_description(
        self,
        place_name: str,
        current_description: Optional[str] = None,
        min_length: int = 50
    ) -> Optional[str]:
        """
        설명이 부족한 경우 Wikipedia에서 보충

        Args:
            place_name: 관광지명
            current_description: 현재 설명
            min_length: 최소 설명 길이

        Returns:
            보강된 설명 또는 None
        """
        # 현재 설명이 충분하면 그대로 반환
        if current_description and len(current_description) >= min_length:
            return current_description

        # Wikipedia에서 검색
        wiki_summary = await self.search_and_get_summary(place_name)

        if not wiki_summary:
            return current_description

        # 기존 설명과 병합
        if current_description:
            # 중복 방지: 기존 설명의 핵심 키워드가 Wikipedia에 있는지 확인
            if len(current_description) < 20:
                return f"{current_description}\n\n{wiki_summary}"
            else:
                return current_description
        else:
            return wiki_summary


# 싱글톤 인스턴스
_wiki_service_instance = None


def get_wikipedia_service() -> WikipediaService:
    global _wiki_service_instance
    if _wiki_service_instance is None:
        _wiki_service_instance = WikipediaService()
    return _wiki_service_instance
