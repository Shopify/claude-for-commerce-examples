// Copyright 2026 Anthropic PBC
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useEffect } from "react";
import { ActivityButton } from "./ActivityButton";
import { Composer, type Prefill } from "./Composer";
import { useStickToBottom } from "./scroll";
import { LatestPill, Transcript, type TranscriptProps } from "./Transcript";
import type { AgentTurn } from "./turn";

/** Under host approval only a card's own buttons can act, so approve/dismiss chips never render. */
const ACTION_CHIP = /\b(approve|apply|dismiss|discard)\b/i;
const isPlainChip = (text: string) => !ACTION_CHIP.test(text);

export interface AssistantPanelCopy {
  title: string;
  intro: string;
  starters: string[];
  /** aria-label of the message box. */
  label: string;
  placeholder: string;
}

/** The merchant portals' assistant rail. */
export function AssistantPanel({
  chat,
  copy,
  renderBlock,
  prefill,
  newMemoryCount,
  onOpenActivity,
  onClose,
  fullscreen = false,
  onToggleFullscreen,
}: {
  chat: AgentTurn;
  copy: AssistantPanelCopy;
  renderBlock: TranscriptProps["renderBlock"];
  prefill?: Prefill | null;
  newMemoryCount: number;
  onOpenActivity: () => void;
  onClose: () => void;
  fullscreen?: boolean;
  onToggleFullscreen?: () => void;
}) {
  const { scrollRef, onScroll, showLatest, jumpToLatest } = useStickToBottom(chat.items, chat.busy, {
    onlyWhileBusy: true,
  });

  // Scrolls a settled reply to its first card. Keyed on turnCount as well as busy: a seeded
  // transcript hydrates in one render batch, so busy alone never observes the settle.
  useEffect(() => {
    if (chat.busy) return;
    const container = scrollRef.current;
    const replies = container?.querySelectorAll<HTMLElement>("[data-turn]");
    const reply = replies?.[replies.length - 1];
    if (!container || !reply) return;
    const target = reply.querySelector<HTMLElement>("[data-component]") ?? reply;
    const top = target.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop;
    container.scrollTo({ top: Math.max(0, top - 8), behavior: "smooth" });
  }, [chat.busy, chat.turnCount, scrollRef]);

  const column = fullscreen ? "mx-auto w-full max-w-3xl" : "";

  return (
    <div className="flex h-full w-full flex-col border-l border-(--line) bg-(--card)">
      <div className="flex items-center justify-between border-b border-(--line) px-4 py-2.5">
        <div className="min-w-0">
          <div className="truncate text-sm font-bold text-(--ink)">{copy.title}</div>
          <div className="truncate text-[11px] text-(--ink-soft)">Changes are previewed before they apply</div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <ActivityButton streaming={chat.streaming} newMemoryCount={newMemoryCount} onClick={onOpenActivity} />
          {onToggleFullscreen ? (
            <button
              type="button"
              onClick={onToggleFullscreen}
              aria-label={fullscreen ? "Exit full screen" : "Expand assistant to full screen"}
              className="hidden rounded-md px-2 py-1 text-[15px] leading-none text-(--ink-soft) hover:text-(--ink) lg:block"
            >
              {fullscreen ? "⤡" : "⤢"}
            </button>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close assistant panel"
            className="rounded-md px-2 py-0.5 text-lg leading-none text-(--ink-soft) hover:text-(--ink)"
          >
            ×
          </button>
        </div>
      </div>

      <div className="relative min-h-0 flex-1">
        <div ref={scrollRef} onScroll={onScroll} className="panel-scroll h-full overflow-y-auto px-4 py-4">
          <div className={`flex flex-col gap-4 ${column}`}>
            {chat.items.length === 0 ? (
              <div className="mt-6">
                <p className="text-[16px] leading-relaxed text-(--ink)">{copy.intro}</p>
                <div className="mt-4 flex flex-col gap-2">
                  {copy.starters.map((starter) => (
                    <button
                      key={starter}
                      type="button"
                      onClick={() => void chat.send(starter)}
                      disabled={chat.busy || !chat.ready}
                      className="rounded-(--radius) border border-(--line) px-3 py-2 text-left text-[13px] text-(--ink) transition hover:border-(--accent) hover:bg-(--accent-soft) disabled:opacity-50"
                    >
                      {starter}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <Transcript
                items={chat.items}
                busy={chat.busy}
                send={chat.send}
                renderBlock={renderBlock}
                suggestionFilter={isPlainChip}
                gap="gap-2"
              />
            )}
          </div>
        </div>
        {showLatest ? <LatestPill onClick={jumpToLatest} /> : null}
      </div>

      <div className="border-t border-(--line) px-3 py-2.5">
        <Composer
          send={chat.send}
          ready={chat.ready}
          busy={chat.busy}
          label={copy.label}
          placeholder={copy.placeholder}
          prefill={prefill}
          className={column}
        />
      </div>
    </div>
  );
}
