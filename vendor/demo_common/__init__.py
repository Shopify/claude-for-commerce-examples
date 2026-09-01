# Copyright 2026 Anthropic PBC
# SPDX-License-Identifier: Apache-2.0

"""Host code the four vertical examples share: the app and its middleware, the session
store both roles use, the storefront routes, the merchant router, and (in
``storefront_fixtures`` and ``merchant_fixtures``) the helpers the mock backends call. A
vertical's ``api/`` package constructs its backends, agents, and configs, mounts these,
and adds only its own routes. This module exports what the verticals and their tests
import; the fixture helpers are imported from their own modules."""

from .host import REPO_ROOT, host_approval_default, load_demo_env, spawn_background
from .memory import MemorySeeder
from .merchant import MerchantIdentity, build_merchant_router
from .sessions import (
    SESSION_HEADER,
    SessionConflictError,
    SessionRecord,
    SessionStore,
    UnknownSessionError,
    session_dependency,
)
from .storefront import CartAddRequest, StorefrontHost, build_storefront_host

__all__ = [
    "REPO_ROOT",
    "SESSION_HEADER",
    "CartAddRequest",
    "MemorySeeder",
    "MerchantIdentity",
    "SessionConflictError",
    "SessionRecord",
    "SessionStore",
    "StorefrontHost",
    "UnknownSessionError",
    "build_merchant_router",
    "build_storefront_host",
    "host_approval_default",
    "load_demo_env",
    "session_dependency",
    "spawn_background",
]
