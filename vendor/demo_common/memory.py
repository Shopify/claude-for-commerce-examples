# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Memory as the demo APIs expose it: the fixture seeder and the read/retract routes both
roles mount. Facts are keyed by the principal the session id resolves to, and a fact
key travels in the request body so it never appears in a URL or an access log."""

# Route parameters below are annotated with dependencies built at call time, so this
# module evaluates its annotations eagerly (no ``from __future__ import annotations``).

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

from commerce_common.memory import MemoryStore
from commerce_common.types import MemoryFact


class MemoryFactRef(BaseModel):
    key: str = Field(min_length=1, max_length=64)


class MemoryFactEdit(MemoryFactRef):
    value: str = Field(min_length=1, max_length=200)


class MemorySeeder:
    """Loads ``data/memory-seed.json`` (``{user_id: [facts]}``) into a store.

    Without a marker file every boot reloads the seed, which suits an in-memory store.
    With one, each user is seeded once and the marker remembers who; the store is then
    the only source of truth across restarts, so a retracted fact stays retracted and a
    purged user stays empty until ``reseed`` is asked for explicitly.
    """

    def __init__(self, seed_file: Path, marker: Path | None = None) -> None:
        self._seed_file = seed_file
        self._marker = marker

    def _seeded_users(self) -> set[str]:
        if self._marker is None or not self._marker.exists():
            return set()
        return set(json.loads(self._marker.read_text(encoding="utf-8") or "[]"))

    def _entries(self) -> dict[str, list[dict[str, Any]]]:
        if not self._seed_file.exists():
            return {}
        return json.loads(self._seed_file.read_text(encoding="utf-8"))

    async def _load(self, store: MemoryStore, user_id: str, entries: list[dict[str, Any]]) -> None:
        # Seed entries carry their own updated_at (check.py enforces it); the stamp here
        # only backstops a hand-written seed so retention has something to compare.
        now = datetime.now(UTC).isoformat()
        facts = [MemoryFact.model_validate({"updated_at": now, **entry}) for entry in entries]
        await store.upsert_facts(user_id, facts)
        if self._marker is not None:
            users = self._seeded_users() | {user_id}
            self._marker.write_text(json.dumps(sorted(users)), encoding="utf-8")

    async def seed_at_boot(self, store: MemoryStore) -> None:
        already = self._seeded_users()
        for user_id, entries in self._entries().items():
            if user_id not in already:
                await self._load(store, user_id, entries)

    async def reseed(self, store: MemoryStore, user_id: str) -> None:
        entries = self._entries().get(user_id)
        if entries is not None:
            await self._load(store, user_id, entries)


def install_memory_routes(
    target: FastAPI | APIRouter,
    path: str,
    *,
    current_session: Any,
    memory_store: MemoryStore,
) -> None:
    """``GET path`` lists the caller's facts and ``DELETE path`` retracts one of them."""

    @target.get(path)
    async def get_memory(record: current_session) -> dict:
        facts = await memory_store.get_facts(record.user_id)
        return {"facts": [fact.model_dump(mode="json") for fact in facts]}

    @target.delete(path)
    async def delete_memory_fact(ref: MemoryFactRef, record: current_session) -> dict:
        if not await memory_store.delete_fact(record.user_id, ref.key):
            raise HTTPException(status_code=404, detail="No such fact")
        return {"ok": True, "deleted": ref.key}
