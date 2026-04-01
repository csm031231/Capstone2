"""Add missing columns to places table

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6g7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """places 테이블에 누락된 컬럼 추가"""
    # Tour API 관련 컬럼
    op.add_column('places', sa.Column('content_id', sa.Integer(), nullable=True))
    op.add_column('places', sa.Column('content_type_id', sa.Integer(), nullable=True))
    op.add_column('places', sa.Column('cat1', sa.String(), nullable=True))
    op.add_column('places', sa.Column('cat2', sa.String(), nullable=True))
    op.add_column('places', sa.Column('cat3', sa.String(), nullable=True))
    op.add_column('places', sa.Column('readcount', sa.Integer(), nullable=True))
    op.add_column('places', sa.Column('tel', sa.String(), nullable=True))
    op.add_column('places', sa.Column('homepage', sa.String(), nullable=True))

    # 축제 관련 컬럼
    op.add_column('places', sa.Column('is_festival', sa.Boolean(), server_default=sa.text('false'), nullable=True))
    op.add_column('places', sa.Column('event_start_date', sa.String(), nullable=True))
    op.add_column('places', sa.Column('event_end_date', sa.String(), nullable=True))

    # content_id 인덱스
    op.create_index(op.f('ix_places_content_id'), 'places', ['content_id'], unique=False)


def downgrade() -> None:
    """추가된 컬럼 제거"""
    op.drop_index(op.f('ix_places_content_id'), table_name='places')

    op.drop_column('places', 'event_end_date')
    op.drop_column('places', 'event_start_date')
    op.drop_column('places', 'is_festival')
    op.drop_column('places', 'homepage')
    op.drop_column('places', 'tel')
    op.drop_column('places', 'readcount')
    op.drop_column('places', 'cat3')
    op.drop_column('places', 'cat2')
    op.drop_column('places', 'cat1')
    op.drop_column('places', 'content_type_id')
    op.drop_column('places', 'content_id')
