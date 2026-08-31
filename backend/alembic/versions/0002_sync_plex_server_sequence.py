"""synchronize Plex server identity sequence

Revision ID: 0002_sync_plex_server_sequence
Revises: 0001_initial
Create Date: 2026-08-31
"""

from alembic import op
from sqlalchemy import text

revision = "0002_sync_plex_server_sequence"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(
            text(
                """
                SELECT setval(
                    pg_get_serial_sequence('plex_servers', 'id'),
                    GREATEST(COALESCE((SELECT MAX(id) FROM plex_servers), 1), 1),
                    true
                )
                """
            )
        )


def downgrade() -> None:
    pass
