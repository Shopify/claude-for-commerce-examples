// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useRouter } from "next/navigation";
import type { Product, ProductsPayload } from "@/lib/types";
import ProductTile from "../ProductTile";

export default function ProductStrip({
  payload,
  onAdd,
  partial,
}: {
  payload: ProductsPayload;
  onAdd?: (product: Product) => boolean | void | Promise<boolean | void>;
  partial?: boolean;
}) {
  const router = useRouter();
  const items = payload.items ?? [];
  const open = (product: Product) => router.push(`/products/${encodeURIComponent(product.product_id)}`);
  const list = payload.layout === "list";
  return (
    <section className="rounded-2xl border border-(--line) bg-(--card) p-3 shadow-sm">
      {payload.title ? <h3 className="mb-3 text-[15px] font-semibold text-(--ink)">{payload.title}</h3> : null}
      <div className={list ? "flex flex-col gap-3" : "panel-scroll flex gap-3 overflow-x-auto pb-1"}>
        {items.map(({ product, reason }) => (
          <div key={product.product_id} className={`ac-reveal ${list ? "" : "w-40 shrink-0"}`}>
            <ProductTile product={product} compact={!list} onAdd={onAdd} onOpen={open} />
            {reason ? <p className="mt-1 text-[11px] leading-snug text-(--ink-soft)">{reason}</p> : null}
          </div>
        ))}
        {partial ? <div className="ac-skeleton h-[180px] w-40 shrink-0 rounded-xl" /> : null}
      </div>
    </section>
  );
}
