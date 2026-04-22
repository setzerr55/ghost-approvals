"""SQLite storage for caches and monitored wallets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id     INTEGER PRIMARY KEY,
    username        TEXT,
    first_seen_ts   INTEGER NOT NULL,
    last_active_ts  INTEGER NOT NULL,
    is_pro          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS monitored_wallets (
    telegram_id     INTEGER NOT NULL,
    address         TEXT NOT NULL,
    label           TEXT,
    created_ts      INTEGER NOT NULL,
    last_scan_ts    INTEGER,
    last_score      INTEGER,
    PRIMARY KEY (telegram_id, address)
);

CREATE TABLE IF NOT EXISTS contract_cache (
    address         TEXT NOT NULL,
    chain           TEXT NOT NULL,
    name            TEXT,
    is_verified     INTEGER,
    is_malicious    INTEGER,
    created_block   INTEGER,
    created_ts      INTEGER,
    ai_summary      TEXT,
    data_json       TEXT,
    updated_ts      INTEGER NOT NULL,
    PRIMARY KEY (address, chain)
);

CREATE TABLE IF NOT EXISTS scan_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER NOT NULL,
    address         TEXT NOT NULL,
    score           INTEGER NOT NULL,
    drainable_usd   REAL NOT NULL,
    approval_count  INTEGER NOT NULL,
    ts              INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scan_history_user
    ON scan_history (telegram_id, ts DESC);
"""


async def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()


class DB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def _conn(self) -> aiosqlite.Connection:
        return await aiosqlite.connect(self.db_path)

    # users ------------------------------------------------------------------
    async def upsert_user(self, tg_id: int, username: str | None, ts: int) -> None:
        async with await self._conn() as db:
            await db.execute(
                """
                INSERT INTO users (telegram_id, username, first_seen_ts, last_active_ts)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = excluded.username,
                    last_active_ts = excluded.last_active_ts
                """,
                (tg_id, username, ts, ts),
            )
            await db.commit()

    # monitored wallets -------------------------------------------------------
    async def add_monitored(self, tg_id: int, address: str, ts: int) -> None:
        address = address.lower()
        async with await self._conn() as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO monitored_wallets
                    (telegram_id, address, created_ts)
                VALUES (?, ?, ?)
                """,
                (tg_id, address, ts),
            )
            await db.commit()

    async def remove_monitored(self, tg_id: int, address: str) -> None:
        async with await self._conn() as db:
            await db.execute(
                "DELETE FROM monitored_wallets WHERE telegram_id=? AND address=?",
                (tg_id, address.lower()),
            )
            await db.commit()

    async def list_monitored(self, tg_id: int) -> list[str]:
        async with await self._conn() as db:
            async with db.execute(
                "SELECT address FROM monitored_wallets WHERE telegram_id=?",
                (tg_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [r[0] for r in rows]

    async def list_all_monitored(self) -> list[tuple[int, str]]:
        async with await self._conn() as db:
            async with db.execute(
                "SELECT telegram_id, address FROM monitored_wallets"
            ) as cur:
                rows = await cur.fetchall()
        return [(r[0], r[1]) for r in rows]

    async def update_monitored_scan(
        self, tg_id: int, address: str, score: int, ts: int
    ) -> None:
        async with await self._conn() as db:
            await db.execute(
                """
                UPDATE monitored_wallets
                SET last_scan_ts = ?, last_score = ?
                WHERE telegram_id = ? AND address = ?
                """,
                (ts, score, tg_id, address.lower()),
            )
            await db.commit()

    # contract cache ----------------------------------------------------------
    async def get_contract_cache(
        self, address: str, chain: str
    ) -> dict[str, Any] | None:
        async with await self._conn() as db:
            async with db.execute(
                """
                SELECT name, is_verified, is_malicious, created_block, created_ts,
                       ai_summary, data_json, updated_ts
                FROM contract_cache WHERE address=? AND chain=?
                """,
                (address.lower(), chain),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return {
            "name": row[0],
            "is_verified": bool(row[1]) if row[1] is not None else None,
            "is_malicious": bool(row[2]) if row[2] is not None else None,
            "created_block": row[3],
            "created_ts": row[4],
            "ai_summary": row[5],
            "data": json.loads(row[6]) if row[6] else {},
            "updated_ts": row[7],
        }

    async def set_contract_cache(
        self,
        address: str,
        chain: str,
        *,
        name: str | None,
        is_verified: bool | None,
        is_malicious: bool | None,
        created_block: int | None,
        created_ts: int | None,
        ai_summary: str | None,
        data: dict[str, Any] | None,
        updated_ts: int,
    ) -> None:
        async with await self._conn() as db:
            await db.execute(
                """
                INSERT INTO contract_cache
                    (address, chain, name, is_verified, is_malicious,
                     created_block, created_ts, ai_summary, data_json, updated_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(address, chain) DO UPDATE SET
                    name = COALESCE(excluded.name, contract_cache.name),
                    is_verified = COALESCE(excluded.is_verified, contract_cache.is_verified),
                    is_malicious = COALESCE(excluded.is_malicious, contract_cache.is_malicious),
                    created_block = COALESCE(excluded.created_block, contract_cache.created_block),
                    created_ts = COALESCE(excluded.created_ts, contract_cache.created_ts),
                    ai_summary = COALESCE(excluded.ai_summary, contract_cache.ai_summary),
                    data_json = excluded.data_json,
                    updated_ts = excluded.updated_ts
                """,
                (
                    address.lower(),
                    chain,
                    name,
                    int(is_verified) if is_verified is not None else None,
                    int(is_malicious) if is_malicious is not None else None,
                    created_block,
                    created_ts,
                    ai_summary,
                    json.dumps(data) if data is not None else None,
                    updated_ts,
                ),
            )
            await db.commit()

    # scan history ------------------------------------------------------------
    async def record_scan(
        self,
        tg_id: int,
        address: str,
        score: int,
        drainable_usd: float,
        approval_count: int,
        ts: int,
    ) -> None:
        async with await self._conn() as db:
            await db.execute(
                """
                INSERT INTO scan_history
                    (telegram_id, address, score, drainable_usd, approval_count, ts)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (tg_id, address.lower(), score, drainable_usd, approval_count, ts),
            )
            await db.commit()
