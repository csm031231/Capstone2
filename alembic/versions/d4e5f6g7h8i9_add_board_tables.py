"""Add board tables: travel_posts, post_images, post_comments, post_likes

Revision ID: d4e5f6g7h8i9
Revises: b2c3d4e5f6g7
Create Date: 2026-02-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4e5f6g7h8i9'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6g7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. travel_posts 테이블 생성
    op.create_table('travel_posts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('trip_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('region', sa.String(), nullable=True),
        sa.Column('travel_start_date', sa.Date(), nullable=True),
        sa.Column('travel_end_date', sa.Date(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=True),
        sa.Column('thumbnail_url', sa.String(), nullable=True),
        sa.Column('view_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('like_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('comment_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['trip_id'], ['trips.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_travel_posts_id'), 'travel_posts', ['id'], unique=False)

    # 2. post_images 테이블 생성
    op.create_table('post_images',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('image_url', sa.String(), nullable=False),
        sa.Column('order_index', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['post_id'], ['travel_posts.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_post_images_id'), 'post_images', ['id'], unique=False)

    # 3. post_comments 테이블 생성 (parent_id 자기참조)
    op.create_table('post_comments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['parent_id'], ['post_comments.id'], ),
        sa.ForeignKeyConstraint(['post_id'], ['travel_posts.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_post_comments_id'), 'post_comments', ['id'], unique=False)

    # 4. post_likes 테이블 생성 (중복 좋아요 방지 UniqueConstraint)
    op.create_table('post_likes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('post_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['post_id'], ['travel_posts.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('post_id', 'user_id', name='uq_post_like')
    )
    op.create_index(op.f('ix_post_likes_id'), 'post_likes', ['id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_post_likes_id'), table_name='post_likes')
    op.drop_table('post_likes')

    op.drop_index(op.f('ix_post_comments_id'), table_name='post_comments')
    op.drop_table('post_comments')

    op.drop_index(op.f('ix_post_images_id'), table_name='post_images')
    op.drop_table('post_images')

    op.drop_index(op.f('ix_travel_posts_id'), table_name='travel_posts')
    op.drop_table('travel_posts')
