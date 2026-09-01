# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""The session record, its store, and the request dependency that loads and writes it back.

``start`` is the only place a principal enters the store: it binds a shopper profile (or,
on the merchant side, the merchant id) to a fresh unguessable session id. Every later
request carries that id in ``SESSION_HEADER`` and the routes read the principal from the
record, so no request shape names a user. A deployment authenticates the caller before
``start`` and passes the principal it verified; ``session_dependency`` and the routes stay.

A session is stored as two things, so a request on any process finds it and a long session
stays cheap to write: a small state document (principal, provenance state, queued app
events), rewritten under a new version only when it changed, and the transcript, which a
request appends its new messages to. The dependency writes the record back when the
request ends; a streamed turn writes back when its stream ends (``stream_turn`` in
``host.py``); code holding a record outside a request calls ``save`` itself. A write whose
version is behind the store's is refused, so two requests racing on one session cannot
overwrite each other. ``SessionStore`` keeps both parts in memory; a deployment subclasses
it and puts the storage methods at the bottom over its own store.
"""

from __future__ import annotations

import copy
import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Annotated, Any, Generic, TypeVar

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel

SESSION_HEADER = "X-Session-Id"

StateT = TypeVar("StateT", bound=BaseModel)


class UnknownSessionError(LookupError):
    """No live session has this id."""


class SessionConflictError(RuntimeError):
    """Another request wrote this session first; the caller retries from a fresh load."""


@dataclass
class SessionRecord(Generic[StateT]):
    session_id: str
    user_id: str
    state: StateT
    messages: list[dict[str, Any]] = field(default_factory=list)
    # Actions taken outside the conversation (a button, a server-side event) since the
    # agent's last reply; the next chat turn hands them to the model as a note.
    pending_app_events: list[str] = field(default_factory=list)
    # What the store holds, so ``save`` writes only the difference: the state document's
    # version and content as loaded, and how many of ``messages`` are stored. A turn that
    # rewrote earlier messages sets ``stored_messages`` to 0 and the transcript is written whole.
    version: int = 0
    stored_state: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    stored_messages: int = field(default=0, repr=False, compare=False)
    ended: bool = field(default=False, repr=False, compare=False)

    def state_document(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "state": self.state.model_dump(mode="json"),
            "pending_app_events": list(self.pending_app_events),
        }


class SessionStore(Generic[StateT]):
    def __init__(self, state_type: type[StateT]) -> None:
        self._state_type = state_type
        self._states: dict[str, tuple[int, dict[str, Any]]] = {}
        self._transcripts: dict[str, list[dict[str, Any]]] = {}

    def start(self, user_id: str) -> SessionRecord[StateT]:
        record = SessionRecord(
            session_id=secrets.token_urlsafe(24), user_id=user_id, state=self._state_type()
        )
        self.save(record)
        return record

    def require(self, session_id: str) -> SessionRecord[StateT]:
        stored = self.read_state(session_id)
        if stored is None:
            raise UnknownSessionError(session_id)
        version, document = stored
        messages = self.read_messages(session_id)
        return SessionRecord(
            session_id=session_id,
            user_id=document["user_id"],
            state=self._state_type.model_validate(document["state"]),
            messages=messages,
            pending_app_events=list(document["pending_app_events"]),
            version=version,
            stored_state=document,
            stored_messages=len(messages),
        )

    def save(self, record: SessionRecord[StateT]) -> None:
        """The state document first, when it changed, so a request that lost a race writes
        nothing at all; then the messages the store lacks."""
        if record.ended:
            return
        document = record.state_document()
        if document != record.stored_state:
            self.write_state(record.session_id, document, record.version)
            record.version += 1
            record.stored_state = document
        if record.stored_messages < len(record.messages):
            new = record.messages[record.stored_messages :]
            self.write_messages(record.session_id, new, record.stored_messages)
            record.stored_messages = len(record.messages)

    def reset(self, record: SessionRecord[StateT]) -> None:
        record.ended = True
        self.delete(record.session_id)

    def sessions_for_user(self, user_id: str) -> list[SessionRecord[StateT]]:
        return [self.require(session_id) for session_id in self.session_ids_for_user(user_id)]

    # -- Storage: the six methods a deployment puts over its own store.

    def read_state(self, session_id: str) -> tuple[int, dict[str, Any]] | None:
        return self._states.get(session_id)

    def write_state(self, session_id: str, document: dict[str, Any], version: int) -> None:
        """Store ``document`` as ``version + 1`` if the stored version is still ``version``
        (0 while a session is being started): a compare-and-set in a shared store."""
        current = self._states.get(session_id)
        if (current[0] if current else 0) != version:
            raise SessionConflictError(session_id)
        self._states[session_id] = (version + 1, document)

    # Copied both ways, as a real store would: a record's later edits reach the store only
    # through save.
    def read_messages(self, session_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self._transcripts.get(session_id, []))

    def write_messages(self, session_id: str, messages: list[dict[str, Any]], start: int) -> None:
        """Replace the transcript from ``start`` on: an append when ``start`` is its stored
        length, the whole transcript after a turn compacted it."""
        self._transcripts.setdefault(session_id, [])[start:] = copy.deepcopy(messages)

    def delete(self, session_id: str) -> None:
        self._states.pop(session_id, None)
        self._transcripts.pop(session_id, None)

    def session_ids_for_user(self, user_id: str) -> list[str]:
        return [
            session_id
            for session_id, (_, document) in self._states.items()
            if document["user_id"] == user_id
        ]


def session_dependency(store: SessionStore[StateT], start_route: str) -> Any:
    """The parameter annotation every scoped route of one role declares: the header's
    session id, resolved to its record and written back before the response goes out
    (FastAPI's function scope; a streamed turn writes back again when its stream ends).
    ``start_route`` names the login route in the 401 detail; a write that another request
    beat is a 409."""

    def current_session(
        session_id: Annotated[str | None, Header(alias=SESSION_HEADER)] = None,
    ) -> Iterator[SessionRecord[StateT]]:
        if not session_id:
            raise HTTPException(
                status_code=401, detail=f"Start a session first (POST {start_route})"
            )
        try:
            record = store.require(session_id)
        except UnknownSessionError as error:
            raise HTTPException(status_code=401, detail="Unknown session") from error
        yield record
        try:
            store.save(record)
        except SessionConflictError as error:
            raise HTTPException(status_code=409, detail="The session changed; retry") from error

    return Annotated[SessionRecord[StateT], Depends(current_session, scope="function")]
