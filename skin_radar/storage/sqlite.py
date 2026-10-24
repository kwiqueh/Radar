from __future__ import annotations

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
                """
            )
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(alerts)")}
            if "status" not in columns:
                connection.execute(
                    "ALTER TABLE alerts ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'"
                )

    def save_scan(
        self,
        *,
        quotes: list[MarketQuote],
        opportunities: list[Opportunity],
        states: list[ProviderState],
        started_at: datetime,
        mode: str,
    ) -> None:
        finished = datetime.now(UTC)
        with self._connect() as connection:
            connection.executemany(
                """INSERT INTO quote_history
                   (observed_at, provider, item_name, kind, price_rub, payload)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (
                        quote.observed_at.isoformat(),
                        quote.provider,
                        quote.item.market_hash_name,
                        quote.kind.value,
                        str(quote.price_rub),
                        quote.model_dump_json(),
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
                    len(quotes),
                    len(opportunities),
                    mode,
                ),
            )

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
