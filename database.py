import aiosqlite
import json
from datetime import datetime
from typing import Optional

import config


async def init_db():
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                bot_name TEXT NOT NULL,
                token TEXT NOT NULL,
                status TEXT DEFAULT 'stopped',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS code_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id INTEGER NOT NULL,
                version INTEGER NOT NULL,
                code TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bot_id) REFERENCES bots(id) ON DELETE CASCADE,
                UNIQUE(bot_id, version)
            );

            CREATE TABLE IF NOT EXISTS env_vars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                FOREIGN KEY (bot_id) REFERENCES bots(id) ON DELETE CASCADE,
                UNIQUE(bot_id, key)
            );

            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id INTEGER NOT NULL,
                level TEXT DEFAULT 'INFO',
                message TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bot_id) REFERENCES bots(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS bot_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                content BLOB NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (bot_id) REFERENCES bots(id) ON DELETE CASCADE,
                UNIQUE(bot_id, filename)
            );

            CREATE INDEX IF NOT EXISTS idx_bots_user ON bots(user_id);
            CREATE INDEX IF NOT EXISTS idx_versions_bot ON code_versions(bot_id);
            CREATE INDEX IF NOT EXISTS idx_logs_bot ON logs(bot_id);
        """)
        await db.commit()


async def add_bot(user_id: int, bot_name: str, token: str) -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO bots (user_id, bot_name, token, status) VALUES (?, ?, ?, 'stopped')",
            (user_id, bot_name, token)
        )
        await db.commit()
        return cursor.lastrowid


async def get_bot(bot_id: int) -> Optional[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM bots WHERE id = ?", (bot_id,))
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


async def get_user_bots(user_id: int) -> list:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM bots WHERE user_id = ? ORDER BY id DESC",
            (user_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def update_bot_status(bot_id: int, status: str):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "UPDATE bots SET status = ?, updated_at = ? WHERE id = ?",
            (status, datetime.utcnow().isoformat(), bot_id)
        )
        await db.commit()


async def delete_bot(bot_id: int):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
        await db.commit()


# -------------------- Code Versions --------------------

async def save_code_version(bot_id: int, code: str) -> int:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COALESCE(MAX(version), 0) FROM code_versions WHERE bot_id = ?",
            (bot_id,)
        )
        row = await cursor.fetchone()
        next_version = (row[0] or 0) + 1

        await db.execute(
            "INSERT INTO code_versions (bot_id, version, code) VALUES (?, ?, ?)",
            (bot_id, next_version, code)
        )
        await db.commit()
        return next_version


async def get_code_versions(bot_id: int) -> list:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT id, bot_id, version, created_at FROM code_versions WHERE bot_id = ? ORDER BY version DESC",
            (bot_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_code_version(bot_id: int, version: int) -> Optional[dict]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM code_versions WHERE bot_id = ? AND version = ?",
            (bot_id, version)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


async def get_latest_code(bot_id: int) -> Optional[str]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT code FROM code_versions WHERE bot_id = ? ORDER BY version DESC LIMIT 1",
            (bot_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def delete_code_version(bot_id: int, version: int) -> bool:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM code_versions WHERE bot_id = ? AND version = ?",
            (bot_id, version)
        )
        await db.commit()
        return cursor.rowcount > 0


# -------------------- Environment Variables --------------------

async def get_env_vars(bot_id: int) -> dict:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT key, value FROM env_vars WHERE bot_id = ?",
            (bot_id,)
        )
        rows = await cursor.fetchall()
        return {r[0]: r[1] for r in rows}


async def set_env_var(bot_id: int, key: str, value: str):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO env_vars (bot_id, key, value) VALUES (?, ?, ?)
            ON CONFLICT(bot_id, key) DO UPDATE SET value = excluded.value
            """,
            (bot_id, key, value)
        )
        await db.commit()


async def delete_env_var(bot_id: int, key: str):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "DELETE FROM env_vars WHERE bot_id = ? AND key = ?",
            (bot_id, key)
        )
        await db.commit()


# -------------------- Logs --------------------

async def add_log(bot_id: int, level: str, message: str):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "INSERT INTO logs (bot_id, level, message) VALUES (?, ?, ?)",
            (bot_id, level, message[:4000])
        )
        # Keep only last 500 logs per bot
        await db.execute(
            """
            DELETE FROM logs WHERE bot_id = ? AND id NOT IN (
                SELECT id FROM logs WHERE bot_id = ? ORDER BY id DESC LIMIT 500
            )
            """,
            (bot_id, bot_id)
        )
        await db.commit()


async def get_logs(bot_id: int, limit: int = 100) -> list:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT level, message, created_at FROM logs WHERE bot_id = ? ORDER BY id DESC LIMIT ?",
            (bot_id, limit)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in reversed(rows)]


# -------------------- Extra Files --------------------

async def save_bot_file(bot_id: int, filename: str, content: bytes):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO bot_files (bot_id, filename, content) VALUES (?, ?, ?)
            ON CONFLICT(bot_id, filename) DO UPDATE SET content = excluded.content
            """,
            (bot_id, filename, content)
        )
        await db.commit()


async def get_bot_files(bot_id: int) -> list:
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT filename, created_at FROM bot_files WHERE bot_id = ?",
            (bot_id,)
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_bot_file(bot_id: int, filename: str) -> Optional[bytes]:
    async with aiosqlite.connect(config.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT content FROM bot_files WHERE bot_id = ? AND filename = ?",
            (bot_id, filename)
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def delete_bot_file(bot_id: int, filename: str):
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute(
            "DELETE FROM bot_files WHERE bot_id = ? AND filename = ?",
            (bot_id, filename)
        )
        await db.commit()
