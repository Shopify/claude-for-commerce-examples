// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

/** Event shapes mirror commerce_common/streaming.py. */

export type AgentEventType =
  | "text_delta"
  | "tool_call"
  | "tool_result"
  | "ui"
  | "ui_partial"
  | "cart_update"
  | "change_update"
  | "progress"
  | "turn_complete"
  | "error";

export interface AgentEvent {
  type: AgentEventType;
  data: Record<string, unknown>;
}

export interface MemoryFact {
  key: string;
  value: string;
  category: string;
  updated_at?: string | null;
}

/** Each app narrows `payload` in its registry. */
export interface UIBlock {
  component: string;
  payload: unknown;
}

export interface TraceEntry {
  kind: "tool_call" | "tool_result" | "turn_complete" | "error";
  turn: number;
  label: string;
  detail?: string;
  isError?: boolean;
  /** "blocked" when a gate held the call; `reason` names the gate. */
  status?: string;
  reason?: string;
  /** tool_result only: the head of a long result, sent when `detail` is the "ok" summary. */
  excerpt?: string;
  /** performance.now() at arrival. */
  at: number;
  /** turn_complete only. */
  elapsedMs?: number;
}

/** `retrying`: the attempt failed validation; its last frame stays until a retry adopts it. */
export type UISlotStatus = "pending" | "partial" | "retrying" | "final";

export type AssistantSegment =
  | { type: "text"; text: string }
  | { type: "error"; text: string }
  | {
      type: "ui";
      block: UIBlock;
      slotKey: string;
      status: UISlotStatus;
    };

export type UISegment = Extract<AssistantSegment, { type: "ui" }>;

export interface UserChatItem {
  kind: "user";
  text: string;
}

export interface AssistantChatItem {
  kind: "assistant";
  turn: number;
  segments: AssistantSegment[];
  suggestions: string[];
  /** Merchant portals: changes previewed in this reply. */
  changeIds?: string[];
  suggestionsStale?: boolean;
  pending: boolean;
  tools: string[];
  /**
   * Status line for the call in flight; cleared when prose or a card lands, or when the
   * tool a progress line names returns.
   */
  activity?: string;
}

export type ChatItem = UserChatItem | AssistantChatItem;
