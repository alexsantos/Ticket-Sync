from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS forwarded_tickets (
    hr_ticket_id INTEGER PRIMARY KEY,
    hr_ticket_number TEXT,
    ops_ticket_id INTEGER NOT NULL,
    ops_ticket_number TEXT,
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS forward_failures (
    hr_ticket_id INTEGER PRIMARY KEY,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    last_attempt_at TEXT NOT NULL
);
"""


@dataclass
class ForwardedTicket:
    hr_ticket_id: int
    hr_ticket_number: Optional[str]
    ops_ticket_id: int
    ops_ticket_number: Optional[str]
    synced_at: str


class StateStore:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def has_been_forwarded(self, hr_ticket_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM forwarded_tickets WHERE hr_ticket_id = ?", (hr_ticket_id,)
        ).fetchone()
        return row is not None

    def get_forwarded(self, hr_ticket_id: int) -> Optional[ForwardedTicket]:
        row = self._conn.execute(
            "SELECT * FROM forwarded_tickets WHERE hr_ticket_id = ?", (hr_ticket_id,)
        ).fetchone()
        if row is None:
            return None
        return ForwardedTicket(**dict(row))

    def record_forwarded(
        self,
        hr_ticket_id: int,
        ops_ticket_id: int,
        hr_ticket_number: Optional[str] = None,
        ops_ticket_number: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO forwarded_tickets
                    (hr_ticket_id, hr_ticket_number, ops_ticket_id, ops_ticket_number, synced_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(hr_ticket_id) DO UPDATE SET
                    hr_ticket_number = excluded.hr_ticket_number,
                    ops_ticket_id = excluded.ops_ticket_id,
                    ops_ticket_number = excluded.ops_ticket_number,
                    synced_at = excluded.synced_at
                """,
                (hr_ticket_id, hr_ticket_number, ops_ticket_id, ops_ticket_number, now),
            )
            self._conn.execute(
                "DELETE FROM forward_failures WHERE hr_ticket_id = ?", (hr_ticket_id,)
            )

    def record_failure(self, hr_ticket_id: int, error: str) -> int:
        """Records a failed forward attempt and returns the ticket's total attempt count."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO forward_failures (hr_ticket_id, attempts, last_error, last_attempt_at)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(hr_ticket_id) DO UPDATE SET
                    attempts = attempts + 1,
                    last_error = excluded.last_error,
                    last_attempt_at = excluded.last_attempt_at
                """,
                (hr_ticket_id, error, now),
            )
        row = self._conn.execute(
            "SELECT attempts FROM forward_failures WHERE hr_ticket_id = ?", (hr_ticket_id,)
        ).fetchone()
        return row["attempts"]
