// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { formatMoney } from "web-shared";
import type { CheckoutPayload } from "@/lib/types";

/** The CTA links to Shopify's hosted checkout; the card itself charges nothing.
 * The link is the page's cart state — /api/cart stages the checkout host-side. */
export default function CheckoutCard({
  payload,
  checkoutUrl,
}: {
  payload: CheckoutPayload;
  checkoutUrl?: string | null;
}) {
  const cart = payload.cart;
  return (
    <section data-checkout-card className="rounded-2xl border-2 border-(--brand) bg-(--card) p-4 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[15px] font-semibold text-(--ink)">Ready to check out</h3>
        <span className="whitespace-nowrap rounded-full border border-(--line) bg-(--well)/60 px-2.5 py-0.5 text-[11px] font-semibold text-(--ink-soft)">
          Not charged here
        </span>
      </div>
      {payload.note ? <p className="mt-1 text-[13px] text-(--ink-soft)">{payload.note}</p> : null}
      <div className="mt-3 space-y-2 rounded-lg bg-(--well)/60 p-3 text-sm">
        {cart.items.map((item) => (
          <div key={item.product_id} className="flex justify-between gap-2">
            <span className="line-clamp-1 text-(--ink)" title={item.title}>
              {item.title} × {item.quantity}
            </span>
            <span className="shrink-0 text-(--ink)">{formatMoney(item.line_total, cart.currency)}</span>
          </div>
        ))}
        <div className="flex justify-between border-t border-(--line) pt-1.5 text-base font-bold text-(--ink)">
          <span>Subtotal</span>
          <span>{formatMoney(cart.subtotal, cart.currency)}</span>
        </div>
        <p className="text-[11px] leading-snug text-(--ink-soft)">
          Shipping and tax are calculated on the shop&apos;s checkout page.
        </p>
      </div>
      {checkoutUrl ? (
        <a href={checkoutUrl} target="_blank" rel="noreferrer" className="btn-primary mt-3 block w-full text-center">
          Check out on Shopify
        </a>
      ) : (
        <button disabled aria-disabled className="btn-primary mt-3 w-full cursor-not-allowed opacity-90">
          Checkout link not ready yet
        </button>
      )}
      <p className="mt-2 text-center text-[11px] text-(--ink-soft)/80">
        Payment happens on the shop&apos;s own checkout page.
      </p>
    </section>
  );
}
