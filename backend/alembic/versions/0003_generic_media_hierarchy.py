"""add generic media hierarchy fields

Revision ID: 0003_generic_media_hierarchy
Revises: 0002_sync_plex_server_sequence
Create Date: 2026-08-31
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_generic_media_hierarchy"
down_revision = "0002_sync_plex_server_sequence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("movies", sa.Column("media_type", sa.String(length=16), server_default="movie", nullable=False))
    op.add_column("movies", sa.Column("parent_rating_key", sa.String(length=64), nullable=True))
    op.add_column("movies", sa.Column("grandparent_rating_key", sa.String(length=64), nullable=True))
    op.add_column("movies", sa.Column("parent_title", sa.String(length=255), nullable=True))
    op.add_column("movies", sa.Column("grandparent_title", sa.String(length=255), nullable=True))
    op.add_column("movies", sa.Column("season_number", sa.Integer(), nullable=True))
    op.add_column("movies", sa.Column("episode_number", sa.Integer(), nullable=True))
    op.create_index("ix_movies_media_type", "movies", ["media_type"])
    op.create_index("ix_movies_parent_rating_key", "movies", ["parent_rating_key"])
    op.create_index("ix_movies_grandparent_rating_key", "movies", ["grandparent_rating_key"])
    op.create_index("ix_movies_season_number", "movies", ["season_number"])
    op.create_index("ix_movies_episode_number", "movies", ["episode_number"])


def downgrade() -> None:
    op.drop_index("ix_movies_episode_number", table_name="movies")
    op.drop_index("ix_movies_season_number", table_name="movies")
    op.drop_index("ix_movies_grandparent_rating_key", table_name="movies")
    op.drop_index("ix_movies_parent_rating_key", table_name="movies")
    op.drop_index("ix_movies_media_type", table_name="movies")
    op.drop_column("movies", "episode_number")
    op.drop_column("movies", "season_number")
    op.drop_column("movies", "grandparent_title")
    op.drop_column("movies", "parent_title")
    op.drop_column("movies", "grandparent_rating_key")
    op.drop_column("movies", "parent_rating_key")
    op.drop_column("movies", "media_type")
