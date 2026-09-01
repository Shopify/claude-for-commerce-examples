# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

import json

import pytest

from commerce_common.memory import InMemoryMemoryStore, JsonFileMemoryStore
from demo_common import MemorySeeder

SEED = {
    "demo-user": [
        {
            "key": "size",
            "value": "wears a medium",
            "category": "preference",
            "updated_at": "2026-06-01T00:00:00+00:00",
        },
        {
            "key": "budget",
            "value": "keeps gifts under $60",
            "category": "constraint",
            "updated_at": "2026-06-01T00:00:00+00:00",
        },
    ]
}


@pytest.fixture
def seed_file(tmp_path):
    path = tmp_path / "memory-seed.json"
    path.write_text(json.dumps(SEED))
    return path


async def _keys(store, user_id: str = "demo-user") -> set[str]:
    return {fact.key for fact in await store.get_facts(user_id)}


async def test_without_a_marker_every_boot_reloads_the_seed(seed_file):
    store = InMemoryMemoryStore()
    seeder = MemorySeeder(seed_file)
    await seeder.seed_at_boot(store)
    assert await store.delete_fact("demo-user", "size")
    await seeder.seed_at_boot(store)
    assert await _keys(store) == {"size", "budget"}


async def test_with_a_marker_a_user_is_seeded_once_and_deletions_survive_reboots(
    seed_file, tmp_path
):
    store = JsonFileMemoryStore(tmp_path / "store.json")
    seeder = MemorySeeder(seed_file, marker=tmp_path / "seeded.json")
    await seeder.seed_at_boot(store)
    assert await store.delete_fact("demo-user", "size")
    await seeder.seed_at_boot(store)
    assert await _keys(store) == {"budget"}
    await store.clear("demo-user")
    await seeder.seed_at_boot(store)
    assert await _keys(store) == set()
    await seeder.reseed(store, "demo-user")
    assert await _keys(store) == {"size", "budget"}


async def test_reseed_ignores_users_the_seed_does_not_know_and_a_missing_file(tmp_path, seed_file):
    store = InMemoryMemoryStore()
    await MemorySeeder(seed_file).reseed(store, "someone-else")
    await MemorySeeder(tmp_path / "absent.json").seed_at_boot(store)
    assert await _keys(store, "someone-else") == set() and await _keys(store) == set()
