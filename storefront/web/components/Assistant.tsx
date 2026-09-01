// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { type AgentTurn, AssistantRail, Chat as ChatShell } from "web-shared";
import GenerativeBlock from "./generative";

const COPY = {
  label: "Message the shopping assistant",
  placeholder: "Ask about products, sizes, your cart…",
  footnote: "The assistant searches the live shop, edits the cart, and hands checkout to Shopify.",
};

const STARTERS = [
  "What's in the catalog?",
  "Find me a gift under $50.",
  "Compare your two most popular products.",
  "What's in my cart?",
];

function Hero({ chat }: { chat: AgentTurn }) {
  return (
    <div className="mt-4">
      <div className="rounded-2xl bg-gradient-to-b from-(--accent-soft) via-(--surface) to-transparent px-4 pb-2 pt-8 text-center">
        <div className="text-xl font-semibold text-(--ink)">Shopping assistant</div>
        <p className="mt-1 text-[15px] text-(--ink-soft)">
          Ask about products, compare options, or build a cart.
        </p>
      </div>
      <div className="mt-5 flex flex-col gap-2">
        {STARTERS.map((starter) => (
          <button
            key={starter}
            type="button"
            onClick={() => void chat.send(starter)}
            disabled={chat.busy || !chat.ready}
            className="rounded-(--radius) border border-(--line) bg-(--card) px-3 py-2 text-left text-[13px] text-(--ink) transition hover:border-(--brand) hover:bg-(--accent-soft) disabled:opacity-50"
          >
            {starter}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function Assistant({
  open,
  chat,
  onClose,
  onAdd,
  checkoutUrl,
}: {
  open: boolean;
  chat: AgentTurn;
  onClose: () => void;
  /** Resolves false when the host refuses the add. */
  onAdd: (productId: string) => Promise<boolean>;
  /** Shopify's hosted checkout for the session's cart, for the checkout card's CTA. */
  checkoutUrl?: string | null;
}) {
  return (
    <AssistantRail open={open} storageKey="shopify-storefront-rail-width" onClose={onClose}>
      {(rail) => (
        <div className="flex h-full w-full flex-col border-l border-(--line) bg-(--card)">
          <div className="flex items-center justify-between border-b border-(--line) px-4 py-2.5">
            <div className="text-sm font-bold text-(--ink)">Assistant</div>
            <div className="flex shrink-0 items-center gap-1">
              <button
                type="button"
                onClick={rail.onToggleFullscreen}
                aria-label={rail.fullscreen ? "Exit full screen" : "Expand assistant to full screen"}
                className="hidden rounded-md px-2 py-1 text-[15px] leading-none text-(--ink-soft) hover:text-(--ink) lg:block"
              >
                {rail.fullscreen ? "⤡" : "⤢"}
              </button>
              <button
                type="button"
                onClick={rail.onClose}
                aria-label="Close assistant panel"
                className="rounded-md px-2 py-0.5 text-lg leading-none text-(--ink-soft) hover:text-(--ink)"
              >
                ×
              </button>
            </div>
          </div>
          <ChatShell
            chat={chat}
            copy={COPY}
            hero={<Hero chat={chat} />}
            renderBlock={(segment) => (
              <GenerativeBlock
                block={segment.block}
                status={segment.status}
                onAdd={(product) => onAdd(product.product_id)}
                checkoutUrl={checkoutUrl}
              />
            )}
          />
        </div>
      )}
    </AssistantRail>
  );
}
