"""Exclusive per-probe leases for hardware-mutating operations."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import AsyncIterator


@dataclass(slots=True)
class LeaseInfo:
    lease_id: str
    probe_serial: str
    owner: str
    acquired_at: datetime


class ProbeBusy(RuntimeError):
    pass


class ProbeLeaseManager:
    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._active: dict[str, LeaseInfo] = {}
        self._by_id: dict[str, LeaseInfo] = {}
        self._guard = asyncio.Lock()

    async def _lock_for(self, probe_serial: str) -> asyncio.Lock:
        async with self._guard:
            return self._locks.setdefault(probe_serial, asyncio.Lock())

    @asynccontextmanager
    async def lease(
        self,
        probe_serial: str,
        *,
        owner: str,
        timeout: float = 30.0,
    ) -> AsyncIterator[LeaseInfo]:
        info = await self.acquire(probe_serial, owner=owner, timeout=timeout)
        try:
            yield info
        finally:
            await self.release(info.lease_id)

    async def acquire(
        self,
        probe_serial: str,
        *,
        owner: str,
        timeout: float = 30.0,
    ) -> LeaseInfo:
        """Acquire a lease that may outlive a single context manager call."""

        lock = await self._lock_for(probe_serial)
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
        except TimeoutError as exc:
            active = self._active.get(probe_serial)
            holder = active.owner if active else "unknown"
            raise ProbeBusy(
                f"probe {probe_serial} is leased by {holder}"
            ) from exc
        info = LeaseInfo(
            lease_id=str(uuid.uuid4()),
            probe_serial=probe_serial,
            owner=owner,
            acquired_at=datetime.now(UTC),
        )
        self._active[probe_serial] = info
        self._by_id[info.lease_id] = info
        return info

    async def release(self, lease_id: str) -> None:
        """Release a previously acquired lease; stale releases are harmless."""

        async with self._guard:
            info = self._by_id.pop(lease_id, None)
            if not info:
                return
            self._active.pop(info.probe_serial, None)
            lock = self._locks.get(info.probe_serial)
            if lock and lock.locked():
                lock.release()

    def active_leases(self) -> list[LeaseInfo]:
        return list(self._active.values())
