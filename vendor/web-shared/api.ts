// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

import type { AgentEvent, MemoryFact } from "./protocol";

const SESSION_HEADER = "X-Session-Id";


/**
 * The session token travels only in the session header. Reads return null on any failure so
 * callers keep their last good state.
 */
export class AgentApi {
  session: string | null = null;

  constructor(readonly base: string) {}

  headers(json = false): Record<string, string> {
    const headers: Record<string, string> = {};
    if (this.session) headers[SESSION_HEADER] = this.session;
    if (json) headers["Content-Type"] = "application/json";
    return headers;
  }

  async get<T>(path: string, params?: Record<string, string>): Promise<T | null> {
    const query = params && Object.keys(params).length ? `?${new URLSearchParams(params)}` : "";
    return this.request<T>(`${path}${query}`, { headers: this.headers() });
  }

  async post<T>(path: string, body?: unknown): Promise<T | null> {
    return this.request<T>(path, {
      method: "POST",
      headers: this.headers(body !== undefined),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  }

  private async request<T>(path: string, init: RequestInit): Promise<T | null> {
    try {
      const response = await fetch(`${this.base}${path}`, init);
      if (!response.ok) return null;
      return (await response.json()) as T;
    } catch {
      return null;
    }
  }

  /** Storefronts pass the demo profile as `{ user_id }`. */
  async startSession(body?: Record<string, unknown>): Promise<string | null> {
    const data = await this.post<{ session_id: string }>("/session", body);
    return data?.session_id ?? null;
  }

  async fetchMemory(): Promise<MemoryFact[] | null> {
    const data = await this.get<{ facts?: MemoryFact[] }>("/memory");
    return data ? (data.facts ?? []) : null;
  }

  /** Throws when the request itself fails. */
  async *chatStream(message: string): AsyncGenerator<AgentEvent> {
    const response = await fetch(`${this.base}/chat`, {
      method: "POST",
      headers: this.headers(true),
      body: JSON.stringify({ message }),
    });
    if (!response.ok || !response.body) throw new Error(`chat request failed: ${response.status}`);
    yield* readEventStream(response.body);
  }
}

async function* readEventStream(body: ReadableStream<Uint8Array>): AsyncGenerator<AgentEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventType: string | null = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline: number;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline).trimEnd();
      buffer = buffer.slice(newline + 1);
      if (line.startsWith("event: ")) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith("data: ") && eventType) {
        try {
          yield { type: eventType, data: JSON.parse(line.slice(6)) } as AgentEvent;
        } catch {
          // A malformed frame is dropped; the stream continues.
        }
      } else if (line === "") {
        eventType = null;
      }
    }
  }
}
