// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useRouter } from "next/navigation";
import type { PlanPayload, Product } from "@/lib/types";
import ProductTile from "../ProductTile";

export default function PlanCard({
  payload,
  onAdd,
  partial,
}: {
  payload: PlanPayload;
  onAdd?: (product: Product) => boolean | void | Promise<boolean | void>;
  partial?: boolean;
}) {
  const router = useRouter();
  const open = (product: Product) => router.push(`/products/${encodeURIComponent(product.product_id)}`);
  return (
    <section className="rounded-2xl border border-(--line) bg-(--card) p-3 shadow-sm">
      <h3 className="text-[15px] font-semibold text-(--ink)">{payload.title}</h3>
      {payload.intro ? <p className="mt-1 text-[13px] text-(--ink-soft)">{payload.intro}</p> : null}
      <ol className="mt-3 space-y-3">
        {(payload.steps ?? []).map((step, index) => (
          <li key={step.label} className="ac-reveal">
            <div className="flex items-baseline gap-2">
              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-(--accent-soft) text-[11px] font-bold text-(--ink)">
                {index + 1}
              </span>
              <div>
                <div className="text-[13px] font-semibold text-(--ink)">{step.label}</div>
                {step.detail ? <p className="text-[13px] text-(--ink-soft)">{step.detail}</p> : null}
              </div>
            </div>
            {step.products?.length ? (
              <div className="panel-scroll mt-2 flex gap-3 overflow-x-auto pb-1 pl-7">
                {step.products.map((product) => (
                  <div key={product.product_id} className="w-40 shrink-0">
                    <ProductTile product={product} compact onAdd={onAdd} onOpen={open} />
                  </div>
                ))}
              </div>
            ) : null}
          </li>
        ))}
      </ol>
      {partial ? <div className="ac-skeleton mt-3 h-10 rounded-xl" /> : null}
    </section>
  );
}
