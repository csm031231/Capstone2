# Place/router.py
from fastapi import APIRouter
from services.kakao_service import search_places, get_route_info

router = APIRouter(
    prefix="/places",
    tags=["places"]
)

@router.get("/search")
async def search_kakao_places(keyword: str):
    return await search_places(keyword)

@router.get("/route")
async def check_route(ox: float, oy: float, dx: float, dy: float):
    return await get_route_info(ox, oy, dx, dy)