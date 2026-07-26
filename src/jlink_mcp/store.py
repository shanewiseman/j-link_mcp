"""Persistent audit, session, and artifact metadata with a hash chain."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import Artifact, CommandResult


class AuditStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS operations (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    probe_serial TEXT,
                    action TEXT NOT NULL,
                    destructive INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    entry_hash TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    sha256 TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    probe_serial TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    state_json TEXT NOT NULL
                );
                """
            )

    def append_operation(
        self,
        *,
        result: CommandResult,
        action: str,
        probe_serial: str | None,
        destructive: bool,
        request: dict[str, Any] | None = None,
    ) -> str:
        payload = {
            "action": action,
            "probe_serial": probe_serial,
            "destructive": destructive,
            "request": request or {},
            "result": result.model_dump(mode="json"),
        }
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT entry_hash FROM operations ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = str(row["entry_hash"]) if row else "0" * 64
            entry_hash = hashlib.sha256(
                (previous_hash + payload_json).encode("utf-8")
            ).hexdigest()
            connection.execute(
                """
                INSERT INTO operations (
                    operation_id, created_at, probe_serial, action, destructive,
                    payload_json, previous_hash, entry_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.operation_id,
                    datetime.now(UTC).isoformat(),
                    probe_serial,
                    action,
                    int(destructive),
                    payload_json,
                    previous_hash,
                    entry_hash,
                ),
            )
        return entry_hash

    def register_artifact(self, artifact: Artifact) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts (
                    sha256, path, size, kind, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(sha256) DO UPDATE SET
                    path = excluded.path,
                    size = excluded.size,
                    kind = excluded.kind,
                    metadata_json = excluded.metadata_json
                """,
                (
                    artifact.sha256,
                    artifact.path,
                    artifact.size,
                    artifact.kind,
                    artifact.created_at.isoformat(),
                    json.dumps(artifact.metadata, sort_keys=True),
                ),
            )

    def list_artifacts(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts ORDER BY created_at, path"
            ).fetchall()
        return [
            {
                "sha256": row["sha256"],
                "path": row["path"],
                "size": row["size"],
                "kind": row["kind"],
                "created_at": row["created_at"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def upsert_session(
        self,
        *,
        session_id: str,
        probe_serial: str,
        backend: str,
        state: dict[str, Any],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, probe_serial, backend, created_at, updated_at,
                    state_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    updated_at = excluded.updated_at,
                    state_json = excluded.state_json
                """,
                (
                    session_id,
                    probe_serial,
                    backend,
                    now,
                    now,
                    json.dumps(state, sort_keys=True),
                ),
            )

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )

    def clear_stale_sessions(self) -> list[dict[str, Any]]:
        """Remove persisted sessions because their subprocesses cannot survive restart."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT session_id, probe_serial, backend, state_json FROM sessions"
            ).fetchall()
            connection.execute("DELETE FROM sessions")
        return [
            {
                "session_id": row["session_id"],
                "probe_serial": row["probe_serial"],
                "backend": row["backend"],
                "state": json.loads(row["state_json"]),
            }
            for row in rows
        ]

    def list_operations(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, operation_id, created_at, probe_serial, action,
                       destructive, payload_json, previous_hash, entry_hash
                FROM operations ORDER BY sequence DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                **dict(row),
                "destructive": bool(row["destructive"]),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def verify_chain(self) -> tuple[bool, str | None]:
        previous_hash = "0" * 64
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM operations ORDER BY sequence"
            ).fetchall()
        for row in rows:
            if row["previous_hash"] != previous_hash:
                return False, f"chain link mismatch at sequence {row['sequence']}"
            expected = hashlib.sha256(
                (previous_hash + row["payload_json"]).encode("utf-8")
            ).hexdigest()
            if row["entry_hash"] != expected:
                return False, f"entry hash mismatch at sequence {row['sequence']}"
            previous_hash = expected
        return True, None

    def has_verified_target(self, board_serial: str, probe_serial: str) -> bool:
        """Return whether immutable history proves this physical pairing."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM operations ORDER BY sequence DESC LIMIT 2000"
            ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            result = payload.get("result", {})
            target = result.get("target_identity", {})
            probe = result.get("probe_identity", {})
            if (
                target.get("board_serial") == board_serial
                and probe.get("serial") == probe_serial
                and target.get("cpuid")
                and target.get("dpidr")
            ):
                return True
        return False


def sha256_file(path: Path, *, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()
