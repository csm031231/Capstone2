from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import provide_session
from core.dependencies import verify_jwt
from core.models import User
from User.crud import get_user_by_id
from Board import crud
from Board.dto import (
    PostCreate, PostUpdate, PostDetail, PostSummary,
    PostListResponse, CommentCreate, CommentResponse, LikeResponse,
    AuthorInfo, PostImageResponse,
)

router = APIRouter(prefix="/board", tags=["board"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="users/login/token", auto_error=False)


# ────────────────────────────────────────────────────────
# 인증 의존성
# ────────────────────────────────────────────────────────

async def get_current_user(
    db: AsyncSession = Depends(provide_session),
    token: str = Depends(oauth2_scheme),
) -> User:
    """로그인 필수 의존성 (토큰 없으면 401)"""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="로그인이 필요합니다",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = verify_jwt(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 토큰입니다",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await get_user_by_id(db, int(payload["sub"]))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="사용자를 찾을 수 없습니다")
    return user


async def get_optional_user(
    db: AsyncSession = Depends(provide_session),
    token: Optional[str] = Depends(oauth2_scheme),
) -> Optional[User]:
    """로그인 선택 의존성 (토큰 없어도 None 반환, 에러 없음)"""
    if not token:
        return None
    payload = verify_jwt(token)
    if not payload:
        return None
    return await get_user_by_id(db, int(payload["sub"]))


# ────────────────────────────────────────────────────────
# 헬퍼
# ────────────────────────────────────────────────────────

def _build_summary(post) -> PostSummary:
    return PostSummary(
        id=post.id,
        author=AuthorInfo(id=post.user.id, nickname=post.user.nickname),
        trip_id=post.trip_id,
        title=post.title,
        content_preview=post.content[:100],
        region=post.region,
        travel_start_date=post.travel_start_date,
        travel_end_date=post.travel_end_date,
        tags=post.tags,
        thumbnail_url=post.thumbnail_url,
        view_count=post.view_count,
        like_count=post.like_count,
        comment_count=post.comment_count,
        created_at=post.created_at,
    )


def _build_comment(comment) -> CommentResponse:
    return CommentResponse(
        id=comment.id,
        post_id=comment.post_id,
        author=AuthorInfo(id=comment.user.id, nickname=comment.user.nickname),
        parent_id=comment.parent_id,
        content=comment.content,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
        replies=[_build_comment(r) for r in (comment.replies or [])],
    )


def _build_detail(post, is_liked: bool) -> PostDetail:
    # 최상위 댓글만 반환 (대댓글은 replies에 포함)
    top_comments = [c for c in post.comments if c.parent_id is None]
    return PostDetail(
        id=post.id,
        author=AuthorInfo(id=post.user.id, nickname=post.user.nickname),
        trip_id=post.trip_id,
        title=post.title,
        content=post.content,
        region=post.region,
        travel_start_date=post.travel_start_date,
        travel_end_date=post.travel_end_date,
        tags=post.tags,
        thumbnail_url=post.thumbnail_url,
        images=[
            PostImageResponse(id=img.id, image_url=img.image_url, order_index=img.order_index)
            for img in sorted(post.images, key=lambda x: x.order_index)
        ],
        comments=[_build_comment(c) for c in top_comments],
        view_count=post.view_count,
        like_count=post.like_count,
        comment_count=post.comment_count,
        is_liked=is_liked,
        created_at=post.created_at,
        updated_at=post.updated_at,
    )


# ────────────────────────────────────────────────────────
# 게시글 엔드포인트
# ────────────────────────────────────────────────────────

@router.get("", response_model=PostListResponse)
async def list_posts(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    region: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    db: AsyncSession = Depends(provide_session),
):
    """게시글 목록 조회 (로그인 불필요)"""
    skip = (page - 1) * size
    items, total = await crud.get_posts(db, skip=skip, limit=size, region=region, tag=tag)
    total_pages = (total + size - 1) // size

    return PostListResponse(
        items=[_build_summary(p) for p in items],
        total=total,
        page=page,
        size=size,
        total_pages=total_pages,
    )


@router.post("", response_model=PostDetail, status_code=status.HTTP_201_CREATED)
async def create_post(
    data: PostCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session),
):
    """게시글 작성 (로그인 필요)"""
    post = await crud.create_post(db, current_user.id, data)
    return _build_detail(post, is_liked=False)


@router.get("/{post_id}", response_model=PostDetail)
async def get_post(
    post_id: int,
    current_user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(provide_session),
):
    """게시글 상세 조회 (로그인 불필요, 로그인 시 좋아요 여부 포함)"""
    post = await crud.get_post_by_id(db, post_id)
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="게시글을 찾을 수 없습니다")

    await crud.increment_view_count(db, post_id)

    # commit 이후 post 객체가 expire되므로 재조회 (MissingGreenlet 방지)
    post = await crud.get_post_by_id(db, post_id)

    is_liked = False
    if current_user:
        like = await crud.get_like(db, post_id, current_user.id)
        is_liked = like is not None

    return _build_detail(post, is_liked=is_liked)


@router.put("/{post_id}", response_model=PostDetail)
async def update_post(
    post_id: int,
    data: PostUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session),
):
    """게시글 수정 (작성자 본인만 가능)"""
    post = await crud.get_post_by_id(db, post_id)
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="게시글을 찾을 수 없습니다")
    if post.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="수정 권한이 없습니다")

    post = await crud.update_post(db, post, data)
    like = await crud.get_like(db, post_id, current_user.id)
    return _build_detail(post, is_liked=like is not None)


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session),
):
    """게시글 삭제 (작성자 본인만 가능)"""
    post = await crud.get_post_by_id(db, post_id)
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="게시글을 찾을 수 없습니다")
    if post.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="삭제 권한이 없습니다")

    await crud.delete_post(db, post)


# ────────────────────────────────────────────────────────
# 댓글 엔드포인트
# ────────────────────────────────────────────────────────

@router.post("/{post_id}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED)
async def create_comment(
    post_id: int,
    data: CommentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session),
):
    """댓글/대댓글 작성 (로그인 필요)"""
    post = await crud.get_post_by_id(db, post_id)
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="게시글을 찾을 수 없습니다")

    comment = await crud.create_comment(db, post_id, current_user.id, data)
    return _build_comment(comment)


@router.delete("/{post_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(
    post_id: int,
    comment_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session),
):
    """댓글 삭제 (작성자 본인만 가능)"""
    comment = await crud.get_comment_by_id(db, comment_id)
    if not comment or comment.post_id != post_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="댓글을 찾을 수 없습니다")
    if comment.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="삭제 권한이 없습니다")

    await crud.delete_comment(db, comment)


# ────────────────────────────────────────────────────────
# 좋아요 엔드포인트
# ────────────────────────────────────────────────────────

@router.post("/{post_id}/like", response_model=LikeResponse)
async def toggle_like(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(provide_session),
):
    """좋아요 토글 (로그인 필요, 이미 눌렀으면 취소)"""
    post = await crud.get_post_by_id(db, post_id)
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="게시글을 찾을 수 없습니다")

    is_liked, like_count = await crud.toggle_like(db, post_id, current_user.id)
    return LikeResponse(post_id=post_id, is_liked=is_liked, like_count=like_count)
