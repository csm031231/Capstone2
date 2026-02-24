from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date, datetime


# ==================== 게시글 요청 DTOs ====================

class PostCreate(BaseModel):
    """게시글 작성 요청"""
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    region: Optional[str] = None
    travel_start_date: Optional[date] = None
    travel_end_date: Optional[date] = None
    trip_id: Optional[int] = None
    tags: Optional[List[str]] = []
    image_urls: Optional[List[str]] = []  # 이미지 URL 목록 (순서대로)


class PostUpdate(BaseModel):
    """게시글 수정 요청 (부분 수정)"""
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    content: Optional[str] = Field(None, min_length=1)
    region: Optional[str] = None
    travel_start_date: Optional[date] = None
    travel_end_date: Optional[date] = None
    tags: Optional[List[str]] = None
    image_urls: Optional[List[str]] = None


class CommentCreate(BaseModel):
    """댓글/대댓글 작성 요청"""
    content: str = Field(..., min_length=1, max_length=1000)
    parent_id: Optional[int] = Field(None, description="대댓글인 경우 부모 댓글 ID")


# ==================== 게시글 응답 DTOs ====================

class AuthorInfo(BaseModel):
    """작성자 정보"""
    id: int
    nickname: Optional[str] = None


class PostImageResponse(BaseModel):
    """이미지 응답"""
    id: int
    image_url: str
    order_index: int

    class Config:
        from_attributes = True


class CommentResponse(BaseModel):
    """댓글 응답"""
    id: int
    post_id: int
    author: AuthorInfo
    parent_id: Optional[int] = None
    content: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    replies: List["CommentResponse"] = []

    class Config:
        from_attributes = True


CommentResponse.model_rebuild()


class PostSummary(BaseModel):
    """게시글 목록용 요약 (content_preview 포함)"""
    id: int
    author: AuthorInfo
    trip_id: Optional[int] = None
    title: str
    content_preview: str = Field(description="본문 앞 100자 미리보기")
    region: Optional[str] = None
    travel_start_date: Optional[date] = None
    travel_end_date: Optional[date] = None
    tags: Optional[List[str]] = None
    thumbnail_url: Optional[str] = None
    view_count: int
    like_count: int
    comment_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class PostDetail(BaseModel):
    """게시글 상세"""
    id: int
    author: AuthorInfo
    trip_id: Optional[int] = None
    title: str
    content: str
    region: Optional[str] = None
    travel_start_date: Optional[date] = None
    travel_end_date: Optional[date] = None
    tags: Optional[List[str]] = None
    thumbnail_url: Optional[str] = None
    images: List[PostImageResponse] = []
    comments: List[CommentResponse] = []
    view_count: int
    like_count: int
    comment_count: int
    is_liked: bool = Field(False, description="현재 사용자의 좋아요 여부")
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class PostListResponse(BaseModel):
    """게시글 목록 응답 (페이지네이션)"""
    items: List[PostSummary]
    total: int
    page: int
    size: int
    total_pages: int


class LikeResponse(BaseModel):
    """좋아요 토글 응답"""
    post_id: int
    is_liked: bool
    like_count: int
