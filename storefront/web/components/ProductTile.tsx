// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import { useState } from "react";
import { formatMoney } from "web-shared";
import type { Product } from "@/lib/types";

export function ProductImage({ product, className = "" }: { product: Product; className?: string }) {
  if (product.image_url) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={product.image_url} alt={product.title} className={`object-cover ${className}`} />;
  }
  return (
    <div className={`flex items-center justify-center bg-(--well) text-(--ink-soft) ${className}`} aria-hidden>
      <span className="text-xl">◻</span>
    </div>
  );
}

export function Rating({ rating, count }: { rating?: number | null; count?: number | null }) {
  if (rating == null) return null;
  return (
    <span className="whitespace-nowrap text-[13px] text-(--ink-soft)">
      <span className="text-(--star)">★</span> {rating.toFixed(1)}
      {count ? <span className="text-[11px] text-(--ink-soft)/80"> ({count.toLocaleString()})</span> : null}
    </span>
  );
}

/** An onAdd that resolves `false` means the server refused the write — the host only
 * accepts direct adds for products the assistant has surfaced this session. */
export function AddButton({
  product,
  onAdd,
}: {
  product: Product;
  onAdd: (product: Product) => boolean | void | Promise<boolean | void>;
}) {
  const [phase, setPhase] = useState<"idle" | "busy" | "done" | "error">("idle");
  return (
    <button
      onClick={async (event) => {
        event.stopPropagation();
        event.preventDefault();
        if (phase !== "idle") return;
        setPhase("busy");
        const added = (await onAdd(product)) !== false;
        setPhase(added ? "done" : "error");
        window.setTimeout(() => setPhase("idle"), added ? 1200 : 1600);
      }}
      aria-label={`Add ${product.title} to cart`}
      className={`absolute bottom-2 right-2 flex h-8 w-8 items-center justify-center rounded-full text-lg font-semibold leading-none text-(--brand-contrast) shadow-sm transition-all hover:scale-105 ${
        phase === "done" ? "bg-emerald-600" : phase === "error" ? "bg-amber-600" : "bg-(--brand)"
      } ${phase === "busy" ? "animate-pulse" : ""}`}
    >
      {phase === "done" ? "✓" : phase === "error" ? "!" : "+"}
    </button>
  );
}

export default function ProductTile({
  product,
  compact = false,
  onAdd,
  onOpen,
}: {
  product: Product;
  compact?: boolean;
  onAdd?: (product: Product) => boolean | void | Promise<boolean | void>;
  onOpen?: (product: Product) => void;
}) {
  const clickable = Boolean(onOpen);
  return (
    <div
      onClick={clickable ? () => onOpen?.(product) : undefined}
      role={clickable ? "button" : undefined}
      className={`flex shrink-0 flex-col overflow-hidden rounded-xl border border-(--line) bg-(--card) shadow-sm transition-[box-shadow,border-color] duration-200 hover:shadow-md ${
        compact ? "w-40" : "w-full"
      } ${clickable ? "cursor-pointer hover:border-(--brand)" : ""}`}
    >
      <div className="relative">
        <ProductImage product={product} className={`w-full ${compact ? "h-28" : "aspect-square"}`} />
        {product.in_stock === false ? (
          <span className="absolute right-1.5 top-1.5 rounded-full bg-(--ink)/85 px-2 py-0.5 text-[11px] font-medium text-white">
            Out of stock
          </span>
        ) : null}
        {onAdd && product.in_stock !== false ? <AddButton product={product} onAdd={onAdd} /> : null}
      </div>
      <div className="flex flex-1 flex-col gap-0.5 p-2.5">
        {product.brand ? (
          <div className="text-[11px] uppercase tracking-wide text-(--ink-soft)/80">{product.brand}</div>
        ) : null}
        <div className="line-clamp-2 text-[13px] font-medium leading-snug text-(--ink)" title={product.title}>
          {product.title}
        </div>
        <div className="mt-auto flex items-center justify-between gap-1 pt-1">
          <span className="text-sm font-semibold">{formatMoney(product.price, product.currency)}</span>
          <Rating rating={product.rating} count={compact ? undefined : product.review_count} />
        </div>
      </div>
    </div>
  );
}
