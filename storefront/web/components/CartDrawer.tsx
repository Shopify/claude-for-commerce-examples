// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { formatMoney } from "web-shared";
import type { CartItem, CartPayload } from "@/lib/types";
import { ProductImage } from "./ProductTile";

/** The buttons send chat messages, so every cart write stays agent-mediated. */
function QuantityControls({ item, onAction }: { item: CartItem; onAction: (message: string) => void }) {
  return (
    <div className="mt-1.5 flex items-center gap-2">
      <div className="flex items-center rounded-full border border-(--line) bg-(--card)">
        <button
          onClick={() =>
            onAction(
              item.quantity <= 1
                ? `Remove the ${item.title} from my cart.`
                : `Change the ${item.title} quantity to ${item.quantity - 1}.`,
            )
          }
          aria-label={`Decrease ${item.title} quantity`}
          className="px-2.5 py-0.5 text-sm text-(--ink-soft) hover:text-(--ink)"
        >
          −
        </button>
        <span className="min-w-6 text-center text-[13px] font-semibold text-(--ink)">{item.quantity}</span>
        <button
          onClick={() => onAction(`Change the ${item.title} quantity to ${item.quantity + 1}.`)}
          aria-label={`Increase ${item.title} quantity`}
          className="px-2.5 py-0.5 text-sm text-(--ink-soft) hover:text-(--ink)"
        >
          +
        </button>
      </div>
      <button
        onClick={() => onAction(`Remove the ${item.title} from my cart.`)}
        aria-label={`Remove ${item.title}`}
        className="text-[11px] text-(--ink-soft) underline-offset-2 hover:text-(--danger) hover:underline"
      >
        Remove
      </button>
    </div>
  );
}

export default function CartDrawer({
  open,
  cart,
  onClose,
  onAction,
}: {
  open: boolean;
  cart: CartPayload | null;
  onClose: () => void;
  onAction: (message: string) => void;
}) {
  const items = cart?.items ?? [];
  const count = cart?.item_count ?? 0;
  return (
    <div
      className={`fixed inset-0 z-50 ${open ? "" : "pointer-events-none"}`}
      aria-hidden={!open}
      inert={!open}
    >
      <div
        onClick={onClose}
        aria-hidden
        className={`absolute inset-0 bg-(--ink)/30 transition-opacity duration-300 ${open ? "opacity-100" : "opacity-0"}`}
      />
      <aside
        className={`absolute inset-y-0 right-0 flex w-[min(92vw,380px)] flex-col border-l border-(--line) bg-(--card) transition-transform duration-300 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between border-b border-(--line) px-4 py-3">
          <h2 className="text-sm font-bold text-(--ink)">
            Cart · {count} item{count === 1 ? "" : "s"}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close cart"
            className="rounded-md px-2 py-0.5 text-lg leading-none text-(--ink-soft) hover:text-(--ink)"
          >
            ×
          </button>
        </div>

        <div className="panel-scroll flex-1 overflow-y-auto px-4 py-3">
          {items.length === 0 ? (
            <p className="mt-8 text-center text-[15px] text-(--ink-soft)/80">
              Your cart is empty so far.
              <br />
              Ask the assistant to track something down.
            </p>
          ) : (
            <ul className="divide-y divide-(--line)">
              {items.map((item) => (
                <li key={item.product_id} className="ac-reveal flex gap-3 py-3 first:pt-0">
                  <ProductImage
                    product={{ product_id: item.product_id, title: item.title, price: item.price, image_url: item.image_url }}
                    className="h-16 w-16 shrink-0 rounded-lg"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-start justify-between gap-2">
                      <div className="line-clamp-3 min-w-0 text-[13px] font-medium leading-snug text-(--ink)" title={item.title}>
                        {item.title}
                      </div>
                      <div className="shrink-0 text-right">
                        <div className="text-sm font-bold text-(--ink)">
                          {formatMoney(item.line_total, cart?.currency)}
                        </div>
                        {item.quantity > 1 ? (
                          <div className="text-[11px] text-(--ink-soft)">{formatMoney(item.price, cart?.currency)} each</div>
                        ) : null}
                      </div>
                    </div>
                    <QuantityControls item={item} onAction={onAction} />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="border-t border-(--line) px-4 py-3">
          <div className="flex items-center justify-between text-[15px]">
            <span className="text-(--ink-soft)">Subtotal</span>
            <span className="text-base font-bold text-(--ink)">
              {formatMoney(cart?.subtotal ?? 0, cart?.currency)}
            </span>
          </div>
          {cart?.checkout_url && items.length > 0 ? (
            <a
              href={cart.checkout_url}
              target="_blank"
              rel="noreferrer"
              className="btn-primary mt-2.5 block w-full text-center"
            >
              Check out on Shopify
            </a>
          ) : (
            <button
              onClick={() => onAction("Check out my cart.")}
              disabled={items.length === 0}
              className="btn-primary mt-2.5 w-full"
            >
              Check out
            </button>
          )}
          <p className="mt-2 text-center text-[11px] text-(--ink-soft)/80">
            Payment happens on the shop&apos;s own checkout page — nothing is charged here.
          </p>
        </div>
      </aside>
    </div>
  );
}
