"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-28
"""

from alembic import op
from sqlalchemy import text

from app.db.database import Base
from app.models import models  # noqa: F401

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    bind.execute(
        text(
            """
            INSERT INTO plex_servers (id, base_url, token_ciphertext, enabled)
            VALUES (1, 'environment', 'environment', true)
            ON CONFLICT (id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
