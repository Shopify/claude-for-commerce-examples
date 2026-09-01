# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

import json

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from starlette.background import BackgroundTask

from demo_common import (
    SESSION_HEADER,
    SessionConflictError,
    SessionStore,
    UnknownSessionError,
    session_dependency,
)
from merchant_agent import AnalysisResult, Listing, MerchantSessionState
from shopping_agent import Product, ShoppingSessionState


def test_start_binds_the_principal_and_mints_a_distinct_token_each_time():
    store = SessionStore(ShoppingSessionState)
    first, second = store.start("demo-user"), store.start("demo-user")
    other = store.start("demo-user-2")
    assert first.session_id != second.session_id and len(first.session_id) >= 24
    assert store.require(first.session_id) == first
    assert store.sessions_for_user("demo-user") == [first, second]
    assert store.sessions_for_user("demo-user-2") == [other]
    store.reset(first)
    with pytest.raises(UnknownSessionError):
        store.require(first.session_id)
    store.save(first)  # a reset record writes nothing back
    assert store.sessions_for_user("demo-user") == [second]


def test_save_writes_the_state_only_when_it_changed_and_appends_the_new_messages():
    store = SessionStore(ShoppingSessionState)
    record = store.start("demo-user")
    record.messages.append({"role": "user", "content": "a kettle"})
    store.save(record)
    assert record.version == 1  # the transcript grew; the state document did not change
    record.state.remember_products([Product(product_id="P-1", title="Kettle", price=39.0)])
    record.messages.append({"role": "assistant", "content": "Here is one."})
    record.pending_app_events.append("Customer tapped add on Kettle (P-1).")
    store.save(record)
    assert record.version == 2
    json.dumps(store.read_state(record.session_id))
    loaded = store.require(record.session_id)
    assert loaded == record and len(loaded.messages) == 2
    loaded.messages.append({"role": "user", "content": "add it"})
    assert len(store.require(record.session_id).messages) == 2  # until saved


def test_a_turn_that_rewrote_earlier_messages_replaces_the_transcript():
    store = SessionStore(ShoppingSessionState)
    record = store.start("demo-user")
    record.messages += [
        {"role": "user", "content": "x" * 50},
        {"role": "assistant", "content": "y"},
    ]
    store.save(record)
    record.messages[0]["content"] = "[cleared]"
    record.messages.append({"role": "user", "content": "next"})
    store.save(record)
    assert store.require(record.session_id).messages[0]["content"] == "x" * 50  # appended only
    record.stored_messages = 0
    store.save(record)
    contents = [m["content"] for m in store.require(record.session_id).messages]
    assert contents == ["[cleared]", "y", "next"]


def test_the_second_writer_of_one_version_is_refused_and_writes_nothing():
    store = SessionStore(MerchantSessionState)
    started = store.start("acme")
    button, turn = store.require(started.session_id), store.require(started.session_id)
    button.pending_app_events.append("Operator approved change chg-1 from the preview card.")
    store.save(button)
    turn.state.remember_listing_record(Listing(listing_id="L-1", title="Kettle", price=39.0))
    turn.messages.append({"role": "user", "content": "raise it"})
    with pytest.raises(SessionConflictError):
        store.save(turn)
    stored = store.require(started.session_id)
    assert stored.state.read_listings == set() and stored.messages == []


def test_merchant_state_round_trips_with_its_sets_and_counters():
    store = SessionStore(MerchantSessionState)
    record = store.start("acme")
    record.state.remember_listing_record(Listing(listing_id="L-1", title="Kettle", price=39.0))
    record.state.remember_analysis(AnalysisResult(question="why flat?", headline="Flat week."))
    store.save(record)
    loaded = store.require(record.session_id)
    assert loaded.state.read_listings == {"L-1"} and loaded.state.analyses_run == 1
    assert (
        loaded.state.remember_analysis(AnalysisResult(question="and now?", headline="Up."))
        == "AN-2"
    )


def test_dependency_loads_writes_back_before_responding_and_reports_a_lost_race():
    store = SessionStore(ShoppingSessionState)
    current_session = session_dependency(store, "/api/session")
    app = FastAPI()

    @app.post("/note")
    def note(record: current_session) -> dict:
        record.pending_app_events.append("tapped")
        return {"user": record.user_id}

    @app.post("/chat")
    def chat(record: current_session) -> StreamingResponse:
        def turn():
            record.messages.append({"role": "assistant", "content": "streamed"})
            yield b"data: ...\n\n"

        return StreamingResponse(turn(), background=BackgroundTask(store.save, record))

    @app.post("/racing-note")
    def racing_note(record: current_session) -> dict:
        rival = store.require(record.session_id)
        rival.pending_app_events.append("first")
        store.save(rival)
        record.pending_app_events.append("second")
        return {}

    client = TestClient(app)
    missing = client.post("/note")
    assert missing.status_code == 401 and "/api/session" in missing.json()["detail"]
    assert client.post("/note", headers={SESSION_HEADER: "made-up"}).status_code == 401
    headers = {SESSION_HEADER: store.start("demo-user-2").session_id}
    assert client.post("/note", headers=headers).json() == {"user": "demo-user-2"}
    assert store.require(headers[SESSION_HEADER]).pending_app_events == ["tapped"]
    assert client.post("/chat", headers=headers).status_code == 200
    assert store.require(headers[SESSION_HEADER]).messages == [
        {"role": "assistant", "content": "streamed"}
    ]
    assert client.post("/racing-note", headers=headers).status_code == 409
    assert store.require(headers[SESSION_HEADER]).pending_app_events == ["tapped", "first"]
