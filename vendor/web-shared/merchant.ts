// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useCallback, useRef } from "react";
import type { AgentApi } from "./api";
import type { AgentEvent, ChatItem } from "./protocol";
import { type AgentTurn, useAgentTurn } from "./turn";

export interface ChangeRef {
  change_id: string;
  status: string;
}

export type ChangeAction = "apply" | "discard";

export interface MerchantChat<TChange extends ChangeRef> extends AgentTurn {
  /** Goes through the same gate as the agent's own apply/discard. */
  actOnChange: (changeId: string, action: ChangeAction) => Promise<TChange | null>;
}

/** Chips written for the staged state go stale once the change moves on. */
function applyChangeUpdate(items: ChatItem[], change: ChangeRef): ChatItem[] {
  return items.map((item) => {
    if (item.kind !== "assistant" || !item.changeIds?.includes(change.change_id)) return item;
    return {
      ...item,
      suggestionsStale: item.suggestionsStale || change.status !== "staged",
      segments: item.segments.map((segment) =>
        segment.type === "ui" &&
        segment.block.component === "change_preview" &&
        (segment.block.payload as { change_id?: string }).change_id === change.change_id
          ? {
              ...segment,
              block: { ...segment.block, payload: { ...(segment.block.payload as object), change } },
            }
          : segment,
      ),
    };
  });
}

/** A change moving mid-turn refreshes the portal's widgets once the turn settles. */
export function useMerchantChat<TChange extends ChangeRef>(
  api: AgentApi,
  options: {
    sessionId: string | null;
    unreachable: string;
    onPortalRefresh: () => void;
  },
): MerchantChat<TChange> {
  const { onPortalRefresh } = options;
  const dirtyRef = useRef(false);
  const setItemsRef = useRef<AgentTurn["setItems"] | null>(null);

  const onEvent = useCallback((event: AgentEvent, turn: number) => {
    if (event.type === "ui" && event.data.component === "change_preview") {
      const changeId = (event.data.payload as { change_id?: string } | undefined)?.change_id;
      if (!changeId) return;
      setItemsRef.current?.((items) =>
        items.map((item) =>
          item.kind === "assistant" && item.turn === turn && !item.changeIds?.includes(changeId)
            ? { ...item, changeIds: [...(item.changeIds ?? []), changeId] }
            : item,
        ),
      );
    } else if (event.type === "change_update") {
      dirtyRef.current = true;
      const change = event.data.change as ChangeRef | undefined;
      if (change?.change_id) setItemsRef.current?.((items) => applyChangeUpdate(items, change));
    }
  }, []);

  const onTurnEnd = useCallback(() => {
    if (!dirtyRef.current) return;
    dirtyRef.current = false;
    onPortalRefresh();
  }, [onPortalRefresh]);

  const turn = useAgentTurn(api, { ...options, onEvent, onTurnEnd });
  setItemsRef.current = turn.setItems;

  const actOnChange = useCallback(
    async (changeId: string, action: ChangeAction) => {
      const data = await api.post<{ change: TChange | null }>(
        `/changes/${encodeURIComponent(changeId)}/${action}`,
      );
      const change = data?.change ?? null;
      if (change) {
        onPortalRefresh();
        turn.setItems((items) => applyChangeUpdate(items, change));
      }
      return change;
    },
    [api, onPortalRefresh, turn.setItems],
  );

  return { ...turn, actOnChange };
}
