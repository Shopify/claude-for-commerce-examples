// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect, useState } from "react";
import type { AgentApi } from "./api";

export interface Session {
  /** Null while login is in flight or after it failed. */
  sessionId: string | null;
}

const generations = new WeakMap<AgentApi, number>();

/**
 * Only the newest start installs its token. Key the caller on the profile so per-session
 * state resets with it.
 */
export function useSession(
  api: AgentApi,
  options: { profile?: string } = {},
): Session {
  const { profile } = options;
  const [session, setSession] = useState<Session>({ sessionId: null });

  useEffect(() => {
    const generation = (generations.get(api) ?? 0) + 1;
    generations.set(api, generation);
    const current = () => generations.get(api) === generation;
    const install = (sessionId: string | null) => {
      if (!current()) return;
      api.session = sessionId;
      setSession({ sessionId });
    };
    void (async () => {
      const token = await api.startSession(profile ? { user_id: profile } : undefined);
      install(token);
    })();
    return () => {
      generations.set(api, (generations.get(api) ?? 0) + 1);
    };
  }, [api, profile]);

  return session;
}
