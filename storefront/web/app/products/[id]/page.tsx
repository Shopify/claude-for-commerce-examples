// Copyright 2026 Shopify Inc.
// SPDX-License-Identifier: Apache-2.0

"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { formatMoney } from "web-shared";
import { fetchProduct } from "@/lib/api";
import type { Product, ProductDetails } from "@/lib/types";
import { ProductImage, Rating } from "@/components/ProductTile";
import { useStore } from "@/components/StoreShell";

function useProductId(): string {
  const params = useParams<{ id: string }>();
  const raw = Array.isArray(params.id) ? params.id[0] : params.id;
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

function AddToCart({ onAdd }: { onAdd: () => Promise<boolean> }) {
  const [phase, setPhase] = useState<"idle" | "busy" | "done" | "error">("idle");
  return (
    <div>
      <button
        type="button"
        disabled={phase === "busy"}
        onClick={async () => {
          if (phase === "busy") return;
          setPhase("busy");
          const added = await onAdd();
          setPhase(added ? "done" : "error");
          window.setTimeout(() => setPhase("idle"), added ? 1500 : 4000);
        }}
        className={`btn-primary w-full sm:w-64 ${phase === "busy" ? "animate-pulse" : ""}`}
      >
        {phase === "done" ? "Added ✓" : phase === "busy" ? "Adding…" : "Add to cart"}
      </button>
      {phase === "error" ? (
        <p className="mt-2 text-[13px] text-(--warn)">
          The host only accepts adds for products the assistant has surfaced this session — ask it about this
          product first, or add it from a chat card.
        </p>
      ) : null}
    </div>
  );
}

export default function ProductPage() {
  const productId = useProductId();
  const { addProduct, askAssistant, openCart } = useStore();
  const [details, setDetails] = useState<ProductDetails | null | "missing">(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setDetails(null);
    setSelectedId(null);
    void fetchProduct(productId).then((value) => {
      if (!cancelled) setDetails(value ?? "missing");
    });
    return () => {
      cancelled = true;
    };
  }, [productId]);

  if (details === "missing") {
    return (
      <div className="mx-auto mt-16 max-w-md px-5 text-center">
        <h1 className="text-lg font-semibold text-(--ink)">Product not found</h1>
        <p className="mt-2 text-[15px] text-(--ink-soft)">
          This product isn&apos;t in the session&apos;s catalog yet. The assistant can look it up in the live shop.
        </p>
        <button type="button" onClick={() => askAssistant()} className="btn-primary mt-4">
          Ask the assistant
        </button>
        <div className="mt-3">
          <Link href="/" className="text-[13px] text-(--ink-soft) underline underline-offset-2">
            Back to the grid
          </Link>
        </div>
      </div>
    );
  }

  if (details === null) {
    return (
      <div className="mx-auto max-w-5xl px-5 py-8">
        <div className="grid gap-8 md:grid-cols-2">
          <div className="ac-skeleton aspect-square rounded-2xl" />
          <div className="space-y-3">
            <div className="ac-skeleton h-8 w-3/4 rounded-lg" />
            <div className="ac-skeleton h-5 w-1/3 rounded-lg" />
            <div className="ac-skeleton h-24 rounded-lg" />
          </div>
        </div>
      </div>
    );
  }

  const variants = details.variants ?? [];
  const selected: Product = variants.find((variant) => variant.product_id === selectedId) ?? details;
  const canAdd = selected.in_stock !== false && (variants.length === 0 || selectedId !== null);

  return (
    <div className="mx-auto max-w-5xl px-5 py-6">
      <Link href="/" className="text-[13px] text-(--ink-soft) underline-offset-2 hover:underline">
        ← Back to the grid
      </Link>
      <div className="mt-4 grid gap-8 md:grid-cols-2">
        <ProductImage product={selected.image_url ? selected : details} className="aspect-square w-full rounded-2xl" />
        <div>
          {details.brand ? (
            <div className="text-[13px] uppercase tracking-wide text-(--ink-soft)">{details.brand}</div>
          ) : null}
          <h1 className="mt-1 text-2xl font-bold leading-tight text-(--ink)">{details.title}</h1>
          <div className="mt-2 flex items-center gap-3">
            <span className="text-xl font-bold text-(--ink)">{formatMoney(selected.price, selected.currency)}</span>
            <Rating rating={details.rating} count={details.review_count} />
            {selected.in_stock === false ? (
              <span className="rounded-full bg-(--ink)/85 px-2 py-0.5 text-[11px] font-medium text-white">
                Out of stock
              </span>
            ) : null}
          </div>
          {details.short_description ? (
            <p className="mt-3 text-[15px] leading-relaxed text-(--ink-soft)">{details.short_description}</p>
          ) : null}

          {variants.length > 0 ? (
            <div className="mt-4">
              <div className="text-[13px] font-semibold text-(--ink)">Options</div>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {variants.map((variant) => (
                  <button
                    key={variant.product_id}
                    type="button"
                    onClick={() => setSelectedId(variant.product_id)}
                    disabled={variant.in_stock === false}
                    className={`chip ${
                      variant.product_id === selectedId ? "!border-(--brand) !bg-(--accent-soft)" : ""
                    } ${variant.in_stock === false ? "line-through" : ""}`}
                  >
                    {variant.title}
                  </button>
                ))}
              </div>
              {selectedId === null ? (
                <p className="mt-1.5 text-[11px] text-(--ink-soft)">Pick an option to add it to the cart.</p>
              ) : null}
            </div>
          ) : null}

          <div className="mt-5">
            {canAdd ? (
              <AddToCart
                onAdd={async () => {
                  const added = await addProduct(selected.product_id);
                  if (added) openCart();
                  return added;
                }}
              />
            ) : null}
          </div>

          {details.long_description ? (
            <p className="mt-6 whitespace-pre-line text-[15px] leading-relaxed text-(--ink)">
              {details.long_description}
            </p>
          ) : null}

          {Object.keys(details.specs ?? {}).length ? (
            <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-2">
              {Object.entries(details.specs ?? {}).map(([key, value]) => (
                <div key={key} className="text-[13px]">
                  <dt className="font-semibold capitalize text-(--ink-soft)">{key.replaceAll("_", " ")}</dt>
                  <dd className="text-(--ink)">{value}</dd>
                </div>
              ))}
            </dl>
          ) : null}

          {details.review_highlights?.length ? (
            <div className="mt-6 space-y-1.5">
              {details.review_highlights.slice(0, 3).map((highlight) => (
                <p key={highlight} className="text-[13px] italic leading-snug text-(--ink-soft)">
                  “{highlight}”
                </p>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
