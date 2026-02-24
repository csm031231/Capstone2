from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from sqlalchemy.orm import selectinload
from typing import List, Optional

from core.models import TravelPost, PostImage, PostComment, PostLike
from Board.dto import PostCreate, PostUpdate, CommentCreate


# ==================== 게시글 CRUD ====================

async def create_post(
    db: AsyncSession,
    user_id: int,
    data: PostCreate
) -> TravelPost:
    """게시글 생성"""
    # 썸네일: 첫 번째 이미지 URL 사용
    thumbnail = data.image_urls[0] if data.image_urls else None

    post = TravelPost(
        user_id=user_id,
        trip_id=data.trip_id,
        title=data.title,
        content=data.content,
        region=data.region,
        travel_start_date=data.travel_start_date,
        travel_end_date=data.travel_end_date,
        tags=data.tags,
        thumbnail_url=thumbnail,
    )
    db.add(post)
    await db.flush()  # post.id 확보

    # 이미지 저장
    for idx, url in enumerate(data.image_urls or []):
        db.add(PostImage(post_id=post.id, image_url=url, order_index=idx))

    await db.commit()
    return await get_post_by_id(db, post.id)


async def get_post_by_id(
    db: AsyncSession,
    post_id: int
) -> Optional[TravelPost]:
    """게시글 상세 조회 (이미지, 댓글, 작성자 포함)"""
    result = await db.execute(
        select(TravelPost)
        .options(
            selectinload(TravelPost.user),
            selectinload(TravelPost.images),
            selectinload(TravelPost.comments)
            .selectinload(PostComment.user),
            selectinload(TravelPost.comments)
            .selectinload(PostComment.replies)
            .selectinload(PostComment.user),
        )
        .where(TravelPost.id == post_id)
    )
    return result.scalar_one_or_none()


async def get_posts(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
    region: Optional[str] = None,
    tag: Optional[str] = None,
) -> tuple[List[TravelPost], int]:
    """게시글 목록 조회 (페이지네이션). (items, total) 반환"""
    query = (
        select(TravelPost)
        .options(selectinload(TravelPost.user))
        .order_by(TravelPost.created_at.desc())
    )
    count_query = select(func.count()).select_from(TravelPost)

    if region:
        query = query.where(TravelPost.region == region)
        count_query = count_query.where(TravelPost.region == region)

    if tag:
        # JSON 배열에서 특정 태그 포함 여부 (PostgreSQL JSON contains)
        query = query.where(TravelPost.tags.contains([tag]))
        count_query = count_query.where(TravelPost.tags.contains([tag]))

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    items_result = await db.execute(query.offset(skip).limit(limit))
    items = items_result.scalars().all()

    return items, total


async def update_post(
    db: AsyncSession,
    post: TravelPost,
    data: PostUpdate
) -> TravelPost:
    """게시글 수정"""
    update_data = data.model_dump(exclude_unset=True)
    image_urls = update_data.pop("image_urls", None)

    for key, value in update_data.items():
        setattr(post, key, value)

    # 이미지 목록 교체
    if image_urls is not None:
        # 기존 이미지 삭제
        await db.execute(
            select(PostImage).where(PostImage.post_id == post.id)
        )
        for img in list(post.images):
            await db.delete(img)

        # 새 이미지 추가
        for idx, url in enumerate(image_urls):
            db.add(PostImage(post_id=post.id, image_url=url, order_index=idx))

        # 썸네일 갱신
        post.thumbnail_url = image_urls[0] if image_urls else None

    await db.commit()
    return await get_post_by_id(db, post.id)


async def delete_post(db: AsyncSession, post: TravelPost) -> None:
    """게시글 삭제 (cascade로 이미지, 댓글, 좋아요 함께 삭제)"""
    await db.delete(post)
    await db.commit()


async def increment_view_count(db: AsyncSession, post_id: int) -> None:
    """조회수 증가"""
    await db.execute(
        update(TravelPost)
        .where(TravelPost.id == post_id)
        .values(view_count=TravelPost.view_count + 1)
    )
    await db.commit()


# ==================== 댓글 CRUD ====================

async def create_comment(
    db: AsyncSession,
    post_id: int,
    user_id: int,
    data: CommentCreate
) -> PostComment:
    """댓글/대댓글 생성"""
    comment = PostComment(
        post_id=post_id,
        user_id=user_id,
        parent_id=data.parent_id,
        content=data.content,
    )
    db.add(comment)

    # 게시글 comment_count 증가
    await db.execute(
        update(TravelPost)
        .where(TravelPost.id == post_id)
        .values(comment_count=TravelPost.comment_count + 1)
    )

    await db.commit()

    result = await db.execute(
        select(PostComment)
        .options(selectinload(PostComment.user))
        .where(PostComment.id == comment.id)
    )
    return result.scalar_one()


async def get_comment_by_id(
    db: AsyncSession,
    comment_id: int
) -> Optional[PostComment]:
    """댓글 조회"""
    result = await db.execute(
        select(PostComment).where(PostComment.id == comment_id)
    )
    return result.scalar_one_or_none()


async def delete_comment(
    db: AsyncSession,
    comment: PostComment
) -> None:
    """댓글 삭제"""
    post_id = comment.post_id
    await db.delete(comment)

    # 게시글 comment_count 감소 (0 미만 방지)
    await db.execute(
        update(TravelPost)
        .where(TravelPost.id == post_id, TravelPost.comment_count > 0)
        .values(comment_count=TravelPost.comment_count - 1)
    )
    await db.commit()


# ==================== 좋아요 CRUD ====================

async def get_like(
    db: AsyncSession,
    post_id: int,
    user_id: int
) -> Optional[PostLike]:
    """좋아요 조회"""
    result = await db.execute(
        select(PostLike).where(
            PostLike.post_id == post_id,
            PostLike.user_id == user_id
        )
    )
    return result.scalar_one_or_none()


async def toggle_like(
    db: AsyncSession,
    post_id: int,
    user_id: int
) -> tuple[bool, int]:
    """좋아요 토글. (is_liked, like_count) 반환"""
    existing = await get_like(db, post_id, user_id)

    if existing:
        # 좋아요 취소
        await db.delete(existing)
        await db.execute(
            update(TravelPost)
            .where(TravelPost.id == post_id, TravelPost.like_count > 0)
            .values(like_count=TravelPost.like_count - 1)
        )
        is_liked = False
    else:
        # 좋아요 추가
        db.add(PostLike(post_id=post_id, user_id=user_id))
        await db.execute(
            update(TravelPost)
            .where(TravelPost.id == post_id)
            .values(like_count=TravelPost.like_count + 1)
        )
        is_liked = True

    await db.commit()

    # 최신 like_count 조회
    result = await db.execute(
        select(TravelPost.like_count).where(TravelPost.id == post_id)
    )
    like_count = result.scalar() or 0

    return is_liked, like_count
