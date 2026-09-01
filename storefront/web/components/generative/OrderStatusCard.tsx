// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { formatMoney } from "web-shared";
import type { OrderStatusPayload } from "@/lib/types";

export default function OrderStatusCard({ payload }: { payload: OrderStatusPayload }) {
  const order = payload.order;
  return (
    <section className="rounded-2xl border border-(--line) bg-(--card) p-4 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[15px] font-semibold text-(--ink)">Order {payload.order_id}</h3>
        {order?.status ? (
          <span className="rounded-full bg-(--accent-soft) px-2.5 py-0.5 text-[11px] font-semibold capitalize text-(--ink)">
            {order.status.replaceAll("_", " ")}
          </span>
        ) : null}
      </div>
      <p className="mt-1 text-[13px] leading-relaxed text-(--ink)">{payload.summary}</p>
      {order?.items?.length ? (
        <div className="mt-3 space-y-1.5 rounded-lg bg-(--well)/60 p-3 text-[13px]">
          {order.items.map((item) => (
            <div key={item.product_id} className="flex justify-between gap-2">
              <span className="line-clamp-1 text-(--ink)">
                {item.title} × {item.quantity}
              </span>
              <span className="shrink-0 text-(--ink)">{formatMoney(item.price * item.quantity, order.currency)}</span>
            </div>
          ))}
          <div className="flex justify-between border-t border-(--line) pt-1.5 font-semibold text-(--ink)">
            <span>Total</span>
            <span>{formatMoney(order.total, order.currency)}</span>
          </div>
        </div>
      ) : null}
      {order?.estimated_delivery ? (
        <p className="mt-2 text-[13px] text-(--ink-soft)">Estimated delivery: {order.estimated_delivery}</p>
      ) : null}
      {payload.next_step ? <p className="mt-2 text-[13px] font-medium text-(--ink)">{payload.next_step}</p> : null}
    </section>
  );
}
