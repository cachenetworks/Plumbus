"""catalog performance indexes

Revision ID: 0004_catalog_performance_indexes
Revises: 0003_generic_media_hierarchy
Create Date: 2026-08-31
"""

from alembic import op
from sqlalchemy import inspect

revision = "0004_catalog_performance_indexes"
down_revision = "0003_generic_media_hierarchy"
branch_labels = None
depends_on = None


INDEXES = (
    ("movie_tags", "ix_movie_tags_kind_value_movie", ["kind", "value", "movie_id"]),
    ("movies", "ix_movies_type_title", ["media_type", "title"]),
    ("movies", "ix_movies_library_type_parent", ["library_id", "media_type", "parent_rating_key"]),
    (
        "movies",
        "ix_movies_library_type_grandparent",
        ["library_id", "media_type", "grandparent_rating_key"],
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table, name, columns in INDEXES:
        existing = {index["name"] for index in inspector.get_indexes(table)}
        if name not in existing:
            op.create_index(name, table, columns, unique=False)
            inspector = inspect(bind)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    for table, name, _columns in reversed(INDEXES):
        existing = {index["name"] for index in inspector.get_indexes(table)}
        if name in existing:
            op.drop_index(name, table_name=table)
            inspector = inspect(bind)
