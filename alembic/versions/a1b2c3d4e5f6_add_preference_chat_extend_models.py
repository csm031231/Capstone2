"""Add UserPreference, ChatSession and extend Trip/Itinerary

Revision ID: a1b2c3d4e5f6
Revises: 871e3607dcab
Create Date: 2026-01-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '871e3607dcab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. user_preferences 테이블 생성
    op.create_table('user_preferences',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('category_weights', sa.JSON(), nullable=True),
        sa.Column('preferred_themes', sa.JSON(), nullable=True),
        sa.Column('travel_pace', sa.String(), nullable=True),
        sa.Column('budget_level', sa.String(), nullable=True),
        sa.Column('preferred_start_time', sa.Time(), nullable=True),
        sa.Column('preferred_end_time', sa.Time(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    op.create_index(op.f('ix_user_preferences_id'), 'user_preferences', ['id'], unique=False)

    # 2. chat_sessions 테이블 생성
    op.create_table('chat_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('trip_id', sa.Integer(), nullable=True),
        sa.Column('messages', sa.JSON(), nullable=True),
        sa.Column('current_state', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['trip_id'], ['trips.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_chat_sessions_id'), 'chat_sessions', ['id'], unique=False)

    # 3. trips 테이블 확장
    op.add_column('trips', sa.Column('region', sa.String(), nullable=True))
    op.add_column('trips', sa.Column('generation_method', sa.String(), server_default='manual', nullable=True))
    op.add_column('trips', sa.Column('preference_snapshot', sa.JSON(), nullable=True))

    # 4. itineraries 테이블 확장
    op.add_column('itineraries', sa.Column('travel_time_from_prev', sa.Integer(), nullable=True))
    op.add_column('itineraries', sa.Column('transport_mode', sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # itineraries 컬럼 제거
    op.drop_column('itineraries', 'transport_mode')
    op.drop_column('itineraries', 'travel_time_from_prev')

    # trips 컬럼 제거
    op.drop_column('trips', 'preference_snapshot')
    op.drop_column('trips', 'generation_method')
    op.drop_column('trips', 'region')

    # chat_sessions 테이블 제거
    op.drop_index(op.f('ix_chat_sessions_id'), table_name='chat_sessions')
    op.drop_table('chat_sessions')

    # user_preferences 테이블 제거
    op.drop_index(op.f('ix_user_preferences_id'), table_name='user_preferences')
    op.drop_table('user_preferences')
