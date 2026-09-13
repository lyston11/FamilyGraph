"""Add display title and activity timestamp to agent_sessions.

title：首条用户消息派生（24 字符截断）或用户重命名结果；NULL = 尚无用户消息且未重命名。
updated_at：最近一次用户消息时间；无消息会话等于 created_at。

agent_sessions 的 CHECK 以列内联命名约束存在（0024 重建产物），alembic batch
反射会产出列序错乱的 CREATE，故沿用 0024 的手工建表复制模式；batch 重建同时
会连带删除触发器，升级/降级末尾统一重建 trg_agent_sessions_scope_immutable。
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "0040_agent_session_title"
down_revision = "0039_platform_feature_configs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCOPE_TRIGGER_SQL = """
CREATE TRIGGER trg_agent_sessions_scope_immutable
BEFORE UPDATE ON agent_sessions
WHEN OLD.account_id <> NEW.account_id
  OR OLD.space_id <> NEW.space_id
  OR OLD.agent_kind <> NEW.agent_kind
BEGIN
    SELECT RAISE(ABORT, 'agent_sessions scope is immutable');
END;
"""

# 与 app.api.agent.derive_session_title / 前端 truncateSessionTitle 对齐（纯展示，各自实现）。
_TITLE_DISPLAY_LENGTH = 24


def _derive_title(text: str | None) -> str | None:
    """迁移内联实现：折叠空白后截取首 24 字符，超出补省略号。"""
    if not text:
        return None
    compact = " ".join(text.split())
    if not compact:
        return None
    chars = list(compact)
    if len(chars) <= _TITLE_DISPLAY_LENGTH:
        return compact
    return "".join(chars[:_TITLE_DISPLAY_LENGTH]) + "…"


def _rebuild_agent_sessions(conn: sa.Connection, *, with_display_columns: bool) -> None:
    """重建 agent_sessions：with_display_columns 控制 title/updated_at 的有无。"""
    previous_fk = bool(conn.execute(sa.text("PRAGMA foreign_keys")).scalar())
    conn.execute(sa.text("DROP TRIGGER IF EXISTS trg_agent_sessions_scope_immutable"))
    conn.execute(sa.text("PRAGMA foreign_keys=OFF"))
    try:
        display_columns = ", title VARCHAR(120), updated_at DATETIME NOT NULL"
        conn.execute(
            sa.text(
                "CREATE TABLE agent_sessions_new ("
                "id INTEGER NOT NULL,"
                "account_id INTEGER NOT NULL,"
                "space_id INTEGER NOT NULL,"
                "agent_kind VARCHAR(16) NOT NULL CONSTRAINT "
                "ck_agent_sessions_ck_agent_sessions_kind CHECK (agent_kind = 'assistant'),"
                "created_at DATETIME NOT NULL,"
                "term_usage_consent BOOLEAN DEFAULT (0) NOT NULL"
                + (display_columns if with_display_columns else "")
                + ",CONSTRAINT pk_agent_sessions PRIMARY KEY (id),"
                "CONSTRAINT fk_agent_sessions_account_id_accounts "
                "FOREIGN KEY(account_id) REFERENCES accounts (id) ON DELETE CASCADE,"
                "CONSTRAINT fk_agent_sessions_space_id_family_spaces "
                "FOREIGN KEY(space_id) REFERENCES family_spaces (id) ON DELETE CASCADE"
                ")"
            )
        )
        copied_columns = "id, account_id, space_id, agent_kind, created_at, term_usage_consent"
        copied_values = "id, account_id, space_id, agent_kind, created_at, term_usage_consent"
        if with_display_columns:
            copied_columns += ", title, updated_at"
            copied_values += ", NULL, created_at"
        conn.execute(
            sa.text(
                f"INSERT INTO agent_sessions_new ({copied_columns}) "
                f"SELECT {copied_values} FROM agent_sessions"
            )
        )
        conn.execute(sa.text("DROP TABLE agent_sessions"))
        conn.execute(sa.text("ALTER TABLE agent_sessions_new RENAME TO agent_sessions"))
        conn.execute(sa.text(_SCOPE_TRIGGER_SQL))
    finally:
        conn.execute(sa.text(f"PRAGMA foreign_keys={'ON' if previous_fk else 'OFF'}"))


def _backfill_display_state(conn: sa.Connection) -> None:
    """updated_at = 末条消息时间（无消息保持 created_at）；title = 首条用户消息派生。"""
    conn.execute(
        sa.text(
            "UPDATE agent_sessions SET updated_at = ("
            "SELECT MAX(m.created_at) FROM agent_messages m WHERE m.session_id = agent_sessions.id"
            ") WHERE EXISTS ("
            "SELECT 1 FROM agent_messages m WHERE m.session_id = agent_sessions.id)"
        )
    )
    first_user_rows = conn.execute(
        sa.text(
            "SELECT s.id AS session_id, MIN(m.id) AS message_id "
            "FROM agent_sessions s JOIN agent_messages m "
            "ON m.session_id = s.id AND m.role = 'user' GROUP BY s.id"
        )
    ).mappings()
    for row in first_user_rows:
        content_json = conn.execute(
            sa.text("SELECT content_json FROM agent_messages WHERE id = :mid"),
            {"mid": row["message_id"]},
        ).scalar()
        try:
            text = json.loads(content_json).get("text") if content_json else None
        except (TypeError, ValueError):
            text = None
        title = _derive_title(text if isinstance(text, str) else None)
        if title is not None:
            conn.execute(
                sa.text("UPDATE agent_sessions SET title = :title WHERE id = :sid"),
                {"title": title, "sid": row["session_id"]},
            )


def upgrade() -> None:
    conn = op.get_bind()
    _rebuild_agent_sessions(conn, with_display_columns=True)
    _backfill_display_state(conn)


def downgrade() -> None:
    conn = op.get_bind()
    _rebuild_agent_sessions(conn, with_display_columns=False)
