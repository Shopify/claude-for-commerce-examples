// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useRouter } from "next/navigation";
import { formatMoney } from "web-shared";
import type { ComparisonPayload, Product } from "@/lib/types";
import { AddButton, ProductImage, Rating } from "../ProductTile";

export default function ComparisonCard({
  payload,
  onAdd,
  partial,
}: {
  payload: ComparisonPayload;
  onAdd?: (product: Product) => boolean | void | Promise<boolean | void>;
  partial?: boolean;
}) {
  const router = useRouter();
  const entries = payload.entries ?? [];
  return (
    <section className="rounded-2xl border border-(--line) bg-(--card) p-3 shadow-sm">
      {payload.title ? <h3 className="mb-3 text-[15px] font-semibold text-(--ink)">{payload.title}</h3> : null}
      <div className="panel-scroll flex gap-3 overflow-x-auto pb-1">
        {entries.map((entry) => {
          const recommended = entry.product_id === payload.recommended_product_id;
          return (
            <div
              key={entry.product_id}
              role="button"
              onClick={() => router.push(`/products/${encodeURIComponent(entry.product_id)}`)}
              className={`ac-reveal w-56 shrink-0 cursor-pointer rounded-xl border bg-(--card) p-2.5 transition hover:shadow-md ${
                recommended ? "border-(--brand)" : "border-(--line)"
              }`}
            >
              {recommended ? (
                <div className="mb-1.5 inline-block rounded-full bg-(--accent-soft) px-2 py-0.5 text-[11px] font-semibold text-(--ink)">
                  Recommended
                </div>
              ) : null}
              <div className="relative">
                <ProductImage product={entry.product} className="h-28 w-full rounded-lg" />
                {onAdd && entry.product.in_stock !== false ? (
                  <AddButton product={entry.product} onAdd={onAdd} />
                ) : null}
              </div>
              <div className="mt-1.5 line-clamp-2 text-[13px] font-medium leading-snug text-(--ink)">
                {entry.product.title}
              </div>
              <div className="mt-0.5 flex items-center justify-between gap-1">
                <span className="text-sm font-semibold">
                  {formatMoney(entry.product.price, entry.product.currency)}
                </span>
                <Rating rating={entry.product.rating} />
              </div>
              {entry.best_for ? (
                <p className="mt-1 text-[11px] font-medium text-(--ink-soft)">Best for: {entry.best_for}</p>
              ) : null}
              {entry.pros?.length ? (
                <ul className="mt-1.5 space-y-0.5 text-[11px] text-(--ink)">
                  {entry.pros.map((pro) => (
                    <li key={pro}>+ {pro}</li>
                  ))}
                </ul>
              ) : null}
              {entry.cons?.length ? (
                <ul className="mt-1 space-y-0.5 text-[11px] text-(--ink-soft)">
                  {entry.cons.map((con) => (
                    <li key={con}>− {con}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          );
        })}
        {partial ? <div className="ac-skeleton h-[220px] w-56 shrink-0 rounded-xl" /> : null}
      </div>
    </section>
  );
}
