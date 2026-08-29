"""export_envelope：数据权利导出一次性下载台账（F4/R-04 导出安全）。

data_right_requests 增 downloaded_at：导出文件为一次性下载，首次下载后置位，
重复下载 409 DATA_RIGHT_EXPORT_CONSUMED。文件本体改为 envelope 密文，磁盘不落
明文（envelope 由 SECRET_KEY 包裹的每文件数据密钥加密）。

Revision ID: 0017_export_envelope
Revises: 0016_atomic_member_creation
Create Date: 2026-08-28

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_export_envelope"
down_revision: str | None = "0016_atomic_member_creation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("data_right_requests", sa.Column("downloaded_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("data_right_requests", "downloaded_at")
