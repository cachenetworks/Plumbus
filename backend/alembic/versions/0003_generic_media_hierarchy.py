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


COLUMNS = {
    "media_type": sa.Column("media_type", sa.String(length=16), server_default="movie", nullable=False),
    "parent_rating_key": sa.Column("parent_rating_key", sa.String(length=64), nullable=True),
    "grandparent_rating_key": sa.Column("grandparent_rating_key", sa.String(length=64), nullable=True),
    "parent_title": sa.Column("parent_title", sa.String(length=255), nullable=True),
    "grandparent_title": sa.Column("grandparent_title", sa.String(length=255), nullable=True),
    "season_number": sa.Column("season_number", sa.Integer(), nullable=True),
    "episode_number": sa.Column("episode_number", sa.Integer(), nullable=True),
}
INDEXES = {
    "ix_movies_media_type": ["media_type"],
    "ix_movies_parent_rating_key": ["parent_rating_key"],
    "ix_movies_grandparent_rating_key": ["grandparent_rating_key"],
    "ix_movies_season_number": ["season_number"],
    "ix_movies_episode_number": ["episode_number"],
}


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    inspector = _inspector()
    existing_columns = {column["name"] for column in inspector.get_columns("movies")}
    for name, column in COLUMNS.items():
        if name not in existing_columns:
            op.add_column("movies", column)

    inspector = _inspector()
    existing_indexes = {index["name"] for index in inspector.get_indexes("movies")}
    for name, columns in INDEXES.items():
        if name not in existing_indexes:
            op.create_index(name, "movies", columns)


def downgrade() -> None:
    inspector = _inspector()
    existing_indexes = {index["name"] for index in inspector.get_indexes("movies")}
    for name in reversed(tuple(INDEXES)):
        if name in existing_indexes:
            op.drop_index(name, table_name="movies")

    inspector = _inspector()
    existing_columns = {column["name"] for column in inspector.get_columns("movies")}
    for name in reversed(tuple(COLUMNS)):
        if name in existing_columns:
            op.drop_column("movies", name)
