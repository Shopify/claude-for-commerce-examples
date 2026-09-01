// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import type { ReactNode } from "react";
import { Composer } from "./Composer";
import { useStickToBottom } from "./scroll";
import { LatestPill, Transcript, type TranscriptProps } from "./Transcript";
import type { AgentTurn } from "./turn";

export interface ChatCopy {
  /** aria-label of the message box. */
  label: string;
  placeholder: string;
  footnote?: string;
}

/** The storefronts' conversation column. */
export function Chat({
  chat,
  hero,
  copy,
  renderBlock,
  renderPending,
}: {
  chat: AgentTurn;
  hero: ReactNode;
  copy: ChatCopy;
  renderBlock: TranscriptProps["renderBlock"];
  renderPending?: TranscriptProps["renderPending"];
}) {
  const { scrollRef, onScroll, showLatest, jumpToLatest } = useStickToBottom(chat.items, chat.busy);
  return (
    <div className="flex h-full min-w-0 flex-1 flex-col">
      <div className="relative min-h-0 flex-1">
        <div ref={scrollRef} onScroll={onScroll} className="panel-scroll h-full overflow-y-auto px-4 py-6 sm:px-8">
          <div className="mx-auto flex max-w-2xl flex-col gap-6">
            {chat.items.length === 0 ? (
              hero
            ) : (
              <Transcript
                items={chat.items}
                busy={chat.busy}
                send={chat.send}
                renderBlock={renderBlock}
                renderPending={renderPending}
              />
            )}
          </div>
        </div>
        {showLatest ? <LatestPill onClick={jumpToLatest} /> : null}
      </div>
      <div className="border-t border-(--line) bg-(--card) px-4 py-3 sm:px-8">
        <Composer
          send={chat.send}
          ready={chat.ready}
          busy={chat.busy}
          label={copy.label}
          placeholder={copy.placeholder}
          className="mx-auto max-w-2xl"
        />
        {copy.footnote ? (
          <p className="mx-auto mt-1.5 max-w-2xl text-center text-[11px] text-(--ink-soft)/80">{copy.footnote}</p>
        ) : null}
      </div>
    </div>
  );
}
