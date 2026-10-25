from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from skin_radar.models import MarketQuote, Opportunity, ProviderState


class SQLiteStorage:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS quote_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    price_rub TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_quote_item_time
                    ON quote_history(item_name, observed_at DESC);

                CREATE TABLE IF NOT EXISTS opportunities (
                    fingerprint TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    profit_rub TEXT NOT NULL,
                    roi_pct TEXT NOT NULL,
                    risk_score INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_states (
                    name TEXT PRIMARY KEY,
                    checked_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    fingerprint TEXT PRIMARY KEY,
                    sent_at TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    quote_count INTEGER NOT NULL,
                    opportunity_count INTEGER NOT NULL,
                    mode TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_filters (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS current_quotes (
                    provider TEXT NOT NULL,
                    quote_id TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    price_rub REAL NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(provider, quote_id)
                );
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(alerts)")}
            if "status" not in columns:
                connection.execute(
                    "ALTER TABLE alerts ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
                )
            history_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(quote_history)")
            }
            if "snapshot_key" not in history_columns:
                connection.execute("ALTER TABLE quote_history ADD COLUMN snapshot_key TEXT")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_quote_snapshot "
                "ON quote_history(snapshot_key)"
            )

    def save_scan(
        self,
        *,
        quotes: list[MarketQuote],
        opportunities: list[Opportunity],
        states: list[ProviderState],
        started_at: datetime,
        mode: str,
        replace_quotes: bool = True,
    ) -> None:
        finished = datetime.now(UTC)
        with self._connect() as connection:
            if replace_quotes:
                connection.execute("DELETE FROM current_quotes")
                connection.executemany(
                    "INSERT INTO current_quotes VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            q.provider,
                            q.quote_id,
                            q.item.market_hash_name,
                            float(q.price_rub),
                            q.observed_at.isoformat(),
                            q.model_dump_json(),
                        )
                        for q in quotes
                    ],
                )
            connection.executemany(
                """INSERT OR IGNORE INTO quote_history
                   (observed_at, provider, item_name, kind, price_rub, payload, snapshot_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        quote.observed_at.isoformat(),
                        quote.provider,
                        quote.item.market_hash_name,
                        quote.kind.value,
                        str(quote.price_rub),
                        quote.model_dump_json(),
                        hashlib.sha256(
                            f"{quote.provider}:{quote.quote_id}:{quote.observed_at.isoformat()}:{quote.price_rub}".encode()
                        ).hexdigest(),
                    )
                    for quote in quotes
                ],
            )
            connection.execute("DELETE FROM opportunities")
            connection.executemany(
                """INSERT INTO opportunities
                   (fingerprint, created_at, item_name, profit_rub, roi_pct, risk_score, payload)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item.fingerprint,
                        item.created_at.isoformat(),
                        item.item_name,
                        str(item.costs.net_profit_rub),
                        str(item.costs.roi_pct),
                        item.risk_score,
                        item.model_dump_json(),
                    )
                    for item in opportunities
                ],
            )
            connection.executemany(
                """INSERT INTO provider_states(name, checked_at, payload) VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     checked_at=excluded.checked_at, payload=excluded.payload""",
                [
                    (state.name, state.checked_at.isoformat(), state.model_dump_json())
                    for state in states
                ],
            )
            connection.execute(
                """INSERT INTO scans
                   (started_at, finished_at, quote_count, opportunity_count, mode)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    started_at.isoformat(),
                    finished.isoformat(),
                    sum(state.quote_count for state in states),
                    len(opportunities),
                    mode,
                ),
            )
            # Bound history size even when a public catalog contains tens of thousands of SKUs.
            connection.execute("""DELETE FROM quote_history WHERE id < COALESCE(
                (SELECT id FROM quote_history ORDER BY id DESC LIMIT 1 OFFSET 99999), 0)""")

    def quote_page(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        search: str = "",
        provider: str = "",
        max_price_rub: float | None = None,
    ) -> dict[str, Any]:
        clauses, params = [], []
        if search:
            clauses.append("item_name LIKE ? ESCAPE '\\'")
            escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%{escaped}%")
        if provider:
            clauses.append("provider=?")
            params.append(provider)
        if max_price_rub is not None:
            clauses.append("price_rub<=?")
            params.append(max_price_rub)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM current_quotes" + where, params
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT payload FROM current_quotes"
                + where
                + " ORDER BY price_rub, provider, quote_id LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return {"total": count, "items": [json.loads(row["payload"]) for row in rows]}

    def opportunities(self, limit: int = 100) -> list[Opportunity]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM opportunities ORDER BY CAST(profit_rub AS REAL) DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [Opportunity.model_validate_json(row["payload"]) for row in rows]

    def provider_states(self) -> list[ProviderState]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM provider_states ORDER BY name"
            ).fetchall()
        return [ProviderState.model_validate_json(row["payload"]) for row in rows]

    def history(self, item_name: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT observed_at, provider, kind, price_rub
                   FROM quote_history WHERE item_name=?
                   ORDER BY observed_at DESC LIMIT ?""",
                (item_name, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def claim_alert(self, opportunity: Opportunity, channel: str) -> bool:
        """Atomically reserve an alert fingerprint. False means it was already sent."""
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO alerts
                    (fingerprint, sent_at, channel, item_name, payload) VALUES (?, ?, ?, ?, ?)""",
                    (
                        opportunity.fingerprint,
                        datetime.now(UTC).isoformat(),
                        channel,
                        opportunity.item_name,
                        opportunity.model_dump_json(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def release_alert(self, fingerprint: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM alerts WHERE fingerprint=?", (fingerprint,))

    def alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT sent_at, channel, item_name, payload, status FROM alerts
                   ORDER BY sent_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [
            {
                "sent_at": row["sent_at"],
                "channel": row["channel"],
                "item_name": row["item_name"],
                "status": row["status"],
                "opportunity": json.loads(row["payload"]),
            }
            for row in rows
        ]

    def stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            latest = connection.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
            scan_count = connection.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
            alert_count = connection.execute(
                "SELECT COUNT(*) FROM alerts WHERE status='sent'"
            ).fetchone()[0]
            alert_attempts = connection.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        return {
            "scan_count": scan_count,
            "alert_count": alert_count,
            "alert_attempts": alert_attempts,
            "latest_scan": dict(latest) if latest else None,
        }

    def set_filter(self, name: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO runtime_filters VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     value=excluded.value, updated_at=excluded.updated_at""",
                (name, value, datetime.now(UTC).isoformat()),
            )

    def filters(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT name, value FROM runtime_filters").fetchall()
        return {row["name"]: row["value"] for row in rows}

    def set_alert_status(self, fingerprint: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE alerts SET status=? WHERE fingerprint=?", (status, fingerprint)
            )

    def set_filters(self, values: dict[str, str]) -> None:
        with self._connect() as connection:
            connection.executemany(
                """INSERT INTO runtime_filters VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     value=excluded.value, updated_at=excluded.updated_at""",
                [(name, value, datetime.now(UTC).isoformat()) for name, value in values.items()],
            )
